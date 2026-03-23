#!/usr/bin/env bash
set -euo pipefail

# Портативная сборка one-file через PyInstaller под Linux.
# По возможности использует `uv`, т.к. там pygame собран корректно (mixer/font).
# Иначе fallback на отдельный Linux-venv, т.к. текущая `.venv` может быть Windows-формата.

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT_DIR="$(pwd)"

PYI_EXTRA_ARGS="${PYI_EXTRA_ARGS:-}"

if command -v uv >/dev/null 2>&1; then
  echo "Using uv environment for build (pygame mixer/font working)."
  # shellcheck disable=SC2086
  uv pip install -q "pyinstaller>=6.0" "pillow>=10" || true
  # shellcheck disable=SC2086
  uv run python -m PyInstaller --clean -y $PYI_EXTRA_ARGS shefostycoon.spec
  echo
  echo "Ready: dist/SHEFOS_Tycoon"
  exit 0
fi

VENV_DIR="$ROOT_DIR/.venv-linux"
PY="$VENV_DIR/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Creating venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "Using Python: $PY"

"$PY" -m pip install -q -U pip setuptools wheel

if ! "$PY" -c "import cryptography, pygame" >/dev/null 2>&1; then
  echo "Installing dependencies: pygame + cryptography"
  "$PY" -m pip install -q "pygame>=2.6.1" "cryptography>=46.0.5" "Pillow>=10"
else
  # pygame/cryptography уже есть, но Pillow нужен для fallback рендера текста.
  "$PY" -m pip install -q "Pillow>=10"
fi

echo "Installing PyInstaller..."
"$PY" -m pip install -q "pyinstaller>=6.0"

echo "Building one-file binary..."

# shellcheck disable=SC2086
"$PY" -m PyInstaller --clean -y $PYI_EXTRA_ARGS shefostycoon.spec

echo
echo "Ready: dist/SHEFOS_Tycoon"
