"""房间聊天服务层（C）：内容校验、持久化、历史分页、限频、30 天清理。"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from dao.message import MessageDao
from service import rooms as rooms_service

MAX_CONTENT_LENGTH = 2000
CLIENT_MSG_ID_MAX_LENGTH = 128
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
DEFAULT_RETENTION_DAYS = 30

ERR_INVALID_MESSAGE = "INVALID_MESSAGE"
ERR_INVALID_PAGINATION = "INVALID_PAGINATION"
ERR_RATE_LIMITED = "RATE_LIMITED"

DEFAULT_CHAT_RATE_LIMIT = 5
DEFAULT_CHAT_RATE_WINDOW_SECONDS = 1.0


class MessageError(Exception):
    """聊天业务异常；携带字符串错误码与建议 HTTP 状态码。"""

    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MessageRateLimiter:
    """按 user_id 的内存滑动窗口限频（默认 5 条/秒），可注入时钟。"""

    def __init__(
        self,
        max_messages: int = DEFAULT_CHAT_RATE_LIMIT,
        window_seconds: float = DEFAULT_CHAT_RATE_WINDOW_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ):
        if max_messages <= 0:
            raise ValueError("max_messages 必须是正整数")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须是正数")
        self.max_messages = int(max_messages)
        self.window_seconds = float(window_seconds)
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

    def check(self, user_id: Any) -> bool:
        """记录一次发送；允许返回 True，超限返回 False（超限不记录）。"""
        key = str(user_id)
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self.max_messages:
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def retry_after(self, user_id: Any) -> float:
        key = str(user_id)
        now = self._clock()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) < self.max_messages:
                return 0.0
            return max(0.0, self.window_seconds - (now - hits[0]))

    def reset(self, user_id: Any = None) -> None:
        with self._lock:
            if user_id is None:
                self._hits.clear()
            else:
                self._hits.pop(str(user_id), None)


def normalize_content(content: Any) -> str:
    if not isinstance(content, str):
        raise MessageError(ERR_INVALID_MESSAGE, "消息内容必须是字符串")
    content = content.strip()
    if not 1 <= len(content) <= MAX_CONTENT_LENGTH:
        raise MessageError(
            ERR_INVALID_MESSAGE,
            f"消息内容长度需在 1-{MAX_CONTENT_LENGTH} 个字符之间",
        )
    return content


def normalize_client_msg_id(client_msg_id: Any) -> Optional[str]:
    if client_msg_id is None or client_msg_id == "":
        return client_msg_id
    if not isinstance(client_msg_id, str):
        raise MessageError(ERR_INVALID_MESSAGE, "client_msg_id 必须是字符串")
    if len(client_msg_id) > CLIENT_MSG_ID_MAX_LENGTH:
        raise MessageError(
            ERR_INVALID_MESSAGE,
            f"client_msg_id 长度不能超过 {CLIENT_MSG_ID_MAX_LENGTH}",
        )
    return client_msg_id


def _dt_to_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def message_to_dict(message) -> Dict[str, Any]:
    """序列化消息；对外只暴露 nickname（username 仅本人可见）。"""
    user = getattr(message, "user", None)
    user_id = getattr(user, "id", None)
    nickname = getattr(user, "nickname", None) or ""
    return {
        "id": str(message.id),
        "room_id": str(getattr(message, "room_id", "")),
        "user": {"id": str(user_id), "nickname": nickname},
        "content": message.content,
        "client_msg_id": getattr(message, "client_msg_id", None),
        "created_at": _dt_to_ms(getattr(message, "created_at", None)),
    }


async def send_message(
    user,
    room,
    content: Any,
    client_msg_id: Any = None,
) -> Dict[str, Any]:
    """校验成员/内容并持久化；返回可广播/返回给客户端的 dict。"""
    normalized_content = normalize_content(content)
    normalized_client_msg_id = normalize_client_msg_id(client_msg_id)

    await rooms_service.ensure_member(room, user)

    message = await MessageDao.create(
        id=uuid.uuid4().hex,
        room=room,
        user=user,
        content=normalized_content,
        client_msg_id=normalized_client_msg_id,
    )
    return message_to_dict(message)


def history_window_days(joined_at: Optional[datetime], now: Optional[datetime] = None) -> int:
    """F4：加入未满 7 天看最近 7 天，满 7 天看最近 30 天。"""
    if joined_at is None:
        return 7
    now = now or datetime.now()
    return 30 if (now - joined_at) >= timedelta(days=7) else 7


async def list_messages(
    room,
    before: Any = None,
    limit: int = DEFAULT_PAGE_SIZE,
    joined_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """历史消息倒序分页；历史窗口按成员 joined_at 计算。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise MessageError(ERR_INVALID_PAGINATION, "limit 必须是整数") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise MessageError(
            ERR_INVALID_PAGINATION,
            f"limit 需在 1-{MAX_PAGE_SIZE} 之间",
        )

    now = datetime.now()
    window_days = history_window_days(joined_at, now)
    since = now - timedelta(days=window_days)

    before_message = None
    if before is not None and before != "":
        if not isinstance(before, str):
            raise MessageError(ERR_INVALID_PAGINATION, "before 游标无效")
        before_message = await MessageDao.get_by_id_for_room(room.id, before)
        if before_message is None:
            raise MessageError(ERR_INVALID_PAGINATION, "before 游标无效")
        # 超出成员历史窗口的游标 → 400（契约见 PROTOCOL）
        if before_message.created_at < since:
            raise MessageError(ERR_INVALID_PAGINATION, "before 游标超出历史窗口")

    messages = await MessageDao.list_for_room(
        room.id, before_message, limit, since=since
    )
    has_more = len(messages) > limit
    page = messages[:limit]
    next_before = page[-1].id if has_more and page else None
    return {
        "items": [message_to_dict(message) for message in page],
        "has_more": has_more,
        "next_before": next_before,
    }


async def cleanup_old_messages(
    days: int = DEFAULT_RETENTION_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """删除超过保留期的消息（默认 30 天）；返回删除条数。暂不接定时任务。"""
    if not isinstance(days, int) or days <= 0:
        raise ValueError("days 必须是正整数")
    cutoff = (now or datetime.now()) - timedelta(days=days)
    return await MessageDao.delete_older_than(cutoff)
