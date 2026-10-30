#!/usr/bin/env bash
# PR Guardian 1.1.0rc1 — local only. Binds 127.0.0.1, never 0.0.0.0.
# Uploads are enabled for this process only; AI stays off. Nothing leaves this machine.
set -uo pipefail

cd "$(dirname "$0")"
PORT="${1:-8000}"

find_python() {
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON="$(find_python)"; then
  echo "[error] Python was not found. Install Python 3.12 or newer and run this script again." >&2
  exit 1
fi

"$PYTHON" scripts/check_env.py
STATUS=$?
if [ "$STATUS" -eq 2 ]; then
  echo "[error] Install Python 3.12 or newer, then run this script again." >&2
  exit 1
elif [ "$STATUS" -eq 1 ]; then
  echo "[error] The project files look incomplete. Re-download the repository and retry." >&2
  exit 1
elif [ "$STATUS" -eq 3 ]; then
  echo
  echo "[notice] This would run:  $PYTHON -m pip install -e '.[dev]'"
  echo "         It changes only this Python environment, not system variables."
  printf 'Install the project dependencies now? [y/N] '
  read -r ANSWER
  case "$ANSWER" in
    y|Y|yes|YES)
      "$PYTHON" -m pip install -e '.[dev]' || { echo "[error] Installation failed." >&2; exit 1; }
      "$PYTHON" scripts/check_env.py || { echo "[error] Still not ready after installing." >&2; exit 1; }
      ;;
    *)
      echo "[stopped] Nothing was installed. Install manually with:"
      echo "          $PYTHON -m pip install -e '.[dev]'"
      exit 1
      ;;
  esac
fi

export PR_GUARDIAN_UPLOAD_ENABLED=1
export PR_GUARDIAN_UPLOAD_AI=0
export PR_GUARDIAN_PUBLIC_MODE=0
export PR_GUARDIAN_ALLOW_LIVE_LLM=0
export PR_GUARDIAN_ALLOW_TEST_EXECUTION=1

echo
echo "  PR Guardian 1.1.0rc1 — local review server"
echo "  Open: http://127.0.0.1:${PORT}"
echo "  Uploads: enabled locally (uploaded code is never executed)"
echo "  AI review: off    Public access: off"
echo "  Press Ctrl+C to stop. Nothing is sent to any server."
echo

exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
