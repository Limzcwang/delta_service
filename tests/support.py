"""离线单元测试辅助：为仓库运行期依赖安装最小 stub。

仓库本身未安装 fastapi / tortoise 等第三方依赖时，测试也必须能跑通，
因此这里用 ``sys.modules`` 注入最小替身，只保留被测试代码实际会用到的名字。
"""

import base64
import hashlib
import hmac
import importlib.util
import json
import logging
import sys
import time
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class RecordingHandler(logging.Handler):
    """把日志记录收集到内存，避免测试写 logs/ 或污染控制台。"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [record.getMessage() for record in self.records]


class capture_logger:
    """上下文管理器：临时替换指定 logger 的 handler/level/propagate 并收集日志。"""

    def __init__(self, target_logger: logging.Logger):
        self.logger = target_logger
        self.handler = RecordingHandler()
        self._original_handlers = None
        self._original_level = None
        self._original_propagate = None

    def __enter__(self):
        self._original_handlers = self.logger.handlers[:]
        self._original_level = self.logger.level
        self._original_propagate = self.logger.propagate
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        return self.handler

    def __exit__(self, exc_type, exc_value, traceback):
        self.logger.handlers = self._original_handlers
        self.logger.setLevel(self._original_level)
        self.logger.propagate = self._original_propagate
        return False


def _replace_module(name, **attrs):
    """安装（或覆盖）一个最小模块并返回它。"""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class FakeJSONResponse:
    """fastapi.responses.JSONResponse 的最小替身。"""

    def __init__(self, status_code: int = 200, content=None, **kwargs):
        self.status_code = status_code
        self.content = content
        self.body = json.dumps(content, ensure_ascii=False).encode("utf-8")


def install_tortoise_stub():
    """安装 tortoise 最小替身，让日志中间件与 dao 层可离线导入。"""
    existing = sys.modules.get("tortoise.backends.base.client")
    if existing is not None and getattr(existing, "_dsh_stub", False):
        return

    tortoise = _replace_module("tortoise")
    backends = _replace_module("tortoise.backends")
    base = _replace_module("tortoise.backends.base")
    client = _replace_module("tortoise.backends.base.client")

    class BaseDBAsyncClient:
        """占位基类。"""

    client.BaseDBAsyncClient = BaseDBAsyncClient
    client._dsh_stub = True
    tortoise.backends = backends
    backends.base = base
    base.client = client

    # dao/user.py 在导入期会用到 fields/models/pydantic_model_creator。
    fields = _replace_module("tortoise.fields")

    class _StubField:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    for field_name in (
        "IntField",
        "CharField",
        "DatetimeField",
        "BooleanField",
        "TextField",
        "FloatField",
        "ForeignKeyField",
    ):
        setattr(fields, field_name, _StubField)

    models = _replace_module("tortoise.models")

    class Model:
        """占位模型基类。"""

    models.Model = Model

    contrib = _replace_module("tortoise.contrib")
    pydantic_module = _replace_module("tortoise.contrib.pydantic")
    pydantic_module.pydantic_model_creator = lambda *args, **kwargs: object

    tortoise.fields = fields
    tortoise.models = models
    tortoise.contrib = contrib


class _StubRouter:
    """APIRouter 的最小替身：记录 (method, path, func) 便于测试路由注册顺序。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.prefix = kwargs.get("prefix", "")
        self.routes = []

    def post(self, *args, **kwargs):
        return self._register("POST", args, kwargs)

    def get(self, *args, **kwargs):
        return self._register("GET", args, kwargs)

    def put(self, *args, **kwargs):
        return self._register("PUT", args, kwargs)

    def patch(self, *args, **kwargs):
        return self._register("PATCH", args, kwargs)

    def delete(self, *args, **kwargs):
        return self._register("DELETE", args, kwargs)

    def _register(self, method, args, kwargs):
        path = args[0] if args else kwargs.get("path", "")

        def decorator(func):
            self.routes.append((method, path, func))
            return func

        return decorator

    def full_paths(self):
        """返回与注册顺序一致的完整路径列表（仅供路由顺序测试使用）。"""
        return [(method, f"{self.prefix}{path}") for method, path, _ in self.routes]


class StubHTTPException(Exception):
    """fastapi.HTTPException 的最小替身，保留 status_code/detail。"""

    def __init__(self, status_code: int = 500, detail=None, headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(str(detail))


class _StubStatus:
    HTTP_400_BAD_REQUEST = 400
    HTTP_401_UNAUTHORIZED = 401
    HTTP_404_NOT_FOUND = 404
    HTTP_500_INTERNAL_SERVER_ERROR = 500


def install_fastapi_stub():
    """安装 fastapi 最小替身（APIRouter/HTTPException/Request/Depends/status）。"""
    _replace_module(
        "fastapi",
        APIRouter=_StubRouter,
        HTTPException=StubHTTPException,
        Request=object,
        Depends=lambda dependency: dependency,
        status=_StubStatus,
    )
    # v1 统一响应包会用到 fastapi.responses.JSONResponse
    _replace_module("fastapi.responses", JSONResponse=FakeJSONResponse)


def install_pydantic_stub():
    """安装 pydantic.BaseModel 最小替身。"""

    class BaseModel:
        def __init__(self, **data):
            self.__fields_set__ = set(data.keys())
            for key, value in data.items():
                setattr(self, key, value)

        @property
        def model_fields_set(self):
            return set(self.__fields_set__)

        def dict(self, exclude_unset=False, **_kwargs):
            data = dict(self.__dict__)
            data.pop("__fields_set__", None)
            if exclude_unset:
                data = {
                    key: value
                    for key, value in data.items()
                    if key in self.__fields_set__
                }
            return data

        def model_dump(self, exclude_unset=False, **_kwargs):
            return self.dict(exclude_unset=exclude_unset)

    _replace_module("pydantic", BaseModel=BaseModel)


def install_main_dependency_stubs():
    """安装 main.py 依赖的第三方/业务模块替身，避免导入时连数据库或网络。"""
    install_tortoise_stub()
    install_fastapi_stub()
    install_pydantic_stub()

    responses = sys.modules.get("fastapi.responses")
    if responses is None or not hasattr(responses, "JSONResponse"):
        _replace_module("fastapi.responses", JSONResponse=FakeJSONResponse)
    else:
        setattr(responses, "JSONResponse", FakeJSONResponse)

    _replace_module("uvicorn", Config=object, Server=object)

    async def _noop(*args, **kwargs):
        return None

    _replace_module("api.init", create_app=lambda *a, **k: None)
    _replace_module("config.database", init_database=_noop, close_database=_noop)
    _replace_module("websocket", websocket_manager=object())


def load_main_module(name="main_under_test"):
    """加载仓库根目录的 main.py（不执行 ``if __name__ == '__main__'`` 分支）。"""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "main.py")
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常时给出明确错误
        raise RuntimeError("无法加载 main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def install_jwt_stub():
    """未安装 PyJWT 时提供最小 HS256 替身（仅测试用）。

    stub 实现了 PyJWT 2.x 中本仓库用到的子集：``encode``/``decode``、
    ``InvalidTokenError``/``ExpiredSignatureError``/``InvalidSignatureError``/
    ``DecodeError``，足以离线验证签名、过期与 claims 校验逻辑。已安装真实
    PyJWT 时什么都不做。
    """
    try:
        import jwt  # noqa: F401  # 真实依赖存在时直接使用
        return
    except ImportError:
        pass

    module = types.ModuleType("jwt")

    class InvalidTokenError(Exception):
        pass

    class DecodeError(InvalidTokenError):
        pass

    class InvalidSignatureError(DecodeError):
        pass

    class ExpiredSignatureError(InvalidTokenError):
        pass

    def encode(payload, key, algorithm="HS256", **kwargs):
        if algorithm != "HS256":
            raise InvalidTokenError("stub 仅支持 HS256")
        header = _b64url_encode(json.dumps({"alg": algorithm, "typ": "JWT"}, separators=(",", ":")).encode())
        body = _b64url_encode(json.dumps(payload, separators=(",", ":"), default=str).encode())
        signing_input = f"{header}.{body}".encode("ascii")
        signature = hmac.new(str(key).encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header}.{body}.{_b64url_encode(signature)}"

    def decode(token, key, algorithms=None, options=None, **kwargs):
        options = options or {}
        try:
            header_b64, body_b64, signature_b64 = str(token).split(".")
            header = json.loads(_b64url_decode(header_b64))
            payload = json.loads(_b64url_decode(body_b64))
            signature = _b64url_decode(signature_b64)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DecodeError("token 格式错误") from exc

        if header.get("alg") != "HS256":
            raise InvalidTokenError("不支持的算法")
        expected = hmac.new(
            str(key).encode("utf-8"), f"{header_b64}.{body_b64}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidSignatureError("签名不匹配")
        if algorithms is not None and header.get("alg") not in algorithms:
            raise InvalidTokenError("算法不被允许")
        for claim in options.get("require", ()):
            if claim not in payload:
                raise InvalidTokenError(f"缺少 claim: {claim}")
        if options.get("verify_exp", True) and isinstance(payload.get("exp"), (int, float)):
            if time.time() >= payload["exp"]:
                raise ExpiredSignatureError("token 已过期")
        return payload

    module.encode = encode
    module.decode = decode
    module.InvalidTokenError = InvalidTokenError
    module.DecodeError = DecodeError
    module.InvalidSignatureError = InvalidSignatureError
    module.ExpiredSignatureError = ExpiredSignatureError
    sys.modules["jwt"] = module
