#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
task_python="${AXIOM_PYTHON:-}"
if [[ -z "$task_python" ]]; then
  for task_candidate in python3.12 python3.13 python3.14 python3; do
    if command -v "$task_candidate" >/dev/null 2>&1 && "$task_candidate" -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
      task_python="$(command -v "$task_candidate")"
      break
    fi
  done
fi
if [[ -z "$task_python" ]]; then
  echo 'Python 3.12+ required for the complete development environment. Set AXIOM_PYTHON to a supported Python executable.' >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo 'Node.js 22+ and npm are required.' >&2
  exit 1
fi
"$task_python" -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e 'backend[test]' -e sdk/python -e integrations/carla -e integrations/onnx -e integrations/coco
npm --prefix frontend ci
mkdir -p .runtime
printf '%s\n' "$(dirname "$(command -v node)")" "$(dirname "$(command -v npm)")" > .runtime/node-path
echo 'Setup complete. Start with: .venv/bin/python scripts/dev.py'
