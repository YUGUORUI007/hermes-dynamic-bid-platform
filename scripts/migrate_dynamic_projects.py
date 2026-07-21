from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_app.database import session_scope
from platform_app.services.dynamic_migration import migrate_projects_to_dynamic_content


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy bid projects to dynamic Schema 1.0")
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag the command is a dry run.")
    parser.add_argument("--actor", default="migration", help="Actor name stored in project content history")
    args = parser.parse_args()
    with session_scope() as session:
        result = migrate_projects_to_dynamic_content(session, apply=args.apply, actor_name=args.actor)
        if not args.apply:
            session.rollback()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
