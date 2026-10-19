"""HTTPS/Caddy 反代：uvicorn 显式信任 X-Forwarded-For 的配置测试。"""

import os
import unittest
from unittest import mock

from tests.support import (
    REPO_ROOT,
    install_main_dependency_stubs,
    load_main_module,
)

install_main_dependency_stubs()


class UvicornProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self.main = load_main_module("main_proxy_headers_test")

    def test_default_forwarded_allow_ips(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORWARDED_ALLOW_IPS", None)
            kwargs = self.main._uvicorn_config_kwargs()
        self.assertEqual("0.0.0.0", kwargs["host"])
        self.assertEqual(8000, kwargs["port"])
        self.assertTrue(kwargs["proxy_headers"])
        self.assertEqual("127.0.0.1", kwargs["forwarded_allow_ips"])

    def test_env_override(self):
        with mock.patch.dict(
            os.environ, {"FORWARDED_ALLOW_IPS": "172.17.0.1"}, clear=False
        ):
            kwargs = self.main._uvicorn_config_kwargs()
        self.assertEqual("172.17.0.1", kwargs["forwarded_allow_ips"])
        self.assertTrue(kwargs["proxy_headers"])

    def test_ci_deploy_passes_forwarded_allow_ips(self):
        source = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("-e FORWARDED_ALLOW_IPS=172.17.0.1", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
