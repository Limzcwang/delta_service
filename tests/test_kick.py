"""H3：房主踢人时向被踢者发送 room_kicked（摘除会话之前）。"""

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

from api import v1_common, v1_rooms  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service.rooms import ERR_NOT_ROOM_OWNER, RoomError  # noqa: E402
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module  # noqa: E402


class DummyRequest:
    def __init__(self):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id="req_kick")
        self.url = SimpleNamespace(path="/api/v1/rooms/A1B2C/kick")
        self.method = "POST"


class NotifyKickedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
        self.room = make_room()
        self.owner = make_user(1, "alice", "Alice")
        self.owner_ws = FakeWebSocket()
        self.owner_session = make_session(
            self.manager, self.owner, self.owner_ws, room=self.room
        )
        self.bob = make_user(2, "bob", "Bob")
        self.bob_ws = FakeWebSocket()
        self.bob_session = make_session(
            self.manager, self.bob, self.bob_ws, room=self.room
        )

    async def test_notify_sends_directed_message_only_to_target(self):
        sent = await self.manager.notify_user_kicked(
            "2", self.room.id, self.room.code, self.owner
        )

        self.assertTrue(sent)
        event = self.bob_ws.messages("room_kicked")[0]
        self.assertEqual("room_kicked", event["type"])
        self.assertEqual(self.room.id, event["room_id"])
        self.assertEqual(self.room.code, event["room_code"])
        self.assertEqual("kicked", event["reason"])
        self.assertEqual({"id": "1", "nickname": "Alice"}, event["by"])
        self.assertEqual([], self.owner_ws.messages("room_kicked"))

    async def test_notify_noop_when_not_in_room_or_offline(self):
        self.bob_session.room_id = None
        self.assertFalse(
            await self.manager.notify_user_kicked(
                "2", self.room.id, self.room.code, self.owner
            )
        )
        self.assertEqual([], self.bob_ws.messages("room_kicked"))
        self.assertFalse(
            await self.manager.notify_user_kicked(
                "999", self.room.id, self.room.code, self.owner
            )
        )


class KickApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
        self.room = make_room()
        self.owner = make_user(1, "alice", "Alice")
        self.owner_ws = FakeWebSocket()
        self.owner_session = make_session(
            self.manager, self.owner, self.owner_ws, room=self.room
        )
        self.bob = make_user(2, "bob", "Bob")
        self.bob_ws = FakeWebSocket()
        self.bob_session = make_session(
            self.manager, self.bob, self.bob_ws, room=self.room
        )

    async def test_kick_sends_room_kicked_then_detaches(self):
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
                self.owner,
            )

        self.assertEqual({"members": []}, response["data"])
        # 被踢者收到定向事件（先于摘除；若先摘除则不会收到）
        event = self.bob_ws.messages("room_kicked")[0]
        self.assertEqual({"id": "1", "nickname": "Alice"}, event["by"])
        # 其他成员收到 user_left(kicked)
        left = self.owner_ws.messages("user_left")[0]
        self.assertEqual("kicked", left["reason"])
        self.assertEqual({"id": "2", "nickname": "Bob"}, left["user"])
        # 被踢者会话已摘除，后续 chat/canvas 被拒
        self.assertIsNone(self.bob_session.room_id)
        await self.manager.process_message(
            self.bob_session, json.dumps({"type": "chat_send", "content": "hi"})
        )
        self.assertEqual("NOT_ROOM_MEMBER", self.bob_ws.last()["code"])
        await self.manager.process_message(
            self.bob_session,
            json.dumps(
                {
                    "type": "canvas_op",
                    "room_id": self.room.id,
                    "rev": 1,
                    "op": {"kind": "clear"},
                }
            ),
        )
        self.assertEqual("NOT_ROOM_MEMBER", self.bob_ws.last()["code"])

    async def test_non_owner_kick_403(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "kick_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_OWNER, "仅房主可执行该操作", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.kick_member(
                    DummyRequest(),
                    self.room.code,
                    v1_rooms.KickMemberRequest(user_id="2"),
                    self.bob,
                )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual(ERR_NOT_ROOM_OWNER, ctx.exception.error_code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
