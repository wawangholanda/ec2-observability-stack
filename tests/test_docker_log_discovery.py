import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
HELPER_PATH = (
    ROOT_DIR
    / "roles"
    / "ec2_monitoring_agent"
    / "files"
    / "discover_docker_log_targets.py"
)

spec = importlib.util.spec_from_file_location("docker_log_discovery", HELPER_PATH)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class DockerLogDiscoveryTests(unittest.TestCase):
    def test_filters_to_application_images(self):
        with tempfile.NamedTemporaryFile() as app_log:
            app_container = {
                "Id": "app-id",
                "Name": "/backend-app",
                "Config": {
                    "Image": "harbor.cnrsoftware.co.id/application/backend:v1"
                },
                "HostConfig": {"LogConfig": {"Type": "json-file"}},
                "LogPath": app_log.name,
            }

            def fake_run(args, **kwargs):
                if args == ["docker", "ps", "--format", "json"]:
                    rows = [
                        {
                            "ID": "app-id",
                            "Image": app_container["Config"]["Image"],
                            "Names": "backend-app",
                        },
                        {
                            "ID": "alloy-id",
                            "Image": "grafana/alloy:v1.20.1",
                            "Names": "ec2-monitoring-alloy",
                        },
                        {
                            "ID": "proxy-id",
                            "Image": "openresty/openresty:alpine",
                            "Names": "frontend-proxy",
                        },
                    ]
                    return subprocess.CompletedProcess(
                        args, 0, "\n".join(map(json.dumps, rows)), ""
                    )

                if args == ["docker", "inspect", "app-id"]:
                    return subprocess.CompletedProcess(
                        args, 0, json.dumps([app_container]), ""
                    )

                raise AssertionError(f"Unexpected command: {args}")

            stdout = io.StringIO()
            with patch.object(discovery.subprocess, "run", side_effect=fake_run):
                with patch.object(sys, "argv", [
                    "discover_docker_log_targets.py",
                    "harbor.cnrsoftware.co.id/application/",
                ]):
                    with contextlib.redirect_stdout(stdout):
                        discovery.main()

            targets = json.loads(stdout.getvalue())
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["container"], "backend-app")
            self.assertEqual(
                targets[0]["image"],
                "harbor.cnrsoftware.co.id/application/backend:v1",
            )

    def test_rejects_empty_prefix(self):
        with patch.object(
            sys, "argv", ["discover_docker_log_targets.py", ""]
        ):
            with self.assertRaises(SystemExit):
                discovery.main()


if __name__ == "__main__":
    unittest.main()
