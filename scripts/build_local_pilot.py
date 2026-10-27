#!/usr/bin/env python3
"""Assemble an internal frozen pilot directory; never a signed/commercial release.

Use a freshly built frontend/dist. The output excludes databases, credentials,
Git metadata, CARLA/native research scripts, tests and node_modules. Dependencies
are installed into a separate environment, never vendored from a private venv.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("backend/src/ade2e", "sdk/python/src/axiom_sdk",
           "integrations/onnx/src/axiom_onnx", "integrations/coco/src/axiom_coco")
RUNTIME_ROOTS = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "mcap", "lz4", "zstandard", "Pillow",
                 "httpx", "onnx", "onnxruntime", "numpy", "opencv-python-headless", "pycocotools")


def dependencies():
    """Resolve installed default runtime dependency closure, not dev/test extras."""
    pending, found = list(RUNTIME_ROOTS), {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in found:
            continue
        package = metadata.distribution(name)
        found[name] = package.version
        for raw in package.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                actual = metadata.version(requirement.name)
                if requirement.specifier and actual not in requirement.specifier:
                    raise ValueError(f"Installed dependency violates {name}'s declared requirement: {requirement}")
                pending.append(requirement.name)
    return dict(sorted(found.items()))


BOOTSTRAP = '''#!/usr/bin/env python3
"""Launch only the application source frozen beside this script."""
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
paths = [str(root / p) for p in ("backend/src", "sdk/python/src", "integrations/onnx/src", "integrations/coco/src")]
sys.path[:0] = paths
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["AXIOM_APP_FACTORY_ONLY"] = "1"
from ade2e.local_pilot import main
sys.argv += ["--bundle", str(root)]
raise SystemExit(main())
'''

COMMAND = '''#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
python="${AXIOM_PYTHON:-$root/../axiom-pilot-env/bin/python}"
if [[ ! -x "$python" ]]; then
  echo 'Prepare the separate Python environment described in PILOT-README.md, or set AXIOM_PYTHON.' >&2
  exit 1
fi
exec "$python" -B "$root/run-pilot.py" start \\
  --data-dir "${AXIOM_PILOT_DATA_DIR:-$root/../axiom-pilot-data}" \\
  --state-dir "${AXIOM_PILOT_STATE_DIR:-$root/../axiom-pilot-access}" \\
  --port "${AXIOM_PILOT_PORT:-5273}"
'''

README = '''# AXIOM local pilot · internal deployment candidate

Trusted single local operator, macOS/Linux, Python 3.12+. This bundle contains
built UI and fixed application source. Node/npm, Vite and CARLA are not used at
runtime. It is not a signed release, a sandbox or a multi-user/Internet service.
Model files remain trusted code inputs. Licensing and customer acceptance are
not approved by generating this bundle. Keep the existing development setup.

## Prepare dependencies outside the bundle

From this directory (Internet/package-index access is needed unless wheels are
already available; installing a new environment is separate from source checks):

```bash
python3.12 -m venv ../axiom-pilot-env
../axiom-pilot-env/bin/python -m pip install -r requirements-pilot.txt
../axiom-pilot-env/bin/python -B run-pilot.py check --data-dir ../axiom-pilot-data --state-dir ../axiom-pilot-access
bash "Start AXIOM Pilot.command"
```

On macOS the command file can also be opened in Finder after preparing the
environment. AXIOM_PYTHON, AXIOM_PILOT_DATA_DIR, AXIOM_PILOT_STATE_DIR and
AXIOM_PILOT_PORT override these defaults. Do not put the venv/data/access state
inside the frozen application directory. Do not edit bundle files; rebuild.

Open the loopback address printed after BOTH API and worker are ready. Open
Workspace settings and enter the token saved in the printed private token file.
On macOS copy it without displaying it: `pbcopy < ../axiom-pilot-access/api-token`.
The token is never printed or placed in a URL. Do not share tokens or access
directories. The HTML shell is public locally; business APIs require the token.
The settings dialog reports the actual API origin, not a fixed development port.

Ctrl-C stops this launcher's API and drains its local worker. It never stops a
conflicting port's owner or reconnects to CARLA. A forced stop is an error; read
the private launch receipt and reconcile jobs before retrying. Use the regular
workspace_backup command only after all API/worker processes stop. Access tokens
are outside the data backup, which remains unencrypted.

## Preserved data and capability boundaries

New folders start EMPTY, without authored examples or hidden model downloads.
To use existing data, first cold-backup it and restore to a NEW sibling folder.
Schema11–16 are accepted; back up before the additive schema16 migration on start.
Older packages cannot open schema16 data. Active owners, quarantined resources and queued work
requiring excluded adapters block startup; the launcher does not discard jobs.
The reference simulation, supported sensor decoders, ONNX CPU and COCO evaluator
are explicit; entry-point discovery and remote-engine configuration are disabled.
Existing CARLA results can be inspected, but new CARLA execution is unavailable.
Full ONNX/COCO workflow examples in examples/ use authored pixels and are not
customer/road accuracy results. They create real data in the selected workspace.

`bundle.json` records source hashes and exact installed dependency versions.
Checksums detect corruption, not malicious modification or publisher identity.
NOTICE inventories are review inputs, not a blanket redistribution permission.
Source relocation with an existing dependency environment is NOT a clean-machine
installation test. See the project acceptance record for the exact tested scope.
'''


def build(root, destination):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Choose a new output directory with an existing parent")
    if not (root / "frontend/dist/index.html").is_file():
        raise ValueError("Build frontend first: npm --prefix frontend run build")
    if destination.resolve() == root or root.is_relative_to(destination.resolve()):
        raise ValueError("Output must not replace or contain the source root")
    packages = dependencies()
    destination.mkdir(mode=0o700)
    def copy(source, relative):
        if source.is_symlink() or not source.is_file():
            raise ValueError("Only regular files may enter the pilot bundle")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    try:
        for module in MODULES:
            for source in (root / module).rglob("*.py"):
                if "__pycache__" not in source.parts:
                    copy(source, source.relative_to(root))
        for source in (root / "frontend/dist").rglob("*"):
            if source.is_symlink():
                raise ValueError("Built UI contains a symlink")
            if source.is_file():
                copy(source, Path("ui") / source.relative_to(root / "frontend/dist"))
        for name in ("dataset_evaluation_workflow.py", "generate_perception_fixture.py", "stack_replay_workflow.py", "generate_stack_recording.py", "stack_workflow_components.py", "scene_discovery_workflow.py"):
            copy(root / "examples" / name, Path("examples") / name)
        for source in (root / "third_party").rglob("*"):
            if source.is_file():
                copy(source, source.relative_to(root))
        for name in ("product-pilot.md", "local-pilot.md", "workspace-backup.md", "open-source-register.md", "internal-platform.md", "cases.md", "case-conditions.md", "case-retests.md", "case-model-replays.md", "case-stack-replays.md", "collections.md", "automatic-labels.md", "scene-discovery.md", "verification-plans.md", "driving-policies.md"):
            copy(root / "docs" / name, Path("docs") / name)
        (destination / "run-pilot.py").write_text(BOOTSTRAP)
        (destination / "Start AXIOM Pilot.command").write_text(COMMAND)
        (destination / "Start AXIOM Pilot.command").chmod(0o700)
        (destination / "PILOT-README.md").write_text(README)
        (destination / "requirements-pilot.txt").write_text("".join(f"{name}=={version}\n" for name, version in packages.items()))
        files = {p.relative_to(destination).as_posix(): {"size_bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(destination.rglob("*")) if p.is_file()}
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
        manifest = {"schema_version": "axiom-local-pilot/1", "label": "AXIOM local evaluation pilot",
            "created_at": datetime.now(timezone.utc).isoformat(), "files": files, "dependencies": packages,
            "source_base_commit": head, "source_worktree_dirty": dirty, "clean_checkout_release": False,
            "tested_python": sys.version.split()[0], "build_platform": sys.platform,
            "node_required_at_runtime": False, "signed": False, "commercial_distribution_review": "pending"}
        (destination / "bundle.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        return {"status": "built", "path": str(destination), "files": len(files), "dependencies": len(packages),
                "manifest_sha256": hashlib.sha256((destination / "bundle.json").read_bytes()).hexdigest(),
                "clean_checkout_release": False}
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.destination)))
