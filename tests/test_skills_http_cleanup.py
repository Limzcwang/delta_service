"""E1 修复：目标无 WS 连接时 HTTP leave/kick 也删除技能状态。"""

import unittest
from datetime import datetime
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

from api import v1_rooms  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service import skills as skills_service  # noqa: E402
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module  # noqa: E402

NOW = datetime(2026, 1, 1, 12, 0, 0)


def skill_data(operator_id="op-1"):
    return {
        "operator_id": operator_id,
        "operator_name": "干员",
        "skills": [
            {"slot": 0, "state": "ready", "remaining_ms": 0, "charges": 1, "max_charges": 1},
            {"slot": 1, "state": "ready", "remaining_ms": 0, "charges": 1, "max_charges": 1},
            {"slot": 2, "state": "unknown", "remaining_ms": 0, "charges": 1, "max_charges": 1},
        ],
        "captured_at": 1792400000000,
    }


class FakeSkillDao:
    def __init__(self):
        self.records = {}

    def seed(self, room_id, user):
        record = SimpleNamespace(
            user=user,
            data=skill_data(f"op-{user.id}"),
            updated_at=datetime.now(),
        )
        self.records[(str(room_id), int(user.id))] = record
        return record

    async def list_for_room(self, room_id):
        return [
            record
            for (rid, _uid), record in self.records.items()
            if rid == str(room_id)
        ]

    async def remove(self, room_id, user_id):
        return 1 if self.records.pop((str(room_id), int(user_id)), None) else 0

    async def clear_room(self, room_id):
        keys = [key for key in self.records if key[0] == str(room_id)]
        for key in keys:
            self.records.pop(key, None)
        return len(keys)

    async def upsert(self, room, user, data):
        record = SimpleNamespace(user=user, data=data, updated_at=datetime.now())
        self.records[(str(room.id), int(user.id))] = record
        return record


class DummyRequest:
    def __init__(self):
        self.headers = {"Authorization": "Bearer test"}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(request_id="req_skill_cleanup")
        self.url = SimpleNamespace(path="/api/v1/rooms/A1B2C/skills")
        self.method = "GET"


class HttpCleanupWithoutSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.dao = FakeSkillDao()
        patcher = mock.patch.object(skills_service, "SkillStateDao", self.dao)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = manager_module.WebSocketManager()
        self.room = make_room()
        self.alice = make_user(1, "alice", "Alice")
        self.bob = make_user(2, "bob", "Bob")

    async def _get_skills(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(self.room, [])),
        ):
            return await v1_rooms.get_room_skills(
                DummyRequest(), self.room.code, self.alice
            )

    async def _room_joined_skill_states(self):
        websocket = FakeWebSocket()
        session = manager_module.ClientSession(websocket=websocket)
        await self.manager._send_room_joined(session, self.room, [])
        return websocket.messages("room_joined")[0]["skill_states"]

    async def test_http_leave_without_session_deletes_state(self):
        self.dao.seed(self.room.id, self.bob)
        self.assertIn((self.room.id, 2), self.dao.records)

        with mock.patch.object(
            v1_rooms.rooms_service,
            "leave_room",
            new=mock.AsyncMock(return_value=True),
        ), mock.patch.object(
            v1_rooms.rooms_service,
            "resolve_room",
            new=mock.AsyncMock(return_value=self.room),
        ), mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=self.manager
        ):
            await v1_rooms.leave_room(
                DummyRequest(), self.room.code, self.bob
            )

        self.assertNotIn((self.room.id, 2), self.dao.records)
        self.assertEqual({"items": []}, (await self._get_skills())["data"])
        self.assertEqual([], await self._room_joined_skill_states())

    async def test_http_kick_without_session_deletes_only_target_state(self):
        self.dao.seed(self.room.id, self.alice)
        self.dao.seed(self.room.id, self.bob)

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
            await v1_rooms.kick_member(
                DummyRequest(),
                self.room.code,
                v1_rooms.KickMemberRequest(user_id="2"),
                self.alice,
            )

        self.assertIn((self.room.id, 1), self.dao.records)
        self.assertNotIn((self.room.id, 2), self.dao.records)
        items = (await self._get_skills())["data"]["items"]
        self.assertEqual(["1"], [item["user_id"] for item in items])
        states = await self._room_joined_skill_states()
        self.assertEqual(["1"], [item["user_id"] for item in states])

    async def test_http_kick_with_session_still_detaches_and_cleans(self):
        bob_ws = FakeWebSocket()
        bob_session = make_session(
            self.manager, self.bob, bob_ws, room=self.room
        )
        self.dao.seed(self.room.id, self.bob)

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
            await v1_rooms.kick_member(
                DummyRequest(),
                self.room.code,
                v1_rooms.KickMemberRequest(user_id="2"),
                self.alice,
            )

        self.assertNotIn((self.room.id, 2), self.dao.records)
        self.assertIsNone(bob_session.room_id)
        self.assertTrue(bob_ws.messages("room_kicked"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
