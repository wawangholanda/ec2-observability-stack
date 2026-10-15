#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'USAGE'
Usage:
  ./quick-start.sh
  ./quick-start.sh --check
  ./quick-start.sh --mode single
  ./quick-start.sh --mode ha
  ./quick-start.sh --mode scale
  ./quick-start.sh --profile-status
  ./quick-start.sh --backup-profile

Profiles:
  single  Single Instance (deployment not enabled yet)
  ha      High Availability (planned; Proxmox lab testing required)
  scale   High Traffic / Scale-out (planned; Proxmox lab testing required)
  --check Check only; no deployment
USAGE
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

MODE="menu"
PROFILE_ACTION=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile-status|--backup-profile)
      [[ "$MODE" == "menu" && -z "$PROFILE_ACTION" ]] ||
        fail "Specify only one action."
      PROFILE_ACTION="$1"
      shift
      ;;
    --check)
      [[ "$MODE" == "menu" ]] || fail "Specify only one mode."
      MODE="check"
      shift
      ;;
    --mode)
      [[ "$MODE" == "menu" ]] || fail "Specify only one mode."
      [[ "$#" -ge 2 ]] || fail "--mode requires single, ha, or scale."
      case "$2" in
        single|ha|scale) MODE="$2" ;;
        *) fail "Unknown profile: $2" ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "Unknown argument: $1"
      ;;
  esac
done

if [[ -n "$PROFILE_ACTION" ]]; then
  case "$PROFILE_ACTION" in
    --profile-status)
      exec "$ROOT_DIR/scripts/profile-manager.sh" status
      ;;
    --backup-profile)
      exec "$ROOT_DIR/scripts/profile-manager.sh" backup
      ;;
  esac
fi

if [[ "$MODE" == "menu" ]]; then
  printf '\nEC2 Observability Stack\n'
  printf '%s\n' \
    '1) Single Instance (WireGuard LXC)' \
    '2) High Availability (Proxmox lab; not enabled)' \
    '3) High Traffic / Scale-out (Proxmox lab; not enabled)' \
    '4) Check Only (no deployment)'
  printf '\nSelect profile [1-4]: '
  read -r choice

  case "$choice" in
    1) MODE="single" ;;
    2) MODE="ha" ;;
    3) MODE="scale" ;;
    4) MODE="check" ;;
    *) fail "Invalid selection. Choose 1, 2, 3, or 4." ;;
  esac
fi

case "$MODE" in
  ha)
    fail "High Availability is planned only. No changes were made. Implement and test it in the Proxmox lab first."
    ;;
  scale)
    fail "High Traffic / Scale-out is planned only. No changes were made. Implement and test it in the Proxmox lab first."
    ;;
  single)
    ;;
  check)
    ;;
esac

ENV_FILE="$ROOT_DIR/.secrets/.env"
INVENTORY_FILE="$ROOT_DIR/inventory/lab/hosts.yml"

if [[ "$MODE" == "single" ]]; then
  command -v flock >/dev/null 2>&1 ||
    fail "Required command not found: flock"

  mkdir -p -- "$ROOT_DIR/.state"
  chmod 700 "$ROOT_DIR/.state"
  [[ ! -L "$ROOT_DIR/.state" ]] ||
    fail "State directory must not be a symlink."

  exec 9>"$ROOT_DIR/.state/deployment.lock"
  flock -n 9 ||
    fail "Another deployment appears to be running. No deployment was started."
fi

info "Checking local dependencies"
for cmd in ansible ansible-playbook ansible-inventory python ssh; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
done

# Automated workstation support is intentionally limited to these platforms.
WORKSTATION_SUPPORTED=false
case "$(uname -s)" in
  Darwin)
    WORKSTATION_SUPPORTED=true
    ;;
  Linux)
    if [[ -r /etc/os-release ]]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      case "${ID:-}" in
        ubuntu|debian|kali) WORKSTATION_SUPPORTED=true ;;
      esac
    fi
    ;;
esac

if [[ "$WORKSTATION_SUPPORTED" == true ]]; then
  info "Inspecting supported workstation platform"
  [[ -s "$ROOT_DIR/playbooks/workstation.yml" ]] || fail "Required file is missing or empty: playbooks/workstation.yml"
  ansible-playbook "$ROOT_DIR/playbooks/workstation.yml" --syntax-check
  ansible-playbook "$ROOT_DIR/playbooks/workstation.yml"
else
  info "Skipping workstation playbook on unsupported operating system"
  printf '%s\n' \\
    "Supported automated workstation platforms: Ubuntu, Debian, Kali Linux, and macOS." \\
    "Install Python 3, Ansible Core, Git, and the OpenSSH client manually." \\
    "Check Only can continue if those dependencies are already available."
fi

info "Checking required project files"
for file in \
  "$INVENTORY_FILE" \
  "$ENV_FILE" \
  "$ROOT_DIR/playbooks/preflight.yml" \
  "$ROOT_DIR/playbooks/configure-monitoring.yml" \
  "$ROOT_DIR/files/monitoring/docker-compose.yml" \
  "$ROOT_DIR/files/monitoring/prometheus/prometheus.yml" \
  "$ROOT_DIR/files/monitoring/loki/config.yml" \
  "$ROOT_DIR/files/monitoring/grafana/provisioning/datasources/datasources.yml" \
  "$ROOT_DIR/files/monitoring/grafana/provisioning/dashboards/dashboards.yml"
do
  [[ -s "$file" ]] || fail "Required file is missing or empty: ${file#"$ROOT_DIR/"}"
done

info "Checking secret file permissions and required values"
python - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.stat().st_mode & 0o777 != 0o600:
    print("ERROR: .secrets/.env must have permission 600", file=sys.stderr)
    raise SystemExit(1)

required = {
    "GRAFANA_ADMIN_USER",
    "GRAFANA_ADMIN_PASSWORD",
    "GRAFANA_SECRET_KEY",
}
values = {}

for raw_line in path.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key in required:
        if key in values:
            print(f"ERROR: Duplicate key: {key}", file=sys.stderr)
            raise SystemExit(1)
        values[key] = value.strip()

missing = sorted(required - values.keys())
invalid = sorted(
    key for key in required & values.keys()
    if not values[key] or "CHANGE_ME" in values[key]
)

if missing:
    print("ERROR: Missing required keys:", ", ".join(missing), file=sys.stderr)
if invalid:
    print("ERROR: Empty or placeholder values:", ", ".join(invalid), file=sys.stderr)
if missing or invalid:
    raise SystemExit(1)

print("Secret validation: PASS (values not displayed)")
PY

info "Checking inventory"
ansible-inventory -i "$INVENTORY_FILE" --graph >/dev/null
ansible -i "$INVENTORY_FILE" monitoring --list-hosts

info "Checking playbook syntax"
ansible-playbook -i "$INVENTORY_FILE" playbooks/preflight.yml --syntax-check
ansible-playbook -i "$INVENTORY_FILE" playbooks/configure-monitoring.yml --syntax-check
ansible-playbook -i "$INVENTORY_FILE" playbooks/deploy-monitoring.yml --syntax-check

info "Preparing temporary SSH/become authentication files"
AUTH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ec2-observability-auth.XXXXXX")"
chmod 700 "$AUTH_DIR"
cleanup_auth() {
  rm -rf -- "$AUTH_DIR"
}
trap cleanup_auth EXIT HUP INT TERM

read -r -s -p "Enter the SSH/sudo password for remote host 'tools': " REMOTE_PASSWORD
printf '\n'
[[ -n "$REMOTE_PASSWORD" ]] || fail "Password must not be empty."
umask 077
printf '%s' "$REMOTE_PASSWORD" > "$AUTH_DIR/ssh-password"
printf '%s' "$REMOTE_PASSWORD" > "$AUTH_DIR/become-password"
unset REMOTE_PASSWORD
chmod 600 "$AUTH_DIR/ssh-password" "$AUTH_DIR/become-password"
AUTH_ARGS=(
  --connection-password-file "$AUTH_DIR/ssh-password"
  --become-password-file "$AUTH_DIR/become-password"
)

info "Checking SSH connectivity"
ansible -i "$INVENTORY_FILE" monitoring -m ansible.builtin.ping "${AUTH_ARGS[@]}"

info "Running read-only preflight"
ansible-playbook -i "$INVENTORY_FILE" playbooks/preflight.yml "${AUTH_ARGS[@]}"

if [[ "$MODE" == "check" ]]; then
  printf '\nChecks passed. No monitoring deployment was run.\n'
  exit 0
fi

info "Initializing or validating local profile state"
"$ROOT_DIR/scripts/profile-manager.sh" init

info "Configuring monitoring stack files"
ansible-playbook -i "$INVENTORY_FILE" playbooks/configure-monitoring.yml "${AUTH_ARGS[@]}"

info "Deploying monitoring stack"
ansible-playbook -i "$INVENTORY_FILE" playbooks/deploy-monitoring.yml "${AUTH_ARGS[@]}"

info "Recording successful Single Instance deployment"
"$ROOT_DIR/scripts/profile-manager.sh" set-state single deployed

printf '\nSingle Instance deployment completed successfully.\n'
