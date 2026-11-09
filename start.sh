#!/usr/bin/env bash
# Start Mami on this laptop:  ./start.sh
# First run: creates backend/.env for you to fill in, then installs everything.
set -euo pipefail
cd "$(dirname "$0")/backend"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created backend/.env and opened it. Fill in the 2 values at the top, save,"
  echo "then run:  bash start.sh"
  echo
  [ "$(uname)" = "Darwin" ] && open -e .env
  exit 0
fi

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Mami needs Python 3.10 or newer. On a Mac: install it from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: installing (takes a minute)…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  if ! { python -m pip install --upgrade pip && python -m pip install -e .; } > .venv/install.log 2>&1; then
    echo "Install failed. Last lines of backend/.venv/install.log:"
    tail -20 .venv/install.log
    exit 1
  fi
  touch .venv/.installed
fi

# Fill in an encryption key once, if empty.
if grep -q '^APP_ENCRYPTION_KEY=$' .env; then
  KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
  python - "$KEY" <<'EOF'
import pathlib, sys
p = pathlib.Path(".env")
p.write_text(p.read_text().replace("APP_ENCRYPTION_KEY=\n", f"APP_ENCRYPTION_KEY={sys.argv[1]}\n"))
EOF
fi

# On a Mac, keep it awake while Mami runs (the screen can still sleep).
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python -m app.local
else
  exec python -m app.local
fi
