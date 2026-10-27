"""Start one disposable copy of a packaged DB for browser acceptance."""
from pathlib import Path
import argparse
import importlib.util
import os
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('sample')
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('github_demo_builder', ROOT / 'scripts/build_github_demo.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = ROOT / 'e2e/.runtime'
runtime.mkdir(parents=True, exist_ok=True)
parent = Path(tempfile.mkdtemp(prefix='github-demo-', dir=runtime))
directory = parent / 'workspace'
module.load(ROOT / 'examples/github-demo', args.sample, directory)
os.environ.update(ADE2E_DATABASE_URL='', ADE2E_DATA_DIR=str(directory), ADE2E_SEED_EXAMPLES='0',
                  ADE2E_API_TOKEN='', ADE2E_TEAM_MODE='0', AXIOM_APP_FACTORY_ONLY='0')
import uvicorn
uvicorn.run('ade2e.main:app', host='127.0.0.1', port=args.port)
