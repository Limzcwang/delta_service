"""B2b：WebSocket 房间频道（join 权限/广播/leave/断线清理）测试（离线）。"""

import unittest
from unittest import mock

from tests.support import capture_logger
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module
from log.logger import logger as service_logger

from service.rooms import ERR_NOT_ROOM_MEMBER, ERR_ROOM_CLOSED, ERR_ROOM_NOT_FOUND, RoomError


class WsRoomTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
        self.room = make_room()

        self.alice = make_user(1, "alice")
        self.alice_ws = FakeWebSocket()
        self.alice_session = make_session(self.manager, self.alice, self.alice_ws)

        self.bob = make_user(2, "bob")
        self.bob_ws = FakeWebSocket()
        self.bob_session = make_session(
            self.manager, self.bob, self.bob_ws, room=self.room
        )

        self.members = [
            {"id": "1", "username": "alice", "role": "owner"},
            {"id": "2", "username": "bob", "role": "member"},
        ]

    async def join_alice(self, payload=None, room=None, members=None):
        room = room or self.room
        members = members if members is not None else self.members
        payload = payload or {"type": "join_room", "room_id_or_code": room.code}
        with mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(room, members)),
        ) as get_room:
            await self.manager.process_message(
                self.alice_session,
                __import__("json").dumps(payload),
            )
        return get_room


class JoinRoomTests(WsRoomTestCase):
    async def test_join_success_returns_room_and_online_members(self):
        get_room = await self.join_alice()

        get_room.assert_awaited_once_with(self.room.code, self.alice)
        response = self.alice_ws.last()
        self.assertEqual("room_joined", response["type"])
        self.assertEqual(self.room.id, response["room"]["id"])
        self.assertEqual(self.room.code, response["room"]["code"])
        member_by_id = {member["id"]: member for member in response["members"]}
        self.assertTrue(member_by_id["1"]["online"])
        self.assertTrue(member_by_id["2"]["online"])
        self.assertEqual(self.room.id, self.alice_session.room_id)

    async def test_join_broadcasts_user_joined_to_others_only(self):
        await self.join_alice()
        events = self.bob_ws.messages("user_joined")
        self.assertEqual(1, len(events))
        self.assertEqual({"id": "1", "nickname": "alice"}, events[0]["user"])
        self.assertEqual(self.room.id, events[0]["room_id"])
        self.assertEqual([], self.alice_ws.messages("user_joined"))

    async def test_join_accepts_room_code_alias(self):
        await self.join_alice(payload={"type": "join_room", "room_code": "A1B2C"})
        self.assertEqual("room_joined", self.alice_ws.last()["type"])

    async def test_join_idempotent_does_not_rebroadcast(self):
        self.alice_session.room_id = self.room.id
        self.alice_session.room_code = self.room.code
        await self.join_alice()
        self.assertEqual("room_joined", self.alice_ws.last()["type"])
        self.assertEqual([], self.bob_ws.messages("user_joined"))

    async def test_join_requires_membership(self):
        with mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            await self.manager.process_message(
                self.alice_session,
                '{"type":"join_room","room_id_or_code":"A1B2C"}',
            )
        error = self.alice_ws.last()
        self.assertEqual("error", error["type"])
        self.assertEqual(ERR_NOT_ROOM_MEMBER, error["code"])
        self.assertEqual(ERR_NOT_ROOM_MEMBER, error["error_code"])
        self.assertIsNone(self.alice_session.room_id)

    async def test_join_maps_room_not_found_and_closed(self):
        for code in (ERR_ROOM_NOT_FOUND, ERR_ROOM_CLOSED):
            with self.subTest(code=code):
                self.alice_ws.sent.clear()
                with mock.patch.object(
                    manager_module.rooms_service,
                    "get_room_for_member",
                    new=mock.AsyncMock(side_effect=RoomError(code, "房间不可用", 404)),
                ):
                    await self.manager.process_message(
                        self.alice_session,
                        '{"type":"join_room","room_id_or_code":"ZZZZZ"}',
                    )
                self.assertEqual(code, self.alice_ws.last()["code"])

    async def test_join_missing_room_reference(self):
        await self.manager.process_message(
            self.alice_session, '{"type":"join_room"}'
        )
        self.assertEqual("INVALID_PAYLOAD", self.alice_ws.last()["code"])


class LeaveAndDisconnectTests(WsRoomTestCase):
    async def test_leave_room_notifies_others(self):
        self.alice_session.room_id = self.room.id
        self.alice_session.room_code = self.room.code

        await self.manager.process_message(self.alice_session, '{"type":"leave_room"}')

        self.assertEqual("left_room", self.alice_ws.last()["type"])
        self.assertEqual(self.room.id, self.alice_ws.last()["room_id"])
        self.assertIsNone(self.alice_session.room_id)
        events = self.bob_ws.messages("user_left")
        self.assertEqual(1, len(events))
        self.assertEqual("left", events[0]["reason"])

    async def test_leave_when_not_in_room_is_idempotent(self):
        await self.manager.process_message(self.alice_session, '{"type":"leave_room"}')
        self.assertEqual("left_room", self.alice_ws.last()["type"])
        self.assertIsNone(self.alice_ws.last()["room_id"])

    async def test_disconnect_cleanup_broadcasts_user_left(self):
        self.alice_session.room_id = self.room.id
        self.alice_session.room_code = self.room.code

        await self.manager._cleanup_session(self.alice_session, reason="disconnected")

        events = self.bob_ws.messages("user_left")
        self.assertEqual(1, len(events))
        self.assertEqual("disconnected", events[0]["reason"])
        self.assertNotIn("1", self.manager.sessions)
        self.assertNotIn(self.alice_ws, self.manager.connected_clients)
        self.assertIsNone(self.alice_session.room_id)

    async def test_disconnect_cleanup_removes_skill_and_broadcasts_order(self):
        self.alice_session.room_id = self.room.id
        self.alice_session.room_code = self.room.code
        with mock.patch.object(
            manager_module.skills_service,
            "remove_state",
            new=mock.AsyncMock(return_value=1),
        ) as remove_state:
            await self.manager._cleanup_session(
                self.alice_session, reason="disconnected"
            )
        remove_state.assert_awaited_once_with(self.room.id, 1)
        types = [message["type"] for message in self.bob_ws.sent]
        self.assertLess(
            types.index("skill_share_removed"), types.index("user_left")
        )
        self.assertEqual(
            "1", self.bob_ws.messages("skill_share_removed")[0]["user_id"]
        )

    async def test_cleanup_is_idempotent(self):
        self.alice_session.room_id = self.room.id
        await self.manager._cleanup_session(self.alice_session)
        self.bob_ws.sent.clear()
        await self.manager._cleanup_session(self.alice_session)
        self.assertEqual([], self.bob_ws.messages("user_left"))


class UnsupportedMessageTests(WsRoomTestCase):
    async def test_unsupported_message_type_after_auth(self):
        for message_type in ("room_message", "skill_report"):
            with self.subTest(message_type=message_type):
                self.alice_ws.sent.clear()
                await self.manager.process_message(
                    self.alice_session,
                    __import__("json").dumps({"type": message_type}),
                )
                error = self.alice_ws.last()
                self.assertEqual("error", error["type"])
                self.assertEqual("UNSUPPORTED_MESSAGE_TYPE", error["code"])

    async def test_ping_returns_pong(self):
        await self.manager.process_message(self.alice_session, '{"type":"ping"}')
        self.assertEqual({"type": "pong"}, self.alice_ws.last())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
