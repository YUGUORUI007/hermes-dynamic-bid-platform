import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app.database import Base
from platform_app.models import Project


class DynamicMigrationSqliteTests(unittest.TestCase):
    def test_real_database_copy_migrates_then_skips(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="migration-copy-") as directory:
            copied = Path(directory) / "legacy-copy.db"
            fixture_engine = create_engine(f"sqlite:///{copied.as_posix()}")
            Base.metadata.create_all(fixture_engine)
            with Session(fixture_engine) as session:
                session.add_all([
                    Project(name="旧库行政中心物业项目", status="tracking", buyer="测试采购人", notes="旧固定字段备注"),
                    Project(name="旧库园区保安项目", status="submitted", owner_name="测试负责人", budget_amount="100 万元"),
                ])
                session.commit()
            fixture_engine.dispose()
            env = dict(os.environ)
            env["BID_PLATFORM_DATABASE_URL"] = f"sqlite:///{copied.as_posix()}"
            command = [sys.executable, str(root / "scripts" / "migrate_dynamic_projects.py"), "--apply", "--actor", "migration-copy-test"]
            first = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_result = json.loads(first.stdout)
            self.assertEqual(first_result["failed_count"], 0)
            self.assertGreater(first_result["migrated_count"], 0)

            second = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(second.returncode, 0, second.stderr)
            second_result = json.loads(second.stdout)
            self.assertEqual(second_result["failed_count"], 0)
            self.assertEqual(second_result["migrated_count"], 0)
            self.assertEqual(second_result["skipped_count"], second_result["total"])


if __name__ == "__main__":
    unittest.main()
