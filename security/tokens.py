"""JWT 访问令牌工具（HS256）。

配置全部来自环境变量（见 PROTOCOL.md §2）：
- ``JWT_SECRET``：**必需**，缺失/空白时在首次使用时直接报错，不提供内置默认密钥；
- ``JWT_EXPIRE_DAYS``：可选，默认 7 天。

令牌 claims：``sub``（用户 ID 字符串）、``iat``、``exp``、``typ="access"``。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

import jwt

ACCESS_TOKEN_TYPE = "access"
ALGORITHM = "HS256"
DEFAULT_EXPIRE_DAYS = 7

# 容忍桌面端时钟漂移：过期后 60s 内仍接受（与 PROTOCOL §2.1 一致）
LEEWAY_SECONDS = 60

# JWT_SECRET 推荐长度（字节）；不足时只告警、不阻断，避免打断本地开发
SECRET_MIN_BYTES = 32

_REQUIRED_CLAIMS = ("sub", "iat", "exp", "typ")

_logger = logging.getLogger("delta_service")
_weak_secret_warned = False


class TokenError(Exception):
    """JWT 相关错误基类。"""


class TokenConfigError(TokenError, RuntimeError):
    """JWT 配置缺失/非法（JWT_SECRET、JWT_EXPIRE_DAYS）。"""


class TokenInvalidError(TokenError):
    """令牌无效（签名错、格式错、typ 不符、用户不存在等）。"""


class TokenExpiredError(TokenInvalidError):
    """令牌已过期。"""


def _require_secret(secret: Optional[str] = None) -> str:
    global _weak_secret_warned

    value = secret if secret is not None else os.getenv("JWT_SECRET")
    if value is None or not value.strip():
        raise TokenConfigError("缺少必需的环境变量: JWT_SECRET")
    value = value.strip()

    # 长度不足只告警一次、不阻断（本地开发可能用短密钥）
    if (
        not _weak_secret_warned
        and len(value.encode("utf-8")) < SECRET_MIN_BYTES
    ):
        _weak_secret_warned = True
        _logger.warning(
            "JWT_SECRET 长度不足 %d 字节，生产环境请使用至少 %d 字节的随机密钥",
            SECRET_MIN_BYTES,
            SECRET_MIN_BYTES,
        )
    return value


def get_expire_days() -> int:
    """读取 JWT 有效期（天），默认 7。"""
    raw = os.getenv("JWT_EXPIRE_DAYS", str(DEFAULT_EXPIRE_DAYS))
    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise TokenConfigError(
            f"JWT_EXPIRE_DAYS 必须是正整数，当前值: {raw!r}"
        ) from exc
    if days <= 0:
        raise TokenConfigError(f"JWT_EXPIRE_DAYS 必须是正整数，当前值: {days!r}")
    return days


def token_ttl_seconds() -> int:
    """令牌有效期（秒）。"""
    return get_expire_days() * 24 * 60 * 60


def create_access_token(
    user_id,
    *,
    secret: Optional[str] = None,
    now: Optional[float] = None,
    expires_in_seconds: Optional[int] = None,
) -> Tuple[str, int]:
    """签发访问令牌，返回 ``(token, expires_in_seconds)``。"""
    if user_id is None or str(user_id).strip() == "":
        raise ValueError("user_id 不能为空")

    key = _require_secret(secret)
    issued_at = int(now if now is not None else time.time())
    ttl = int(expires_in_seconds if expires_in_seconds is not None else token_ttl_seconds())
    if ttl <= 0:
        raise ValueError("expires_in_seconds 必须为正数")

    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + ttl,
        "typ": ACCESS_TOKEN_TYPE,
    }
    token = jwt.encode(payload, key, algorithm=ALGORITHM)
    if isinstance(token, bytes):  # PyJWT 1.x 兼容；2.x 返回 str
        token = token.decode("ascii")
    return token, ttl


def decode_access_token(
    token: str,
    *,
    secret: Optional[str] = None,
    now: Optional[float] = None,
) -> str:
    """校验并解析访问令牌，返回用户 ID 字符串。

    校验：HS256 签名、必需 claims、``typ=="access"``、过期时间
    （允许 ``LEEWAY_SECONDS`` 秒时钟偏差）。
    失败抛 ``TokenExpiredError`` / ``TokenInvalidError`` / ``TokenConfigError``。
    """
    if not isinstance(token, str) or not token.strip():
        raise TokenInvalidError("token 为空")

    key = _require_secret(secret)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            options={
                # 过期由本模块用可注入的 now 校验，便于测试不依赖真实时钟
                "verify_exp": False,
                "verify_iat": False,
                "require": list(_REQUIRED_CLAIMS),
            },
        )
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError("token 无效") from exc

    if not isinstance(payload, dict) or payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise TokenInvalidError("token 类型不正确")

    subject = payload.get("sub")
    if not isinstance(subject, str) or subject == "":
        raise TokenInvalidError("token 缺少有效的 sub")

    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)):
        raise TokenInvalidError("token 缺少有效的 exp")

    current = now if now is not None else time.time()
    # 允许 LEEWAY_SECONDS 的时钟偏差：过期 59s 内仍通过，超过 60s 拒绝。
    if current >= expires_at + LEEWAY_SECONDS:
        raise TokenExpiredError("token 已过期")

    return subject
