"""内部数据集采集：权限/meta/大小/配额/存储/查询/迁移/清理测试。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import v1_common, v1_dataset  # noqa: E402
from scripts import migrate_dataset, purge_dataset  # noqa: E402
from service import dataset as dataset_service  # noqa: E402


def make_meta(**overrides):
    meta = {
        "room_code": "A1B2C",
        "operator_id": "haizhua",
        "resolution": "2560x1440",
        "hud_scale": 1.25,
        "captured_at": 1792400000000,
        "ocr": ["大招 12s", "技能 就绪", "投掷物 2"],
        "squad_number": 2,
        "client_version": "0.1.4",
    }
    meta.update(overrides)
    return meta


def make_request(headers=None):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host="203.0.113.10"),
        state=SimpleNamespace(request_id="req_dataset"),
        url=SimpleNamespace(path="/api/v1/dataset/samples"),
        method="POST",
    )


class FakeUpload:
    def __init__(self, data=b"png-bytes"):
        self.data = data

    async def read(self):
        return self.data


class FakeDatasetDao:
    def __init__(self, count=0, records=None):
        self.count = count
        self.records = records or []
        self.created = []

    async def count_for_user_date(self, user_id, date_key):
        return self.count

    async def create_sample(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=len(self.created), created_at=datetime.now())

    async def list_by_date(self, date_key, limit=500):
        return self.records


class DatasetUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {
            "DATASET_DIR": self.tmp.name,
            "DATASET_UPLOADERS": "DeltaQA01,DeltaQA02",
            "DATASET_ADMINS": "admin",
            "DATASET_ENABLED": "true",
            "DATASET_DAILY_QUOTA": "500",
        }
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = SimpleNamespace(id=7, username="DeltaQA01")
        self.dao = FakeDatasetDao()
        dao_patcher = mock.patch.object(
            v1_dataset, "DatasetSampleDao", self.dao
        )
        dao_patcher.start()
        self.addCleanup(dao_patcher.stop)

    async def test_upload_writes_files_and_meta(self):
        response = await v1_dataset.upload_dataset_sample(
            make_request(),
            meta=json.dumps(make_meta(), ensure_ascii=False),
            slots_png=FakeUpload(b"slot-png"),
            avatar_png=FakeUpload(b"avatar-png"),
            user=self.user,
        )
        data = response["data"]
        sample_dir = Path(self.tmp.name) / data["date_key"] / data["sample_id"]
        self.assertTrue((sample_dir / "slots.png").is_file())
        self.assertTrue((sample_dir / "avatar.png").is_file())
        meta_saved = json.loads((sample_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual("haizhua", meta_saved["operator_id"])
        self.assertEqual(2, meta_saved["squad_number"])
        self.assertEqual(1, len(self.dao.created))
        created = self.dao.created[0]
        self.assertEqual("DeltaQA01", created["username"])
        self.assertEqual(2, created["squad_number"])
        self.assertEqual(499, data["quota_remaining"])

    async def test_upload_metadata_only(self):
        response = await v1_dataset.upload_dataset_sample(
            make_request(),
            meta=json.dumps(make_meta()),
            slots_png=None,
            avatar_png=None,
            user=self.user,
        )
        sample_dir = (
            Path(self.tmp.name)
            / response["data"]["date_key"]
            / response["data"]["sample_id"]
        )
        self.assertEqual(["meta.json"], response["data"]["files"])
        self.assertTrue((sample_dir / "meta.json").is_file())

    async def test_non_whitelist_forbidden(self):
        user = SimpleNamespace(id=8, username="random")
        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.upload_dataset_sample(
                make_request(),
                meta=json.dumps(make_meta()),
                slots_png=None,
                avatar_png=None,
                user=user,
            )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual("DATASET_FORBIDDEN", ctx.exception.error_code)

    async def test_invalid_meta_rejected(self):
        bad_metas = [
            {},
            make_meta(operator_id=""),
            make_meta(captured_at="123"),
            make_meta(ocr="not-list"),
            make_meta(squad_number=0),
            make_meta(squad_number=4),
            make_meta(hud_scale=0),
            make_meta(client_version=""),
        ]
        for meta in bad_metas:
            with self.subTest(meta=str(meta)[:60]):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_dataset.upload_dataset_sample(
                        make_request(),
                        meta=json.dumps(meta),
                        slots_png=None,
                        avatar_png=None,
                        user=self.user,
                    )
                self.assertEqual(400, ctx.exception.status_code)
                self.assertEqual("INVALID_DATASET_META", ctx.exception.error_code)

    async def test_file_size_limits(self):
        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.upload_dataset_sample(
                make_request(),
                meta=json.dumps(make_meta()),
                slots_png=FakeUpload(b"x" * (2 * 1024 * 1024 + 1)),
                avatar_png=None,
                user=self.user,
            )
        self.assertEqual(413, ctx.exception.status_code)

        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.upload_dataset_sample(
                make_request(),
                meta=json.dumps(make_meta()),
                slots_png=FakeUpload(b"a" * (2 * 1024 * 1024)),
                avatar_png=FakeUpload(b"b" * (2 * 1024 * 1024)),
                user=self.user,
            )
        self.assertEqual(413, ctx.exception.status_code)

    async def test_quota_exceeded(self):
        self.dao.count = 500
        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.upload_dataset_sample(
                make_request(),
                meta=json.dumps(make_meta()),
                slots_png=None,
                avatar_png=None,
                user=self.user,
            )
        self.assertEqual(429, ctx.exception.status_code)
        self.assertEqual("DATASET_QUOTA_EXCEEDED", ctx.exception.error_code)

    async def test_disabled(self):
        with mock.patch.dict(os.environ, {"DATASET_ENABLED": "false"}):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_dataset.upload_dataset_sample(
                    make_request(),
                    meta=json.dumps(make_meta()),
                    slots_png=None,
                    avatar_png=None,
                    user=self.user,
                )
        self.assertEqual(503, ctx.exception.status_code)
        self.assertEqual("DATASET_DISABLED", ctx.exception.error_code)

    async def test_status_and_admin_query(self):
        self.dao.count = 12
        status = await v1_dataset.dataset_status(make_request(), self.user)
        self.assertEqual(12, status["data"]["today_count"])
        self.assertEqual(500, status["data"]["quota"])
        self.assertTrue(status["data"]["enabled"])

        with self.assertRaises(v1_common.APIError):
            await v1_dataset.dataset_status(make_request(), SimpleNamespace(id=9, username="x"))

        record = SimpleNamespace(
            id=1,
            sample_id="abc",
            username="DeltaQA01",
            date_key="20261020",
            room_code="A1B2C",
            operator_id="haizhua",
            resolution="2560x1440",
            hud_scale=1.25,
            captured_at=1792400000000,
            ocr=["a"],
            squad_number=2,
            client_version="0.1.4",
            files=["meta.json"],
            rel_dir="20261020/abc",
            created_at=datetime.now(),
        )
        self.dao.records = [record]
        admin = SimpleNamespace(id=1, username="admin")
        listed = await v1_dataset.list_dataset_samples(
            make_request(), date="20261020", user=admin
        )
        self.assertEqual(1, listed["data"]["total"])
        self.assertEqual("abc", listed["data"]["items"][0]["sample_id"])

        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.list_dataset_samples(
                make_request(), date="20261020", user=self.user
            )
        self.assertEqual("DATASET_FORBIDDEN", ctx.exception.error_code)

        with self.assertRaises(v1_common.APIError) as ctx:
            await v1_dataset.list_dataset_samples(
                make_request(), date="2026-10-20", user=admin
            )
        self.assertEqual("INVALID_DATASET_DATE", ctx.exception.error_code)


class DatasetMetaTests(unittest.TestCase):
    def test_valid_and_defaults(self):
        data = dataset_service.validate_meta(json.dumps(make_meta(room_code=None)))
        self.assertIsNone(data["room_code"])
        self.assertEqual(["大招 12s", "技能 就绪", "投掷物 2"], data["ocr"])

    def test_invalid_meta(self):
        for raw in ("", "[]", "{bad", json.dumps({"operator_id": "x"})):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(dataset_service.DatasetError):
                    dataset_service.validate_meta(raw)


class DatasetOpsTests(unittest.TestCase):
    def test_migration_idempotent(self):
        class Cursor:
            def __init__(self):
                self.sql = []

            def execute(self, sql, params=None):
                self.sql.append(sql)

            def close(self):
                pass

        class Connection:
            def __init__(self):
                self.cursor_obj = Cursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.commits += 1

            def rollback(self):
                pass

        conn = Connection()
        migrate_dataset.migrate_dataset(conn)
        migrate_dataset.migrate_dataset(conn)
        self.assertEqual(2, conn.commits)
        self.assertEqual(2, len(conn.cursor_obj.sql))
        self.assertIn("CREATE TABLE IF NOT EXISTS dataset_samples", conn.cursor_obj.sql[0])

    def test_purge_by_date_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y%m%d")
            recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
            for key in (old, recent, "not-a-date"):
                (root / key).mkdir()
            removed = purge_dataset.purge(root, before_days=30, dry_run=True)
            self.assertEqual([old], removed)
            self.assertTrue((root / old).is_dir())

            removed = purge_dataset.purge(root, before_days=30)
            self.assertEqual([old], removed)
            self.assertFalse((root / old).exists())
            self.assertTrue((root / recent).exists())

            removed = purge_dataset.purge(root, exact_date=recent)
            self.assertEqual([recent], removed)
            self.assertFalse((root / recent).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
