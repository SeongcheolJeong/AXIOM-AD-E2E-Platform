"""Task-owned Splatfacto runner. Executed only by the optional CUDA environment.

The input is an adapter-authored request, never browser Python/configuration.
Published images are actual model renders; originals retain their dataset bytes.
"""
from __future__ import annotations
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import random
import re
import shutil
import time
import zipfile

import numpy as np
from PIL import Image
from data import SWEEP_FRAME_COUNT, camera_sweep_lateral_m, canonical, changed_pose, sha_file

FIDELITY = 'neural_reconstruction_of_carla'
RECIPE = 'carla-known-pose-lidar-splatfacto/1'


def write_json(path, value):
    Path(path).write_bytes(canonical(value))


def engine_identity():
    import torch
    if importlib.metadata.version('nerfstudio') != '1.1.5' or not torch.cuda.is_available():
        raise RuntimeError('Pinned Nerfstudio 1.1.5 and a working CUDA GPU are required')
    free, total = torch.cuda.mem_get_info()
    return dict(nerfstudio=importlib.metadata.version('nerfstudio'), gsplat=importlib.metadata.version('gsplat'),
        torch=torch.__version__, cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(0),
        gpu_free_bytes=free, gpu_total_bytes=total)


def train(request, run, engine):
    import torch
    from nerfstudio.configs.method_configs import all_methods
    from nerfstudio.scripts.train import train_loop
    dataset = run / 'dataset'
    if sha_file(run / 'dataset.zip') != request['dataset_sha256']:
        raise ValueError('Dataset transfer hash differs')
    dataset.mkdir()
    with zipfile.ZipFile(run / 'dataset.zip') as archive:
        members = archive.infolist()
        if len(members) > 310 or sum(item.file_size for item in members) > 512 * 1024 ** 2:
            raise ValueError('Dataset archive exceeds its bound')
        names = [item.filename for item in members]
        if len(names) != len(set(names)):
            raise ValueError('Dataset contains duplicate paths')
        for item in members:
            if not re.fullmatch(r'(images/\d{6}\.png|transforms\.json|points\.ply|neural-dataset\.json)', item.filename):
                raise ValueError('Unsupported dataset member')
            destination = dataset / item.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))
    evidence = json.loads((dataset / 'neural-dataset.json').read_bytes())
    real = request.get('source_kind') == 'real_road'
    if real:
        if (evidence.get('schema_version') != 'neural-dataset/2'
                or evidence.get('source_id') != request['source_metadata']['source_id']
                or len(evidence['frames']) != request['source_metadata']['source_frame_count']
                or any(evidence.get(key) != request['source_metadata'][key] for key in
                       ('recording_id', 'channel_id', 'recording_sha256', 'source_use_scope'))):
            raise ValueError('Real-road dataset identity differs')
    elif evidence['source_execution_id'] != request['source_execution_id'] or len(evidence['frames']) != 200:
        raise ValueError('Dataset source identity or frame count differs')
    if engine['gpu_free_bytes'] < 1800 * 1024 ** 2:
        raise RuntimeError('Less than 1800 MiB CUDA memory is free; release the configured demo GPU before training')
    random.seed(request['seed']); np.random.seed(request['seed']); torch.manual_seed(request['seed'])
    config = copy.deepcopy(all_methods['splatfacto'])
    config.output_dir = run / 'training'
    config.experiment_name = 'real-road-front' if real else 'carla-front'
    config.timestamp = 'fixed'
    config.machine.seed = request['seed']
    config.max_num_iterations = request['training_steps']
    config.steps_per_save = request['training_steps']
    config.steps_per_eval_batch = 0
    config.steps_per_eval_image = 0
    config.steps_per_eval_all_images = 0
    config.vis = 'tensorboard'
    config.logging.local_writer.enable = False
    manager = config.pipeline.datamanager
    manager.data = dataset
    manager.cache_images = 'cpu'
    manager.cache_images_type = 'uint8'
    parser = manager.dataparser
    parser.data = dataset
    parser.orientation_method = 'none'
    parser.center_method = 'none'
    parser.auto_scale_poses = False
    parser.scale_factor = 1.0
    parser.downscale_factor = 1
    parser.train_split_fraction = 1.0
    parser.load_3D_points = True
    model = config.pipeline.model
    model.camera_optimizer.mode = 'off'
    model.background_color = 'black'
    model.num_downscales = 0
    model.stop_split_at = min(6000, request['training_steps'])
    model.cull_alpha_thresh = .01
    config.save_config()
    train_loop(0, 1, config)
    checkpoints = sorted(config.get_checkpoint_dir().glob('step-*.ckpt'))
    if not checkpoints:
        raise RuntimeError('Native trainer did not produce a checkpoint')
    checkpoint = checkpoints[-1]
    config_path = config.get_base_dir() / 'config.yml'
    shutil.copyfile(checkpoint, run / 'out/neural-checkpoint.ckpt')
    shutil.copyfile(config_path, run / 'out/neural-trainer-config.yml')
    shutil.copyfile(dataset / 'neural-dataset.json', run / 'out/neural-dataset.json')
    summary = dict(schema_version='neural-reconstruction-result/1', fidelity='neural_reconstruction_of_real_road' if real else FIDELITY,
        source_kind='real_road' if real else 'simulation', source_execution_id=request['source_execution_id'],
        source_manifest_sha256=request.get('source_manifest_sha256', evidence.get('source_manifest_sha256')), rig='rig_a', camera='front',
        frame_count=len(evidence['frames']), width=640, height=360, fps=evidence.get('fps', 10), seed=request['seed'],
        checkpoint_sha256=sha_file(checkpoint), engine=engine, recipe=RECIPE,
        training_steps=request['training_steps'], model_ref=request['run_id'],
        environment_sha256=request['environment_sha256'], dataset_sha256=request['dataset_sha256'],
        native_code_sha256=request['native_code_sha256'],
        limitations=['Static reconstruction of synthetic CARLA images; no real-world or sensor validation.',
            'Known poses and native LiDAR initialization; all 200 frames used for demonstration training.',
            'Newly revealed regions may contain holes or blur; no geometry/photometric acceptance claim.'])
    if real:
        summary.update(request['source_metadata'])
        summary.update(recipe='nuscenes-recorded-pose-lidar-splatfacto/1',
            source_camera=evidence['camera'], source_rig=evidence['rig'],
            limitations=['Static reconstruction of recorded real-road images; moving objects may ghost.',
                'All source frames used for demonstration training; no held-out quality or safety claim.',
                'nuScenes data is limited to isolated noncommercial research; external publication is not cleared.',
                'Novelly revealed content may be incomplete; perception outputs are predictions, not validation.'])
    write_json(run / 'model.json', dict(summary=summary, config=str(config_path), checkpoint=str(checkpoint),
        config_sha256=sha_file(config_path), dataset_manifest_sha256=sha_file(dataset / 'neural-dataset.json')))
    write_json(run / 'out/neural-reconstruction-result.json', summary)
    return summary


def render(request, run, engine):
    import torch
    from nerfstudio.cameras.cameras import Cameras, CameraType
    from nerfstudio.utils.eval_utils import eval_setup
    ref = request['model_ref']
    if not re.fullmatch(r'neural_[a-f0-9]{32}', ref):
        raise ValueError('Unsupported native model reference')
    model_root = run.parent / ref
    model = json.loads((model_root / 'model.json').read_bytes())
    if (model['summary']['checkpoint_sha256'] != request['checkpoint_sha256']
            or model['summary']['source_execution_id'] != request['source_execution_id']
            or model['summary']['environment_sha256'] != request['environment_sha256']):
        raise ValueError('Native retained model identity differs from the published reconstruction')
    config_path, checkpoint = Path(model['config']), Path(model['checkpoint'])
    if (model_root.resolve() not in config_path.resolve().parents or model_root.resolve() not in checkpoint.resolve().parents
            or sha_file(checkpoint) != request['checkpoint_sha256'] or sha_file(config_path) != model['config_sha256']):
        raise ValueError('Native checkpoint/configuration changed or left its owned directory')
    dataset = model_root / 'dataset'
    if sha_file(dataset / 'neural-dataset.json') != model['dataset_manifest_sha256']:
        raise ValueError('Retained source camera data changed')
    evidence = json.loads((dataset / 'neural-dataset.json').read_bytes())
    _, pipeline, loaded, _ = eval_setup(config_path, test_mode='inference')
    if sha_file(loaded) != request['checkpoint_sha256']:
        raise ValueError('Inference did not load the published checkpoint')
    real = evidence.get('source_kind') == 'real_road'
    intrinsics = evidence['intrinsics']
    edits = request['parameters']
    sweep = edits['mode'] == 'camera_sweep'
    if edits['mode'] not in ('frame', 'sequence', 'camera_sweep'):
        raise ValueError('Unsupported native rendering mode')
    anchor = evidence['frames'][edits['frame_index']]
    rows = [anchor] * SWEEP_FRAME_COUNT if sweep else evidence['frames'] if edits['mode'] == 'sequence' else [anchor]
    images = run / 'images'; images.mkdir()
    frames = []
    def render_image(pose, path):
        camera = Cameras(camera_to_worlds=torch.tensor(pose[:3], dtype=torch.float32)[None],
            fx=intrinsics['fx'], fy=intrinsics['fy'], cx=intrinsics['cx'], cy=intrinsics['cy'],
            width=640, height=360, camera_type=CameraType.PERSPECTIVE).to(pipeline.device)
        with torch.no_grad():
            rgb = pipeline.model.get_outputs_for_camera(camera)['rgb']
        if rgb.shape != (360, 640, 3) or not torch.isfinite(rgb).all():
            raise RuntimeError('Native renderer returned invalid pixels')
        Image.fromarray((rgb.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)).save(path)
    archive_path = run / 'out/neural-render-frames.zip'
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_STORED) as archive:
        for position, row in enumerate(rows):
            baseline = np.array(row['camera_to_world'])
            lateral = camera_sweep_lateral_m(position, edits['lateral_m']) if sweep else edits['lateral_m']
            novel = changed_pose(baseline, lateral, edits['height_m'], evidence['scale'])
            index = position if sweep else row['frame_index']
            frame = (dict(frame_index=index, time_s=index / 10, source_frame=row['source_frame'],
                source_frame_index=row['frame_index'], source_time_s=row['time_s'],
                applied_lateral_m=lateral, applied_height_m=edits['height_m']) if sweep
                else {key: row[key] for key in ('frame_index', 'time_s', 'source_frame')})
            if real:
                frame.update(source_frame_index=row['frame_index'], source_time_s=row['time_s'],
                    recording_ordinal=row['recording_ordinal'], log_time_ns=row['log_time_ns'],
                    sample_data_token=row['sample_data_token'], original_image_sha256=row['original_image_sha256'],
                    applied_lateral_m=lateral, applied_height_m=edits['height_m'])
            frame.update(baseline_camera_to_world=baseline.tolist(), novel_camera_to_world=novel.tolist())
            for kind in ('original', 'baseline', 'novel'):
                name = f"{kind}/{index:06d}.png"
                path = images / name; path.parent.mkdir(exist_ok=True)
                if kind == 'original':
                    original = dataset / row['file_path']
                    if sha_file(original) != row['png_sha256']:
                        raise ValueError('Original frame changed since reconstruction')
                    shutil.copyfile(original, path)
                elif sweep and kind == 'baseline' and position > 0:
                    shutil.copyfile(images / 'baseline/000000.png', path)
                else:
                    render_image(baseline if kind == 'baseline' else novel, path)
                frame[kind] = dict(name=name, size_bytes=path.stat().st_size, sha256=sha_file(path))
                archive.write(path, name)
            frames.append(frame)
            if len(frames) % 20 == 0:
                print(f'Rendered {len(frames)}/{len(rows)} frames', flush=True)
    summary = dict(schema_version='neural-render-summary/2' if sweep else 'neural-render-summary/1', fidelity=model['summary']['fidelity'], source_kind=model['summary']['source_kind'],
        source_execution_id=request['source_execution_id'],
        source_manifest_sha256=request.get('source_manifest_sha256', evidence.get('source_manifest_sha256')), checkpoint_sha256=request['checkpoint_sha256'],
        width=640, height=360, fps=10 if sweep else evidence.get('fps', 10), frame_count=len(frames), seed=request['seed'],
        engine=engine, recipe=RECIPE, environment_sha256=request['environment_sha256'],
        result_artifact='neural-render-result.json', frames_artifact='neural-render-frames.zip',
        frames_archive_sha256=sha_file(archive_path), **edits)
    if real:
        summary.update(request['source_metadata'])
        summary.update(recipe=model['summary']['recipe'], limitations=model['summary']['limitations'],
            source_camera=evidence['camera'], source_rig=evidence['rig'])
    if sweep:
        summary.update(source_frame_index=anchor['frame_index'], source_time_s=anchor['time_s'])
    write_json(run / 'out/neural-render-result.json', {**summary, 'schema_version': 'neural-render-result/2' if sweep else 'neural-render-result/1',
        'frames_archive_sha256': sha_file(archive_path), 'frames': frames})
    return summary


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('request'); args = parser.parse_args()
    path = Path(args.request).resolve(); run = path.parent
    request = json.loads(path.read_bytes())
    if (request['schema_version'] != 'neural-native-request/1' or request['run_id'] != run.name
            or sha_file(__file__) != request['native_code_sha256']
            or sha_file(Path(__file__).with_name('data.py')) != request['data_code_sha256']):
        raise ValueError('Native request or code identity differs')
    (run / 'out').mkdir(exist_ok=False)
    start = time.monotonic()
    engine = engine_identity()
    summary = train(request, run, engine) if request['kind'] == 'neural_reconstruction' else render(request, run, engine)
    summary['elapsed_seconds'] = round(time.monotonic() - start, 3)
    members = []
    for item in sorted((run / 'out').iterdir()):
        members.append(dict(name=item.name, size_bytes=item.stat().st_size, sha256=sha_file(item),
            media_type='application/json' if item.suffix == '.json' else 'application/zip' if item.suffix == '.zip' else 'application/octet-stream'))
    write_json(run / 'output.json', dict(schema_version='neural-native-output/1', run_id=request['run_id'],
        request_sha256=sha_file(path), summary=summary, artifacts=members))


if __name__ == '__main__':
    main()
