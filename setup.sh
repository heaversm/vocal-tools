#!/usr/bin/env bash
# One-time install: a Python environment in .venv/ and Seed-VC in vendor/seed-vc/.
# Models (about 2.5 GB) download on first use, not here.
set -euo pipefail
cd "$(dirname "$0")"

SEED_VC_COMMIT=51383efd921027683c89e5348211d93ff12ac2a8  # Seed-VC as of April 2025, the version the patch is for

PY=""
for candidate in python3.11 python3.10; do
  if command -v "$candidate" >/dev/null; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  echo "Needs Python 3.10 or 3.11 (Seed-VC's pinned libraries don't install on newer versions)."
  echo "On a Mac: brew install python@3.11"
  exit 1
fi
if ! command -v ffmpeg >/dev/null; then
  echo "Needs ffmpeg to read mp3/m4a files. On a Mac: brew install ffmpeg"
  exit 1
fi

if [ ! -d vendor/seed-vc/.git ]; then
  echo "Downloading Seed-VC..."
  mkdir -p vendor
  git clone --quiet https://github.com/Plachtaa/seed-vc.git vendor/seed-vc
fi
git -C vendor/seed-vc checkout --quiet "$SEED_VC_COMMIT"
if git -C vendor/seed-vc apply --check ../../patches/seed-vc-apple-silicon.patch 2>/dev/null; then
  git -C vendor/seed-vc apply ../../patches/seed-vc-apple-silicon.patch
  echo "Applied Apple Silicon fixes to Seed-VC."
fi

if [ ! -d .venv ]; then
  echo "Creating Python environment with $PY..."
  "$PY" -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
echo "Installing libraries (a few GB)..."
.venv/bin/pip install --quiet -r requirements.txt
# Second step on purpose: see the note in requirements-lyrics.txt. pip warns about descript-audiotools
# and protobuf here; that's expected and harmless.
.venv/bin/pip install --quiet -r requirements-lyrics.txt

./make-app.sh

echo
echo "Done. Open Vocal Tools from ~/Applications, or run ./start.command"
