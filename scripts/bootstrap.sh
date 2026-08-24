#!/usr/bin/env bash
#
# Create a local dev/test venv for the SolarEdge ONE Home Assistant integration.
#
# Reproducible from a fresh clone on any machine. Creates ".venv" in the repo
# root (gitignored) with Home Assistant, the test harness, ruff, and the
# aiosolaredge-one client library.
#
# Usage:
#   scripts/bootstrap.sh
#
# Overrides (env vars):
#   PYTHON=python3.13         interpreter to build the venv from (must be 3.13)
#   VENV=/path/to/.venv       where to create the venv
#   SOLAREDGE_LIB_PATH=../solaredge-v2
#                             editable-install the client from this local checkout
#                             (auto-detected at ../solaredge-v2); otherwise the
#                             pinned version from manifest.json is installed from PyPI
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO_ROOT/.venv}"
PYTHON="${PYTHON:-python3.13}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: '$PYTHON' not found. Home Assistant needs Python 3.13." >&2
  echo "       Install it (e.g. 'brew install python@3.13') or set PYTHON=..." >&2
  exit 1
fi

echo "==> Creating venv at $VENV ($($PYTHON --version))"
"$PYTHON" -m venv "$VENV"
PY="$VENV/bin/python"

echo "==> Upgrading pip"
"$PY" -m pip install --quiet --upgrade pip

echo "==> Installing Home Assistant test harness + ruff"
# Unpinned, matching CI (.github/workflows/ci.yml). This pulls in the correct
# Home Assistant version transitively.
"$PY" -m pip install --quiet pytest-homeassistant-custom-component ruff

# Install the client library. Prefer an editable local checkout (for cross-repo
# work); otherwise install the exact version pinned in manifest.json from PyPI.
LIB_PATH="${SOLAREDGE_LIB_PATH:-$REPO_ROOT/../solaredge-v2}"
if [ -f "$LIB_PATH/pyproject.toml" ]; then
  echo "==> Installing aiosolaredge-one (editable) from $LIB_PATH"
  "$PY" -m pip install --quiet -e "$LIB_PATH"
else
  PIN="$("$PY" - "$REPO_ROOT/custom_components/solaredge_one/manifest.json" <<'PYEOF'
import json, sys
reqs = json.load(open(sys.argv[1]))["requirements"]
pin = next((r for r in reqs if r.startswith("aiosolaredge-one")), "aiosolaredge-one")
print(pin)
PYEOF
)"
  echo "==> Installing $PIN from PyPI (no local library checkout found)"
  "$PY" -m pip install --quiet "$PIN"
fi

echo
echo "Done. Run from the repo root:"
echo "  $VENV/bin/python -m pytest tests -q"
echo "  $VENV/bin/ruff check custom_components/solaredge_one tests"
