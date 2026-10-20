"""内部数据集采集接口（仅测试白名单账号，不面向线上）。"""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from api.deps import get_current_user
from api.v1_common import APIError, CODE_BAD_REQUEST, CODE_FORBIDDEN, CODE_RATE_LIMITED, CODE_UNAVAILABLE, ok
from dao.dataset import DatasetSampleDao
from log.logger import log_exception
from service import dataset as dataset_service

router = APIRouter(prefix="/api/v1", tags=["v1-dataset"])

CODE_PAYLOAD_TOO_LARGE = 41300
DATE_RE = re.compile(r"^\d{8}$")


def _code_for_status(status_code: int) -> int:
    return {
        400: CODE_BAD_REQUEST,
        403: CODE_FORBIDDEN,
        413: CODE_PAYLOAD_TOO_LARGE,
        429: CODE_RATE_LIMITED,
        503: CODE_UNAVAILABLE,
    }.get(status_code, CODE_BAD_REQUEST)


def _raise_dataset_error(exc: dataset_service.DatasetError) -> None:
    raise APIError(
        exc.status_code,
        _code_for_status(exc.status_code),
        exc.message,
        error_code=exc.error_code,
    )


def _require_uploader(user) -> None:
    if not dataset_service.is_uploader(user):
        raise APIError(
            403,
            CODE_FORBIDDEN,
            "无数据集上传权限",
            error_code=dataset_service.ERR_DATASET_FORBIDDEN,
        )


def _require_admin(user) -> None:
    if not dataset_service.is_admin(user):
        raise APIError(
            403,
            CODE_FORBIDDEN,
            "无数据集管理权限",
            error_code=dataset_service.ERR_DATASET_FORBIDDEN,
        )


async def _read_upload(file: Optional[UploadFile], field: str) -> Optional[bytes]:
    if file is None:
        return None
    try:
        data = await file.read()
    except Exception as exc:  # pragma: no cover - 客户端断开等
        raise APIError(
            400,
            CODE_BAD_REQUEST,
            f"{field} 读取失败",
            error_code=dataset_service.ERR_INVALID_DATASET_META,
        ) from exc
    if len(data) > dataset_service.MAX_FILE_BYTES:
        raise dataset_service.DatasetError(
            dataset_service.ERR_DATASET_FILE_TOO_LARGE,
            f"{field} 超过 2MB",
            413,
        )
    return data


@router.post("/dataset/samples", summary="上传内部数据集样本（白名单）")
async def upload_dataset_sample(
    request: Request,
    meta: str = Form(...),
    slots_png: Optional[UploadFile] = File(None, alias="slots.png"),
    avatar_png: Optional[UploadFile] = File(None, alias="avatar.png"),
    user=Depends(get_current_user),
):
    _require_uploader(user)
    if not dataset_service.enabled():
        raise APIError(
            503,
            CODE_UNAVAILABLE,
            "数据集采集已关闭",
            error_code=dataset_service.ERR_DATASET_DISABLED,
        )

    try:
        normalized = dataset_service.validate_meta(meta)
        slots_data = await _read_upload(slots_png, "slots.png")
        avatar_data = await _read_upload(avatar_png, "avatar.png")

        total_bytes = len(meta.encode("utf-8"))
        total_bytes += len(slots_data or b"")
        total_bytes += len(avatar_data or b"")
        if total_bytes > dataset_service.MAX_TOTAL_BYTES:
            raise dataset_service.DatasetError(
                dataset_service.ERR_DATASET_FILE_TOO_LARGE,
                "请求总大小超过 4MB",
                413,
            )
        content_length = getattr(request, "headers", {}).get("Content-Length")
        if content_length:
            try:
                if int(content_length) > dataset_service.MAX_TOTAL_BYTES:
                    raise dataset_service.DatasetError(
                        dataset_service.ERR_DATASET_FILE_TOO_LARGE,
                        "请求总大小超过 4MB",
                        413,
                    )
            except ValueError:
                pass
    except dataset_service.DatasetError as exc:
        _raise_dataset_error(exc)

    date_key = dataset_service.date_key_now()
    quota = dataset_service.daily_quota()
    try:
        used = await DatasetSampleDao.count_for_user_date(
            int(user.id), date_key
        )
    except Exception as exc:  # pragma: no cover
        log_exception(exc, "查询数据集配额失败", {"user": getattr(user, "id", "")})
        raise APIError(500, 50000, "数据集服务暂不可用") from exc
    if used >= quota:
        raise APIError(
            429,
            CODE_RATE_LIMITED,
            "今日数据集上传配额已用完",
            error_code=dataset_service.ERR_DATASET_QUOTA_EXCEEDED,
        )

    sample_id = dataset_service.new_sample_id()
    root = dataset_service.dataset_root()
    rel_dir = dataset_service.sample_rel_dir(date_key, sample_id)
    try:
        files = await asyncio.to_thread(
            dataset_service.write_sample_files,
            root,
            date_key,
            sample_id,
            normalized,
            slots_data,
            avatar_data,
        )
    except Exception as exc:  # pragma: no cover - 磁盘异常
        log_exception(exc, "数据集样本落盘失败", {"sample": sample_id})
        raise APIError(500, 50000, "数据集写入失败") from exc

    try:
        record = await DatasetSampleDao.create_sample(
            user=user,
            username=dataset_service.username_of(user),
            sample_id=sample_id,
            date_key=date_key,
            room_code=normalized["room_code"],
            operator_id=normalized["operator_id"],
            resolution=normalized["resolution"],
            hud_scale=normalized["hud_scale"],
            captured_at=normalized["captured_at"],
            ocr=normalized["ocr"],
            squad_number=normalized["squad_number"],
            client_version=normalized["client_version"],
            rel_dir=rel_dir,
            files=files,
            total_bytes=total_bytes,
        )
    except Exception as exc:  # pragma: no cover - DB 异常回滚文件
        log_exception(exc, "数据集样本入库失败", {"sample": sample_id})
        await asyncio.to_thread(
            shutil.rmtree, root / date_key / sample_id, True
        )
        raise APIError(500, 50000, "数据集入库失败") from exc

    return ok(
        {
            "id": str(getattr(record, "id", "")),
            "sample_id": sample_id,
            "date_key": date_key,
            "files": files,
            "path": rel_dir,
            "quota_remaining": max(quota - used - 1, 0),
        },
        request=request,
    )


@router.get("/dataset/status", summary="数据集上传状态（白名单）")
async def dataset_status(
    request: Request,
    user=Depends(get_current_user),
):
    _require_uploader(user)
    date_key = dataset_service.date_key_now()
    try:
        today_count = await DatasetSampleDao.count_for_user_date(
            int(user.id), date_key
        )
    except Exception as exc:  # pragma: no cover
        log_exception(exc, "查询数据集状态失败", {"user": getattr(user, "id", "")})
        raise APIError(500, 50000, "数据集服务暂不可用") from exc
    quota = dataset_service.daily_quota()
    return ok(
        {
            "enabled": dataset_service.enabled(),
            "date_key": date_key,
            "today_count": int(today_count),
            "quota": quota,
            "remaining": max(quota - int(today_count), 0),
        },
        request=request,
    )


@router.get("/dataset/samples", summary="查询数据集样本（管理员）")
async def list_dataset_samples(
    request: Request,
    date: Optional[str] = None,
    user=Depends(get_current_user),
):
    _require_admin(user)
    date_key = date or dataset_service.date_key_now()
    if not isinstance(date_key, str) or not DATE_RE.match(date_key):
        _raise_dataset_error(
            dataset_service.DatasetError(
                dataset_service.ERR_INVALID_DATASET_DATE,
                "date 必须是 YYYYMMDD",
                400,
            )
        )
    try:
        records = await DatasetSampleDao.list_by_date(date_key)
    except Exception as exc:  # pragma: no cover
        log_exception(exc, "查询数据集样本失败", {"date": date_key})
        raise APIError(500, 50000, "数据集服务暂不可用") from exc
    items = [dataset_service.sample_to_dict(record) for record in records]
    return ok({"date_key": date_key, "items": items, "total": len(items)}, request=request)
