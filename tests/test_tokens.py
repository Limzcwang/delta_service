"""JWT 工具测试（security/tokens.py）：签发/校验/过期/篡改/密钥配置。"""

import base64
import json
import os
import time
import unittest
from unittest import mock

from tests.support import capture_logger, install_jwt_stub

install_jwt_stub()

import jwt  # noqa: E402  （离线环境下为 tests/support.py 提供的 HS256 stub）

from log.logger import logger as service_logger  # noqa: E402
from security import tokens  # noqa: E402

SECRET = "unit-test-jwt-secret-0123456789abcdef"
OTHER_SECRET = "another-unit-test-secret-fedcba9876543210"


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _raw_claims(token: str) -> dict:
    return json.loads(_b64url_decode(token.split(".")[1]))


class TokenTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            os.environ,
            {"JWT_SECRET": SECRET, "JWT_EXPIRE_DAYS": "7"},
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # 每个用例从“未告警”状态开始
        tokens._weak_secret_warned = False


class CreateAndDecodeTests(TokenTestCase):
    def test_roundtrip_returns_user_id_and_ttl(self):
        token, expires_in = tokens.create_access_token(42)
        self.assertIsInstance(token, str)
        self.assertEqual(7 * 24 * 3600, expires_in)
        self.assertEqual("42", tokens.decode_access_token(token))

    def test_claims_include_sub_iat_exp_typ(self):
        token, expires_in = tokens.create_access_token(7)
        claims = _raw_claims(token)
        self.assertEqual("7", claims["sub"])
        self.assertEqual("access", claims["typ"])
        self.assertIsInstance(claims["iat"], int)
        self.assertIsInstance(claims["exp"], int)
        self.assertEqual(expires_in, claims["exp"] - claims["iat"])

    def test_user_id_is_always_stringified(self):
        token, _ = tokens.create_access_token(123)
        self.assertEqual("123", tokens.decode_access_token(token))

        token2, _ = tokens.create_access_token("uuid-like-user")
        self.assertEqual("uuid-like-user", tokens.decode_access_token(token2))

    def test_secret_argument_overrides_environment(self):
        token, _ = tokens.create_access_token(1, secret=OTHER_SECRET)
        self.assertEqual("1", tokens.decode_access_token(token, secret=OTHER_SECRET))

    def test_decode_accepts_injected_clock(self):
        issued_at = 1_700_000_000
        token, _ = tokens.create_access_token(
            1, now=issued_at, expires_in_seconds=60
        )
        self.assertEqual(
            "1", tokens.decode_access_token(token, now=issued_at + 59)
        )
        # 过期 59s 内仍在 60s 容忍窗口内
        self.assertEqual(
            "1", tokens.decode_access_token(token, now=issued_at + 119)
        )
        with self.assertRaises(tokens.TokenExpiredError):
            tokens.decode_access_token(token, now=issued_at + 120)

    def test_expiry_leeway_59s_passes_60s_rejected(self):
        issued_at = 1_000
        token, _ = tokens.create_access_token(
            1, now=issued_at, expires_in_seconds=100
        )
        expires_at = issued_at + 100
        self.assertEqual(
            "1", tokens.decode_access_token(token, now=expires_at + 59)
        )
        with self.assertRaises(tokens.TokenExpiredError):
            tokens.decode_access_token(token, now=expires_at + 60)


class InvalidTokenTests(TokenTestCase):
    def test_expired_token_raises_expired_error(self):
        token, _ = tokens.create_access_token(
            1, now=time.time() - 10 * 24 * 3600
        )
        with self.assertRaises(tokens.TokenExpiredError):
            tokens.decode_access_token(token)

    def test_tampered_payload_raises_invalid_error(self):
        token, _ = tokens.create_access_token(1)
        header, _payload, signature = token.split(".")
        claims = _raw_claims(token)
        claims["sub"] = "999"
        tampered = ".".join(
            [header, _b64url_encode(json.dumps(claims).encode()), signature]
        )
        with self.assertRaises(tokens.TokenInvalidError):
            tokens.decode_access_token(tampered)

    def test_wrong_secret_raises_invalid_error(self):
        token, _ = tokens.create_access_token(1, secret=SECRET)
        with mock.patch.dict(os.environ, {"JWT_SECRET": OTHER_SECRET}, clear=False):
            with self.assertRaises(tokens.TokenInvalidError):
                tokens.decode_access_token(token)

    def test_wrong_typ_raises_invalid_error(self):
        now = int(time.time())
        refresh_token = jwt.encode(
            {"sub": "1", "iat": now, "exp": now + 3600, "typ": "refresh"},
            SECRET,
            algorithm="HS256",
        )
        with self.assertRaises(tokens.TokenInvalidError):
            tokens.decode_access_token(refresh_token)

    def test_empty_and_malformed_tokens_raise_invalid_error(self):
        for value in ("", None, 123, "not-a-jwt", "a.b.c"):
            with self.subTest(value=value):
                with self.assertRaises(tokens.TokenInvalidError):
                    tokens.decode_access_token(value)


class ConfigTests(TokenTestCase):
    def test_missing_secret_raises_config_error(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_SECRET", None)
            with self.assertRaises(tokens.TokenConfigError):
                tokens.create_access_token(1)
            with self.assertRaises(tokens.TokenConfigError):
                tokens.decode_access_token("a.b.c")
        self.assertTrue(issubclass(tokens.TokenConfigError, RuntimeError))

    def test_blank_secret_raises_config_error(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET": "   "}, clear=False):
            with self.assertRaises(tokens.TokenConfigError):
                tokens.create_access_token(1)

    def test_default_expire_days_is_seven(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_EXPIRE_DAYS", None)
            self.assertEqual(7, tokens.get_expire_days())
            self.assertEqual(7 * 24 * 3600, tokens.token_ttl_seconds())

    def test_expire_days_from_environment(self):
        with mock.patch.dict(os.environ, {"JWT_EXPIRE_DAYS": "1"}, clear=False):
            _token, expires_in = tokens.create_access_token(1)
        self.assertEqual(24 * 3600, expires_in)

    def test_invalid_expire_days_raises_config_error(self):
        for raw in ("abc", "0", "-1", "1.5"):
            with self.subTest(raw=raw):
                with mock.patch.dict(
                    os.environ, {"JWT_EXPIRE_DAYS": raw}, clear=False
                ):
                    with self.assertRaises(tokens.TokenConfigError):
                        tokens.create_access_token(1)

    def test_short_secret_warns_once_but_does_not_block(self):
        with mock.patch.dict(
            os.environ, {"JWT_SECRET": "short-secret"}, clear=False
        ), capture_logger(service_logger) as capture:
            token, _ = tokens.create_access_token(1)
            self.assertEqual("1", tokens.decode_access_token(token))
            tokens.create_access_token(2)  # 第二次调用不应重复告警

        warnings = [record for record in capture.records if record.levelname == "WARNING"]
        self.assertEqual(1, len(warnings))
        self.assertIn("32", warnings[0].getMessage())

    def test_long_secret_does_not_warn(self):
        with capture_logger(service_logger) as capture:
            tokens.create_access_token(1)
        warnings = [record for record in capture.records if record.levelname == "WARNING"]
        self.assertEqual([], warnings)

    def test_empty_user_id_is_rejected(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tokens.create_access_token(value)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
