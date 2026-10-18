"""离线单元测试辅助：为仓库运行期依赖安装最小 stub。

仓库本身未安装 fastapi / tortoise 等第三方依赖时，测试也必须能跑通，
因此这里用 ``sys.modules`` 注入最小替身，只保留被测试代码实际会用到的名字。
"""

import importlib.util
import json
import logging
import sys
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
    """让 ``from tortoise.backends.base.client import BaseDBAsyncClient`` 可导入。"""
    if "tortoise.backends.base.client" in sys.modules:
        return

    tortoise = _replace_module("tortoise")
    backends = _replace_module("tortoise.backends")
    base = _replace_module("tortoise.backends.base")
    client = _replace_module("tortoise.backends.base.client")

    class BaseDBAsyncClient:
        """占位基类。"""

    client.BaseDBAsyncClient = BaseDBAsyncClient
    tortoise.backends = backends
    backends.base = base
    base.client = client


def install_main_dependency_stubs():
    """安装 main.py 依赖的第三方/业务模块替身，避免导入时连数据库或网络。"""
    install_tortoise_stub()

    fastapi = sys.modules.get("fastapi")
    if fastapi is None or not hasattr(fastapi, "Request"):
        fastapi = _replace_module("fastapi", Request=object)
    else:
        setattr(fastapi, "Request", object)

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
