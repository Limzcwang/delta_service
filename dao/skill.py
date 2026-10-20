"""房间技能共享状态模型 / DAO（E1）。"""

from __future__ import annotations

from typing import List, Optional

from tortoise import fields, models


class TinyIntField(fields.SmallIntField):
    """MySQL TINYINT；Tortoise 无内置 TinyInt 字段。"""

    SQL_TYPE = "TINYINT"


class RoomSkillState(models.Model):
    """每房间每用户最新一份技能/大招/投掷物共享状态（不存历史）。"""

    id = fields.IntField(pk=True)
    room = fields.ForeignKeyField("models.Room", related_name="skill_states")
    user = fields.ForeignKeyField("models.User", related_name="skill_states")
    data = fields.JSONField()
    # 队伍编号（1/2/3，允许重复；旧客户端可能为 NULL）
    squad_number = TinyIntField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "room_skill_states"
        unique_together = (("room", "user"),)


class SkillStateDao:
    @staticmethod
    async def get(room_id: str, user_id: int) -> Optional[RoomSkillState]:
        return await RoomSkillState.filter(
            room_id=room_id, user_id=user_id
        ).first()

    @staticmethod
    async def list_for_room(room_id: str) -> List[RoomSkillState]:
        return (
            await RoomSkillState.filter(room_id=room_id)
            .select_related("user")
            .all()
        )

    @staticmethod
    async def upsert(room, user, data: dict, squad_number=None) -> RoomSkillState:
        record = await SkillStateDao.get(str(room.id), int(user.id))
        if record is None:
            return await RoomSkillState.create(
                room=room, user=user, data=data, squad_number=squad_number
            )
        record.data = data
        record.squad_number = squad_number
        await record.save(
            update_fields=["data", "squad_number", "updated_at"]
        )
        return record

    @staticmethod
    async def remove(room_id: str, user_id: int) -> int:
        deleted = await RoomSkillState.filter(
            room_id=room_id, user_id=user_id
        ).delete()
        return int(deleted or 0)

    @staticmethod
    async def clear_room(room_id: str) -> int:
        deleted = await RoomSkillState.filter(room_id=room_id).delete()
        return int(deleted or 0)
