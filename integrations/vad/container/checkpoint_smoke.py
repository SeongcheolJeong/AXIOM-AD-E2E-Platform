"""Trusted upstream checkpoint compatibility check; no input data or inference."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--confirm-trusted-native-code', action='store_true')
args = parser.parse_args()
if not args.confirm_trusted_native_code:
    parser.error('The pinned source/config and selected checkpoint can execute native Python')
from axiom_vad.native import CONFIG, source_identity
from mmcv import Config
from mmcv.runner import load_checkpoint
from mmdet3d.models import build_model
import torch

source = Path('/work/VAD')
checkpoint = Path('/work/weights/VAD_tiny.pth')
expected = '430e8881f33bdb19f86557db00c2ccc77da22b3bc042ba7f41a9ef4c2b258a16'
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()
if digest(checkpoint) != expected:
    raise RuntimeError('Checkpoint bytes differ from the inspected upstream download')
commit, source_hash = source_identity(source)
sys.path.insert(0, str(source))
module = importlib.import_module('projects.mmdet3d_plugin')
Path(module.__file__).resolve().relative_to(source.resolve())
cfg = Config.fromfile(str(source / CONFIG))
cfg.model.pretrained = None
started = time.monotonic()
model = build_model(cfg.model, train_cfg=None, test_cfg=cfg.get('test_cfg'))
load_checkpoint(model, str(checkpoint), map_location='cpu', strict=True)
if digest(checkpoint) != expected or source_identity(source) != (commit, source_hash):
    raise RuntimeError('Selected checkpoint/source changed during loading')
print(json.dumps({'status':'strict_checkpoint_load_passed','source_commit':commit,
    'source_files_hash':source_hash,'checkpoint_sha256':expected,
    'parameters':sum(p.numel() for p in model.parameters()),
    'state_tensors':len(model.state_dict()),'device':'cpu',
    'elapsed_ms':(time.monotonic()-started)*1000,
    'learned_model_inference':False,'commercial_rights':'unverified'}, indent=2))
