"""B2a：v1 房间/注册 HTTP 接口与错误码测试（离线）。"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    REPO_ROOT,
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
from security import tokens  # noqa: E402
from service import backgrounds as backgrounds_service  # noqa: E402
from service.rooms import RoomError  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"


class DummyRequest:
    def __init__(self, request_id="req_test", ip="203.0.113.10"):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host=ip)
        self.state = SimpleNamespace(request_id=request_id)
        self.url = SimpleNamespace(path="/api/v1/rooms")
        self.method = "GET"


def make_room(room_id="room-1", code="A1B2C", **overrides):
    values = {
        "id": room_id,
        "code": code,
        "name": "开黑房",
        "visibility": "public",
        "password_hash": None,
        "owner_id": 1,
        "background_id": None,
        "created_at": None,
        "updated_at": None,
        "closed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_user(user_id=1, username="alice"):
    return SimpleNamespace(id=user_id, username=username)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_Days": "7", "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.user = make_user()


class CreateRoomApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_create_room_success_envelope(self):
        room = make_room()
        member = {"id": "1", "username": "alice", "role": "owner"}
        payload = v1_rooms.CreateRoomRequest(
            name="开黑房", visibility="public", password=None, background_id=None
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "create_room",
            new=mock.AsyncMock(return_value=room),
        ) as create_room, mock.patch.object(
            v1_rooms.rooms_service,
            "list_members",
            new=mock.AsyncMock(return_value=[member]),
        ):
            response = await v1_rooms.create_room(
                DummyRequest(request_id="req_create"), payload, self.user
            )

        create_room.assert_awaited_once()
        self.assertEqual(200, response["code"])
        self.assertEqual("req_create", response["request_id"])
        data = response["data"]["room"]
        self.assertEqual("room-1", data["id"])
        self.assertEqual("A1B2C", data["code"])
        self.assertNotIn("password_hash", json.dumps(response))

    async def test_create_room_maps_room_error(self):
        payload = v1_rooms.CreateRoomRequest(name="", visibility="public")
        with mock.patch.object(
            v1_rooms.rooms_service,
            "create_room",
            new=mock.AsyncMock(side_effect=RoomError("ROOM_NAME_INVALID", "房间名非法")),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.create_room(DummyRequest(), payload, self.user)
        self.assertEqual(400, ctx.exception.status_code)
        self.assertEqual("ROOM_NAME_INVALID", ctx.exception.error_code)


class ListRoomApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_list_returns_items_has_more_next_before(self):
        room = make_room()
        with mock.patch.object(
            v1_rooms.rooms_service,
            "list_public_rooms",
            new=mock.AsyncMock(return_value=([(room, 2)], True, 123456)),
        ) as list_rooms:
            response = await v1_rooms.list_rooms(
                DummyRequest(), limit=20, before=None, user=self.user
            )
        list_rooms.assert_awaited_once_with(20, None)
        data = response["data"]
        self.assertEqual(1, len(data["items"]))
        self.assertEqual(2, data["items"][0]["member_count"])
        self.assertTrue(data["has_more"])
        self.assertEqual(123456, data["next_before"])

    async def test_list_maps_closed_and_pagination_errors(self):
        for error in (
            RoomError("ROOM_CLOSED", "房间已关闭", 409),
            RoomError("INVALID_PAGINATION", "limit 非法"),
        ):
            with self.subTest(error=error.error_code):
                with mock.patch.object(
                    v1_rooms.rooms_service,
                    "list_public_rooms",
                    new=mock.AsyncMock(side_effect=error),
                ):
                    with self.assertRaises(v1_common.APIError) as ctx:
                        await v1_rooms.list_rooms(
                            DummyRequest(), limit=20, before=None, user=self.user
                        )
                self.assertEqual(error.error_code, ctx.exception.error_code)


class JoinLeaveApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_join_password_required_error_has_string_code(self):
        payload = v1_rooms.JoinRoomRequest(password=None)
        with mock.patch.object(
            v1_rooms.rooms_service,
            "join_room",
            new=mock.AsyncMock(
                side_effect=RoomError("ROOM_PASSWORD_REQUIRED", "该房间需要密码")
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.join_room(
                    DummyRequest(), "A1B2C", payload, self.user
                )
        error = ctx.exception
        self.assertEqual(400, error.status_code)
        self.assertEqual(v1_common.CODE_BAD_REQUEST, error.code)
        self.assertEqual("ROOM_PASSWORD_REQUIRED", error.error_code)

        response = await v1_common.api_error_handler(DummyRequest(), error)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(
            {"error_code": "ROOM_PASSWORD_REQUIRED"}, body["data"]
        )

    async def test_join_success_includes_members(self):
        room = make_room()
        members = [{"id": "1", "username": "alice", "role": "member"}]
        with mock.patch.object(
            v1_rooms.rooms_service,
            "join_room",
            new=mock.AsyncMock(return_value=(room, members)),
        ):
            response = await v1_rooms.join_room(
                DummyRequest(), "A1B2C", v1_rooms.JoinRoomRequest(), self.user
            )
        self.assertEqual(200, response["code"])
        self.assertEqual(members, response["data"]["members"])
        self.assertNotIn("password_hash", json.dumps(response))

    async def test_leave_success(self):
        manager = SimpleNamespace(
            notify_user_kicked=mock.AsyncMock(return_value=True),
            detach_user_from_room=mock.AsyncMock(return_value=True),
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "leave_room",
            new=mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=make_room()),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            response = await v1_rooms.leave_room(
                DummyRequest(), "A1B2C", self.user
            )
        self.assertEqual({"left": True}, response["data"])
        manager.detach_user_from_room.assert_awaited_once_with(
            1, "room-1", reason="left"
        )


class RoomOwnerApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_error_mapping(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "update_room",
            new=mock.AsyncMock(
                side_effect=RoomError("NOT_ROOM_OWNER", "仅房主可执行该操作", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.update_room(
                    DummyRequest(),
                    "A1B2C",
                    v1_rooms.UpdateRoomRequest(name="x"),
                    self.user,
                )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual("NOT_ROOM_OWNER", ctx.exception.error_code)

    async def test_close_and_kick_success(self):
        manager = SimpleNamespace(
            flush_canvas_on_close=mock.AsyncMock(return_value=2),
            detach_room_sessions=mock.AsyncMock(return_value=1),
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "delete_room",
            new=mock.AsyncMock(return_value=make_room()),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            response = await v1_rooms.close_room(DummyRequest(), "A1B2C", self.user)
        self.assertEqual({"deleted": True}, response["data"])
        manager.flush_canvas_on_close.assert_awaited_once_with("room-1")
        manager.detach_room_sessions.assert_awaited_once()
        self.assertEqual("room-1", manager.detach_room_sessions.await_args.args[0])
        self.assertEqual("deleted", manager.detach_room_sessions.await_args.kwargs["reason"])

        manager = SimpleNamespace(
            notify_user_kicked=mock.AsyncMock(return_value=True),
            detach_user_from_room=mock.AsyncMock(return_value=True),
        )
        with mock.patch.object(
            v1_rooms.rooms_service,
            "kick_member",
            new=mock.AsyncMock(return_value=[]),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=make_room()),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            response = await v1_rooms.kick_member(
                DummyRequest(), "A1B2C", v1_rooms.KickMemberRequest(user_id="2"), self.user
            )
        self.assertEqual({"members": []}, response["data"])
        manager.notify_user_kicked.assert_awaited_once()
        self.assertEqual("2", manager.notify_user_kicked.await_args.args[0])
        manager.detach_user_from_room.assert_awaited_once_with(
            "2", "room-1", reason="kicked"
        )


class BackgroundApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_backgrounds_endpoint(self):
        items = [{"id": "bg1", "url": "/bg.png", "width": 1920, "height": 1080}]
        with mock.patch.object(
            backgrounds_service, "load_backgrounds", return_value=items
        ):
            response = await v1_rooms.list_backgrounds(DummyRequest(), self.user)
        self.assertEqual(items, response["data"]["items"])


class RegisterApiTests(ApiTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_register_returns_same_shape_as_login(self):
        payload = SimpleNamespace(username="bobby1", password="Pa55w0rd!")
        with mock.patch.object(
            v1_auth.UserService,
            "create_user",
            new=mock.AsyncMock(return_value={"success": True, "user": {"id": 7, "username": "bobby1", "nickname": "bobby1"}}),
        ) as create_user:
            response = await v1_auth.register(DummyRequest(), payload)

        create_user.assert_awaited_once_with("bobby1", "Pa55w0rd!")
        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertEqual("Bearer", data["token_type"])
        self.assertEqual(7 * 24 * 3600, data["expires_in"])
        self.assertEqual("7", tokens.decode_access_token(data["access_token"]))
        self.assertEqual(
            {"id": "7", "username": "bobby1", "nickname": "bobby1"},
            data["user"],
        )

    async def test_register_validation(self):
        cases = [
            (SimpleNamespace(username="ab", password="Pa55w0rd!"), 400),  # 太短
            (SimpleNamespace(username="bad name", password="Pa55w0rd!"), 400),  # 特殊字符
            (SimpleNamespace(username="123456", password="Pa55w0rd!"), 400),  # 纯数字
            (SimpleNamespace(username="bobby1", password="123"), 400),  # 密码太短
        ]
        for payload, status in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(v1_common.APIError) as ctx:
                    await v1_auth.register(DummyRequest(), payload)
                self.assertEqual(status, ctx.exception.status_code)

    async def test_register_duplicate_returns_409(self):
        payload = SimpleNamespace(username="bobby1", password="Pa55w0rd!")
        with mock.patch.object(
            v1_auth.UserService,
            "create_user",
            new=mock.AsyncMock(return_value={"success": False, "message": "创建用户失败"}),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_auth.register(DummyRequest(), payload)
        self.assertEqual(409, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_CONFLICT, ctx.exception.code)


class RouterRegistrationTests(unittest.TestCase):
    def test_v1_room_routes_registered(self):
        paths = {path for _method, path in v1_rooms.router.full_paths()}
        for path in (
            "/api/v1/backgrounds",
            "/api/v1/rooms",
            "/api/v1/rooms/{room_id_or_code}",
            "/api/v1/rooms/{room_id_or_code}/join",
            "/api/v1/rooms/{room_id_or_code}/leave",
            "/api/v1/rooms/{room_id_or_code}/kick",
        ):
            self.assertIn(path, paths)

    def test_create_app_registers_v1_rooms_router(self):
        source = (REPO_ROOT / "api" / "init.py").read_text(encoding="utf-8")
        self.assertIn("v1_rooms_router", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
