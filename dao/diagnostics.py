"""对局识别诊断事件模型 / DAO（0.2.12 B6）。"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List

from tortoise import fields, models


class DiagnosticEvent(models.Model):
    id = fields.BigIntField(pk=True)
    account_user_id = fields.BigIntField(null=True, db_index=True)
    client_diag_id = fields.CharField(max_length=36, db_index=True)
    session_id = fields.CharField(max_length=36)
    room_id = fields.CharField(max_length=64, null=True)
    kind = fields.CharField(max_length=16)
    payload = fields.JSONField()
    client_version = fields.CharField(max_length=32, null=True)
    created_at = fields.DatetimeField(auto_now_add=True, db_index=True)

    class Meta:
        table = "diagnostics_events"
        indexes = (
            ("account_user_id", "created_at"),
            ("client_diag_id", "created_at"),
            ("kind", "created_at"),
        )


class DiagnosticEventDao:
    @staticmethod
    async def bulk_create(events: Iterable[dict]) -> int:
        objects = [DiagnosticEvent(**event) for event in events]
        if not objects:
            return 0
        await DiagnosticEvent.bulk_create(objects)
        return len(objects)

    @staticmethod
    async def delete_before(cutoff: datetime) -> int:
        deleted = await DiagnosticEvent.filter(created_at__lt=cutoff).delete()
        return int(deleted or 0)

    @staticmethod
    async def list_by_user(user_id: int, limit: int = 200) -> List[DiagnosticEvent]:
        return (
            await DiagnosticEvent.filter(account_user_id=user_id)
            .order_by("-created_at")
            .limit(limit)
        )

    @staticmethod
    async def list_by_diag(diag_id: str, limit: int = 200) -> List[DiagnosticEvent]:
        return (
            await DiagnosticEvent.filter(client_diag_id=diag_id)
            .order_by("-created_at")
            .limit(limit)
        )
