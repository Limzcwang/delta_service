"""C：消息服务层/限频/清理/DAO 结构测试（离线，fake DAO）。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from tests.support import install_tortoise_stub

install_tortoise_stub()

from dao import message as message_dao_module  # noqa: E402
from service import messages as messages_service  # noqa: E402
from service.rooms import ERR_NOT_ROOM_MEMBER, RoomError  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_user(user_id=1, username="alice"):
    return SimpleNamespace(id=user_id, username=username)


def make_room(room_id="room-1"):
    return SimpleNamespace(id=room_id)


def make_message(message_id="m1", room_id="room-1", user=None, content="hi", client_msg_id=None, created_at=BASE_TIME):
    return SimpleNamespace(
        id=message_id,
        room_id=room_id,
        user=user or make_user(),
        content=content,
        client_msg_id=client_msg_id,
        created_at=created_at,
    )


class FakeMessageDao:
    def __init__(self):
        self.created = []
        self.create_result = None
        self.cursor_result = None
        self.cursor_lookup = None
        self.list_args = None
        self.list_result = []
        self.deleted_cutoff = None
        self.deleted_count = 0

    async def create(self, **kwargs):
        self.created.append(kwargs)
        if self.create_result is not None:
            return self.create_result
        return make_message(
            message_id=kwargs["id"],
            room_id=kwargs["room"].id,
            user=kwargs["user"],
            content=kwargs["content"],
            client_msg_id=kwargs.get("client_msg_id"),
        )

    async def get_by_id_for_room(self, room_id, message_id):
        self.cursor_lookup = (room_id, message_id)
        return self.cursor_result

    async def list_for_room(self, room_id, before=None, limit=50):
        self.list_args = (room_id, before, limit)
        return list(self.list_result)

    async def delete_older_than(self, cutoff):
        self.deleted_cutoff = cutoff
        return self.deleted_count


class MessageServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.dao = FakeMessageDao()
        self.patches = [
            mock.patch.object(messages_service, "MessageDao", self.dao),
            mock.patch.object(
                messages_service.rooms_service,
                "ensure_member",
                new=mock.AsyncMock(return_value=None),
            ),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def await_(self, coro):
        import asyncio

        return asyncio.run(coro)


class ContentValidationTests(unittest.TestCase):
    def test_valid_content_is_stripped(self):
        self.assertEqual("hello", messages_service.normalize_content("  hello  "))
        self.assertEqual("x" * 2000, messages_service.normalize_content("x" * 2000))

    def test_invalid_content(self):
        for content in (None, 123, "", "   ", "x" * 2001):
            with self.subTest(content=repr(content)[:20]):
                with self.assertRaises(messages_service.MessageError) as ctx:
                    messages_service.normalize_content(content)
                self.assertEqual(
                    messages_service.ERR_INVALID_MESSAGE,
                    ctx.exception.error_code,
                )

    def test_client_msg_id_length_and_type(self):
        self.assertIsNone(messages_service.normalize_client_msg_id(None))
        self.assertEqual("abc", messages_service.normalize_client_msg_id("abc"))
        with self.assertRaises(messages_service.MessageError):
            messages_service.normalize_client_msg_id("x" * 129)
        with self.assertRaises(messages_service.MessageError):
            messages_service.normalize_client_msg_id(123)


class SendMessageTests(MessageServiceTestCase):
    def test_send_message_persists_and_serializes(self):
        room = make_room()
        user = make_user(5, "dsh_test")
        result = self.await_(
            messages_service.send_message(user, room, "  hi  ", "c-1")
        )

        messages_service.rooms_service.ensure_member.assert_awaited_once_with(room, user)
        created = self.dao.created[0]
        self.assertEqual(room, created["room"])
        self.assertEqual(user, created["user"])
        self.assertEqual("hi", created["content"])
        self.assertEqual("c-1", created["client_msg_id"])
        self.assertEqual(32, len(created["id"]))
        self.assertEqual("hi", result["content"])
        self.assertEqual("c-1", result["client_msg_id"])
        self.assertEqual({"id": "5", "username": "dsh_test"}, result["user"])
        self.assertEqual(room.id, result["room_id"])
        self.assertEqual(int(BASE_TIME.timestamp() * 1000), result["created_at"])

    def test_send_message_requires_membership(self):
        with mock.patch.object(
            messages_service.rooms_service,
            "ensure_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(RoomError):
                self.await_(
                    messages_service.send_message(make_user(), make_room(), "hi")
                )
        self.assertEqual([], self.dao.created)

    def test_send_message_empty_content_never_hits_dao(self):
        with self.assertRaises(messages_service.MessageError):
            self.await_(messages_service.send_message(make_user(), make_room(), ""))
        self.assertEqual([], self.dao.created)


class ListMessagesTests(MessageServiceTestCase):
    def test_first_page_has_more_and_next_before(self):
        room = make_room()
        self.dao.list_result = [make_message(f"m{i}") for i in range(3)]
        page = self.await_(messages_service.list_messages(room, limit=2))

        self.assertTrue(page["has_more"])
        self.assertEqual(["m0", "m1"], [item["id"] for item in page["items"]])
        self.assertEqual("m1", page["next_before"])
        self.assertEqual((room.id, None, 2), self.dao.list_args)

    def test_cursor_is_resolved_and_forwarded(self):
        room = make_room()
        cursor = make_message("m9")
        self.dao.cursor_result = cursor
        self.dao.list_result = [make_message("m8")]
        page = self.await_(
            messages_service.list_messages(room, before="m9", limit=50)
        )

        self.assertEqual((room.id, "m9"), self.dao.cursor_lookup)
        self.assertIs(cursor, self.dao.list_args[1])
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_before"])
        self.assertEqual("m8", page["items"][0]["id"])

    def test_unknown_cursor_and_bad_limit(self):
        room = make_room()
        with self.assertRaises(messages_service.MessageError) as ctx:
            self.await_(messages_service.list_messages(room, before="missing"))
        self.assertEqual(
            messages_service.ERR_INVALID_PAGINATION, ctx.exception.error_code
        )

        for limit in (0, 101, "x"):
            with self.subTest(limit=limit):
                with self.assertRaises(messages_service.MessageError):
                    self.await_(
                        messages_service.list_messages(room, limit=limit)
                    )

    def test_empty_history(self):
        page = self.await_(messages_service.list_messages(make_room()))
        self.assertEqual(
            {"items": [], "has_more": False, "next_before": None}, page
        )


class CleanupTests(MessageServiceTestCase):
    def test_cleanup_uses_retention_days(self):
        now = datetime(2026, 2, 1, 0, 0, 0)
        self.dao.deleted_count = 7
        deleted = self.await_(
            messages_service.cleanup_old_messages(now=now)
        )

        self.assertEqual(7, deleted)
        self.assertEqual(now - timedelta(days=30), self.dao.deleted_cutoff)

    def test_cleanup_rejects_bad_days(self):
        for days in (0, -1, "30"):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    self.await_(messages_service.cleanup_old_messages(days=days))


class RateLimiterTests(unittest.TestCase):
    class FakeClock:
        def __init__(self):
            self.now = 100.0

        def __call__(self):
            return self.now

    def test_five_per_second_then_limited(self):
        clock = self.FakeClock()
        limiter = messages_service.MessageRateLimiter(clock=clock)
        for _ in range(5):
            self.assertTrue(limiter.check("1"))
        self.assertFalse(limiter.check("1"))
        self.assertGreater(limiter.retry_after("1"), 0)

        # 其他用户不受影响
        self.assertTrue(limiter.check("2"))

    def test_window_slides(self):
        clock = self.FakeClock()
        limiter = messages_service.MessageRateLimiter(clock=clock)
        for _ in range(5):
            self.assertTrue(limiter.check("1"))
        clock.now += 1.1
        self.assertTrue(limiter.check("1"))

    def test_reset(self):
        limiter = messages_service.MessageRateLimiter()
        for _ in range(5):
            self.assertTrue(limiter.check("1"))
        self.assertFalse(limiter.check("1"))
        limiter.reset("1")
        self.assertTrue(limiter.check("1"))


class MessageDaoStructureTests(unittest.TestCase):
    def test_model_and_dao_methods(self):
        self.assertEqual("room_messages", message_dao_module.RoomMessage.Meta.table)
        for name in (
            "create",
            "get_by_id_for_room",
            "list_for_room",
            "delete_older_than",
        ):
            self.assertTrue(
                hasattr(message_dao_module.MessageDao, name), name
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
