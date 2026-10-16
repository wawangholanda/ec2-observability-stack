import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    ROOT_DIR
    / "roles"
    / "ec2_monitoring_agent"
    / "templates"
    / "config.alloy.j2"
)


class Ec2DockerLogConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_keeps_prometheus_metrics_pipeline(self):
        self.assertIn('prometheus.remote_write "central"', self.template)
        self.assertIn("{{ ec2_prometheus_remote_write_url }}", self.template)

    def test_keeps_central_loki_destination(self):
        self.assertIn('loki.write "central"', self.template)
        self.assertIn("{{ ec2_loki_push_url }}", self.template)

    def test_uses_dedicated_docker_application_log_pipeline(self):
        self.assertIn('local.file_match "docker_app_logs"', self.template)
        self.assertIn('loki.source.file "docker_app_logs"', self.template)
        self.assertIn('loki.process "docker_logs"', self.template)
        self.assertIn("stage.docker", self.template)

    def test_does_not_collect_system_logs(self):
        self.assertNotIn("/host/var/log/syslog", self.template)
        self.assertNotIn("/host/var/log/auth.log", self.template)
        self.assertNotIn('job      = "system"', self.template)

    def test_does_not_mount_docker_socket(self):
        self.assertNotIn("/var/run/docker.sock", self.template)


if __name__ == "__main__":
    unittest.main()
