#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  bash scripts/deploy_hotfix.sh [options] -- <tracked-file> [<tracked-file> ...]

Options:
  --pytest <path>       Relevant pytest path. Repeat for multiple paths.
  --restart             Always restart the AstrBot service after install.
  --no-restart          Never restart; hot reload is still used when needed.
  --remote-root <path>  Remote AstrBot root. Default: /home/<ssh-user>/AstrBot.
  --dry-run             Validate the manifest and print the selected operation only.
  --delete              Also delete the listed files on the remote (requires the
                        local file to be gone or marked for removal).
  --full                Full release: rebuild SPA, tar the whole plugin
                        (excluding data/.git/__pycache__), back up the remote
                        copy, and replace it. Ignores the file list.
  --skip-remote-checks  Skip remote pytest/ruff checks (faster iteration).
  --skip-local-checks   Skip local pytest/ruff/node checks.
  --smoke               Run the Playwright SPA smoke test locally after build.
  -h, --help            Show this help.

The script reads target, port, password, and (optionally) API key from
.deploy.local.md. It deploys only the listed committed files, verifies
checksums, runs local and remote targeted checks, and performs one
controlled restart only when required.

Plugin pages: if any file under webui/ or scripts/build_spa.py is listed,
the SPA is rebuilt locally first and the generated pages/humanize/ is
included in the deployment. After install, the plugin is hot-reloaded via
POST /api/v1/plugins/reload (API key from config) instead of a full restart
unless --restart is given. Python changes also hot reload; a full restart is
only used with --restart or when the service is not healthy.
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_root="$(cd "$script_dir/.." && pwd)"
cd "$plugin_root"

tests=()
files=()
restart_mode="auto"
dry_run=false
remote_root=""
delete_files=false
full_release=false
skip_remote_checks=false
skip_local_checks=false
run_smoke=false

while (( $# )); do
    case "$1" in
        --pytest)
            (( $# >= 2 )) || die "--pytest requires a path"
            tests+=("$2")
            shift 2
            ;;
        --restart)
            restart_mode="always"
            shift
            ;;
        --no-restart)
            restart_mode="never"
            shift
            ;;
        --remote-root)
            (( $# >= 2 )) || die "--remote-root requires a path"
            remote_root="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        --delete)
            delete_files=true
            shift
            ;;
        --full)
            full_release=true
            shift
            ;;
        --skip-remote-checks)
            skip_remote_checks=true
            shift
            ;;
        --skip-local-checks)
            skip_local_checks=true
            shift
            ;;
        --smoke)
            run_smoke=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            files=("$@")
            break
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

if "$full_release"; then
    "$dry_run" || true  # dry-run handled later
    needs_restart=false
    needs_build=true
    needs_reload=true
else
    (( ${#files[@]} > 0 )) || die "at least one tracked file is required after --"
    (( ${#tests[@]} > 0 )) || die "at least one --pytest path is required"
fi

if "$full_release"; then
    # Full release: everything under the plugin root except data/.git/caches.
    needs_restart=false
    needs_build=true
    needs_reload=true
    full_files="$(git ls-files)"
    mapfile -t files <<< "$full_files"
    [[ "${#files[@]}" -gt 0 ]] || die "no tracked files to deploy"
else
    validate_relative_path() {
        local path="$1"
        [[ "$path" =~ ^[A-Za-z0-9._/-]+$ ]] || die "unsafe path: $path"
        [[ "$path" != /* && "$path" != *..* ]] || die "unsafe path: $path"
    }

    for file in "${files[@]}"; do
        validate_relative_path "$file"
        [[ -f "$file" ]] || die "file not found: $file"
        git ls-files --error-unmatch -- "$file" >/dev/null
        git diff --quiet -- "$file" || die "file must be committed before deployment: $file"
        git diff --cached --quiet -- "$file" || die "file must be committed before deployment: $file"
    done

    for test_path in "${tests[@]}"; do
        validate_relative_path "$test_path"
        [[ -f "$test_path" ]] || die "pytest path not found: $test_path"
        git ls-files --error-unmatch -- "$test_path" >/dev/null
        git diff --quiet -- "$test_path" || die "test must be committed: $test_path"
        git diff --cached --quiet -- "$test_path" || die "test must be committed: $test_path"
        test_is_deployed=false
        for file in "${files[@]}"; do
            [[ "$file" == "$test_path" ]] && test_is_deployed=true && break
        done
        "$test_is_deployed" || die "pytest path must also be listed for deployment: $test_path"
    done
fi

needs_restart=false
needs_build=false
needs_reload=false
if "$full_release"; then
    needs_build=true
    needs_reload=true
else
    for file in "${files[@]}"; do
        [[ "$file" == webui/* || "$file" == scripts/build_spa.py || "$file" == pages/humanize/* ]] && needs_build=true
        # Hot reload is enough for plugin code and pages; full restart is only
        # forced by --restart or when the service is unhealthy.
        [[ "$file" == webui/* || "$file" == pages/* || "$file" == scripts/build_spa.py \
           || "$file" == *.py && "$file" != tests/* \
           || "$file" == main.py || "$file" == metadata.yaml \
           || "$file" == .astrbot-plugin/* ]] && needs_reload=true
    done
fi
case "$restart_mode" in
    always) needs_restart=true ;;
    never) needs_restart=false ;;
esac

if "$needs_build"; then
    printf 'Rebuilding SPA from webui/ sources...\n'
    python scripts/build_spa.py
    # The build tool must ship with the plugin so remote --check works.
    build_tool_deployed=false
    for file in "${files[@]}"; do [[ "$file" == "scripts/build_spa.py" ]] && build_tool_deployed=true; done
    "$build_tool_deployed" || files+=("scripts/build_spa.py")
    # Generated artifacts must be committed for the manifest check.
    git add pages/humanize
    if ! git diff --cached --quiet -- pages/humanize; then
        die "build changed pages/humanize; please commit the generated files and re-run"
    fi
    # Include generated artifacts in the deployment.
    for generated in $(git ls-files pages/humanize); do
        already=false
        for file in "${files[@]}"; do [[ "$file" == "$generated" ]] && already=true; done
        "$already" || files+=("$generated")
    done
fi

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
[[ "$remote_root" =~ ^/[A-Za-z0-9._/-]+$ && "$remote_root" != *..* ]] || die "unsafe remote root"
plugin_name="$(basename "$plugin_root")"
remote_plugin="$remote_root/data/plugins/$plugin_name"
remote_python="$remote_root/.venv/bin/python"
remote_ruff="$remote_root/.venv/bin/ruff"
revision="$(git rev-parse --short HEAD)"
stamp="$(date +%Y%m%d-%H%M%S)"
remote_stage="/home/$remote_user/.humanize-hotfix-$revision-$stamp"

if "$dry_run"; then
    printf 'dry-run: target=%s port=%s restart=%s reload=%s\n' "$target" "$port" "$needs_restart" "$needs_reload"
    printf 'dry-run: files=%s\n' "${files[*]}"
    printf 'dry-run: pytest=%s\n' "${tests[*]}"
    exit 0
fi

for command_name in awk git mktemp scp sha256sum ssh uv; do
    require_command "$command_name"
done

manifest="$(mktemp -t humanize-hotfix-manifest.XXXXXX)"
askpass="$(mktemp -t humanize-askpass.XXXXXX)"
remote_body="$(mktemp -t humanize-remote.XXXXXX)"
cleanup_local() {
    rm -f -- "$manifest" "$askpass" "$remote_body"
}
trap cleanup_local EXIT

for file in "${files[@]}"; do
    printf '%s  %s\n' "$(sha256sum "$file" | awk '{print $1}')" "$file" >> "$manifest"
done

cat > "$askpass" <<'ASKPASS'
#!/usr/bin/env bash
set -euo pipefail
awk '
    /^- Password:/ {
        value = $0
        sub(/^- Password:[[:space:]]*/, "", value)
        sub(/\r$/, "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^[`"*]+|[`"*]+$/, "", value)
        print value
        exit
    }
' "${HUMANIZE_DEPLOY_CONFIG:?}"
ASKPASS
chmod 700 "$askpass"

export HUMANIZE_DEPLOY_CONFIG="$config_file"
export SSH_ASKPASS="$askpass"
export SSH_ASKPASS_REQUIRE=force
export DISPLAY="${DISPLAY:-humanize-deploy}"

ssh_options=(
    -o BatchMode=no
    -o ConnectTimeout=15
    -o StrictHostKeyChecking=accept-new
    -p "$port"
)

ssh_no_stdin() {
    ssh "${ssh_options[@]}" "$target" "$@" < /dev/null
}

scp_no_stdin() {
    scp -P "$port" -o BatchMode=no -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$@" < /dev/null
}

py_files=()
js_files=()
for file in "${files[@]}"; do
    case "$file" in
        *.py) py_files+=("$file") ;;
        *.js) js_files+=("$file") ;;
    esac
done

printf 'Running local targeted checks...\n'
if "$skip_local_checks"; then
    printf '(local checks skipped)\n'
elif "$full_release"; then
    uv run pytest -q
    uvx ruff format --check .
    uvx ruff check .
    node --check pages/humanize/app.js
else
    uv run pytest -q "${tests[@]}"
    (( ${#py_files[@]} > 0 )) && uvx ruff format --check "${py_files[@]}"
    (( ${#py_files[@]} > 0 )) && uvx ruff check "${py_files[@]}"
    for js in "${js_files[@]}"; do
        node --check "$js"
    done
fi
git diff --check

if "$run_smoke"; then
    printf 'Running SPA smoke test...\n'
    python scripts/smoke_spa.py || die "smoke test failed"
fi

printf 'Staging selected files remotely...\n'
ssh_no_stdin "mkdir -p -- '$remote_stage'"
if "$full_release"; then
    # Full release: ship a tarball of the whole plugin (excluding local-only
    # directories) and let the remote side replace the plugin root.
    tar_pkg="/tmp/humanize-full-$revision-$stamp.tar.gz"
    tar czf "$tar_pkg" \
        --exclude='data' --exclude='.git' --exclude='.agents' --exclude='.pi' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
        --exclude='.pytest_cache' --exclude='.pytest-tmp-current' --exclude='.trae' \
        --exclude='docs' --exclude='.venv' --exclude='.deploy.local.md' .
    scp_no_stdin "$tar_pkg" "$target:$remote_stage/release.tar.gz"
    rm -f "$tar_pkg"
else
    for file in "${files[@]}"; do
        remote_directory="$(dirname "$file")"
        ssh_no_stdin "mkdir -p -- '$remote_stage/$remote_directory'"
        scp_no_stdin "$file" "$target:$remote_stage/$file"
    done
    scp_no_stdin "$manifest" "$target:$remote_stage/manifest.sha256"
fi

{
    printf 'remote_stage=%q\n' "$remote_stage"
    printf 'remote_plugin=%q\n' "$remote_plugin"
    printf 'remote_python=%q\n' "$remote_python"
    printf 'remote_ruff=%q\n' "$remote_ruff"
    printf 'remote_user=%q\n' "$remote_user"
    printf 'remote_root=%q\n' "$remote_root"
    printf 'revision=%q\n' "$revision"
    printf 'stamp=%q\n' "$stamp"
    printf 'needs_restart=%q\n' "$needs_restart"
    printf 'needs_reload=%q\n' "$needs_reload"
    printf 'deploy_api_key=%q\n' "$deploy_api_key"
    printf 'full_release=%q\n' "$full_release"
    printf 'skip_remote_checks=%q\n' "$skip_remote_checks"
    printf 'delete_files=%q\n' "$delete_files"
    printf 'files=(\n'
    for file in "${files[@]}"; do
        printf '    %q\n' "$file"
    done
    printf ')\n'
    printf 'tests=(\n'
    for test_path in "${tests[@]}"; do
        printf '    %q\n' "$test_path"
    done
    printf ')\n'
    printf 'py_files=(\n'
    for py_file in "${py_files[@]}"; do
        printf '    %q\n' "$py_file"
    done
    printf ')\n'
    cat <<'REMOTE'
set -euo pipefail

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

sudo_run() {
    printf '%s\n' "$deploy_password" | sudo -S -p '' "$@"
}

cleanup_stage() {
    [[ "$remote_stage" == "/home/$remote_user/.humanize-hotfix-"* ]] || return
    rm -rf -- "$remote_stage"
}
trap cleanup_stage EXIT

cd "$remote_stage"
if "$full_release"; then
    # Full release: unpack the tarball over the plugin and keep runtime data.
    # No local backup: the plugin is versioned in git.
    sudo_run mkdir -p "$remote_plugin"
    sudo_run tar xzf "$remote_stage/release.tar.gz" -C "$remote_plugin" --strip-components=0
    sudo_run chown -R "$remote_user:$remote_user" "$remote_plugin" 2>/dev/null || \
        sudo_run chown -R "$remote_user" "$remote_plugin"
else
    sha256sum -c manifest.sha256

    for file in "${files[@]}"; do
        sudo_run mkdir -p -- "$(dirname "$remote_plugin/$file")"
        sudo_run install -m 0644 -- "$remote_stage/$file" "$remote_plugin/$file"
    done

    for file in "${files[@]}"; do
        expected="$(awk -v path="$file" '$2 == path {print $1}' manifest.sha256)"
        actual="$(sha256sum "$remote_plugin/$file" | awk '{print $1}')"
        [[ "$actual" == "$expected" ]] || die "installed checksum mismatch: $file"
    done

    if "$delete_files"; then
        for file in "${files[@]}"; do
            sudo_run rm -f -- "$remote_plugin/$file" || true
        done
    fi
fi

if "$skip_remote_checks"; then
    printf '(remote checks skipped)\n'
elif "$full_release"; then
    cd "$remote_plugin"
    sudo_run "$remote_python" -m pytest -q
    sudo_run "$remote_ruff" format --check .
    sudo_run "$remote_ruff" check .
else
    cd "$remote_plugin"
    sudo_run "$remote_python" -m pytest -q "${tests[@]}"
    (( ${#py_files[@]} > 0 )) && sudo_run "$remote_ruff" format --check "${py_files[@]}"
    (( ${#py_files[@]} > 0 )) && sudo_run "$remote_ruff" check "${py_files[@]}"
fi

if "$needs_restart"; then
    old_session="$(tmux list-sessions -F '#{session_name}' 2>/dev/null | awk '/^astrbot-service-/{value=$0} END {print value}')"
    [[ -n "$old_session" ]] || die "active AstrBot tmux session not found"
    tmux send-keys -t "$old_session:0.0" C-c
    stopped=false
    for _ in $(seq 1 20); do
        if ! curl -sS -o /dev/null --max-time 2 http://127.0.0.1:6185/; then
            stopped=true
            break
        fi
        sleep 1
    done
    "$stopped" || die "previous service did not stop cleanly"

    new_session="astrbot-service-$revision"
    if tmux has-session -t "$new_session" 2>/dev/null; then
        new_session="$new_session-$stamp"
    fi
    tmux new-session -d -s "$new_session" "cd $remote_root && exec sudo -k -S -p '' $remote_python main.py"
    sleep 1
    tmux send-keys -t "$new_session:0.0" -l "$deploy_password"
    tmux send-keys -t "$new_session:0.0" Enter

    status=""
    for _ in $(seq 1 20); do
        status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:6185/ || true)"
        [[ "$status" == "200" ]] && break
        sleep 1
    done
    [[ "$status" == "200" ]] || die "service did not become healthy after restart"
elif "$needs_reload"; then
    [[ -n "$deploy_api_key" ]] || die "hot reload requires an API key (add '- API key:' to .deploy.local.md)"
    reload_body="$(curl -sS --max-time 15 -X POST \
        -H "X-API-Key: $deploy_api_key" \
        -H "Content-Type: application/json" \
        -d '{"plugin_id":"astrbot_plugin_humanize"}' \
        http://127.0.0.1:6185/api/v1/plugins/reload)"
    printf '%s' "$reload_body" | grep -q '"status":"ok"' || die "plugin hot reload failed: $reload_body"
    printf 'plugin hot reloaded\n'
fi

printf 'deployment=ok\n'
REMOTE
} > "$remote_body"

printf 'Installing, validating, and%s restarting remotely...\n' "$("$needs_restart" && printf '' || printf ' not')"
{
    printf 'IFS= read -r deploy_password\n'
    printf '%s\n' "$password"
    cat "$remote_body"
} | ssh "${ssh_options[@]}" "$target" /bin/bash -s
