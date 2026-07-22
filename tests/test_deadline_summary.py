import json
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
        self.assertEqual(entries[0]["project_title"], "到期节点测试项目")
        self.assertEqual(entries[0]["label"], "报名截止")

    def test_can_include_past_dates_for_calendar_history(self):
        now = datetime(2026, 7, 22, 9, 0)
        project = Project(id=8, name="历史节点测试", signup_deadline=now - timedelta(days=2))

        self.assertEqual(project_deadline_entries(project, now), [])
        entries = project_deadline_entries(project, now, include_past=True)
        self.assertEqual(entries[0]["label"], "报名截止")

    def test_collects_calendar_entries_from_dynamic_timeline(self):
        now = datetime(2026, 7, 22, 9, 0)
        project = Project(
            id=9,
            name="动态时间线项目",
            dynamic_content=json.dumps(
                {
                    "workflow": {},
                    "sections": [
                        {
                            "id": "key-dates",
                            "title": "关键节点",
                            "blocks": [
                                {
                                    "id": "schedule",
                                    "type": "timeline",
                                    "items": [
                                        {"label": "投标文件递交", "at": "2026-07-25 09:30"},
                                        {"label": "无效日期", "at": "待定"},
                                    ],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        entries = project_deadline_entries(project, now, within_days=7)

        self.assertEqual([(item["label"], item["deadline_at"]) for item in entries], [("投标文件递交", datetime(2026, 7, 25, 9, 30))])

    def test_hides_completed_signup_and_deposit_deadlines(self):
        now = datetime(2026, 7, 22, 9, 0)
        registered = Project(id=10, name="已报名项目", status="registered", signup_deadline=now + timedelta(days=1))
        paid_deposit = Project(
            id=11,
            name="保证金已汇出项目",
            status="tracking",
            deposit_deadline=now + timedelta(days=1),
            dynamic_content=json.dumps({"workflow": {"deposit": "done"}, "sections": []}, ensure_ascii=False),
        )

        self.assertEqual(project_deadline_entries(registered, now, within_days=7), [])
        self.assertEqual(project_deadline_entries(paid_deposit, now, within_days=7), [])

    def test_highlights_overdue_deposit_refund_after_fourteen_days(self):
        now = datetime(2026, 7, 22, 9, 0)
        overdue = workflow_status_items({}, "submitted", now - timedelta(days=17), now)
        refund = next(item for item in overdue if item["id"] == "deposit_refund")
        self.assertEqual(refund["tone"], "danger")
        self.assertEqual(refund["refund_overdue_days"], 3)

        completed = workflow_status_items({"workflow": {"deposit_refund": "done"}}, "submitted", now - timedelta(days=17), now)
        self.assertEqual(next(item for item in completed if item["id"] == "deposit_refund")["tone"], "success")

    def test_only_shows_prequalification_when_the_project_requires_it(self):
        default_items = workflow_status_items({}, "tracking")
        self.assertNotIn("prequalification", [item["id"] for item in default_items])

        required_items = workflow_status_items({"workflow": {"prequalification": "pending"}}, "tracking")
        self.assertIn("prequalification", [item["id"] for item in required_items])

        not_required_items = workflow_status_items({"workflow": {"prequalification": "not_applicable"}}, "tracking")
        self.assertNotIn("prequalification", [item["id"] for item in not_required_items])


if __name__ == "__main__":
    unittest.main()
