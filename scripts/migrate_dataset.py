"""内部数据集表幂等初始化（已有库首次上线时执行）。

应用启动的 generate_schemas 也会建表；本脚本用于只跑迁移、不重启应用的场景。
可重复执行（CREATE TABLE IF NOT EXISTS）。

用法：
    DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... python scripts/migrate_dataset.py
"""

from __future__ import annotations

from typing import Any

try:  # 作为脚本直接执行 vs 测试中 import
    from scripts.migrate_f import build_connection
except ImportError:  # pragma: no cover
    from migrate_f import build_connection

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dataset_samples (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  username VARCHAR(32) NOT NULL,
  sample_id VARCHAR(32) NOT NULL UNIQUE,
  date_key CHAR(8) NOT NULL,
  room_code VARCHAR(16) NULL,
  operator_id VARCHAR(32) NOT NULL,
  resolution VARCHAR(32) NOT NULL,
  hud_scale DOUBLE NOT NULL,
  captured_at BIGINT NOT NULL,
  ocr JSON NOT NULL,
  squad_number TINYINT NULL,
  client_version VARCHAR(32) NOT NULL,
  rel_dir VARCHAR(128) NOT NULL,
  files JSON NOT NULL,
  total_bytes INT NOT NULL DEFAULT 0,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY idx_dataset_date (date_key, created_at),
  KEY idx_dataset_user_date (user_id, date_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def migrate_dataset(connection: Any) -> int:
    """执行幂等建表；返回执行语句数（便于日志/测试）。"""
    cursor = connection.cursor()
    try:
        cursor.execute(CREATE_SQL)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    return 1


if __name__ == "__main__":  # pragma: no cover
    conn = build_connection()
    try:
        migrate_dataset(conn)
        print("dataset_samples 表已就绪")
    finally:
        conn.close()
