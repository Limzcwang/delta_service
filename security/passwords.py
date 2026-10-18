"""口令哈希工具（仅使用 Python 标准库，不引入第三方依赖）。

存储格式：``scrypt$N$r$p$<salt_b64>$<hash_b64>``
- N=2**14, r=8, p=1, dklen=32, salt=16 字节随机
- 同一口令每次哈希结果不同（随机 salt）
- 校验使用 ``hmac.compare_digest`` 做常量时间比较
- 兼容历史明文口令：``verify_password`` 能识别并比对，但调用方应尽快重哈希
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from typing import Optional, Tuple

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
HASH_PREFIX = "scrypt"

# 防御库中被写入畸形/恶意的高开销参数导致内存放大。
_MAX_N = 2**20
_MAX_R = 32
_MAX_P = 16

_HASH_RE = re.compile(
    r"^scrypt\$(\d+)\$(\d+)\$(\d+)\$([A-Za-z0-9+/]+=*)\$([A-Za-z0-9+/]+=*)$"
)


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def parse_hashed_password(stored: str) -> Optional[Tuple[int, int, int, bytes, bytes]]:
    """解析哈希串，返回 (N, r, p, salt, expected_hash)；不合法返回 None。"""
    if not isinstance(stored, str):
        return None
    match = _HASH_RE.match(stored)
    if match is None:
        return None

    n, r, p = (int(match.group(index)) for index in (1, 2, 3))
    if not (2 <= n <= _MAX_N and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P):
        return None
    if n & (n - 1):  # scrypt 要求 N 是 2 的幂
        return None

    try:
        salt = _b64decode(match.group(4))
        expected = _b64decode(match.group(5))
    except (ValueError, TypeError):
        return None

    if not salt or not expected:
        return None
    return n, r, p, salt, expected


def is_hashed(stored: str) -> bool:
    """判断存储值是否是本模块生成的 scrypt 哈希。"""
    return parse_hashed_password(stored) is not None


def hash_password(raw_password: str) -> str:
    """把明文口令哈希为 ``scrypt$N$r$p$salt$hash``。"""
    if not isinstance(raw_password, str) or raw_password == "":
        raise ValueError("password 必须是非空字符串")

    salt = os.urandom(SALT_BYTES)
    derived = hashlib.scrypt(
        raw_password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "{}${}${}${}${}${}".format(
        HASH_PREFIX,
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        _b64encode(salt),
        _b64encode(derived),
    )


def verify_password(raw_password: str, stored: str) -> bool:
    """校验口令。

    - stored 是 scrypt 哈希：按哈希参数重新派生并使用常量时间比较；
    - stored 是历史明文：使用 ``hmac.compare_digest`` 比对（过渡期兼容）；
    - 形如哈希但无法解析的存储值一律拒绝，避免把损坏哈希当明文处理。
    """
    if not isinstance(raw_password, str) or not isinstance(stored, str) or stored == "":
        return False

    parsed = parse_hashed_password(stored)
    if parsed is None:
        if stored.startswith(HASH_PREFIX + "$"):
            return False
        # 历史明文口令（过渡期）：登录成功后调用方会立即重哈希。
        return hmac.compare_digest(
            raw_password.encode("utf-8"), stored.encode("utf-8")
        )

    n, r, p, salt, expected = parsed
    try:
        derived = hashlib.scrypt(
            raw_password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(derived, expected)
