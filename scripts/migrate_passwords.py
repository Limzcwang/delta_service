"""一次性存量口令迁移脚本：把 users 表中未哈希的口令就地替换为 scrypt 哈希。

用法::

    # 1) 先演练：只统计，不写库
    DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python scripts/migrate_passwords.py --dry-run

    # 2) 确认后正式迁移（可重复执行，已哈希的行会自动跳过）
    python scripts/migrate_passwords.py

设计说明：
- 迁移逻辑（plan_migration / apply_migration）与数据库 IO（TortoiseUserStore）
  分离，单元测试只用 fake store 即可离线运行；
- 幂等：只处理 is_hashed() 为假的行，重复执行不会二次哈希；
- 不打印任何口令或哈希值，只输出统计。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Tuple

# 允许 `python scripts/migrate_passwords.py` 直接运行（把仓库根目录加入 sys.path）。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from security.passwords import hash_password, is_hashed


@dataclass
class MigrationPlan:
    """迁移计划：只包含需要写入的行。"""

    updates: List[Tuple[Any, str]] = field(default_factory=list)
    already_hashed: int = 0
    empty_password: int = 0
    total: int = 0

    @property
    def pending(self) -> int:
        """待迁移行数。"""
        return len(self.updates)


def _get_field(user: Any, name: str) -> Any:
    if isinstance(user, dict):
        return user.get(name)
    return getattr(user, name, None)


def plan_migration(users: Iterable[Any]) -> MigrationPlan:
    """根据用户行生成迁移计划（纯逻辑，不写库）。"""
    plan = MigrationPlan()
    for user in users:
        plan.total += 1
        stored = _get_field(user, "password")
        if stored is None or stored == "":
            plan.empty_password += 1
            continue
        if is_hashed(stored):
            plan.already_hashed += 1
            continue
        plan.updates.append((_get_field(user, "id"), hash_password(stored)))
    return plan


async def apply_migration(
    plan: MigrationPlan,
    update_password: Callable[[Any, str], Awaitable[Any]],
    dry_run: bool = False,
) -> int:
    """执行迁移计划，返回实际写入行数；dry_run=True 时只统计不写。"""
    if dry_run:
        return 0

    applied = 0
    for user_id, hashed_password in plan.updates:
        await update_password(user_id, hashed_password)
        applied += 1
    return applied


class TortoiseUserStore:
    """数据库 IO 适配层：包含软删除的整张 users 表都迁移，避免遗留明文。"""

    async def list_users(self) -> List[Any]:
        from dao.user import User

        return await User.all()

    async def update_password(self, user_id: Any, hashed_password: str) -> None:
        from dao.user import User

        await User.filter(id=user_id).update(password=hashed_password)


async def migrate(store: Any, dry_run: bool = False) -> Tuple[MigrationPlan, int]:
    """从 store 读取用户、生成计划并按需写入。"""
    users = await store.list_users()
    plan = plan_migration(users)
    applied = await apply_migration(plan, store.update_password, dry_run=dry_run)
    return plan, applied


def _print_summary(plan: MigrationPlan, applied: int, dry_run: bool) -> None:
    print(f"用户总数: {plan.total}")
    print(f"已是哈希（跳过）: {plan.already_hashed}")
    print(f"空口令（跳过）: {plan.empty_password}")
    if dry_run:
        print(f"[dry-run] 待迁移明文口令: {plan.pending} 行（未写库）")
    else:
        print(f"已迁移: {applied} 行")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 users 表中未哈希的明文口令迁移为 scrypt 哈希（幂等）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将迁移多少行，不写数据库",
    )
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    # 延迟导入：模块被测试导入时不会要求数据库环境变量，也不会连接数据库。
    from tortoise import Tortoise

    from config.database import DATABASE_CONFIG

    await Tortoise.init(config=DATABASE_CONFIG)
    try:
        plan, applied = await migrate(TortoiseUserStore(), dry_run=args.dry_run)
    finally:
        await Tortoise.close_connections()

    _print_summary(plan, applied, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
