"""Build a local Ansible inventory from a private production asset profile."""

import os
import re
import tempfile
from pathlib import Path

import yaml


NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _validate_name(value, field):
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {field}: use letters, digits, '_' or '-'")
    return value


def load_production_profile(profile_path):
    """Load and validate the production asset profile."""
    path = Path(profile_path)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("Cannot read production asset profile") from exc

    if not isinstance(data, dict) or not isinstance(data.get("production"), dict):
        raise ValueError("Production asset profile must contain a production mapping")

    production = data["production"]
    ssh = production.get("ssh")

    if not isinstance(ssh, dict):
        raise ValueError("Production profile must define an ssh mapping")

    if not isinstance(ssh.get("user"), str) or not ssh["user"].strip():
        raise ValueError("Production SSH user is required")

    private_key = ssh.get("private_key")
    if private_key is not None and (
        not isinstance(private_key, str)
        or not private_key.strip()
    ):
        raise ValueError(
            "Production SSH private-key path must be a non-empty string when provided"
        )

    if not isinstance(ssh.get("become"), bool):
        raise ValueError("Production SSH become setting must be boolean")

    # Preferred schema: production.servers is a list of named servers.
    servers = production.get("servers")
    if servers is not None:
        if not isinstance(servers, list) or not servers:
            raise ValueError("Production servers must be a non-empty list")

        names = set()
        for server in servers:
            if not isinstance(server, dict):
                raise ValueError("Each production server must be a mapping")

            name = _validate_name(server.get("name"), "server name")
            if name in names:
                raise ValueError("Production server names must be unique")
            names.add(name)

            address = server.get("ansible_host")
            if not isinstance(address, str) or not address.strip():
                raise ValueError("Each production server must define ansible_host")

            groups = server.get("groups", [name])
            if (
                not isinstance(groups, list)
                or not groups
                or any(not isinstance(group, str) for group in groups)
            ):
                raise ValueError("Each server must have a non-empty groups list")

            validated_groups = [_validate_name(group, "group name") for group in groups]
            if len(set(validated_groups)) != len(validated_groups):
                raise ValueError("Server groups must not contain duplicates")
            server["groups"] = validated_groups

        return production

    # Backward compatibility: the existing production.ec2 mapping.
    hosts = production.get("ec2")
    if not isinstance(hosts, dict) or not hosts:
        raise ValueError("Production profile must define servers or an ec2 mapping")

    required_hosts = {"frontend", "backend", "rpc"}
    missing_hosts = required_hosts - hosts.keys()
    if missing_hosts:
        raise ValueError("Production profile is missing required EC2 hosts")

    for name, host in hosts.items():
        _validate_name(name, "server name")
        if not isinstance(host, dict):
            raise ValueError("Each EC2 host must be a mapping")
        address = host.get("ansible_host")
        if not isinstance(address, str) or not address.strip():
            raise ValueError("Each EC2 host must define ansible_host")

    return production


def build_inventory(production):
    """Build an Ansible inventory mapping from a validated profile."""
    ssh = production["ssh"]
    groups = {}

    if "servers" in production:
        servers = production["servers"]
    else:
        # Convert the legacy mapping into the generic internal representation.
        servers = [
            {"name": name, "ansible_host": host["ansible_host"], "groups": [name]}
            for name, host in production["ec2"].items()
        ]

    for server in servers:
        hostname = f"ec2-{server['name']}"
        host_entry = {"ansible_host": server["ansible_host"]}

        for group in server.get("groups", [server["name"]]):
            groups.setdefault(group, {}).setdefault("hosts", {})[hostname] = host_entry.copy()

    group_vars = {
        "ansible_user": ssh["user"],
        "ansible_become": ssh["become"],
        "ec2_monitoring_endpoint_host": production["monitoring"]["endpoint_host"],
        "ec2_prometheus_remote_write_port": production["monitoring"]["prometheus_remote_write_port"],
        "ec2_loki_push_port": production["monitoring"]["loki_push_port"],
    }

    private_key = ssh.get("private_key")
    if private_key:
        group_vars["ansible_ssh_private_key_file"] = private_key

    return {
        "all": {
            "children": {
                "ec2_production": {
                    "children": groups,
                    "vars": group_vars,
                }
            }
        }
    }


def write_inventory(inventory, output_path, overwrite=False):
    """Write inventory securely, refusing to replace existing files by default."""
    output = Path(output_path)
    parent = output.parent

    if output.is_symlink():
        raise ValueError("Refusing to write inventory through a symlink")

    if not parent.is_dir():
        raise ValueError("Inventory output directory does not exist")

    if output.exists() and not overwrite:
        raise FileExistsError("Inventory already exists; refusing to overwrite")

    fd, temp_name = tempfile.mkstemp(
        prefix=".production-inventory-",
        suffix=".tmp",
        dir=parent,
        text=True,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(inventory, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temp_path, 0o600)

        if overwrite:
            if output.is_symlink():
                raise ValueError("Refusing to replace a symlink")
            os.replace(temp_path, output)
        else:
            # Atomic creation: never replace a file created concurrently.
            os.link(temp_path, output)
            temp_path.unlink()

        os.chmod(output, 0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()
