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

# 3. Install halia as an isolated uv tool — its own venv, `halia` put on PATH.
uv tool install --force "$TARGET"

# 4. Make sure uv's tool-bin dir is on PATH for future shells.
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "✓ halia installed. Next:"
echo "    halia setup         # choose a provider + paste your API key"
echo "    halia               # start the chat shell (or 'halia qa' for the QA vertical)"
echo "    halia --resume <id> # pick up a past session"
echo "    halia gateway setup # (optional) Telegram notifications"
echo
echo "If 'halia' isn't found, open a new terminal (uv adds ~/.local/bin to PATH)."
