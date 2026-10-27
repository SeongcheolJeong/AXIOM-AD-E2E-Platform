"""Optional, operator-configured transport for bounded native stunt experiments."""
from __future__ import annotations

import hashlib
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from fastapi import HTTPException

from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.service import canonical_bytes, digest

VERSION = 'carla-stunt/1.0.0'
FIDELITY = 'assumed_vehicle_rigid_body'


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_native_output(snapshot, trace, camera_manifest, receipt, work_dir, external_id):
    """Recompute the published verdict from pinned observations and applied commands.

    This checks native evidence consistency, not real-vehicle accuracy. Video
    decoding is mandatory: a matching file hash alone does not establish that
    the promised frames can be played.
    """
    import math
    import struct
    from . import stunt_math as mechanics
    from .stunt_native import TORQUE_API_PER_NM
    from ade2e.stunt_lab_schemas import StuntConfig, model_definition

    def require(condition, message):
        if not condition:
            raise ValueError('Native stunt evidence: ' + message)

    def finite(value):
        if isinstance(value, dict):
            for child in value.values(): finite(child)
        elif isinstance(value, list):
            for child in value: finite(child)
        elif isinstance(value, float):
            require(math.isfinite(value), 'nonfinite numeric observation')

    def number(value):
        require(type(value) in (int, float) and math.isfinite(value), 'numeric field is invalid')
        return value

    def vector(value, count=3):
        require(isinstance(value, list) and len(value) == count, 'vector dimension differs')
        return [number(v) for v in value]

    def same(actual, expected, label):
        if type(expected) in (float, int):
            require(math.isclose(number(actual), expected, rel_tol=1e-6, abs_tol=1e-5), label + ' differs')
        elif isinstance(expected, list):
            require(isinstance(actual, list) and len(actual) == len(expected), label + ' dimension differs')
            for a, b in zip(actual, expected): same(a, b, label)
        elif isinstance(expected, dict):
            require(isinstance(actual, dict) and set(actual) == set(expected), label + ' fields differ')
            for key, value in expected.items(): same(actual[key], value, label + '.' + key)
        else:
            require(type(actual) is type(expected) and actual == expected, label + ' differs')

    def rotation(q):
        x, y, z, w = q
        return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]

    # Native Transform::TransformationMatrix is float32. A normalized output
    # quaternion projects its slightly nonorthogonal matrix back to a proper
    # rotation; it must not replace the exact matrix used by the controller.
    # gamma(64), with unit roundoff 2^-24, bounds the degree conversion,
    # quaternion products/normalization and matrix sums for unit rotations.
    # This envelope is used ONLY for orientation representations, never for
    # commanded forces, impulse limits, contact criteria or frame cadence.
    matrix_roundoff = (64*2**-24)/(1-64*2**-24)

    def orientation_consistent(actual, expected, label):
        require(all(abs(a-b) <= matrix_roundoff for ar, er in zip(actual, expected)
                    for a, b in zip(ar, er)), label + ' exceeds float32 orientation roundoff')

    for value in (snapshot, trace, camera_manifest, receipt): finite(value)
    design = snapshot['design']
    config = StuntConfig.model_validate(design['config']).model_dump()
    require(config['preset'] == 'kickflip', 'unsupported preset')
    require(digest({k: v for k, v in design.items() if k != 'sha256'}) == design['sha256'], 'design content hash differs')
    require(design['model_definition'] == model_definition(), 'model geometry differs')
    require(trace.get('schema_version') == 'carla-stunt-trace/1' and trace.get('adapter_version') == VERSION,
            'trace or adapter version differs')
    require(trace.get('controller_version') == mechanics.VERSION, 'controller version differs')
    require(trace.get('execution_fidelity') == FIDELITY and trace.get('physical_validation') == 'not_established',
            'fidelity boundary differs')
    require(trace.get('status') == 'completed' and trace.get('error') is None and
            trace.get('cleanup_status') == 'confirmed' and trace.get('cleanup_errors') == [], 'execution/cleanup is not complete')
    require(trace.get('provenance') == {**snapshot, 'external_execution_id': external_id}, 'frozen provenance differs')
    require(trace.get('design_ref') == {k: design[k] for k in ('id', 'revision', 'sha256')} and
            trace.get('seed') == design['seed'] and trace.get('config') == config, 'design reference, seed or configuration differs')
    probe = snapshot['probe']
    require(probe.get('physics_verified') is True and probe.get('available') is True, 'pinned probe is unverified')
    identity_keys = ('stunt_native_sha256', 'stunt_math_sha256', 'server_version', 'client_version',
                     'server_pid', 'map', 'opendrive_sha256')
    require(set(trace['native_identity']) == set(identity_keys), 'native identity fields differ')
    require(all(trace['native_identity'][key] == probe[key] for key in identity_keys), 'native identity changed after preview')
    require(probe['stunt_math_sha256'] == file_hash(Path(mechanics.__file__)) and
            probe['stunt_native_sha256'] == file_hash(Path(__file__).with_name('stunt_native.py')), 'native source differs from verifier')
    require(re.fullmatch(r'[a-f0-9]{64}', str(probe.get('commissioning_sha256', ''))) is not None and
            trace.get('commissioning_sha256') == probe['commissioning_sha256'], 'commissioning evidence differs')
    require(probe['map'].split('/')[-1] == config['map_name'], 'map differs')
    require(trace.get('coordinate_basis') == 'carla_world_m_with_proper_transform_rotation_matrix', 'coordinate basis differs')
    # The source-pinned runner's commissioned conversion is authoritative;
    # keeping a second dimensional guess here can invalidate real evidence.
    same(trace.get('torque_api_per_nm'), TORQUE_API_PER_NM, 'torque conversion')
    same(trace['vehicle_physics']['mass_kg'], config['mass_kg'], 'vehicle mass')
    require(trace['vehicle_physics']['blueprint'] == config['vehicle_blueprint'], 'vehicle blueprint differs')
    vector(trace['vehicle_physics']['bounding_box_extent_m'])
    dt = config['dt_s']
    actual_dt = struct.unpack('<f', struct.pack('<f', dt))[0]
    require(trace.get('clock_contract') == dict(nominal_dt_s=dt, engine_dt_s=actual_dt,
        normalization='exact_float32_promoted_to_double_for_1ae5356_sync_window',
        impulse_accounting='nominal_dt_conservative_budget'), 'exact engine clock contract differs')
    require(trace['settings'] == dict(synchronous_mode=True, fixed_delta_seconds=actual_dt, substepping=True,
                                     max_substep_delta_time=actual_dt, max_substeps=1), 'exact world clock settings differ')
    frames = trace['frames']
    expected_steps = round(config['duration_s']/dt)
    require(isinstance(frames, list) and len(frames) == expected_steps+1, 'observation count differs')
    require(type(frames[0]['world_frame']) is int and frames[0]['world_frame'] >= 0, 'world frame is not a nonnegative integer')
    initial_frame = frames[0]['world_frame']
    origin = vector(trace['spawn_transform']['position_m'])
    controller = mechanics.KickflipController(config)
    previous_command = dict(force_n=[0., 0., 0.], torque_nm=[0., 0., 0.], thruster_forces_n=[0.]*4,
                            remaining_impulse_ns=config['impulse_budget_ns'], phase='initial', accumulated_roll_rad=0.)
    impulse = 0.
    first_contact = None
    for index, row in enumerate(frames):
        require(type(row['world_frame']) is int and row['world_frame'] == initial_frame+index, 'world frame gap or reorder')
        same(row['time_s'], index*actual_dt, 'observation cadence')
        require(number(row['delta_seconds']) == actual_dt, 'native delta seconds differs from exact engine clock')
        for key in ('position_m', 'rotation_deg', 'velocity_mps', 'angular_velocity_rad_s', 'force_n', 'torque_nm'):
            vector(row[key])
        q = vector(row['quaternion_xyzw'], 4)
        same(mechanics.norm(q), 1., 'quaternion norm')
        raw_matrix = row['rotation_matrix3x3']
        require(isinstance(raw_matrix, list) and len(raw_matrix) == 3, 'native rotation matrix dimension differs')
        matrix = [vector(matrix_row) for matrix_row in raw_matrix]
        require(all(abs(value) <= 1+matrix_roundoff for mr in matrix for value in mr),
                'native rotation matrix entry exceeds unit rotation bounds')
        require(all(value == struct.unpack('<f', struct.pack('<f', value))[0] for mr in matrix for value in mr),
                'native rotation matrix entries are not exact float32 values')
        projected = rotation(q)
        orientation_consistent(matrix, projected, 'matrix/quaternion orientation')
        decoded_q = mechanics.quaternion(matrix)
        require(all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) for a, b in zip(q, decoded_q)),
                'quaternion is not derived from the retained native matrix')
        roll, pitch, yaw = [math.radians(v) for v in row['rotation_deg']]
        sr, cr, sp, cp, sy, cy = math.sin(roll), math.cos(roll), math.sin(pitch), math.cos(pitch), math.sin(yaw), math.cos(yaw)
        orientation_consistent(matrix, [[cp*cy, cy*sp*sr-sy*cr, -cy*sp*cr-sy*sr],
                      [cp*sy, sy*sp*sr+cy*cr, -sy*sp*cr+cy*sr], [sp, -cp*sr, cp*cr]], 'Euler/matrix orientation')
        same(row['height_m'], row['position_m'][2]-origin[2], 'height reference')
        require(type(row['contact_confirmed']) is bool, 'contact flag is not boolean')
        if row['contact_confirmed'] and first_contact is None: first_contact = index
        if first_contact is not None: require(row['contact_confirmed'], 'contact confirmation regressed')
        for key, value in previous_command.items():
            if key not in ('brake', 'rotation_complete'): same(row.get(key), value, 'applied '+key)
        forces = vector(row['thruster_forces_n'], 4)
        limit = config['max_thrust_n']*config['thrust_scale']
        require(sum(abs(f) for f in forces) <= limit+1e-5 and all(abs(f) <= limit/4+1e-5 for f in forces), 'nozzle force exceeds limits')
        if index: impulse += sum(abs(f) for f in forces)*dt
        require(impulse <= config['impulse_budget_ns']+1e-5, 'impulse budget exceeded')
        same(row['remaining_impulse_ns'], config['impulse_budget_ns']-impulse, 'applied impulse accounting')
        if index < expected_steps:
            previous_command = controller.step({**row, 'matrix': matrix})
        else:
            controller.roll = controller.accumulator.observe(q)
        same(row['observed_accumulated_roll_rad'], controller.roll, 'quaternion accumulated roll')
    require(isinstance(trace['events'], list), 'collision events are not a list')
    for event in trace['events']:
        require(event.get('type') == 'native_collision' and type(event.get('world_frame')) is int,
                'unsupported collision event')
        require(initial_frame <= event['world_frame'] <= frames[-1]['world_frame'], 'collision event outside observed frames')
        same(event['time_s'], (event['world_frame']-initial_frame)*actual_dt, 'collision event timestamp')
        vector(event['normal_impulse'])
    contact = trace['summary'].get('contact')
    if contact is None:
        require(first_contact is None, 'contact flag lacks supporting contact evidence')
    else:
        require(first_contact is not None and any(r['height_m'] > .5 for r in frames[:first_contact]), 'contact without prior airborne observation')
        current = frames[first_contact]
        require(0 <= number(contact['time_s']) <= current['time_s']+1e-5, 'contact timestamp differs')
        speed = number(contact['pre_contact_vertical_speed_mps'])
        require(speed >= 0, 'negative contact speed')
        if contact['source'] == 'native_collision_callback':
            matches = [event for event in trace['events'] if event.get('type') == 'native_collision'
                       and event['world_frame'] == contact.get('world_frame')
                       and abs(event['time_s']-contact['time_s']) < 1e-5]
            require(current['height_m'] < .5 and any(mechanics.norm(e['normal_impulse']) > 0 and
                    abs(e['normal_impulse'][2]) >= .5*mechanics.norm(e['normal_impulse']) for e in matches),
                    'landing callback lacks vertical collision evidence')
            boundary = contact['world_frame']-initial_frame
            require(type(boundary) is int and 0 < boundary <= first_contact, 'collision source frame outside contact interval')
            same(contact['time_s'], frames[boundary]['time_s'], 'collision source time')
        elif contact['source'] == 'native_ground_rays_and_zero_thrust_support':
            same(contact['confirmed_at_s'], current['time_s'], 'support confirmation time')
            require(contact['confirmed_at_s']-contact['time_s'] >= .12-1e-6, 'support duration too short')
            impact = contact['impact_boundary']
            boundary = impact['world_frame']-initial_frame
            require(type(boundary) is int and 0 < boundary <= first_contact, 'support impact boundary invalid')
            before, after = frames[boundary-1], frames[boundary]
            require(impact['previous_world_frame'] == before['world_frame'] and before['velocity_mps'][2] < -.2, 'support lacks descending impact boundary')
            same(impact['time_s'], after['time_s'], 'impact boundary time')
            predicted = (after['force_n'][2]/config['mass_kg']-mechanics.GRAVITY)*dt
            observed = after['velocity_mps'][2]-before['velocity_mps'][2]
            same(impact['observed_delta_vz_mps'], observed, 'observed support response')
            same(impact['commanded_freeflight_delta_vz_mps'], predicted, 'expected freeflight response')
            require(observed > predicted+.08 and after['height_m'] < .5, 'no uncommanded ground support response')
            impact_recent = frames[max(0, boundary-max(2, round(.15/dt))):boundary]
            same(impact['pre_contact_vertical_speed_mps'], max(abs(r['velocity_mps'][2]) for r in impact_recent),
                 'support impact speed')
            require(contact['baseline'] == trace['ground_support_baseline'], 'support baseline differs')
            baseline_hits = contact['baseline'].get('hits', [])
            require(len(baseline_hits) == 4 and all('road' in h['semantic_label'].lower() or
                    'ground' in h['semantic_label'].lower() for h in baseline_hits), 'resting baseline lacks four road rays')
            baseline_heights = [vector(h['position_m'])[2] for h in baseline_hits]
            require(max(baseline_heights)-min(baseline_heights) < .15, 'resting baseline is nonplanar')
            same(contact['baseline']['body_ground_clearance_m'], origin[2]-sum(baseline_heights)/4, 'resting ground clearance')
            support_index = round(contact['time_s']/dt)
            same(contact['time_s'], frames[support_index]['time_s'], 'support interval start')
            require(contact['ground_evidence'] == frames[support_index].get('ground_support_evidence'), 'support start evidence differs')
            for row in frames:
                if contact['time_s']-1e-5 <= row['time_s'] <= contact['confirmed_at_s']+1e-5:
                    evidence = row.get('ground_support_evidence', {})
                    require(row['height_m'] < .2 and abs(row['velocity_mps'][2]) < .15 and mechanics.norm(row['force_n']) < .1, 'unsupported ground support interval')
                    require(evidence.get('available') is True and contact['baseline'].get('available') is True, 'native ground rays unavailable')
                    hits = evidence.get('hits', [])
                    require(len(hits) == 4 and all('road' in h['semantic_label'].lower() or 'ground' in h['semantic_label'].lower() for h in hits), 'four road rays required')
                    heights = [vector(h['position_m'])[2] for h in hits]
                    require(max(heights)-min(heights) < .15, 'nonplanar ground ray hits')
                    same(evidence['body_ground_clearance_m'], row['position_m'][2]-sum(heights)/4, 'ground clearance')
                    require(abs(evidence['body_ground_clearance_m']-contact['baseline']['body_ground_clearance_m']) < .12, 'ground clearance differs from rest')
        else:
            raise ValueError('Native stunt evidence: unsupported contact inference')
        recent = frames[max(0, boundary-max(2, round(.15/dt))):boundary]
        expected_impact = max(abs(r['velocity_mps'][2]) for r in recent)
        # Native callback delivery can occur after a previously detected support
        # boundary. Preserve the earlier, more conservative source speed too.
        airborne_seen = False
        for index in range(1, first_contact):
            before, after = frames[index-1], frames[index]
            airborne_seen = airborne_seen or before['height_m'] > .5
            if (airborne_seen and after['height_m'] < .5 and
                    before['velocity_mps'][2] < -.2 and after['velocity_mps'][2]-before['velocity_mps'][2] >
                    (after['force_n'][2]/config['mass_kg']-mechanics.GRAVITY)*dt+.08):
                impact_recent = frames[max(0, index-max(2, round(.15/dt))):index]
                expected_impact = max(expected_impact, max(abs(r['velocity_mps'][2]) for r in impact_recent))
                break
        if 'additional_collision_world_frame' in contact:
            extra = contact['additional_collision_world_frame']
            require(type(extra) is int and initial_frame < extra <= frames[-1]['world_frame'], 'additional collision frame invalid')
            require(any(e['world_frame'] == extra and mechanics.norm(e['normal_impulse']) > 0 and
                        abs(e['normal_impulse'][2]) >= .5*mechanics.norm(e['normal_impulse']) for e in trace['events']),
                    'additional collision lacks vertical impact evidence')
            extra_index = extra-initial_frame
            extra_recent = frames[max(0, extra_index-max(2, round(.15/dt))):extra_index]
            expected_impact = max(expected_impact, max(abs(r['velocity_mps'][2]) for r in extra_recent))
        same(speed, expected_impact, 'conservative pre-contact speed')
    expected = mechanics.landing_summary(frames, contact=contact, rotation_complete=controller.rotation_complete)
    expected.update(max_height_m=max(row['height_m'] for row in frames),
                    accumulated_roll_deg=math.degrees(controller.roll), impulse_used_ns=impulse)
    same(trace['summary'], expected, 'recomputed landing summary')
    require(receipt.get('schema_version') == 'carla-stunt-native-receipt/1' and
            receipt.get('external_execution_id') == external_id and receipt.get('status') == 'completed' and
            receipt.get('cleanup_status') == 'confirmed' and receipt.get('error') is None, 'receipt identity/status differs')
    same(receipt['summary'], expected, 'receipt summary')
    require(camera_manifest.get('schema_version') == 'stunt-camera-manifest/1' and
            camera_manifest.get('physical_validation') == 'not_established' and
            camera_manifest.get('fps') == config['camera_fps'], 'camera manifest contract differs')
    require(set(camera_manifest['counts']) == {'external', 'onboard'} and
            all(row.get('camera') in ('external', 'onboard') for row in camera_manifest['frames']), 'unknown camera stream')
    try:
        import cv2
    except ImportError as exc:
        raise ValueError('Native stunt evidence: OpenCV is required to verify playable videos') from exc
    interval = round(1/(config['camera_fps']*dt))
    for camera in ('external', 'onboard'):
        rows = [row for row in camera_manifest['frames'] if row['camera'] == camera]
        count = camera_manifest['counts'][camera]
        require(type(count) is int and len(rows) == count and
                math.ceil(config['duration_s']*config['camera_fps']-2) <= count <= expected_steps//interval+1,
                'camera frame denominator incomplete')
        for index, row in enumerate(rows):
            require(type(row['index']) is int and row['index'] == index and type(row['world_frame']) is int, 'camera index differs')
            require(initial_frame < row['world_frame'] <= frames[-1]['world_frame'], 'camera frame outside physics interval')
            if index: require(row['world_frame']-rows[index-1]['world_frame'] == interval, 'camera cadence gap')
            same(row['time_s'], (row['world_frame']-initial_frame)*actual_dt, 'camera/physics timestamp')
            require(re.fullmatch(r'[a-f0-9]{64}', str(row['sha256'])) is not None, 'source image hash invalid')
        require(rows[0]['world_frame']-initial_frame <= interval+1, 'camera start is missing')
        require(frames[-1]['world_frame']-rows[-1]['world_frame'] <= 2*interval, 'camera tail is missing')
        capture = cv2.VideoCapture(str(Path(work_dir)/(camera+'.mp4')))
        try:
            require(capture.isOpened(), 'video cannot be decoded')
            same(capture.get(cv2.CAP_PROP_FPS), config['camera_fps'], 'video frame rate')
            decoded = 0
            while True:
                okay, image = capture.read()
                if not okay: break
                require(image.shape == (540, 960, 3), 'video dimensions differ')
                decoded += 1
                require(decoded <= count, 'video has unexpected extra frames')
            require(decoded == count, 'video decoded count differs from source manifest')
        finally:
            capture.release()
    return expected


class StuntHandler:
    def __init__(self, *, database=None):
        self.db = database
        self.target = os.environ.get('AXIOM_CARLA_STUNT_SSH', '')
        self.python = os.environ.get('AXIOM_CARLA_STUNT_PYTHON', '')
        self.script = os.environ.get('AXIOM_CARLA_STUNT_SCRIPT', '')
        self.directory = os.environ.get('AXIOM_CARLA_STUNT_DIR', '')
        self.owner = os.environ.get('AXIOM_CARLA_STUNT_OWNER', '')
        self.spool = os.environ.get('AXIOM_CARLA_STUNT_SPOOL', '')
        self.port = int(os.environ.get('AXIOM_CARLA_STUNT_PORT', '2400'))

    def metadata(self):
        configured = all((self.db, self.target, self.python, self.script, self.directory, self.owner, self.spool))
        return ExperimentHandlerMetadata(id='carla-stunt', version=VERSION, kinds=['object_sim'],
            status='available' if configured else 'not_configured', fidelity=FIDELITY,
            resource_id='carla-city-' + str(self.port), cancellation_supported=True,
            description='Limited thrusters on an assumed rigid vehicle; native CARLA, no real vehicle validation.')

    def config(self):
        if self.metadata().status != 'available':
            raise ValueError('Dedicated stunt transport is not configured')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', self.target):
            raise ValueError('Invalid operator SSH target')
        if self.port == 2000 or not 1024 <= self.port <= 65535:
            raise ValueError('A dedicated CARLA endpoint is required')
        for path in (self.python, self.script, self.directory, self.owner, self.spool):
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in Path(path).parts:
                raise ValueError('Operator paths must be absolute paths without shell metacharacters')

    def command(self, arguments):
        self.config()
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', self.target,
            shlex.join([self.python, self.script, '--port', str(self.port), '--owner', self.owner,
                        '--spool', self.spool, *arguments])]

    def transport_hash(self):
        return digest([self.target, self.python, self.script, self.directory, self.owner, self.spool, self.port])

    def native_probe(self):
        result = subprocess.run(self.command(['--probe']), capture_output=True, timeout=35)
        if result.returncode:
            raise ValueError('Native stunt probe failed: ' + result.stderr.decode(errors='replace')[-1200:])
        if len(result.stdout) > 1024 * 1024:
            raise ValueError('Native probe exceeds size limit')
        return json.loads(result.stdout)

    def readiness(self):
        if self.metadata().status != 'available':
            return {'available': False, 'physics_verified': False, 'reason': 'not_configured'}
        if find_spec('cv2') is None:
            return {'available': False, 'physics_verified': False, 'reason': 'video_decoder_not_installed'}
        try:
            probe = self.native_probe()
            return {'available': probe.get('available', False),
                    'physics_verified': probe.get('physics_verified', False), 'probe': probe,
                    'ramp_available': False, 'reason': probe.get('reason', 'commissioning_required')}
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            return {'available': False, 'physics_verified': False, 'reason': str(error)}

    def prepare(self, spec):
        from ade2e.stunt_lab import resolve_design
        from ade2e.stunt_lab_schemas import StuntConfig
        self.config()
        if find_spec('cv2') is None:
            raise ValueError('Install axiom-carla-adapter[stunt] for native video verification')
        if spec.kind != 'object_sim' or set(spec.input) != {'design_ref'}:
            raise ValueError('Stunts require an immutable design_ref')
        with self.db.read_session() as session:
            design = resolve_design(session, spec.project_id, spec.input['design_ref'])
        config = StuntConfig.model_validate(design['config'])
        if spec.seed != design['seed']:
            raise ValueError('Execution seed differs from saved design')
        if config.preset != 'kickflip':
            raise ValueError('Ramp requires an installed, verified collision asset; no native Ramp is available')
        probe = self.native_probe()
        if probe.get('available') is not True or probe.get('physics_verified') is not True:
            raise ValueError('Native stunt physics commissioning/readiness is not verified')
        if probe.get('map', '').split('/')[-1] != config.map_name:
            raise ValueError('Selected map is not the dedicated server current map')
        return {'schema_version': 'stunt-execution/1', 'design': design, 'probe': probe,
                'transport_sha256': self.transport_hash(), 'execution_fidelity': FIDELITY,
                'physical_validation': 'not_established'}

    def execute(self, snapshot, work_dir, should_cancel):
        self.config()
        if snapshot['transport_sha256'] != self.transport_hash():
            raise ValueError('Operator transport changed after preview')
        if should_cancel():
            raise AdapterCancelled('Cancelled before native stunt dispatch')
        work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
        external_id = 'stunt_' + uuid.uuid4().hex
        remote_dir = self.directory.rstrip('/') + '/' + external_id
        payload = {**snapshot, 'external_execution_id': external_id}
        (work / 'stunt-input.json').write_bytes(canonical_bytes(payload))
        journal = {'schema_version': 'stunt-dispatch/1', 'external_execution_id': external_id,
                   'remote_directory': remote_dir, 'status': 'dispatching', 'cleanup_status': 'unknown'}
        def record():
            (work / 'external-dispatch.json').write_bytes(canonical_bytes(journal))
        record()
        cancelled = False
        with (work / 'stunt-input.json').open('rb') as source, (work / 'stunt-native-stdout.log').open('wb') as stdout, (work / 'stunt-native-stderr.log').open('wb') as stderr:
            process = subprocess.Popen(self.command(['--output', remote_dir]), stdin=source, stdout=stdout, stderr=stderr)
            deadline = time.monotonic() + 600
            while process.poll() is None:
                if should_cancel() and not cancelled:
                    self._cancel(remote_dir); cancelled = True
                if time.monotonic() > deadline:
                    self._cancel(remote_dir)
                    try: process.wait(timeout=20)
                    except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
                    raise RuntimeError('Stunt transport timed out; native cleanup must be reconciled before reuse')
                time.sleep(.1)
        if process.returncode:
            journal['status'] = 'native_error'; record()
            raise RuntimeError('Native stunt failed: ' + (work / 'stunt-native-stderr.log').read_text(errors='replace')[-2000:])
        receipt = json.loads((work / 'stunt-native-stdout.log').read_bytes())
        if receipt.get('cleanup_status') != 'confirmed':
            raise ValueError('Native stunt cleanup is unconfirmed')
        journal.update(status=receipt.get('status'), cleanup_status='confirmed', error=receipt.get('error')); record()
        (work / 'stunt-native-diagnostics.json').write_bytes(canonical_bytes(receipt))
        if receipt.get('status') == 'cancelled' or cancelled:
            raise AdapterCancelled('Native stunt cancelled and owned actors cleaned up')
        if receipt.get('status') != 'completed' or receipt.get('external_execution_id') != external_id:
            raise HTTPException(502, 'Native stunt did not complete: ' + str(receipt.get('error') or 'receipt identity mismatch'))
        entries = receipt.get('artifacts', [])
        media_types = {'stunt-trace.json': 'application/json', 'camera-manifest.json': 'application/json',
                       'external.mp4': 'video/mp4', 'onboard.mp4': 'video/mp4'}
        if len(entries) != len(media_types) or {e.get('name') for e in entries} != set(media_types):
            raise ValueError('Native artifacts must be the exact trace, camera manifest and two videos')
        names = set(); outputs = []; total = 0
        for entry in entries:
            name = entry['name']; size = entry['size_bytes']
            if entry.get('media_type') != media_types.get(name):
                raise ValueError('Native artifact media type differs from the contract')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name) or name in names:
                raise ValueError('Unsafe or duplicate native artifact path')
            names.add(name)
            if type(size) is not int or not 0 < size <= 512 * 1024**2:
                raise ValueError('Native artifact size is outside budget')
            total += size
            if total > 1024**3: raise ValueError('Native artifacts exceed total budget')
            local = work / name
            result = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', self.target + ':' + remote_dir + '/' + name, str(local)], capture_output=True, timeout=120)
            if result.returncode or local.stat().st_size != size or file_hash(local) != entry['sha256']:
                raise ValueError('Native artifact transfer integrity failed')
            outputs.append(ExperimentOutputFile(local, name, entry['media_type']))
        if not {'stunt-trace.json', 'camera-manifest.json', 'external.mp4', 'onboard.mp4'}.issubset(names):
            raise ValueError('Native trace and both videos are required')
        trace = json.loads((work / 'stunt-trace.json').read_bytes())
        try:
            verified_summary = validate_native_output(snapshot, trace,
                json.loads((work / 'camera-manifest.json').read_bytes()), receipt, work, external_id)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, 'Native evidence rejected: ' + str(exc)) from exc
        for name in ('stunt-input.json', 'external-dispatch.json', 'stunt-native-stdout.log'):
            outputs.append(ExperimentOutputFile(work / name, name, 'application/json'))
        if should_cancel(): raise AdapterCancelled('Cancelled after native cleanup; result publication stopped')
        summary = {**verified_summary, 'schema_version': 'stunt-result/1', 'trace_artifact': 'stunt-trace.json',
                   'trace_sha256': file_hash(work / 'stunt-trace.json'), 'external_execution_id': external_id,
                   'design_ref': {k: snapshot['design'][k] for k in ('id', 'revision', 'sha256')},
                   'physical_validation': 'not_established', 'cleanup_status': 'confirmed'}
        return ExperimentExecution(summary=summary, artifacts=outputs, fidelity=FIDELITY)

    def _cancel(self, remote_dir):
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', self.target,
            shlex.join(['touch', remote_dir + '/cancel.request'])], capture_output=True, timeout=15)
        if result.returncode: raise RuntimeError('Native cancellation delivery unconfirmed')


def create_stunt_handler(*, database=None):
    return StuntHandler(database=database)
