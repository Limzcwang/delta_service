"""内部数据集样本模型与 DAO。"""

from __future__ import annotations

from typing import List, Optional

from tortoise import fields, models

from dao.skill import TinyIntField


class DatasetSample(models.Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField("models.User", related_name="dataset_samples")
    username = fields.CharField(max_length=32)
    sample_id = fields.CharField(max_length=32, unique=True)
    date_key = fields.CharField(max_length=8, db_index=True)
    room_code = fields.CharField(max_length=16, null=True)
    operator_id = fields.CharField(max_length=32)
    resolution = fields.CharField(max_length=32)
    hud_scale = fields.FloatField()
    captured_at = fields.BigIntField()
    ocr = fields.JSONField()
    squad_number = TinyIntField(null=True)
    client_version = fields.CharField(max_length=32)
    rel_dir = fields.CharField(max_length=128)
    files = fields.JSONField()
    total_bytes = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "dataset_samples"
        indexes = (("date_key", "created_at"),)


class DatasetSampleDao:
    @staticmethod
    async def count_for_user_date(user_id: int, date_key: str) -> int:
        return await DatasetSample.filter(
            user_id=user_id, date_key=date_key
        ).count()

    @staticmethod
    async def create_sample(**kwargs) -> DatasetSample:
        return await DatasetSample.create(**kwargs)

    @staticmethod
    async def list_by_date(date_key: str, limit: int = 500) -> List[DatasetSample]:
        return (
            await DatasetSample.filter(date_key=date_key)
            .order_by("created_at")
            .limit(limit)
        )

    @staticmethod
    async def delete_before(date_key: str) -> int:
        deleted = await DatasetSample.filter(date_key__lt=date_key).delete()
        return int(deleted or 0)
