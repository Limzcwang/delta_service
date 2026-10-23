"""对局识别诊断批量上报（0.2.12 B6）。"""

from __future__ import annotations

import gzip
import json

from fastapi import APIRouter, Depends, Request

from api.deps import get_optional_user
from api.v1_common import (
    APIError,
    CODE_BAD_REQUEST,
    CODE_RATE_LIMITED,
    CODE_UNAVAILABLE,
    ok,
)
from log.logger import log_exception, logger
from service import diagnostics as diagnostics_service

router = APIRouter(prefix="/api/v1", tags=["v1-diagnostics"])

CODE_PAYLOAD_TOO_LARGE = 41300


def _raise(exc: diagnostics_service.DiagnosticError) -> None:
    code = {
        400: CODE_BAD_REQUEST,
        413: CODE_PAYLOAD_TOO_LARGE,
        429: CODE_RATE_LIMITED,
        503: CODE_UNAVAILABLE,
    }.get(exc.status_code, CODE_BAD_REQUEST)
    raise APIError(
        exc.status_code, code, exc.message, error_code=exc.error_code
    )


def _client_diag_id(request: Request, body_diag_id=None) -> str:
    value = getattr(request, "headers", {}).get("X-Diag-Id")
    if value is None:
        value = getattr(request, "headers", {}).get("x-diag-id")
    value = (value or body_diag_id or "").strip()
    return value[:36]


@router.post("/diagnostics/batch", summary="诊断事件批量上报")
async def upload_diagnostics(
    request: Request,
    user=Depends(get_optional_user),
):
    client_diag_id = _client_diag_id(request)
    try:
        raw = await request.body()
        encoding = (
            getattr(request, "headers", {}).get("Content-Encoding") or ""
        ).lower()
        if encoding == "gzip":
            if len(raw) > diagnostics_service.MAX_GZIP_BYTES:
                _raise(diagnostics_service.DiagnosticError(
                    diagnostics_service.DIAGNOSTICS_TOO_LARGE,
                    "gzip body 超过 128KB",
                    413,
                ))
            try:
                raw = gzip.decompress(raw)
            except OSError as exc:
                raise APIError(
                    400,
                    CODE_BAD_REQUEST,
                    "gzip body 非法",
                    error_code=diagnostics_service.INVALID_DIAGNOSTICS_PAYLOAD,
                ) from exc
        if len(raw) > diagnostics_service.MAX_BODY_BYTES:
            _raise(diagnostics_service.DiagnosticError(
                diagnostics_service.DIAGNOSTICS_TOO_LARGE,
                "body 超过 512KB",
                413,
            ))
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(
                400,
                CODE_BAD_REQUEST,
                "body 不是合法 JSON",
                error_code=diagnostics_service.INVALID_DIAGNOSTICS_PAYLOAD,
            ) from exc

        meta, events, skipped = diagnostics_service.validate_batch(body)
        header_diag = _client_diag_id(request)
        if header_diag:
            meta["client_diag_id"] = header_diag
        client_diag_id = meta["client_diag_id"]
        if not client_diag_id:
            _raise(diagnostics_service.DiagnosticError(
                diagnostics_service.INVALID_DIAGNOSTICS_PAYLOAD,
                "缺少 client_diag_id",
                400,
            ))
    except diagnostics_service.DiagnosticError as exc:
        _raise(exc)

    account_user_id = getattr(user, "id", None) if user is not None else None
    rate_key = (
        f"user:{account_user_id}"
        if account_user_id is not None
        else f"diag:{client_diag_id}"
    )
    if not diagnostics_service.rate_limiter.check(rate_key):
        raise APIError(
            429,
            CODE_RATE_LIMITED,
            "诊断上报过于频繁",
            error_code=diagnostics_service.DIAGNOSTICS_RATE_LIMITED,
        )

    try:
        accepted = await diagnostics_service.save_batch(
            meta, events, int(account_user_id) if account_user_id is not None else None
        )
    except Exception as exc:  # 诊断失败绝不影响主流程/客户端
        log_exception(
            exc,
            "诊断批量入库失败",
            {
                "account_user_id": str(account_user_id) if account_user_id is not None else "anonymous",
                "client_diag_id": client_diag_id,
            },
        )
        accepted = 0

    logger.info(
        "诊断批量接收: accepted=%s skipped=%s account_user_id=%s client_diag_id=%s",
        accepted,
        skipped,
        account_user_id if account_user_id is not None else "anonymous",
        client_diag_id,
    )
    return ok(
        {"accepted": accepted, "skipped": skipped},
        request=request,
    )
