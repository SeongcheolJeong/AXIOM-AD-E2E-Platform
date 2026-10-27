"""Optional known-pose CARLA → Splatfacto execution over owned SSH transport."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import uuid

from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.neural_demo import execution_pin, resolve_neural_execution, _source, _reconstruction
from ade2e.neural_demo_schemas import NeuralReconstructionInput, NeuralRenderInput
from .data import SWEEP_FRAME_COUNT, build_dataset, canonical, inspect_manifest, sha_file
from .results import validate_downloaded_output
from .real_road import load_source

ADAPTER = 'neural-splatfacto'
VERSION = '1.0.0'
FIDELITY = 'neural_reconstruction_of_carla'
PACKAGE = Path(__file__).parent
SHA = re.compile(r'[a-f0-9]{64}')


def code_hashes():
    return {name: sha_file(PACKAGE / name) for name in ('handler.py', 'data.py', 'native.py', 'results.py', 'real_road.py')}


def native_config():
    configured = os.environ.get('AXIOM_NEURAL_NATIVE_CONFIG')
    if not configured:
        raise ValueError('Configure AXIOM_NEURAL_NATIVE_CONFIG with the isolated GPU launcher')
    path = Path(configured)
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('Neural native configuration is missing or too large')
    value = json.loads(path.read_bytes())
    required = {'schema_version', 'ssh_destination', 'remote_root', 'launcher', 'launcher_sha256',
                'environment_sha256', 'training_steps', 'timeout_seconds'}
    if set(value) != required or value['schema_version'] != 'neural-native-config/1':
        raise ValueError('Unsupported Neural native configuration')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', value['ssh_destination']):
        raise ValueError('Invalid configured SSH destination')
    for key in ('remote_root', 'launcher'):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value[key]) or '..' in value[key].split('/'):
            raise ValueError('Neural transport uses absolute operator-owned paths')
    for key in ('launcher_sha256', 'environment_sha256'):
        if not isinstance(value[key], str) or not SHA.fullmatch(value[key]):
            raise ValueError('Neural runtime identity requires SHA-256')
    if (type(value['training_steps']) is not int or not 1000 <= value['training_steps'] <= 30000
            or type(value['timeout_seconds']) is not int or not 300 <= value['timeout_seconds'] <= 14400):
        raise ValueError('Neural recipe or timeout exceeds the demo bound')
    return value


def item(record, name):
    return next(value for value in record['artifacts'] if value['name'] == name)


class NeuralExperimentHandler:
    def __init__(self, *, database=None):
        self.database = database

    def metadata(self):
        try:
            native_config(); configured = self.database is not None
        except (OSError, ValueError, TypeError, KeyError):
            configured = False
        return ExperimentHandlerMetadata(id=ADAPTER, version=VERSION,
            kinds=['neural_source_import', 'neural_reconstruction', 'neural_render'], status='available' if configured else 'not_configured',
            fidelity=FIDELITY, resource_id='carla', cancellation_supported=False,
            description='Known-pose static Splatfacto reconstruction of CARLA RGB and bounded camera view edits; no physical validation.')

    def prepare(self, spec):
        config = native_config()
        if (self.database is None or spec.adapter_id != ADAPTER or spec.adapter_version not in (None, VERSION)
                or spec.comparison_kind != 'single' or any(value is not None for value in (spec.model, spec.sensor, spec.evaluator))):
            raise ValueError('Neural demo requires its connected single-scene adapter without unrelated model metadata')
        if spec.kind == 'neural_source_import':
            if set(spec.input) != {'source_id'} or not isinstance(spec.input['source_id'], str):
                raise ValueError('Real-road source import requires one catalog source_id')
            manifest, manifest_path, dataset_path = load_source(spec.input['source_id'])
            return dict(schema_version='neural-source-import/1', project_id=spec.project_id,
                kind=spec.kind, parameters=dict(spec.input), seed=spec.seed, code_hashes=code_hashes(),
                manifest_sha256=sha_file(manifest_path), dataset_sha256=sha_file(dataset_path),
                execution_fidelity='recorded_real_road', fidelity='recorded_real_road',
                workload=dict(frame_count=manifest['frame_count'], requires_cuda=False),
                limitations=['nuScenes noncommercial research data; external publication is not cleared.'])
        reconstruction = None
        if spec.kind == 'neural_reconstruction':
            parameters = NeuralReconstructionInput.model_validate(spec.input).model_dump(mode='json')
            source_id = parameters['source_execution_id']
        elif spec.kind == 'neural_render':
            parameters = NeuralRenderInput.model_validate(spec.input).model_dump(mode='json')
            reconstruction = resolve_neural_execution(self.database, spec.project_id,
                parameters['reconstruction_execution_id'], 'neural_reconstruction')
            source_id = reconstruction['summary']['source_execution_id']
        else:
            raise ValueError('Unsupported Neural experiment kind')
        source = resolve_neural_execution(self.database, spec.project_id, source_id)
        _source(source)
        real = source['kind'] == 'neural_source_import'
        manifest = json.loads(item(source, 'neural-real-road-source.json' if real else 'rig_a-capture-manifest.json')['path'].read_bytes())
        if real:
            count = manifest['frame_count']
        else:
            _, records = inspect_manifest(manifest)
            count = len(records)
            if count != 200:
                raise ValueError('CARLA demo requires all 200 source frames')
        if spec.kind == 'neural_render' and parameters['frame_index'] >= count:
            raise ValueError('Selected source frame is outside this recorded scene')
        fidelity = 'neural_reconstruction_of_real_road' if real else FIDELITY
        result = dict(schema_version='neural-experiment/1', project_id=spec.project_id,
            kind=spec.kind, seed=spec.seed, parameters=parameters, source_execution_id=source_id,
            source_pin=execution_pin(source), native_config_sha256=hashlib.sha256(canonical(config)).hexdigest(),
            code_hashes=code_hashes(), fidelity=fidelity, execution_fidelity=fidelity,
            workload=dict(frame_count=count if spec.kind == 'neural_reconstruction' or parameters['mode'] == 'sequence'
                else SWEEP_FRAME_COUNT if parameters['mode'] == 'camera_sweep' else 1,
                width=640, height=360, training_steps=config['training_steps'] if spec.kind == 'neural_reconstruction' else 0,
                resource='carla', requires_cuda=True),
            limitations=['nuScenes noncommercial research source; external publication is not cleared.' if real else 'CARLA 합성 로그의 Neural 재구성', 'Static scene; camera sweep is experimental; newly visible content may be incomplete.'])
        if reconstruction:
            _reconstruction(reconstruction, source)
            result.update(reconstruction_execution_id=reconstruction['execution_id'], reconstruction_pin=execution_pin(reconstruction))
        return result

    @staticmethod
    def _ssh(config, args, **kwargs):
        return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', config['ssh_destination'],
            shlex.join(args)], check=True, **kwargs)

    @staticmethod
    def _copy(config, source, destination, *, download=False):
        remote = f"{config['ssh_destination']}:{source if download else destination}"
        args = [remote, str(destination)] if download else [str(source), remote]
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', *args],
            check=True, timeout=600, capture_output=True)

    def execute(self, snapshot, work_dir, should_cancel):
        if snapshot.get('schema_version') == 'neural-source-import/1':
            if snapshot.get('code_hashes') != code_hashes():
                raise ValueError('Source adapter changed after preview')
            manifest, manifest_path, dataset_path = load_source(snapshot['parameters']['source_id'])
            if sha_file(manifest_path) != snapshot['manifest_sha256'] or sha_file(dataset_path) != snapshot['dataset_sha256']:
                raise ValueError('Prepared source changed after preview')
            if should_cancel():
                raise AdapterCancelled('Source import cancelled')
            work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
            artifacts = []
            for original, name, media in ((manifest_path, 'neural-real-road-source.json', 'application/json'),
                    (dataset_path, 'neural-real-road-dataset.zip', 'application/zip')):
                target = work / name; shutil.copyfile(original, target)
                if sha_file(target) != sha_file(original):
                    raise ValueError('Source copy changed')
                artifacts.append(ExperimentOutputFile(target, name, media))
            return ExperimentExecution(summary=manifest, artifacts=artifacts, fidelity='recorded_real_road')
        config = native_config()
        if (snapshot.get('schema_version') != 'neural-experiment/1' or snapshot.get('code_hashes') != code_hashes()
                or snapshot.get('native_config_sha256') != hashlib.sha256(canonical(config)).hexdigest()):
            raise ValueError('Neural code or runtime changed after preview; create a new preview')
        source = resolve_neural_execution(self.database, snapshot['project_id'], snapshot['source_execution_id'])
        if execution_pin(source) != snapshot['source_pin']:
            raise ValueError('Pinned CARLA source changed before native execution')
        _source(source)
        real = source['kind'] == 'neural_source_import'
        source_artifact = item(source, 'neural-real-road-source.json' if real else 'rig_a-capture-manifest.json')
        source_manifest = json.loads(source_artifact['path'].read_bytes()) if real else None
        work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
        request = dict(schema_version='neural-native-request/1', run_id='neural_' + uuid.uuid4().hex,
            kind=snapshot['kind'], source_execution_id=source['execution_id'], seed=snapshot['seed'],
            native_code_sha256=snapshot['code_hashes']['native.py'], data_code_sha256=snapshot['code_hashes']['data.py'],
            environment_sha256=config['environment_sha256'])
        request.update(source_kind='real_road' if real else 'simulation',
            source_manifest_sha256=source_artifact['sha256'])
        if real:
            request['source_metadata'] = {key: source_manifest[key] for key in (
                'source_id', 'recording_id', 'channel_id', 'recording_sha256', 'scene_name',
                'source_start_time_ns', 'source_end_time_ns', 'source_use_scope', 'license_scope',
                'external_publication_cleared')}
            request['source_metadata'].update(source_frame_count=source_manifest['frame_count'], source_fps=source_manifest['fps'])
            if snapshot['kind'] == 'neural_render':
                request['source_anchor_time_s'] = source_manifest['frames'][snapshot['parameters']['frame_index']]['time_s']
        dataset = None
        if snapshot['kind'] == 'neural_reconstruction':
            if real:
                dataset = item(source, 'neural-real-road-dataset.zip')['path']
            else:
                manifest = item(source, 'rig_a-capture-manifest.json')
                _, dataset = build_dataset(manifest['path'], item(source, 'rig_a-native-capture.zip')['path'],
                    work / 'dataset', source['execution_id'], manifest['sha256'])
            request.update(dataset_sha256=sha_file(dataset), training_steps=config['training_steps'])
        else:
            reconstruction = resolve_neural_execution(self.database, snapshot['project_id'],
                snapshot['reconstruction_execution_id'], 'neural_reconstruction')
            if execution_pin(reconstruction) != snapshot['reconstruction_pin']:
                raise ValueError('Pinned reconstruction changed before native execution')
            _reconstruction(reconstruction, source)
            request.update(reconstruction_execution_id=reconstruction['execution_id'], model_ref=reconstruction['summary']['model_ref'],
                checkpoint_sha256=reconstruction['summary']['checkpoint_sha256'], parameters=snapshot['parameters'])
        if should_cancel():
            raise AdapterCancelled('Neural execution cancelled before dispatch')
        # Check the launcher before any CUDA work; the launcher pins its image and driver libraries.
        check = self._ssh(config, ['sha256sum', config['launcher']], timeout=30, capture_output=True)
        if check.stdout.decode().split()[0] != config['launcher_sha256']:
            raise ValueError('Configured native launcher changed')
        remote = config['remote_root'].rstrip('/') + '/runs/' + request['run_id']
        self._ssh(config, ['mkdir', '-p', config['remote_root'].rstrip('/') + '/runs'], timeout=30, capture_output=True)
        self._ssh(config, ['mkdir', remote], timeout=30, capture_output=True)
        request_path = work / 'request.json'; request_path.write_bytes(canonical(request))
        for source_path, name in ((PACKAGE / 'native.py', 'native.py'), (PACKAGE / 'data.py', 'data.py'), (request_path, 'request.json')):
            self._copy(config, source_path, remote + '/' + name)
        if dataset:
            self._copy(config, dataset, remote + '/dataset.zip')
        journal = dict(schema_version='external-neural-dispatch/1', external_execution_id=request['run_id'],
            request_sha256=sha_file(request_path), status='dispatching', cleanup_status='not_applicable_retained_model')
        journal_path = work / 'external-dispatch.json'; journal_path.write_bytes(canonical(journal))
        log = work / 'neural-native-stdout.log'
        try:
            with log.open('wb') as stream:
                self._ssh(config, [config['launcher'], remote + '/native.py', remote + '/request.json'],
                    timeout=config['timeout_seconds'], stdout=stream, stderr=subprocess.STDOUT)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            journal['status'] = 'outcome_unknown' if isinstance(exc, subprocess.TimeoutExpired) or exc.returncode == 255 else 'failed'
            journal_path.write_bytes(canonical(journal))
            tail = log.read_bytes()[-2500:].decode(errors='replace')
            raise RuntimeError(f'Native Neural execution {journal["status"]}; retained run {request["run_id"]}. {tail}') from exc
        self._copy(config, remote + '/output.json', work / 'output.json', download=True)
        if (work / 'output.json').stat().st_size > 1024 * 1024:
            raise ValueError('Native Neural receipt exceeds its bound')
        output = json.loads((work / 'output.json').read_bytes())
        if (output.get('schema_version') != 'neural-native-output/1' or output.get('run_id') != request['run_id']
                or output.get('request_sha256') != sha_file(request_path)):
            raise ValueError('Native Neural receipt has a different request identity')
        validate_output(output, request, source_artifact['sha256'])
        artifacts = []
        for entry in output['artifacts']:
            path = work / entry['name']
            self._copy(config, remote + '/out/' + entry['name'], path, download=True)
            if path.stat().st_size != entry['size_bytes'] or sha_file(path) != entry['sha256']:
                raise ValueError('Native Neural output transfer hash differs')
            artifacts.append(ExperimentOutputFile(path, entry['name'], entry['media_type']))
        # Cross-check the published checkpoint against the receipt before normal queue publication.
        if snapshot['kind'] == 'neural_reconstruction' and sha_file(work / 'neural-checkpoint.ckpt') != output['summary']['checkpoint_sha256']:
            raise ValueError('Neural checkpoint differs from the reconstruction summary')
        validate_downloaded_output(output, work, source_manifest=source_manifest)
        journal['status'] = 'completed'; journal_path.write_bytes(canonical(journal))
        artifacts.extend([ExperimentOutputFile(log, log.name, 'text/plain'),
            ExperimentOutputFile(journal_path, journal_path.name, 'application/json')])
        return ExperimentExecution(summary=output['summary'], artifacts=artifacts, fidelity=output['summary']['fidelity'])


def validate_output(output, request, source_manifest_sha256):
    summary = output['summary']; entries = output['artifacts']
    real = request.get('source_kind') == 'real_road'
    source_metadata = request.get('source_metadata', {})
    source_count = source_metadata.get('source_frame_count', 200)
    source_fps = source_metadata.get('source_fps', 10)
    sweep = request.get('parameters', {}).get('mode') == 'camera_sweep'
    expected_fps = 10 if sweep else source_fps
    if real and any(summary.get(key) != value for key, value in source_metadata.items()):
        raise ValueError('Real-road recording lineage differs')
    expected = ({'neural-reconstruction-result.json', 'neural-checkpoint.ckpt', 'neural-trainer-config.yml', 'neural-dataset.json'}
        if request['kind'] == 'neural_reconstruction' else {'neural-render-result.json', 'neural-render-frames.zip'})
    if len(entries) != len(expected) or {entry['name'] for entry in entries} != expected:
        raise ValueError('Native Neural artifact set differs')
    for entry in entries:
        if (type(entry['size_bytes']) is not int or not 0 < entry['size_bytes'] <= 2 * 1024 ** 3
                or not SHA.fullmatch(entry['sha256'])):
            raise ValueError('Native Neural artifact is oversized or lacks a hash')
    if (summary.get('fidelity') != ('neural_reconstruction_of_real_road' if real else FIDELITY) or summary.get('source_kind') != ('real_road' if real else 'simulation')
            or summary.get('source_execution_id') != request['source_execution_id']
            or summary.get('source_manifest_sha256') != source_manifest_sha256
            or (summary.get('width'), summary.get('height'), summary.get('fps')) != (640, 360, expected_fps)
            or summary.get('seed') != request['seed'] or summary.get('environment_sha256') != request['environment_sha256']):
        raise ValueError('Native Neural source, resolution or runtime differs')
    if request['kind'] == 'neural_reconstruction':
        if (summary.get('schema_version') != 'neural-reconstruction-result/1' or summary.get('frame_count') != source_count
                or summary.get('model_ref') != request['run_id'] or summary.get('training_steps') != request['training_steps']):
            raise ValueError('Native reconstruction recipe differs')
    else:
        sweep = request['parameters']['mode'] == 'camera_sweep'
        if (summary.get('schema_version') != ('neural-render-summary/2' if sweep else 'neural-render-summary/1')
            or summary.get('reconstruction_execution_id') != request['reconstruction_execution_id']
            or summary.get('checkpoint_sha256') != request['checkpoint_sha256']
            or any(summary.get(key) != value for key, value in request['parameters'].items())
            or summary.get('frame_count') != (SWEEP_FRAME_COUNT if sweep else source_count if request['parameters']['mode'] == 'sequence' else 1)
            or sweep and (summary.get('source_frame_index') != request['parameters']['frame_index']
                or summary.get('source_time_s') != request.get('source_anchor_time_s', request['parameters']['frame_index'] / 10))):
            raise ValueError('Native rendered camera edit or checkpoint differs')
