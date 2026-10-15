#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STATE_DIR="$ROOT_DIR/.state"
STATE_FILE="$STATE_DIR/profile.json"
BACKUP_DIR="$ROOT_DIR/backups/profiles"

usage() {
  printf '%s\n' \
    "Usage:" \
    "  ./scripts/profile-manager.sh status" \
    "  ./scripts/profile-manager.sh init" \
    "  ./scripts/profile-manager.sh set-state single deployed" \
    "  ./scripts/profile-manager.sh backup"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

status() {
  if [[ ! -e "$STATE_FILE" && ! -L "$STATE_FILE" ]]; then
    printf '%s\\n' \
      "Active profile: uninitialized" \
      "Deployment status: not recorded" \
      "Profile state was not created or modified."
    return 0
  fi

  [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] ||
    fail "Profile state must be a regular, non-symlink file."

  python - "$ROOT_DIR/scripts/profile_state.py" "$STATE_FILE" <<'PYVALIDATE'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
from profile_state import validate_state

try:
    state = validate_state(sys.argv[2])
except ValueError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"Active profile: {state['active_profile']}")
print(f"Deployment status: {state['deployment_status']}")
PYVALIDATE
}

init_state() {
  [[ ! -L "$STATE_DIR" ]] ||
    fail "State directory must not be a symlink."

  if [[ -e "$STATE_FILE" ]]; then
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] ||
      fail "Existing profile state must be a regular, non-symlink file."
    status
    return
  fi

  mkdir -p -- "$STATE_DIR"
  chmod 700 "$STATE_DIR"

  local temp_state
  temp_state="$(mktemp "$STATE_DIR/.profile-state.XXXXXX")"
  chmod 600 "$temp_state"

  if ! python - "$temp_state" <<'PYJSON'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
state = {
    "active_profile": "uninitialized",
    "deployment_status": "not_deployed",
}
path.write_text(json.dumps(state, indent=2) + "\n")
PYJSON
  then
    rm -f -- "$temp_state"
    fail "Could not write initial profile state."
  fi

  if [[ -e "$STATE_FILE" || -L "$STATE_FILE" ]]; then
    rm -f -- "$temp_state"
    fail "Profile state appeared concurrently; refusing to overwrite it."
  fi

  mv -- "$temp_state" "$STATE_FILE"
  chmod 600 "$STATE_FILE"

  printf '%s\n' \
    "Initial profile state created." \
    "No profile is active; no deployment was performed."
  status
}


set_state() {
  [[ "$#" -eq 2 ]] || fail "set-state requires PROFILE and DEPLOYMENT_STATUS."
  [[ "$1" == "single" && "$2" == "deployed" ]] ||
    fail "Only the verified single/deployed state transition is currently supported."

  [[ ! -L "$STATE_DIR" ]] ||
    fail "State directory must not be a symlink."
  [[ -d "$STATE_DIR" ]] ||
    fail "State directory is missing; run init first."
  [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] ||
    fail "Profile state must be a regular, non-symlink file."

  python - "$ROOT_DIR/scripts/profile_state.py" "$STATE_FILE" "$1" "$2" <<'PYSETSTATE'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
from profile_state import validate_state, write_state

state_path = Path(sys.argv[2])
try:
    current = validate_state(state_path)
    if current["active_profile"] not in ("uninitialized", "single"):
        raise ValueError("Current profile does not permit the single deployment.")
    write_state(state_path, sys.argv[3], sys.argv[4])
    updated = validate_state(state_path)
except (ValueError, OSError) as exc:
    print(f"ERROR: Could not update profile state: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"Active profile: {updated['active_profile']}")
print(f"Deployment status: {updated['deployment_status']}")
PYSETSTATE
}

backup() {
  local required_paths=(
    ansible.cfg
    inventory/lab/hosts.yml
    inventory/lab/hosts.example.yml
    group_vars
    playbooks
    roles
    files
  )

  local path
  for path in "${required_paths[@]}"; do
    [[ -e "$ROOT_DIR/$path" ]] ||
      fail "Required backup path is missing: $path"
    [[ ! -L "$ROOT_DIR/$path" ]] ||
      fail "Symlinks are not permitted in backup roots: $path"
  done

  command -v tar >/dev/null 2>&1 || fail "Required command not found: tar"
  command -v date >/dev/null 2>&1 || fail "Required command not found: date"
  command -v python >/dev/null 2>&1 || fail "Required command not found: python"

  mkdir -p -- "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"

  local timestamp archive temp_archive
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  archive="$BACKUP_DIR/profile-${timestamp}.tar.gz"
  [[ ! -e "$archive" ]] ||
    fail "Backup archive already exists: $archive"

  temp_archive="$(mktemp "$BACKUP_DIR/.profile-backup.XXXXXX")"
  chmod 600 "$temp_archive"

  cleanup_temp() {
    rm -f -- "$temp_archive"
  }
  trap cleanup_temp RETURN

  tar -czf "$temp_archive" -- "${required_paths[@]}" ||
    fail "Backup creation failed; no profile transition is allowed."

  python - "$temp_archive" "${required_paths[@]}" <<'PYVERIFY'
import sys
import tarfile
from pathlib import PurePosixPath

archive_path = sys.argv[1]
required_roots = sys.argv[2:]

try:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name.rstrip("/") for member in members}

        for root in required_roots:
            if root not in names:
                print(f"ERROR: Backup root missing from archive: {root}", file=sys.stderr)
                raise SystemExit(1)

        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                print(f"ERROR: Unsafe archive path: {member.name}", file=sys.stderr)
                raise SystemExit(1)
            if member.issym() or member.islnk():
                print(f"ERROR: Symlink or hard link in archive: {member.name}", file=sys.stderr)
                raise SystemExit(1)
            if any(part in {".secrets", ".state"} for part in path.parts):
                print(f"ERROR: Sensitive path in archive: {member.name}", file=sys.stderr)
                raise SystemExit(1)
            if path.name == ".env" or path.name.startswith(".env."):
                print(f"ERROR: Environment file in archive: {member.name}", file=sys.stderr)
                raise SystemExit(1)
except (OSError, tarfile.TarError) as exc:
    print(f"ERROR: Backup verification failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
PYVERIFY

  [[ ! -e "$archive" ]] ||
    fail "Backup archive appeared during creation: $archive"

  chmod 600 "$temp_archive" ||
    fail "Could not set secure permissions on backup archive."
  mv -- "$temp_archive" "$archive" ||
    fail "Could not publish verified backup archive."

  trap - RETURN

  printf 'Backup created and verified: %s\n' "${archive#"$ROOT_DIR/"}"
  printf '%s\n' "Secret and profile-state paths were excluded."
}
case "${1:-}" in
  status)
    [[ "$#" -eq 1 ]] || fail "status accepts no additional arguments."
    status
    ;;
  init)
    [[ "$#" -eq 1 ]] || fail "init accepts no additional arguments."
    init_state
    ;;
  set-state)
    [[ "$#" -eq 3 ]] || fail "set-state requires PROFILE and DEPLOYMENT_STATUS."
    set_state "$2" "$3"
    ;;
  backup)
    [[ "$#" -eq 1 ]] || fail "backup accepts no additional arguments."
    backup
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    fail "Unknown or missing command."
    ;;
esac
