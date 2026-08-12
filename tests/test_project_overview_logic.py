import json
from datetime import datetime, timedelta
import unittest

from platform_app.models import Project
from platform_app.services.dynamic_ui import (
    apply_auto_lifecycle,
    project_attention_items,
    serialize_project_detail,
    suggest_auto_lifecycle,
    STATUS_LABELS,
)


class ProjectOverviewLogicTests(unittest.TestCase):
    def test_post_submission_auto_marks_submitted(self):
        now = datetime(2026, 7, 28, 9, 0)
        project = Project(
            id=1,
            name="Submission check",
            submission_datetime=now - timedelta(hours=2),
            status="ready_deliver",
            dynamic_content=json.dumps({"workflow": {"delivery": "pending"}, "sections": []}, ensure_ascii=False),
        )
        suggestion = suggest_auto_lifecycle(project, now)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["to_status"], "submitted")
        apply_auto_lifecycle(project, now)
        self.assertEqual(project.status, "submitted")
        self.assertEqual(STATUS_LABELS[project.status], "已投")

    def test_post_bid_opening_auto_moves_to_result_pending(self):
        now = datetime(2026, 7, 28, 9, 0)
        project = Project(
            id=2,
            name="Result check",
            bid_datetime=now - timedelta(days=1),
            status="ready_deliver",
            dynamic_content=json.dumps({"workflow": {}, "sections": []}, ensure_ascii=False),
        )
        apply_auto_lifecycle(project, now)
        self.assertEqual(project.status, "result_pending")
        self.assertEqual(STATUS_LABELS[project.status], "已投待结果")
        items = project_attention_items(project, now)
        self.assertTrue(any("结果" in item["label"] for item in items))

    def test_detail_merges_duplicate_key_node_tabs(self):
        project = Project(
            id=3,
            name="Merged timeline",
            bid_datetime=datetime(2026, 8, 2, 9, 0),
            dynamic_content=json.dumps({"sections": [{"id": "dates", "title": "关键节点", "blocks": []}]}, ensure_ascii=False),
        )
        detail = serialize_project_detail(project)
        self.assertEqual([section["title"] for section in detail["content"]["sections"]].count("关键节点"), 1)


if __name__ == "__main__":
    unittest.main()
