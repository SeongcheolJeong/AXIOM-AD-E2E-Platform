"""Versioned private-model validation, not a normal worker dispatch.

Model identity is separate from scenario/controller identity. Cases and behavior
checks are predeclared. Road grades cannot be authored by tilting an ego pose.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from native_stop_observer_gdb import BINARY_SHA256, BUILD_ID
from sticky_damping_contract import profile

CASES = {
    'close-lead-1p5': {'ego_speed_mps': 1.5, 'target_speed_mps': 0.0, 'initial_gap_m': 8.0},
    'close-lead-2p5': {'ego_speed_mps': 2.5, 'target_speed_mps': 0.0, 'initial_gap_m': 8.0},
    'cruise-2p0': {'ego_speed_mps': 2.0, 'target_speed_mps': 2.0, 'initial_gap_m': 20.0},
}
EXECUTION_FILES = (
    'native_stop_observer_gdb.py', 'native_physics_clock_gdb.py',
    'native_sticky_damping_gdb.py', 'sticky_damping_contract.py',
    'launch_native_stop_observer.py', 'run_native_stop_observer_trial.py',
    'carla_vehicle_model_validation.py',
)
CRITERIA = {'collision_count': {'operator': 'eq', 'threshold': 0.0},
            'min_ttc_s': {'operator': 'gte', 'threshold': 1.5},
            'max_deceleration_mps2': {'operator': 'lte', 'threshold': 6.0},
            'max_jerk_mps3': {'operator': 'lte', 'threshold': 10.0}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Expected a bounded ordinary manifest file')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate manifest key')
            value[key] = item
        return value
    def invalid(token):
        raise ValueError('Nonfinite manifest token')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    return value, raw


def model_manifest(reference, source_hashes, damping):
    if set(source_hashes) != set(EXECUTION_FILES):
        raise ValueError('Incomplete model implementation identity')
    for value in source_hashes.values():
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid implementation hash')
    controller = reference['request']['context']['stack_snapshot']['definition']['controller']
    physics = reference['physics_profile']
    if (controller['id'], controller['version']) != ('terminal-brake-control', '1'):
        raise ValueError('This revision requires the historical terminal controller')
    if physics != {'schema_version': 'carla-world-physics-candidate/2',
                   'synchronous_mode': True, 'fixed_delta_seconds': .05,
                   'substepping': True, 'max_substep_delta_time': .001, 'max_substeps': 50}:
        raise ValueError('The measured private 1ms profile must be preserved')
    selected = profile(damping)
    return {'schema_version': 'carla-private-vehicle-model/1',
            'model_id': 'model3-sticky-original' if damping == 1000. else 'model3-sticky-soft',
            'revision': 1, 'deployment': 'private_gdb_only', 'production_rollout': False,
            'binary_sha256': BINARY_SHA256, 'build_id': BUILD_ID,
            'adapter_version': '0.8.0', 'source_manifest_sha256': reference['source_manifest_sha256'],
            'execution_source_hashes': deepcopy(source_hashes), 'physics_profile': deepcopy(physics),
            'native_profile': selected, 'controller_sha256': digest(canonical(controller)),
            'scope': 'all_vehicle_forward_sticky_rows_in_owned_private_engine',
            'road_grade_support': 'not_implemented', 'real_world_calibration': False,
            'support_warning': 'Client max_substeps range warning retained; installed-build experiment only'}


def case_definition(case_id):
    if case_id not in CASES:
        raise ValueError('Unsupported validation case; slope is not an authored road in this runner')
    return {'case_id': case_id, 'schema_version': 'carla-model-validation-case/1',
            'scenario_overrides': deepcopy(CASES[case_id]), 'criteria': deepcopy(CRITERIA),
            'behavior_contract': 'continuous_cruise/1' if case_id.startswith('cruise') else 'planned_stop_and_hold/1',
            'controller_policy_sensor_changes': False,
            'scope': 'Initial-speed holdout on the original map; unchanged cruise-speed policy'}


def make_selection(reference, reference_raw, source_hashes, case_id, damping):
    model = model_manifest(reference, source_hashes, damping)
    value = {'schema_version': 'carla-model-validation-selection/1',
             'reference_intent_sha256': digest(reference_raw),
             'model': model, 'model_sha256': digest(canonical(model)),
             'case': case_definition(case_id)}
    return {**value, 'selection_sha256': digest(canonical(value))}


def validate_selection(value, reference, reference_raw, source_hashes):
    try:
        expected = make_selection(reference, reference_raw, source_hashes,
                                  value['case']['case_id'], value['model']['native_profile']['forward_damping_per_s'])
        if canonical(expected) != canonical(value):
            raise ValueError('Selection does not match its declared model, case or criteria')
    except (KeyError, TypeError) as error:
        raise ValueError('Malformed model selection') from error
    return deepcopy(expected)


def validate_selection_files(root):
    root = Path(root)
    value, raw = read_json(root / 'model-validation.json')
    if canonical(value) != raw:
        raise ValueError('Model selection is not canonical')
    reference, reference_raw = read_json(root / 'historical-intent.json')
    source_hashes = {}
    for name in EXECUTION_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or unsafe model implementation')
        source_hashes[name] = digest(path.read_bytes())
    selected = validate_selection(value, reference, reference_raw, source_hashes)
    native_profile, _ = read_json(root / 'sticky-profile.json')
    if native_profile != selected['model']['native_profile']:
        raise ValueError('Selected model differs from actual native profile')
    _, manifest_raw = read_json(root / 'bundle/candidate-source-manifest.json')
    if digest(manifest_raw) != reference['source_manifest_sha256']:
        raise ValueError('Selected model base-source manifest differs')
    return selected


def derive_scenario(reference_scenario, case_id):
    case = case_definition(case_id)
    value = deepcopy(reference_scenario)
    if value['map_name'] != 'Town10HD_Opt' or value['duration_s'] != 4.0 or value['seed'] != 42:
        raise ValueError('Original map, duration and seed must be preserved')
    value.update(id='scn_model_validation_' + case_id, revision=1,
                 name='Model validation / ' + case_id,
                 description='Predeclared initial-speed or cruise holdout; original sensors and control policy unchanged.',
                 created_at='2026-09-27T00:00:00+09:00', updated_at='2026-09-27T00:00:00+09:00')
    value['parameters'].update(case['scenario_overrides'])
    return value


def evaluate_behavior(case, rows, policy):
    """Additional checks; do not alter any original simulator metric/verdict."""
    if len(rows) != 81:
        raise ValueError('Behavior evaluation requires all81 observations')
    modes = [row['mode'] for row in rows[:-1]]
    if case['behavior_contract'] == 'continuous_cruise/1':
        checks = {'all_80_commands_remain_pilot_cruise': all(m == 'pilot_cruise' for m in modes),
                  'final_speed_within_original_tracking_limit': abs(rows[-1]['speed_mps'] - policy['cruise_speed_mps']) <= policy['max_speed_tracking_error_mps']}
    elif case['behavior_contract'] == 'planned_stop_and_hold/1':
        hold = next((i for i, mode in enumerate(modes) if mode == 'hold'), None)
        checks = {'no_emergency_or_unknown_stop': not any(m in ('emergency_stop', 'unknown_stop') for m in modes),
                  'hold_observed': hold is not None,
                  'final_speed_within_original_standstill_limit': rows[-1]['speed_mps'] <= policy['standstill_speed_mps'],
                  'all_samples_since_hold_within_standstill_limit': hold is not None and all(r['speed_mps'] <= policy['standstill_speed_mps'] for r in rows[hold:])}
    else:
        raise ValueError('Unknown behavior contract')
    return {'contract': case['behavior_contract'], 'checks': checks,
            'verdict': 'passed' if all(checks.values()) else 'failed',
            'original_metric_verdict_unchanged': True}
