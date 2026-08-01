#!/usr/bin/env bash
# halia installer — bootstraps uv (if needed) and installs the `halia` command.
#
#   curl -LsSf https://.../install.sh | bash      # once published
#   ./install.sh                                  # from a clone
#
# uv provisions its own Python, so the only real requirement is a shell + curl.
set -euo pipefail

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

# 2. Install halia as an isolated uv tool — its own venv, `halia` put on PATH.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "→ installing the halia command…"
uv tool install --force "$SCRIPT_DIR"

# 3. Make sure uv's tool-bin dir is on PATH for future shells.
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "✓ halia installed. Next:"
echo "    halia setup      # choose a provider + paste your API key"
echo "    halia --help"
echo
echo "If 'halia' isn't found, open a new terminal (uv adds ~/.local/bin to PATH)."
