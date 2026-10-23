"""v1 统一响应包与 API 错误（PROTOCOL.md §1.4 / §7）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from log.logger import logger, resolve_request_id

# 业务码（与 HTTP 状态码分离，见 PROTOCOL §7.1）
CODE_OK = 200
CODE_BAD_REQUEST = 40000
CODE_UNAUTHORIZED = 40100
CODE_TOKEN_EXPIRED = 40101
CODE_TOKEN_INVALID = 40102
CODE_FORBIDDEN = 40300
CODE_NOT_FOUND = 40400
CODE_USER_NOT_FOUND = 40401
CODE_CONFLICT = 40900
CODE_RATE_LIMITED = 42900
CODE_INTERNAL = 50000
CODE_DEPENDENCY = 50001
CODE_UNAVAILABLE = 50300


class APIError(Exception):
    """v1 业务异常：由 api_error_handler 统一转成 ``{code,message,data,request_id?}``。"""

    def __init__(
        self,
        status_code: int,
        code: int,
        message: str,
        headers: Optional[Dict[str, str]] = None,
        error_code: Optional[str] = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        # 业务字符串错误码（客户端据此做特定交互，如弹房间密码框）
        self.error_code = error_code
        super().__init__(message)


def ok(
    data: Any = None,
    *,
    message: str = "OK",
    request: Any = None,
) -> Dict[str, Any]:
    """构造成功响应体；传 request 时附带 request_id。"""
    body: Dict[str, Any] = {"code": CODE_OK, "message": message, "data": data}
    if request is not None:
        body["request_id"] = resolve_request_id(request)
    return body


def error_body(
    code: int,
    message: str,
    request: Any = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """构造错误响应体；error_code 放在 data.error_code（客户端依赖）。"""
    data = {"error_code": error_code} if error_code else None
    body: Dict[str, Any] = {"code": code, "message": message, "data": data}
    if request is not None:
        body["request_id"] = resolve_request_id(request)
    return body


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """FastAPI 异常处理器：APIError → v1 统一错误包。"""
    headers = getattr(request, "headers", {}) or {}
    account_user_id = getattr(
        getattr(request, "state", None), "account_user_id", None
    ) or "anonymous"
    client_diag_id = headers.get("X-Diag-Id") or headers.get("x-diag-id") or "-"
    logger.warning(
        "API错误 code=%s error_code=%s path=%s account_user_id=%s client_diag_id=%s",
        exc.code,
        exc.error_code or "-",
        getattr(getattr(request, "url", None), "path", "-"),
        account_user_id,
        client_diag_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            exc.code, exc.message, request, error_code=exc.error_code
        ),
        headers=exc.headers or None,
    )
