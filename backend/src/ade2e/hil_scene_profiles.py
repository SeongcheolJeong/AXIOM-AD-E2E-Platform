"""Fixed, engine-neutral HIL lead-motion overlays; also staged standalone.

Only the Python standard library is used. Integers stay integers in canonical
JSON so the browser, API and native adapter authenticate exactly the same data.
Authored target-speed schedules describe input time, not measured vehicle motion.
"""
import copy
import hashlib
import json

SCHEMA = 'hil-scene-profile/1'


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _profile(identity, title, description, schedule):
    value = dict(schema_version=SCHEMA, id=identity, revision=1, title=title, description=description,
                 clock='simulation_input', duration_s=18, lead_speed_schedule=schedule)
    return {**value, 'sha256': hashlib.sha256(_canonical(value)).hexdigest()}


_PROFILES = {
    'lead_deceleration': _profile('lead_deceleration', '앞차 감속',
        '입력 시각 4초부터 앞차 목표 속도를 2m/s에서 0.5m/s로 낮춥니다.',
        [dict(at_s=0, target_speed_mps=2), dict(at_s=4, target_speed_mps=.5)]),
    'lead_stop_go': _profile('lead_stop_go', '앞차 정지 후 재출발',
        '입력 시각 4초에 앞차 목표 속도를 0m/s로, 8초에 2m/s로 바꿉니다.',
        [dict(at_s=0, target_speed_mps=2), dict(at_s=4, target_speed_mps=0), dict(at_s=8, target_speed_mps=2)]),
}


def list_scene_profiles():
    return copy.deepcopy(list(_PROFILES.values()))


def resolve_scene_profile(reference):
    """Resolve one exact fixed-catalog reference, never an implicit latest value."""
    if (not isinstance(reference, dict) or set(reference) != {'id', 'revision', 'sha256'}
            or not isinstance(reference['id'], str) or reference['id'] not in _PROFILES
            or type(reference['revision']) is not int or reference['revision'] != 1):
        raise ValueError('Unsupported HIL scene profile reference')
    value = _PROFILES[reference['id']]
    if reference['sha256'] != value['sha256']:
        raise ValueError('HIL scene profile hash differs from the fixed catalog')
    return copy.deepcopy(value)


def validate_scene_profile(value):
    """Authenticate the complete frozen descriptor, including schedule and text."""
    try:
        if not isinstance(value, dict):
            raise ValueError('A frozen HIL scene profile object is required')
        expected = resolve_scene_profile({key: value[key] for key in ('id', 'revision', 'sha256')})
        if _canonical(value) != _canonical(expected):
            raise ValueError('Frozen HIL scene profile differs from the supported catalog record')
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError('Invalid frozen HIL scene profile') from error
    return expected
