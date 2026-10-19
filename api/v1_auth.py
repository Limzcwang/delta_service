"""v1 认证与用户自助接口（PROTOCOL.md §2.2 / §3.2）。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.v1_common import (
    APIError,
    CODE_BAD_REQUEST,
    CODE_INTERNAL,
    CODE_UNAUTHORIZED,
    ok,
)
from log.logger import log_exception
from security.tokens import TokenConfigError, create_access_token
from service.user import UserService

router = APIRouter(prefix="/api/v1", tags=["v1"])


class LoginRequest(BaseModel):
    """v1 登录请求。"""

    username: str
    password: str


def _user_summary(user: Any) -> Dict[str, Any]:
    """把用户对象/字典收敛为对外字段（不含 password）。"""
    if isinstance(user, dict):
        user_id = user.get("id")
        username = user.get("username")
    else:
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)
    return {"id": str(user_id), "username": username}


@router.post("/auth/login", summary="登录并签发 JWT")
async def login(request: Request, payload: LoginRequest):
    """校验用户名口令，成功返回 access_token。

    口令校验复用 service 层（scrypt + 旧明文兼容）；失败统一 401，不区分用户是否存在。
    """
    username = (payload.username or "").strip()
    password = payload.password or ""
    if not username or not password:
        raise APIError(400, CODE_BAD_REQUEST, "用户名和密码不能为空")

    result = await UserService.login(username, password)
    if not result.get("success"):
        raise APIError(401, CODE_UNAUTHORIZED, "用户名或密码错误")

    user = result.get("user") or {}
    try:
        access_token, expires_in = create_access_token(user.get("id"))
    except TokenConfigError as exc:
        # 配置错误不把细节回传客户端，只记服务端日志
        log_exception(exc, "JWT 配置错误")
        raise APIError(500, CODE_INTERNAL, "服务端认证配置错误") from exc

    return ok(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": _user_summary(user),
        },
        request=request,
    )


@router.get("/users/me", summary="获取当前登录用户")
async def get_me(request: Request, user=Depends(get_current_user)):
    """需要 ``Authorization: Bearer <token>``。"""
    created_at = getattr(user, "created_at", None)
    created_at_ms = (
        int(created_at.timestamp() * 1000) if created_at is not None else None
    )
    return ok(
        {
            "id": str(getattr(user, "id", "")),
            "username": getattr(user, "username", ""),
            "created_at": created_at_ms,
        },
        request=request,
    )
