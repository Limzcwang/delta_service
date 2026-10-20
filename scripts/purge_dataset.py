"""按日期清理内部数据集样本（手动执行）。

默认删除 30 天前的 ``<root>/YYYYMMDD`` 目录；可指定确切日期。
同时尝试删除 ``dataset_samples`` 中对应日期的记录（DB 不可用则仅清文件）。

用法：
    python scripts/purge_dataset.py --before-days 30 --dry-run
    python scripts/purge_dataset.py --date 20260101
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATE_RE = re.compile(r"^\d{8}$")


def date_dirs(root: Path) -> List[str]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and DATE_RE.match(child.name)
    )


def purge(
    root: Path,
    before_days: Optional[int] = 30,
    exact_date: Optional[str] = None,
    dry_run: bool = False,
    purge_db: bool = False,
) -> List[str]:
    root = Path(root)
    keys = date_dirs(root)
    if exact_date:
        if not DATE_RE.match(exact_date):
            raise ValueError("date 必须是 YYYYMMDD")
        targets = [key for key in keys if key == exact_date]
    else:
        days = 30 if before_days is None else max(int(before_days), 0)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        targets = [key for key in keys if key < cutoff]

    if not dry_run:
        for key in targets:
            shutil.rmtree(root / key, ignore_errors=True)
        if purge_db and targets:
            _purge_db(targets)
    return targets


def _purge_db(keys: List[str]) -> None:  # pragma: no cover - 需真实 DB
    try:
        import pymysql
    except ImportError:
        print("未安装 pymysql，跳过 DB 清理")
        return
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", ""),
            charset=os.getenv("DB_CHARSET", "utf8mb4"),
        )
    except Exception as exc:
        print(f"DB 连接失败，跳过记录清理: {exc}")
        return
    try:
        with conn.cursor() as cursor:
            for key in keys:
                cursor.execute("DELETE FROM dataset_samples WHERE date_key = %s", (key,))
        conn.commit()
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="清理内部数据集样本")
    parser.add_argument("--root", default=os.getenv("DATASET_DIR", "/data/datasets"))
    parser.add_argument("--date", help="只清理指定 YYYYMMDD")
    parser.add_argument("--before-days", type=int, default=30, help="清理早于 N 天（默认 30）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge-db", action="store_true", help="同时删除 DB 记录")
    args = parser.parse_args(argv)

    removed = purge(
        Path(args.root),
        before_days=args.before_days,
        exact_date=args.date,
        dry_run=args.dry_run,
        purge_db=args.purge_db,
    )
    action = "将删除" if args.dry_run else "已删除"
    print(f"{action} {len(removed)} 个日期目录: {', '.join(removed) or '无'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
