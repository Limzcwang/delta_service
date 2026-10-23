"""诊断数据校验/裁剪、限频与 7 天清理（0.2.12 B6）。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from dao.diagnostics import DiagnosticEventDao
from service.messages import MessageRateLimiter

MAX_BODY_BYTES = 512 * 1024
MAX_GZIP_BYTES = 128 * 1024
MAX_EVENTS = 1000
RETENTION_DAYS = 7

INVALID_DIAGNOSTICS_PAYLOAD = "INVALID_DIAGNOSTICS_PAYLOAD"
DIAGNOSTICS_TOO_LARGE = "DIAGNOSTICS_TOO_LARGE"
DIAGNOSTICS_RATE_LIMITED = "DIAGNOSTICS_RATE_LIMITED"
DIAGNOSTICS_DISABLED = "DIAGNOSTICS_DISABLED"

ALLOWED_KINDS = ("env", "tick", "event")


class DiagnosticError(Exception):
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


rate_limiter = MessageRateLimiter(max_messages=6, window_seconds=60.0)


MAX_STRING = 512
MAX_LIST = 100
MAX_DICT = 64
MAX_DEPTH = 4
MAX_PAYLOAD_BYTES = 64 * 1024
SETTINGS_KEYS = {
    "auto_start", "interval", "interval_ms", "overlay_enabled",
    "sharing_enabled", "capture_interval", "hud_scale",
}
_ANY = object()

_SENSITIVE_RE = re.compile(
    r"(password|passwd|pwd|token|jwt|secret|apikey|authorization|cookie|"
    r"sessionkey|privatekey)",
    re.IGNORECASE,
)


def _is_sensitive_key(key) -> bool:
    raw = str(key)
    normalized = re.sub(r"[^a-z0-9]", "", raw.lower())
    return bool(_SENSITIVE_RE.search(normalized))


_ENV_SCHEMA = {
    "client_version": None,
    "os_version": None,
    "is_admin": None,
    "monitor": {
        "width": None, "height": None, "dpi": None, "scale": None,
        "refresh_hz": None, "name": None, "count": None,
    },
    "game_window": {
        "title": None, "process": None, "exe": None,
        "client_rect": {"x": None, "y": None, "width": None, "height": None},
        "display_mode": None,
    },
    "hud_geometry": {
        "avatar_box": {"x": None, "y": None, "width": None, "height": None},
        "skill_bar_rect": {"x": None, "y": None, "width": None, "height": None},
        "minimap_radius": None,
        "text_height_px": None,
        "estimated": None,
    },
}

_TICK_SCHEMA = {
    "ts": None,
    "phase": None,
    "shared": None,
    "auto_start": None,
    "interval_ms": None,
    "capture": {
        "ok": None, "fail_reason": None, "width": None, "height": None,
        "mean_luma": None,
    },
    "start_ocr": {
        "lines": [{"text": None, "box": _ANY}],
        "hits": None, "attempts": None, "threshold": None, "result": None,
    },
    "operator": {
        "top3": [{"id": None, "score": None}],
        "locked": None, "fallback": None, "unknown": None,
    },
    "skill_bar": {
        "found": None,
        "roi": _ANY,
        "slots": [{"state": None, "charges": None, "pct": None}],
        "ocr_lines": [_ANY],
    },
    "end_async": {
        "delay_ms": None, "scans": None, "hits": None, "threshold": None,
        "result": None,
    },
    "error": {"code": None, "message": None, "trace_digest": None},
}

_EVENT_SCHEMA = {
    "event_type": None, "event": None, "type": None, "ts": None,
    "room_id": None, "room_code": None, "client_version": None,
    "enabled": None, "value": None, "name": None, "template_version": None,
    "ws_connect": None, "ws_disconnect": None, "room_join": None,
    "room_leave": None, "share_on": None, "share_off": None,
    "reset_match": None, "window_lost": None, "settings": _ANY,
}

_KIND_SCHEMAS = {"env": _ENV_SCHEMA, "tick": _TICK_SCHEMA, "event": _EVENT_SCHEMA}


def _truncate_string(value: str) -> str:
    return value if len(value) <= MAX_STRING else value[:MAX_STRING]


def _sanitize_generic(value, depth: int):
    """settings/roi 等任意结构：限制深度、长度，丢弃敏感键。"""
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_generic(item, depth + 1) for item in value[:MAX_LIST]]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_DICT]:
            if _is_sensitive_key(key):
                continue
            result[str(key)[:64]] = _sanitize_generic(item, depth + 1)
        return result
    return None


def _sanitize(value, schema, depth: int = 0):
    """按 schema 白名单递归裁剪；schema=None 只允许标量。"""
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        item_schema = schema[0] if isinstance(schema, list) and schema else _ANY
        return [
            _sanitize(item, item_schema, depth + 1)
            for item in value[:MAX_LIST]
        ]
    if isinstance(value, dict):
        if schema is _ANY:
            return _sanitize_generic(value, depth)
        if not isinstance(schema, dict):
            return None
        result = {}
        for key, item in list(value.items())[:MAX_DICT]:
            key = str(key)
            if _is_sensitive_key(key) or key not in schema:
                continue
            if key == "settings":
                clean = {}
                if isinstance(item, dict):
                    for setting_key, setting_value in list(item.items())[:MAX_DICT]:
                        if (
                            _is_sensitive_key(setting_key)
                            or str(setting_key) not in SETTINGS_KEYS
                        ):
                            continue
                        clean[str(setting_key)] = _sanitize_generic(
                            setting_value, depth + 1
                        )
                result[key] = clean
                continue
            sanitized = _sanitize(item, schema[key], depth + 1)
            if sanitized is not None:
                result[key] = sanitized
        return result
    return None


def _uint(value: Any, field: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise DiagnosticError(
            INVALID_DIAGNOSTICS_PAYLOAD,
            f"{field} 必须是非空字符串（≤{max_len}）",
        )
    return value.strip()


def validate_batch(payload: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int]:
    """返回 (batch_meta, sanitized_events, skipped_count)。"""
    if not isinstance(payload, dict):
        raise DiagnosticError(INVALID_DIAGNOSTICS_PAYLOAD, "body 必须是 JSON 对象")
    client_diag_id = _uint(
        payload.get("client_diag_id"), "client_diag_id", 36
    )
    session_id = _uint(payload.get("session_id"), "session_id", 36)
    client_version = payload.get("client_version")
    if client_version is not None:
        client_version = _truncate_string(str(client_version))[:32]
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise DiagnosticError(
            INVALID_DIAGNOSTICS_PAYLOAD, "events 必须是非空数组"
        )
    if len(events) > MAX_EVENTS:
        raise DiagnosticError(
            DIAGNOSTICS_TOO_LARGE, f"events 超过 {MAX_EVENTS} 条", 413
        )

    sanitized = []
    skipped = 0
    room_id = None
    for event in events:
        if not isinstance(event, dict):
            skipped += 1
            continue
        kind = event.get("kind")
        if kind not in ALLOWED_KINDS:
            skipped += 1
            continue
        raw_payload = event.get("payload")
        if not isinstance(raw_payload, dict):
            skipped += 1
            continue
        clean_payload = _sanitize(raw_payload, _KIND_SCHEMAS[kind])
        if not isinstance(clean_payload, dict):
            skipped += 1
            continue
        encoded = json.dumps(clean_payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            skipped += 1
            continue
        sanitized.append({"kind": kind, "payload": clean_payload})
        if room_id is None:
            candidate = clean_payload.get("room_id") or clean_payload.get("room_code")
            if isinstance(candidate, str):
                room_id = candidate[:64]

    if not sanitized:
        raise DiagnosticError(
            INVALID_DIAGNOSTICS_PAYLOAD, "没有可接受的 events"
        )
    meta = {
        "client_diag_id": client_diag_id,
        "session_id": session_id,
        "client_version": client_version,
        "room_id": room_id,
    }
    return meta, sanitized, skipped


def build_rows(
    meta: Dict[str, Any],
    events: List[Dict[str, Any]],
    account_user_id: Optional[int],
) -> List[Dict[str, Any]]:
    return [
        {
            "account_user_id": account_user_id,
            "client_diag_id": meta["client_diag_id"],
            "session_id": meta["session_id"],
            "room_id": meta["room_id"],
            "kind": event["kind"],
            "payload": event["payload"],
            "client_version": meta["client_version"],
        }
        for event in events
    ]


async def save_batch(
    meta: Dict[str, Any],
    events: List[Dict[str, Any]],
    account_user_id: Optional[int],
) -> int:
    return await DiagnosticEventDao.bulk_create(
        build_rows(meta, events, account_user_id)
    )


async def cleanup_old_events(now: Optional[datetime] = None) -> int:
    cutoff = (now or datetime.now()) - timedelta(days=RETENTION_DAYS)
    return await DiagnosticEventDao.delete_before(cutoff)
