"""内部数据集采集：配置、meta 校验、落盘与序列化。"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_UPLOADERS = "DeltaQA01,DeltaQA02"
DEFAULT_ADMINS = "admin"
DEFAULT_DATASET_DIR = "/data/datasets"
DEFAULT_DAILY_QUOTA = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_META_BYTES = 64 * 1024
DATE_KEY_RE = re.compile(r"^\d{8}$")

ERR_INVALID_DATASET_META = "INVALID_DATASET_META"
ERR_DATASET_FORBIDDEN = "DATASET_FORBIDDEN"
ERR_DATASET_FILE_TOO_LARGE = "DATASET_FILE_TOO_LARGE"
ERR_DATASET_QUOTA_EXCEEDED = "DATASET_QUOTA_EXCEEDED"
ERR_DATASET_DISABLED = "DATASET_DISABLED"
ERR_INVALID_DATASET_DATE = "INVALID_DATASET_DATE"


class DatasetError(Exception):
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def uploaders() -> List[str]:
    return _csv_env("DATASET_UPLOADERS", DEFAULT_UPLOADERS)


def admins() -> List[str]:
    return _csv_env("DATASET_ADMINS", DEFAULT_ADMINS)


def enabled() -> bool:
    return os.getenv("DATASET_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def daily_quota() -> int:
    try:
        value = int(os.getenv("DATASET_DAILY_QUOTA", str(DEFAULT_DAILY_QUOTA)))
    except (TypeError, ValueError):
        value = DEFAULT_DAILY_QUOTA
    return max(value, 0)


def dataset_root() -> Path:
    return Path(os.getenv("DATASET_DIR", DEFAULT_DATASET_DIR))


def date_key_now(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d")


def username_of(user: Any) -> str:
    return str(getattr(user, "username", "") or "")


def is_uploader(user: Any) -> bool:
    return username_of(user) in uploaders()


def is_admin(user: Any) -> bool:
    return username_of(user) in admins()


def _invalid(message: str):
    raise DatasetError(ERR_INVALID_DATASET_META, message)


def _nonempty_str(value: Any, field: str, max_len: int = 64) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        _invalid(f"{field} 必须是非空字符串（≤{max_len}）")
    return value.strip()


def validate_meta(raw: Any) -> Dict[str, Any]:
    """校验 multipart 的 meta 字符串，返回可落盘的归一化元数据。"""
    if not isinstance(raw, str) or not raw.strip():
        _invalid("meta 必须是非空 JSON 字符串")
    if len(raw.encode("utf-8")) > MAX_META_BYTES:
        _invalid("meta 超过 64KB")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _invalid("meta 不是合法 JSON")
    if not isinstance(payload, dict):
        _invalid("meta 必须是 JSON 对象")

    operator_id = _nonempty_str(payload.get("operator_id"), "operator_id", 32)
    resolution = _nonempty_str(payload.get("resolution"), "resolution", 32)
    client_version = _nonempty_str(
        payload.get("client_version"), "client_version", 32
    )
    room_code = payload.get("room_code")
    if room_code is not None:
        room_code = _nonempty_str(room_code, "room_code", 16)

    hud_scale = payload.get("hud_scale")
    if (
        not isinstance(hud_scale, (int, float))
        or isinstance(hud_scale, bool)
        or hud_scale <= 0
    ):
        _invalid("hud_scale 必须是正数")

    captured_at = payload.get("captured_at")
    if (
        not isinstance(captured_at, int)
        or isinstance(captured_at, bool)
        or captured_at < 0
    ):
        _invalid("captured_at 必须是非负毫秒整数")

    ocr = payload.get("ocr")
    if not isinstance(ocr, list) or len(ocr) > 10:
        _invalid("ocr 必须是数组（≤10 项）")
    normalized_ocr = []
    for item in ocr:
        if not isinstance(item, str) or len(item) > 64:
            _invalid("ocr 每项必须 ≤64 字符字符串")
        normalized_ocr.append(item)

    squad_number = payload.get("squad_number")
    if squad_number is not None and (
        not isinstance(squad_number, int)
        or isinstance(squad_number, bool)
        or squad_number not in (1, 2, 3)
    ):
        _invalid("squad_number 必须是 1/2/3 或空")

    return {
        "room_code": room_code,
        "operator_id": operator_id,
        "resolution": resolution,
        "hud_scale": hud_scale,
        "captured_at": captured_at,
        "ocr": normalized_ocr,
        "squad_number": squad_number,
        "client_version": client_version,
    }


def new_sample_id() -> str:
    return uuid.uuid4().hex


def sample_rel_dir(date_key: str, sample_id: str) -> str:
    return f"{date_key}/{sample_id}"


def write_sample_files(
    root: Path,
    date_key: str,
    sample_id: str,
    meta: Dict[str, Any],
    slots_png: Optional[bytes],
    avatar_png: Optional[bytes],
) -> List[str]:
    """写入 <root>/<date>/<uuid>/；返回已写入的相对文件名列表。"""
    target = Path(root) / date_key / sample_id
    target.mkdir(parents=True, exist_ok=True)
    written = []
    if slots_png is not None:
        (target / "slots.png").write_bytes(slots_png)
        written.append("slots.png")
    if avatar_png is not None:
        (target / "avatar.png").write_bytes(avatar_png)
        written.append("avatar.png")
    (target / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    written.append("meta.json")
    return written


def sample_to_dict(record) -> Dict[str, Any]:
    return {
        "id": str(getattr(record, "id", "")),
        "sample_id": getattr(record, "sample_id", ""),
        "username": getattr(record, "username", ""),
        "date_key": getattr(record, "date_key", ""),
        "room_code": getattr(record, "room_code", None),
        "operator_id": getattr(record, "operator_id", ""),
        "resolution": getattr(record, "resolution", ""),
        "hud_scale": getattr(record, "hud_scale", None),
        "captured_at": getattr(record, "captured_at", None),
        "ocr": getattr(record, "ocr", []) or [],
        "squad_number": getattr(record, "squad_number", None),
        "client_version": getattr(record, "client_version", ""),
        "files": getattr(record, "files", []) or [],
        "path": getattr(record, "rel_dir", ""),
        "created_at": _dt_to_ms(getattr(record, "created_at", None)),
    }


def _dt_to_ms(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(value.timestamp() * 1000)
