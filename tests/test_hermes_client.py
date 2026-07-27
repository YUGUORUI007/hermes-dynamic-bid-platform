import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "hermes-skill" / "manage-bid-projects" / "scripts" / "bid_platform.py"
SPEC = importlib.util.spec_from_file_location("bid_platform", SCRIPT_PATH)
bid_platform = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bid_platform)


def confirmed_payload():
    return {
        "summary": "Updated summary",
        "confirmation": {
            "confirmed_by": "User",
            "confirmed_at": "2026-07-27T09:00:00+08:00",
            "summary": "Confirm update",
        },
    }


class HermesClientTests(unittest.TestCase):
    def test_apply_update_validates_and_uses_current_version(self):
        calls = []

        def fake_request(method, path, *, payload=None, headers=None):
            calls.append((method, path, payload, headers))
            if method == "GET":
                return {"version": 7}
            if path.startswith("/validate/"):
                return {"validation_token": "fresh-token"}
            return {"updated": True}

        with patch.object(bid_platform, "request", side_effect=fake_request):
            response = bid_platform.apply_update(12, confirmed_payload(), idempotency_key="test-key")

        self.assertTrue(response["updated"])
        self.assertEqual(calls[0][:2], ("GET", "/projects/12"))
        self.assertEqual(calls[1][:2], ("POST", "/validate/project?partial=true"))
        self.assertEqual(calls[2][3], {"Idempotency-Key": "test-key", "If-Match": "7"})
        self.assertEqual(calls[2][2]["validation_token"], "fresh-token")

    def test_apply_rejects_missing_confirmation_before_network_call(self):
        with patch.object(bid_platform, "request") as mocked:
            with self.assertRaises(SystemExit):
                bid_platform.apply_create({"title": "No confirmation"}, idempotency_key="test-key")
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
