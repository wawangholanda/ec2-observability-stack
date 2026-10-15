import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from profile_state import validate_state, write_state


class ProfileStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "profile.json"

    def write_state(self, content):
        self.state_path.write_text(content, encoding="utf-8")

    def test_valid_uninitialized_state(self):
        self.write_state(json.dumps({
            "active_profile": "uninitialized",
            "deployment_status": "not_deployed",
        }))

        result = validate_state(self.state_path)

        self.assertEqual(result, {
            "active_profile": "uninitialized",
            "deployment_status": "not_deployed",
        })

    def test_valid_deployed_single_profile(self):
        self.write_state(json.dumps({
            "active_profile": "single",
            "deployment_status": "deployed",
        }))

        result = validate_state(self.state_path)

        self.assertEqual(result["active_profile"], "single")
        self.assertEqual(result["deployment_status"], "deployed")

    def test_rejects_malformed_json(self):
        self.write_state("{invalid json")

        with self.assertRaisesRegex(ValueError, "Cannot read profile state"):
            validate_state(self.state_path)

    def test_rejects_non_object_json(self):
        self.write_state(json.dumps(["single", "deployed"]))

        with self.assertRaisesRegex(ValueError, "JSON object"):
            validate_state(self.state_path)

    def test_rejects_unknown_profile(self):
        self.write_state(json.dumps({
            "active_profile": "production",
            "deployment_status": "deployed",
        }))

        with self.assertRaisesRegex(ValueError, "Unknown active profile"):
            validate_state(self.state_path)

    def test_rejects_unknown_deployment_status(self):
        self.write_state(json.dumps({
            "active_profile": "single",
            "deployment_status": "pending",
        }))

        with self.assertRaisesRegex(ValueError, "Unknown deployment status"):
            validate_state(self.state_path)

    def test_rejects_missing_or_unexpected_fields(self):
        cases = [
            {"active_profile": "single"},
            {
                "active_profile": "single",
                "deployment_status": "deployed",
                "unexpected": True,
            },
        ]

        for state in cases:
            with self.subTest(state=state):
                self.write_state(json.dumps(state))
                with self.assertRaisesRegex(ValueError, "missing or unexpected"):
                    validate_state(self.state_path)

    def test_rejects_inconsistent_profile_and_status(self):
        cases = [
            ("single", "not_deployed"),
            ("uninitialized", "deployed"),
        ]

        for profile, deployment in cases:
            with self.subTest(profile=profile, deployment=deployment):
                self.write_state(json.dumps({
                    "active_profile": profile,
                    "deployment_status": deployment,
                }))
                with self.assertRaisesRegex(ValueError, "inconsistent"):
                    validate_state(self.state_path)

    def test_rejects_wrong_field_types(self):
        cases = [
            {"active_profile": 123, "deployment_status": "deployed"},
            {"active_profile": "single", "deployment_status": False},
        ]

        for state in cases:
            with self.subTest(state=state):
                self.write_state(json.dumps(state))
                with self.assertRaises(ValueError):
                    validate_state(self.state_path)

    def test_rejects_missing_state_file(self):
        with self.assertRaisesRegex(ValueError, "Cannot read profile state"):
            validate_state(self.state_path)

    def test_write_state_creates_valid_state_with_secure_permissions(self):
        self.state_path.parent.mkdir(exist_ok=True)
        write_state(self.state_path, "single", "deployed")

        self.assertEqual(validate_state(self.state_path), {
            "active_profile": "single",
            "deployment_status": "deployed",
        })
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_write_state_rejects_invalid_transition_without_changing_state(self):
        self.write_state(json.dumps({
            "active_profile": "uninitialized",
            "deployment_status": "not_deployed",
        }))
        original = self.state_path.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "inconsistent"):
            write_state(self.state_path, "single", "not_deployed")

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), original)

    def test_write_state_rejects_unknown_profile(self):
        with self.assertRaisesRegex(ValueError, "Unknown active profile"):
            write_state(self.state_path, "production", "deployed")

    def test_write_state_rejects_unknown_deployment_status(self):
        with self.assertRaisesRegex(ValueError, "Unknown deployment status"):
            write_state(self.state_path, "single", "pending")

if __name__ == "__main__":
    unittest.main()
