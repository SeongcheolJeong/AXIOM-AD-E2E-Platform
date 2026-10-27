"""Export a bounded source snapshot without touching the development Git index.

No upload occurs here. Historical operational data and Git history stay local.
The output must be a new directory; symlinks and oversized files fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {'backend', 'frontend', 'sdk', 'integrations', 'scripts', 'examples', 'e2e', 'assets', 'third_party'}
ROOT_FILES = {'AGENTS.md', '.env.example', 'requirements.lock'}
EXCLUDED = {
    'integrations/hil_gateway/scripts/deploy_example.py',
    'integrations/hil_gateway/scripts/launch_local.py',
    'integrations/hil_gateway/scripts/reload_local.py',
    'integrations/vecu_network/scripts/native_network_acceptance.py',
}
SKIP_PARTS = {'.git', '.runtime', '.data', '.cache', '.venv', 'node_modules', '__pycache__', '.pytest_cache', 'dist', 'build', 'test-results', 'playwright-report'}
PRIVATE_PATH = re.compile(r'/(?:Users|home)/seongcheoljeong(?=/|\b)')
PRIVATE_IP = re.compile(r'192\.168\.219\.193\b')
TEXT_EXTENSIONS = {'.md', '.py', '.json', '.ts', '.tsx', '.sh', '.yml', '.yaml', '.txt', '.toml'}
GITIGNORE = '''.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.ruff_cache/
.DS_Store
node_modules/
dist/
.runtime/
.data/
/data/
.env
.env.*
!.env.example
/artifacts/
test-results/
playwright-report/
*.log
*.tsbuildinfo
*.db-wal
*.db-shm
output/
outputs/
.chart-data-*/
'''


def selected(name: str) -> bool:
    path = Path(name)
    if any(part in SKIP_PARTS or part.endswith('.egg-info') for part in path.parts):
        return False
    if name in EXCLUDED or name.endswith(('.tsbuildinfo', '.db-wal', '.db-shm', '.pyc', '.log')):
        return False
    if path.parts[0] in SOURCE_DIRS:
        return True
    if name in ROOT_FILES or name == '.github/workflows/portable.yml':
        return True
    return path.parent == Path('docs') and path.suffix == '.md'


def export(destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Destination must not exist; use a new snapshot path.')
    names = set(subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
    ).decode().strip('\0').split('\0'))
    # Packaged sample artifacts intentionally bypass the development artifact ignore.
    demo_root = ROOT / 'examples/github-demo'
    if not demo_root.is_dir():
        raise ValueError('Build examples/github-demo before exporting.')
    names.update(str(p.relative_to(ROOT)) for p in demo_root.rglob('*') if p.is_file())
    candidates = sorted(name for name in names if name and selected(name))
    for name in candidates:
        source = ROOT / name
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise ValueError(f'Symlink or escaping path is not publishable: {name}')
        if source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError(f'Review oversized file before publication: {name}')
    destination.mkdir(parents=True)
    transforms = []
    for name in candidates:
        source = ROOT / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if source.suffix in TEXT_EXTENSIONS:
            original = content
            value = content.decode('utf-8')
            value = PRIVATE_PATH.sub('/home/axiom-user', value)
            value = PRIVATE_IP.sub('carla-host.example.invalid', value)
            content = value.encode('utf-8')
            if content != original:
                transforms.append({'path': name, 'operation': 'replace private workstation examples',
                                   'original_sha256': hashlib.sha256(original).hexdigest()})
        target.write_bytes(content)
        shutil.copymode(source, target)
    (destination / 'README.md').write_bytes((ROOT / 'docs/github-readme.md').read_bytes())
    (destination / '.gitignore').write_text(GITIGNORE)
    records = [{'path': str(p.relative_to(destination)), 'bytes': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted(destination.rglob('*')) if p.is_file()]
    manifest = {
        'format': 'axiom-source-publication/1',
        'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_kind': 'working-tree snapshot including current uncommitted source; not a clean HEAD export',
        'included': 'core/optional adapter source, tests, authored assets, notices, portable example DBs and top-level documentation',
        'excluded': ['original Git history', 'operational DBs and .env', 'engine binaries and model weights',
                     'third-party datasets', 'historical reports/validation media', *sorted(EXCLUDED)],
        'text_transforms': transforms,
        'files': records,
        'total_bytes': sum(item['bytes'] for item in records),
    }
    (destination / 'PUBLICATION-MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = export(args.output)
    print(json.dumps({'files': len(receipt['files']), 'bytes': receipt['total_bytes'], 'transforms': len(receipt['text_transforms'])}))
