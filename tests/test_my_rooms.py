"""H1：我的房间列表/退出后移除/路由顺序测试。"""

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

from api import deps, v1_common, v1_rooms  # noqa: E402
from service import rooms as rooms_service  # noqa: E402
from service.rooms import ERR_ROOM_OWNER_MUST_CLOSE, RoomError  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


class DummyRequest:
    def __init__(self, headers=None):
        self.headers = headers if headers is not None else {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id="req_mine")
        self.url = SimpleNamespace(path="/api/v1/rooms/mine")
        self.method = "GET"


def make_user(user_id=1):
    return SimpleNamespace(id=user_id, username="alice", nickname="Alice")


def make_room(room_id="room-1", **overrides):
    values = {
        "id": room_id,
        "code": "A1B2C",
        "name": "房间",
        "visibility": "public",
        "owner_id": 1,
        "password_hash": None,
        "background_id": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "closed_at": None,
        "deleted": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_member(room, role="member", joined_at=BASE_TIME):
    return SimpleNamespace(room=room, role=role, joined_at=joined_at)


class FakeMemberDao:
    def __init__(self, memberships):
        self.memberships = memberships
        self.list_args = None
        self.count_value = 2

    async def list_for_user(self, user_id, limit, before_joined_at=None, offset=0):
        self.list_args = (user_id, limit, before_joined_at, offset)
        items = [
            member
            for member in self.memberships
            if before_joined_at is None
            or getattr(member, "joined_at", None) < before_joined_at
        ]
        return list(items[offset : offset + limit + 1])

    async def count(self, room_id):
        return self.count_value


class MyRoomsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_joined_open_undeleted_rooms_including_private(self):
        private = make_room("private", visibility="private", password_hash="hash")
        public = make_room("public")
        closed = make_room("closed", closed_at=BASE_TIME)
        deleted = make_room("deleted", deleted=True)
        dao = FakeMemberDao([
            make_member(private, role="owner", joined_at=BASE_TIME),
            make_member(public, role="member", joined_at=BASE_TIME - timedelta(minutes=1)),
            make_member(closed, joined_at=BASE_TIME - timedelta(minutes=2)),
            make_member(deleted, joined_at=BASE_TIME - timedelta(minutes=3)),
        ])
        with mock.patch.object(rooms_service, "RoomMemberDao", dao):
            items, has_more, next_before = await rooms_service.list_my_rooms(
                make_user(), limit=20
            )

        self.assertEqual(["private", "public"], [item["id"] for item in items])
        self.assertEqual(1, dao.list_args[0])
        self.assertFalse(has_more)
        self.assertIsNone(next_before)
        self.assertEqual("owner", items[0]["role"])
        self.assertEqual(int(BASE_TIME.timestamp() * 1000), items[0]["joined_at"])
        for item in items:
            self.assertNotIn("password_hash", item)

    async def test_pagination_cursor_and_has_more(self):
        memberships = [
            make_member(make_room(f"r{i}"), joined_at=BASE_TIME - timedelta(minutes=i))
            for i in range(3)
        ]
        dao = FakeMemberDao(memberships)
        with mock.patch.object(rooms_service, "RoomMemberDao", dao):
            items, has_more, next_before = await rooms_service.list_my_rooms(
                make_user(), limit=2
            )

        self.assertEqual(["r0", "r1"], [item["id"] for item in items])
        self.assertTrue(has_more)
        self.assertEqual(
            int((BASE_TIME - timedelta(minutes=1)).timestamp() * 1000),
            next_before,
        )
        self.assertEqual((1, 2, None, 0), dao.list_args)

    async def test_invalid_rooms_do_not_break_pagination(self):
        # A(新,有效), deletedB, closedC, D(旧,有效), E(更旧,有效)
        t0 = BASE_TIME
        memberships = [
            make_member(make_room("A"), joined_at=t0),
            make_member(
                make_room("B", deleted=True),
                joined_at=t0 - timedelta(minutes=1),
            ),
            make_member(
                make_room("C", closed_at=t0),
                joined_at=t0 - timedelta(minutes=2),
            ),
            make_member(make_room("D"), joined_at=t0 - timedelta(minutes=3)),
            make_member(make_room("E"), joined_at=t0 - timedelta(minutes=4)),
        ]
        dao = FakeMemberDao(memberships)
        all_items = []
        before = None
        with mock.patch.object(rooms_service, "RoomMemberDao", dao):
            for _page_index in range(10):
                items, has_more, next_before = await rooms_service.list_my_rooms(
                    make_user(), limit=1, before=before
                )
                all_items.extend(items)
                if not has_more:
                    break
                before = next_before

        self.assertEqual(["A", "D", "E"], [item["id"] for item in all_items])
        self.assertEqual(3, len(all_items))  # 无重复
        self.assertFalse(has_more)
        self.assertIsNone(next_before)
        self.assertNotIn("B", [item["id"] for item in all_items])
        self.assertNotIn("C", [item["id"] for item in all_items])

    async def test_before_cursor_and_offset_forwarded(self):
        dao = FakeMemberDao([])
        before = int(BASE_TIME.timestamp() * 1000)
        with mock.patch.object(rooms_service, "RoomMemberDao", dao):
            await rooms_service.list_my_rooms(
                make_user(), limit=20, before=before, offset=40
            )
        self.assertIsInstance(dao.list_args[2], datetime)
        self.assertEqual(40, dao.list_args[3])

    async def test_invalid_page_size(self):
        for limit in (0, 101):
            with self.subTest(limit=limit):
                with self.assertRaises(RoomError) as ctx:
                    await rooms_service.list_my_rooms(make_user(), limit=limit)
                self.assertEqual("INVALID_PAGINATION", ctx.exception.error_code)

    async def test_leave_removes_room_from_my_list(self):
        membership = make_member(make_room("r1"))
        dao = FakeMemberDao([membership])
        with mock.patch.object(rooms_service, "RoomMemberDao", dao):
            items, _, _ = await rooms_service.list_my_rooms(make_user())
            self.assertEqual(1, len(items))

            dao.memberships = []
            items, _, _ = await rooms_service.list_my_rooms(make_user())
            self.assertEqual([], items)

    async def test_owner_cannot_leave(self):
        room = make_room("room-1", owner_id=1)
        with mock.patch.object(
            rooms_service.RoomDao, "get_by_code", new=mock.AsyncMock(return_value=room)
        ), mock.patch.object(
            rooms_service.RoomDao, "get_by_id", new=mock.AsyncMock(return_value=room)
        ):
            with self.assertRaises(RoomError) as ctx:
                await rooms_service.leave_room(make_user(1), room.code)
        self.assertEqual(ERR_ROOM_OWNER_MUST_CLOSE, ctx.exception.error_code)
        self.assertEqual(409, ctx.exception.status_code)


class MyRoomsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_fields_and_pagination_args(self):
        room = make_room("r1")
        item = {
            "id": "r1",
            "code": "A1B2C",
            "name": "房间",
            "visibility": "private",
            "owner_id": "1",
            "member_count": 2,
            "has_password": True,
            "background_id": None,
            "background": None,
            "created_at": 1,
            "updated_at": 1,
            "closed_at": None,
            "joined_at": int(BASE_TIME.timestamp() * 1000),
            "role": "member",
        }
        with mock.patch.object(
            v1_rooms.rooms_service,
            "list_my_rooms",
            new=mock.AsyncMock(return_value=([item], True, 12345)),
        ) as list_my_rooms:
            response = await v1_rooms.list_my_rooms(
                DummyRequest(), page=2, page_size=5, before=None, user=make_user()
            )

        list_my_rooms.assert_awaited_once_with(make_user(), 5, None, 5)
        self.assertEqual(200, response["code"])
        self.assertEqual([item], response["data"]["items"])
        self.assertTrue(response["data"]["has_more"])
        self.assertEqual(12345, response["data"]["next_before"])
        self.assertNotIn("password_hash", response["data"]["items"][0])

    async def test_unauthenticated_401(self):
        with self.assertRaises(v1_common.APIError) as ctx:
            await deps.get_current_user(DummyRequest(headers={}))
        self.assertEqual(401, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_UNAUTHORIZED, ctx.exception.code)

    async def test_route_order_mine_before_dynamic(self):
        paths = [path for _method, path in v1_rooms.router.full_paths()]
        self.assertLess(
            paths.index("/api/v1/rooms/mine"),
            paths.index("/api/v1/rooms/{room_id_or_code}"),
        )
        # 以“先声明先匹配”的方式模拟：/rooms/mine 必须命中静态路由
        template = None
        for method, path in v1_rooms.router.full_paths():
            if method != "GET":
                continue
            if path == "/api/v1/rooms/mine":
                template = path
                break
            if path == "/api/v1/rooms/{room_id_or_code}":
                template = path
                break
        self.assertEqual("/api/v1/rooms/mine", template)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
