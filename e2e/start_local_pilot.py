"""Fresh source-isolated packaged server for the pilot browser acceptance test."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
work = Path(os.environ["AXIOM_PILOT_TEST_ROOT"])
work.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(root / "scripts"))
from build_local_pilot import build

bundle = work / "relocated application"
build(root, bundle)
# Launch from outside the checkout with no Node/npm on PATH. The existing Python
# environment supplies dependencies; this is not a clean-machine install test.
os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
os.environ["ADE2E_CARLA_WORKER_URL"] = "http://127.0.0.1:9/must-not-be-used"
os.environ["ADE2E_DATABASE_URL"] = "postgresql://must-not-be-used.invalid/db"
os.chdir(work)
os.execv(sys.executable, [sys.executable, "-B", str(bundle / "run-pilot.py"), "start",
    "--data-dir", str(work / "data"), "--state-dir", str(work / "access"), "--port", "8191"])
