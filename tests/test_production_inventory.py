"""Build a local Ansible inventory from a private production asset profile."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from production_inventory import (
    build_inventory,
    load_production_profile,
    write_inventory,
)


class ProductionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.profile_path = Path(self.temp_dir.name) / "production.yml"
        self.profile = {
            "production": {
                "ssh": {
                    "user": "ubuntu",
                    "private_key": "/tmp/example-key.pem",
                    "become": True,
                },
                "ec2": {
                    "frontend": {"ansible_host": "192.0.2.10"},
                    "backend": {"ansible_host": "192.0.2.11"},
                    "rpc": {"ansible_host": "192.0.2.12"},
                },
                "monitoring": {
                    "endpoint_host": "192.0.2.20",
                    "prometheus_remote_write_port": 9090,
                    "loki_push_port": 3100,
                },
                "grafana": {
                    "wireguard_bind_ip": "192.0.2.20",
                    "lan_bind_ip": "192.0.2.21",
                },
            }
        }
        self.write_profile(self.profile)

    def write_profile(self, data):
        self.profile_path.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

    def test_loads_valid_profile(self):
        result = load_production_profile(self.profile_path)
        self.assertEqual(result["ssh"]["user"], "ubuntu")
        self.assertEqual(
            sorted(result["ec2"]),
            ["backend", "frontend", "rpc"],
        )

    def test_builds_expected_inventory_structure(self):
        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        group = inventory["all"]["children"]["ec2_production"]

        self.assertEqual(
            group["children"]["frontend"]["hosts"]["ec2-frontend"]["ansible_host"],
            "192.0.2.10",
        )
        self.assertEqual(
            group["children"]["backend"]["hosts"]["ec2-backend"]["ansible_host"],
            "192.0.2.11",
        )
        self.assertEqual(
            group["children"]["rpc"]["hosts"]["ec2-rpc"]["ansible_host"],
            "192.0.2.12",
        )
        self.assertEqual(group["vars"]["ansible_user"], "ubuntu")
        self.assertTrue(group["vars"]["ansible_become"])
        self.assertEqual(group["vars"]["ec2_monitoring_endpoint_host"], "192.0.2.20")
        self.assertEqual(group["vars"]["ec2_prometheus_remote_write_port"], 9090)
        self.assertEqual(group["vars"]["ec2_loki_push_port"], 3100)
        self.assertEqual(
            group["vars"]["ansible_ssh_private_key_file"],
            "/tmp/example-key.pem",
        )

    def test_inventory_omits_private_key_when_not_configured(self):
        self.profile["production"]["ssh"]["private_key"] = None
        self.write_profile(self.profile)

        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        group_vars = inventory["all"]["children"]["ec2_production"]["vars"]

        self.assertNotIn("ansible_ssh_private_key_file", group_vars)
        self.assertEqual(group_vars["ansible_user"], "ubuntu")

    def test_inventory_omits_private_key_when_empty(self):
        self.profile["production"]["ssh"]["private_key"] = ""
        self.write_profile(self.profile)

        with self.assertRaisesRegex(ValueError, "private-key path must be a non-empty string"):
            load_production_profile(self.profile_path)

    def test_builds_multi_server_inventory(self):
        self.profile["production"].pop("ec2")
        self.profile["production"]["servers"] = [
            {
                "name": "web01",
                "ansible_host": "192.0.2.30",
                "groups": ["frontend", "web"],
            },
            {
                "name": "api01",
                "ansible_host": "192.0.2.31",
                "groups": ["backend"],
            },
        ]
        self.write_profile(self.profile)

        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        groups = inventory["all"]["children"]["ec2_production"]["children"]

        self.assertEqual(
            groups["frontend"]["hosts"]["ec2-web01"]["ansible_host"],
            "192.0.2.30",
        )
        self.assertEqual(
            groups["web"]["hosts"]["ec2-web01"]["ansible_host"],
            "192.0.2.30",
        )
        self.assertEqual(
            groups["backend"]["hosts"]["ec2-api01"]["ansible_host"],
            "192.0.2.31",
        )

    def test_rejects_duplicate_server_names(self):
        self.profile["production"].pop("ec2")
        self.profile["production"]["servers"] = [
            {
                "name": "web01",
                "ansible_host": "192.0.2.30",
                "groups": ["frontend"],
            },
            {
                "name": "web01",
                "ansible_host": "192.0.2.31",
                "groups": ["backend"],
            },
        ]
        self.write_profile(self.profile)

        with self.assertRaisesRegex(ValueError, "server names must be unique"):
            load_production_profile(self.profile_path)

    def test_rejects_missing_profile_file(self):
        with self.assertRaisesRegex(ValueError, "Cannot read production asset profile"):
            load_production_profile(
                Path(self.temp_dir.name) / "missing.yml"
            )

    def test_rejects_invalid_yaml(self):
        self.profile_path.write_text("production: [", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Cannot read production asset profile"):
            load_production_profile(self.profile_path)

    def test_rejects_missing_required_host(self):
        del self.profile["production"]["ec2"]["rpc"]
        self.write_profile(self.profile)
        with self.assertRaisesRegex(ValueError, "missing required EC2 hosts"):
            load_production_profile(self.profile_path)

    def test_rejects_missing_ssh_user(self):
        del self.profile["production"]["ssh"]["user"]
        self.write_profile(self.profile)
        with self.assertRaisesRegex(ValueError, "SSH user is required"):
            load_production_profile(self.profile_path)

    def test_rejects_whitespace_server_address(self):
        self.profile["production"].pop("ec2")
        self.profile["production"]["servers"] = [
            {
                "name": "web01",
                "ansible_host": "   ",
                "groups": ["frontend"],
            }
        ]
        self.write_profile(self.profile)

        with self.assertRaisesRegex(ValueError, "must define ansible_host"):
            load_production_profile(self.profile_path)

    def test_rejects_empty_server_groups(self):
        self.profile["production"].pop("ec2")
        self.profile["production"]["servers"] = [
            {
                "name": "web01",
                "ansible_host": "192.0.2.30",
                "groups": [],
            }
        ]
        self.write_profile(self.profile)

        with self.assertRaisesRegex(ValueError, "non-empty groups list"):
            load_production_profile(self.profile_path)

    def test_rejects_missing_host_address(self):
        self.profile["production"]["ec2"]["frontend"]["ansible_host"] = ""
        self.write_profile(self.profile)
        with self.assertRaisesRegex(ValueError, "define ansible_host"):
            load_production_profile(self.profile_path)
    def test_write_inventory_creates_file_with_restricted_permissions(self):
        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        output = Path(self.temp_dir.name) / "hosts.yml"

        write_inventory(inventory, output)

        self.assertTrue(output.is_file())
        self.assertEqual(
            output.stat().st_mode & 0o777,
            0o600,
        )
        written = yaml.safe_load(output.read_text(encoding="utf-8"))
        self.assertEqual(written, inventory)

    def test_write_inventory_refuses_to_overwrite_existing_file(self):
        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        output = Path(self.temp_dir.name) / "hosts.yml"
        output.write_text("keep-this-content\n", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            write_inventory(inventory, output)

        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "keep-this-content\n",
        )

    def test_write_inventory_rejects_symlink(self):
        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        target = Path(self.temp_dir.name) / "target.yml"
        output = Path(self.temp_dir.name) / "hosts.yml"
        target.write_text("keep-this-content\n", encoding="utf-8")
        output.symlink_to(target)

        with self.assertRaisesRegex(ValueError, "symlink"):
            write_inventory(inventory, output)

        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "keep-this-content\n",
        )

    def test_write_inventory_rejects_missing_output_directory(self):
        production = load_production_profile(self.profile_path)
        inventory = build_inventory(production)
        output = Path(self.temp_dir.name) / "missing" / "hosts.yml"

        with self.assertRaisesRegex(ValueError, "directory does not exist"):
            write_inventory(inventory, output)

if __name__ == "__main__":
    unittest.main()
