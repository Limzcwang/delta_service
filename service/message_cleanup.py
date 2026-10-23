"""聊天消息/画板/诊断保留期清理任务（应用 lifespan 周期执行）。

不依赖 legacy 8765 WS 开关：由 FastAPI 应用 startup/shutdown 事件启动/停止。
间隔 ``MESSAGE_CLEANUP_INTERVAL_HOURS``（默认 24 小时）；启动后先跑一次。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Awaitable, Callable, Optional

from log.logger import log_exception, logger
from service import canvas as canvas_service
from service import diagnostics as diagnostics_service
from service import messages as messages_service

DEFAULT_INTERVAL_HOURS = 24.0
ENV_INTERVAL_HOURS = "MESSAGE_CLEANUP_INTERVAL_HOURS"


def resolve_interval_hours(raw=None) -> float:
    """解析清理间隔；非法/非正数回退默认值并告警。"""
    value = raw if raw is not None else os.getenv(ENV_INTERVAL_HOURS, str(DEFAULT_INTERVAL_HOURS))
    try:
        hours = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "%s 非法（%r），使用默认 %.0f 小时", ENV_INTERVAL_HOURS, value, DEFAULT_INTERVAL_HOURS
        )
        return DEFAULT_INTERVAL_HOURS
    if hours <= 0:
        logger.warning(
            "%s 必须为正数（%r），使用默认 %.0f 小时", ENV_INTERVAL_HOURS, value, DEFAULT_INTERVAL_HOURS
        )
        return DEFAULT_INTERVAL_HOURS
    return hours


class MessageCleanupTask:
    """周期性删除超过保留期的聊天消息；可注入 cleanup/sleep/clock 便于测试。"""

    def __init__(
        self,
        interval_hours: Optional[float] = None,
        cleanup: Optional[Callable[[], Awaitable[int]]] = None,
        canvas_cleanup: Optional[Callable[[], Awaitable[int]]] = None,
        diagnostics_cleanup: Optional[Callable[[], Awaitable[int]]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.interval_hours = resolve_interval_hours(interval_hours)
        self.interval_seconds = self.interval_hours * 3600
        # 消息 30 天 + 画板快照 7 天，一并周期清理
        self._cleanups = [
            ("messages", cleanup or messages_service.cleanup_old_messages),
            ("canvas", canvas_cleanup or canvas_service.cleanup_canvas_snapshots),
            ("diagnostics", diagnostics_cleanup or diagnostics_service.cleanup_old_events),
        ]
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        self._task: Optional[asyncio.Task] = None

    async def run_once(self) -> int:
        """执行一次清理；任一清理异常只记日志（不打断周期任务）。"""
        started = self._clock()
        total = 0
        for name, cleanup in self._cleanups:
            try:
                deleted = await cleanup()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_exception(exc, f"{name} 保留期清理失败")
                continue
            total += int(deleted or 0)
            logger.debug("%s 清理完成: 删除 %s 条", name, deleted)
        logger.debug("保留期清理完成: 共删除 %s 条，耗时 %.3fs", total, self._clock() - started)
        return total

    async def _loop(self) -> None:
        while True:
            await self.run_once()
            await self._sleep(self.interval_seconds)

    def start(self) -> asyncio.Task:
        """启动后台任务（幂等）；启动后立即执行第一次清理。"""
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._loop(), name="chat-message-cleanup")
        logger.info("聊天消息清理任务已启动: 每 %.2f 小时", self.interval_hours)
        return self._task

    async def stop(self) -> None:
        """优雅停止：取消并等待任务结束（幂等）。"""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("聊天消息清理任务已停止")
