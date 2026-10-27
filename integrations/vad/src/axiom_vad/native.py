"""Native Tiny learned head, independent from upstream GT-reading test evaluator.

No dummy/fallback output. CUDA/MMCV and real checkpoint failures stay failures.
"""
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys
import time

from ade2e.driving_contract import SOURCE_COMMIT, PROFILE, content_hash, validate_result
from .nuscenes_input import bounded_file, file_hash, verify_files

CONFIG = 'projects/configs/VAD/VAD_tiny_stage_2.py'


def source_identity(source):
    source = Path(source).resolve()
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=str(source),text=True).strip()
    if commit != SOURCE_COMMIT or subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=str(source),text=True).strip():
        raise ValueError('Require the pinned unmodified upstream VAD source')
    extras = subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=str(source)).decode().split('\0')
    if any(name.endswith('.py') for name in extras):
        raise ValueError('Untracked Python can alter upstream imports')
    files = subprocess.check_output(['git','ls-files','-z'],cwd=str(source)).decode().split('\0')
    identities = {}
    for name in files:
        if name and (name.endswith('.py') or name in ('LICENSE','requirements.txt')):
            identities[name] = file_hash(bounded_file(source,name))
    return commit,content_hash(identities)


def inspect_environment():
    result = {'schema_version':'vad-environment/1','status':'unavailable','python_version':sys.version.split()[0],'modules':{},'gpu':None,'errors':[]}
    if sys.version_info[:2] != (3,8):
        result['errors'].append('Native profile requires Python 3.8; other versions need separate acceptance')
    for name in ('torch','mmcv','mmdet','mmdet3d','mmseg','nuscenes'):
        try:
            module = importlib.import_module(name)
            try:
                version = metadata.version('nuscenes-devkit' if name == 'nuscenes' else 'mmcv-full' if name == 'mmcv' else name)
            except metadata.PackageNotFoundError:
                version = getattr(module,'__version__','unknown')
            result['modules'][name] = version
        except (ImportError,RuntimeError,OSError) as error:
            result['errors'].append(name+': '+str(error)[:200])
    if 'torch' in result['modules']:
        import torch
        if torch.cuda.is_available():
            properties = torch.cuda.get_device_properties(0)
            free,total = torch.cuda.mem_get_info(0) if hasattr(torch.cuda,'mem_get_info') else (None,properties.total_memory)
            result['gpu'] = {'name':properties.name,'total_bytes':total,'free_bytes':free,'torch_cuda':torch.version.cuda}
        else:
            result['errors'].append('Native CUDA is unavailable; CPU fallback is not supported')
    if 'mmcv' in result['modules']:
        try:
            from mmcv.ops import MultiScaleDeformableAttention
        except (ImportError,RuntimeError,OSError) as error:
            result['errors'].append('MMCV native operators unavailable: '+str(error)[:200])
    versions = {'mmcv':'1.4.0','mmdet':'2.14.0','mmdet3d':'0.17.1','mmseg':'0.14.1','nuscenes':'1.1.9'}
    for name,expected in versions.items():
        if name in result['modules'] and result['modules'][name] != expected:
            result['errors'].append(name+': native profile requires '+expected)
    if 'torch' in result['modules'] and not result['modules']['torch'].split('+')[0] == '1.9.1':
        result['errors'].append('Native profile requires torch 1.9.1; other versions need separate acceptance')
    if not result['errors']:
        result['status'] = 'available_for_native_check'
    return result


def predict(source, root, checkpoint, value, *, seed=42, weights_rights):
    source,checkpoint = Path(source).resolve(),Path(checkpoint).resolve()
    verify_files(root,value)
    commit,source_hash = source_identity(source)
    if checkpoint.is_symlink() or not checkpoint.is_file() or not weights_rights.strip():
        raise ValueError('Select trusted checkpoint bytes and an explicit weights-rights note')
    from ade2e import driving_contract
    adapter_hash = content_hash({p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    adapter_hash = content_hash({'adapter':adapter_hash,'contract':file_hash(Path(driving_contract.__file__))})
    checkpoint_hash = file_hash(checkpoint)
    env = inspect_environment()
    if env['status'] != 'available_for_native_check':
        raise RuntimeError('Native VAD environment unavailable: '+json.dumps(env['errors']))
    # Trust boundary: the pinned source/config and the explicitly selected checkpoint execute code.
    sys.path.insert(0,str(source))
    import torch
    import mmcv
    from mmcv import Config
    from mmcv.runner import load_checkpoint
    from mmdet.apis import set_random_seed
    from mmdet.datasets.pipelines import Compose
    from mmdet3d.models import build_model
    plugin = importlib.import_module('projects.mmdet3d_plugin')
    Path(plugin.__file__).resolve().relative_to(source)
    config_path = source/CONFIG
    cfg = Config.fromfile(str(config_path))
    if cfg.model.pts_bbox_head.ego_his_encoder is not None or cfg.model.pts_bbox_head.ego_lcf_feat_idx is not None or not cfg.model.video_test_mode:
        raise ValueError('Unsupported head/state profile; missing optional ego channels cannot be invented')
    resolved_hash = hashlib.sha256(cfg.pretty_text.encode()).hexdigest()
    cfg.model.pretrained = None
    set_random_seed(seed,deterministic=True)
    torch.cuda.reset_peak_memory_stats()
    model = build_model(cfg.model,train_cfg=None,test_cfg=cfg.get('test_cfg'))
    load_checkpoint(model,str(checkpoint),map_location='cpu',strict=True)
    if file_hash(checkpoint) != checkpoint_hash:
        raise ValueError('Checkpoint changed during loading')
    model.cuda().eval()
    pipeline = Compose([
        {'type':'LoadMultiViewImageFromFiles','to_float32':True},
        {'type':'NormalizeMultiviewImage',**dict(cfg.img_norm_cfg)},
        {'type':'RandomScaleImageMultiViewImage','scales':[0.4]},
        {'type':'PadMultiViewImage','size_divisor':32}])
    prev_bev,prev_pos,prev_angle = None,None,None
    outputs = []
    started = time.monotonic()
    with torch.no_grad():
        for frame in value['frames']:
            import numpy as np
            meta = pipeline({'img_filename':[str(bounded_file(root,c['path'])) for c in frame['cameras']],
                'lidar2img':[np.asarray(c['lidar2img'],dtype=np.float64) for c in frame['cameras']]})
            if any(image.shape != meta['img'][0].shape for image in meta['img']):
                raise ValueError('Camera tensors must share the native padded shape')
            image = torch.from_numpy(np.stack([img.transpose(2,0,1) for img in meta.pop('img')])).unsqueeze(0).cuda()
            bus = np.asarray(frame['can_bus'],dtype=np.float64)
            position,angle = bus[:3].copy(),float(bus[-1])
            if prev_bev is None:
                bus[:3]=0;bus[-1]=0
            else:
                bus[:3]-=prev_pos;bus[-1]-=prev_angle
            meta.update(can_bus=bus,scene_token=value['dataset']['scene_token'])
            features = model.extract_feat(img=image,img_metas=[meta])
            head = model.pts_bbox_head(features,[meta],prev_bev=prev_bev,ego_his_trajs=None,ego_lcf_feat=None)
            predictions = head['ego_fut_preds']
            if tuple(predictions.shape) != (1,3,6,2) or not torch.isfinite(predictions).all():
                raise RuntimeError('Invalid native predicted trajectory dimensions/values')
            paths = predictions[0].cumsum(dim=-2).cpu().tolist()
            outputs.append({'sample_token':frame['sample_token'],'timestamp_us':frame['timestamp_us'],
                'coordinate_frame':'current_lidar_xy_m','step_seconds':0.5,
                'modes':dict(zip(('right','left','straight'),paths))})
            prev_bev,prev_pos,prev_angle = head['bev_embed'],position,angle
    torch.cuda.synchronize()
    # Detect source/file mutation before claiming a sealed result.
    if source_identity(source) != (commit,source_hash) or file_hash(checkpoint) != checkpoint_hash:
        raise ValueError('Native source/checkpoint changed during execution')
    verify_files(root,value)
    result = {'schema_version':'driving-result/1','input':value,'input_hash':content_hash(value),
        'model':{'source_commit':commit,'source_files_hash':source_hash,'adapter_source_hash':adapter_hash,'checkpoint_sha256':checkpoint_hash,
            'config_sha256':file_hash(config_path),'resolved_config_hash':resolved_hash,'adapter_version':'axiom-vad-native/2',
            'profile':PROFILE,'weights_rights':weights_rights},
        'execution':{'status':'completed','engine':'native_torch','device':env['gpu']['name'],
            'library_versions':env['modules'],'seed':seed,'elapsed_ms':(time.monotonic()-started)*1000,
            'peak_cuda_bytes':torch.cuda.max_memory_allocated(),'ground_truth_in_model_input':False},
        'outputs':outputs,'limitations':['Recorded open-loop learned predictions, not closed-loop safety evidence.',
            'All three modes are preserved; no oracle route selection or future GT metric input.',
            'nuScenes data/derived results and checkpoint rights are not commercially cleared.',
            'Tiny optional ego-history/LCF encoders are disabled in the pinned original configuration.',
            'Historical position/yaw deltas follow original VAD temporal BEV initialization.']}
    validate_result(result)
    return result
