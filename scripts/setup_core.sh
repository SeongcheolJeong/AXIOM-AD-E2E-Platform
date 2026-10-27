#!/usr/bin/env bash
# Minimal local workspace: no simulator, model weights, or native toolchain.
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
task_python="${AXIOM_PYTHON:-python3.12}"
"$task_python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 for this tested profile"'
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if(major<22 || (major===22 && minor<12)) throw Error("Node.js 22.12+ required")'
"$task_python" -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e 'backend[test]' -e sdk/python
npm --prefix frontend ci
mkdir -p .runtime
printf '%s\n' "$(dirname "$(command -v node)")" "$(dirname "$(command -v npm)")" > .runtime/node-path
echo 'Core workspace installed. See examples/github-demo/README.md to load a sample DB.'
