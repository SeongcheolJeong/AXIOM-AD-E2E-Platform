"""Isolated native service entrypoint; copied code, original read-only catalogs."""
import argparse
from pathlib import Path
import uvicorn
from camera_platform.api import create_app
from camera_platform.lens_scene_plans import install_routes

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=Path, required=True)
parser.add_argument('--state-dir', type=Path, required=True)
parser.add_argument('--port', type=int, default=8773)
args = parser.parse_args()
app = create_app(args.state_dir, args.workspace, start_worker=True, seed_demo=False)
install_routes(app, app.state.service)
uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
