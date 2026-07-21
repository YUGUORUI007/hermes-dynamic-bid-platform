import json
import subprocess
import sys
import unittest
from pathlib import Path


class IsolatedApiIntegrationTests(unittest.TestCase):
    def test_full_dynamic_api_flow_in_temporary_database(self):
        runner = Path(__file__).with_name("api_integration_runner.py")
        result = subprocess.run(
            [sys.executable, str(runner)],
            cwd=str(runner.parent.parent),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"], 45)


if __name__ == "__main__":
    unittest.main()
