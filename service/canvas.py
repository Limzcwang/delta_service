"""房间画板服务层（D）：op 校验、快照读写、7 天清理。

设计取舍（已知限制）：
- WS canvas_op 只做校验与实时广播，不落库；快照仅由显式
  ``canvas_snapshot_save`` 或房间关闭时落盘（不做 60s/30s 自动快照）。
- rev 单调性由 WS 会话层维护（每连接每房间独立）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dao.canvas import CanvasDao

MAX_POINTS_PER_STROKE = 512
MAX_STROKES = 5000
MAX_SNAPSHOT_BYTES = 512 * 1024
WIDTH_MIN = 1
WIDTH_MAX = 32
RETENTION_DAYS = 7
CANVAS_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TOOLS = ("pen", "eraser")
KINDS = ("stroke", "clear")

ERR_INVALID_CANVAS_OP = "INVALID_CANVAS_OP"
ERR_CANVAS_OUT_OF_ORDER = "CANVAS_OUT_OF_ORDER"
ERR_INVALID_CANVAS_SNAPSHOT = "INVALID_CANVAS_OP"


class CanvasError(Exception):
    """画板业务异常；error_code 直接用于 WS/HTTP 响应。"""

    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_stroke(op: Dict[str, Any]) -> None:
    points = op.get("points")
    if not isinstance(points, list) or not points:
        raise CanvasError(ERR_INVALID_CANVAS_OP, "stroke.points 必须是非空数组")
    if len(points) > MAX_POINTS_PER_STROKE:
        raise CanvasError(
            ERR_INVALID_CANVAS_OP,
            f"单条笔画点数不能超过 {MAX_POINTS_PER_STROKE}",
        )
    for point in points:
        if not isinstance(point, dict):
            raise CanvasError(ERR_INVALID_CANVAS_OP, "point 必须是对象")
        for axis in ("x", "y"):
            value = point.get(axis)
            if not _is_number(value) or not 0 <= float(value) <= 1:
                raise CanvasError(
                    ERR_INVALID_CANVAS_OP,
                    "坐标必须是 0-1 的归一化数值",
                )

    if not isinstance(op.get("color"), str) or not CANVAS_COLOR_RE.fullmatch(op["color"]):
        raise CanvasError(ERR_INVALID_CANVAS_OP, "color 必须是 #RRGGBB")
    width = op.get("width")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not WIDTH_MIN <= width <= WIDTH_MAX
    ):
        raise CanvasError(
            ERR_INVALID_CANVAS_OP, f"width 需在 {WIDTH_MIN}-{WIDTH_MAX} 之间"
        )
    if op.get("tool") not in TOOLS:
        raise CanvasError(ERR_INVALID_CANVAS_OP, "tool 只能是 pen 或 eraser")


def validate_canvas_op(op: Any) -> None:
    """校验单个 canvas op；不修改原始内容（广播原样）。"""
    if not isinstance(op, dict):
        raise CanvasError(ERR_INVALID_CANVAS_OP, "op 必须是对象")
    kind = op.get("kind")
    if kind not in KINDS:
        raise CanvasError(ERR_INVALID_CANVAS_OP, "op.kind 必须是 stroke 或 clear")
    if kind == "stroke":
        _validate_stroke(op)


def validate_snapshot_strokes(strokes: Any) -> List[Any]:
    """校验全量快照笔画数组（数量/单笔画/JSON 大小）。"""
    if not isinstance(strokes, list):
        raise CanvasError(ERR_INVALID_CANVAS_OP, "strokes 必须是数组")
    if len(strokes) > MAX_STROKES:
        raise CanvasError(
            ERR_INVALID_CANVAS_OP, f"快照笔画数不能超过 {MAX_STROKES}"
        )
    for stroke in strokes:
        validate_canvas_op(stroke)
    encoded = json.dumps(
        strokes, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise CanvasError(
            ERR_INVALID_CANVAS_OP,
            f"快照大小不能超过 {MAX_SNAPSHOT_BYTES // 1024}KB",
        )
    return strokes


def _dt_to_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def snapshot_to_dict(record) -> Dict[str, Any]:
    data = getattr(record, "data", None) or {}
    if isinstance(data, str):  # 兼容手工写入的文本 JSON
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    return {
        "strokes": data.get("strokes", []),
        "rev": int(data.get("rev", 0) or 0),
        "updated_at": _dt_to_ms(getattr(record, "updated_at", None)),
    }


def empty_snapshot() -> Dict[str, Any]:
    return {"strokes": [], "rev": 0, "updated_at": None}


async def save_snapshot(
    room_id: str, strokes: Any, rev: Any = 0
) -> Dict[str, Any]:
    """校验并落库全量快照（显式 save / 关房 flush 复用）。"""
    validate_snapshot_strokes(strokes)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise CanvasError(ERR_INVALID_CANVAS_OP, "rev 必须是非负整数")
    record = await CanvasDao.upsert(
        str(room_id), {"strokes": strokes, "rev": rev}
    )
    return snapshot_to_dict(record)


async def get_snapshot(room_id: str) -> Dict[str, Any]:
    record = await CanvasDao.get_by_room(str(room_id))
    if record is None:
        return empty_snapshot()
    return snapshot_to_dict(record)


async def cleanup_canvas_snapshots(
    days: int = RETENTION_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """删除超过保留期的画板快照（默认 7 天）；返回删除条数。"""
    if not isinstance(days, int) or days <= 0:
        raise ValueError("days 必须是正整数")
    cutoff = (now or datetime.now()) - timedelta(days=days)
    return await CanvasDao.delete_older_than(cutoff)
