"""C 修复：HTTP leave/kick 摘除 WS 会话（安全回归测试）。"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    capture_logger,
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import v1_rooms  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module  # noqa: E402


class DummyRequest:
    def __init__(self):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id="req_detach")
        self.url = SimpleNamespace(path="/api/v1/rooms/A1B2C/leave")
        self.method = "POST"


class _DetachTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
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


class DetachSessionTests(_DetachTestCase):
    async def test_detach_clears_room_and_broadcasts_user_left(self):
        detached = await self.manager.detach_user_from_room(
            "2", self.room.id, reason="kicked"
        )

        self.assertTrue(detached)
        self.assertIsNone(self.bob_session.room_id)
        self.assertNotIn("2", self.manager.room_online_user_ids(self.room.id))
        events = self.alice_ws.messages("user_left")
        self.assertEqual(1, len(events))
        self.assertEqual("kicked", events[0]["reason"])
        self.assertEqual({"id": "2", "username": "bob"}, events[0]["user"])
        self.assertEqual([], self.bob_ws.messages("user_left"))

    async def test_detached_user_no_longer_receives_chat_broadcast(self):
        await self.manager.detach_user_from_room("2", self.room.id, reason="kicked")
        await self.manager.broadcast_to_room(
            self.room.id,
            {
                "type": "chat_message",
                "id": "m1",
                "room_id": self.room.id,
                "user": {"id": "1", "username": "alice"},
                "content": "secret",
                "client_msg_id": None,
                "created_at": 1792400000000,
            },
        )

        self.assertEqual([], self.bob_ws.messages("chat_message"))
        self.assertEqual(1, len(self.alice_ws.messages("chat_message")))

    async def test_detach_is_noop_for_other_room_or_unknown_user(self):
        self.assertFalse(
            await self.manager.detach_user_from_room("2", "other-room", reason="left")
        )
        self.assertFalse(
            await self.manager.detach_user_from_room("999", self.room.id, reason="left")
        )
        self.assertEqual(self.room.id, self.bob_session.room_id)


class HttpLeaveKickDetachTests(_DetachTestCase):
    async def test_http_leave_detaches_ws_session(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "leave_room",
            new=mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=self.room),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=self.manager
        ):
            response = await v1_rooms.leave_room(
                DummyRequest(), self.room.code, self.bob
            )

        self.assertEqual({"left": True}, response["data"])
        self.assertIsNone(self.bob_session.room_id)
        events = self.alice_ws.messages("user_left")
        self.assertEqual("left", events[0]["reason"])

    async def test_http_kick_detaches_ws_session(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "kick_member",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=self.room),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=self.manager
        ):
            response = await v1_rooms.kick_member(
                DummyRequest(),
                self.room.code,
                v1_rooms.KickMemberRequest(user_id="2"),
                self.alice,
            )

        self.assertEqual({"members": []}, response["data"])
        self.assertIsNone(self.bob_session.room_id)
        events = self.alice_ws.messages("user_left")
        self.assertEqual("kicked", events[0]["reason"])

        # 被踢用户不断开 WS，也收不到后续聊天广播
        await self.manager.broadcast_to_room(
            self.room.id,
            {"type": "chat_message", "id": "m2", "content": "after-kick"},
        )
        self.assertEqual([], self.bob_ws.messages("chat_message"))
        self.assertEqual(1, len(self.alice_ws.messages("chat_message")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
