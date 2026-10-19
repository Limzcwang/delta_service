"""房间业务层（B2a：DB 持久化房间模型）。

与 ``service/room.py``（旧的进程内房间实现）并存：
- 旧模块继续服务旧版 `/api/room/*` 与 legacy WebSocket；
- 本模块服务 v1 `/api/v1/rooms*`，数据落 MySQL。
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dao.room import Room, RoomDao, RoomMember, RoomMemberDao
from security.passwords import hash_password, verify_password
from service import backgrounds as backgrounds_service

ROOM_CODE_LENGTH = 5
ROOM_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ROOM_CODE_RETRIES = 10
MAX_MEMBERS = 8
NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 30
PASSWORD_MIN_LENGTH = 4
PASSWORD_MAX_LENGTH = 64
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

ROOM_CODE_RE = re.compile(r"^[A-Z0-9]{5}$")

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"

# 错误码（写入 v1 错误体 data.error_code）
ERR_ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
ERR_ROOM_PASSWORD_REQUIRED = "ROOM_PASSWORD_REQUIRED"
ERR_ROOM_PASSWORD_INVALID = "ROOM_PASSWORD_INVALID"
ERR_ROOM_FULL = "ROOM_FULL"
ERR_ROOM_CLOSED = "ROOM_CLOSED"
ERR_ROOM_CODE_GENERATION_FAILED = "ROOM_CODE_GENERATION_FAILED"
ERR_NOT_ROOM_MEMBER = "NOT_ROOM_MEMBER"
ERR_NOT_ROOM_OWNER = "NOT_ROOM_OWNER"
ERR_MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
ERR_INVALID_BACKGROUND = "INVALID_BACKGROUND"
ERR_ROOM_NAME_INVALID = "ROOM_NAME_INVALID"
# 以下为 B2a 补充码（客户端可按通用文案处理）
ERR_ROOM_OWNER_MUST_CLOSE = "ROOM_OWNER_MUST_CLOSE"
ERR_CANNOT_KICK_OWNER = "CANNOT_KICK_OWNER"
ERR_INVALID_VISIBILITY = "INVALID_VISIBILITY"
ERR_INVALID_ROOM_PAYLOAD = "INVALID_ROOM_PAYLOAD"
ERR_ROOM_FORBIDDEN = "ROOM_FORBIDDEN"


class RoomError(Exception):
    """房间业务异常；携带 error_code 与建议 HTTP 状态码。"""

    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def generate_room_code(length: int = ROOM_CODE_LENGTH) -> str:
    """生成 5 位大写字母/数字房间码。"""
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def normalize_room_code(value: str) -> str:
    return (value or "").strip().upper()


def _validate_room_name(name: Any) -> str:
    if not isinstance(name, str):
        raise RoomError(ERR_ROOM_NAME_INVALID, "房间名必须是字符串")
    name = name.strip()
    if not (NAME_MIN_LENGTH <= len(name) <= NAME_MAX_LENGTH):
        raise RoomError(
            ERR_ROOM_NAME_INVALID,
            f"房间名长度需在 {NAME_MIN_LENGTH}-{NAME_MAX_LENGTH} 个字符之间",
        )
    return name


def _validate_room_password(password: Any) -> str:
    if not isinstance(password, str):
        raise RoomError("ROOM_PASSWORD_INVALID", "房间密码必须是字符串")
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        raise RoomError(
            "ROOM_PASSWORD_INVALID",
            f"房间密码长度需在 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 个字符之间",
        )
    return password


def _validate_visibility(visibility: Any) -> str:
    if visibility not in (VISIBILITY_PUBLIC, VISIBILITY_PRIVATE):
        raise RoomError(ERR_INVALID_VISIBILITY, "visibility 只能是 public 或 private")
    return visibility


def _validate_background(background_id: Optional[str]) -> Optional[str]:
    if background_id is None:
        return None
    if not isinstance(background_id, str) or not background_id.strip():
        raise RoomError(ERR_INVALID_BACKGROUND, "background_id 非法")
    background_id = background_id.strip()
    if backgrounds_service.find_background(background_id) is None:
        raise RoomError(ERR_INVALID_BACKGROUND, "background_id 不存在")
    return background_id


def _ensure_open(room: Room) -> None:
    if getattr(room, "closed_at", None) is not None:
        raise RoomError(ERR_ROOM_CLOSED, "房间已关闭", 409)


def _ensure_owner_for_mutation(owner, room: Room) -> None:
    if str(getattr(owner, "id", "")) != str(getattr(room, "owner_id", "")):
        raise RoomError(ERR_ROOM_FORBIDDEN, "仅房主可执行该操作", 403)


def _ensure_owner(owner, room: Room) -> None:
    if str(getattr(owner, "id", "")) != str(getattr(room, "owner_id", "")):
        raise RoomError(ERR_NOT_ROOM_OWNER, "仅房主可执行该操作", 403)


def _dt_to_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def _ms_to_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError) as exc:
        raise RoomError("INVALID_CURSOR", "before 必须是毫秒时间戳") from exc
    if ms < 0:
        raise RoomError("INVALID_CURSOR", "before 必须是毫秒时间戳")
    return datetime.fromtimestamp(ms / 1000)


async def resolve_room(
    room_id_or_code: str, include_deleted: bool = False
) -> Room:
    """按 5 位 room_code 或内部 id 解析房间。

    F2 软删除：默认把 deleted 房间视为不存在（ROOM_NOT_FOUND，不泄露删除状态）；
    仅房主 DELETE 幂等路径允许 include_deleted=True。
    """
    if not isinstance(room_id_or_code, str) or not room_id_or_code.strip():
        raise RoomError(ERR_ROOM_NOT_FOUND, "房间不存在", 404)
    identifier = room_id_or_code.strip()
    normalized = normalize_room_code(identifier)

    room = None
    if ROOM_CODE_RE.fullmatch(normalized):
        room = await RoomDao.get_by_code(normalized)
    if room is None:
        room = await RoomDao.get_by_id(identifier)
    if room is None or (getattr(room, "deleted", False) and not include_deleted):
        raise RoomError(ERR_ROOM_NOT_FOUND, "房间不存在", 404)
    return room


def member_to_dict(member: RoomMember) -> Dict[str, Any]:
    """对外成员信息只暴露 nickname（F1：username 仅本人可见）。"""
    user = getattr(member, "user", None)
    user_id = getattr(user, "id", None)
    return {
        "id": str(user_id),
        "nickname": getattr(user, "nickname", None) or "",
        "role": getattr(member, "role", "member"),
    }


async def list_members(room: Room) -> List[Dict[str, Any]]:
    members = await RoomMemberDao.list_for_room(room.id)
    return [member_to_dict(member) for member in members]


def room_to_dict(room: Room, member_count: int) -> Dict[str, Any]:
    """对外房间信息：绝不包含 password_hash。"""
    background = None
    background_id = getattr(room, "background_id", None)
    if background_id:
        background = backgrounds_service.find_background(background_id)
    return {
        "id": str(room.id),
        "code": room.code,
        "name": room.name,
        "owner_id": str(getattr(room, "owner_id", "")),
        "visibility": room.visibility,
        "member_count": int(member_count),
        "has_password": bool(getattr(room, "password_hash", None)),
        "background_id": background_id,
        "background": background,
        "created_at": _dt_to_ms(getattr(room, "created_at", None)),
        "updated_at": _dt_to_ms(getattr(room, "updated_at", None)),
        "closed_at": _dt_to_ms(getattr(room, "closed_at", None)),
    }


async def create_room(
    owner,
    name: str,
    visibility: str = VISIBILITY_PUBLIC,
    password: Optional[str] = None,
    background_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Room:
    """创建房间并自动把创建者加为 owner 成员。"""
    name = _validate_room_name(name)
    visibility = _validate_visibility(visibility)
    background_id = _validate_background(background_id)

    password_hash = None
    if visibility == VISIBILITY_PRIVATE:
        if not password:
            raise RoomError(ERR_ROOM_PASSWORD_REQUIRED, "私有房间必须设置密码")
        password_hash = hash_password(_validate_room_password(password))
    # 公共房间的密码参数忽略（不存储）

    code = None
    for _attempt in range(ROOM_CODE_RETRIES):
        candidate = generate_room_code()
        if not await RoomDao.code_exists(candidate):
            code = candidate
            break
    if code is None:
        raise RoomError(
            ERR_ROOM_CODE_GENERATION_FAILED,
            "无法生成唯一房间码，请稍后重试",
            500,
        )

    room = await RoomDao.create(
        id=uuid.uuid4().hex,
        code=code,
        name=name,
        visibility=visibility,
        password_hash=password_hash,
        owner=owner,
        background_id=background_id,
    )
    await RoomMemberDao.add(room, owner, role="owner")
    return room


async def list_public_rooms(
    limit: int = DEFAULT_PAGE_SIZE, before: Any = None
) -> Tuple[List[Tuple[Room, int]], bool, Optional[int]]:
    """公共房间分页列表：只含未关闭 public，按 created_at 倒序。

    返回 ``(items[(room, member_count)], has_more, next_before_ms)``。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RoomError("INVALID_PAGINATION", "limit 必须是整数") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise RoomError(
            "INVALID_PAGINATION", f"limit 需在 1-{MAX_PAGE_SIZE} 之间"
        )
    before_dt = _ms_to_datetime(before)

    rooms = await RoomDao.list_public(limit, before_dt)
    rooms = [
        room
        for room in rooms
        if room.visibility == VISIBILITY_PUBLIC
        and getattr(room, "closed_at", None) is None
        and not getattr(room, "deleted", False)
    ]

    has_more = len(rooms) > limit
    page = rooms[:limit]
    items = []
    for room in page:
        items.append((room, await RoomMemberDao.count(room.id)))
    next_before = _dt_to_ms(page[-1].created_at) if has_more and page else None
    return items, has_more, next_before


async def get_room_detail(room_id_or_code: str) -> Tuple[Room, List[Dict[str, Any]]]:
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)
    return room, await list_members(room)


async def get_room_for_member(
    room_id_or_code: str, user
) -> Tuple[Room, List[Dict[str, Any]]]:
    """WS 进房前校验：房间存在、未关闭、且用户已是房间成员（HTTP join 过）。"""
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)
    membership = await RoomMemberDao.get(room.id, getattr(user, "id", None))
    if membership is None:
        raise RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
    return room, await list_members(room)


async def get_room_membership(
    room_id_or_code: str, user
) -> Tuple[Room, RoomMember]:
    """历史窗口等场景：返回房间 + 当前用户的成员关系（含 joined_at）。"""
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)
    membership = await RoomMemberDao.get(room.id, getattr(user, "id", None))
    if membership is None:
        raise RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
    return room, membership


async def ensure_member(room: Room, user) -> None:
    """校验房间未关闭且用户是房间成员（聊天等房间内操作复用）。"""
    _ensure_open(room)
    membership = await RoomMemberDao.get(room.id, getattr(user, "id", None))
    if membership is None:
        raise RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)


async def join_room(
    user,
    room_id_or_code: str,
    password: Optional[str] = None,
) -> Tuple[Room, List[Dict[str, Any]]]:
    """加入房间；已在房间内幂等返回。"""
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)

    existing = await RoomMemberDao.get(room.id, getattr(user, "id", None))
    if existing is not None:
        return room, await list_members(room)

    if room.visibility == VISIBILITY_PRIVATE and getattr(room, "password_hash", None):
        if not password:
            raise RoomError(ERR_ROOM_PASSWORD_REQUIRED, "该房间需要密码", 400)
        if not verify_password(password, room.password_hash):
            raise RoomError(ERR_ROOM_PASSWORD_INVALID, "房间密码错误", 400)

    member_count = await RoomMemberDao.count(room.id)
    if member_count >= MAX_MEMBERS:
        raise RoomError(ERR_ROOM_FULL, f"房间人数已满（上限 {MAX_MEMBERS} 人）", 409)

    await RoomMemberDao.add(room, user, role="member")
    return room, await list_members(room)


async def leave_room(user, room_id_or_code: str) -> bool:
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)

    if str(getattr(user, "id", "")) == str(getattr(room, "owner_id", "")):
        raise RoomError(
            ERR_ROOM_OWNER_MUST_CLOSE, "房主离开前需先关闭房间", 409
        )

    member = await RoomMemberDao.get(room.id, getattr(user, "id", None))
    if member is None:
        raise RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)

    await RoomMemberDao.remove(room.id, getattr(user, "id", None))
    return True


async def kick_member(owner, room_id_or_code: str, target_user_id: Any) -> List[Dict[str, Any]]:
    room = await resolve_room(room_id_or_code)
    _ensure_open(room)
    _ensure_owner(owner, room)

    target_id = str(target_user_id)
    if target_id == str(getattr(owner, "id", "")):
        raise RoomError(ERR_CANNOT_KICK_OWNER, "不能踢出房主", 400)

    try:
        target_pk = int(target_user_id)
    except (TypeError, ValueError) as exc:
        raise RoomError(ERR_MEMBER_NOT_FOUND, "成员不存在", 404) from exc

    member = await RoomMemberDao.get(room.id, target_pk)
    if member is None:
        raise RoomError(ERR_MEMBER_NOT_FOUND, "成员不存在", 404)

    await RoomMemberDao.remove(room.id, target_pk)
    return await list_members(room)


async def close_room(owner, room_id_or_code: str) -> Room:
    room = await resolve_room(room_id_or_code)
    _ensure_owner(owner, room)
    if getattr(room, "closed_at", None) is None:
        await RoomDao.close(room)
        room = await RoomDao.get_by_id(room.id) or room
    return room


async def delete_room(owner, room_id_or_code: str) -> Room:
    """F2 软删除：仅房主；已删除再删幂等成功，不物理删数据。"""
    room = await resolve_room(room_id_or_code, include_deleted=True)
    _ensure_owner_for_mutation(owner, room)
    if getattr(room, "deleted", False):
        return room
    await RoomDao.soft_delete(room)
    return await RoomDao.get_by_id(room.id) or room


async def update_room(owner, room_id_or_code: str, changes: Dict[str, Any]) -> Room:
    """F3 房主修改房间：仅 name / password；visibility 不可切换。"""
    room = await resolve_room(room_id_or_code)
    _ensure_owner_for_mutation(owner, room)
    _ensure_open(room)

    fields: Dict[str, Any] = {}
    if "name" in changes:
        fields["name"] = _validate_room_name(changes["name"])
    if "password" in changes:
        new_password = changes["password"]
        if new_password is None:
            fields["password_hash"] = None
        else:
            if room.visibility != VISIBILITY_PRIVATE:
                raise RoomError(
                    ERR_INVALID_ROOM_PAYLOAD,
                    "公开房不支持设置密码；如需加密请新建私有房",
                    400,
                )
            fields["password_hash"] = hash_password(
                _validate_room_password(new_password)
            )

    if not fields:
        return room
    updated = await RoomDao.update(room, **fields)
    return updated or room
