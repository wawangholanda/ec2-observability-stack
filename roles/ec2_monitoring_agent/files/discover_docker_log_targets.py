#!/usr/bin/env python3
"""Discover active application Docker JSON log files without reading log contents."""

import json
import os
import subprocess
import sys


def run_docker(*args):
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        raise SystemExit("A non-empty application image prefix is required")

    prefix = sys.argv[1]
    containers = []

    for line in run_docker("ps", "--format", "json").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("Image", "").startswith(prefix):
            containers.append(item)

    targets = []
    for container in containers:
        inspected = json.loads(
            run_docker("inspect", container["ID"])
        )[0]

        log_config = inspected.get("HostConfig", {}).get("LogConfig", {})
        log_path = inspected.get("LogPath", "")
        name = inspected.get("Name", "").lstrip("/")
        image = inspected.get("Config", {}).get("Image", "")

        if log_config.get("Type") != "json-file":
            raise SystemExit(
                f"Container {name!r} does not use the json-file logging driver"
            )

        if not log_path.startswith("/") or not os.path.isfile(log_path):
            raise SystemExit(
                f"Container {name!r} has no accessible Docker log file"
            )

        targets.append(
            {
                "path": "/host/root" + log_path,
                "container": name,
                "image": image,
            }
        )

    if not targets:
        raise SystemExit(
            f"No running containers found for image prefix {prefix!r}"
        )

    print(json.dumps(sorted(targets, key=lambda item: item["container"])))


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"Docker log target discovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
