import json
import subprocess
import sys
import unittest
from pathlib import Path


class HermesSkillEndToEndTests(unittest.TestCase):
    def test_skill_create_and_incremental_update_with_audit(self):
        runner = Path(__file__).with_name("hermes_skill_runner.py")
        result = subprocess.run([sys.executable, str(runner)], cwd=runner.parent.parent, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"], 8)


if __name__ == "__main__":
    unittest.main()
