#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
CHIMERA_GATE_PYTHON="${CHIMERA_GATE_PYTHON:-$PWD/.venv/bin/python}"
export PYTHONPATH="$PWD/src"
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="${CHIMERA_GATE_CACHE:-$PWD/gate-work/uv}"
unset TAIPAN_TOKEN TAIPAN_COGNITO_ACCESS_TOKEN
mkdir -p "$PWD/gate-work"

"$CHIMERA_GATE_PYTHON" -c 'import pathlib, sys, chimera; print(sys.executable, sys.version); print(chimera.__file__); assert sys.version_info >= (3, 11); assert pathlib.Path(chimera.__file__).resolve().is_relative_to(pathlib.Path.cwd() / "src")'
uv lock --check --offline
"$CHIMERA_GATE_PYTHON" -m ruff check src/chimera tests
"$CHIMERA_GATE_PYTHON" -m ruff format --check src/chimera tests
"$CHIMERA_GATE_PYTHON" -m mypy --strict src/chimera
"$CHIMERA_GATE_PYTHON" -m pytest tests -q -p no:cacheprovider --basetemp="$PWD/gate-work/pytest"
