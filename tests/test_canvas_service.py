"""D：画板服务层（op/快照校验、读写、7 天清理）测试（离线 fake DAO）。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from tests.support import install_tortoise_stub

install_tortoise_stub()

from dao import canvas as canvas_dao_module  # noqa: E402
from service import canvas as canvas_service  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def stroke(points=None, **overrides):
    op = {
        "kind": "stroke",
        "points": points if points is not None else [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}],
        "color": "#FF3B30",
        "width": 4,
        "tool": "pen",
    }
    op.update(overrides)
    return op


class FakeCanvasDao:
    def __init__(self):
        self.record = None
        self.upserts = []
        self.deleted_cutoff = None
        self.deleted_count = 0

    async def upsert(self, room_id, data):
        self.upserts.append((room_id, data))
        self.record = SimpleNamespace(
            room_id=room_id, data=dict(data), updated_at=BASE_TIME
        )
        return self.record

    async def get_by_room(self, room_id):
        return self.record

    async def delete_older_than(self, cutoff):
        self.deleted_cutoff = cutoff
        return self.deleted_count


class CanvasValidationTests(unittest.TestCase):
    def test_valid_stroke_and_clear(self):
        canvas_service.validate_canvas_op(stroke())
        canvas_service.validate_canvas_op({"kind": "clear"})
        # 边界坐标/宽度/颜色合法
        canvas_service.validate_canvas_op(
            stroke(
                points=[{"x": 0, "y": 1}],
                color="#000000",
                width=1,
                tool="eraser",
            )
        )

    def test_invalid_ops(self):
        bad_ops = [
            None,
            [],
            {"kind": "line"},
            stroke(points=[]),
            stroke(points="x"),
            stroke(points=[{"x": -0.1, "y": 0.5}]),
            stroke(points=[{"x": 1.1, "y": 0.5}]),
            stroke(points=[{"x": "0.5", "y": 0.5}]),
            stroke(points=[{"x": True, "y": 0.5}]),
            stroke(color="red"),
            stroke(color="#FF3B3"),
            stroke(width=0),
            stroke(width=33),
            stroke(width=1.5),
            stroke(width=True),
            stroke(tool="brush"),
            stroke(points=[{"x": 0.5, "y": 0.5}] * 513),
        ]
        for op in bad_ops:
            with self.subTest(op=str(op)[:60]):
                with self.assertRaises(canvas_service.CanvasError) as ctx:
                    canvas_service.validate_canvas_op(op)
                self.assertEqual(
                    canvas_service.ERR_INVALID_CANVAS_OP,
                    ctx.exception.error_code,
                )

    def test_snapshot_stroke_count_limit(self):
        with self.assertRaises(canvas_service.CanvasError):
            canvas_service.validate_snapshot_strokes([stroke()] * 5001)
        with mock.patch.object(canvas_service, "MAX_SNAPSHOT_BYTES", 10 * 1024 * 1024):
            self.assertEqual(
                5000,
                len(canvas_service.validate_snapshot_strokes([stroke()] * 5000)),
            )

    def test_snapshot_size_limit(self):
        with mock.patch.object(canvas_service, "MAX_SNAPSHOT_BYTES", 32):
            with self.assertRaises(canvas_service.CanvasError):
                canvas_service.validate_snapshot_strokes([stroke()])

    def test_snapshot_requires_list(self):
        with self.assertRaises(canvas_service.CanvasError):
            canvas_service.validate_snapshot_strokes({"strokes": []})


class CanvasSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.dao = FakeCanvasDao()
        patcher = mock.patch.object(canvas_service, "CanvasDao", self.dao)
        patcher.start()
        self.addCleanup(patcher.stop)

    def await_(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_save_and_get_snapshot(self):
        strokes = [stroke()]
        saved = self.await_(
            canvas_service.save_snapshot("room-1", strokes, rev=9)
        )

        self.assertEqual(("room-1", {"strokes": strokes, "rev": 9}), self.dao.upserts[0])
        self.assertEqual(strokes, saved["strokes"])
        self.assertEqual(9, saved["rev"])
        self.assertEqual(int(BASE_TIME.timestamp() * 1000), saved["updated_at"])

        fetched = self.await_(canvas_service.get_snapshot("room-1"))
        self.assertEqual(saved, fetched)

    def test_get_missing_snapshot_is_empty(self):
        self.assertEqual(
            {"strokes": [], "rev": 0, "updated_at": None},
            self.await_(canvas_service.get_snapshot("missing")),
        )

    def test_save_rejects_bad_rev_and_strokes(self):
        for rev in (-1, True, "1", 1.5):
            with self.subTest(rev=rev):
                with self.assertRaises(canvas_service.CanvasError):
                    self.await_(
                        canvas_service.save_snapshot("room-1", [stroke()], rev=rev)
                    )
        with self.assertRaises(canvas_service.CanvasError):
            self.await_(
                canvas_service.save_snapshot("room-1", [{"kind": "bad"}], rev=1)
            )
        self.assertEqual([], self.dao.upserts)

    def test_cleanup_uses_seven_days(self):
        now = datetime(2026, 2, 1, 0, 0, 0)
        self.dao.deleted_count = 4
        deleted = self.await_(canvas_service.cleanup_canvas_snapshots(now=now))

        self.assertEqual(4, deleted)
        self.assertEqual(now - timedelta(days=7), self.dao.deleted_cutoff)

    def test_cleanup_rejects_bad_days(self):
        for days in (0, -3, "7"):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    self.await_(canvas_service.cleanup_canvas_snapshots(days=days))


class CanvasDaoStructureTests(unittest.TestCase):
    def test_model_and_dao(self):
        self.assertEqual("room_canvas", canvas_dao_module.RoomCanvas.Meta.table)
        for name in ("get_by_room", "upsert", "delete_older_than"):
            self.assertTrue(hasattr(canvas_dao_module.CanvasDao, name), name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
