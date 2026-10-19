"""房间画板快照数据模型 / DAO（D：画板）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from tortoise import fields, models


class RoomCanvas(models.Model):
    """每房间一条最新画板快照（strokes + rev 水位）。"""

    room = fields.OneToOneField(
        "models.Room", related_name="canvas", pk=True
    )
    data = fields.JSONField()
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "room_canvas"


class CanvasDao:
    """画板快照数据访问。"""

    @staticmethod
    async def get_by_room(room_id: str) -> Optional[RoomCanvas]:
        return await RoomCanvas.filter(room_id=room_id).first()

    @staticmethod
    async def upsert(room_id: str, data: dict) -> RoomCanvas:
        record = await RoomCanvas.filter(room_id=room_id).first()
        if record is None:
            return await RoomCanvas.create(room_id=room_id, data=data)
        record.data = data
        await record.save(update_fields=["data", "updated_at"])
        return record

    @staticmethod
    async def delete_older_than(cutoff: datetime) -> int:
        deleted = await RoomCanvas.filter(updated_at__lt=cutoff).delete()
        return int(deleted or 0)
