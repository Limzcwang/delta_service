"""D：HTTP 画板快照接口测试（离线，mock service）。"""

import json
import unittest
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
from service.rooms import ERR_NOT_ROOM_MEMBER, RoomError  # noqa: E402


class DummyRequest:
    def __init__(self):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id="req_canvas")
        self.url = SimpleNamespace(path="/api/v1/rooms/A1B2C/canvas")
        self.method = "GET"


def make_user():
    return SimpleNamespace(id=1, username="alice")


def make_room():
    return SimpleNamespace(id="room-1", code="A1B2C")


class CanvasApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = make_user()
        self.room = make_room()

    async def test_get_snapshot_success(self):
        snapshot = {
            "strokes": [{"kind": "clear"}],
            "rev": 9,
            "updated_at": 1792400000000,
        }
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(self.room, [])),
        ) as get_room, mock.patch.object(
            v1_rooms.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(return_value=snapshot),
        ) as get_snapshot:
            response = await v1_rooms.get_room_canvas(
                DummyRequest(), "A1B2C", user=self.user
            )

        get_room.assert_awaited_once_with("A1B2C", self.user)
        get_snapshot.assert_awaited_once_with(self.room.id)
        self.assertEqual(200, response["code"])
        self.assertEqual("req_canvas", response["request_id"])
        self.assertEqual(snapshot, response["data"])

    async def test_get_snapshot_empty_is_ok(self):
        empty = {"strokes": [], "rev": 0, "updated_at": None}
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(self.room, [])),
        ), mock.patch.object(
            v1_rooms.canvas_service,
            "get_snapshot",
            new=mock.AsyncMock(return_value=empty),
        ):
            response = await v1_rooms.get_room_canvas(
                DummyRequest(), "room-1", user=self.user
            )
        self.assertEqual(empty, response["data"])

    async def test_non_member_forbidden(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.get_room_canvas(
                    DummyRequest(), "A1B2C", user=self.user
                )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_FORBIDDEN, ctx.exception.code)
        self.assertEqual(ERR_NOT_ROOM_MEMBER, ctx.exception.error_code)

    async def test_error_body_has_error_code(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.get_room_canvas(
                    DummyRequest(), "A1B2C", user=self.user
                )
        response = await v1_common.api_error_handler(DummyRequest(), ctx.exception)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual({"error_code": ERR_NOT_ROOM_MEMBER}, body["data"])


class CanvasRouteTests(unittest.TestCase):
    def test_canvas_route_registered(self):
        paths = [path for _method, path in v1_rooms.router.full_paths()]
        self.assertIn("/api/v1/rooms/{room_id_or_code}/canvas", paths)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
