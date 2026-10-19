"""运营预置底图（assets/backgrounds/manifest.json）。

manifest 格式（示例见 assets/backgrounds/manifest.example.json）：

    {"backgrounds": [{"id": "bg_default_01", "name": "默认", "url": "...",
                      "width": 1920, "height": 1080}]}

文件不存在/损坏时返回空列表（不阻断服务）；创建/修改房间时用 ids 校验。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

_logger = logging.getLogger("delta_service")

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = _REPO_ROOT / "assets" / "backgrounds" / "manifest.json"


def manifest_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path)
    env_path = os.getenv("BACKGROUNDS_MANIFEST_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_MANIFEST_PATH


def _normalize_background(item) -> Optional[Dict]:
    if not isinstance(item, dict):
        return None
    background_id = item.get("id")
    url = item.get("url")
    width = item.get("width")
    height = item.get("height")
    if not isinstance(background_id, str) or not background_id:
        return None
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    if width <= 0 or height <= 0:
        return None
    normalized = {
        "id": background_id,
        "url": url,
        "width": width,
        "height": height,
    }
    if isinstance(item.get("name"), str) and item["name"]:
        normalized["name"] = item["name"]
    return normalized


def load_backgrounds(path: Optional[str] = None) -> List[Dict]:
    """读取底图列表；文件缺失/损坏/格式不对时返回空列表。"""
    target = manifest_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("读取底图 manifest 失败: %s", exc)
        return []

    if isinstance(payload, dict):
        raw_items = payload.get("backgrounds", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    if not isinstance(raw_items, list):
        return []

    backgrounds = []
    for item in raw_items:
        normalized = _normalize_background(item)
        if normalized is None:
            _logger.warning("忽略非法底图条目: %r", item)
            continue
        backgrounds.append(normalized)
    return backgrounds


def background_ids(path: Optional[str] = None) -> set:
    return {item["id"] for item in load_backgrounds(path)}


def find_background(background_id: str, path: Optional[str] = None) -> Optional[Dict]:
    for item in load_backgrounds(path):
        if item["id"] == background_id:
            return item
    return None
