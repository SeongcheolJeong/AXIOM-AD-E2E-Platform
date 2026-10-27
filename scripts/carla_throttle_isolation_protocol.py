"""Immutable input-isolation experiment; no engine I/O or brake admission.

Replay a verified failed protocol3 preparation, then continue its PI or hold
the last requested throttle. Source anchoring and direct pair comparison are
different checks; neither demonstrates equality of hidden simulator state.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re
import struct
import uuid

import carla_brake_protocol as brake
from carla_brake_protocol import canonical, sha, require, exact_keys, finite, DT

VERSION = 'carla-throttle-isolation-protocol/1'
SUBSTEP_VERSION = 'carla-throttle-isolation-protocol/2'
INTENT_VERSION = 'carla-throttle-isolation-intent/1'
REPORT_VERSION = 'carla-throttle-isolation-diagnostic/1'
REFERENCE_VERSION = 'carla-throttle-isolation-reference/1'
STEPS = 40
BRANCHES = ('continue_pi', 'hold_throttle')
BASE_SOURCES = ('carla_brake_protocol.py', 'diagnose_carla_brake_response.py', 'diagnose_carla_self_hit.py')
SOURCE_FILES = (*BASE_SOURCES, 'carla_throttle_isolation_protocol.py', 'diagnose_carla_throttle_isolation.py')
CAPTURE_FILES = ('source-intent.json', 'report.json', 'events.jsonl', 'reservation-release.json')
MODULES = ('axiom_carla.startup', 'axiom_carla.vehicle_response', 'axiom_carla.native_journal',
           'axiom_carla.spool', 'axiom_carla.version')
CONDITIONS = ('expected_world', 'expected_store_id', 'engine_build', 'adapter_version',
              'spawn_transform', 'requested_weather', 'expected_physics')


def hashes(value, names, label):
    exact_keys(value, names, label)
    require(all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) for x in value.values()),
            label + ' contains an invalid digest')


def validate_control(value):
    exact_keys(value, brake.control(), 'Isolation control')
    require(0. <= finite(value['throttle'], 'Throttle') <= .35, 'Throttle outside bounds')
    require(canonical({k: v for k, v in value.items() if k != 'throttle'}) ==
            canonical({k: v for k, v in brake.control().items() if k != 'throttle'}),
            'Isolation requires zero brake/steer and automatic forward controls')
    return deepcopy(value)


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def controls_equal(a, b):
    validate_control(a); validate_control(b)
    return all(f32(a[k]) == f32(b[k]) if k in ('throttle', 'brake', 'steer') else a[k] == b[k] for k in a)


def make_protocol(reference_protocol, branch, prefix_steps, *, continuation_substep_s=None):
    value = {'schema_version': VERSION, 'initialization': 'throttle',
        'branch': branch, 'policy': deepcopy(reference_protocol['policy']),
        'controller_protocol': deepcopy(reference_protocol), 'prefix_steps': prefix_steps,
        'continuation_steps': STEPS}
    if continuation_substep_s is not None:
        require(type(continuation_substep_s) in (int, float) and continuation_substep_s in (.01, .005),
                'The bounded substep experiment only permits 0.01s or 0.005s x 10')
        value['schema_version'] = SUBSTEP_VERSION
        value['continuation_physics'] = {'mode': 'apply_settings_first_interval',
            'max_substep_delta_time_s': continuation_substep_s,
            'max_substeps': 10, 'expected_advanced_frames': 1}
    return validate_protocol(value)


def intent_version(protocol):
    return INTENT_VERSION if protocol['schema_version'] == VERSION else 'carla-throttle-isolation-intent/2'


def report_version(protocol):
    return REPORT_VERSION if protocol['schema_version'] == VERSION else 'carla-throttle-isolation-diagnostic/2'


def validate_protocol(value):
    require(type(value) is dict and value.get('schema_version') in (VERSION, SUBSTEP_VERSION),
            'Unsupported isolation protocol')
    extra = ('continuation_physics',) if value['schema_version'] == SUBSTEP_VERSION else ()
    exact_keys(value, ('schema_version', 'initialization', 'branch', 'policy', 'controller_protocol',
                       'prefix_steps', 'continuation_steps', *extra), 'Isolation protocol')
    require(value['initialization'] == 'throttle',
            'Unsupported isolation protocol')
    require(value['branch'] in BRANCHES, 'Unsupported isolation branch')
    controller = brake.validate_protocol(value['controller_protocol'])
    require(controller['schema_version'] == brake.SETTLED_VERSION and controller['initialization'] == 'throttle'
            and controller['measurement'] == 'coast' and controller['brake_level'] == 0.,
            'Isolation requires the original protocol3 throttle/coast controller')
    require(canonical(value['policy']) == canonical(controller['policy']), 'Isolation policy differs')
    require(type(value['continuation_steps']) is int and value['continuation_steps'] == STEPS,
            'Isolation continuation is exactly forty commands')
    require(type(value['prefix_steps']) is int and 1 <= value['prefix_steps'] <= 300 - STEPS,
            'Replay and continuation exceed the original bounded controller horizon')
    if extra:
        physics = value['continuation_physics']
        exact_keys(physics, ('mode', 'max_substep_delta_time_s', 'max_substeps', 'expected_advanced_frames'), 'Continuation physics')
        require(physics['mode'] == 'apply_settings_first_interval', 'Unsupported continuation physics mode')
        step = finite(physics['max_substep_delta_time_s'], 'Continuation substep')
        require(step in (.01, .005) and type(physics['max_substeps']) is int and physics['max_substeps'] == 10,
                'The bounded substep experiment only permits 0.01s or 0.005s x 10')
        require(type(physics['expected_advanced_frames']) is int and physics['expected_advanced_frames'] == 1,
                'The settings transition must be exactly one observed interval')
        require(value['branch'] == 'hold_throttle',
                'Substep isolation requires the fixed-throttle continuation')
    return deepcopy(value)


def sequence_descriptor(protocol, reference):
    validate_protocol(protocol)
    value = {'schema_version': 'carla-throttle-isolation-sequence/1',
        'prefix_controls': deepcopy(reference['controls']),
        'continuation': [{'step': i, 'time_s': i * DT, 'method': protocol['branch'],
            'control': deepcopy(reference['controls'][-1]) if protocol['branch'] == 'hold_throttle' else None}
            for i in range(STEPS)],
        'scope': 'PI continuation controls are computed from fresh observations immediately before dispatch.'}
    if protocol['schema_version'] == SUBSTEP_VERSION:
        value.update(schema_version='carla-throttle-isolation-sequence/2',
            continuation_physics=deepcopy(protocol['continuation_physics']),
            scope='After matched F0 and command0 ACK, apply_settings supplies the first observed interval; both 0.01s sham and 0.005s treatment use the same call. The remaining 39 intervals use explicit ticks.')
    return value


def _observation(row):
    sample = brake._settled_sample(row)
    require(abs(finite(row['delta_seconds'], 'Snapshot delta') - DT) <= 1e-6, 'Snapshot delta differs')
    for field, keys in (('position_m', ('x', 'y', 'z')), ('velocity_mps', ('x', 'y', 'z')),
                        ('acceleration_mps2', ('x', 'y', 'z')), ('angular_velocity_deg_s', ('x', 'y', 'z')),
                        ('rotation_deg', ('pitch', 'yaw', 'roll'))):
        exact_keys(row['ego'][field], keys, field)
        for v in row['ego'][field].values(): finite(v, field)
    cached = row['reported_control']['data']
    exact_keys(cached, brake.control(), 'Cached control')
    for key in ('throttle', 'brake', 'steer'): finite(cached[key], key)
    for key in ('hand_brake', 'reverse', 'manual_gear_shift'):
        require(type(cached[key]) is bool, 'Invalid cached control flag')
    require(cached['brake'] == cached['steer'] == 0. and not any(cached[k] for k in
            ('hand_brake', 'reverse', 'manual_gear_shift')), 'Unexpected cached non-throttle input')
    require(type(cached['gear']) is int, 'Invalid cached gear')
    return sample


def compare_entry(protocol, candidate, baseline, *, rotating=True):
    """Compare fresh raw states. Frame/time/actor identities may differ across runs."""
    validate_protocol(protocol)
    policy = protocol['policy']; differences = {}; criteria = {}
    try:
        a, b = _observation(candidate), _observation(baseline)
        ea, eb = candidate['ego'], baseline['ego']
        def distance(field):
            return math.dist([ea[field][k] for k in ('x', 'y', 'z')], [eb[field][k] for k in ('x', 'y', 'z')])
        differences.update(position_distance_m=distance('position_m'), velocity_distance_mps=distance('velocity_mps'),
            rotation_max_wrapped_axis_difference_deg=max(abs((ea['rotation_deg'][k]-eb['rotation_deg'][k]+180)%360-180)
                for k in ('pitch', 'yaw', 'roll')),
            heading_acceleration_difference_mps2=abs(a['acceleration']-b['acceleration']),
            angular_velocity_distance_deg_s=distance('angular_velocity_deg_s'),
            cached_throttle_difference=abs(a['throttle']-b['throttle']))
        for field, limit in (('position_distance_m', 'pair_max_position_distance_m'),
                ('velocity_distance_mps', 'pair_max_velocity_distance_mps'),
                ('rotation_max_wrapped_axis_difference_deg', 'pair_max_rotation_difference_deg'),
                ('heading_acceleration_difference_mps2', 'pair_max_heading_acceleration_difference_mps2'),
                ('angular_velocity_distance_deg_s', 'pair_max_angular_velocity_distance_deg_s'),
                ('cached_throttle_difference', 'pair_max_cached_throttle_difference')):
            criteria[field] = differences[field] <= policy[limit]
        criteria['cached_controls'] = all(candidate['reported_control']['data'][k] == baseline['reported_control']['data'][k]
            for k in brake.control() if k != 'throttle') and (a['gear'] > 0 if rotating else True)
        if rotating:
            x, y = brake._raw_rotating_state(candidate), brake._raw_rotating_state(baseline)
            differences['raw_rotating_relative_difference'] = [abs(v-w)/abs(w) if abs(w) > 1e-6 else None for v,w in zip(x,y)]
            criteria['raw_rotating_state'] = all(d is not None and d <= policy['pair_max_raw_state_relative_difference']
                and v*w > 0. for d,v,w in zip(differences['raw_rotating_relative_difference'],x,y))
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        criteria['raw_fields_available'] = False
        differences['unavailable_reason'] = str(error)
    return {'admitted': bool(criteria) and all(criteria.values()), 'criteria': criteria, 'differences': differences,
            'scope': 'Observed entry agreement only; hidden simulator state equality is not established.'}


def validate_reference(reference, protocol):
    exact_keys(reference, ('schema_version', 'experiment_id', 'artifacts', 'source_files', 'installed_module_hashes',
        'rest_ticks', 'inventory_refresh_ticks', 'controls', 'source_cached_gears', 'initial_observation',
        'entry_observation', 'conditions', 'controller_protocol', 'source_evidence_sha256'), 'Isolation reference')
    require(reference['schema_version'] == REFERENCE_VERSION, 'Unsupported reference version')
    require(str(uuid.UUID(reference['experiment_id'])) == reference['experiment_id'], 'Invalid reference UUID')
    hashes(reference['artifacts'], CAPTURE_FILES, 'Reference artifacts')
    hashes(reference['source_files'], BASE_SOURCES, 'Reference source files')
    hashes(reference['installed_module_hashes'], MODULES, 'Reference installed modules')
    hashes({'source': reference['source_evidence_sha256']}, ('source',), 'Original physics evidence')
    exact_keys(reference['conditions'], CONDITIONS, 'Reference conditions')
    require(canonical(reference['controller_protocol']) == canonical(protocol['controller_protocol']), 'Reference controller differs')
    require(type(reference['rest_ticks']) is int and 8 <= reference['rest_ticks'] <= 100, 'Invalid reference rest count')
    require(type(reference['inventory_refresh_ticks']) is int and reference['inventory_refresh_ticks'] in (0,1), 'Invalid inventory tick count')
    n = protocol['prefix_steps']
    require(type(reference['controls']) is list and len(reference['controls']) == n, 'Reference prefix count differs')
    for c in reference['controls']: validate_control(c)
    gears = reference['source_cached_gears']
    require(type(gears) is list and len(gears) == n+1 and all(type(g) is int for g in gears), 'Reference gear history differs')
    a, b = _observation(reference['initial_observation']), _observation(reference['entry_observation'])
    require(b['frame']-a['frame'] == n and abs(b['time']-a['time']-n*DT) <= 1e-6,
            'Reference elapsed frame/time differs')
    require(a['gear'] == gears[0] and b['gear'] == gears[-1], 'Reference endpoint gear differs')
    require(compare_entry(protocol, reference['entry_observation'], reference['entry_observation'])['admitted'],
            'Reference entry raw state unavailable')
    return deepcopy(reference)


def reference_from_report(report, source_intent, artifact_hashes):
    """Derive a compact pin after callers verify the original hash chain and sources."""
    brake.validate_intent(source_intent)
    prep = report['preparation']; rows = prep['observations']; commands = prep['commands']
    require(report['schema_version'] == brake.REPORT_VERSION and report['experiment_id'] == source_intent['experiment_id']
        and report['intent_sha256'] == sha(canonical(source_intent)), 'Source report identity differs')
    for key in ('protocol_sha256', 'sequence_sha256', 'source_files', 'source_evidence', 'engine_build', 'adapter_version'):
        require(canonical(report[key]) == canonical(source_intent[key]), 'Source report pin differs: ' + key)
    require(report['original'] == source_intent['expected_world'] and
            report['requested_weather'] == source_intent['requested_weather'], 'Source world/weather differs')
    physics = report['physics_configuration']
    require(physics['status'] == 'available' and physics['friction_status'] == 'matched' and
            canonical({k: physics[k] for k in ('scalars', 'wheels')}) == canonical(source_intent['expected_physics']),
            'Source post-rest physics differs')
    require(report['cleanup']['errors'] == [] and report['cleanup']['observed'] == report['original'],
            'Source cleanup world preservation differs')
    shutdown = report['callback_shutdown']
    require(shutdown['accepting'] is False and shutdown['drained'] is True and type(shutdown['active']) is int and
            shutdown['active'] == 0 and type(shutdown['started']) is int and shutdown['started'] >= 0 and
            type(shutdown['finished']) is int and shutdown['finished'] == shutdown['started'],
            'Source callback drain differs')
    require(report['status'] == 'failed' and report['rest']['status'] == 'passed' and
            report['cleanup']['status'] == 'confirmed' and report['release_eligible'] is True and
            report['event_chain']['sealed'] is True and not report['collision_callbacks'], 'Source terminal/cleanup state differs')
    require(prep['entry_gate']['plane_reached'] is True and prep['entry_gate']['admitted'] is False,
            'Source is not the observed failed first-crossing preparation')
    require(report['measurement']['observations'] == [] and all(c['application_status'] == 'not_attempted'
            for c in report['measurement']['commands']), 'Source has attempted brake measurement')
    require(len(rows) == len(commands)+1 and all(c['application_status'] == 'observed_next_frame' for c in commands),
            'Source preparation is not a fully observed control prefix')
    p = make_protocol(source_intent['protocol'], 'continue_pi', len(commands))
    expected_actor = report['spawn']['ego']['returned_identity']
    require(prep['velocity_request'] is None and prep['mode'] == 'throttle', 'Source velocity injection is not allowed')
    require(len(prep['pi_steps']) == len(commands), 'Source PI trace count differs')
    for row in rows:
        require({k: row['ego'][k] for k in ('actor_id', 'type_id', 'role_name')} == expected_actor,
                'Source observation actor identity differs')
        bounds = brake.spatial_check(row['ego']['position_m'], row['ego']['rotation_deg'], row['ego']['velocity_mps'],
                                    source_intent['spawn_transform'], phase='preparation', protocol=source_intent['protocol'])
        require(row['bounds'] == bounds and bounds['status'] == 'within_bounds', 'Source preparation bounds differ')
    state = None
    for i, (command, before, after) in enumerate(zip(commands, rows, rows[1:])):
        a, b = _observation(before), _observation(after)
        require(a['frame']+1 == b['frame'] and abs(b['time']-a['time']-DT) <= 1e-6, 'Source clock discontinuity')
        proposed = brake.settled_preparation_step(p['controller_protocol'], before, state)
        require(canonical(command['control']) == canonical(proposed['control']), 'Source PI request differs')
        require(canonical(prep['pi_steps'][i]) == canonical(proposed['trace']), 'Source PI trace differs')
        require(command['rpc_response'] == {'actor_id': expected_actor['actor_id'], 'error': None} and
                command['rpc_before_world_frame'] == command['rpc_after_world_frame'] == a['frame'],
                'Source command ACK identity/frame differs')
        require(command['world_frame'] == a['frame'] and command['next_world_frame'] == b['frame'] and
                command['next_observation_index'] == i+1, 'Source command transition differs')
        for k in ('throttle', 'brake', 'steer'):
            require(after['reported_control']['data'][k] == f32(command['control'][k]), 'Source readback differs')
        state = proposed['state']
    require(canonical(state) == canonical(prep['pi_state']), 'Source committed PI state differs')
    gate = brake.settled_preparation_gate(source_intent['protocol'], rows, source_intent['spawn_transform'])
    require(canonical(gate) == canonical(prep['entry_gate']) and gate['crossed_plane_now'] is True and
            gate['first_crossing_index'] == len(commands), 'Source first-crossing gate differs')
    reference = {'schema_version': REFERENCE_VERSION, 'experiment_id': source_intent['experiment_id'],
        'artifacts': deepcopy(artifact_hashes), 'source_files': deepcopy(source_intent['source_files']),
        'installed_module_hashes': deepcopy(report['installed_module_hashes']),
        'rest_ticks': len(report['rest']['trace']), 'inventory_refresh_ticks': report['inventory_refresh_ticks'],
        'controls': [deepcopy(c['control']) for c in commands],
        'source_cached_gears': [_observation(row)['gear'] for row in rows],
        'initial_observation': deepcopy(rows[0]), 'entry_observation': deepcopy(rows[-1]),
        'conditions': {k: deepcopy(source_intent[k]) for k in CONDITIONS},
        'controller_protocol': deepcopy(source_intent['protocol']),
        'source_evidence_sha256': source_intent['source_evidence']['sha256']}
    return validate_reference(reference, p)


def verify_reference_capture(blobs):
    """Verify original capture consistency before a runner imports CARLA.

    Content hashes are not an authentication of engine execution. This rejects
    internally inconsistent inputs even when a caller bypasses the author CLI.
    """
    exact_keys(blobs, CAPTURE_FILES, 'Reference capture')
    for name, maximum in (('source-intent.json', 128*1024), ('report.json', 16*1024*1024),
            ('events.jsonl', 32*1024*1024), ('reservation-release.json', 64*1024)):
        require(type(blobs[name]) is bytes and 0 < len(blobs[name]) <= maximum, 'Reference capture byte bound differs')
    source_intent = brake.parse_intent(blobs['source-intent.json'])
    require(blobs['source-intent.json'] == canonical(source_intent), 'Source intent canonical bytes differ')
    report = parse_json(blobs['report.json']); receipt = parse_json(blobs['reservation-release.json'])
    previous = '0'*64; count = 0; starts = []; final = None
    require(blobs['events.jsonl'].endswith(b'\n'), 'Truncated reference event chain')
    for line in blobs['events.jsonl'].splitlines():
        require(count < 12000 and len(line) <= 512*1024, 'Reference journal bound exceeded')
        event = parse_json(line)
        exact_keys(event, ('sequence', 'previous_sha256', 'at', 'kind', 'data', 'sha256'), 'Reference event')
        require(type(event['sequence']) is int and event['sequence'] == count and event['previous_sha256'] == previous,
                'Reference event sequence/previous hash differs')
        require(type(event['at']) is str and type(event['kind']) is str and type(event['data']) is dict,
                'Reference event types differ')
        require(event['sha256'] == sha(canonical({k:v for k,v in event.items() if k != 'sha256'})),
                'Reference event content hash differs')
        if event['kind'] == 'diagnostic_started': starts.append(event['data'])
        previous = event['sha256']; count += 1; final = event
    require(report['event_chain'] == {'entries': count, 'head_sha256': previous, 'sealed': True},
            'Reference report chain seal differs')
    require(len(starts) == 1 and starts[0] == {k: report[k] for k in
            ('experiment_id', 'intent_sha256', 'protocol_sha256', 'sequence_sha256', 'source_files')},
            'Reference journal start differs')
    expected = {'status': report['status'], 'error': report['error'], 'cleanup_status': report['cleanup']['status'],
        'reservation_retained': report['reservation_retained'],
        'report_projection_sha256': sha(canonical({k:v for k,v in report.items() if k not in ('event_chain','finished_at')}))}
    require(final is not None and final['kind'] == 'diagnostic_final' and final['data'] == expected,
            'Reference terminal report projection differs')
    exact_keys(receipt, ('schema_version','experiment_id','diagnostic_id','store_id','report_sha256','status','released_at'),
               'Reference release receipt')
    require(receipt['schema_version'] == 'carla-brake-reservation-release/1' and receipt['status'] == 'released' and
            receipt['experiment_id'] == source_intent['experiment_id'] and receipt['diagnostic_id'] == report['diagnostic_id'] and
            receipt['store_id'] == source_intent['expected_store_id'] and receipt['report_sha256'] == sha(blobs['report.json']) and
            report['reservation_retained'] is True and report['store_id'] == source_intent['expected_store_id'],
            'Reference release receipt identity/hash differs')
    return reference_from_report(report, source_intent, {k:sha(v) for k,v in blobs.items()})


def validate_intent(value):
    base_keys = ('schema_version', 'experiment_id', 'protocol', 'protocol_sha256', 'sequence_sha256',
        'expected_world', 'expected_store_id', 'engine_build', 'adapter_version', 'spawn_transform',
        'requested_weather', 'expected_physics', 'source_files', 'source_evidence')
    exact_keys(value, (*base_keys, 'reference'), 'Isolation intent')
    protocol = validate_protocol(value['protocol'])
    require(value['schema_version'] == intent_version(protocol), 'Unsupported isolation intent/protocol version pair')
    reference = validate_reference(value['reference'], protocol)
    require(value['experiment_id'] != reference['experiment_id'], 'Source experiment cannot be reused')
    require(value['protocol_sha256'] == sha(canonical(protocol)), 'Isolation protocol hash differs')
    require(value['sequence_sha256'] == sha(canonical(sequence_descriptor(protocol, reference))), 'Isolation sequence hash differs')
    hashes(value['source_files'], SOURCE_FILES, 'Isolation source files')
    for k in CONDITIONS:
        if k == 'expected_world' and protocol['schema_version'] == SUBSTEP_VERSION:
            # A restart changes the episode ID, not the pinned map/settings or
            # static actor inventory. Keep the original reference untouched.
            current, original = value[k], reference['conditions'][k]
            require(canonical({n:v for n,v in current.items() if n != 'world_id'}) ==
                    canonical({n:v for n,v in original.items() if n != 'world_id'}),
                    'Rebased world differs beyond its episode ID')
            continue
        require(canonical(value[k]) == canonical(reference['conditions'][k]), 'Reference condition differs: ' + k)
    require(value['source_evidence']['sha256'] == reference['source_evidence_sha256'], 'Original physics source differs')
    base = {k: deepcopy(value[k]) for k in base_keys}
    base.update(schema_version=brake.INTENT_VERSION, protocol=protocol['controller_protocol'],
        protocol_sha256=sha(canonical(protocol['controller_protocol'])),
        sequence_sha256=sha(canonical(brake.measurement_sequence(protocol['controller_protocol']))),
        source_files={k: value['source_files'][k] for k in BASE_SOURCES})
    brake.validate_intent(base)
    return deepcopy(value)


def parse_json(raw):
    def unique(pairs):
        out = {}
        for k,v in pairs:
            require(k not in out, 'Duplicate JSON key'); out[k] = v
        return out
    def invalid(token): raise ValueError('Nonfinite JSON token: ' + token)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def parse_intent(raw):
    require(len(raw) <= 128*1024, 'Isolation intent exceeds byte cap')
    return validate_intent(parse_json(raw))


def prefix_step(protocol, reference, index, fresh_observation, state=None):
    validate_protocol(protocol)
    require(type(index) is int and 0 <= index < protocol['prefix_steps'], 'Invalid prefix step')
    require((state['step'] if state else 0) == index, 'Prefix state index differs')
    proposed = brake.settled_preparation_step(protocol['controller_protocol'], fresh_observation, state)
    requested = validate_control(reference['controls'][index])
    matched = controls_equal(proposed['control'], requested) and _observation(fresh_observation)['gear'] == reference['source_cached_gears'][index]
    committed = deepcopy(proposed['state'])
    committed['previous_throttle'] = requested['throttle']
    return {'control': requested, 'state': committed, 'matched': matched,
        'trace': {'schema_version': 'carla-throttle-isolation-step/1', 'method': 'replay_prefix', 'step': index,
            'proposed_control': proposed['control'], 'requested_control': requested, 'matched': matched,
            'expected_source_cached_gear': reference['source_cached_gears'][index],
            'observed_source_cached_gear': _observation(fresh_observation)['gear'],
            'pi_trace': proposed['trace'], 'state_after_ack': committed}}


def handoff_gate(protocol, reference, fresh_observations, rest_ticks, inventory_refresh_ticks):
    validate_protocol(protocol)
    rows = fresh_observations; n = protocol['prefix_steps']
    criteria = {'complete_prefix': len(rows) == n+1, 'rest_ticks': rest_ticks == reference['rest_ticks'],
        'inventory_refresh_ticks': inventory_refresh_ticks == reference['inventory_refresh_ticks']}
    differences = {}
    try:
        samples = [_observation(row) for row in rows]
        criteria['source_gear_history'] = [s['gear'] for s in samples] == reference['source_cached_gears']
        criteria['source_clock'] = all(b['frame'] == a['frame']+1 and abs(b['time']-a['time']-DT) <= 1e-6
                                      for a,b in zip(samples,samples[1:]))
        for key, candidate, baseline, rotating in (('initial_state', rows[0], reference['initial_observation'], False),
                ('entry_state', rows[-1], reference['entry_observation'], True)):
            comparison = compare_entry(protocol, candidate, baseline, rotating=rotating)
            criteria[key] = comparison['admitted']; differences[key] = comparison
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        criteria['raw_fields_available'] = False; differences['unavailable_reason'] = str(error)
    return {'admitted': all(criteria.values()), 'criteria': criteria, 'differences': differences,
            'scope': 'Input isolation handoff; failed protocol3 settled admission is not overridden.'}


def extension_step(protocol, index, fresh_observation, state, last_requested_control):
    validate_protocol(protocol)
    require(type(index) is int and 0 <= index < STEPS, 'Invalid continuation step')
    _observation(fresh_observation)
    if protocol['branch'] == 'continue_pi':
        require(state['step'] == protocol['prefix_steps']+index, 'Continuation PI state index differs')
        proposed = brake.settled_preparation_step(protocol['controller_protocol'], fresh_observation, state)
        return {**proposed, 'trace': {'schema_version': 'carla-throttle-isolation-step/1',
            'method': 'continue_pi', 'step': index, 'pi_trace': proposed['trace']}}
    requested = validate_control(last_requested_control)
    return {'control': requested, 'state': deepcopy(state),
        'trace': {'schema_version': 'carla-throttle-isolation-step/1', 'method': 'hold_throttle',
            'step': index, 'source_world_frame': fresh_observation['world_frame'],
            'source_time_s': fresh_observation['source_time_s'], 'requested_control': requested,
            'scope': 'Last prefix request held; the PI controller state is not advanced.'}}


def spatial_check(position, rotation, velocity, spawn, *, phase='measurement', protocol):
    validate_protocol(protocol)
    return brake.spatial_check(position, rotation, velocity, spawn, phase=phase, protocol=protocol['controller_protocol'])
