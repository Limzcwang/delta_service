"""登录限频（内存实现，按客户端 IP，滑动窗口，可注入时钟）。

配置（环境变量）：
- ``LOGIN_RATE_LIMIT_PER_MIN``：窗口内允许的登录尝试次数，默认 5；
- ``LOGIN_RATE_LIMIT_WINDOW_SECONDS``：窗口秒数，默认 60。

测试可注入 ``clock``（返回单调秒数的函数），避免 sleep。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, List, Optional

DEFAULT_LIMIT_PER_MIN = 5
DEFAULT_WINDOW_SECONDS = 60


class LoginRateLimiter:
    """按 key（通常是 IP）的滑动窗口限频器。"""

    def __init__(
        self,
        max_attempts: int = DEFAULT_LIMIT_PER_MIN,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.max_attempts = int(max_attempts)
        self.window_seconds = int(window_seconds)
        if self.max_attempts <= 0:
            raise ValueError("max_attempts 必须是正整数")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds 必须是正整数")
        self._clock = clock or time.monotonic
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> List[float]:
        cutoff = now - self.window_seconds
        hits = [hit for hit in self._hits.get(key, []) if hit > cutoff]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def check(self, key: str) -> bool:
        """记录一次尝试；允许返回 True，超限返回 False（不记录超限请求）。"""
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def retry_after(self, key: str) -> int:
        """距离窗口释放的秒数；未超限返回 0。"""
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) < self.max_attempts:
                return 0
            return max(0, int(self.window_seconds - (now - hits[0])) + 1)

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数，当前值: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数，当前值: {value!r}")
    return value


def build_login_rate_limiter() -> LoginRateLimiter:
    """按环境变量构造默认限频器（启动时调用）。"""
    return LoginRateLimiter(
        max_attempts=_env_positive_int("LOGIN_RATE_LIMIT_PER_MIN", DEFAULT_LIMIT_PER_MIN),
        window_seconds=_env_positive_int(
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS
        ),
    )
