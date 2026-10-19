"""v1 统一响应包与 API 错误（PROTOCOL.md §1.4 / §7）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from log.logger import resolve_request_id

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


class APIError(Exception):
    """v1 业务异常：由 api_error_handler 统一转成 ``{code,message,data,request_id?}``。"""

    def __init__(
        self,
        status_code: int,
        code: int,
        message: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
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


def error_body(code: int, message: str, request: Any = None) -> Dict[str, Any]:
    """构造错误响应体。"""
    body: Dict[str, Any] = {"code": code, "message": message, "data": None}
    if request is not None:
        body["request_id"] = resolve_request_id(request)
    return body


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """FastAPI 异常处理器：APIError → v1 统一错误包。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, request),
        headers=exc.headers or None,
    )
