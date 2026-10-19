"""房间与房间成员数据模型 / DAO（B2a）。

表结构由 ``Tortoise.generate_schemas()`` 创建（项目暂无迁移框架）。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from tortoise import fields, models


class Room(models.Model):
    """房间（5 位 room_code + 内部字符串 id）。"""

    id = fields.CharField(pk=True, max_length=32)
    # 仅在“活跃房间”内唯一：已关闭房间的 code 允许复用，因此不用 DB unique，
    # 由 RoomDao.code_exists() 在创建时判重（并发窗口极小，冲突由重试覆盖）。
    code = fields.CharField(max_length=5, index=True)
    name = fields.CharField(max_length=32)
    visibility = fields.CharField(max_length=8, default="public")  # public/private
    password_hash = fields.CharField(max_length=255, null=True)
    owner = fields.ForeignKeyField("models.User", related_name="owned_rooms")
    background_id = fields.CharField(max_length=64, null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)
    updated_at = fields.DatetimeField(auto_now=True)
    closed_at = fields.DatetimeField(null=True, index=True)
    # F2 软删除：不物理删数据；软删后不再出现在列表/详情/加入路径。
    deleted = fields.BooleanField(default=False, index=True)

    class Meta:
        table = "rooms"


class RoomMember(models.Model):
    """房间成员关系；同一用户在同一房间只应有一条记录。"""

    id = fields.IntField(pk=True)
    room = fields.ForeignKeyField("models.Room", related_name="members")
    user = fields.ForeignKeyField("models.User", related_name="room_memberships")
    role = fields.CharField(max_length=8, default="member")  # owner/member
    joined_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "room_members"
        unique_together = (("room", "user"),)


class RoomDao:
    """房间表数据访问。"""

    @staticmethod
    async def create(**kwargs) -> Room:
        return await Room.create(**kwargs)

    @staticmethod
    async def get_by_id(room_id: str) -> Optional[Room]:
        return await Room.filter(id=room_id).first()

    @staticmethod
    async def get_by_code(code: str) -> Optional[Room]:
        return await Room.filter(code=code).first()

    @staticmethod
    async def code_exists(code: str) -> bool:
        """code 是否已被占用：软删房间永不復用；未软删的已关闭房间仍可复用。"""
        if await Room.filter(code=code, deleted=True).exists():
            return True
        return await Room.filter(
            code=code, deleted=False, closed_at__isnull=True
        ).exists()

    @staticmethod
    async def list_public(limit: int, before: Optional[datetime] = None) -> List[Room]:
        """按 created_at 倒序返回未关闭的公共房间（多取 1 条用于判断 has_more）。"""
        query = Room.filter(
            visibility="public", closed_at__isnull=True, deleted=False
        )
        if before is not None:
            query = query.filter(created_at__lt=before)
        return await query.order_by("-created_at").limit(limit + 1)

    @staticmethod
    async def update(room: Room, **fields) -> Optional[Room]:
        fields.setdefault("updated_at", datetime.now())
        await Room.filter(id=room.id).update(**fields)
        return await Room.filter(id=room.id).first()

    @staticmethod
    async def close(room: Room, closed_at: Optional[datetime] = None) -> None:
        now = closed_at or datetime.now()
        await Room.filter(id=room.id).update(closed_at=now, updated_at=now)

    @staticmethod
    async def soft_delete(room: Room, deleted_at: Optional[datetime] = None) -> None:
        """F2 软删除：deleted=True + closed_at=now（保留全部数据）。"""
        now = deleted_at or datetime.now()
        await Room.filter(id=room.id).update(
            deleted=True, closed_at=now, updated_at=now
        )


class RoomMemberDao:
    """房间成员表数据访问。"""

    @staticmethod
    async def add(room: Room, user, role: str = "member") -> RoomMember:
        return await RoomMember.create(room=room, user=user, role=role)

    @staticmethod
    async def get(room_id: str, user_id: int) -> Optional[RoomMember]:
        return await RoomMember.filter(room_id=room_id, user_id=user_id).first()

    @staticmethod
    async def list_for_room(room_id: str) -> List[RoomMember]:
        return (
            await RoomMember.filter(room_id=room_id)
            .select_related("user")
            .order_by("joined_at")
        )

    @staticmethod
    async def list_for_user(
        user_id: int,
        limit: int,
        before_joined_at: Optional[datetime] = None,
        offset: int = 0,
    ) -> List[RoomMember]:
        """H1 我的房间：按 joined_at 倒序返回成员的房间关系（多取 1 条）。"""
        query = RoomMember.filter(user_id=user_id).select_related("room")
        if before_joined_at is not None:
            query = query.filter(joined_at__lt=before_joined_at)
        query = query.order_by("-joined_at", "-id")
        if offset > 0:
            query = query.offset(offset)
        return await query.limit(limit + 1)

    @staticmethod
    async def count(room_id: str) -> int:
        return await RoomMember.filter(room_id=room_id).count()

    @staticmethod
    async def remove(room_id: str, user_id: int) -> int:
        deleted = await RoomMember.filter(
            room_id=room_id, user_id=user_id
        ).delete()
        return int(deleted or 0)
