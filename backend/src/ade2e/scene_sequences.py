"""Freeze exact MCAP driving observations without inventing missing inputs."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from .recording_models import RecordingChannelRow, RecordingMessageRow
from .recordings import _get_recording, _managed_path, read_message_bytes, select_message_at
from .scene_sequence_models import SceneSequenceRow
from .scene_sequence_schemas import ObjectTracks, PlanningEgo, SceneSequenceCreate, SceneSequenceDetail
from .sensors import _channel, binding_get
from .service import canonical_bytes, digest, uid, utcnow

EGO_SCHEMA_NAME = 'axiom.PlanningEgo.v1'
OBJECT_SCHEMA_NAME = 'axiom.ObjectTracks.v1'
EGO_SCHEMA = PlanningEgo.model_json_schema()
OBJECT_SCHEMA = ObjectTracks.model_json_schema()
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024


def _json_exact(content):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    if len(content) > MAX_JSON_BYTES:
        raise ValueError('Driving log message exceeds 256 KiB')
    return json.loads(content.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _supported(channel, schema, role):
    name, definition = (EGO_SCHEMA_NAME, EGO_SCHEMA) if role == 'ego' else (OBJECT_SCHEMA_NAME, OBJECT_SCHEMA)
    if (channel.message_encoding != 'json' or schema is None or schema.name != name
            or schema.encoding != 'jsonschema' or schema.definition is None or schema.definition_truncated):
        return False
    raw = schema.definition.encode('utf-8')
    return (len(raw) == schema.size_bytes and hashlib.sha256(raw).hexdigest() == schema.sha256
            and _json_exact(raw) == definition)


def discover_sequence_sources(session, recording_id):
    recording = _get_recording(session, recording_id)
    channels = []
    for row in session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id == recording_id).order_by(RecordingChannelRow.channel_id)):
        channel, schema = _channel(session, recording_id, row.channel_id)
        for role in ('ego', 'objects'):
            try:
                supported = _supported(channel, schema, role)
            except (ValueError, UnicodeError):
                supported = False
            if supported:
                channels.append({'channel_id': channel.id, 'topic': channel.topic, 'role': role,
                    'schema_name': schema.name, 'schema_sha256': schema.sha256, 'message_count': channel.message_count,
                    'start_time_ns': channel.start_time_ns, 'end_time_ns': channel.end_time_ns})
    return {'recording_id': recording_id, 'recording_sha256': recording.sha256, 'channels': channels,
        'schemas': {'ego': EGO_SCHEMA, 'objects': OBJECT_SCHEMA},
        'limitations': ['Topic names alone do not establish roles.', 'Existing EgoState.v1 has no world pose and is not sufficient for planning closed loop.']}


def _message(recording, entry, decoder, decoder_version='1'):
    return {'recording_id': recording.id, 'recording_sha256': recording.sha256, 'channel_id': entry.channel_id,
        'ordinal': entry.ordinal, 'log_time_ns': str(int(entry.log_time_key)),
        'publish_time_ns': str(int(entry.publish_time_key)), 'sequence': entry.sequence,
        'message_sha256': entry.sha256, 'message_size_bytes': entry.payload_length,
        'decoder_id': decoder, 'decoder_version': decoder_version}


def _decode(db, recording, entry, role):
    cls = PlanningEgo if role == 'ego' else ObjectTracks
    decoded = cls.model_validate(_json_exact(read_message_bytes(db, recording, entry))).model_dump(mode='json')
    # These v1 planning channels declare one capture clock. A container write
    # time is not a substitute for the pose/box observation time. More general
    # clock mappings require an explicit converter, not inferred offsets.
    if int(decoded['timestamp_ns']) != int(entry.log_time_key):
        raise ValueError(f'{role} capture timestamp must equal its original MCAP log timestamp for the v1 planning contract; explicit capture-clock conversion is required')
    return decoded


def _time_check(previous, at, maximum):
    if not 0 <= at <= 2**64 - 1:
        raise ValueError('Aligned decision timestamp exceeds uint64')
    if previous is not None and (at <= previous or at - previous > maximum):
        raise ValueError('Decision times must be strictly increasing with no gap above max_step_ns')


def prepare_sequence(session, db, body, *, snapshot_version='scene-sequence/2'):
    """Resolve causal predecessor identities and preserve omissions as omissions."""
    body = SceneSequenceCreate.model_validate(body)
    if snapshot_version not in {'scene-sequence/1', 'scene-sequence/2'}:
        raise ValueError('Unsupported scene sequence snapshot version')
    request = body.model_dump(mode='json')
    frames, previous = [], None
    limitations = ['Planar declared geometry; neither calibrated road truth nor a sensor reconstruction.',
        'Objects follow their recorded observations and do not react to the replay ego.',
        'Retained camera/LiDAR/radar references are nonreactive and are not controller inputs in reference planning replay.']
    if body.source_type == 'authored_reference':
        for index, frame in enumerate(body.authored_frames):
            at = int(frame.at_ns)
            _time_check(previous, at, int(body.max_step_ns))
            previous = at
            if frame.ego.frame_id != body.world_frame:
                raise ValueError('Authored ego must explicitly use the selected world frame')
            if int(frame.ego.timestamp_ns) > at or at - int(frame.ego.timestamp_ns) > int(body.object_max_age_ns):
                raise ValueError('Authored ego capture time is future or stale')
            if int(frame.objects.timestamp_ns) > at or at - int(frame.objects.timestamp_ns) > int(body.object_max_age_ns):
                raise ValueError('Authored object capture time is future or stale')
            if frame.objects.coordinate_frame == 'world' and frame.objects.frame_id != body.world_frame:
                raise ValueError('World objects must declare the selected world frame')
            if frame.objects.coordinate_frame == 'ego' and frame.objects.frame_id != 'ego':
                raise ValueError('Relative objects require frame_id ego with the pose origin at the original ego reference point')
            if frame.objects.coordinate_frame == 'ego' and frame.objects.timestamp_ns != frame.ego.timestamp_ns:
                raise ValueError('Relative authored objects require the original ego pose at their exact capture timestamp')
            frames.append({'index': index, 'at_ns': frame.at_ns, 'ego': frame.ego.model_dump(mode='json'),
                'objects': frame.objects.model_dump(mode='json'), 'object_reference_ego': frame.ego.model_dump(mode='json'),
                'ego_source': None, 'object_source': None, 'object_age_ns': str(at-int(frame.objects.timestamp_ns)),
                'object_drop': None, 'sensors': []})
        limitations.append('Authored synthetic reference inputs; no measured-log acceptance.')
        return {'schema_version': snapshot_version, 'request': request, 'source_kind': 'synthetic',
            'recording_snapshot': None, 'binding_snapshot': None, 'frames': frames, 'limitations': limitations}
    recording = _get_recording(session, body.recording_id)
    original_path=_managed_path(db.recording_dir,recording.storage_key)
    original_sha=hashlib.sha256()
    original_size=0
    with original_path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            original_size+=len(chunk)
            if original_size>recording.payload['size_bytes']:
                raise ValueError('Original recording grew beyond its pinned byte size')
            original_sha.update(chunk)
    if original_size!=recording.payload['size_bytes'] or original_sha.hexdigest()!=recording.sha256:
        raise ValueError('Original recording bytes differ from the pinned source identity')
    checked_sensor_messages=set()
    channel_ids = [body.ego_channel_id, body.object_channel_id] + [item.channel_id for item in body.sensor_channels]
    channel_defs = [_channel(session, recording.id, identifier) for identifier in channel_ids]
    for role, (channel, schema) in zip(('ego', 'objects'), channel_defs):
        if not _supported(channel, schema, role):
            raise ValueError(f'{role} channel must use the exact supported JSON/schema contract; no topic-based conversion')
    binding = binding_get(session, recording.id, body.binding_revision) if body.binding_revision else None
    if binding:
        if binding.recording_sha256 != recording.sha256 or binding.rig_snapshot.coordinate_convention != 'right_handed_z_up':
            raise ValueError('Pinned sensor binding source/convention differs from the replay')
        for sensor in body.sensor_channels:
            mapping = next((item for item in binding.channels if item.channel_id == sensor.channel_id), None)
            if (mapping is None or mapping.decoder_id != sensor.decoder_id or mapping.decoder_version != sensor.decoder_version
                    or mapping.log_time_offset_ns != sensor.log_time_offset_ns or mapping.max_age_ns != sensor.max_age_ns):
                raise ValueError('Additional sensor selection differs from its pinned binding revision')
            declaration = next(item for item in binding.rig_snapshot.sensors if item.key == mapping.sensor_key)
            if declaration.kind != sensor.role:
                raise ValueError('Additional sensor role differs from its pinned rig declaration')
    for index, ordinal in enumerate(body.ego_ordinals):
        ego_entry = session.get(RecordingMessageRow, (recording.id, ordinal))
        if ego_entry is None or ego_entry.channel_id != body.ego_channel_id:
            raise ValueError('An exact ego ordinal is absent from the selected channel')
        at = int(ego_entry.log_time_key) + int(body.ego_log_time_offset_ns)
        _time_check(previous, at, int(body.max_step_ns))
        previous = at
        ego = _decode(db, recording, ego_entry, 'ego')
        if ego['frame_id'] != body.world_frame:
            raise ValueError('Original ego pose must use the selected world frame')
        object_entry, following = select_message_at(session, recording.id, body.object_channel_id,
            at - int(body.object_log_time_offset_ns))
        object_at = int(object_entry.log_time_key) + int(body.object_log_time_offset_ns) if object_entry else None
        if object_at is not None and not 0<=object_at<=2**64-1:
            raise ValueError('Aligned object observation timestamp exceeds uint64')
        object_age = at-object_at if object_at is not None else None
        objects, reference, object_drop, reference_source = None, None, None, None
        if object_entry is None:
            object_drop = 'no_causal_object_message'
        elif object_age > int(body.object_max_age_ns):
            object_drop = 'stale_object_message'
        else:
            objects = _decode(db, recording, object_entry, 'objects')
            if objects['coordinate_frame'] == 'world':
                if objects['frame_id'] != body.world_frame:
                    raise ValueError('Object world frame differs from the selected world frame')
            else:
                if objects['frame_id']!='ego':
                    raise ValueError('Relative objects must use frame_id ego at the original ego reference origin')
                ref_entry, _ = select_message_at(session, recording.id, body.ego_channel_id,
                    object_at - int(body.ego_log_time_offset_ns))
                if ref_entry is None or int(ref_entry.log_time_key)+int(body.ego_log_time_offset_ns)!=object_at:
                    raise ValueError('Relative objects require the exact original world ego pose at aligned object observation time; no pose interpolation/extrapolation')
                reference = _decode(db, recording, ref_entry, 'ego')
                if reference['frame_id'] != body.world_frame:
                    raise ValueError('Object reference ego pose uses a different world frame')
                reference_source = _message(recording, ref_entry, 'planning-ego-json')
        sensors = []
        for sensor in body.sensor_channels:
            entry, next_entry = select_message_at(session, recording.id, sensor.channel_id,
                at-int(sensor.log_time_offset_ns))
            aligned = int(entry.log_time_key)+int(sensor.log_time_offset_ns) if entry else None
            if aligned is not None and not 0<=aligned<=2**64-1:
                raise ValueError('Aligned retained sensor timestamp exceeds uint64')
            age = at-aligned if aligned is not None else None
            if entry and entry.ordinal not in checked_sensor_messages:
                read_message_bytes(db,recording,entry)
                checked_sensor_messages.add(entry.ordinal)
            sensors.append({'selection': sensor.model_dump(mode='json'),
                'message': _message(recording, entry, sensor.decoder_id,sensor.decoder_version) if entry else None,
                'aligned_time_ns': str(aligned) if aligned is not None else None,
                'age_ns': str(age) if age is not None else None,
                'drop': 'no_causal_message' if entry is None else 'stale_message' if age > int(sensor.max_age_ns) else None,
                'next_aligned_time_ns': str(int(next_entry.log_time_key)+int(sensor.log_time_offset_ns)) if next_entry else None})
        frames.append({'index': index, 'at_ns': str(at), 'ego': ego, 'objects': objects,
            'object_reference_ego': reference, 'object_reference_ego_source': reference_source,
            'ego_source': _message(recording, ego_entry, 'planning-ego-json'),
            'object_source': _message(recording, object_entry, 'object-tracks-json') if object_entry else None,
            'object_age_ns': str(object_age) if object_age is not None else None,
            'next_object_aligned_time_ns': str(int(following.log_time_key)+int(body.object_log_time_offset_ns)) if following else None,
            'object_drop': object_drop, 'sensors': sensors})
    snapshot = {key: recording.payload[key] for key in ('id', 'sha256', 'size_bytes', 'source_kind', 'source', 'index')}
    snapshot['channels'] = [channel.model_dump(mode='json') for channel, _ in channel_defs]
    snapshot['schemas'] = [schema.model_dump(mode='json') if schema else None for _, schema in channel_defs]
    result = {'schema_version': snapshot_version, 'request': request, 'source_kind': recording.payload['source_kind'],
        'recording_snapshot': snapshot, 'binding_snapshot': binding.model_dump(mode='json') if binding else None,
        'frames': frames, 'limitations': limitations}
    if snapshot_version == 'scene-sequence/2':
        from .project_recordings import recording_use_scope
        snapshot['metadata'] = recording.payload.get('metadata', [])
        result['source_use_scope'] = recording_use_scope(recording.payload)
        if result['source_use_scope'].get('commercial_use') is False:
            result['limitations'].append('Original source restricts use to isolated noncommercial research; a project grant does not change source rights.')
    return result


def sequence_preflight(session, db, body):
    """Inspect exact inputs without publishing a sequence or silently fixing them."""
    body = SceneSequenceCreate.model_validate(body)
    summary = {'source_type': body.source_type, 'recording_id': body.recording_id,
        'clock_domain': body.clock_domain, 'world_frame': body.world_frame,
        'capture_clock_contract': 'v1_payload_timestamp_equals_native_mcap_log_timestamp',
        'declared_clock_offsets_ns': {'ego': body.ego_log_time_offset_ns, 'objects': body.object_log_time_offset_ns},
        'geometry_scope': 'declared_planar_geometry_not_calibrated_road_truth'}
    result = {'schema_version': 'scene-sequence-preflight/1', 'executable': False,
        'snapshot_sha256': None, 'input_summary': summary, 'checks': [], 'blocking_reasons': [],
        'warnings': [], 'frame_count': 0, 'frames': [], 'source_kind': 'unknown',
        'source_use_scope': {'use_scope': 'unknown', 'commercial_use': None},
        'supported_modes': [], 'next_actions': []}
    try:
        snapshot = prepare_sequence(session, db, body)
        if len(canonical_bytes(snapshot)) > MAX_SNAPSHOT_BYTES:
            raise ValueError('Temporal sequence snapshot exceeds 16 MiB')
    except (ValueError, TypeError, UnicodeError, HTTPException) as error:
        if isinstance(error, HTTPException) and error.status_code in {401, 403}:
            raise
        reason = str(getattr(error, 'detail', error))
        if 'capture timestamp' in reason:
            code, action = 'capture_clock_mismatch', 'Convert the source with an explicit capture-clock mapping; preserve original clocks and message identities.'
        elif 'schema' in reason or 'JSON' in reason:
            code, action = 'unsupported_channel_contract', 'Select exact supported ego/object channels or use a versioned source converter; topic names do not establish roles.'
        elif 'ordinal' in reason:
            code, action = 'missing_selected_observation', 'Select exact ego observations from the source sample picker.'
        elif 'gap' in reason or 'increasing' in reason or 'uint64' in reason:
            code, action = 'invalid_temporal_order', 'Review original timestamps, explicit offsets and selected interval; do not sort or fill missing observations silently.'
        elif 'pose' in reason or 'frame' in reason:
            code, action = 'unsupported_coordinate_mapping', 'Provide original world poses and an explicit coordinate conversion; relative tracks require the exact source ego pose.'
        elif 'identity' in reason or 'integrity' in reason or 'recording' in reason.lower():
            code, action = 'source_integrity_or_availability', 'Restore the original pinned source and inspect its integrity before execution.'
        else:
            code, action = 'invalid_input', 'Review the reported input contract error before freezing the sequence.'
        result['checks'].append({'id': code, 'status': 'blocked', 'reason': reason})
        result['blocking_reasons'].append(reason)
        result['next_actions'].append(action)
        return result
    frames = snapshot['frames']
    missing = sum(frame['object_drop'] is not None for frame in frames)
    sensor_drops = sum(sensor['drop'] is not None for frame in frames for sensor in frame['sensors'])
    record = snapshot['recording_snapshot']
    scope = snapshot.get('source_use_scope', result['source_use_scope'])
    result.update(executable=True, snapshot_sha256=digest(snapshot), source_kind=snapshot['source_kind'],
        source_use_scope=scope, frame_count=len(frames), warnings=list(snapshot['limitations']),
        supported_modes=['original_replay', 'open_loop'] + (['planning_closed_loop'] if len(frames) >= 2 and not missing else []),
        frames=[{key: frame[key] for key in ('index', 'at_ns', 'ego_source', 'object_source', 'object_drop', 'object_age_ns')} for frame in frames])
    summary.update(recording_sha256=record['sha256'] if record else None,
        source_use_scope=scope, object_steps_available=len(frames)-missing,
        object_steps_unevaluated=missing, sensor_message_drops=sensor_drops,
        missing_object_velocities=sum(obj['vx_mps'] is None or obj['vy_mps'] is None
            for frame in frames if frame['objects'] for obj in frame['objects']['objects']))
    result['checks'] = [
        {'id': 'original_source_identity', 'status': 'verified' if record else 'authored',
            'reason': 'Exact original MCAP and selected message/schema hashes verified.' if record else 'Explicit authored reference; no measured source identity.'},
        {'id': 'capture_clock_and_pose', 'status': 'verified', 'reason': 'Selected clocks, temporal ordering and declared pose/coordinate contracts validated.'},
        {'id': 'causal_object_observations', 'status': 'incomplete' if missing else 'available',
            'available': len(frames)-missing, 'unevaluated': missing,
            'reason': 'Missing/stale object observations stay missing; planning closed loop is unavailable.' if missing else 'Causal retained object observations exist for every selected step.'},
        {'id': 'additional_sensor_references', 'status': 'incomplete' if sensor_drops else 'available' if body.sensor_channels else 'not_selected',
            'dropped': sensor_drops, 'reason': 'Retained references are nonreactive and are not reference-controller inputs.'},
        {'id': 'source_rights', 'status': 'restricted' if scope.get('commercial_use') is False else 'unknown',
            'reason': 'Source permits isolated noncommercial research only.' if scope.get('commercial_use') is False else 'A project grant is access permission, not verification of commercial source rights.'},
    ]
    if missing:
        result['next_actions'].append('Select complete causal object observations for planning closed loop, or retain missing steps in original/open-loop investigation.')
    if summary['missing_object_velocities']:
        result['warnings'].append('Unknown object velocities remain null; corresponding TTC observations stay unevaluated.')
    return result


def create_scene_sequence(session, db, body, *, expected_snapshot_sha256=None):
    try:
        snapshot = prepare_sequence(session, db, body)
    except (ValueError, TypeError, UnicodeError) as error:
        raise HTTPException(422, str(error)) from error
    if len(canonical_bytes(snapshot)) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(422, 'Temporal sequence snapshot exceeds 16 MiB')
    if expected_snapshot_sha256 is not None and digest(snapshot) != expected_snapshot_sha256:
        raise HTTPException(409, 'Temporal input changed after preview; inspect the current source again before freezing')
    detail = SceneSequenceDetail(id=uid('seq'), project_id=body.project_id, name=body.name, created_at=utcnow(),
        sha256=digest(snapshot), source_type=body.source_type, source_kind=snapshot['source_kind'],
        frame_count=len(snapshot['frames']), snapshot=snapshot, limitations=snapshot['limitations'])
    session.add(SceneSequenceRow(id=detail.id, project_id=detail.project_id, created_at=detail.created_at,
        sha256=detail.sha256, payload=detail.model_dump(mode='json')))
    session.flush()
    return detail


def get_scene_sequence(session, identifier):
    row = session.get(SceneSequenceRow, identifier)
    if row is None:
        raise HTTPException(404, 'Scene sequence not found')
    detail = SceneSequenceDetail.model_validate(row.payload)
    if row.sha256 != detail.sha256 or digest(detail.snapshot) != detail.sha256 or row.project_id != detail.project_id or row.id != detail.id:
        raise HTTPException(409, 'Scene sequence integrity check failed')
    return detail


def validate_retained_sequence(session, db, detail):
    """Re-read/hash only exact retained messages, then rebuild the frozen contract."""
    fresh = prepare_sequence(session, db, SceneSequenceCreate.model_validate(detail.snapshot['request']),
        snapshot_version=detail.snapshot['schema_version'])
    if digest(fresh) != detail.sha256 or fresh != detail.snapshot:
        raise ValueError('Pinned recording/schema/binding/message identities or retained bytes changed')
    return fresh
