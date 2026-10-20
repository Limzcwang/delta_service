"""房间技能共享服务（E1）：校验、最新状态读写、30 分钟 stale 过滤。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dao.skill import SkillStateDao
from log.logger import logger

SLOT_ULTIMATE = 0
SLOT_SKILL = 1
SLOT_THROWABLE = 2
VALID_SLOTS = (SLOT_ULTIMATE, SLOT_SKILL, SLOT_THROWABLE)
VALID_STATES = ("ready", "charging", "unknown")
MAX_REMAINING_MS = 600000
MAX_CHARGES = 9
MAX_PERCENT = 100
MAX_OPERATOR_LENGTH = 32
VALID_SQUAD_NUMBERS = (1, 2, 3)
STALE_AFTER_SECONDS = 30 * 60
STALE_CAPTURE_MS = 10_000  # captured_at 超过 10 秒视为过期状态

ERR_INVALID_SKILL_PAYLOAD = "INVALID_SKILL_PAYLOAD"


class SkillError(Exception):
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _invalid(message: str):
    raise SkillError(ERR_INVALID_SKILL_PAYLOAD, message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_squad_number(value: Any) -> Optional[int]:
    """只接受 1/2/3 的 int；非法值忽略（不拒绝整条 payload）。"""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value in VALID_SQUAD_NUMBERS:
        return value
    logger.debug("忽略非法 squad_number: %r", value)
    return None


def validate_skill_payload(payload: Any) -> Dict[str, Any]:
    """校验 skill_share_update 载荷；返回归一化的可落库字段。"""
    if not isinstance(payload, dict):
        _invalid("payload 必须是对象")
    operator_id = payload.get("operator_id")
    operator_name = payload.get("operator_name")
    if not isinstance(operator_id, str) or len(operator_id) > MAX_OPERATOR_LENGTH:
        _invalid("operator_id 必须是 ≤32 字符字符串")
    if not isinstance(operator_name, str) or len(operator_name) > MAX_OPERATOR_LENGTH:
        _invalid("operator_name 必须是 ≤32 字符字符串")

    skills = payload.get("skills")
    if not isinstance(skills, list) or len(skills) != 3:
        _invalid("skills 必须恰好 3 条（slot 0/1/2）")
    normalized = []
    seen_slots = set()
    for item in skills:
        if not isinstance(item, dict):
            _invalid("skill 必须是对象")
        slot = item.get("slot")
        if not _is_int(slot) or slot not in VALID_SLOTS:
            _invalid("slot 必须是 0/1/2")
        if slot in seen_slots:
            _invalid("slot 不能重复")
        seen_slots.add(slot)
        state = item.get("state")
        if state not in VALID_STATES:
            _invalid("state 必须是 ready/charging/unknown")
        remaining_ms = item.get("remaining_ms")
        if not _is_int(remaining_ms) or not 0 <= remaining_ms <= MAX_REMAINING_MS:
            _invalid("remaining_ms 必须是 0-600000 整数")
        charges = item.get("charges")
        max_charges = item.get("max_charges")
        if (
            not _is_int(max_charges)
            or not 1 <= max_charges <= MAX_CHARGES
        ):
            _invalid("max_charges 必须是 1-9 整数")
        if not _is_int(charges) or not 0 <= charges <= max_charges:
            _invalid("charges 必须是 0-max_charges 整数")
        pct = item.get("pct")
        if pct is not None:
            if not _is_int(pct) or not 0 <= pct <= MAX_PERCENT:
                _invalid("pct 必须是 0-100 整数或空")
        normalized_item = {
            "slot": slot,
            "state": state,
            "remaining_ms": remaining_ms,
            "charges": charges,
            "max_charges": max_charges,
        }
        if pct is not None:
            normalized_item["pct"] = pct
        normalized.append(normalized_item)

    captured_at = payload.get("captured_at")
    if not _is_int(captured_at) or captured_at < 0:
        _invalid("captured_at 必须是非负毫秒整数")

    return {
        "operator_id": operator_id,
        "operator_name": operator_name,
        "skills": normalized,
        "captured_at": captured_at,
        "squad_number": _normalize_squad_number(payload.get("squad_number")),
    }


def _dt_to_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def state_to_dict(record) -> Dict[str, Any]:
    user = getattr(record, "user", None)
    data = getattr(record, "data", None) or {}
    payload = {
        "user_id": str(getattr(user, "id", "")),
        "nickname": getattr(user, "nickname", None) or "",
        "operator_id": data.get("operator_id", ""),
        "operator_name": data.get("operator_name", ""),
        "skills": data.get("skills", []),
        "captured_at": data.get("captured_at"),
        "server_received_at": data.get("server_received_at")
        if data.get("server_received_at") is not None
        else _dt_to_ms(getattr(record, "updated_at", None)),
        "updated_at": _dt_to_ms(getattr(record, "updated_at", None)),
    }
    squad_number = getattr(record, "squad_number", None)
    if squad_number is not None:
        payload["squad_number"] = squad_number
    return payload


def is_stale(record, now: Optional[datetime] = None) -> bool:
    updated_at = getattr(record, "updated_at", None)
    if updated_at is None:
        return True
    now = now or datetime.now()
    return (now - updated_at) > timedelta(seconds=STALE_AFTER_SECONDS)


def _adjust_skills(skills: List[Dict[str, Any]], now_ms: int, captured_at: int) -> List[Dict[str, Any]]:
    """服务端权威折算：按到达时间把 remaining_ms 扣减为 adjusted，并给绝对 ready_at。"""
    age_ms = now_ms - captured_at
    too_old = age_ms > STALE_CAPTURE_MS
    if age_ms < 0:
        # 客户端时钟漂移/未来时间：不放大剩余时间
        age_ms = 0
    adjusted_skills = []
    for item in skills:
        if too_old:
            # 旧状态：视为已过期，不再把旧 remaining 当新状态
            adjusted = 0
        else:
            adjusted = max(0, int(item.get("remaining_ms", 0)) - age_ms)
        normalized = dict(item)
        normalized["remaining_ms"] = adjusted
        normalized["ready_at"] = int(now_ms + adjusted)
        adjusted_skills.append(normalized)
    return adjusted_skills


def server_now_ms() -> int:
    return int(time.time() * 1000)


async def save_state(
    room, user, payload: Any, now_ms: Optional[int] = None
) -> Dict[str, Any]:
    data = validate_skill_payload(payload)
    now_ms = int(now_ms if now_ms is not None else server_now_ms())
    data["skills"] = _adjust_skills(
        data["skills"], now_ms, int(data["captured_at"])
    )
    data["server_received_at"] = now_ms
    squad_number = data.pop("squad_number", None)
    record = await SkillStateDao.upsert(
        room, user, data, squad_number=squad_number
    )
    return state_to_dict(record)


async def list_states(room_id: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """返回未 stale 的状态（30 分钟无更新视为未共享，直接过滤）。"""
    records = await SkillStateDao.list_for_room(str(room_id))
    return [state_to_dict(record) for record in records if not is_stale(record, now)]


def filter_states_by_members(
    states: List[Dict[str, Any]], member_ids: Any
) -> List[Dict[str, Any]]:
    """只保留当前仍在房间成员列表里的用户状态（过滤残留行）。"""
    allowed = {str(member_id) for member_id in (member_ids or [])}
    return [state for state in states if str(state.get("user_id")) in allowed]


async def remove_state(room_id: str, user_id: int) -> int:
    return await SkillStateDao.remove(str(room_id), int(user_id))


async def clear_room(room_id: str) -> int:
    return await SkillStateDao.clear_room(str(room_id))
