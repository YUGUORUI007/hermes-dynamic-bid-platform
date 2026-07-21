import unittest

from scripts.production_acceptance_check import confirmation, sample_payload


class ProductionAcceptanceCheckTests(unittest.TestCase):
    def test_sample_covers_dynamic_sections(self) -> None:
        payload = sample_payload("test")
        block_types = {block["type"] for section in payload["content"]["sections"] for block in section["blocks"]}
        self.assertTrue({"field", "callout", "timeline", "checklist", "table"}.issubset(block_types))

    def test_confirmation_has_required_fields(self) -> None:
        value = confirmation("确认验收")
        self.assertEqual(value["summary"], "确认验收")
        self.assertTrue(value["confirmed_by"])
        self.assertIn("+00:00", value["confirmed_at"])


if __name__ == "__main__":
    unittest.main()
