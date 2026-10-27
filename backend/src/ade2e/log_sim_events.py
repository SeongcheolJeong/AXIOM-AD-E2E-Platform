"""Deterministic investigation candidates from retained, hash-checked log samples.

These rules identify places to review. They do not label a safety failure or run
camera perception. Distances are annotation/native bbox CENTER distances.
"""
import math
from typing import Literal

from fastapi import HTTPException, Query, Request
from sqlalchemy import select

from . import log_sim
from .recording_models import RecordingMessageRow
from .service import digest
from .team_access import project_authorize
from .project_recordings import authorize_recording_source

RULE_VERSION = 'log-investigation-rules/1'
MAX_SAMPLE_GAP_S = 1.0
RULES = {
    'low_speed': dict(label='저속·정지 구간', description='기록된 자차 속도가 0.3 m/s 이하인 구간입니다.',
        aliases='저속 정지 대기 low speed stop wait',
        thresholds={'maximum_speed_mps': .3, 'minimum_duration_s': .5}),
    'deceleration': dict(label='감속 구간', description='약 0.5초 간격의 기록 속도에서 1.5 m/s² 이상의 평균 감속이 관측됩니다.',
        aliases='감속 제동 deceleration braking brake',
        thresholds={'maximum_acceleration_mps2': -1.5, 'target_difference_window_s': .5,
                    'minimum_difference_window_s': .2}),
    'nearby_ahead': dict(label='전방 근접 객체', description='자차 진행 방향 앞쪽 15 m, 좌우 3 m 안에 객체 중심이 기록되었습니다.',
        aliases='전방 근접 객체 nearby ahead close object',
        thresholds={'maximum_longitudinal_center_distance_m': 15., 'maximum_absolute_lateral_center_distance_m': 3.}),
}


def source_frames(db, session, project_id, source):
    """Read existing bytes only; authorization belongs to the calling route."""
    if source['kind'] == 'carla':
        scene, _, receipt = log_sim._scene(db, session, project_id, source)
        frames = []
        for index, (ego, objects) in enumerate(log_sim.carla_planning_frames(scene)):
            frames.append(dict(time_s=scene['frames'][index]['time_s'], timestamp_ns=ego['timestamp_ns'],
                ego=dict(x=ego['x_m'], y=ego['y_m'], yaw_rad=ego['yaw_rad'], speed_mps=ego['speed_mps']),
                objects=[dict(id=obj['id'], x=obj['x_m'], y=obj['y_m']) for obj in objects['objects']],
                sample_ref={'scene_frame_index': index}))
        side, snapshot = log_sim.object_demo._execution(session, project_id, source['baseline_execution_id'])
        receipt = receipt | {'adapter_id': side['execution']['adapter_id'],
            'adapter_version': side['execution']['adapter_version'], 'fidelity': side['execution'].get('fidelity'),
            'seed': snapshot.get('source', {}).get('seed'), 'coordinate_conversion': 'native_bbox_center_then_y_yaw_reflection'}
        return frames, receipt
    if source['kind'] not in {'nuscenes', 'recorded'}:
        raise HTTPException(422, 'Event discovery does not support this source kind')
    frames = log_sim._road_frames(db, session, project_id, source)
    ref = source['recording_ref']
    channels = (ref['ego_channel_id'], ref['objects_channel_id'])
    samples = {}
    for row in session.scalars(select(RecordingMessageRow).where(
            RecordingMessageRow.recording_id == ref['id'], RecordingMessageRow.channel_id.in_(channels))):
        key = (int(row.log_time_key), row.channel_id)
        if key in samples:
            raise HTTPException(409, 'Event discovery requires unambiguous source timestamps')
        samples[key] = {'ordinal': row.ordinal, 'sha256': row.sha256, 'channel_id': row.channel_id}
    for frame in frames:
        at = int(frame['timestamp_ns'])
        if any((at, channel) not in samples for channel in channels):
            raise HTTPException(409, 'Event discovery requires exact ego and object samples')
        frame['sample_ref'] = {'ego': samples[(at, channels[0])], 'objects': samples[(at, channels[1])]}
    return frames, {'recording_ref': ref, 'annotation_source': source.get('annotation_source', 'recorded_objects'),
        'source_scope': source['source_scope'], 'integrity': 'indexed_ego_and_object_sample_sha256_verified'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate_frames(frames):
    if not 1 <= len(frames) <= 512:
        raise HTTPException(422, 'Event discovery requires 1..512 retained frames')
    previous = -math.inf
    for frame in frames:
        if not _finite(frame.get('time_s')) or frame['time_s'] < 0 or frame['time_s'] <= previous:
            raise HTTPException(409, 'Event discovery requires finite, increasing source times')
        previous = frame['time_s']
        ego = frame.get('ego', {})
        if any(not _finite(ego.get(key)) for key in ('x', 'y', 'yaw_rad', 'speed_mps')) or ego['speed_mps'] < 0:
            raise HTTPException(409, 'Event discovery requires complete recorded ego state')
        if not isinstance(frame.get('timestamp_ns'), str) or not frame['timestamp_ns'].isdigit():
            raise HTTPException(409, 'Event discovery requires exact source timestamps')
        if not isinstance(frame.get('objects'), list):
            raise HTTPException(409, 'Event discovery requires recorded object observations')
        for obj in frame['objects']:
            if not obj.get('id') or any(not _finite(obj.get(key)) for key in ('x', 'y')):
                raise HTTPException(409, 'Event discovery requires complete recorded object positions')


def _spans(frames, matches):
    """Group only adjacent matching samples without bridging missing time."""
    group = []
    for index in matches:
        if group and (index != group[-1] + 1 or frames[index]['time_s'] - frames[group[-1]]['time_s'] > MAX_SAMPLE_GAP_S):
            yield group
            group = []
        group.append(index)
    if group:
        yield group


def discover_events(source, frames, receipt):
    """Return a pinned analysis independent of filtering/pagination."""
    _validate_frames(frames)
    # Ignore presentation URLs/camera pairing: the rules consume exact ego/object state.
    trace = [{key: frame[key] for key in ('time_s', 'timestamp_ns', 'ego', 'objects', 'sample_ref') if key in frame}
             for frame in frames]
    rules = [{'id': 'log-sim.' + kind, 'kind': kind, 'version': RULE_VERSION,
              'label': value['label'], 'thresholds': value['thresholds'] | {'maximum_sample_gap_s': MAX_SAMPLE_GAP_S}}
             for kind, value in RULES.items()]
    provenance = dict(source_id=source['id'], source_hash=source['source_hash'], source_receipt=receipt,
        rule_version=RULE_VERSION, rules=rules, trace_hash=digest(trace), frame_count=len(frames))
    analysis_hash = digest(provenance)
    events = []

    def add(kind, start, end, measured):
        rule = RULES[kind]
        evidence = dict(start_sample_index=start, end_sample_index=end,
            start_timestamp_ns=frames[start]['timestamp_ns'], end_timestamp_ns=frames[end]['timestamp_ns'],
            sample_count=end-start+1, sample_refs=[frame.get('sample_ref', {'scene_frame_index': index})
                for index, frame in enumerate(frames[start:end+1], start)], **measured)
        value = dict(label=rule['label'], kind=kind, time_s=frames[start]['time_s'], end_time_s=frames[end]['time_s'],
            severity='review', description=rule['description'], rule_id='log-sim.'+kind, rule_version=RULE_VERSION,
            thresholds=rule['thresholds'] | {'maximum_sample_gap_s': MAX_SAMPLE_GAP_S}, evidence=evidence,
            source_id=source['id'], source_hash=source['source_hash'], analysis_hash=analysis_hash,
            origin='recorded_state_rule', review_status='unreviewed', verdict='not_evaluated')
        event_hash = digest(value)
        events.append(value | {'id': 'logevent_'+event_hash[:32], 'event_hash': event_hash})

    low = [i for i, frame in enumerate(frames) if frame['ego']['speed_mps'] <= .3]
    for span in _spans(frames, low):
        start, end = span[0], span[-1]
        if frames[end]['time_s'] - frames[start]['time_s'] >= .5:
            add('low_speed', start, end, {'minimum_speed_mps': min(frames[i]['ego']['speed_mps'] for i in span),
                'maximum_speed_mps': max(frames[i]['ego']['speed_mps'] for i in span),
                'observed_duration_s': frames[end]['time_s'] - frames[start]['time_s']})

    acceleration = {}; window_start = {}
    for index in range(1, len(frames)):
        candidates = []
        for previous in range(index-1, -1, -1):
            dt = frames[index]['time_s'] - frames[previous]['time_s']
            if dt > MAX_SAMPLE_GAP_S:
                break
            if dt >= .2 - 1e-9:
                candidates.append((abs(dt-.5), previous, dt))
        if candidates:
            _, previous, dt = min(candidates)
            value = (frames[index]['ego']['speed_mps'] - frames[previous]['ego']['speed_mps']) / dt
            if value <= -1.5:
                acceleration[index] = value; window_start[index] = previous
    braking_spans = []
    for span in _spans(frames, acceleration):
        # Overlapping measurement windows describe the same review interval.
        if braking_spans and window_start[span[0]] <= braking_spans[-1][-1]:
            braking_spans[-1].extend(span)
        else:
            braking_spans.append(span)
    for span in braking_spans:
        peak = min(span, key=acceleration.get)
        add('deceleration', window_start[span[0]], span[-1], {'minimum_acceleration_mps2': acceleration[peak],
            'peak_timestamp_ns': frames[peak]['timestamp_ns'],
            'peak_window_start_timestamp_ns': frames[window_start[peak]]['timestamp_ns'],
            'peak_window_duration_s': frames[peak]['time_s']-frames[window_start[peak]]['time_s'],
            'qualifying_window_count': len(span), 'overlapping_measurement_windows_merged': True,
            'method': 'windowed_recorded_speed_finite_difference'})

    nearby = {}
    for index, frame in enumerate(frames):
        ego = frame['ego']; cosine = math.cos(ego['yaw_rad']); sine = math.sin(ego['yaw_rad'])
        for obj in frame['objects']:
            dx, dy = obj['x']-ego['x'], obj['y']-ego['y']
            forward, lateral = dx*cosine+dy*sine, -dx*sine+dy*cosine
            if 0 < forward <= 15 and abs(lateral) <= 3:
                nearby.setdefault(obj['id'], {})[index] = (forward, lateral)
    for identity in sorted(nearby):
        observations = nearby[identity]
        for span in _spans(frames, observations):
            closest = min(span, key=lambda i: observations[i][0])
            distance, lateral = observations[closest]
            add('nearby_ahead', span[0], span[-1], {'object_id': identity,
                'minimum_longitudinal_center_distance_m': distance, 'absolute_lateral_center_distance_m': abs(lateral),
                'closest_timestamp_ns': frames[closest]['timestamp_ns'], 'method': 'annotation_center_in_ego_heading_frame',
                'distance_scope': 'center_distance_not_bbox_clearance_or_collision_risk'})
    events.sort(key=lambda event: (event['time_s'], event['kind'], event['id']))
    return provenance | {'analysis_hash': analysis_hash, 'items': events, 'events_hash': digest(events),
        'scope': 'rule_based_investigation_candidates', 'physical_sensor_validation': False,
        'verdict': 'not_evaluated', 'limitations': [
            'Threshold matches are review candidates, not confirmed failures.',
            'No new camera perception or model inference is performed.',
            'Object center distances do not establish lane membership, collision clearance or TTC.',
            'Intervals cover observed samples; no interpolation across missing time.']}


def register_log_sim_event_routes(api, db):
    @api.get('/log-sim/sources/{identity}/events', tags=['Log Sim'])
    def source_events(identity: str, request: Request, project_id: str = 'default',
            q: str = Query(default='', max_length=120),
            kind: Literal['low_speed', 'deceleration', 'nearby_ahead'] | None = None,
            start_time_s: float | None = Query(default=None, ge=0, allow_inf_nan=False),
            end_time_s: float | None = Query(default=None, ge=0, allow_inf_nan=False),
            offset: int = Query(default=0, ge=0, le=10000), limit: int = Query(default=50, ge=1, le=100)):
        if start_time_s is not None and end_time_s is not None and start_time_s > end_time_s:
            raise HTTPException(422, 'Use an ordered source time interval')
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            source = log_sim._source(session, project_id, identity)
            if source['kind'] in {'nuscenes', 'recorded'}:
                authorize_recording_source(request, db, project_id, source['recording_ref']['id'])
            frames, receipt = source_frames(db, session, project_id, source)
        analysis = discover_events(source, frames, receipt)
        terms = q.casefold().split()
        items = [event for event in analysis['items']
                 if (kind is None or event['kind'] == kind)
                 and (start_time_s is None or event['end_time_s'] >= start_time_s)
                 and (end_time_s is None or event['time_s'] <= end_time_s)
                 and all(term in (' '.join([event['label'], event['description'], event['kind'],
                     RULES[event['kind']]['aliases'], str(event['evidence'].get('object_id', ''))])).casefold() for term in terms)]
        return analysis | {'schema_version': 'log-sim-event-search/1', 'project_id': project_id,
            'items': items[offset:offset+limit], 'total': len(items), 'unfiltered_total': len(analysis['items']),
            'offset': offset, 'limit': limit, 'query': {'q': q, 'kind': kind, 'start_time_s': start_time_s, 'end_time_s': end_time_s}}
