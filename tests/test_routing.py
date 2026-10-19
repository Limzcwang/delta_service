"""修复 1：/api/users/list 不再被 /api/users/{user_id} 路由遮蔽。"""

import inspect
import re
import unittest
from unittest import mock

from tests.support import (
    install_fastapi_stub,
    install_pydantic_stub,
    install_tortoise_stub,
)

install_tortoise_stub()
install_fastapi_stub()
install_pydantic_stub()

from api import user as user_api  # noqa: E402


def _template_to_regex(template: str) -> re.Pattern:
    escaped = re.escape(template)
    escaped = re.sub(r"\\\{[^}]+\\\}", "[^/]+", escaped)
    return re.compile(f"^{escaped}$")


def _first_matching_path(method: str, request_path: str):
    """按注册顺序模拟路由匹配（等价于 FastAPI 的“先注册先匹配”）。"""
    prefix = user_api.router.prefix
    for route_method, template, _func in user_api.router.routes:
        if route_method != method:
            continue
        if _template_to_regex(prefix + template).match(request_path):
            return prefix + template, _func
    return None, None


class UserRouteOrderTests(unittest.TestCase):
    def test_list_route_registered_before_dynamic_route(self):
        routes = user_api.router.full_paths()
        list_index = routes.index(("GET", "/api/users/list"))
        dynamic_index = routes.index(("GET", "/api/users/{user_id}"))
        self.assertLess(
            list_index,
            dynamic_index,
            "GET /api/users/list 必须先于 GET /api/users/{user_id} 注册，否则会被动态路由遮蔽",
        )

    def test_list_path_matches_static_route_first(self):
        template, func = _first_matching_path("GET", "/api/users/list")
        self.assertEqual("/api/users/list", template)
        self.assertIs(user_api.list_users, func)

    def test_non_numeric_user_id_still_hits_dynamic_route(self):
        template, func = _first_matching_path("GET", "/api/users/abc")
        self.assertEqual("/api/users/{user_id}", template)
        self.assertIs(user_api.get_user_by_id, func)

        # FastAPI 会按类型注解把 "abc" 判定为非法 int 并返回 422；
        # 这里确认处理器参数仍声明为 int，防止被改成 str 而静默接受。
        annotation = inspect.signature(user_api.get_user_by_id).parameters["user_id"].annotation
        self.assertIs(int, annotation)

    def test_numeric_user_id_hits_dynamic_route(self):
        template, _ = _first_matching_path("GET", "/api/users/42")
        self.assertEqual("/api/users/{user_id}", template)

    def test_username_route_is_not_shadowed(self):
        template, _ = _first_matching_path("GET", "/api/users/username/alice")
        self.assertEqual("/api/users/username/{username}", template)


class UserListEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_endpoint_returns_pagination_envelope(self):
        fake_result = {
            "success": True,
            "message": "获取用户列表成功",
            "users": [
                {"id": 1, "username": "alice"},
                {"id": 2, "username": "bob"},
            ],
            "total": 2,
        }
        with mock.patch.object(
            user_api.UserService,
            "list_users",
            new=mock.AsyncMock(return_value=fake_result),
        ) as list_users:
            response = await user_api.list_users(0, 10)

        list_users.assert_awaited_once_with(0, 10)
        self.assertEqual(200, response["code"])
        self.assertEqual("获取用户列表成功", response["message"])
        self.assertEqual(2, response["data"]["total"])
        self.assertEqual(2, len(response["data"]["users"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
