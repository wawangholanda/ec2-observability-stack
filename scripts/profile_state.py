#!/usr/bin/env python3
"""Validation helpers for observability deployment profile state."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any



VALID_PROFILES = {"uninitialized", "single", "ha", "scale"}
VALID_DEPLOYMENT_STATUSES = {"not_deployed", "deployed"}
EXPECTED_KEYS = {"active_profile", "deployment_status"}


def validate_state(path: str | Path) -> dict[str, str]:
    """Load and validate profile state, raising ValueError on invalid data."""
    state_path = Path(path)

    try:
        state: Any = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read profile state: {exc}") from exc

    if not isinstance(state, dict):
        raise ValueError("Profile state must be a JSON object.")

    if set(state) != EXPECTED_KEYS:
        raise ValueError("Profile state has missing or unexpected fields.")

    profile = state["active_profile"]
    deployment = state["deployment_status"]

    if not isinstance(profile, str) or profile not in VALID_PROFILES:
        raise ValueError("Unknown active profile in state file.")

    if (
        not isinstance(deployment, str)
        or deployment not in VALID_DEPLOYMENT_STATUSES
    ):
        raise ValueError("Unknown deployment status in state file.")

    if (profile == "uninitialized") != (deployment == "not_deployed"):
        raise ValueError("Profile and deployment status are inconsistent.")

    return {
        "active_profile": profile,
        "deployment_status": deployment,
    }


def write_state(path: str | Path, profile: str, deployment: str) -> None:
    """Atomically write a valid profile state with restrictive permissions."""
    if not isinstance(profile, str) or profile not in VALID_PROFILES:
        raise ValueError("Unknown active profile.")
    if (
        not isinstance(deployment, str)
        or deployment not in VALID_DEPLOYMENT_STATUSES
    ):
        raise ValueError("Unknown deployment status.")
    if (profile == "uninitialized") != (deployment == "not_deployed"):
        raise ValueError("Profile and deployment status are inconsistent.")

    state_path = Path(path)
    state_dir = state_path.parent

    if state_path.is_symlink() or state_dir.is_symlink():
        raise ValueError("Profile state path must not use symlinks.")
    if not state_dir.is_dir():
        raise ValueError("Profile state directory must already exist.")

    fd, temp_name = tempfile.mkstemp(prefix=".profile-state.", dir=state_dir)
    temp_path = Path(temp_name)

    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "active_profile": profile,
                    "deployment_status": deployment,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_path, state_path)
        dir_fd = os.open(state_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temp_path.unlink(missing_ok=True)
