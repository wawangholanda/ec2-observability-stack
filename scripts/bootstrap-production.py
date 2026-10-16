#!/usr/bin/env python3
"""Interactively bootstrap a local production profile and Ansible inventory."""

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

import yaml


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a local production profile and Ansible inventory."
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("."),
        help="Configuration root containing .secrets and inventory.",
    )
    return parser.parse_args()


def prompt_required(label):
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("Value is required.")


def prompt_bool(label, default=True):
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def build_profile():
    print("\n=== Production SSH ===")
    ssh_user = prompt_required("SSH user")
    print("SSH private key path (leave blank to use SSH password with Ansible -k).")
    private_key_input = input("SSH private key path [optional]: ").strip()
    private_key = private_key_input or None
    become = prompt_bool("Use Ansible become/sudo", True)

    print("\n=== EC2 Server ===")
    name = prompt_required("Server name")
    address = prompt_required("Server ansible_host")
    group = prompt_required("Server group")

    print("\n=== Monitoring Endpoint ===")
    endpoint_host = prompt_required("Monitoring endpoint host")
    prometheus_port = prompt_required("Prometheus remote_write port")
    loki_port = prompt_required("Loki push port")

    print("\n=== Grafana ===")
    grafana_wireguard = prompt_required("Grafana WireGuard bind IP")
    grafana_lan = prompt_required("Grafana LAN bind IP")

    return {
        "production": {
            "ssh": {
                "user": ssh_user,
                "private_key": private_key,
                "become": become,
            },
            "servers": [
                {
                    "name": name,
                    "ansible_host": address,
                    "groups": [group],
                }
            ],
            "monitoring": {
                "endpoint_host": endpoint_host,
                "prometheus_remote_write_port": int(prometheus_port),
                "loki_push_port": int(loki_port),
            },
            "grafana": {
                "wireguard_bind_ip": grafana_wireguard,
                "lan_bind_ip": grafana_lan,
            },
        }
    }


def write_secure_file(path, data):
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags, 0o600)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, sort_keys=False)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def main():
    args = parse_args()
    config_dir = args.config_dir.expanduser().resolve()

    profile_path = config_dir / ".secrets" / "assets" / "production.yml"
    inventory_path = config_dir / "inventory" / "production" / "hosts.yml"

    print(f"Configuration root: {config_dir}")
    print(f"Profile output:     {profile_path}")
    print(f"Inventory output:   {inventory_path}")

    profile = build_profile()

    print("\n=== Summary ===")
    print(yaml.safe_dump(profile, sort_keys=False))

    if not prompt_bool("Write production profile", True):
        print("Cancelled. No files were written.")
        return 0

    try:
        write_secure_file(profile_path, profile)
        inventory_path.parent.mkdir(parents=True, exist_ok=True)

        generator = Path(__file__).resolve().parent / "production_inventory.py"

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "from pathlib import Path; "
                    "sys.path.insert(0, str(Path(sys.argv[1]).parent)); "
                    "from production_inventory import "
                    "load_production_profile, build_inventory, write_inventory; "
                    "profile = load_production_profile(sys.argv[2]); "
                    "inventory = build_inventory(profile); "
                    "write_inventory(inventory, Path(sys.argv[3]))"
                ),
                str(generator),
                str(profile_path),
                str(inventory_path),
            ],
            check=False,
        )

        if result.returncode != 0:
            print(
                "ERROR: Inventory generation failed. "
                f"The profile remains at {profile_path}.",
                file=sys.stderr,
            )
            return result.returncode

    except (FileExistsError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nBootstrap completed successfully.")
    print(f"Profile:   {profile_path}")
    print(f"Inventory: {inventory_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
