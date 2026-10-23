"""E1：技能共享服务（校验/最新状态/stale/清理）测试。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from tests.support import install_tortoise_stub

install_tortoise_stub()

from service import skills as skills_service  # noqa: E402

NOW = datetime(2026, 1, 1, 12, 0, 0)


def make_user(user_id=1, nickname="Alice"):
    return SimpleNamespace(id=user_id, username="alice", nickname=nickname)


def make_payload(**overrides):
    payload = {
        "room_id": "room-1",
        "operator_id": "op-1",
        "operator_name": "干员",
        "skills": [
            {
                "slot": 0,
                "state": "charging",
                "remaining_ms": 12000,
                "charges": 0,
                "max_charges": 1,
                "pct": 30,
            },
            {
                "slot": 1,
                "state": "ready",
                "remaining_ms": 0,
                "charges": 2,
                "max_charges": 2,
            },
            {
                "slot": 2,
                "state": "unknown",
                "remaining_ms": 0,
                "charges": 1,
                "max_charges": 1,
            },
        ],
        "captured_at": 1792400000000,
    }
    payload.update(overrides)
    return payload


def make_record(user=None, data=None, updated_at=NOW, squad_number=None):
    return SimpleNamespace(
        user=user or make_user(),
        data=data if data is not None else skills_service.validate_skill_payload(make_payload()),
        updated_at=updated_at,
        squad_number=squad_number,
    )


class SkillValidationTests(unittest.TestCase):
    def test_valid_payload_normalized(self):
        data = skills_service.validate_skill_payload(make_payload())
        self.assertEqual("op-1", data["operator_id"])
        self.assertEqual(3, len(data["skills"]))
        self.assertEqual(1792400000000, data["captured_at"])
        self.assertEqual(30, data["skills"][0]["pct"])
        self.assertNotIn("pct", data["skills"][1])

    def test_match_state_valid_default_and_invalid(self):
        self.assertEqual(
            "running",
            skills_service.validate_skill_payload(
                make_payload(match_state="running")
            )["match_state"],
        )
        self.assertEqual(
            "idle",
            skills_service.validate_skill_payload(make_payload())["match_state"],
        )
        for value in ("running2", "", None, 1):
            with self.subTest(value=value):
                self.assertEqual(
                    "idle",
                    skills_service.validate_skill_payload(
                        make_payload(match_state=value)
                    )["match_state"],
                )

    def test_squad_number_valid_values(self):
        for value in (1, 2, 3):
            with self.subTest(value=value):
                data = skills_service.validate_skill_payload(
                    make_payload(squad_number=value)
                )
                self.assertEqual(value, data["squad_number"])

    def test_squad_number_invalid_values_ignored(self):
        for value in (0, 4, 9, "2", 2.5, True, -1):
            with self.subTest(value=value):
                data = skills_service.validate_skill_payload(
                    make_payload(squad_number=value)
                )
                self.assertIsNone(data["squad_number"])

    def test_squad_number_none_or_omitted(self):
        payload = make_payload()
        payload.pop("squad_number", None)
        self.assertIsNone(skills_service.validate_skill_payload(payload)["squad_number"])
        self.assertIsNone(
            skills_service.validate_skill_payload(
                make_payload(squad_number=None)
            )["squad_number"]
        )

    def test_missing_operator_and_null_remaining_accepted(self):
        payload = make_payload()
        payload.pop("operator_id")
        payload.pop("operator_name")
        payload["skills"][0]["remaining_ms"] = None
        payload["skills"][1]["state"] = "unknown"
        payload["skills"][1]["remaining_ms"] = None
        data = skills_service.validate_skill_payload(payload)
        self.assertEqual("", data["operator_id"])
        self.assertEqual("", data["operator_name"])
        self.assertEqual(0, data["skills"][0]["remaining_ms"])
        self.assertEqual(0, data["skills"][1]["remaining_ms"])

    def test_match_state_only_unknown_slots_accepted(self):
        payload = {
            "operator_id": "",
            "operator_name": "",
            "match_state": "running",
            "skills": [
                {"slot": 0, "state": "unknown", "remaining_ms": None, "charges": 0, "max_charges": 1},
                {"slot": 1, "state": "unknown", "remaining_ms": None, "charges": 0, "max_charges": 2},
                {"slot": 2, "state": "unknown", "remaining_ms": None, "charges": 0, "max_charges": 1},
            ],
            "captured_at": 1792400000000,
        }
        data = skills_service.validate_skill_payload(payload)
        self.assertEqual("running", data["match_state"])
        self.assertEqual([0, 0, 0], [item["remaining_ms"] for item in data["skills"]])

    def test_operator_non_string_still_rejected(self):
        for field in ("operator_id", "operator_name"):
            payload = make_payload()
            payload[field] = 123
            with self.subTest(field=field):
                with self.assertRaises(skills_service.SkillError):
                    skills_service.validate_skill_payload(payload)

    def test_invalid_payloads(self):
        base = make_payload()
        bad_cases = []

        def variant(**changes):
            payload = make_payload()
            payload.update(changes)
            return payload

        bad_cases.extend([
            None,
            [],
            variant(skills=base["skills"][:2]),
            variant(skills=base["skills"] + [dict(base["skills"][0])]),
            variant(operator_id="x" * 33),
            variant(operator_name="x" * 33),
            variant(captured_at=-1),
            variant(captured_at="1"),
        ])
        skills = [dict(item) for item in base["skills"]]
        skills[0]["slot"] = 3
        bad_cases.append(variant(skills=skills))
        skills = [dict(item) for item in base["skills"]]
        skills[1]["slot"] = 0
        bad_cases.append(variant(skills=skills))
        for field, value in (
            ("state", "broken"),
            ("remaining_ms", -1),
            ("remaining_ms", 600001),
            ("charges", 3),
            ("max_charges", 0),
            ("max_charges", 10),
            ("pct", 101),
            ("pct", True),
        ):
            skills = [dict(item) for item in base["skills"]]
            skills[0][field] = value
            bad_cases.append(variant(skills=skills))
        for payload in bad_cases:
            with self.subTest(payload=str(payload)[:80]):
                with self.assertRaises(skills_service.SkillError) as ctx:
                    skills_service.validate_skill_payload(payload)
                self.assertEqual(
                    skills_service.ERR_INVALID_SKILL_PAYLOAD,
                    ctx.exception.error_code,
                )


class FakeSkillDao:
    def __init__(self):
        self.upserts = []
        self.records = []
        self.removed = []
        self.cleared = []
        self.record = None

    async def upsert(self, room, user, data, squad_number=None):
        self.upserts.append((room.id, user.id, data, squad_number))
        if self.record is None:
            self.record = make_record(
                user=user, data=data, squad_number=squad_number
            )
            self.records.append(self.record)
        else:
            self.record.data = data
            self.record.squad_number = squad_number
        return self.record

    async def list_for_room(self, room_id):
        return self.records

    async def remove(self, room_id, user_id):
        self.removed.append((room_id, user_id))
        return 1

    async def clear_room(self, room_id):
        self.cleared.append(room_id)
        return 2


class SkillStateServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dao = FakeSkillDao()
        patcher = mock.patch.object(skills_service, "SkillStateDao", self.dao)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.room = SimpleNamespace(id="room-1")
        self.user = make_user()

    async def test_save_state_stores_latest_and_serializes(self):
        state = await skills_service.save_state(
            self.room, self.user, make_payload()
        )
        self.assertEqual(1, len(self.dao.upserts))
        self.assertEqual("room-1", self.dao.upserts[0][0])
        self.assertEqual("op-1", self.dao.upserts[0][2]["operator_id"])
        self.assertIsNone(self.dao.upserts[0][3])
        self.assertEqual("1", state["user_id"])
        self.assertEqual("Alice", state["nickname"])
        self.assertEqual(3, len(state["skills"]))
        self.assertEqual(int(NOW.timestamp() * 1000), state["updated_at"])

    async def test_match_state_stored_and_serialized(self):
        state = await skills_service.save_state(
            self.room, self.user, make_payload(match_state="running")
        )
        self.assertEqual("running", state["match_state"])
        listed = await skills_service.list_states("room-1", now=NOW)
        self.assertEqual("running", listed[0]["match_state"])

    async def test_squad_number_stored_and_serialized(self):
        state = await skills_service.save_state(
            self.room, self.user, make_payload(squad_number=2)
        )
        self.assertEqual(2, self.dao.upserts[0][3])
        self.assertEqual(2, state["squad_number"])
        listed = await skills_service.list_states("room-1", now=NOW)
        self.assertEqual(2, listed[0]["squad_number"])

    async def test_squad_number_duplicate_users_allowed(self):
        for user_id in (1, 2):
            dao = FakeSkillDao()
            with mock.patch.object(skills_service, "SkillStateDao", dao):
                state = await skills_service.save_state(
                    self.room, make_user(user_id), make_payload(squad_number=2)
                )
            self.assertEqual(2, state["squad_number"])
            self.assertEqual(2, dao.upserts[0][3])

    async def test_server_folds_network_delay(self):
        now = 1792400001000
        payload = make_payload(
            captured_at=now - 500,
        )
        payload["skills"][0]["remaining_ms"] = 5000
        state = await skills_service.save_state(
            self.room, self.user, payload, now_ms=now
        )
        slot0 = state["skills"][0]
        self.assertEqual(4500, slot0["remaining_ms"])
        self.assertEqual(now + 4500, slot0["ready_at"])
        self.assertEqual(now, state["server_received_at"])
        self.assertEqual(now - 500, state["captured_at"])
        # 保留原字段
        self.assertEqual("op-1", state["operator_id"])

        listed = await skills_service.list_states("room-1", now=NOW)
        self.assertEqual(1, len(listed))
        self.assertEqual(now, listed[0]["server_received_at"])
        self.assertEqual(now + 4500, listed[0]["skills"][0]["ready_at"])
        self.assertEqual(now - 500, listed[0]["captured_at"])

    async def test_server_folds_future_and_old_captured_at(self):
        now = 1792400001000
        future = make_payload(captured_at=now + 5000)
        future["skills"][0]["remaining_ms"] = 5000
        state = await skills_service.save_state(
            self.room, self.user, future, now_ms=now
        )
        self.assertEqual(5000, state["skills"][0]["remaining_ms"])
        self.assertEqual(now + 5000, state["skills"][0]["ready_at"])

        old = make_payload(captured_at=now - 11000)
        old["skills"][0]["remaining_ms"] = 5000
        state = await skills_service.save_state(
            self.room, self.user, old, now_ms=now
        )
        self.assertEqual(0, state["skills"][0]["remaining_ms"])
        self.assertEqual(now, state["skills"][0]["ready_at"])

    async def test_server_clamps_negative_remaining(self):
        now = 1792400001000
        payload = make_payload(captured_at=now - 2000)
        payload["skills"][0]["remaining_ms"] = 1000
        state = await skills_service.save_state(
            self.room, self.user, payload, now_ms=now
        )
        self.assertEqual(0, state["skills"][0]["remaining_ms"])
        self.assertEqual(now, state["skills"][0]["ready_at"])

    async def test_list_states_filters_stale(self):
        fresh = make_record(user=make_user(1, "Alice"), updated_at=NOW)
        stale = make_record(
            user=make_user(2, "Bob"),
            updated_at=NOW - timedelta(minutes=31),
        )
        self.dao.records = [fresh, stale]
        items = await skills_service.list_states("room-1", now=NOW)
        self.assertEqual(["1"], [item["user_id"] for item in items])

    async def test_remove_and_clear(self):
        self.assertEqual(1, await skills_service.remove_state("room-1", 1))
        self.assertEqual([("room-1", 1)], self.dao.removed)
        self.assertEqual(2, await skills_service.clear_room("room-1"))
        self.assertEqual(["room-1"], self.dao.cleared)

    def test_stale_boundary(self):
        self.assertFalse(
            skills_service.is_stale(make_record(updated_at=NOW), now=NOW)
        )
        self.assertTrue(
            skills_service.is_stale(
                make_record(updated_at=NOW - timedelta(minutes=30, seconds=1)),
                now=NOW,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
