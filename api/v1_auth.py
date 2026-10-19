"""v1 认证与用户自助接口（PROTOCOL.md §2.2 / §3.2）。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.v1_common import (
    APIError,
    CODE_BAD_REQUEST,
    CODE_CONFLICT,
    CODE_INTERNAL,
    CODE_RATE_LIMITED,
    CODE_UNAUTHORIZED,
    ok,
)
from log.logger import log_exception
from security.login_limit import build_login_rate_limiter
from security.tokens import TokenConfigError, create_access_token
from service.user import UserService

router = APIRouter(prefix="/api/v1", tags=["v1"])

# 登录限频（内存、按 IP、5 次/分钟，可由 LOGIN_RATE_LIMIT_PER_MIN 调整）。
# 测试可直接替换本模块的 login_rate_limiter 注入假时钟。
login_rate_limiter = build_login_rate_limiter()


def _client_ip(request: Any) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or "unknown"


class LoginRequest(BaseModel):
    """v1 登录请求。"""

    username: str
    password: str


class RegisterRequest(BaseModel):
    """v1 注册请求。"""

    username: str
    password: str


class UpdateMeRequest(BaseModel):
    """v1 修改昵称请求。"""

    nickname: str


class ChangePasswordRequest(BaseModel):
    """v1 修改密码请求。"""

    old_password: str
    new_password: str


# F1：新注册用户名 6–20 位字母/数字，且至少一个字母；大小写敏感；老账号不受影响。
_USERNAME_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{6,20}$")
_PASSWORD_MIN_LENGTH = 6
NICKNAME_MIN_LENGTH = 1
NICKNAME_MAX_LENGTH = 20
NEW_PASSWORD_MIN_LENGTH = 8
NEW_PASSWORD_MAX_LENGTH = 64


def _token_response(user: Any, request: Any = None) -> Dict[str, Any]:
    """签发 JWT 并构造登录/注册统一响应。"""
    summary = _user_summary(user)
    try:
        access_token, expires_in = create_access_token(summary["id"])
    except TokenConfigError as exc:
        # 配置错误不把细节回传客户端，只记服务端日志
        log_exception(exc, "JWT 配置错误")
        raise APIError(500, CODE_INTERNAL, "服务端认证配置错误") from exc
    return ok(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": summary,
        },
        request=request,
    )


def _user_summary(user: Any) -> Dict[str, Any]:
    """当前登录用户本人的概要（可含自己的 username；不含 password）。"""
    if isinstance(user, dict):
        user_id = user.get("id")
        username = user.get("username")
        nickname = user.get("nickname")
    else:
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)
        nickname = getattr(user, "nickname", None)
    return {
        "id": str(user_id),
        "username": username,
        "nickname": nickname if nickname is not None else username,
    }


def _validate_username(username: str) -> str:
    username = (username or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        raise APIError(
            400,
            CODE_BAD_REQUEST,
            "用户名需为 6-20 位字母或数字，且至少包含一个字母",
            error_code="INVALID_USERNAME",
        )
    return username


def _validate_nickname(nickname: Any) -> str:
    if not isinstance(nickname, str):
        raise APIError(
            400, CODE_BAD_REQUEST, "昵称必须是字符串", error_code="INVALID_NICKNAME"
        )
    # 去掉控制字符（Unicode C* 类）后去首尾空白
    cleaned = "".join(ch for ch in nickname if unicodedata.category(ch)[0] != "C")
    cleaned = cleaned.strip()
    if not NICKNAME_MIN_LENGTH <= len(cleaned) <= NICKNAME_MAX_LENGTH:
        raise APIError(
            400,
            CODE_BAD_REQUEST,
            f"昵称长度需在 {NICKNAME_MIN_LENGTH}-{NICKNAME_MAX_LENGTH} 个字符之间",
            error_code="INVALID_NICKNAME",
        )
    return cleaned


def _validate_new_password(password: Any) -> str:
    if not isinstance(password, str) or not (
        NEW_PASSWORD_MIN_LENGTH <= len(password) <= NEW_PASSWORD_MAX_LENGTH
    ):
        raise APIError(
            400,
            CODE_BAD_REQUEST,
            f"新密码长度需在 {NEW_PASSWORD_MIN_LENGTH}-{NEW_PASSWORD_MAX_LENGTH} 个字符之间",
            error_code="INVALID_NEW_PASSWORD",
        )
    return password


@router.post("/auth/login", summary="登录并签发 JWT")
async def login(request: Request, payload: LoginRequest):
    """校验用户名口令，成功返回 access_token。

    口令校验复用 service 层（scrypt + 旧明文兼容）；失败统一 401，不区分用户是否存在。
    """
    client_ip = _client_ip(request)
    if not login_rate_limiter.check(client_ip):
        retry_after = login_rate_limiter.retry_after(client_ip)
        raise APIError(
            429,
            CODE_RATE_LIMITED,
            "登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(max(retry_after, 1))},
        )

    username = (payload.username or "").strip()
    password = payload.password or ""
    if not username or not password:
        raise APIError(400, CODE_BAD_REQUEST, "用户名和密码不能为空")

    result = await UserService.login(username, password)
    if not result.get("success"):
        raise APIError(401, CODE_UNAUTHORIZED, "用户名或密码错误")

    return _token_response(result.get("user") or {}, request=request)


@router.post("/auth/register", summary="注册并签发 JWT")
async def register(request: Request, payload: RegisterRequest):
    """创建用户并直接返回与登录一致的 token 响应。"""
    username = _validate_username(payload.username or "")
    password = payload.password or ""
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise APIError(
            400,
            CODE_BAD_REQUEST,
            f"密码长度不能少于 {_PASSWORD_MIN_LENGTH} 个字符",
        )

    result = await UserService.create_user(username, password)
    if not result.get("success"):
        raise APIError(409, CODE_CONFLICT, "用户名已存在或注册失败")

    return _token_response(result.get("user") or {}, request=request)


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
            "nickname": getattr(user, "nickname", None)
            or getattr(user, "username", ""),
            "created_at": created_at_ms,
        },
        request=request,
    )


@router.patch("/users/me", summary="修改我的昵称")
async def update_me(
    request: Request,
    payload: UpdateMeRequest,
    user=Depends(get_current_user),
):
    """F1.4：昵称 1–20 字符，去首尾空白/控制字符，不要求唯一。"""
    nickname = _validate_nickname(payload.nickname)
    result = await UserService.update_nickname(getattr(user, "id"), nickname)
    if not result.get("success"):
        raise APIError(404, CODE_BAD_REQUEST, "用户不存在")
    return ok({"user": _user_summary(result.get("user") or {})}, request=request)


@router.post("/users/me/password", summary="修改我的密码")
async def change_my_password(
    request: Request,
    payload: ChangePasswordRequest,
    user=Depends(get_current_user),
):
    """F1.5：旧密码校验；新密码 8–64；成功不强制下线已有 token。"""
    new_password = _validate_new_password(payload.new_password)
    result = await UserService.change_password(
        getattr(user, "id"), payload.old_password or "", new_password
    )
    if not result.get("success"):
        if result.get("error_code") == "WRONG_OLD_PASSWORD":
            raise APIError(
                400,
                CODE_BAD_REQUEST,
                "旧密码错误",
                error_code="WRONG_OLD_PASSWORD",
            )
        raise APIError(404, CODE_BAD_REQUEST, "用户不存在")
    return ok({"changed": True}, request=request)
