"""F1–F4：用户资料/昵称/改密、房间软删、房主改房、历史窗口测试（离线）。"""

import os
import unittest
from datetime import datetime, timedelta
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

from api import v1_auth, v1_common, v1_rooms  # noqa: E402
from security.passwords import hash_password, verify_password  # noqa: E402
from service import messages as messages_service  # noqa: E402
from service import rooms as rooms_service  # noqa: E402
from service import user as user_service  # noqa: E402
from service.messages import ERR_INVALID_PAGINATION, MessageError  # noqa: E402
from service.rooms import ERR_ROOM_FORBIDDEN, ERR_ROOM_NOT_FOUND, RoomError  # noqa: E402
from tests.ws_support import make_room, manager_module  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


class DummyRequest:
    def __init__(self, request_id="req_f"):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id=request_id)
        self.url = SimpleNamespace(path="/api/v1/users/me")
        self.method = "PATCH"


def make_user(user_id=1, username="alice", nickname="Alice"):
    return SimpleNamespace(
        id=user_id, username=username, nickname=nickname, created_at=BASE_TIME
    )


class RegisterUsernameTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": "unit-test-jwt-secret-0123456789abcdef", "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.cases = [
            ("abc123", True),  # 至少一个字母 + 6 位
            ("ABCdef", True),
            ("a12345", True),
            ("12345a", True),
            ("abc12", False),  # 太短
            ("a" * 21, False),  # 太长
            ("123456", False),  # 纯数字
            ("abc-123", False),  # 特殊字符
            ("abc_123", False),  # 下划线不允许（新规则）
        ]

    def test_register_username_rules(self):
        async def run():
            for username, valid in self.cases:
                with self.subTest(username=username):
                    payload = SimpleNamespace(username=username, password="Pa55w0rd!")
                    with mock.patch.object(
                        v1_auth.UserService,
                        "create_user",
                        new=mock.AsyncMock(
                            return_value={
                                "success": True,
                                "user": {
                                    "id": 9,
                                    "username": username,
                                    "nickname": username,
                                },
                            }
                        ),
                    ):
                        if valid:
                            response = await v1_auth.register(
                                DummyRequest(), payload
                            )
                            self.assertEqual(200, response["code"])
                        else:
                            with self.assertRaises(v1_common.APIError) as ctx:
                                await v1_auth.register(DummyRequest(), payload)
                            self.assertEqual(
                                "INVALID_USERNAME", ctx.exception.error_code
                            )

        import asyncio

        asyncio.run(run())

    def test_register_username_case_sensitive(self):
        async def run():
            for username in ("Alice1", "alice1"):
                with self.subTest(username=username):
                    with mock.patch.object(
                        v1_auth.UserService,
                        "create_user",
                        new=mock.AsyncMock(
                            return_value={
                                "success": True,
                                "user": {"id": 9, "username": username, "nickname": username},
                            }
                        ),
                    ) as create:
                        await v1_auth.register(
                            DummyRequest(),
                            SimpleNamespace(username=username, password="Pa55w0rd!"),
                        )
                    self.assertEqual(username, create.await_args.args[0])

        import asyncio

        asyncio.run(run())


class NicknameApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_nickname_success_and_trim_control_chars(self):
        user = make_user()
        with mock.patch.object(
            v1_auth.UserService,
            "update_nickname",
            new=mock.AsyncMock(
                return_value={
                    "success": True,
                    "user": {"id": 1, "username": "alice", "nickname": "Ali"},
                }
            ),
        ) as update:
            response = await v1_auth.update_me(
                DummyRequest(),
                SimpleNamespace(nickname=" \x00Ali\n "),
                user,
            )

        update.assert_awaited_once_with(1, "Ali")
        self.assertEqual({"id": "1", "username": "alice", "nickname": "Ali"}, response["data"]["user"])

    async def test_update_nickname_validation(self):
        for nickname in ("", "   ", "x" * 21, "\x01\x02"):
            with self.subTest(nickname=repr(nickname)):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_auth.update_me(
                        DummyRequest(),
                        SimpleNamespace(nickname=nickname),
                        make_user(),
                    )
                self.assertEqual("INVALID_NICKNAME", ctx.exception.error_code)

    async def test_update_nickname_without_service_call_when_invalid(self):
        with mock.patch.object(
            v1_auth.UserService, "update_nickname", new=mock.AsyncMock()
        ) as update:
            with self.assertRaises(v1_common.APIError):
                await v1_auth.update_me(
                    DummyRequest(), SimpleNamespace(nickname=" "), make_user()
                )
        update.assert_not_awaited()


class RegisterPasswordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": "unit-test-jwt-secret-0123456789abcdef"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    async def test_registration_password_8_to_32(self):
        for password in ("123456", "1234567"):
            with self.subTest(length=len(password)):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_auth.register(
                        DummyRequest(),
                        SimpleNamespace(username="abc123", password=password),
                    )
                self.assertEqual("INVALID_NEW_PASSWORD", ctx.exception.error_code)

        for password, ok in (("12345678", True), ("x" * 32, True), ("x" * 33, False)):
            with self.subTest(length=len(password)):
                with mock.patch.object(
                    v1_auth.UserService,
                    "create_user",
                    new=mock.AsyncMock(
                        return_value={
                            "success": True,
                            "user": {"id": 1, "username": "abc123", "nickname": "abc123"},
                        }
                    ),
                ) as create:
                    if ok:
                        response = await v1_auth.register(
                            DummyRequest(),
                            SimpleNamespace(username="abc123", password=password),
                        )
                        self.assertEqual(200, response["code"])
                    else:
                        with self.assertRaises(v1_common.APIError) as ctx:
                            await v1_auth.register(
                                DummyRequest(),
                                SimpleNamespace(username="abc123", password=password),
                            )
                        self.assertEqual(
                            "INVALID_NEW_PASSWORD", ctx.exception.error_code
                        )
                        create.assert_not_awaited()


class OldAccountLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_short_username_can_still_login(self):
        env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": "unit-test-jwt-secret-0123456789abcdef"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        with mock.patch.object(
            v1_auth.UserService,
            "login",
            new=mock.AsyncMock(
                return_value={
                    "success": True,
                    "user": {"id": 1, "username": "Lim", "nickname": "Lim"},
                }
            ),
        ):
            response = await v1_auth.login(
                DummyRequest(), SimpleNamespace(username="Lim", password="pw")
            )
        self.assertEqual(200, response["code"])
        self.assertEqual("Lim", response["data"]["user"]["username"])


class ProfileRouteTests(unittest.TestCase):
    def test_profile_routes_registered(self):
        routes = set(v1_auth.router.full_paths())
        self.assertIn(("PATCH", "/api/v1/users/me"), routes)
        self.assertIn(("POST", "/api/v1/users/me/password"), routes)


class ChangePasswordApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_change_password_success_does_not_require_relogin(self):
        with mock.patch.object(
            v1_auth.UserService,
            "change_password",
            new=mock.AsyncMock(return_value={"success": True}),
        ) as change:
            response = await v1_auth.change_my_password(
                DummyRequest(),
                SimpleNamespace(old_password="old-pass", new_password="new-pass-123"),
                make_user(),
            )
        change.assert_awaited_once_with(1, "old-pass", "new-pass-123")
        self.assertEqual({"changed": True}, response["data"])

    async def test_change_password_accepts_32(self):
        with mock.patch.object(
            v1_auth.UserService,
            "change_password",
            new=mock.AsyncMock(return_value={"success": True}),
        ) as change:
            response = await v1_auth.change_my_password(
                DummyRequest(),
                SimpleNamespace(old_password="old", new_password="x" * 32),
                make_user(),
            )
        self.assertEqual({"changed": True}, response["data"])
        change.assert_awaited_once()

    async def test_wrong_old_password(self):
        with mock.patch.object(
            v1_auth.UserService,
            "change_password",
            new=mock.AsyncMock(
                return_value={
                    "success": False,
                    "error_code": "WRONG_OLD_PASSWORD",
                    "message": "旧密码错误",
                }
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_auth.change_my_password(
                    DummyRequest(),
                    SimpleNamespace(old_password="bad", new_password="new-pass-123"),
                    make_user(),
                )
        self.assertEqual("WRONG_OLD_PASSWORD", ctx.exception.error_code)
        self.assertEqual(400, ctx.exception.status_code)

    async def test_new_password_length(self):
        for password in ("short", "x" * 33):
            with self.subTest(length=len(password)):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_auth.change_my_password(
                        DummyRequest(),
                        SimpleNamespace(old_password="old", new_password=password),
                        make_user(),
                    )
                self.assertEqual("INVALID_NEW_PASSWORD", ctx.exception.error_code)


class UserServiceProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_nickname_returns_user(self):
        updated = make_user(nickname="新昵称")
        with mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock(return_value=updated)
        ):
            result = await user_service.UserService.update_nickname(1, "新昵称")
        self.assertTrue(result["success"])
        self.assertEqual("新昵称", result["user"]["nickname"])

    async def test_change_password_hashes_new_password(self):
        stored = hash_password("old-pass")
        user = SimpleNamespace(id=1, username="alice", nickname="Alice", password=stored)
        with mock.patch.object(
            user_service.UserDao, "get_user_by_id", new=mock.AsyncMock(return_value=user)
        ), mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock()
        ) as update:
            result = await user_service.UserService.change_password(
                1, "old-pass", "new-pass-123"
            )
        self.assertTrue(result["success"])
        written = update.await_args.kwargs["password"]
        self.assertTrue(verify_password("new-pass-123", written))
        self.assertNotIn("new-pass-123", written)

    async def test_change_password_wrong_old(self):
        user = SimpleNamespace(
            id=1, username="alice", nickname="Alice", password=hash_password("old-pass")
        )
        with mock.patch.object(
            user_service.UserDao, "get_user_by_id", new=mock.AsyncMock(return_value=user)
        ), mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock()
        ) as update:
            result = await user_service.UserService.change_password(
                1, "wrong", "new-pass-123"
            )
        self.assertFalse(result["success"])
        self.assertEqual("WRONG_OLD_PASSWORD", result["error_code"])
        update.assert_not_awaited()


class SerializerNicknameTests(unittest.TestCase):
    def test_room_member_exposes_nickname_only(self):
        member = SimpleNamespace(
            user=make_user(username="alice", nickname="Ali"), role="member"
        )
        data = rooms_service.member_to_dict(member)
        self.assertEqual({"id": "1", "nickname": "Ali", "role": "member"}, data)
        self.assertNotIn("username", data)

    def test_chat_message_exposes_nickname_only(self):
        message = SimpleNamespace(
            id="m1",
            room_id="r1",
            user=make_user(username="alice", nickname="Ali"),
            content="hi",
            client_msg_id=None,
            created_at=BASE_TIME,
        )
        data = messages_service.message_to_dict(message)
        self.assertEqual({"id": "1", "nickname": "Ali"}, data["user"])
        self.assertNotIn("username", data["user"])

    def test_ws_user_payload_exposes_nickname_only(self):
        payload = manager_module.WebSocketManager._user_payload(
            make_user(username="alice", nickname="Ali")
        )
        self.assertEqual({"id": "1", "nickname": "Ali"}, payload)


class SoftDeleteServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.room = make_room()
        self.room.deleted = False
        self.room.owner_id = 1

    async def test_owner_soft_deletes(self):
        soft_delete = mock.AsyncMock()
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "soft_delete", new=soft_delete
        ):
            result = await rooms_service.delete_room(make_user(1), self.room.code)
        soft_delete.assert_awaited_once_with(self.room)
        self.assertIs(self.room, result)

    async def test_delete_is_idempotent(self):
        self.room.deleted = True
        soft_delete = mock.AsyncMock()
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "soft_delete", new=soft_delete
        ):
            await rooms_service.delete_room(make_user(1), self.room.code)
        soft_delete.assert_not_awaited()

    async def test_non_owner_forbidden(self):
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=self.room)
        ):
            with self.assertRaises(RoomError) as ctx:
                await rooms_service.delete_room(make_user(2), self.room.code)
        self.assertEqual(ERR_ROOM_FORBIDDEN, ctx.exception.error_code)

    async def test_deleted_room_resolves_as_not_found(self):
        self.room.deleted = True
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=self.room)
        ):
            with self.assertRaises(RoomError) as ctx:
                await rooms_service.resolve_room(self.room.code)
        self.assertEqual(ERR_ROOM_NOT_FOUND, ctx.exception.error_code)

    async def test_public_list_excludes_deleted(self):
        deleted = make_room(room_id="r-deleted")
        deleted.deleted = True
        active = make_room(room_id="r-active")
        active.deleted = False
        with mock.patch.object(
            rooms_service.RoomDao,
            "list_public",
            new=mock.AsyncMock(return_value=[deleted, active]),
        ), mock.patch.object(
            rooms_service.RoomMemberDao, "count", new=mock.AsyncMock(return_value=1)
        ):
            items, has_more, _ = await rooms_service.list_public_rooms(limit=10)
        self.assertEqual(["r-active"], [room.id for room, _count in items])

    async def test_join_deleted_room_returns_not_found(self):
        self.room.deleted = True
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=self.room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=self.room)
        ):
            with self.assertRaises(RoomError) as ctx:
                await rooms_service.join_room(make_user(2), self.room.code)
        self.assertEqual(ERR_ROOM_NOT_FOUND, ctx.exception.error_code)


class RoomPatchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_patch_only_name_password(self):
        room = SimpleNamespace(
            id="r1", code="A1B2C", name="旧名", visibility="private",
            password_hash=hash_password("old-pass"), owner_id=1,
            closed_at=None, deleted=False, background_id=None,
        )
        updated = mock.AsyncMock(return_value=room)
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=room)
        ), mock.patch.object(rooms_service.RoomDao, "update", new=updated):
            await rooms_service.update_room(
                make_user(1),
                "A1B2C",
                {"name": " 新名 ", "password": "new-pass", "visibility": "public", "background_id": "bg"},
            )
        fields = updated.await_args.kwargs
        self.assertEqual("新名", fields["name"])
        self.assertTrue(verify_password("new-pass", fields["password_hash"]))
        self.assertNotIn("visibility", fields)
        self.assertNotIn("background_id", fields)

    async def test_name_max_30(self):
        with self.assertRaises(RoomError) as ctx:
            rooms_service._validate_room_name("x" * 31)
        self.assertEqual(rooms_service.ERR_ROOM_NAME_INVALID, ctx.exception.error_code)
        self.assertEqual("x" * 30, rooms_service._validate_room_name("x" * 30))


class HistoryWindowTests(unittest.IsolatedAsyncioTestCase):
    def test_window_days(self):
        now = datetime(2026, 1, 10, 0, 0, 0)
        self.assertEqual(7, messages_service.history_window_days(now - timedelta(days=6), now))
        self.assertEqual(30, messages_service.history_window_days(now - timedelta(days=7), now))

    async def test_list_messages_passes_since_and_rejects_old_cursor(self):
        class FakeDao:
            def __init__(self):
                self.since = None
                self.cursor = None
                self.rows = []

            async def get_by_id_for_room(self, room_id, message_id):
                return self.cursor

            async def list_for_room(self, room_id, before=None, limit=50, since=None):
                self.since = since
                return list(self.rows)

            async def has_older_than(self, room_id, cutoff):
                return False

        dao = FakeDao()
        room = SimpleNamespace(id="r1")
        now = datetime.now()
        with mock.patch.object(messages_service, "MessageDao", dao):
            page = await messages_service.list_messages(
                room, limit=10, joined_at=now - timedelta(days=8)
            )
            self.assertFalse(page["has_more"])
            self.assertEqual(30, page["window_days"])
            self.assertFalse(page["truncated"])
            self.assertIsNotNone(dao.since)
            self.assertGreaterEqual(now - dao.since, timedelta(days=29, hours=23))

            old = SimpleNamespace(
                id="old", created_at=now - timedelta(days=31), room_id="r1"
            )
            dao.cursor = old
            with self.assertRaises(MessageError) as ctx:
                await messages_service.list_messages(
                    room, before="old", joined_at=now - timedelta(days=1)
                )
        self.assertEqual(ERR_INVALID_PAGINATION, ctx.exception.error_code)


class HistoryNicknameRealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_nickname_reflects_current_user(self):
        now = datetime.now()
        user = SimpleNamespace(id=5, username="alice", nickname="旧昵称")
        message = SimpleNamespace(
            id="m1",
            room_id="r1",
            user=user,
            content="hi",
            client_msg_id=None,
            created_at=now,
        )

        class FakeDao:
            async def list_for_room(self, room_id, before=None, limit=50, since=None):
                return [message]

            async def has_older_than(self, room_id, cutoff):
                return False

        room = SimpleNamespace(id="r1")
        with mock.patch.object(messages_service, "MessageDao", FakeDao()):
            first = await messages_service.list_messages(
                room, limit=10, joined_at=now
            )
            self.assertEqual("旧昵称", first["items"][0]["user"]["nickname"])

            user.nickname = "新昵称"
            second = await messages_service.list_messages(
                room, limit=10, joined_at=now
            )
            self.assertEqual("新昵称", second["items"][0]["user"]["nickname"])
            self.assertNotIn("username", second["items"][0]["user"])


class RoomApiF2F3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = make_user()
        self.room = make_room()
        self.room.deleted = True
        self.room.owner_id = 1

    async def test_delete_endpoint_soft_deletes_and_broadcasts(self):
        manager = SimpleNamespace(
            flush_canvas_on_close=mock.AsyncMock(return_value=0),
            detach_room_sessions=mock.AsyncMock(return_value=2),
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "delete_room",
            new=mock.AsyncMock(return_value=self.room),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            response = await v1_rooms.close_room(DummyRequest(), self.room.code, self.user)

        self.assertEqual({"deleted": True}, response["data"])
        manager.flush_canvas_on_close.assert_awaited_once_with(self.room.id)
        kwargs = manager.detach_room_sessions.await_args.kwargs
        self.assertEqual("deleted", kwargs["reason"])
        self.assertEqual("room_deleted", kwargs["before_event"]["type"])

    async def test_patch_endpoint_broadcasts_room_updated(self):
        room = make_room()
        room.password_hash = None
        manager = SimpleNamespace(
            notify_room_updated=mock.AsyncMock(return_value=None)
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "update_room",
            new=mock.AsyncMock(return_value=room),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "list_members",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            response = await v1_rooms.update_room(
                DummyRequest(),
                room.code,
                v1_rooms.UpdateRoomRequest(name="新名"),
                self.user,
            )
        manager.notify_room_updated.assert_awaited_once_with(room)
        self.assertEqual(200, response["code"])


class MigrationScriptTests(unittest.TestCase):
    def test_migration_is_idempotent(self):
        import scripts.migrate_f as migration

        class FakeCursor:
            def __init__(self, existing, tables=("users", "rooms")):
                self.existing = set(existing)
                self.tables = set(tables)
                self.executed = []
                self.rowcount = 0
                self._result = None

            def execute(self, sql, params=None):
                self.executed.append((sql, params))
                if sql.startswith("SELECT 1 FROM"):
                    table = sql.split()[3]
                    if table not in self.tables:
                        raise RuntimeError(f"no such table: {table}")
                    self._result = (1,)
                elif sql.startswith("SELECT COUNT(*)"):
                    self._result = (1 if (params[0], params[1]) in self.existing else 0,)
                elif "UPDATE users SET nickname" in sql:
                    self.rowcount = 3
                    self._result = None
                else:
                    self._result = None

            def fetchone(self):
                return self._result

            def fetchall(self):
                return []

            def close(self):
                pass

        class FakeConnection:
            def __init__(self, existing, tables=("users", "rooms")):
                self.cursor_obj = FakeCursor(existing, tables)
                self.commits = 0

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.commits += 1

            def rollback(self):
                pass

        connection = FakeConnection(existing=set())
        stats = migration.migrate(connection)
        self.assertEqual(1, stats["nickname_added"])
        self.assertEqual(1, stats["deleted_added"])
        self.assertEqual(3, stats["nickname_backfilled"])
        alters = [sql for sql, _ in connection.cursor_obj.executed if sql.startswith("ALTER")]
        self.assertEqual(2, len(alters))

        connection2 = FakeConnection(
            existing={("users", "nickname"), ("rooms", "deleted")}
        )
        stats2 = migration.migrate(connection2)
        alters2 = [sql for sql, _ in connection2.cursor_obj.executed if sql.startswith("ALTER")]
        self.assertEqual([], alters2)
        self.assertEqual(0, stats2["nickname_added"])
        self.assertEqual(0, stats2["deleted_added"])

        # 空库（应用还未 generate_schemas）：安全跳过，不 ALTER 不 UPDATE
        connection3 = FakeConnection(existing=set(), tables=())
        stats3 = migration.migrate(connection3)
        self.assertEqual(2, stats3["skipped_missing_tables"])
        self.assertEqual(0, stats3["nickname_added"])
        self.assertEqual(0, stats3["deleted_added"])
        self.assertEqual(
            [],
            [
                sql
                for sql, _ in connection3.cursor_obj.executed
                if sql.startswith("ALTER") or "UPDATE users" in sql
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
