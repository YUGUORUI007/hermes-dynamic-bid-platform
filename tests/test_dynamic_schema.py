import json
import unittest
from pathlib import Path

from platform_app.dynamic_schema import BLOCK_TYPES, PROJECT_STATUSES, SchemaValidationError, payload_fingerprint, validate_project_payload


def sample_payload():
    return {
        "title": "行政中心物业服务项目",
        "status": "tracking",
        "owner": "余国锐",
        "summary": "需要在本周完成保证金与述标准备。",
        "schema_version": "1.0",
        "content": {
            "sections": [
                {
                    "id": "key-dates",
                    "title": "关键节点",
                    "visibility": "summary",
                    "blocks": [
                        {
                            "id": "submission",
                            "type": "timeline",
                            "items": [
                                {
                                    "label": "投标文件递交",
                                    "at": "2026-07-25 09:30",
                                    "status": "未完成",
                                    "tone": "danger",
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "staffing",
                    "title": "人员配置",
                    "blocks": [
                        {
                            "id": "staff-table",
                            "type": "table",
                            "columns": ["岗位", "人数"],
                            "rows": [["项目经理", 1]],
                        }
                    ],
                },
            ]
        },
    }


class DynamicSchemaTests(unittest.TestCase):
    def test_normalizes_project_specific_tabs(self):
        normalized = validate_project_payload(sample_payload())
        self.assertEqual(normalized["schema_version"], "1.0")
        self.assertEqual([section["title"] for section in normalized["content"]["sections"]], ["关键节点", "人员配置"])
        self.assertEqual(normalized["content"]["sections"][1]["blocks"][0]["rows"][0][1], 1)

    def test_rejects_duplicate_section_ids(self):
        payload = sample_payload()
        payload["content"]["sections"][1]["id"] = "key-dates"
        with self.assertRaises(SchemaValidationError) as context:
            validate_project_payload(payload)
        self.assertTrue(any(item["code"] == "duplicate" for item in context.exception.errors))

    def test_rejects_unsafe_urls(self):
        payload = sample_payload()
        payload["content"]["sections"][0]["blocks"] = [
            {"id": "bad-link", "type": "field", "label": "链接", "value": "javascript:alert(1)", "semantic": "url"}
        ]
        with self.assertRaises(SchemaValidationError) as context:
            validate_project_payload(payload)
        self.assertTrue(any(item["code"] == "unsafe_url" for item in context.exception.errors))

    def test_rejects_table_rows_with_wrong_cell_count(self):
        payload = sample_payload()
        payload["content"]["sections"][1]["blocks"][0]["rows"] = [["项目经理"]]
        with self.assertRaises(SchemaValidationError) as context:
            validate_project_payload(payload)
        self.assertTrue(any(item["code"] == "invalid_row" for item in context.exception.errors))

    def test_fingerprint_is_order_independent_for_object_keys(self):
        self.assertEqual(payload_fingerprint({"a": 1, "b": 2}), payload_fingerprint({"b": 2, "a": 1}))

    def test_rejects_unknown_nested_item_fields(self):
        payload = sample_payload()
        payload["content"]["sections"][0]["blocks"][0]["items"][0]["unexpected"] = "不能静默丢失"
        with self.assertRaises(SchemaValidationError) as context:
            validate_project_payload(payload)
        self.assertTrue(any(item["code"] == "unknown_field" for item in context.exception.errors))

    def test_json_schema_contract_matches_runtime_enums(self):
        schema_path = Path(__file__).resolve().parent.parent / "docs" / "api" / "project.schema.json"
        document = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(document["properties"]["status"]["enum"]), PROJECT_STATUSES)
        declared_blocks = {entry["$ref"].split("/")[-1] for entry in document["$defs"]["section"]["properties"]["blocks"]["items"]["oneOf"]}
        self.assertEqual(declared_blocks, BLOCK_TYPES)


if __name__ == "__main__":
    unittest.main()
