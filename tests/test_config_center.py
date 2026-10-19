"""干员配置中心：版本/ETag/304/降级/限频/公开访问测试。"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import (
    REPO_ROOT,
    install_fastapi_stub,
    install_jwt_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()
install_jwt_stub()

from api import v1_common, v1_config  # noqa: E402
from service.config_center import (  # noqa: E402
    OperatorConfigCenter,
    OperatorConfigError,
)


def write_config(path: Path, operators, meta=None):
    payload = {"operators": operators, "_meta": meta or {"schema": 1}}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


def make_request(if_none_match=None, ip="203.0.113.10", request_id="req_cfg"):
    headers = {}
    if if_none_match is not None:
        headers["If-None-Match"] = if_none_match
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=ip),
        state=SimpleNamespace(request_id=request_id),
        url=SimpleNamespace(path="/api/v1/config/operators"),
        method="GET",
    )


class ConfigCenterLoadTests(unittest.TestCase):
    def test_load_version_and_etag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operators.json"
            raw = write_config(path, [{"id": "op-1", "name": "干员A"}])
            center = OperatorConfigCenter(str(path)).load()
            expected = hashlib.sha256(raw).hexdigest()[:16]

            self.assertEqual(expected, center.version)
            self.assertEqual(expected, center.etag)
            payload = center.get()
            self.assertEqual(expected, payload["version"])
            self.assertEqual(expected, payload["etag"])
            self.assertEqual([{"id": "op-1", "name": "干员A"}], payload["operators"])
            self.assertEqual({"schema": 1}, payload["meta"])
            self.assertEqual(int(path.stat().st_mtime * 1000), payload["updated_at"])

    def test_content_change_changes_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operators.json"
            write_config(path, [{"id": "a"}])
            first = OperatorConfigCenter(str(path)).load().version
            write_config(path, [{"id": "b"}])
            second = OperatorConfigCenter(str(path)).load().version
            self.assertNotEqual(first, second)

    def test_missing_or_invalid_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with self.assertRaises(OperatorConfigError):
                OperatorConfigCenter(str(missing)).load()

            path = Path(tmp) / "bad.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(OperatorConfigError):
                OperatorConfigCenter(str(path)).load()

            path.write_text(json.dumps({"operators": "x"}), encoding="utf-8")
            with self.assertRaises(OperatorConfigError):
                OperatorConfigCenter(str(path)).load()


class ConfigApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "operators.json"
        self.raw = write_config(self.path, [{"id": "op-1", "name": "干员A"}])
        self.center = OperatorConfigCenter(str(self.path)).load()
        self.patcher = mock.patch.object(v1_config, "config_center", self.center)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        # 避开真实限频，单独测试限频分支时再覆盖
        limiter = SimpleNamespace(
            check=mock.Mock(return_value=True),
            retry_after=mock.Mock(return_value=1),
        )
        self.limiter_patcher = mock.patch.object(
            v1_config, "config_rate_limiter", limiter
        )
        self.limiter_patcher.start()
        self.addCleanup(self.limiter_patcher.stop)

    async def test_public_200_with_etag_cache_control(self):
        response = await v1_config.get_operators_config(make_request())

        self.assertEqual(200, response.status_code)
        expected = hashlib.sha256(self.raw).hexdigest()[:16]
        self.assertEqual(f'"{expected}"', response.headers["ETag"])
        self.assertEqual("public, max-age=60", response.headers["Cache-Control"])
        body = response.content
        self.assertEqual(expected, body["version"])
        self.assertEqual(expected, body["etag"])
        self.assertEqual([{"id": "op-1", "name": "干员A"}], body["operators"])
        self.assertEqual({"schema": 1}, body["meta"])

    async def test_if_none_match_returns_304_empty(self):
        etag = f'"{self.center.etag}"'
        response = await v1_config.get_operators_config(
            make_request(if_none_match=etag)
        )
        self.assertEqual(304, response.status_code)
        self.assertEqual(b"", response.body)
        self.assertEqual(etag, response.headers["ETag"])

    async def test_if_none_match_mismatch_returns_200(self):
        response = await v1_config.get_operators_config(
            make_request(if_none_match='"deadbeefdeadbeef"')
        )
        self.assertEqual(200, response.status_code)

    async def test_weak_and_list_etag(self):
        etag = f'"{self.center.etag}"'
        for header in (f"W/{etag}", f'"other", {etag}', "*"):
            with self.subTest(header=header):
                response = await v1_config.get_operators_config(
                    make_request(if_none_match=header)
                )
                self.assertEqual(304, response.status_code)

    async def test_missing_config_returns_503(self):
        with mock.patch.object(
            v1_config,
            "config_center",
            OperatorConfigCenter("/no/such/operators.json"),
        ):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_config.get_operators_config(make_request())
        self.assertEqual(503, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_UNAVAILABLE, ctx.exception.code)
        self.assertEqual("CONFIG_UNAVAILABLE", ctx.exception.error_code)

    async def test_rate_limited(self):
        limiter = SimpleNamespace(
            check=mock.Mock(return_value=False),
            retry_after=mock.Mock(return_value=10),
        )
        with mock.patch.object(v1_config, "config_rate_limiter", limiter):
            with self.assertRaises(v1_common.APIError) as ctx:
                await v1_config.get_operators_config(make_request())
        self.assertEqual(429, ctx.exception.status_code)
        self.assertEqual(v1_common.CODE_RATE_LIMITED, ctx.exception.code)

    def test_source_wires_startup_load(self):
        source = (REPO_ROOT / "api" / "init.py").read_text(encoding="utf-8")
        self.assertIn("load_operator_config()", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
