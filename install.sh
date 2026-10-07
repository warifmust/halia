#!/usr/bin/env bash
# halia installer — bootstraps uv (if needed) and installs the `halia` command.
#
#   curl -LsSf https://raw.githubusercontent.com/warifmust/halia/main/install.sh | bash
#   ./install.sh                                  # from a local clone
#
# halia is a Python CLI. uv provisions its own Python (>=3.11), so the only real
# requirement is a shell + curl. Works on macOS (Apple Silicon + Intel) and Linux.
set -euo pipefail

REPO_URL="https://github.com/warifmust/halia.git"
REF="main"

echo "Installing halia…"

# 1. Ensure uv (Python toolchain + package manager) is present.
if ! command -v uv >/dev/null 2>&1; then
  echo "→ uv not found — installing it first…"
  # Download the installer to a file (inspectable, and avoids curl|sh pipe quirks).
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/halia-uv-install.sh
  sh /tmp/halia-uv-install.sh
  rm -f /tmp/halia-uv-install.sh
  # uv installs to ~/.local/bin (or ~/.cargo/bin on older setups).
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Could not find uv after install — open a new terminal and re-run this script."
  exit 1
fi

# 2. Choose the install source: a local clone if this script sits in the repo,
#    otherwise install straight from GitHub (the curl | bash path on a fresh machine).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "${SCRIPT_DIR:-}" ] && [ -f "${SCRIPT_DIR}/pyproject.toml" ]; then
  TARGET="${SCRIPT_DIR}"
  echo "→ installing the halia command from this clone…"
else
  TARGET="git+${REPO_URL}@${REF}"
  echo "→ installing the halia command from ${REPO_URL}@${REF} …"
fi

# 3. Preserve the extras an existing install already had. `uv tool install --force`
#    rebuilds the venv from THIS command line alone and rewrites the receipt to
#    match, so a re-run used to silently drop `mcp` and the pinned `cua-driver` —
#    leaving "mcp not installed" and the `cursor_motion` CUA failure behind. The
#    previous install knows what it had: halia records the specs in its own config
#    (~/.halia/config.json, which uv never touches) as well as the receipt, so ask
#    it. An install predating this helper behaves exactly as it did before.
WITH_ARGS=()
TOOL_DIR="$(uv tool dir 2>/dev/null || true)"
TOOL_PY="${TOOL_DIR:+${TOOL_DIR}/halia/bin/python}"
if [ -n "$TOOL_PY" ] && [ -x "$TOOL_PY" ]; then
  EXTRAS="$("$TOOL_PY" -c 'from halia.upgrade import upgrade_with_requirements as f; print("\n".join(f()))' 2>/dev/null || true)"
  while IFS= read -r spec; do
    if [ -n "$spec" ]; then
      WITH_ARGS+=(--with "$spec")
    fi
  done <<< "$EXTRAS"
  if [ "${#WITH_ARGS[@]}" -gt 0 ]; then
    echo "→ keeping installed extras: ${EXTRAS//$'\n'/ }"
  fi
fi

# 4. Install (or update) halia as an isolated uv tool — its own venv, `halia` on PATH.
#    --force re-pulls the latest, so re-running this script updates an existing install.
#    ${WITH_ARGS[@]+…} keeps this safe under `set -u` when the array is empty (bash 3.2).
uv tool install --force "$TARGET" ${WITH_ARGS[@]+"${WITH_ARGS[@]}"}

# 5. Make sure uv's tool-bin dir is on PATH for future shells.
uv tool update-shell >/dev/null 2>&1 || true

# 6. Confirm the version that landed (best-effort — PATH may only apply to new shells).
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
VERSION="$(halia --version 2>/dev/null || true)"

echo
echo "✓ ${VERSION:-halia} installed. Next:"
echo "    halia setup         # choose a provider + paste your API key"
echo "    halia               # start the chat shell (/help lists commands)"
echo "    halia qa            # a vertical (finance / data / research / qa / …)"
echo "    halia --resume <id> # pick up a past session"
echo
echo "If 'halia' isn't found, open a new terminal (uv adds ~/.local/bin to PATH)."
