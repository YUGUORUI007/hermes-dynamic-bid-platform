import json
import unittest

from platform_app.services.ai_pipeline import answer_project_question_locally, repair_mojibake_text
from platform_app.services.ai_pipeline import parse_question_answer_metadata


QUESTION = "\u8fd9\u4e2a\u9879\u76ee\u7684\u6295\u6807\u4fdd\u8bc1\u91d1\u548c\u5f00\u6807\u65f6\u95f4\u662f\u4ec0\u4e48\uff1f"

CONTEXT = """
\u9879\u76ee\u540d\u79f0\uff1a\u67d0\u4f4f\u5b85\u5c0f\u533a\u7269\u4e1a\u670d\u52a1\u9879\u76ee
\u72b6\u6001\uff1a\u5f85\u8ddf\u8fdb

--- \u6587\u4ef6\uff1asample_tender.txt ---
[\u7b2c 1 \u884c] \u9879\u76ee\u540d\u79f0\uff1a\u67d0\u4f4f\u5b85\u5c0f\u533a\u7269\u4e1a\u670d\u52a1\u9879\u76ee
[\u7b2c 2 \u884c] \u62db\u6807\u7f16\u53f7\uff1aWY-2026-001
[\u7b2c 3 \u884c] \u62db\u6807\u4eba\uff1a\u67d0\u7269\u4e1a\u53d1\u5c55\u6709\u9650\u516c\u53f8
[\u7b2c 4 \u884c] \u9879\u76ee\u5730\u70b9\uff1a\u676d\u5dde\u5e02\u897f\u6e56\u533a
[\u7b2c 5 \u884c] \u62a5\u540d\u622a\u6b62\u65f6\u95f4\uff1a2026-07-20 17:00
[\u7b2c 6 \u884c] \u6295\u6807\u4fdd\u8bc1\u91d1\uff1a5\u4e07
[\u7b2c 7 \u884c] \u6295\u6807\u6587\u4ef6\u9012\u4ea4\u622a\u6b62\u65f6\u95f4\uff1a2026-07-25 09:30
[\u7b2c 8 \u884c] \u5f00\u6807\u65f6\u95f4\uff1a2026-07-25 09:30
""".strip()


class LocalQuestionAnswerTests(unittest.TestCase):
    def test_answers_common_field_questions_from_context(self) -> None:
        answer, citations = answer_project_question_locally(QUESTION, CONTEXT)
        payload = json.loads(citations)

        self.assertIn("5", answer)
        self.assertIn("2026-07-25 09:30", answer)
        self.assertEqual(payload["answer_status"], "grounded")
        self.assertEqual(payload["answer_mode"], "fallback")
        self.assertEqual(len(payload["basis_items"]), 2)
        self.assertTrue(any("\u7b2c 6 \u884c" == item["source_location"] for item in payload["basis_items"]))
        self.assertTrue(any("\u7b2c 8 \u884c" == item["source_location"] for item in payload["basis_items"]))

    def test_returns_not_found_when_context_has_no_support(self) -> None:
        answer, citations = answer_project_question_locally(QUESTION, "\u9879\u76ee\u540d\u79f0\uff1a\u6d4b\u8bd5\u9879\u76ee")
        payload = json.loads(citations)

        self.assertIn("\u672a\u627e\u5230", answer)
        self.assertEqual(payload["answer_status"], "not_found")
        self.assertEqual(payload["answer_mode"], "fallback")
        self.assertEqual(payload["basis_items"], [])

    def test_repairs_mojibake_question_before_matching(self) -> None:
        mojibake_question = QUESTION.encode("utf-8").decode("latin1")
        self.assertNotEqual(mojibake_question, QUESTION)
        self.assertEqual(repair_mojibake_text(mojibake_question), QUESTION)

        answer, citations = answer_project_question_locally(mojibake_question, CONTEXT)
        payload = json.loads(citations)

        self.assertIn("5", answer)
        self.assertIn("2026-07-25 09:30", answer)
        self.assertEqual(payload["answer_status"], "grounded")
        self.assertEqual(payload["answer_mode"], "fallback")
        self.assertEqual(len(payload["basis_items"]), 2)

    def test_parse_legacy_metadata_defaults_mode_to_unknown(self) -> None:
        parsed = parse_question_answer_metadata("历史答案", '{"legacy":"yes"}')
        self.assertEqual(parsed["answer_mode"], "unknown")


if __name__ == "__main__":
    unittest.main()
