import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "hermes-skill" / "manage-bid-projects" / "scripts" / "sync_skill.py"
SPEC = importlib.util.spec_from_file_location("skill_sync", SCRIPT_PATH)
skill_sync = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(skill_sync)


class SkillSyncTests(unittest.TestCase):
    def test_normalizes_semantic_versions(self):
        self.assertEqual(skill_sync.normalize_version("v0.4.0"), (0, 4, 0))
        with self.assertRaises(ValueError):
            skill_sync.normalize_version("latest")

    def test_detects_unknown_or_outdated_installed_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manage-bid-projects"
            target.mkdir()
            self.assertTrue(skill_sync.release_state(target, "v0.4.0")["update_available"])

            (target / "VERSION").write_text("0.4.0\n", encoding="utf-8")
            self.assertFalse(skill_sync.release_state(target, "v0.4.0")["update_available"])
            self.assertTrue(skill_sync.release_state(target, "v0.4.1")["update_available"])


if __name__ == "__main__":
    unittest.main()
