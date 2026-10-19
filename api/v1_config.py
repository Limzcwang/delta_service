"""公开的运营干员配置下发接口（配置中心）。"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from api.v1_common import (
    APIError,
    CODE_RATE_LIMITED,
    CODE_UNAVAILABLE,
)
from security.login_limit import LoginRateLimiter
from service.config_center import OperatorConfigError, config_center

router = APIRouter(prefix="/api/v1", tags=["v1-config"])


def _build_config_rate_limiter() -> LoginRateLimiter:
    raw = os.getenv("CONFIG_RATE_LIMIT_PER_MIN", "60")
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 60
    if limit <= 0:
        limit = 60
    return LoginRateLimiter(max_attempts=limit, window_seconds=60)


# 公开接口但保底限频；测试可直接替换本模块的 config_rate_limiter
config_rate_limiter = _build_config_rate_limiter()


def _client_ip(request: Any) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or "unknown"


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """支持 If-None-Match 的逗号列表与 W/ 弱校验。"""
    expected = etag.strip()
    if expected.startswith("W/"):
        expected = expected[2:]
    for token in str(if_none_match).split(","):
        candidate = token.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == expected:
            return True
    return False


@router.get("/config/operators", summary="运营干员配置（公开）")
async def get_operators_config(request: Request):
    """公开可读；ETag/Cache-Control 协商缓存；配置缺失/非法返回 503。"""
    client_ip = _client_ip(request)
    if not config_rate_limiter.check(client_ip):
        raise APIError(
            429,
            CODE_RATE_LIMITED,
            "配置请求过于频繁，请稍后再试",
            headers={"Retry-After": str(max(config_rate_limiter.retry_after(client_ip), 1))},
        )

    try:
        payload = config_center.get()
    except OperatorConfigError as exc:
        raise APIError(
            503,
            CODE_UNAVAILABLE,
            "干员配置暂不可用",
            error_code="CONFIG_UNAVAILABLE",
        ) from exc

    etag = f'"{payload["etag"]}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=60"}
    if_none_match = getattr(request, "headers", {}).get("If-None-Match")
    if if_none_match and _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=payload, headers=headers)
