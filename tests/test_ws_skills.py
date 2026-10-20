"""E1：WS 技能共享广播/room_joined/off/限频与 HTTP GET 测试。"""

import json
import unittest
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

from api import deps, v1_common, v1_rooms  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from service.messages import MessageRateLimiter  # noqa: E402
from service.rooms import ERR_NOT_ROOM_MEMBER, RoomError  # noqa: E402
from tests.ws_support import FakeWebSocket, make_room, make_session, make_user, manager_module  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def skill_payload(room_id="room-1", **overrides):
    payload = {
        "type": "skill_share_update",
        "room_id": room_id,
        "operator_id": "op-1",
        "operator_name": "干员",
        "skills": [
            {"slot": 0, "state": "charging", "remaining_ms": 12000, "charges": 0, "max_charges": 1, "pct": 30},
            {"slot": 1, "state": "ready", "remaining_ms": 0, "charges": 2, "max_charges": 2},
            {"slot": 2, "state": "unknown", "remaining_ms": 0, "charges": 1, "max_charges": 1},
        ],
        "captured_at": 1792400000000,
    }
    payload.update(overrides)
    return payload


def state_payload(user_id="1", nickname="Alice"):
    skills = []
    for item in skill_payload()["skills"]:
        normalized = dict(item)
        normalized.setdefault("ready_at", 1792400004500)
        skills.append(normalized)
    return {
        "user_id": user_id,
        "nickname": nickname,
        "operator_id": "op-1",
        "operator_name": "干员",
        "skills": skills,
        "captured_at": 1792400000000,
        "server_received_at": 1792400000002,
        "squad_number": 1,
        "updated_at": 1792400000001,
    }


class WsSkillTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        log_context = capture_logger(service_logger)
        log_context.__enter__()
        self.addCleanup(log_context.__exit__, None, None, None)
        self.clock = FakeClock()
        self.manager = manager_module.WebSocketManager(
            skill_rate_limiter=MessageRateLimiter(
                max_messages=2, window_seconds=1.0, clock=self.clock
            )
        )
        self.room = make_room()
        self.alice = make_user(1, "alice", "Alice")
        self.alice_ws = FakeWebSocket()
        self.alice_session = make_session(
            self.manager, self.alice, self.alice_ws, room=self.room
        )
        self.bob = make_user(2, "bob", "Bob")
        self.bob_ws = FakeWebSocket()
        self.bob_session = make_session(
            self.manager, self.bob, self.bob_ws, room=self.room
        )
        for target, value in (
            ("resolve_room", mock.AsyncMock(return_value=self.room)),
            ("ensure_member", mock.AsyncMock(return_value=None)),
        ):
            patcher = mock.patch.object(
                manager_module.rooms_service, target, new=value
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _send(self, session, payload):
        await self.manager.process_message(session, json.dumps(payload))


class SkillUpdateTests(WsSkillTestCase):
    async def test_update_broadcasts_to_all_including_sender(self):
        with mock.patch.object(
            manager_module.skills_service,
            "save_state",
            new=mock.AsyncMock(return_value=state_payload()),
        ) as save_state:
            await self._send(self.alice_session, skill_payload())

        save_state.assert_awaited_once()
        for websocket in (self.alice_ws, self.bob_ws):
            event = websocket.messages("skill_share_state")[0]
            self.assertEqual("skill_share_state", event["type"])
            self.assertEqual("1", event["user_id"])
            self.assertEqual("Alice", event["nickname"])
            self.assertEqual("op-1", event["operator_id"])
            self.assertEqual(3, len(event["skills"]))
            self.assertEqual(1792400000000, event["captured_at"])
            self.assertEqual(1792400000002, event["server_received_at"])
            self.assertEqual(1, event["squad_number"])
            self.assertEqual(1792400000001, event["updated_at"])
            self.assertEqual(1792400004500, event["skills"][0]["ready_at"])

    async def test_invalid_payload_rejected_without_save_or_broadcast(self):
        for payload in (
            skill_payload(skills=[{"slot": 0, "state": "ready"}] * 2),
            skill_payload(room_id="other"),
            skill_payload(captured_at=-1),
        ):
            with self.subTest(payload=str(payload)[:50]):
                await self._send(self.alice_session, payload)
                self.assertIn(
                    self.alice_ws.last()["code"],
                    {"INVALID_SKILL_PAYLOAD", "NOT_ROOM_MEMBER"},
                )
        self.assertEqual([], self.bob_ws.messages("skill_share_state"))

    async def test_invalid_squad_number_ignored_payload_still_succeeds(self):
        with mock.patch.object(
            manager_module.skills_service,
            "save_state",
            new=mock.AsyncMock(return_value=state_payload()),
        ) as save_state:
            await self._send(
                self.alice_session, skill_payload(squad_number=9)
            )
        save_state.assert_awaited_once()
        self.assertEqual([], self.alice_ws.messages("error"))
        self.assertTrue(self.bob_ws.messages("skill_share_state"))

    async def test_rate_limit_two_per_second(self):
        with mock.patch.object(
            manager_module.skills_service,
            "save_state",
            new=mock.AsyncMock(return_value=state_payload()),
        ) as save_state:
            await self._send(self.alice_session, skill_payload())
            await self._send(self.alice_session, skill_payload())
            await self._send(self.alice_session, skill_payload())

        self.assertEqual(2, save_state.await_count)
        self.assertEqual("RATE_LIMITED", self.alice_ws.last()["code"])
        self.assertEqual(2, len(self.bob_ws.messages("skill_share_state")))

    async def test_off_removes_and_broadcasts_removed(self):
        with mock.patch.object(
            manager_module.skills_service,
            "remove_state",
            new=mock.AsyncMock(return_value=1),
        ) as remove_state:
            await self._send(self.alice_session, {"type": "skill_share_off"})

        remove_state.assert_awaited_once_with(self.room.id, 1)
        for websocket in (self.alice_ws, self.bob_ws):
            event = websocket.messages("skill_share_removed")[0]
            self.assertEqual({"type": "skill_share_removed", "user_id": "1"}, event)

    async def test_detach_and_leave_remove_skill_state(self):
        with mock.patch.object(
            manager_module.skills_service,
            "remove_state",
            new=mock.AsyncMock(return_value=1),
        ) as remove_state:
            await self.manager.detach_user_from_room("2", self.room.id, reason="kicked")
        remove_state.assert_awaited_once_with(self.room.id, 2)

        with mock.patch.object(
            manager_module.skills_service,
            "remove_state",
            new=mock.AsyncMock(return_value=1),
        ) as remove_state:
            await self.manager.process_message(
                self.alice_session, json.dumps({"type": "leave_room"})
            )
        remove_state.assert_awaited_once_with(self.room.id, 1)


class RoomJoinedSkillStatesTests(WsSkillTestCase):
    async def test_room_joined_includes_current_skill_states(self):
        carol = make_user(3, "carol", "Carol")
        carol_ws = FakeWebSocket()
        carol_session = make_session(self.manager, carol, carol_ws)
        states = [state_payload("1", "Alice"), state_payload("2", "Bob")]
        with mock.patch.object(
            manager_module.skills_service,
            "list_states",
            new=mock.AsyncMock(return_value=states),
        ), mock.patch.object(
            manager_module.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(self.room, [])),
        ):
            await self._send(
                carol_session,
                {"type": "join_room", "room_id_or_code": self.room.code},
            )

        event = carol_ws.messages("room_joined")[0]
        self.assertEqual(states, event["skill_states"])


class SkillsApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=1, username="alice", nickname="Alice")
        self.room = make_room()

    async def test_get_skills_success(self):
        states = [state_payload("1", "Alice")]
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(return_value=(self.room, [])),
        ), mock.patch.object(
            v1_rooms.skills_service,
            "list_states",
            new=mock.AsyncMock(return_value=states),
        ):
            response = await v1_rooms.get_room_skills(
                SimpleNamespace(), self.room.code, self.user
            )
        self.assertEqual({"items": states}, response["data"])

    async def test_get_skills_non_member_403(self):
        with mock.patch.object(
            v1_rooms.rooms_service,
            "get_room_for_member",
            new=mock.AsyncMock(
                side_effect=RoomError(ERR_NOT_ROOM_MEMBER, "你不在该房间中", 403)
            ),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_rooms.get_room_skills(
                    SimpleNamespace(), self.room.code, self.user
                )
        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual(ERR_NOT_ROOM_MEMBER, ctx.exception.error_code)

    async def test_get_skills_unauthenticated_401(self):
        request = SimpleNamespace(headers={})
        with self.assertRaises(v1_common.APIError) as ctx:
            await deps.get_current_user(request)
        self.assertEqual(401, ctx.exception.status_code)

    async def test_soft_delete_clears_skill_states(self):
        manager = SimpleNamespace(
            flush_canvas_on_close=mock.AsyncMock(return_value=0),
            detach_room_sessions=mock.AsyncMock(return_value=0),
        )
        deleted_room = make_room()
        deleted_room.deleted = True
        with mock.patch.object(
            v1_rooms.rooms_service,
            "delete_room",
            new=mock.AsyncMock(return_value=deleted_room),
        ), mock.patch.object(
            v1_rooms.skills_service,
            "clear_room",
            new=mock.AsyncMock(return_value=0),
        ) as clear_room, mock.patch.object(
            v1_rooms, "_get_websocket_manager", return_value=manager
        ):
            await v1_rooms.close_room(
                SimpleNamespace(), self.room.code, self.user
            )
        clear_room.assert_awaited_once_with(deleted_room.id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
