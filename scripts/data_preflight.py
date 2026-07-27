#!/usr/bin/env python3
"""Read-only production data check used before and after a code-only rollout."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path


def database_path() -> Path:
    configured = os.getenv("BID_PLATFORM_DATABASE_URL", "").strip()
    if configured.startswith("sqlite:///"):
        return Path(configured.removeprefix("sqlite:///"))
    instance_dir = Path(os.getenv("BID_PLATFORM_INSTANCE_DIR", "instance"))
    return instance_dir / "platform.db"


def count_rows(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def inspect(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError(f"Database file does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"projects", "archived_projects", "api_tokens"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"Database is missing expected tables: {', '.join(missing)}")
        return {
            "database": str(path.resolve()),
            "projects": count_rows(connection, "projects"),
            "archived_projects": count_rows(connection, "archived_projects"),
            "api_tokens": count_rows(connection, "api_tokens"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only bid platform data preflight")
    parser.add_argument("--minimum-projects", type=int, default=0)
    args = parser.parse_args()
    result = inspect(database_path())
    result["ok"] = result["projects"] >= args.minimum_projects
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
