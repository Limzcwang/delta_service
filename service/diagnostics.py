"""诊断数据校验/裁剪、限频与 7 天清理（0.2.12 B6）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from dao.diagnostics import DiagnosticEventDao
from service.messages import MessageRateLimiter

MAX_BODY_BYTES = 512 * 1024
MAX_GZIP_BYTES = 128 * 1024
MAX_EVENTS = 1000
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_STRING = 256
MAX_LIST = 100
MAX_DICT = 64
RETENTION_DAYS = 7

INVALID_DIAGNOSTICS_PAYLOAD = "INVALID_DIAGNOSTICS_PAYLOAD"
DIAGNOSTICS_TOO_LARGE = "DIAGNOSTICS_TOO_LARGE"
DIAGNOSTICS_RATE_LIMITED = "DIAGNOSTICS_RATE_LIMITED"
DIAGNOSTICS_DISABLED = "DIAGNOSTICS_DISABLED"

ALLOWED_KINDS = ("env", "tick", "event")
ALLOWED_FIELDS = {
    "client_version", "os_version", "is_admin", "monitor", "game_window",
    "hud_geometry", "ts", "phase", "shared", "auto_start", "interval_ms",
    "capture", "start_ocr", "operator", "skill_bar", "end_async", "error",
    "settings", "template_version", "ws_connect", "ws_disconnect",
    "room_join", "room_leave", "share_on", "share_off", "reset_match",
    "window_lost", "room_code", "room_id", "title", "process", "exe",
    "client_rect", "display_mode", "avatar_box", "skill_bar_rect",
    "minimap_radius", "text_height_px", "estimated", "ok", "fail_reason",
    "width", "height", "mean_luma", "lines", "text", "box", "hits",
    "attempts", "threshold", "result", "top3", "id", "score", "locked",
    "fallback", "unknown", "found", "roi", "slots", "state", "charges",
    "pct", "ocr_lines", "delay_ms", "scans", "code", "message",
    "trace_digest", "truncated",
}


class DiagnosticError(Exception):
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


rate_limiter = MessageRateLimiter(max_messages=6, window_seconds=60.0)


def _truncate_string(value: str) -> str:
    return value if len(value) <= MAX_STRING else value[:MAX_STRING]


def _sanitize_any(value: Any, depth: int = 0) -> Any:
    """用于 settings：保留任意键，但限制深度/长度/数量。"""
    if depth > 6:
        return None
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_any(item, depth + 1) for item in value[:MAX_LIST]]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _sanitize_any(item, depth + 1)
            for key, item in list(value.items())[:MAX_DICT]
            if _sanitize_any(item, depth + 1) is not None
        }
    return None


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [
            _sanitize_value(item, depth + 1)
            for item in value[:MAX_LIST]
        ]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_DICT]:
            key = str(key)
            if key == "settings":
                sanitized = _sanitize_any(item, depth + 1)
            elif key in ALLOWED_FIELDS:
                sanitized = _sanitize_value(item, depth + 1)
            else:
                continue
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
        clean_payload = _sanitize_value(raw_payload)
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
