"""存量口令迁移逻辑测试（fake store，不连数据库）。"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from scripts.migrate_passwords import (
    MigrationPlan,
    apply_migration,
    migrate,
    plan_migration,
    _print_summary,
)
from security.passwords import hash_password, is_hashed, verify_password


class FakeUserStore:
    """内存 fake store：模拟 TortoiseUserStore 的两个 IO 方法。"""

    def __init__(self, users):
        self.users = []
        for user in users:
            if isinstance(user, dict):
                self.users.append(dict(user))
            else:
                self.users.append(SimpleNamespace(**vars(user)))
        self.updates = []

    async def list_users(self):
        return list(self.users)

    async def update_password(self, user_id, hashed_password):
        self.updates.append((user_id, hashed_password))
        for user in self.users:
            if isinstance(user, dict):
                if user.get("id") == user_id:
                    user["password"] = hashed_password
            elif user.id == user_id:
                user.password = hashed_password


def _stored_password(user):
    if isinstance(user, dict):
        return user.get("password")
    return user.password


class PlanMigrationTests(unittest.TestCase):
    def test_plan_counts_plaintext_hashed_and_empty_rows(self):
        users = [
            {"id": 1, "password": "plain-1"},
            {"id": 2, "password": "plain-2"},
            {"id": 3, "password": "plain-3"},
            {"id": 4, "password": "plain-4"},
            {"id": 5, "password": hash_password("already-hashed")},
            {"id": 6, "password": None},
            {"id": 7, "password": ""},
        ]
        plan = plan_migration(users)
        self.assertEqual(7, plan.total)
        self.assertEqual(4, plan.pending)
        self.assertEqual(1, plan.already_hashed)
        self.assertEqual(2, plan.empty_password)

    def test_plan_hashes_are_verifiable_and_hide_plaintext(self):
        users = [{"id": 1, "password": "Plain#1"}]
        plan = plan_migration(users)
        user_id, hashed = plan.updates[0]
        self.assertEqual(1, user_id)
        self.assertTrue(is_hashed(hashed))
        self.assertTrue(verify_password("Plain#1", hashed))
        self.assertNotEqual("Plain#1", hashed)

    def test_plan_does_not_mutate_input_rows(self):
        users = [{"id": 1, "password": "Plain#1"}]
        plan_migration(users)
        self.assertEqual("Plain#1", users[0]["password"])

    def test_plan_supports_attribute_objects_and_includes_deleted_users(self):
        users = [
            SimpleNamespace(id=1, password="plain-a", deleted=False),
            SimpleNamespace(id=2, password="plain-b", deleted=True),
        ]
        plan = plan_migration(users)
        self.assertEqual(2, plan.pending)
        self.assertEqual([1, 2], [user_id for user_id, _ in plan.updates])


class ApplyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_does_not_write(self):
        store = FakeUserStore([{"id": 1, "password": "plain"}])
        plan, applied = await migrate(store, dry_run=True)
        self.assertEqual(1, plan.pending)
        self.assertEqual(0, applied)
        self.assertEqual([], store.updates)
        self.assertEqual("plain", store.users[0]["password"])

    async def test_apply_writes_each_pending_row(self):
        plan = MigrationPlan(updates=[(1, "hash-1"), (2, "hash-2")])
        written = []

        async def update(user_id, hashed):
            written.append((user_id, hashed))

        applied = await apply_migration(plan, update)
        self.assertEqual(2, applied)
        self.assertEqual([(1, "hash-1"), (2, "hash-2")], written)

    async def test_apply_is_noop_for_empty_plan(self):
        async def update(user_id, hashed):  # pragma: no cover - 不应被调用
            raise AssertionError("空计划不应写库")

        applied = await apply_migration(MigrationPlan(), update)
        self.assertEqual(0, applied)


class MigrateExistingUsersTests(unittest.IsolatedAsyncioTestCase):
    """模拟“现有 4 个明文用户”上线迁移场景。"""

    async def test_four_plaintext_users_are_migrated_and_idempotent(self):
        originals = {
            1: "alice-Pass1",
            2: "bob-Pass2",
            3: "carol-Pass3",
            4: "dave-Pass4",
        }
        store = FakeUserStore(
            [{"id": user_id, "password": raw} for user_id, raw in originals.items()]
        )

        plan, applied = await migrate(store)
        self.assertEqual(4, plan.pending)
        self.assertEqual(4, applied)
        self.assertEqual(4, len(store.updates))

        for user in store.users:
            stored = _stored_password(user)
            self.assertTrue(is_hashed(stored))
            self.assertTrue(verify_password(originals[user["id"]], stored))
            self.assertNotEqual(originals[user["id"]], stored)

        # 二次执行：幂等，不重复哈希
        second_plan, second_applied = await migrate(store)
        self.assertEqual(0, second_plan.pending)
        self.assertEqual(4, second_plan.already_hashed)
        self.assertEqual(0, second_applied)
        self.assertEqual(4, len(store.updates))

    async def test_plaintext_users_still_log_in_via_legacy_verify_before_migration(self):
        # 未迁移前：verify_password 兼容旧明文，登录不会立刻中断（应用层会重哈希）
        raw = "legacy-Pass!"
        store = FakeUserStore([{"id": 1, "password": raw}])
        self.assertTrue(verify_password(raw, store.users[0]["password"]))
        self.assertFalse(is_hashed(store.users[0]["password"]))

        await migrate(store)
        self.assertTrue(is_hashed(store.users[0]["password"]))
        self.assertTrue(verify_password(raw, store.users[0]["password"]))

    async def test_empty_and_none_passwords_are_left_untouched(self):
        store = FakeUserStore(
            [
                {"id": 1, "password": None},
                {"id": 2, "password": ""},
                {"id": 3, "password": "plain"},
            ]
        )
        plan, applied = await migrate(store)
        self.assertEqual(1, plan.pending)
        self.assertEqual(1, applied)
        self.assertIsNone(store.users[0]["password"])
        self.assertEqual("", store.users[1]["password"])
        self.assertTrue(is_hashed(store.users[2]["password"]))

    async def test_metadata_already_hashed_rows_are_not_touched(self):
        existing = hash_password("already")
        store = FakeUserStore([{"id": 1, "password": existing}])
        plan, applied = await migrate(store)
        self.assertEqual(0, plan.pending)
        self.assertEqual(0, applied)
        self.assertEqual([], store.updates)
        self.assertEqual(existing, store.users[0]["password"])


class MigrationSummaryTests(unittest.TestCase):
    def test_dry_run_summary_reports_pending_rows(self):
        plan = plan_migration([{"id": 1, "password": "plain"}])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_summary(plan, applied=0, dry_run=True)
        output = buffer.getvalue()
        self.assertIn("用户总数: 1", output)
        self.assertIn("[dry-run] 待迁移明文口令: 1 行", output)

    def test_apply_summary_reports_migrated_rows(self):
        plan = plan_migration([{"id": 1, "password": "plain"}])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_summary(plan, applied=1, dry_run=False)
        self.assertIn("已迁移: 1 行", buffer.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
