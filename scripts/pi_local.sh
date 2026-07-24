#!/usr/bin/env bash
# Run Pi with project-local state and the existing Codex-compatible provider.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
codex_config="${USERPROFILE:-$HOME}/.codex/config.toml"

if [[ ! -f "$codex_config" ]]; then
  printf 'Codex configuration was not found: %s\n' "$codex_config" >&2
  exit 1
fi

export PI_CODING_AGENT_DIR="$project_root/.pi"
export PI_CODEX_API_KEY="$(CODEX_CONFIG="$codex_config" python - <<'PY'
import os
from pathlib import Path
import tomllib

config_path = Path(os.environ["CODEX_CONFIG"])
config = tomllib.loads(config_path.read_text(encoding="utf-8"))
provider_name = config.get("model_provider")
provider = config.get("model_providers", {}).get(provider_name, {})
api_key = provider.get("api_key")

if not isinstance(api_key, str) or not api_key:
    raise SystemExit("Codex provider API key is missing.")

print(api_key, end="")
PY
)"

exec pi "$@"
