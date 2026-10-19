"""D：WS canvas_op / canvas_snapshot_save / 关房落盘测试（离线）。"""

import json
import unittest
from unittest import mock

from tests.support import capture_logger
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module

from log.logger import logger as service_logger
from service.canvas import CanvasError
from service.messages import MessageRateLimiter
from service.rooms import ERR_NOT_ROOM_MEMBER, ERR_ROOM_CLOSED, RoomError


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def stroke(points=None):
    return {
        "kind": "stroke",
        "points": points
        if points is not None
        else [{"x": 0.0, "y": 0.0}, {"x": 0.5, "y": 0.5}],
        "color": "#FF3B30",
        "width": 4,
        "tool": "pen",
    }


class WsCanvasTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)

        self.clock = FakeClock()
        self.manager = manager_module.WebSocketManager(
            canvas_rate_limiter=MessageRateLimiter(
                max_messages=30, window_seconds=1.0, clock=self.clock
            )
        )
        self.room = make_room()
        self.alice = make_user(1, "alice")
        self.alice_ws = FakeWebSocket()
        self.alice_session = make_session(
            self.manager, self.alice, self.alice_ws, room=self.room
        )
        self.bob = make_user(2, "bob")
        self.bob_ws = FakeWebSocket()
        self.bob_session = make_session(
            self.manager, self.bob, self.bob_ws, room=self.room
        )

        patcher = mock.patch.object(
            manager_module.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=self.room),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            manager_module.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(
                return_value={"strokes": [], "rev": 0, "updated_at": None}
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            manager_module.rooms_service,
            "ensure_member",
            new=mock.AsyncMock(return_value=None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def send_op(self, session, rev=1, op=None, room_id=None):
        payload = {
            "type": "canvas_op",
            "room_id": room_id or self.room.id,
            "rev": rev,
            "op": op if op is not None else stroke(),
        }
        await self.manager.process_message(session, json.dumps(payload))

    async def send_save(self, session, strokes, rev=0, room_id=None):
        payload = {
            "type": "canvas_snapshot_save",
            "strokes": strokes,
            "rev": rev,
        }
        if room_id is not None:
            payload["room_id"] = room_id
        await self.manager.process_message(session, json.dumps(payload))


class CanvasBroadcastTests(WsCanvasTestCase):
    async def test_broadcast_to_others_not_sender(self):
        await self.send_op(self.alice_session, rev=1)

        self.assertEqual([], self.alice_ws.messages("canvas_op"))
        events = self.bob_ws.messages("canvas_op")
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual(self.room.id, event["room_id"])
        self.assertEqual("1", event["user_id"])
        self.assertEqual(1, event["rev"])
        self.assertEqual(stroke(), event["op"])

    async def test_multiple_members_receive(self):
        carol = make_user(3, "carol")
        carol_ws = FakeWebSocket()
        carol_session = make_session(
            self.manager, carol, carol_ws, room=self.room
        )
        await self.send_op(self.alice_session, rev=1)

        self.assertEqual(1, len(self.bob_ws.messages("canvas_op")))
        self.assertEqual(1, len(carol_ws.messages("canvas_op")))
        self.assertEqual([], self.alice_ws.messages("canvas_op"))

    async def test_clear_op(self):
        await self.send_op(self.alice_session, rev=1, op={"kind": "clear"})
        event = self.bob_ws.messages("canvas_op")[0]
        self.assertEqual({"kind": "clear"}, event["op"])
        self.assertEqual(
            {"strokes": [], "rev": 1, "updated_at": None},
            self.manager.canvas_states[self.room.id],
        )


class CanvasValidationTests(WsCanvasTestCase):
    async def test_rev_must_increase(self):
        await self.send_op(self.alice_session, rev=2)
        await self.send_op(self.alice_session, rev=1)
        self.assertEqual("CANVAS_OUT_OF_ORDER", self.alice_ws.last()["code"])

        await self.send_op(self.alice_session, rev=2)
        self.assertEqual("CANVAS_OUT_OF_ORDER", self.alice_ws.last()["code"])
        self.assertEqual(1, len(self.bob_ws.messages("canvas_op")))

    async def test_coordinates_out_of_range(self):
        for point in ({"x": -0.01, "y": 0.5}, {"x": 0.5, "y": 1.01}):
            with self.subTest(point=point):
                self.alice_ws.sent.clear()
                await self.send_op(
                    self.alice_session, rev=1, op=stroke(points=[point])
                )
                self.assertEqual("INVALID_CANVAS_OP", self.alice_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("canvas_op"))

    async def test_too_many_points(self):
        points = [{"x": 0.5, "y": 0.5}] * 513
        await self.send_op(self.alice_session, rev=1, op=stroke(points=points))
        self.assertEqual("INVALID_CANVAS_OP", self.alice_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("canvas_op"))

    async def test_not_in_room(self):
        lonely_ws = FakeWebSocket()
        lonely = make_session(self.manager, make_user(9, "lonely"), lonely_ws)
        await self.send_op(lonely, rev=1)
        self.assertEqual("NOT_ROOM_MEMBER", lonely_ws.last()["code"])

    async def test_room_id_mismatch(self):
        await self.send_op(self.alice_session, rev=1, room_id="other-room")
        self.assertEqual("NOT_ROOM_MEMBER", self.alice_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("canvas_op"))

    async def test_closed_room_rejected(self):
        with mock.patch.object(
            manager_module.rooms_service,
            "ensure_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_ROOM_CLOSED, "房间已关闭", 409)
            ),
        ):
            await self.send_op(self.alice_session, rev=1)
        self.assertEqual("ROOM_CLOSED", self.alice_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("canvas_op"))

    async def test_kicked_session_cannot_send(self):
        await self.manager.detach_user_from_room("2", self.room.id, reason="kicked")
        await self.send_op(self.bob_session, rev=1)
        self.assertEqual("NOT_ROOM_MEMBER", self.bob_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("canvas_op"))


class CanvasJoinStateTests(WsCanvasTestCase):
    async def test_new_member_gets_unsaved_ops(self):
        await self.send_op(self.alice_session, rev=1)
        carol = make_user(3, "carol")
        carol_ws = FakeWebSocket()
        carol_session = make_session(self.manager, carol, carol_ws)
        with mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                return_value=(self.room, [
                    {"id": "1", "username": "alice", "role": "owner"},
                    {"id": "2", "username": "bob", "role": "member"},
                    {"id": "3", "username": "carol", "role": "member"},
                ])
            ),
        ):
            await self.manager.process_message(
                carol_session,
                json.dumps({"type": "join_room", "room_id_or_code": self.room.code}),
            )

        snapshot = carol_ws.messages("room_joined")[0]["canvas_snapshot"]
        self.assertEqual([stroke()], snapshot["strokes"])
        self.assertEqual(1, snapshot["rev"])

    async def test_join_loads_db_snapshot(self):
        db_stroke = stroke()
        with mock.patch.object(
            manager_module.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(
                return_value={"strokes": [db_stroke], "rev": 5, "updated_at": 1}
            ),
        ):
            self.assertEqual(
                {"strokes": [db_stroke], "rev": 5, "updated_at": 1},
                await self.manager.get_canvas_snapshot(self.room.id),
            )
            self.assertEqual([db_stroke], self.manager.canvas_states[self.room.id]["strokes"])

    async def test_get_canvas_snapshot_merges_db_and_ops(self):
        db_stroke = stroke()
        with mock.patch.object(
            manager_module.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(
                return_value={"strokes": [db_stroke], "rev": 5, "updated_at": 1}
            ),
        ):
            await self.send_op(self.alice_session, rev=1)

        merged = await self.manager.get_canvas_snapshot(self.room.id)
        self.assertEqual([db_stroke, stroke()], merged["strokes"])
        self.assertEqual(5, merged["rev"])


class CanvasFlushMergeTests(WsCanvasTestCase):
    async def test_flush_merges_db_snapshot_with_memory_ops(self):
        db_stroke = stroke()
        with mock.patch.object(
            manager_module.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(
                return_value={"strokes": [db_stroke], "rev": 5, "updated_at": 1}
            ),
        ):
            await self.send_op(self.alice_session, rev=1)

        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(return_value={"rev": 5, "updated_at": 2}),
        ) as save_snapshot:
            await self.manager.flush_canvas_on_close(self.room.id)

        save_snapshot.assert_awaited_once_with(
            self.room.id, [db_stroke, stroke()], 5
        )

    async def test_flush_persists_clear_as_empty(self):
        db_stroke = stroke()
        with mock.patch.object(
            manager_module.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(
                return_value={"strokes": [db_stroke], "rev": 5, "updated_at": 1}
            ),
        ):
            await self.send_op(self.alice_session, rev=1, op={"kind": "clear"})

        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(return_value={"rev": 5, "updated_at": 2}),
        ) as save_snapshot:
            await self.manager.flush_canvas_on_close(self.room.id)

        save_snapshot.assert_awaited_once_with(self.room.id, [], 5)

    async def test_flush_without_memory_state_does_not_overwrite_db(self):
        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(),
        ) as save_snapshot:
            flushed = await self.manager.flush_canvas_on_close("other-room")
        self.assertEqual(0, flushed)
        save_snapshot.assert_not_awaited()


class CanvasRateLimitTests(WsCanvasTestCase):
    async def test_thirty_first_op_is_rate_limited(self):
        for rev in range(1, 31):
            await self.send_op(self.alice_session, rev=rev)
        self.assertEqual([], self.alice_ws.messages("error"))
        self.assertEqual(30, len(self.bob_ws.messages("canvas_op")))

        await self.send_op(self.alice_session, rev=31)
        self.assertEqual("RATE_LIMITED", self.alice_ws.last()["code"])
        self.assertEqual(30, len(self.bob_ws.messages("canvas_op")))


class CanvasSnapshotSaveTests(WsCanvasTestCase):
    async def test_save_snapshot_acks_and_updates_state(self):
        saved = {"strokes": [stroke()], "rev": 5, "updated_at": 1792400000000}
        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(return_value=saved),
        ) as save_snapshot:
            await self.send_save(self.alice_session, [stroke()], rev=5)

        save_snapshot.assert_awaited_once_with(self.room.id, [stroke()], 5)
        ack = self.alice_ws.last()
        self.assertEqual("canvas_snapshot_saved", ack["type"])
        self.assertEqual(5, ack["rev"])
        self.assertEqual([stroke()], self.manager.canvas_states[self.room.id]["strokes"])

    async def test_save_rejects_invalid_snapshot(self):
        await self.send_save(self.alice_session, [{"kind": "bad"}], rev=1)
        self.assertEqual("INVALID_CANVAS_OP", self.alice_ws.last()["code"])

    async def test_save_room_mismatch(self):
        await self.send_save(
            self.alice_session, [stroke()], rev=1, room_id="other-room"
        )
        self.assertEqual("NOT_ROOM_MEMBER", self.alice_ws.last()["code"])

    async def test_save_requires_membership(self):
        with mock.patch.object(
            manager_module.rooms_service,
            "ensure_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            await self.send_save(self.alice_session, [stroke()], rev=1)
        self.assertEqual("NOT_ROOM_MEMBER", self.alice_ws.last()["code"])

    async def test_flush_on_close_persists_state(self):
        strokes = [stroke()]
        self.manager.canvas_states[self.room.id] = {"strokes": strokes, "rev": 7}
        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(return_value={"rev": 7, "updated_at": 1}),
        ) as save_snapshot:
            flushed = await self.manager.flush_canvas_on_close(self.room.id)

        self.assertEqual(1, flushed)
        save_snapshot.assert_awaited_once_with(self.room.id, strokes, 7)
        self.assertNotIn(self.room.id, self.manager.canvas_states)

    async def test_flush_noop_without_state(self):
        with mock.patch.object(
            manager_module.canvas_service,
            "save_snapshot",
            new=mock.AsyncMock(),
        ) as save_snapshot:
            self.assertEqual(0, await self.manager.flush_canvas_on_close("missing"))
        save_snapshot.assert_not_awaited()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
