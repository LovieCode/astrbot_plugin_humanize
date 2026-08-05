#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  bash scripts/deploy_git.sh [--remote-root <path>] [--dry-run]

Deploy the plugin via git:

  1. Run local checks (pytest, ruff on changed files) and build the SPA.
  2. Push local main to the origin remote (GitHub).
  3. On the remote AstrBot host, git pull the plugin repository.
  4. Hot reload the plugin through POST /api/v1/plugins/reload.

The remote plugin directory must be a git clone of the same repository
(see WEBUI_TODO.md section 10 for the setup). Runtime data under data/ is
gitignored and never touched.

Config: target/port/password/API key are read from .deploy.local.md.
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_root="$(cd "$script_dir/.." && pwd)"
cd "$plugin_root"

dry_run=false
remote_root=""
while (( $# )); do
    case "$1" in
        --remote-root)
            (( $# >= 2 )) || die "--remote-root requires a path"
            remote_root="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

config_file="${HUMANIZE_DEPLOY_CONFIG:-$plugin_root/.deploy.local.md}"
[[ -f "$config_file" ]] || die "deployment config not found: $config_file"

read_config_value() {
    local key="$1"
    awk -v key="$key" '
        $0 ~ "^- " key ":" {
            value = $0
            sub("^- " key ":[[:space:]]*", "", value)
            sub(/\r$/, "", value)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            gsub(/^[`"*]+|[`"*]+$/, "", value)
            print value
            exit
        }
    ' "$config_file"
}

target="$(read_config_value "Target")"
port="$(read_config_value "SSH port")"
password="$(read_config_value "Password")"
deploy_api_key="$(read_config_value "API key")"
[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || die "invalid Target in deployment config"
[[ "$port" =~ ^[0-9]{1,5}$ ]] || die "invalid SSH port in deployment config"
[[ -n "$password" ]] || die "empty deployment password"

remote_user="${target%@*}"
remote_root="${remote_root:-${HUMANIZE_REMOTE_ASTRBOT_ROOT:-/home/$remote_user/AstrBot}}"
plugin_name="$(basename "$plugin_root")"
remote_plugin="$remote_root/data/plugins/$plugin_name"

# ---- safety: nothing uncommitted / unpushed ----
git diff --quiet || die "worktree has uncommitted changes"
git diff --cached --quiet || die "index has staged changes"
branch="$(git branch --show-current)"
[[ "$branch" == "main" ]] || die "expected main branch, got $branch"

local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse origin/main 2>/dev/null || echo '')"
if [[ "$local_rev" == "$remote_rev" ]]; then
    printf 'already up to date with origin/main\n'
else
    printf 'local %s vs origin/main %s\n' "$local_rev" "$remote_rev"
    [[ "$local_rev" != "" && "$remote_rev" != "" ]] || die "cannot compare revisions"
    git merge-base --is-ancestor origin/main HEAD || die "local history is not a fast-forward of origin/main"
fi

# ---- local checks ----
printf 'Running local checks...\n'
uv run pytest -q tests/test_webui_static.py tests/test_sdk_webapi_endpoints.py
uvx ruff format --check humanize tests scripts/build_spa.py main.py
uvx ruff check humanize tests scripts/build_spa.py main.py
python scripts/build_spa.py --check || die "SPA build is out of date; run scripts/build_spa.py and commit"
git diff --check

if "$dry_run"; then
    printf 'dry-run: target=%s port=%s remote_plugin=%s\n' "$target" "$port" "$remote_plugin"
    printf 'dry-run: push %s -> origin/main, remote git pull + hot reload\n' "$local_rev"
    exit 0
fi

# ---- push ----
if [[ "$local_rev" != "$remote_rev" ]]; then
    printf 'Pushing to origin...\n'
    git push origin main
fi

# ---- remote pull + reload ----
printf 'Updating remote plugin and hot reloading...\n'
{
    printf 'IFS= read -r deploy_password\n'
    printf '%s\n' "$password"
    cat <<REMOTE
set -euo pipefail
sudo_run() { printf '%s\n' "\$deploy_password" | sudo -S -p '' "\$@"; }
cd "$remote_plugin"
sudo_run git pull --ff-only origin main
# keep runtime data (gitignored) untouched
sudo_run chown -R "$remote_user:$remote_user" "$remote_plugin" 2>/dev/null || true
[[ -n "$deploy_api_key" ]] || die "API key missing in config"
body="\$(curl -sS --max-time 15 -X POST \\
    -H "X-API-Key: $deploy_api_key" \\
    -H "Content-Type: application/json" \\
    -d '{"plugin_id":"$plugin_name"}' \\
    http://127.0.0.1:6185/api/v1/plugins/reload)"
printf '%s' "\$body" | grep -q '"status":"ok"' || die "hot reload failed: \$body"
printf 'deployment=ok (git pull + hot reload)\n'
REMOTE
} | ssh -p "$port" -o BatchMode=no -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new \
    "$target" /bin/bash -s
