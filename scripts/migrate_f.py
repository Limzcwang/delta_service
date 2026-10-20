"""F/E 里程碑幂等迁移：users.nickname + rooms.deleted + skill squad_number。

生产库是已有表，``Tortoise.generate_schemas()`` 不会补列，因此需要本脚本。
可重复执行：列存在则跳过 ADD；回填只处理空 nickname。

用法（部署机）：
    DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... python scripts/migrate_f.py
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict


def _column_exists(cursor, table: str, column: str) -> bool:
    """MySQL 查 information_schema；sqlite（测试）回退 PRAGMA。"""
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
        row = cursor.fetchone()
        return bool(row and row[0])
    except Exception:
        cursor.execute(f"PRAGMA table_info({table})")
        rows = cursor.fetchall() or []
        return any(len(row) > 1 and row[1] == column for row in rows)


def _table_exists(cursor, table: str) -> bool:
    """空库（表尚未由应用创建）时返回 False，迁移安全跳过。"""
    try:
        cursor.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except Exception:
        return False


def migrate(connection: Any) -> Dict[str, int]:
    """执行迁移；返回本次新增列/回填数量，便于日志与测试断言。

    - 已迁移库：列存在 → 跳过 ADD，仅回填空 nickname（幂等）；
    - 空库：表还不存在 → 全部跳过（应用启动 generate_schemas 会建带新列的表）。
    """
    stats = {
        "nickname_added": 0,
        "nickname_backfilled": 0,
        "deleted_added": 0,
        "squad_number_added": 0,
        "skipped_missing_tables": 0,
    }
    cursor = connection.cursor()
    try:
        if not _table_exists(cursor, "users"):
            stats["skipped_missing_tables"] += 1
        else:
            if not _column_exists(cursor, "users", "nickname"):
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN nickname VARCHAR(32) "
                    "NOT NULL DEFAULT ''"
                )
                stats["nickname_added"] = 1

            # 老账号昵称默认=username；已迁移/已自定义的行不覆盖
            cursor.execute(
                "UPDATE users SET nickname = username "
                "WHERE nickname IS NULL OR nickname = ''"
            )
            stats["nickname_backfilled"] = max(int(cursor.rowcount or 0), 0)

        if not _table_exists(cursor, "rooms"):
            stats["skipped_missing_tables"] += 1
        else:
            if not _column_exists(cursor, "rooms", "deleted"):
                cursor.execute(
                    "ALTER TABLE rooms ADD COLUMN deleted TINYINT(1) "
                    "NOT NULL DEFAULT 0"
                )
                stats["deleted_added"] = 1

        # E1 扩展：技能共享队伍编号（1/2/3 或 NULL，允许重复）
        if not _table_exists(cursor, "room_skill_states"):
            stats["skipped_missing_tables"] += 1
        else:
            if not _column_exists(cursor, "room_skill_states", "squad_number"):
                cursor.execute(
                    "ALTER TABLE room_skill_states ADD COLUMN "
                    "squad_number TINYINT NULL"
                )
                stats["squad_number_added"] = 1

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    return stats


def build_connection():
    import pymysql

    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset=os.getenv("DB_CHARSET", "utf8mb4"),
        autocommit=False,
    )


def main() -> int:
    try:
        connection = build_connection()
    except KeyError as exc:
        print(f"缺少数据库环境变量: {exc}", file=sys.stderr)
        return 2
    try:
        stats = migrate(connection)
        print(f"F 迁移完成: {stats}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
