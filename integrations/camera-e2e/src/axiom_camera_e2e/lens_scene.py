"""Actual CameraE2E central lens x saved-sensor diagnostic, never road truth."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time

from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from .retained import RetainedISPHandler, canonical, _artifact_descriptor, _references, MAX_JSON, MAX_NATIVE

SLOTS = ('L1S1', 'L1S2', 'L2S1', 'L2S2')
SPEC_SCHEMA = 'camera-e2e-lens-scene-spec/1'
FIDELITY = 'native_geometric_lens_db_estimate_sensor_central_point_grid_diagnostic'


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _connector_sources():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(__file__).with_name('retained.py'))}


def _download_route(artifact, diagnostic_identity=None):
    identity, _, _, _ = _artifact_descriptor(artifact)
    standard = f'/api/v1/artifacts/{identity}'
    supplied = artifact.get('url', standard)
    if supplied == standard:
        return standard
    if diagnostic_identity and supplied == f'/api/v1/lens-sensor-quality-studies/{diagnostic_identity}/diagnostic-artifacts/{identity}':
        return supplied
    raise ValueError('Native artifact uses an unexpected or unbound download route')


def _plan_checked(value, kind):
    if not isinstance(value, dict) or value.get('schema') != f'camera_lens_sensor_{kind}_plan_v1':
        raise ValueError('Unsupported external planned lens/sensor contract')
    frozen = value.get('frozen_request')
    if not isinstance(frozen, dict) or _hash(frozen) != value.get('plan_hash'):
        raise ValueError('Native planned request SHA-256 differs from its frozen definition')
    contract = value.get('output_contract')
    if (not isinstance(contract, dict) or contract.get('scope') != 'central_slanted_edge_model_diagnostic_only'
            or contract.get('comparison_map_wavelength_nm') != 550.0
            or contract.get('coordinate_model') != 'strict_raytrace_radial_geometry_inverse_piecewise_linear'
            or contract.get('coordinate_convention') != 'row_positive_down_col_positive_right_degrees'
            or contract.get('source_wavelength_nm') != [450.0, 550.0, 650.0]
            or contract.get('physical_validation') != 'not_established'
            or contract.get('product_ranking_allowed') is not False
            or contract.get('pixel_integrated_effective_area_matched') is not False
            or contract.get('perception_label_correspondence') != 'not_established'):
        raise ValueError('Native lens/sensor plan exceeded the accepted model point-center scope')
    return frozen


def _accepted_job(job, plan, kind):
    frozen = _plan_checked(plan, kind)
    identity = job.get('id')
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identity):
        raise ValueError('Invalid native child execution identity')
    if job.get('accepted_plan_hash') != plan['plan_hash'] or {key: job.get(key) for key in frozen} != frozen:
        raise ValueError('Native execution lost its exact accepted input/source/environment plan')
    return identity


def _quality_bindings_checked(entries, source_jobs, exports, plans):
    if not isinstance(entries, list) or len(entries) != 4 or {entry.get('slot') for entry in entries if isinstance(entry, dict)} != set(SLOTS):
        raise ValueError('Derived quality plan must retain exactly four unique crossed matrix slots')
    for entry in entries:
        slot = entry['slot']
        if entry['source_job_id'] != source_jobs.get(slot):
            raise ValueError('Derived quality plan selected a different RAW child')
        if entry.get('source_capture_result_sha256') != _hash(exports[slot]['result']):
            raise ValueError('Derived quality plan changed an exact native capture result hash')
        original = plans[slot]['frozen_request']['source_identities']
        binding = entry.get('source_binding') or {}
        if binding.get('lens_identity') != original['lens'] or binding.get('saved_sensor_revision') != original['sensor']:
            raise ValueError('Derived quality plan changed a frozen lens or saved sensor identity')
        required = set(_references(entry))
        available = {_artifact_descriptor(a) for a in exports[slot]['artifacts']}
        if not required <= available:
            raise ValueError('Derived quality plan lost original native RAW/result bytes')


class LensSceneHandler(RetainedISPHandler):
    def __init__(self, *, database=None):
        self.url = os.environ.get('AXIOM_CAMERA_SCENE_URL', '').rstrip('/')
        self.token = os.environ.get('AXIOM_CAMERA_SCENE_TOKEN', '')

    def metadata(self):
        return ExperimentHandlerMetadata(id='camera-e2e-lens-sensor-scene', version='1', kinds=['sensor_capture'],
            status='available' if self.url else 'not_configured', fidelity=FIDELITY,
            resource_id='camera-e2e-native-scene-cpu', cancellation_supported=False,
            description='Two Lens DB revisions x two saved db_estimate sensors: actual native central slanted-edge RAW, fixed ISP and 550 nm point-grid diagnostics. Physical camera quality, road labels and product ranking are unestablished.')

    def sources(self):
        native = self._request('/api/v1/lens-sensor-raw/template?scope=central_bounded_dense_common_scene')
        quality = self._request('/api/v1/lens-sensor-quality-studies/template')
        return {'schema_version': 'camera-e2e-lens-scene-catalog/1',
                'lenses': [row for row in native['lens_catalog']['revisions'] if row.get('preview_status') == 'ready'],
                'sensors': [row for row in native['sensor_catalog'] if row.get('model') == 'db_estimate_v1' and row.get('status') == 'simulated' and row.get('run_id')],
                'fixed_isp_profile': quality['specification']['profile'],
                'scene_template': {'scene_kind': 'slanted_edge', 'sensor_size': [32, 32], 'scene_samples_row_col': [256, 256],
                    'scene_horizontal_fov_deg': 0.12, 'luminance_cd_m2': 1000.0, 'exposure_s': 0.004, 'noise_flag': 0},
                'source_scope': 'default_project_only', 'physical_validation': 'not_established',
                'resource_contract': native['resource_contract'], 'limitation': native['limitation']}

    def prepare(self, spec):
        if spec.project_id != 'default':
            raise ValueError('CameraE2E native source catalogs are explicitly bound to the default project only')
        if (spec.kind != 'sensor_capture' or spec.comparison_kind != 'same_scene_sensor_ab'
                or any(value is not None for value in (spec.model, spec.sensor, spec.evaluator))):
            raise ValueError('Lens/sensor diagnostic requires same_scene_sensor_ab without an unconnected perception model or evaluator')
        data = spec.input
        if set(data) != {'schema_version', 'lenses', 'sensors', 'scene', 'profile', 'acknowledge_simulation_limits'} or data['schema_version'] != SPEC_SCHEMA or data['acknowledge_simulation_limits'] is not True:
            raise ValueError('Use the complete v1 lens/sensor scene specification and acknowledge simulation limits')
        lenses, sensors, scene = data['lenses'], data['sensors'], data['scene']
        if not isinstance(lenses, dict) or set(lenses) != {'L1', 'L2'} or len(set(lenses.values())) != 2:
            raise ValueError('Choose two distinct immutable lens revisions')
        if not isinstance(sensors, dict) or set(sensors) != {'S1', 'S2'}:
            raise ValueError('Choose two explicit saved sensor revisions')
        if any(not isinstance(row, dict) or set(row) != {'identifier', 'run_id'} for row in sensors.values()) or sensors['S1']['run_id'] == sensors['S2']['run_id']:
            raise ValueError('Choose two distinct saved sensor run IDs; aliases are not independent sensors')
        expected_scene = {'scene_kind', 'sensor_size', 'scene_samples_row_col', 'scene_horizontal_fov_deg', 'luminance_cd_m2', 'exposure_s', 'noise_flag'}
        if not isinstance(scene, dict) or set(scene) != expected_scene or scene.get('scene_kind') != 'slanted_edge':
            raise ValueError('This adapter supports only the explicit dense common-angle slanted edge scene')
        catalog = self.sources()
        if data['profile'] != catalog['fixed_isp_profile']:
            raise ValueError('This version executes the pinned native default ISP v2 profile only')
        plans = {}
        for slot in SLOTS:
            native_spec = {'schema': 'camerae2e_lens_sensor_raw_preview_study_v5', 'name': f'AXIOM central diagnostic {slot}',
                'lens_revision_id': lenses[slot[:2]], 'sensor_revision': sensors[slot[2:]], **scene,
                'seed': spec.seed, 'accept_approximate_lens_radiometry': True}
            plan = self._request('/api/v1/lens-sensor-raw/plan', {'specification': native_spec})
            frozen = _plan_checked(plan, 'raw')
            if frozen['specification'] != native_spec or frozen.get('job_kind') != 'lens_sensor_raw':
                raise ValueError('Native RAW plan changed its requested scene, lens, sensor or seed')
            plans[slot] = plan
        environments = [_hash(plan['frozen_request']['environment']) for plan in plans.values()]
        if len(set(environments)) != 1:
            raise ValueError('The four RAW plans must share one exact native executable environment')
        for l in ('L1', 'L2'):
            if plans[l+'S1']['frozen_request']['source_identities']['lens'] != plans[l+'S2']['frozen_request']['source_identities']['lens']:
                raise ValueError('Lens identity changed while freezing the crossed matrix')
        for s in ('S1', 'S2'):
            if plans['L1'+s]['frozen_request']['source_identities']['sensor'] != plans['L2'+s]['frozen_request']['source_identities']['sensor']:
                raise ValueError('Saved sensor identity changed while freezing the crossed matrix')
        return {'schema_version': 'camera-e2e-lens-scene-input/1', 'specification': data,
            'raw_plans': plans, 'root_plan_hash': _hash({'raw_plans': plans, 'fixed_isp_profile': data['profile']}),
            'fixed_isp_profile': data['profile'], 'native_environment_sha256': environments[0],
            'external_origin_hash': hashlib.sha256(self.url.encode()).hexdigest(), 'seed': spec.seed,
            'workload': {'native_raw_jobs': 4, 'native_quality_jobs': 1, 'execution_order': 'serial',
                'native_total_artifact_bytes_bound': MAX_NATIVE, 'native_file_count_bound': 128},
            'connector_source_sha256': _connector_sources(),
            'fidelity': FIDELITY, 'evaluation_status': 'central_model_diagnostic_only',
            'limitations': ['Central authored slanted edge, not a road scene or reviewed labels.',
                'Lens radiometry assumes unity; geometric PSF omits diffraction and validated pixel integration.',
                'Saved db_estimate sensor; no measured sensor accuracy or product ranking.',
                '550nm point-center map is not broadband PSF footprint or perception-label correspondence.',
                'Common angular diagnostic may remain unevaluated; native cycles/pixel are not paired across different grids.']}

    def execute(self, snapshot, work_dir, should_cancel):
        if snapshot.get('connector_source_sha256') != _connector_sources():
            raise ValueError('Optional connector executable changed after preview')
        if hashlib.sha256(self.url.encode()).hexdigest() != snapshot['external_origin_hash']:
            raise ValueError('Native CameraE2E service changed after preview')
        if _hash({'raw_plans': snapshot['raw_plans'], 'fixed_isp_profile': snapshot['fixed_isp_profile']}) != snapshot['root_plan_hash']:
            raise ValueError('Frozen lens/sensor root plan digest differs')
        if should_cancel():
            raise AdapterCancelled('Lens/sensor matrix cancelled before native dispatch')
        # Check ALL four plans before ANY dispatch, not lazily after earlier jobs.
        for slot in SLOTS:
            planned = snapshot['raw_plans'][slot]
            fresh = self._request('/api/v1/lens-sensor-raw/plan', {'specification': planned['frozen_request']['specification']})
            _plan_checked(fresh, 'raw')
            if fresh != planned:
                raise ValueError(f'{slot} native source/environment changed after preview; no matrix dispatched')
        work_dir = Path(work_dir); work_dir.mkdir(parents=True, exist_ok=True)
        journal_path = work_dir / 'camera-scene-external-dispatch.json'
        journal = {'schema_version': 'camera-e2e-lens-scene-dispatch/1', 'root_plan_hash': snapshot['root_plan_hash'],
                   'native_children': {}, 'status': 'dispatching', 'cleanup_status': 'unknown'}
        def record():
            journal_path.write_bytes(canonical(journal))
        record()
        outputs = []; exports = {}; total = 0; transferred = {}; receipts = []
        def retain_artifacts(items, prefix, diagnostic_identity=None):
            nonlocal total
            for artifact in items:
                identity, name, sha, size = _artifact_descriptor(artifact)
                if identity in transferred:
                    if transferred[identity]['sha256'] != sha or transferred[identity]['size_bytes'] != size:
                        raise ValueError('Native artifact aliases disagree about bytes')
                    transferred[identity]['aliases'].append(prefix+'-'+name)
                    continue
                if len(transferred) >= 128 or total+size > MAX_NATIVE:
                    raise ValueError('Native matrix closure exceeds the accepted file/byte budget')
                target = work_dir / (prefix+'-'+name)
                observed = self._request(_download_route(artifact, diagnostic_identity), file=target, limit=size)
                if observed != {'sha256': sha, 'size_bytes': size}:
                    raise ValueError('Native artifact differs from the exact sealed descriptor')
                total += size
                receipt = {'external_id': identity, 'local_name': target.name, 'aliases': [prefix+'-'+name], **observed}
                transferred[identity] = receipt; receipts.append(receipt)
                outputs.append(ExperimentOutputFile(target, target.name, artifact['media_type']))
        def run_child(slot, kind, plan):
            base = '/api/v1/lens-sensor-raw' if kind == 'raw' else '/api/v1/lens-sensor-quality-studies'
            journal['native_children'][slot] = {'plan_hash': plan['plan_hash'], 'status': 'dispatching'}; record()
            job = self._request(base+'/planned', {'specification': plan['frozen_request']['specification'], 'plan_hash': plan['plan_hash']})
            # A successful POST may have dispatched even if its contract is
            # subsequently rejected. Preserve the known ID before validation.
            returned_id = job.get('id') if isinstance(job, dict) else None
            journal['native_children'][slot].update(status='response_received_unverified', response_sha256=_hash(job))
            if isinstance(returned_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', returned_id):
                journal['native_children'][slot]['id'] = returned_id
            record()
            identity = _accepted_job(job, plan, kind)
            journal['native_children'][slot].update(id=identity, status=job['status']); record()
            deadline = time.monotonic()+600
            while job['status'] in {'queued', 'running', 'cancel_requested'}:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{slot} native timeout; external execution outcome requires inspection')
                time.sleep(0.5)
                job = self._request(base+'/'+identity)
                _accepted_job(job, plan, kind)
            journal['native_children'][slot].update(status=job['status'], error=job.get('error')); record()
            if job['status'] != 'succeeded':
                failed = work_dir / (slot+'-native-diagnostics.json'); failed.write_bytes(canonical(job))
                raise RuntimeError(f'{slot} native computation {job["status"]}; retained child ID and native error require inspection')
            export = self._request(base+'/'+identity+'/export')
            _accepted_job(export, plan, kind)
            if export.get('status') != 'succeeded':
                raise ValueError('Native export does not describe a completed child')
            items = export.get('artifacts')
            available = {_artifact_descriptor(a) for a in items} if isinstance(items, list) else set()
            if kind == 'quality':
                available |= {_artifact_descriptor(a) for parent in exports.values() for a in parent['artifacts']}
            if not isinstance(items, list) or not set(_references(export['result'])) <= available:
                raise ValueError('Native child export omitted result artifact closure')
            path = work_dir / (slot+'-native-export.json'); path.write_bytes(canonical(export))
            outputs.append(ExperimentOutputFile(path, path.name, 'application/json'))
            retain_artifacts(items, slot)
            exports[slot] = export
            return identity
        source_jobs = {slot: run_child(slot, 'raw', snapshot['raw_plans'][slot]) for slot in SLOTS}
        quality_spec = {'schema': 'camerae2e_lens_sensor_quality_study_v2', 'name': 'AXIOM central lens x sensor fixed ISP diagnostic',
            'source_jobs': source_jobs, 'profile': snapshot['fixed_isp_profile'], 'acknowledge_simulation_limits': True}
        quality_plan = self._request('/api/v1/lens-sensor-quality-studies/plan', {'specification': quality_spec})
        frozen_quality = _plan_checked(quality_plan, 'quality')
        if frozen_quality['specification'] != quality_spec or _hash(frozen_quality['environment']) != snapshot['native_environment_sha256']:
            raise ValueError('Derived quality plan changed the fixed ISP or native environment')
        _quality_bindings_checked(frozen_quality['source_entries'], source_jobs, exports, snapshot['raw_plans'])
        quality_path = work_dir / 'derived-quality-plan.json'; quality_path.write_bytes(canonical({'root_plan_hash': snapshot['root_plan_hash'], 'native_source_jobs': source_jobs, 'plan': quality_plan}))
        outputs.append(ExperimentOutputFile(quality_path, quality_path.name, 'application/json'))
        journal['derived_quality_plan_hash'] = quality_plan['plan_hash']; record()
        quality_id = run_child('quality', 'quality', quality_plan)
        diagnostic = self._request('/api/v1/lens-sensor-quality-studies/'+quality_id+'/diagnostic-export')
        _accepted_job(diagnostic['job'], quality_plan, 'quality')
        native_diag = diagnostic['diagnostic']
        if native_diag.get('job_id') != quality_id or native_diag.get('accepted_plan_hash') != quality_plan['plan_hash'] or native_diag.get('contract') != quality_plan['output_contract']:
            raise ValueError('Native point correspondence differs from the accepted diagnostic scope')
        descriptors = {_artifact_descriptor(a) for a in diagnostic['artifacts']}
        descriptors |= {_artifact_descriptor(a) for parent in exports.values() for a in parent['artifacts']}
        if not set(_references(native_diag)) <= descriptors:
            raise ValueError('Native diagnostic export omitted coordinate, mask or image evidence')
        retain_artifacts(diagnostic['artifacts'], 'diagnostic', quality_id)
        export_path = work_dir / 'native-correspondence-export.json'; export_path.write_bytes(canonical(diagnostic))
        outputs.append(ExperimentOutputFile(export_path, export_path.name, 'application/json'))
        report = exports['quality']['result']['summary']
        if report['comparison'].get('product_ranking_allowed') is not False or report['verification'].get('physical_validation') != 'not_established':
            raise ValueError('Native matrix exceeded software diagnostic evidence scope')
        if set(native_diag.get('native_coordinates', {})) != set(SLOTS):
            raise ValueError('Native correspondence lost a crossed matrix slot')
        common = report['comparison']['common_grid_object_angle_edge_mtf50']
        if native_diag.get('common_angular_diagnostic') != common:
            raise ValueError('Native correspondence changed its sealed angular diagnostic')
        for slot in SLOTS:
            if native_diag['native_coordinates'][slot]['contract'] != report['cases'][slot]['conditions']['native_output_angular_grid']['distortion_aware_point_center']:
                raise ValueError('Native point coordinates lost the executed source mapping')
        stages = {(image.get('slot'), image.get('stage')) for image in native_diag['previews']}
        required_stages = {(slot, 'native_fixed_isp') for slot in SLOTS}
        if common.get('available') is True:
            required_stages |= {(slot, 'common_550nm_point_grid') for slot in SLOTS}
        if stages != required_stages or len(native_diag['previews']) != len(required_stages):
            raise ValueError('Native diagnostic images changed their slot/stage coverage')
        image_previews = [{**preview, 'local_name': transferred[preview['artifact']['id']]['local_name']} for preview in native_diag['previews']]
        diagnostic_json = next((item for item in diagnostic['artifacts'] if item['name'] == 'native-correspondence-diagnostic.json'), None)
        if diagnostic_json is None:
            raise ValueError('Native diagnostic export lost its exact coordinate JSON')
        # The full native closure remains downloadable. The core manifest is a
        # bounded summary; do not repeat thousands of coordinates in three places.
        compact_report = {key: value for key, value in report.items() if key not in {'cases', 'source_entries'}}
        compact_report['cases'] = {slot: {key: value for key, value in case.items() if key not in {'conditions', 'source_binding'}}
                                  for slot, case in report['cases'].items()}
        compact_report['comparison'] = {key: value for key, value in report['comparison'].items()
                                        if key != 'native_output_angular_grid_by_slot'}
        compact_report['native_output_angular_grid_sha256_by_slot'] = {
            slot: _hash(grid) for slot, grid in report['comparison'].get('native_output_angular_grid_by_slot', {}).items()}
        compact_coordinates = {slot: {key: value for key, value in item.items() if key != 'contract'}
                               for slot, item in native_diag['native_coordinates'].items()}
        compact_diagnostic = {key: value for key, value in native_diag.items() if key != 'native_coordinates'}
        compact_diagnostic['native_coordinates'] = compact_coordinates
        journal.update(status='completed', cleanup_status='confirmed_no_device_or_actor_ownership', native_artifact_bytes=total); record()
        outputs.append(ExperimentOutputFile(journal_path, journal_path.name, 'application/json'))
        return ExperimentExecution(summary={'schema_version': 'camera-e2e-lens-scene-result/1', 'execution_status': 'completed',
            'verification_verdict': 'Central model diagnostic only; no physical or road-label validation',
            'root_plan_hash': snapshot['root_plan_hash'], 'derived_quality_plan_hash': quality_plan['plan_hash'],
            'native_children': journal['native_children'], 'seed': snapshot['seed'],
            'native_environment_sha256': snapshot['native_environment_sha256'], 'native_artifact_bytes': total,
            'connector_source_sha256': snapshot['connector_source_sha256'],
            'native_artifacts': receipts, 'image_previews': image_previews,
            'correspondence': compact_diagnostic, 'quality_diagnostic': compact_report,
            'correspondence_artifact_name': transferred[diagnostic_json['id']]['local_name'],
            'physical_validation': 'not_established', 'product_ranking_allowed': False,
            'perception_label_correspondence': 'not_established', 'limitations': snapshot['limitations']},
            artifacts=outputs, fidelity=FIDELITY)


def create_lens_scene_handler(*, database=None):
    return LensSceneHandler(database=database)
