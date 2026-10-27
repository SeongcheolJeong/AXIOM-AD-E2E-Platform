#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
.venv/bin/python -m pytest backend/tests sdk/python/tests integrations/carla/tests integrations/onnx/tests integrations/coco/tests scripts/tests -q
npm --prefix frontend run build
