"""房间消息数据模型 / DAO（C：聊天持久化）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from tortoise import fields, models


class RoomMessage(models.Model):
    """一条房间文字消息；client_msg_id 供客户端去重/回显。"""

    id = fields.CharField(pk=True, max_length=32)
    room = fields.ForeignKeyField("models.Room", related_name="messages")
    user = fields.ForeignKeyField("models.User", related_name="room_messages")
    content = fields.TextField()
    client_msg_id = fields.CharField(max_length=128, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "room_messages"
        # 历史分页：按 (room, created_at) 倒序取
        indexes = (("room", "created_at"),)


class MessageDao:
    """房间消息表数据访问。"""

    @staticmethod
    async def create(**kwargs) -> RoomMessage:
        return await RoomMessage.create(**kwargs)

    @staticmethod
    async def get_by_id_for_room(
        room_id: str, message_id: str
    ) -> Optional[RoomMessage]:
        return (
            await RoomMessage.filter(id=message_id, room_id=room_id)
            .select_related("user")
            .first()
        )

    @staticmethod
    async def list_for_room(
        room_id: str,
        before: Optional[RoomMessage] = None,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[RoomMessage]:
        """按 created_at/id 倒序返回；since 为历史窗口下界，多取 1 条判断 has_more。"""
        query = RoomMessage.filter(room_id=room_id)
        if since is not None:
            query = query.filter(created_at__gte=since)
        if before is not None:
            query = query.filter(created_at__lt=before.created_at)
        return (
            await query.select_related("user")
            .order_by("-created_at", "-id")
            .limit(limit + 1)
        )

    @staticmethod
    async def delete_older_than(cutoff: datetime) -> int:
        deleted = await RoomMessage.filter(created_at__lt=cutoff).delete()
        return int(deleted or 0)
