"""C：WS chat_send 校验/限频/广播测试（离线）。"""

import json
import unittest
from unittest import mock

from tests.support import capture_logger
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module

from log.logger import logger as service_logger
from service.messages import MessageRateLimiter
from service.rooms import ERR_NOT_ROOM_MEMBER, ERR_ROOM_CLOSED, RoomError


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def chat_payload(content="hi", client_msg_id="c-1"):
    payload = {"type": "chat_send", "content": content}
    if client_msg_id is not None:
        payload["client_msg_id"] = client_msg_id
    return json.dumps(payload)


class WsChatTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)

        self.clock = FakeClock()
        self.manager = manager_module.WebSocketManager(
            chat_rate_limiter=MessageRateLimiter(
                max_messages=5, window_seconds=1.0, clock=self.clock
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

        self.message = {
            "id": "m-1",
            "room_id": self.room.id,
            "user": {"id": "1", "username": "alice"},
            "content": "hi",
            "client_msg_id": "c-1",
            "created_at": 1792400000000,
        }
        self.resolve_patcher = mock.patch.object(
            manager_module.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=self.room),
        )
        self.resolve_patcher.start()
        self.addCleanup(self.resolve_patcher.stop)

    async def process(self, session, payload):
        await self.manager.process_message(session, payload)


class ChatSuccessTests(WsChatTestCase):
    async def test_chat_is_broadcast_to_sender_and_others(self):
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=self.message),
        ) as send_message:
            await self.process(self.alice_session, chat_payload("hi", "c-1"))

        send_message.assert_awaited_once()
        for websocket in (self.alice_ws, self.bob_ws):
            events = websocket.messages("chat_message")
            self.assertEqual(1, len(events), websocket)
            self.assertEqual("m-1", events[0]["id"])
            self.assertEqual(self.room.id, events[0]["room_id"])
            self.assertEqual("c-1", events[0]["client_msg_id"])
            self.assertEqual({"id": "1", "username": "alice"}, events[0]["user"])
            self.assertEqual("hi", events[0]["content"])

    async def test_client_msg_id_is_echoed_verbatim(self):
        message = dict(self.message, client_msg_id="client-abc-123")
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=message),
        ) as send_message:
            await self.process(
                self.alice_session, chat_payload("hi", "client-abc-123")
            )
        self.assertEqual("client-abc-123", send_message.call_args.args[3])
        self.assertEqual(
            "client-abc-123",
            self.alice_ws.messages("chat_message")[0]["client_msg_id"],
        )

    async def test_missing_client_msg_id_is_allowed(self):
        message = dict(self.message, client_msg_id=None)
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=message),
        ):
            await self.process(self.alice_session, chat_payload("hi", None))
        self.assertIsNone(
            self.alice_ws.messages("chat_message")[0]["client_msg_id"]
        )


class ChatValidationTests(WsChatTestCase):
    async def test_chat_requires_joined_room(self):
        lonely_ws = FakeWebSocket()
        lonely = make_session(self.manager, make_user(3, "carol"), lonely_ws)
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(),
        ) as send_message:
            await self.process(lonely, chat_payload())
        self.assertEqual("NOT_ROOM_MEMBER", lonely_ws.last()["code"])
        send_message.assert_not_awaited()

    async def test_non_member_is_rejected(self):
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            await self.process(self.alice_session, chat_payload())
        self.assertEqual("NOT_ROOM_MEMBER", self.alice_ws.last()["code"])
        self.assertEqual([], self.bob_ws.messages("chat_message"))

    async def test_closed_room_is_rejected(self):
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_ROOM_CLOSED, "房间已关闭", 409)
            ),
        ):
            await self.process(self.alice_session, chat_payload())
        self.assertEqual("ROOM_CLOSED", self.alice_ws.last()["code"])

    async def test_empty_and_oversized_content_rejected(self):
        for content in ("", "   ", "x" * 2001):
            with self.subTest(content=repr(content)[:10]):
                self.alice_ws.sent.clear()
                with mock.patch.object(
                    manager_module.messages_service,
                    "send_message",
                    new=mock.AsyncMock(),
                ) as send_message:
                    await self.process(
                        self.alice_session, chat_payload(content)
                    )
                self.assertEqual("INVALID_MESSAGE", self.alice_ws.last()["code"])
                send_message.assert_not_awaited()

    async def test_unauthenticated_chat_is_rejected(self):
        session = manager_module.ClientSession(websocket=FakeWebSocket())
        await self.process(session, chat_payload())
        self.assertEqual("NOT_AUTHENTICATED", session.websocket.last()["code"])


class ChatRateLimitTests(WsChatTestCase):
    async def test_sixth_message_in_one_second_is_rate_limited(self):
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=self.message),
        ) as send_message:
            for _ in range(5):
                await self.process(self.alice_session, chat_payload())
                self.assertEqual([], self.alice_ws.messages("error"))
            await self.process(self.alice_session, chat_payload())

        self.assertEqual(5, send_message.await_count)
        self.assertEqual("RATE_LIMITED", self.alice_ws.last()["code"])
        self.assertEqual(5, len(self.alice_ws.messages("chat_message")))
        # 其他用户不受影响
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=self.message),
        ):
            await self.process(self.bob_session, chat_payload())

    async def test_window_slides(self):
        with mock.patch.object(
            manager_module.messages_service,
            "send_message",
            new=mock.AsyncMock(return_value=self.message),
        ):
            for _ in range(5):
                await self.process(self.alice_session, chat_payload())
            self.clock.now += 1.1
            await self.process(self.alice_session, chat_payload())

        self.assertEqual([], self.alice_ws.messages("error"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
