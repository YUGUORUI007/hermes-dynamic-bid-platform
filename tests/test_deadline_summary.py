from datetime import datetime, timedelta
import unittest

from platform_app.models import Project
from platform_app.services.dynamic_ui import project_deadline_entries, workflow_status_items


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

    def test_can_include_past_dates_for_calendar_history(self):
        now = datetime(2026, 7, 22, 9, 0)
        project = Project(id=8, name="历史节点测试", signup_deadline=now - timedelta(days=2))

        self.assertEqual(project_deadline_entries(project, now), [])
        entries = project_deadline_entries(project, now, include_past=True)
        self.assertEqual(entries[0]["label"], "报名截止")

    def test_highlights_overdue_deposit_refund_after_fourteen_days(self):
        now = datetime(2026, 7, 22, 9, 0)
        overdue = workflow_status_items({}, "submitted", now - timedelta(days=17), now)
        refund = next(item for item in overdue if item["id"] == "deposit_refund")
        self.assertEqual(refund["tone"], "danger")
        self.assertEqual(refund["refund_overdue_days"], 3)

        completed = workflow_status_items({"workflow": {"deposit_refund": "done"}}, "submitted", now - timedelta(days=17), now)
        self.assertEqual(next(item for item in completed if item["id"] == "deposit_refund")["tone"], "success")


if __name__ == "__main__":
    unittest.main()
