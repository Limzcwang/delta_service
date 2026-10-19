"""运营干员配置下发（配置中心，E+ / H4）。

数据来源：镜像内 ``assets/operators.json``，启动时读入内存并计算版本；
文件变更后重启生效（不做热加载）。版本 = 文件内容 sha256 前 16 位，
内容变则版本/ETag 必变。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from log.logger import logger

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "operators.json"
)


class OperatorConfigError(Exception):
    """配置缺失/非法；HTTP 层降级为 503。"""


class OperatorConfigCenter:
    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else self._default_path()
        self.version: Optional[str] = None
        self.etag: Optional[str] = None
        self.updated_at: Optional[int] = None
        self.operators = []
        self.meta: Dict[str, Any] = {}
        self.loaded = False

    @staticmethod
    def _default_path() -> Path:
        env_path = os.getenv("OPERATORS_CONFIG_PATH")
        return Path(env_path) if env_path else DEFAULT_CONFIG_PATH

    def load(self, path: Optional[str] = None) -> "OperatorConfigCenter":
        target = Path(path) if path else self.path
        try:
            raw = target.read_bytes()
        except FileNotFoundError as exc:
            raise OperatorConfigError(f"缺少干员配置文件: {target}") from exc
        except OSError as exc:
            raise OperatorConfigError(f"读取干员配置失败: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperatorConfigError("干员配置不是合法 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("operators"), list
        ):
            raise OperatorConfigError("干员配置缺少 operators 数组")
        if not payload["operators"]:
            # 空配置绝不能下发给客户端，否则客户端会误以为有效并覆盖本地配置
            raise OperatorConfigError("operators 不能为空")
        meta = payload.get("_meta")
        if not isinstance(meta, dict):
            raise OperatorConfigError("缺少或非法 _meta")

        seen_ids = set()
        for index, operator in enumerate(payload["operators"]):
            if not isinstance(operator, dict):
                raise OperatorConfigError(f"operators[{index}] 必须是对象")
            operator_id = operator.get("id")
            if not isinstance(operator_id, str) or not operator_id:
                raise OperatorConfigError(f"operators[{index}].id 非法")
            if operator_id in seen_ids:
                raise OperatorConfigError(f"operator id 重复: {operator_id}")
            seen_ids.add(operator_id)
            if not isinstance(operator.get("name"), str) or not operator["name"]:
                raise OperatorConfigError(f"operators[{index}].name 非法")
            skills = operator.get("skills")
            if not isinstance(skills, list) or len(skills) != 3:
                raise OperatorConfigError(f"operators[{index}].skills 必须 3 条")
            slots = set()
            for skill in skills:
                if not isinstance(skill, dict):
                    raise OperatorConfigError(f"operators[{index}].skills 项非法")
                slot = skill.get("slot")
                if slot not in (0, 1, 2) or slot in slots:
                    raise OperatorConfigError(f"operators[{index}] slot 非法/重复")
                slots.add(slot)
                if not isinstance(skill.get("name"), str) or not skill["name"]:
                    raise OperatorConfigError(f"operators[{index}] skill.name 非法")
                total_cd_ms = skill.get("total_cd_ms")
                if total_cd_ms is not None and (
                    not isinstance(total_cd_ms, int)
                    or isinstance(total_cd_ms, bool)
                    or total_cd_ms < 0
                ):
                    raise OperatorConfigError(
                        f"operators[{index}] total_cd_ms 非法"
                    )
                max_charges = skill.get("max_charges")
                if (
                    not isinstance(max_charges, int)
                    or isinstance(max_charges, bool)
                    or not 1 <= max_charges <= 9
                ):
                    raise OperatorConfigError(
                        f"operators[{index}] max_charges 非法"
                    )

        self.path = target
        self.version = hashlib.sha256(raw).hexdigest()[:16]
        self.etag = self.version
        self.updated_at = int(target.stat().st_mtime * 1000)
        self.operators = payload["operators"]
        self.meta = meta
        self.loaded = True
        logger.info(
            "干员配置已加载: version=%s operators=%s",
            self.version,
            len(self.operators),
        )
        return self

    def get(self) -> Dict[str, Any]:
        if not self.loaded:
            self.load()
        return {
            "version": self.version,
            "etag": self.etag,
            "updated_at": self.updated_at,
            "operators": self.operators,
            "meta": self.meta,
        }


config_center = OperatorConfigCenter()


def load_operator_config(path: Optional[str] = None) -> bool:
    """启动时调用；失败只记日志（接口运行期再降级 503）。"""
    try:
        config_center.load(path)
        return True
    except OperatorConfigError as exc:
        logger.warning("干员配置加载失败: %s", exc)
        return False
