"""启动前数据库迁移 + 关键列自检（E1 squad_number 线上缺列教训）。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Tuple

from log.logger import log_exception, logger
from scripts.migrate_f import _column_exists, _table_exists, build_connection, migrate

AUTO_MIGRATE_ENV = "AUTO_MIGRATE_ON_STARTUP"

# 新增列必须同时加入这里与 scripts/migrate_f.py，启动自检负责发现漏迁移
REQUIRED_COLUMNS = {
    "users": ("nickname",),
    "rooms": ("deleted",),
    "room_skill_states": ("squad_number",),
    "diagnostics_events": (
        "account_user_id",
        "client_diag_id",
        "session_id",
        "kind",
        "payload",
        "created_at",
    ),
}


def auto_migrate_enabled() -> bool:
    return os.getenv(AUTO_MIGRATE_ENV, "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def check_required_columns(connection: Any) -> List[str]:
    """返回缺失的 table.column 列表；空列表表示关键列齐全。"""
    missing = []
    cursor = connection.cursor()
    try:
        for table, columns in REQUIRED_COLUMNS.items():
            if not _table_exists(cursor, table):
                missing.append(f"{table}.*")
                continue
            for column in columns:
                if not _column_exists(cursor, table, column):
                    missing.append(f"{table}.{column}")
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    return missing


def _run_sync_migration(enabled: bool) -> Tuple[Dict[str, int], List[str]]:
    connection = build_connection()
    try:
        stats = migrate(connection) if enabled else {}
        missing = check_required_columns(connection)
        return stats, missing
    finally:
        connection.close()


async def run_startup_migrations() -> None:
    """默认启动自动迁移；任何失败只告警，不阻断服务启动。"""
    enabled = auto_migrate_enabled()
    try:
        stats, missing = await asyncio.to_thread(_run_sync_migration, enabled)
    except Exception as exc:
        log_exception(exc, "启动数据库迁移失败（不阻断服务启动）")
        return

    if enabled:
        logger.info("启动数据库迁移完成: %s", stats)
    else:
        logger.info("%s=false，跳过启动迁移", AUTO_MIGRATE_ENV)
    if missing:
        logger.error(
            "数据库关键列缺失（可能未执行迁移）: %s；请运行 "
            "python scripts/migrate_f.py",
            ", ".join(missing),
        )
    else:
        logger.info("数据库关键列自检通过")
