"""Pinned source/config import diagnostic; no weights, images or predictions."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from mmcv import Config
from axiom_vad.native import CONFIG, source_identity

source = Path('/work/VAD')
commit, digest = source_identity(source)
sys.path.insert(0, str(source))
module = importlib.import_module('projects.mmdet3d_plugin')
Path(module.__file__).resolve().relative_to(source.resolve())
cfg = Config.fromfile(str(source / CONFIG))
if cfg.model.type != 'VAD' or cfg.model.pts_bbox_head.ego_his_encoder is not None or cfg.model.pts_bbox_head.ego_lcf_feat_idx is not None or not cfg.model.video_test_mode:
    raise RuntimeError('Unsupported Tiny profile')
print(json.dumps({'status':'source_config_import_passed', 'source_commit':commit,
    'source_files_hash':digest, 'resolved_config_hash':hashlib.sha256(cfg.pretty_text.encode()).hexdigest(),
    'model_type':cfg.model.type, 'learned_model_inference':False}, indent=2))
