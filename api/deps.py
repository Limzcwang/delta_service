"""FastAPI 依赖：JWT Bearer 认证（PROTOCOL.md §2.3）。"""

from __future__ import annotations

from fastapi import Request

from api.v1_common import (
    APIError,
    CODE_INTERNAL,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_UNAUTHORIZED,
)
from dao.user import UserDao
from log.logger import log_exception
from security.tokens import (
    TokenConfigError,
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)


def _extract_bearer_token(request: Request) -> str:
    """从 Authorization 头解析 Bearer token；缺失/格式错误统一 40100。"""
    headers = getattr(request, "headers", None)
    authorization = None
    if headers is not None:
        authorization = headers.get("Authorization") or headers.get("authorization")

    if not authorization:
        raise APIError(401, CODE_UNAUTHORIZED, "未认证")

    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise APIError(401, CODE_UNAUTHORIZED, "未认证")
    return parts[1].strip()


async def get_current_user(request: Request):
    """校验 Bearer token 并返回未删除的用户对象。

    失败统一 401：缺失/格式错(40100)、过期(40101)、无效/用户不存在(40102)。
    """
    token = _extract_bearer_token(request)

    try:
        user_id = decode_access_token(token)
    except TokenExpiredError as exc:
        raise APIError(401, CODE_TOKEN_EXPIRED, "登录已过期，请重新登录") from exc
    except TokenInvalidError as exc:
        raise APIError(401, CODE_TOKEN_INVALID, "登录凭证无效") from exc
    except TokenConfigError as exc:
        log_exception(exc, "JWT 配置错误")
        raise APIError(500, CODE_INTERNAL, "服务端认证配置错误") from exc

    try:
        user_pk = int(user_id)
    except (TypeError, ValueError) as exc:
        raise APIError(401, CODE_TOKEN_INVALID, "登录凭证无效") from exc

    # UserDao.get_user_by_id 已过滤 deleted=False
    user = await UserDao.get_user_by_id(user_pk)
    if user is None:
        raise APIError(401, CODE_TOKEN_INVALID, "登录凭证无效")
    try:
        request.state.account_user_id = str(getattr(user, "id", ""))
    except Exception:
        pass
    return user


async def get_optional_user(request: Request):
    """诊断上报用：无 Authorization 时匿名；有则按普通用户校验。"""
    headers = getattr(request, "headers", None)
    authorization = None
    if headers is not None:
        authorization = headers.get("Authorization") or headers.get("authorization")
    if not authorization:
        try:
            request.state.account_user_id = "anonymous"
        except Exception:
            pass
        return None
    return await get_current_user(request)
