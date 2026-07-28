import json
from datetime import datetime, timedelta
import unittest

from platform_app.models import Project
from platform_app.services.dynamic_ui import project_attention_items, serialize_project_detail


class ProjectOverviewLogicTests(unittest.TestCase):
    def test_post_submission_requires_confirmation_instead_of_archiving(self):
        now = datetime(2026, 7, 28, 9, 0)
        project = Project(id=1, name="Submission check", submission_datetime=now - timedelta(hours=2), status="ready_deliver")
        items = project_attention_items(project, now)
        self.assertTrue(any(item["kind"] == "confirmation" for item in items))

    def test_post_bid_opening_requires_result_confirmation(self):
        now = datetime(2026, 7, 28, 9, 0)
        project = Project(id=2, name="Result check", bid_datetime=now - timedelta(days=1), status="submitted")
        items = project_attention_items(project, now)
        self.assertTrue(any("result" in item["label"].lower() or "结果" in item["label"] for item in items))

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
