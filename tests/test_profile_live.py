"""F 回归：昵称修改后已连接 WS 会话无需重连即使用 DB 实时昵称。"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from tests.support import capture_logger
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module

from log.logger import logger as service_logger


def stale_user(user_id=1, username="alice", nickname="旧昵称"):
    return SimpleNamespace(id=user_id, username=username, nickname=nickname)


def fresh_user(user_id=1, username="alice", nickname="新昵称"):
    return SimpleNamespace(id=user_id, username=username, nickname=nickname)


class WsLiveNicknameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.manager = manager_module.WebSocketManager()
        self.room = make_room()
        self.alice = stale_user()
        self.alice_ws = FakeWebSocket()
        self.alice_session = make_session(
            self.manager, self.alice, self.alice_ws, room=self.room
        )
        self.bob = stale_user(2, "bob", "Bob")
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
            manager_module.rooms_service,
            "ensure_member",
            new=mock.AsyncMock(return_value=None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            manager_module.UserDao,
            "get_user_by_id",
            new=mock.AsyncMock(side_effect=self._fresh_user),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    async def _fresh_user(user_id):
        return fresh_user(user_id, "alice", "新昵称") if str(user_id) == "1" else stale_user(
            2, "bob", "Bob"
        )

    async def test_chat_uses_live_nickname_without_reconnect(self):
        async def fake_send_message(user, room, content, client_msg_id):
            return {
                "id": "m1",
                "room_id": room.id,
                "user": {"id": str(user.id), "nickname": user.nickname},
                "content": content,
                "client_msg_id": client_msg_id,
                "created_at": 1,
            }

        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(side_effect=fake_send_message),
        ) as send_message:
            await self.manager.process_message(
                self.alice_session,
                json.dumps(
                    {"type": "chat_send", "content": "hi", "client_msg_id": "c1"}
                ),
            )

        send_message.assert_awaited_once()
        self.assertEqual("新昵称", self.alice_session.user.nickname)
        for websocket in (self.alice_ws, self.bob_ws):
            event = websocket.messages("chat_message")[0]
            self.assertEqual({"id": "1", "nickname": "新昵称"}, event["user"])
        # 其它成员连接状态不受影响
        self.assertIs(self.bob_session, self.manager.sessions["2"])
        self.assertEqual(self.room.id, self.bob_session.room_id)
        self.assertEqual("Bob", self.bob_session.user.nickname)

    async def test_user_left_uses_live_nickname(self):
        await self.manager.process_message(
            self.alice_session, json.dumps({"type": "leave_room"})
        )

        event = self.bob_ws.messages("user_left")[0]
        self.assertEqual({"id": "1", "nickname": "新昵称"}, event["user"])
        self.assertEqual("left", event["reason"])

    async def test_member_serializer_uses_live_nickname(self):
        member = SimpleNamespace(user=fresh_user(), role="member")
        data = manager_module.rooms_service.member_to_dict(member)
        self.assertEqual(
            {"id": "1", "nickname": "新昵称", "role": "member"}, data
        )
        self.assertNotIn("username", data)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
