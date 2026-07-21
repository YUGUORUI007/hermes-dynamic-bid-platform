import unittest

from platform_app.models import Project
from platform_app.services.dynamic_migration import migrate_projects_to_dynamic_content


class FakeQuery:
    def __init__(self, projects):
        self.projects = projects

    def order_by(self, *_):
        return self

    def all(self):
        return self.projects


class FakeSession:
    def __init__(self, projects):
        self.projects = projects
        self.added = []

    def query(self, model):
        self.assert_model = model
        return FakeQuery(self.projects)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass


class DynamicMigrationTests(unittest.TestCase):
    def test_empty_database_is_a_successful_noop(self):
        result = migrate_projects_to_dynamic_content(FakeSession([]), apply=True)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["failed_count"], 0)

    def test_dry_run_does_not_modify_project(self):
        project = Project(id=10, name="旧项目", status="tracking", notes="旧备注")
        session = FakeSession([project])
        result = migrate_projects_to_dynamic_content(session, apply=False)
        self.assertEqual(result["migrated_project_ids"], [10])
        self.assertIsNone(project.dynamic_content)
        self.assertEqual(session.added, [])

    def test_apply_is_idempotent(self):
        project = Project(id=11, name="旧项目", status="tracking", buyer="采购人")
        session = FakeSession([project])
        first = migrate_projects_to_dynamic_content(session, apply=True)
        second = migrate_projects_to_dynamic_content(session, apply=True)
        self.assertEqual(first["migrated_count"], 1)
        self.assertEqual(second["skipped_project_ids"], [11])
        self.assertIn("legacy-overview", project.dynamic_content)
        self.assertEqual(len(session.added), 1)

    def test_invalid_legacy_project_is_reported_without_stopping_batch(self):
        broken = Project(id=12, name=None, status="tracking")
        valid = Project(id=13, name="可迁移项目", status="tracking")
        session = FakeSession([broken, valid])
        result = migrate_projects_to_dynamic_content(session, apply=False)
        self.assertEqual(result["migrated_project_ids"], [13])
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["failures"][0]["project_id"], 12)


if __name__ == "__main__":
    unittest.main()
