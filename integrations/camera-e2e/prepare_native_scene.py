"""Create a new isolated source/runtime; never modify or restart original services."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--destination', type=Path, required=True)
args = parser.parse_args()
if args.destination.exists():
    raise SystemExit('Choose a new destination; existing source/runtime are preserved.')
extension = Path(__file__).resolve().parent / 'native_extension'
args.destination.mkdir(parents=True)
shutil.copytree(args.source / 'src', args.destination / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(extension / 'lens_scene_plans.py', args.destination / 'src/camera_platform/lens_scene_plans.py')
shutil.copy2(extension / 'serve.py', args.destination / 'serve.py')
shutil.copy2(args.source / 'LICENSE', args.destination / 'LICENSE')
identity = {'schema_version': 'camera-e2e-native-scene-source-copy/1', 'source_workspace': str(args.source.resolve()),
            'copied_sources': {str(path.relative_to(args.destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in sorted((args.destination / 'src').rglob('*.py'))},
            'native_catalogs_and_upstream': 'original_workspace_read_only_hash_pinned',
            'original_source_and_service_modified': False}
(args.destination / 'source-copy.json').write_text(json.dumps(identity, indent=2) + '\n')
print(json.dumps({'destination': str(args.destination.resolve()), 'source_file_count': len(identity['copied_sources'])}))
