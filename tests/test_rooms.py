"""B2a：房间服务层 / 底图 / DAO 结构测试（离线，fake DAO）。"""

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import install_tortoise_stub

install_tortoise_stub()

from dao import room as room_dao_module  # noqa: E402
from security.passwords import hash_password, is_hashed, verify_password  # noqa: E402
from service import backgrounds as backgrounds_service  # noqa: E402
from service import rooms  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_user(user_id=1, username="alice", nickname=None):
    return SimpleNamespace(
        id=user_id, username=username, nickname=nickname if nickname is not None else username
    )


def make_room(room_id="room-1", code="A1B2C", **overrides):
    values = {
        "id": room_id,
        "code": code,
        "name": "开黑房",
        "visibility": "public",
        "password_hash": None,
        "owner_id": 1,
        "background_id": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "closed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeRoomDao:
    def __init__(self):
        self.rooms_by_id = {}
        self.rooms_by_code = {}
        self.active_codes = set()
        self.public_rooms = []
        self.created = []
        self.updated = []
        self.closed = []
        self.before_arg = None

    async def create(self, **kwargs):
        self.created.append(kwargs)
        owner = kwargs.get("owner")
        room = make_room(
            room_id=kwargs["id"],
            code=kwargs["code"],
            name=kwargs["name"],
            visibility=kwargs["visibility"],
            password_hash=kwargs.get("password_hash"),
            owner_id=getattr(owner, "id", None),
            background_id=kwargs.get("background_id"),
        )
        self.rooms_by_id[room.id] = room
        self.rooms_by_code[room.code] = room
        self.active_codes.add(room.code)
        return room

    async def get_by_id(self, room_id):
        return self.rooms_by_id.get(room_id)

    async def get_by_code(self, code):
        return self.rooms_by_code.get(code)

    async def code_exists(self, code):
        return code in self.active_codes

    async def list_public(self, limit, before=None):
        self.before_arg = before
        return list(self.public_rooms)[: limit + 1]

    async def update(self, room, **fields):
        self.updated.append((room.id, fields))
        for key, value in fields.items():
            setattr(room, key, value)
        return room

    async def close(self, room, closed_at=None):
        self.closed.append(room.id)
        room.closed_at = closed_at or datetime.now()


class FakeMemberDao:
    def __init__(self):
        self.members = {}  # (room_id, user_id) -> member
        self.added = []
        self.removed = []

    async def add(self, room, user, role="member"):
        self.added.append((room.id, user.id, role))
        member = SimpleNamespace(user=user, role=role)
        self.members[(room.id, user.id)] = member
        return member

    async def get(self, room_id, user_id):
        return self.members.get((room_id, user_id))

    async def list_for_room(self, room_id):
        return [
            member
            for (rid, _uid), member in self.members.items()
            if rid == room_id
        ]

    async def count(self, room_id):
        return sum(1 for rid, _uid in self.members if rid == room_id)

    async def remove(self, room_id, user_id):
        self.removed.append((room_id, user_id))
        return 1 if self.members.pop((room_id, user_id), None) is not None else 0


class RoomServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.room_dao = FakeRoomDao()
        self.member_dao = FakeMemberDao()
        self.patches = [
            mock.patch.object(rooms, "RoomDao", self.room_dao),
            mock.patch.object(rooms, "RoomMemberDao", self.member_dao),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def seed_room(self, **overrides):
        room = make_room(**overrides)
        self.room_dao.rooms_by_id[room.id] = room
        self.room_dao.rooms_by_code[room.code] = room
        if room.closed_at is None:
            self.room_dao.active_codes.add(room.code)
        return room


class RoomCodeTests(unittest.TestCase):
    def test_generated_code_matches_five_char_format(self):
        for _ in range(20):
            code = rooms.generate_room_code()
            self.assertRegex(code, r"^[A-Z0-9]{5}$")

    def test_normalize_room_code(self):
        self.assertEqual("A1B2C", rooms.normalize_room_code(" a1b2c "))


class CreateRoomTests(RoomServiceTestCase):
    def test_create_public_room_and_owner_membership(self):
        owner = make_user()
        room = self._run(rooms.create_room(owner, "  开黑房  ", "public"))
        self.assertEqual("开黑房", room.name)
        self.assertEqual(5, len(room.code))
        self.assertIsNone(room.password_hash)
        self.assertEqual((room.id, owner.id, "owner"), self.member_dao.added[0])
        self.assertEqual(owner.id, room.owner_id)

    def test_create_private_room_hashes_password(self):
        owner = make_user()
        room = self._run(
            rooms.create_room(owner, "私房", "private", password="room-pass")
        )
        self.assertTrue(room.password_hash)
        self.assertTrue(is_hashed(room.password_hash))
        self.assertTrue(verify_password("room-pass", room.password_hash))
        self.assertNotIn("room-pass", room.password_hash)

    def test_private_room_requires_password(self):
        with self.assertRaises(rooms.RoomError) as ctx:
            self._run(rooms.create_room(make_user(), "私房", "private"))
        self.assertEqual(rooms.ERR_ROOM_PASSWORD_REQUIRED, ctx.exception.error_code)

    def test_public_room_ignores_password(self):
        room = self._run(
            rooms.create_room(make_user(), "公共", "public", password="ignored")
        )
        self.assertIsNone(room.password_hash)

    def test_invalid_name_visibility_and_background(self):
        cases = [
            (dict(name="", visibility="public"), rooms.ERR_ROOM_NAME_INVALID),
            (dict(name="x" * 33, visibility="public"), rooms.ERR_ROOM_NAME_INVALID),
            (dict(name="房", visibility="other"), rooms.ERR_INVALID_VISIBILITY),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(rooms.RoomError) as ctx:
                    self._run(rooms.create_room(make_user(), **kwargs))
                self.assertEqual(expected, ctx.exception.error_code)

        with mock.patch.object(
            rooms.backgrounds_service, "find_background", return_value=None
        ):
            with self.assertRaises(rooms.RoomError) as ctx:
                self._run(
                    rooms.create_room(
                        make_user(), "房", "public", background_id="missing"
                    )
                )
        self.assertEqual(rooms.ERR_INVALID_BACKGROUND, ctx.exception.error_code)

    def test_room_code_retries_on_collision(self):
        owner = make_user()
        self.room_dao.active_codes.add("AAAAA")
        room = self._run(
            rooms.create_room(owner, "房", "public"),
            code_sequence=["AAAAA", "BBBBB"],
        )
        self.assertEqual("BBBBB", room.code)

    def test_room_code_generation_failure(self):
        self.room_dao.active_codes.add("AAAAA")
        with mock.patch.object(
            rooms, "generate_room_code", return_value="AAAAA"
        ):
            with self.assertRaises(rooms.RoomError) as ctx:
                self._run(rooms.create_room(make_user(), "房", "public"))
        self.assertEqual(
            rooms.ERR_ROOM_CODE_GENERATION_FAILED, ctx.exception.error_code
        )

    def _run(self, coro, code_sequence=None):
        if code_sequence is None:
            return self._await(coro)
        with mock.patch.object(
            rooms, "generate_room_code", side_effect=code_sequence
        ):
            return self._await(coro)

    @staticmethod
    def _await(coro):
        import asyncio

        return asyncio.run(coro)


class ResolveRoomTests(RoomServiceTestCase):
    def test_resolve_by_code_and_id(self):
        room = self.seed_room()
        self.assertIs(room, self._await(rooms.resolve_room("a1b2c")))
        self.assertIs(room, self._await(rooms.resolve_room("room-1")))

    def test_unknown_room_raises(self):
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.resolve_room("ZZZZZ"))
        self.assertEqual(rooms.ERR_ROOM_NOT_FOUND, ctx.exception.error_code)

    @staticmethod
    def _await(coro):
        import asyncio

        return asyncio.run(coro)


class ListPublicRoomsTests(RoomServiceTestCase):
    def test_pagination_and_member_counts(self):
        rooms_list = [
            self.seed_room(room_id=f"r{i}", code=f"A1B2{i}", name=f"房{i}")
            for i in range(3)
        ]
        for index, room in enumerate(rooms_list):
            room.created_at = BASE_TIME - timedelta(minutes=index)
            self.member_dao.members[(room.id, 1)] = SimpleNamespace(
                user=make_user(), role="owner"
            )
        self.room_dao.public_rooms = rooms_list

        items, has_more, next_before = self._await(
            rooms.list_public_rooms(limit=2)
        )
        self.assertTrue(has_more)
        self.assertEqual(2, len(items))
        self.assertEqual(int(rooms_list[1].created_at.timestamp() * 1000), next_before)
        self.assertEqual(1, items[0][1])

    def test_filters_closed_and_private_defensively(self):
        public = self.seed_room(room_id="r1", code="AAAAA")
        closed = self.seed_room(room_id="r2", code="BBBBB", closed_at=BASE_TIME)
        private = self.seed_room(room_id="r3", code="CCCCC", visibility="private")
        self.room_dao.public_rooms = [public, closed, private]
        items, has_more, next_before = self._await(rooms.list_public_rooms())
        self.assertEqual([("r1", 0)], [(room.id, count) for room, count in items])
        self.assertFalse(has_more)
        self.assertIsNone(next_before)

    def test_invalid_limit(self):
        for limit in (0, 101, "abc"):
            with self.subTest(limit=limit):
                with self.assertRaises(rooms.RoomError) as ctx:
                    self._await(rooms.list_public_rooms(limit=limit))
                self.assertEqual("INVALID_PAGINATION", ctx.exception.error_code)

    def test_before_cursor_is_converted_to_datetime(self):
        self.room_dao.public_rooms = []
        before_ms = int(BASE_TIME.timestamp() * 1000)
        self._await(rooms.list_public_rooms(before=before_ms))
        self.assertIsInstance(self.room_dao.before_arg, datetime)

    @staticmethod
    def _await(coro):
        import asyncio

        return asyncio.run(coro)


class JoinLeaveTests(RoomServiceTestCase):
    def test_join_public_room(self):
        room = self.seed_room()
        user = make_user(2)
        joined, members = self._await(rooms.join_room(user, "A1B2C"))
        self.assertIs(room, joined)
        self.assertEqual([(room.id, 2, "member")], self.member_dao.added)
        self.assertEqual(1, len(members))

    def test_join_is_idempotent(self):
        room = self.seed_room()
        user = make_user(2)
        self.member_dao.members[(room.id, 2)] = SimpleNamespace(
            user=user, role="member"
        )
        _, members = self._await(rooms.join_room(user, room.id))
        self.assertEqual([], self.member_dao.added)
        self.assertEqual(1, len(members))

    def test_private_room_password_rules(self):
        room = self.seed_room(
            visibility="private", password_hash=hash_password("room-pass")
        )
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.join_room(make_user(2), room.id))
        self.assertEqual(rooms.ERR_ROOM_PASSWORD_REQUIRED, ctx.exception.error_code)

        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.join_room(make_user(2), room.id, password="wrong"))
        self.assertEqual(rooms.ERR_ROOM_PASSWORD_INVALID, ctx.exception.error_code)

        self._await(rooms.join_room(make_user(2), room.id, password="room-pass"))
        self.assertIn((room.id, 2), self.member_dao.members)

    def test_room_full(self):
        room = self.seed_room()
        for uid in range(1, 9):
            self.member_dao.members[(room.id, uid)] = SimpleNamespace(
                user=make_user(uid), role="member"
            )
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.join_room(make_user(99), room.id))
        self.assertEqual(rooms.ERR_ROOM_FULL, ctx.exception.error_code)

    def test_closed_room_cannot_be_joined(self):
        room = self.seed_room(closed_at=BASE_TIME)
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.join_room(make_user(2), room.id))
        self.assertEqual(rooms.ERR_ROOM_CLOSED, ctx.exception.error_code)

    def test_owner_cannot_leave(self):
        room = self.seed_room(owner_id=1)
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.leave_room(make_user(1), room.id))
        self.assertEqual(rooms.ERR_ROOM_OWNER_MUST_CLOSE, ctx.exception.error_code)

    def test_non_member_cannot_leave(self):
        room = self.seed_room()
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.leave_room(make_user(2), room.id))
        self.assertEqual(rooms.ERR_NOT_ROOM_MEMBER, ctx.exception.error_code)

    def test_member_can_leave(self):
        room = self.seed_room()
        user = make_user(2)
        self.member_dao.members[(room.id, 2)] = SimpleNamespace(
            user=user, role="member"
        )
        self.assertTrue(self._await(rooms.leave_room(user, room.id)))
        self.assertNotIn((room.id, 2), self.member_dao.members)

    @staticmethod
    def _await(coro):
        import asyncio

        return asyncio.run(coro)


class KickCloseUpdateTests(RoomServiceTestCase):
    def test_kick_requires_owner_and_existing_member(self):
        room = self.seed_room(owner_id=1)
        self.member_dao.members[(room.id, 2)] = SimpleNamespace(
            user=make_user(2), role="member"
        )
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.kick_member(make_user(2), room.id, "3"))
        self.assertEqual(rooms.ERR_NOT_ROOM_OWNER, ctx.exception.error_code)

        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.kick_member(make_user(1), room.id, "1"))
        self.assertEqual(rooms.ERR_CANNOT_KICK_OWNER, ctx.exception.error_code)

        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.kick_member(make_user(1), room.id, "99"))
        self.assertEqual(rooms.ERR_MEMBER_NOT_FOUND, ctx.exception.error_code)

        members = self._await(rooms.kick_member(make_user(1), room.id, "2"))
        self.assertEqual([], members)
        self.assertNotIn((room.id, 2), self.member_dao.members)

    def test_close_room_owner_only_and_idempotent(self):
        room = self.seed_room(owner_id=1)
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(rooms.close_room(make_user(2), room.id))
        self.assertEqual(rooms.ERR_NOT_ROOM_OWNER, ctx.exception.error_code)

        closed = self._await(rooms.close_room(make_user(1), room.id))
        self.assertIsNotNone(closed.closed_at)
        self.assertEqual([room.id], self.room_dao.closed)
        # 幂等：再次关闭不重复写
        self._await(rooms.close_room(make_user(1), room.id))
        self.assertEqual([room.id], self.room_dao.closed)

    def test_update_room_fields_and_permissions(self):
        room = self.seed_room(
            owner_id=1, visibility="private", password_hash=hash_password("old")
        )
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(
                rooms.update_room(make_user(2), room.id, {"name": "新名"})
            )
        self.assertEqual(rooms.ERR_ROOM_FORBIDDEN, ctx.exception.error_code)

        updated = self._await(
            rooms.update_room(
                make_user(1),
                room.id,
                {"name": " 新名 "},
            )
        )
        self.assertEqual("新名", updated.name)

        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(
                rooms.update_room(make_user(1), room.id, {"name": "x" * 31})
            )
        self.assertEqual(rooms.ERR_ROOM_NAME_INVALID, ctx.exception.error_code)

    def test_update_password_set_and_clear(self):
        room = self.seed_room(owner_id=1, visibility="private")
        updated = self._await(
            rooms.update_room(make_user(1), room.id, {"password": "new-pass"})
        )
        self.assertTrue(is_hashed(updated.password_hash))
        self.assertTrue(verify_password("new-pass", updated.password_hash))

        cleared = self._await(
            rooms.update_room(make_user(1), room.id, {"password": None})
        )
        self.assertIsNone(cleared.password_hash)

    def test_public_room_cannot_set_password(self):
        room = self.seed_room(owner_id=1, visibility="public")
        with self.assertRaises(rooms.RoomError) as ctx:
            self._await(
                rooms.update_room(make_user(1), room.id, {"password": "new-pass"})
            )
        self.assertEqual(
            rooms.ERR_INVALID_ROOM_PAYLOAD, ctx.exception.error_code
        )

    @staticmethod
    def _await(coro):
        import asyncio

        return asyncio.run(coro)


class SerializationTests(unittest.TestCase):
    def test_room_to_dict_never_includes_password_hash(self):
        room = make_room(password_hash=hash_password("secret"))
        data = rooms.room_to_dict(room, member_count=3)
        self.assertNotIn("password_hash", data)
        self.assertTrue(data["has_password"])
        self.assertEqual(3, data["member_count"])
        self.assertEqual(str(room.id), data["id"])
        self.assertEqual(int(room.created_at.timestamp() * 1000), data["created_at"])

    def test_room_to_dict_with_background(self):
        room = make_room(background_id="bg1")
        with mock.patch.object(
            rooms.backgrounds_service,
            "find_background",
            return_value={"id": "bg1", "url": "/bg.png", "width": 1920, "height": 1080},
        ):
            data = rooms.room_to_dict(room, 1)
        self.assertEqual("bg1", data["background_id"])
        self.assertEqual("/bg.png", data["background"]["url"])


class BackgroundManifestTests(unittest.TestCase):
    def test_missing_manifest_returns_empty(self):
        self.assertEqual([], backgrounds_service.load_backgrounds("/no/such/file.json"))

    def test_valid_manifest_and_invalid_entries(self):
        payload = {
            "backgrounds": [
                {"id": "bg1", "url": "/a.png", "width": 1920, "height": 1080},
                {"id": "bg2", "url": "/b.png", "width": 1920, "height": 1080, "name": "B"},
                {"id": "", "url": "/c.png", "width": 1, "height": 1},
                {"id": "bad", "url": "/d.png", "width": -1, "height": 1},
                "not-a-dict",
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            items = backgrounds_service.load_backgrounds(str(path))
            self.assertEqual(["bg1", "bg2"], [item["id"] for item in items])
            self.assertEqual({"bg1", "bg2"}, backgrounds_service.background_ids(str(path)))
            self.assertEqual("bg2", backgrounds_service.find_background("bg2", str(path))["id"])
            self.assertIsNone(backgrounds_service.find_background("missing", str(path)))

    def test_corrupt_manifest_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual([], backgrounds_service.load_backgrounds(str(path)))


class RoomDaoStructureTests(unittest.TestCase):
    def test_models_and_dao_methods_exist(self):
        self.assertEqual("rooms", room_dao_module.Room.Meta.table)
        self.assertEqual("room_members", room_dao_module.RoomMember.Meta.table)
        for name in ("create", "get_by_id", "get_by_code", "code_exists",
                     "list_public", "update", "close"):
            self.assertTrue(hasattr(room_dao_module.RoomDao, name), name)
        for name in ("add", "get", "list_for_room", "count", "remove"):
            self.assertTrue(hasattr(room_dao_module.RoomMemberDao, name), name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
