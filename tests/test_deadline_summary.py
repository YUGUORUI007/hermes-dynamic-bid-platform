from datetime import datetime, timedelta
import unittest

from platform_app.models import Project
from platform_app.services.dynamic_ui import project_deadline_entries


class DeadlineSummaryTests(unittest.TestCase):
    def test_collects_registered_project_dates_with_tones(self):
        now = datetime(2026, 7, 22, 9, 0)
        project = Project(
            id=7,
            name="到期节点测试项目",
            signup_deadline=now + timedelta(days=1),
            clarification_deadline=now + timedelta(days=5),
            bid_datetime=now + timedelta(days=8),
        )

        entries = project_deadline_entries(project, now, within_days=7)

        self.assertEqual([entry["label"] for entry in entries], ["报名截止", "疑问澄清截止"])
        self.assertEqual(entries[0]["tone"], "danger")
        self.assertEqual(entries[1]["tone"], "warning")
        self.assertEqual(entries[0]["project_id"], 7)


if __name__ == "__main__":
    unittest.main()
