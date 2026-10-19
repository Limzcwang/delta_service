"""口令哈希工具 + 登录/创建/更新改造 + API 序列化 的单元测试。"""

import json
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    capture_logger,
    install_fastapi_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()

from api import user as user_api  # noqa: E402
from log.logger import logger as service_logger  # noqa: E402
from security import passwords  # noqa: E402
from security.passwords import (  # noqa: E402
    SALT_BYTES,
    SCRYPT_DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    hash_password,
    is_hashed,
    verify_password,
)
from service import user as user_service  # noqa: E402

RAW_PASSWORD = "Pa55w0rd!测试"
HASH_PATTERN = re.compile(
    r"^scrypt\$(\d+)\$(\d+)\$(\d+)\$([A-Za-z0-9+/]+=*)\$([A-Za-z0-9+/]+=*)$"
)


def _hash_parts(stored):
    match = HASH_PATTERN.match(stored)
    if match is None:
        raise AssertionError(f"不是合法哈希格式: {stored!r}")
    return match


class PasswordHashingTests(unittest.TestCase):
    def test_hash_format_and_parameters(self):
        stored = hash_password(RAW_PASSWORD)
        match = _hash_parts(stored)
        self.assertEqual(str(SCRYPT_N), match.group(1))
        self.assertEqual(str(SCRYPT_R), match.group(2))
        self.assertEqual(str(SCRYPT_P), match.group(3))
        # salt 16 字节 -> base64 24 字符；dklen 32 -> base64 44 字符
        self.assertEqual(16, SALT_BYTES)
        self.assertEqual(32, SCRYPT_DKLEN)
        self.assertEqual(24, len(match.group(4)))
        self.assertEqual(44, len(match.group(5)))
        self.assertNotIn(RAW_PASSWORD, stored)

    def test_hash_and_verify_roundtrip(self):
        stored = hash_password(RAW_PASSWORD)
        self.assertTrue(is_hashed(stored))
        self.assertTrue(verify_password(RAW_PASSWORD, stored))
        self.assertFalse(verify_password("wrong-password", stored))
        self.assertFalse(verify_password("", stored))

    def test_same_password_uses_random_salt(self):
        first = hash_password(RAW_PASSWORD)
        second = hash_password(RAW_PASSWORD)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(RAW_PASSWORD, first))
        self.assertTrue(verify_password(RAW_PASSWORD, second))

    def test_is_hashed_rejects_non_hashes_and_malformed_values(self):
        malformed = [
            "",
            None,
            123,
            "plaintext-password",
            "scrypt$",
            "scrypt$16384$8$1$not-base64",
            "scrypt$16384$8$1$c2FsdA==$",  # 空 expected
            "scrypt$16384$8$1$!!!$!!!",
        ]
        for value in malformed:
            with self.subTest(value=value):
                self.assertFalse(is_hashed(value))

    def test_malformed_parameters_are_rejected(self):
        # N 不是 2 的幂
        self.assertFalse(is_hashed("scrypt$3$8$1$c2FsdA==$aGFzaA=="))
        # N 过大（防御内存放大）
        self.assertFalse(is_hashed("scrypt$1073741824$8$1$c2FsdA==$aGFzaA=="))
        # r/p 越界
        self.assertFalse(is_hashed("scrypt$16384$0$1$c2FsdA==$aGFzaA=="))
        self.assertFalse(is_hashed("scrypt$16384$8$99$c2FsdA==$aGFzaA=="))

    def test_verify_legacy_plaintext(self):
        self.assertTrue(verify_password("Pa55w0rd!", "Pa55w0rd!"))
        self.assertFalse(verify_password("wrong", "Pa55w0rd!"))
        self.assertFalse(verify_password("Pa55w0rd!", "Pa55w0rd"))
        self.assertFalse(verify_password("text", None))
        self.assertFalse(verify_password(None, "text"))

    def test_corrupt_hash_lookalike_is_not_treated_as_plaintext(self):
        stored = "scrypt$broken-value"
        self.assertFalse(is_hashed(stored))
        # 即使 raw 与 stored 完全相同也不能通过（避免损坏哈希退化成明文比较）
        self.assertFalse(verify_password(stored, stored))

    def test_verify_uses_constant_time_compare(self):
        plaintext_stored = "legacy-PlainText!"
        hashed_stored = hash_password(RAW_PASSWORD)
        with mock.patch.object(
            passwords.hmac, "compare_digest", wraps=passwords.hmac.compare_digest
        ) as compare:
            self.assertTrue(verify_password(plaintext_stored, plaintext_stored))
            self.assertTrue(verify_password(RAW_PASSWORD, hashed_stored))
        self.assertGreaterEqual(compare.call_count, 2)

    def test_hash_password_input_validation(self):
        for value in ("", None, 123, b"bytes"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    hash_password(value)

    def test_unicode_password_roundtrip(self):
        raw = "口令🔐-pässwörd"
        stored = hash_password(raw)
        self.assertTrue(verify_password(raw, stored))
        self.assertFalse(verify_password("口令", stored))


class UserServicePasswordTests(unittest.IsolatedAsyncioTestCase):
    """登录/创建/更新链路：写库只出现哈希，登录按用户名取用户后校验。"""

    def setUp(self):
        ctx = capture_logger(service_logger)
        self.capture = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    async def test_create_user_stores_hash_not_plaintext(self):
        fake_user = SimpleNamespace(id=1, username="alice", nickname="alice")
        with mock.patch.object(
            user_service.UserDao,
            "create_user",
            new=mock.AsyncMock(return_value=fake_user),
        ) as create_user:
            result = await user_service.UserService.create_user("alice", RAW_PASSWORD)

        self.assertTrue(result["success"])
        stored = create_user.await_args.args[1]
        self.assertTrue(is_hashed(stored))
        self.assertTrue(verify_password(RAW_PASSWORD, stored))
        self.assertNotEqual(RAW_PASSWORD, stored)

    async def test_create_user_without_password_stores_none(self):
        fake_user = SimpleNamespace(id=1, username="alice", nickname="alice")
        with mock.patch.object(
            user_service.UserDao,
            "create_user",
            new=mock.AsyncMock(return_value=fake_user),
        ) as create_user:
            await user_service.UserService.create_user("alice", None)
        self.assertIsNone(create_user.await_args.args[1])

    async def test_update_user_hashes_password(self):
        fake_user = SimpleNamespace(id=7, username="alice", nickname="alice")
        with mock.patch.object(
            user_service.UserDao,
            "update_user",
            new=mock.AsyncMock(return_value=fake_user),
        ) as update_user:
            result = await user_service.UserService.update_user(7, password=RAW_PASSWORD)

        self.assertTrue(result["success"])
        stored = update_user.await_args.kwargs["password"]
        self.assertTrue(is_hashed(stored))
        self.assertTrue(verify_password(RAW_PASSWORD, stored))

    async def test_update_user_without_password_does_not_hash_other_fields(self):
        fake_user = SimpleNamespace(id=7, username="alice", nickname="alice")
        with mock.patch.object(
            user_service.UserDao,
            "update_user",
            new=mock.AsyncMock(return_value=fake_user),
        ) as update_user:
            await user_service.UserService.update_user(7, username="alice2")

        self.assertEqual("alice2", update_user.await_args.kwargs["username"])
        self.assertNotIn("password", update_user.await_args.kwargs)

    async def test_login_with_hashed_password_succeeds_without_rehash(self):
        stored = hash_password(RAW_PASSWORD)
        user = SimpleNamespace(id=1, username="alice", nickname="alice", password=stored)
        with mock.patch.object(
            user_service.UserDao,
            "get_user_by_username",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock()
        ) as update_user:
            result = await user_service.UserService.login("alice", RAW_PASSWORD)

        self.assertTrue(result["success"])
        self.assertEqual({"id": 1, "username": "alice", "nickname": "alice"}, result["user"])
        update_user.assert_not_awaited()

    async def test_login_with_legacy_plaintext_rehashes_immediately(self):
        user = SimpleNamespace(id=1, username="alice", nickname="alice", password=RAW_PASSWORD)
        with mock.patch.object(
            user_service.UserDao,
            "get_user_by_username",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock()
        ) as update_user:
            result = await user_service.UserService.login("alice", RAW_PASSWORD)

        self.assertTrue(result["success"])
        update_user.assert_awaited_once()
        kwargs = update_user.await_args.kwargs
        self.assertEqual(1, update_user.await_args.args[0])
        self.assertTrue(is_hashed(kwargs["password"]))
        self.assertTrue(verify_password(RAW_PASSWORD, kwargs["password"]))
        self.assertNotEqual(RAW_PASSWORD, kwargs["password"])

    async def test_login_wrong_password_fails(self):
        user = SimpleNamespace(id=1, username="alice", nickname="alice", password=hash_password(RAW_PASSWORD))
        with mock.patch.object(
            user_service.UserDao,
            "get_user_by_username",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            user_service.UserDao, "update_user", new=mock.AsyncMock()
        ) as update_user:
            result = await user_service.UserService.login("alice", "wrong-password")

        self.assertFalse(result["success"])
        self.assertEqual("用户名或密码错误", result["message"])
        update_user.assert_not_awaited()

    async def test_login_unknown_user_fails(self):
        with mock.patch.object(
            user_service.UserDao,
            "get_user_by_username",
            new=mock.AsyncMock(return_value=None),
        ):
            result = await user_service.UserService.login("ghost", RAW_PASSWORD)
        self.assertFalse(result["success"])
        self.assertEqual("用户名或密码错误", result["message"])

    async def test_login_rehash_failure_does_not_break_login(self):
        user = SimpleNamespace(id=1, username="alice", nickname="alice", password=RAW_PASSWORD)
        with mock.patch.object(
            user_service.UserDao,
            "get_user_by_username",
            new=mock.AsyncMock(return_value=user),
        ), mock.patch.object(
            user_service.UserDao,
            "update_user",
            new=mock.AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await user_service.UserService.login("alice", RAW_PASSWORD)

        self.assertTrue(result["success"])
        self.assertTrue(any("重哈希失败" in message for message in self.capture.messages()))

    def test_dao_no_longer_exposes_plaintext_login_query(self):
        self.assertFalse(hasattr(user_service.UserDao, "login"))


class UserSerializationTests(unittest.IsolatedAsyncioTestCase):
    """API 响应不得包含 password 字段。"""

    def test_public_user_filters_password_from_dict_and_object(self):
        payload = {
            "id": 1,
            "nickname": "alice",
            "password": hash_password(RAW_PASSWORD),
            "created_at": "2026-01-01",
            "updated_at": "2026-01-02",
            "internal_note": "x",
        }
        self.assertEqual(
            {"id": 1, "nickname": "alice", "created_at": "2026-01-01", "updated_at": "2026-01-02"},
            user_api._public_user(payload),
        )
        self.assertIsNone(user_api._public_user(None))

        user_obj = SimpleNamespace(id=2, username="bob", nickname="bob", password="secret")
        self.assertEqual({"id": 2, "nickname": "bob"}, user_api._public_user(user_obj))

    async def test_create_user_response_has_no_password(self):
        fake_result = {
            "success": True,
            "message": "用户创建成功",
            "user": {"id": 1, "nickname": "alice", "password": "should-not-leak"},
        }
        with mock.patch.object(
            user_api.UserService, "create_user", new=mock.AsyncMock(return_value=fake_result)
        ):
            response = await user_api.create_user(
                SimpleNamespace(username="alice01", nickname="alice", password=RAW_PASSWORD)
            )

        text = json.dumps(response, ensure_ascii=False)
        self.assertNotIn("password", text)
        self.assertNotIn("should-not-leak", text)
        self.assertEqual({"id": 1, "nickname": "alice"}, response["data"])

    async def test_login_response_has_no_password(self):
        fake_result = {
            "success": True,
            "message": "登录成功",
            "user": {"id": 1, "nickname": "alice", "password": "should-not-leak"},
        }
        with mock.patch.object(
            user_api.UserService, "login", new=mock.AsyncMock(return_value=fake_result)
        ):
            response = await user_api.login(
                SimpleNamespace(username="alice01", nickname="alice", password=RAW_PASSWORD)
            )
        self.assertNotIn("password", json.dumps(response, ensure_ascii=False))

    async def test_list_users_response_has_no_password(self):
        fake_result = {
            "success": True,
            "message": "获取用户列表成功",
            "users": [
                {"id": 1, "nickname": "alice", "password": "h1"},
                {"id": 2, "nickname": "bob", "password": "h2"},
            ],
            "total": 2,
        }
        with mock.patch.object(
            user_api.UserService, "list_users", new=mock.AsyncMock(return_value=fake_result)
        ):
            response = await user_api.list_users(0, 10)
        text = json.dumps(response, ensure_ascii=False)
        self.assertNotIn("password", text)
        self.assertEqual(2, response["data"]["total"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
