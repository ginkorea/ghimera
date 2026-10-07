#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
# These fixture dependencies are operator inputs, never guessed or downloaded.
# Refuse before a long suite rather than producing dozens of missing-env reds.
: "${CHIMERA_TEST_BROWSER:?Set CHIMERA_TEST_BROWSER to the installed Chromium executable}"
: "${CHIMERA_TEST_ISOLATOR:?Set CHIMERA_TEST_ISOLATOR to the installed browser isolator}"
for gate_fixture_executable in "$CHIMERA_TEST_BROWSER" "$CHIMERA_TEST_ISOLATOR"; do
  if [[ "$gate_fixture_executable" != /* || ! -f "$gate_fixture_executable" || ! -x "$gate_fixture_executable" ]]; then
    printf 'Invalid gate fixture executable: %s\n' "$gate_fixture_executable" >&2
    exit 2
  fi
done
GHIMERA_GATE_PYTHON="${GHIMERA_GATE_PYTHON:-${CHIMERA_GATE_PYTHON:-$PWD/.venv/bin/python}}"
export PYTHONPATH="$PWD/src"
export PYTHONDONTWRITEBYTECODE=1
GHIMERA_GATE_WORK="${GHIMERA_GATE_WORK:-$PWD/gate-work}"
if [[ "$GHIMERA_GATE_WORK" != /* || "$GHIMERA_GATE_WORK" == / ]]; then
  printf 'GHIMERA_GATE_WORK must name an owned absolute working directory\n' >&2
  exit 2
fi
export UV_CACHE_DIR="${GHIMERA_GATE_CACHE:-${CHIMERA_GATE_CACHE:-$GHIMERA_GATE_WORK/uv}}"
unset TAIPAN_TOKEN TAIPAN_COGNITO_ACCESS_TOKEN
mkdir -p "$GHIMERA_GATE_WORK"
printf 'Gate work directory: %s\n' "$GHIMERA_GATE_WORK"

"$GHIMERA_GATE_PYTHON" -c 'import pathlib, sys, ghimera; print(sys.executable, sys.version); print(ghimera.__file__); assert sys.version_info >= (3, 11); assert pathlib.Path(ghimera.__file__).resolve().is_relative_to(pathlib.Path.cwd() / "src")'
uv lock --check --offline
"$GHIMERA_GATE_PYTHON" -m ruff check src/ghimera src/chimera tests
"$GHIMERA_GATE_PYTHON" -m ruff format --check src/ghimera src/chimera tests
"$GHIMERA_GATE_PYTHON" -m mypy --strict src/ghimera src/chimera
"$GHIMERA_GATE_PYTHON" -m pytest tests -q -p no:cacheprovider --basetemp="$GHIMERA_GATE_WORK/pytest"
