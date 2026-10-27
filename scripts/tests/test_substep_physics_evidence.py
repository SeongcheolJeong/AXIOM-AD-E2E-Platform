"""Authored substep readback/clock counterexamples, not simulator executions."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_carla_throttle_isolation as analysis
import carla_throttle_isolation_protocol as protocol
import carla_brake_protocol as brake


def evidence_pair(step=.005):
    p = protocol.make_protocol(brake.make_protocol('throttle', version=brake.SETTLED_VERSION),
        'hold_throttle', 225, continuation_substep_s=step)
    before = {'values': {'synchronous_mode': True, 'substepping': True,
        'fixed_delta_seconds': .05, 'max_substep_delta_time': .01,
        'max_substeps': 10, 'no_rendering_mode': False}, 'invalid_fields': {}}
    target = deepcopy(before); target['values']['max_substep_delta_time'] = step
    e = {'requested': p['continuation_physics'], 'status': 'applied_and_read_back',
        'before': before, 'target': deepcopy(target), 'after': deepcopy(target),
        'handoff_frame': 20, 'after_frame': 21, 'api_returned_frame': 20,
        'before_time_s': 1., 'after_time_s': 1.05}
    report = {'status': 'collected', 'continuation_physics': e,
        'requested_settings': target, 'measurement': {'observations': [
            {'world_frame': 20, 'source_time_s': 1.}, {'world_frame': 21, 'source_time_s': 1.05}]}}
    return report, {'protocol': p}


@pytest.mark.parametrize('step', [.01, .005])
def test_both_sham_and_treatment_retain_the_first_interval(step):
    report, intent = evidence_pair(step)
    analysis.verify_continuation_physics(report, intent)


@pytest.mark.parametrize('change', [
    lambda r: r['continuation_physics'].update(after_frame=22),
    lambda r: r['continuation_physics'].update(after_time_s=1.1),
    lambda r: r['continuation_physics'].update(api_returned_frame=19),
    lambda r: r['continuation_physics'].update(api_returned_frame=True),
    lambda r: r['continuation_physics'].update(status='not_attempted'),
    lambda r: r['continuation_physics']['after']['values'].update(no_rendering_mode=True),
    lambda r: r['continuation_physics']['after']['values'].update(max_substeps=9),
    lambda r: r['measurement']['observations'][1].update(world_frame=22),
])
def test_incomplete_or_changed_transition_is_not_verified(change):
    report, intent = evidence_pair(); change(report)
    with pytest.raises(analysis.InvalidEvidence):
        analysis.verify_continuation_physics(report, intent)


def test_original_v1_shape_has_no_added_physics_field():
    p = protocol.make_protocol(brake.make_protocol('throttle', version=brake.SETTLED_VERSION), 'hold_throttle', 225)
    assert p['schema_version'] == protocol.VERSION and 'continuation_physics' not in p
    assert protocol.intent_version(p).endswith('/1') and protocol.report_version(p).endswith('/1')
    descriptor = protocol.sequence_descriptor(p, {'controls': [brake.control(throttle=.1)]})
    assert descriptor['schema_version'].endswith('/1') and 'continuation_physics' not in descriptor
