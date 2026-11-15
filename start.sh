#!/usr/bin/env bash
# Sets Margin up the first time, then starts it. Run it again any time: ./start.sh
set -e
cd "$(dirname "$0")"

PY=""
for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 wasn't found. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Margin needs Python 3.9 or newer. Install a newer one from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Setting up Margin (the first run takes a couple of minutes)..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ] || [ start.sh -nt .venv/.installed ]; then
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt

  # Optional parts. Each one is tried on its own; Margin still runs if one can't be installed.
  optional=(
    "fastembed>=0.4"       # matching by meaning (downloads a ~130 MB model the first time it is used)
    "google-genai>=1.0"    # handwriting reading with Gemini (needs GEMINI_API_KEY in .env)
    "pillow-heif>=0.16"    # iPhone .heic photos
  )
  if [ "$(uname)" = "Darwin" ]; then
    optional+=("ocrmac>=1.0")  # handwriting reading with Apple Vision: no key, works offline
  fi
  for package in "${optional[@]}"; do
    if ! python -m pip install --quiet "$package"; then
      echo "  (couldn't install optional part: $package; continuing without it)"
    fi
  done
  touch .venv/.installed
fi

[ -f .env ] || cp .env.example .env

exec python -m margin "$@"
