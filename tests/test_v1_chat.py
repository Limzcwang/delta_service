"""C：HTTP 历史消息接口测试（离线，mock service）。"""

import json
import unittest
from datetime import datetime
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

from api import v1_common, v1_rooms  # noqa: E402
from service.messages import ERR_INVALID_PAGINATION, MessageError  # noqa: E402
from service.rooms import ERR_NOT_ROOM_MEMBER, RoomError  # noqa: E402


class DummyRequest:
    def __init__(self, request_id="req_chat"):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id=request_id)
        self.url = SimpleNamespace(path="/api/v1/rooms/A1B2C/messages")
        self.method = "GET"


def make_user():
    return SimpleNamespace(id=1, username="alice")


def make_room():
    return SimpleNamespace(id="room-1", code="A1B2C")


class ChatHistoryApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = make_user()
        self.room = make_room()
        self.membership = SimpleNamespace(joined_at=datetime(2026, 1, 1, 0, 0, 0))

    async def test_history_success_envelope(self):
        page = {
            "items": [
                {
                    "id": "m1",
                    "room_id": "room-1",
                    "user": {"id": "1", "username": "alice"},
                    "content": "hi",
                    "client_msg_id": "c1",
                    "created_at": 1792400000000,
                }
            ],
            "has_more": True,
            "next_before": "m1",
        }
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(return_value=(self.room, self.membership)),
        ) as get_room, mock.patch.object(
            v1_rooms.messages_service,
            "list_messages",
            new=mock.AsyncMock(return_value=page),
        ) as list_messages:
            response = await v1_rooms.list_room_messages(
                DummyRequest(), "A1B2C", before="m2", limit=20, user=self.user
            )

        get_room.assert_awaited_once_with("A1B2C", self.user)
        list_messages.assert_awaited_once_with(
            self.room,
            before="m2",
            limit=20,
            joined_at=self.membership.joined_at,
        )
        self.assertEqual(200, response["code"])
        self.assertEqual("req_chat", response["request_id"])
        self.assertEqual(page, response["data"])

    async def test_history_default_limit_is_50(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(return_value=(self.room, self.membership)),
        ), mock.patch.object(
            v1_rooms.messages_service,
            "list_messages",
            new=mock.AsyncMock(return_value={"items": [], "has_more": False, "next_before": None}),
        ) as list_messages:
            await v1_rooms.list_room_messages(
                DummyRequest(), "A1B2C", user=self.user
            )
        self.assertEqual(50, list_messages.call_args.kwargs["limit"])

    async def test_non_member_returns_not_room_member(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.list_room_messages(
                    DummyRequest(), "A1B2C", user=self.user
                )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_FORBIDDEN, ctx.exception.code)
        self.assertEqual(ERR_NOT_ROOM_MEMBER, ctx.exception.error_code)

    async def test_invalid_pagination_maps_to_400(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(return_value=(self.room, self.membership)),
        ), mock.patch.object(
            v1_rooms.messages_service,
            "list_messages",
            new=mock.AsyncMock(
                side_effect=MessageError(ERR_INVALID_PAGINATION, "before 游标无效")
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.list_room_messages(
                    DummyRequest(), "A1B2C", before="missing", user=self.user
                )
        self.assertEqual(400, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_BAD_REQUEST, ctx.exception.code)
        self.assertEqual(ERR_INVALID_PAGINATION, ctx.exception.error_code)

    async def test_room_not_found_maps_to_404(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(
                side_effect=RoomError(
                    v1_rooms.rooms_service.ERR_ROOM_NOT_FOUND, "房间不存在", 404
                )
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.list_room_messages(
                    DummyRequest(), "ZZZZZ", user=self.user
                )
        self.assertEqual(404, ctx.exception.status_code)
        self.assertEqual(
            v1_rooms.rooms_service.ERR_ROOM_NOT_FOUND, ctx.exception.error_code
        )

    async def test_error_body_contains_error_code(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_membership",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.list_room_messages(
                    DummyRequest(), "A1B2C", user=self.user
                )
        response = await v1_common.api_error_handler(DummyRequest(), ctx.exception)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(
            {"error_code": ERR_NOT_ROOM_MEMBER}, body["data"]
        )


class ChatRouteTests(unittest.TestCase):
    def test_history_route_registered(self):
        paths = [path for _method, path in v1_rooms.router.full_paths()]
        self.assertIn("/api/v1/rooms/{room_id_or_code}/messages", paths)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
