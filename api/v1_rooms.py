"""v1 房间 HTTP 接口（B2a）。

路径参数 ``{room_id_or_code}`` 同时接受 5 位 room_code 与内部 id。
错误体：``{code,message,data:{error_code}}``，字符串错误码供客户端做特定交互。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.v1_common import (
    CODE_BAD_REQUEST,
    CODE_CONFLICT,
    CODE_FORBIDDEN,
    CODE_INTERNAL,
    CODE_NOT_FOUND,
    APIError,
    ok,
)
from service import backgrounds as backgrounds_service
from service import rooms as rooms_service
from service.rooms import RoomError

router = APIRouter(prefix="/api/v1", tags=["v1-rooms"])

CODE_ROOM_NOT_FOUND = 40402
CODE_ROOM_MEMBER_NOT_FOUND = 40400
CODE_NOT_OWNER = 40300
CODE_ROOM_CONFLICT = 40900
CODE_ROOM_CLOSED = 40901

# error_code -> (HTTP 状态码, v1 数字业务码)
_ERROR_MAP = {
    rooms_service.ERR_ROOM_NOT_FOUND: (404, CODE_ROOM_NOT_FOUND),
    rooms_service.ERR_ROOM_PASSWORD_REQUIRED: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_ROOM_PASSWORD_INVALID: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_ROOM_FULL: (409, CODE_ROOM_CONFLICT),
    rooms_service.ERR_ROOM_CLOSED: (409, CODE_ROOM_CLOSED),
    rooms_service.ERR_ROOM_CODE_GENERATION_FAILED: (500, CODE_INTERNAL),
    rooms_service.ERR_NOT_ROOM_MEMBER: (403, CODE_FORBIDDEN),
    rooms_service.ERR_NOT_ROOM_OWNER: (403, CODE_NOT_OWNER),
    rooms_service.ERR_MEMBER_NOT_FOUND: (404, CODE_ROOM_MEMBER_NOT_FOUND),
    rooms_service.ERR_INVALID_BACKGROUND: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_ROOM_NAME_INVALID: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_ROOM_OWNER_MUST_CLOSE: (409, CODE_ROOM_CONFLICT),
    rooms_service.ERR_CANNOT_KICK_OWNER: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_INVALID_VISIBILITY: (400, CODE_BAD_REQUEST),
    rooms_service.ERR_ROOM_PASSWORD_NOT_ALLOWED: (400, CODE_BAD_REQUEST),
    "INVALID_PAGINATION": (400, CODE_BAD_REQUEST),
    "INVALID_CURSOR": (400, CODE_BAD_REQUEST),
}


def _raise_room_error(exc: RoomError) -> None:
    status, code = _ERROR_MAP.get(exc.error_code, (400, CODE_BAD_REQUEST))
    raise APIError(
        status,
        code,
        exc.message,
        error_code=exc.error_code,
    )


class CreateRoomRequest(BaseModel):
    name: str = ""
    visibility: str = "public"
    password: Optional[str] = None
    background_id: Optional[str] = None


class JoinRoomRequest(BaseModel):
    password: Optional[str] = None


class UpdateRoomRequest(BaseModel):
    name: Optional[str] = None
    background_id: Optional[str] = None
    password: Optional[str] = None


class KickMemberRequest(BaseModel):
    user_id: str


@router.get("/backgrounds", summary="运营预置底图列表")
async def list_backgrounds(request: Request, user=Depends(get_current_user)):
    """读取 assets/backgrounds/manifest.json；文件不存在时返回空列表。"""
    return ok(
        {"items": backgrounds_service.load_backgrounds()},
        request=request,
    )


@router.post("/rooms", summary="创建房间")
async def create_room(
    request: Request,
    payload: CreateRoomRequest,
    user=Depends(get_current_user),
):
    try:
        room = await rooms_service.create_room(
            user,
            name=payload.name,
            visibility=payload.visibility,
            password=payload.password,
            background_id=payload.background_id,
        )
        members = await rooms_service.list_members(room)
    except RoomError as exc:
        _raise_room_error(exc)

    return ok(
        {"room": rooms_service.room_to_dict(room, len(members))},
        request=request,
    )


@router.get("/rooms", summary="公共房间列表")
async def list_rooms(
    request: Request,
    limit: int = rooms_service.DEFAULT_PAGE_SIZE,
    before: Optional[int] = None,
    user=Depends(get_current_user),
):
    try:
        items, has_more, next_before = await rooms_service.list_public_rooms(
            limit, before
        )
    except RoomError as exc:
        _raise_room_error(exc)

    return ok(
        {
            "items": [
                rooms_service.room_to_dict(room, member_count)
                for room, member_count in items
            ],
            "has_more": has_more,
            "next_before": next_before,
        },
        request=request,
    )


@router.get("/rooms/{room_id_or_code}", summary="房间详情")
async def get_room(
    request: Request,
    room_id_or_code: str,
    user=Depends(get_current_user),
):
    try:
        room, members = await rooms_service.get_room_detail(room_id_or_code)
    except RoomError as exc:
        _raise_room_error(exc)

    return ok(
        {
            "room": rooms_service.room_to_dict(room, len(members)),
            "members": members,
        },
        request=request,
    )


@router.patch("/rooms/{room_id_or_code}", summary="更新房间（仅房主）")
async def update_room(
    request: Request,
    room_id_or_code: str,
    payload: UpdateRoomRequest,
    user=Depends(get_current_user),
):
    changes = payload.model_dump(exclude_unset=True)
    try:
        room = await rooms_service.update_room(user, room_id_or_code, changes)
        members = await rooms_service.list_members(room)
    except RoomError as exc:
        _raise_room_error(exc)

    return ok(
        {"room": rooms_service.room_to_dict(room, len(members))},
        request=request,
    )


@router.delete("/rooms/{room_id_or_code}", summary="关闭房间（仅房主）")
async def close_room(
    request: Request,
    room_id_or_code: str,
    user=Depends(get_current_user),
):
    try:
        await rooms_service.close_room(user, room_id_or_code)
    except RoomError as exc:
        _raise_room_error(exc)
    return ok({"closed": True}, request=request)


@router.post("/rooms/{room_id_or_code}/join", summary="加入房间")
async def join_room(
    request: Request,
    room_id_or_code: str,
    payload: JoinRoomRequest,
    user=Depends(get_current_user),
):
    try:
        room, members = await rooms_service.join_room(
            user, room_id_or_code, password=payload.password
        )
    except RoomError as exc:
        _raise_room_error(exc)

    return ok(
        {
            "room": rooms_service.room_to_dict(room, len(members)),
            "members": members,
        },
        request=request,
    )


@router.post("/rooms/{room_id_or_code}/leave", summary="离开房间")
async def leave_room(
    request: Request,
    room_id_or_code: str,
    user=Depends(get_current_user),
):
    try:
        await rooms_service.leave_room(user, room_id_or_code)
    except RoomError as exc:
        _raise_room_error(exc)
    return ok({"left": True}, request=request)


@router.post("/rooms/{room_id_or_code}/kick", summary="踢出成员（仅房主）")
async def kick_member(
    request: Request,
    room_id_or_code: str,
    payload: KickMemberRequest,
    user=Depends(get_current_user),
):
    try:
        members = await rooms_service.kick_member(
            user, room_id_or_code, payload.user_id
        )
    except RoomError as exc:
        _raise_room_error(exc)
    return ok({"members": members}, request=request)
