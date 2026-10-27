"""Fenced, independently persisted stack ticks over pinned recorded observations."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import func, select

from . import jobs
from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch, AdapterOwnershipLost
from .artifact_transactions import track_created_artifact
from .job_models import JobEventRow, JobRow
from .job_schemas import JobStatus
from .sensor_schemas import BindingDetail, BindingSnapshot
from .service import canonical_bytes, digest, uid, utcnow
from .stack_replay_models import StackReplayRow, StackReplayTickRow
from .stack_replay_schemas import (StackReplayCreate, StackReplayInput, StackReplayTick, StackReplayPoints,
    StackReplayCounts, StackReplaySummary, StackReplayDetail, StackReplayPage, StackReplaySources)
from .stack_replay_sources import prepare_sources, discover_sources
from .stacks import get_stack, validate_stack_snapshot
from .stack_schemas import validate_stack_definition, validate_stack_output, stack_kernel_version

MANIFEST_LIMIT = 32 * 1024 * 1024
PREPARED_LIMIT = 4 * 1024 * 1024
TICK_LIMIT = 128 * 1024
JOB_POINTS_LIMIT = 128 * 1024 * 1024
RUNTIME_ID, RUNTIME_VERSION = 'recorded-stack', '1'
MANIFEST_FIELDS = {'manifest_hash', 'manifest_size_bytes', 'download_url'}


@dataclass(frozen=True)
class PreparedStackReplay:
    payload_json: bytes

    def to_payload(self):
        return json.loads(self.payload_json)

    @property
    def input_hash(self):
        return hashlib.sha256(self.payload_json).hexdigest()

    @classmethod
    def from_payload(cls, value):
        if set(value) != {'request', 'stack_snapshot', 'recording_snapshot', 'binding_snapshot', 'inputs', 'kernel_version'}:
            raise ValueError('Unknown prepared stack replay contract')
        body = StackReplayCreate.model_validate(value['request'])
        stack = validate_stack_snapshot(value['stack_snapshot'])
        binding = BindingDetail.model_validate(value['binding_snapshot'])
        snapshot = BindingSnapshot.model_validate(binding.model_dump(mode='json', exclude=MANIFEST_FIELDS))
        content = canonical_bytes(snapshot.model_dump(mode='json'))
        if (binding.manifest_size_bytes != len(content) or binding.manifest_hash != hashlib.sha256(content).hexdigest()
                or binding.recording_id != body.recording_id or binding.revision != body.binding_revision
                or binding.recording_sha256 != value['recording_snapshot']['sha256']
                or binding.rig_snapshot.reference_frame != stack.definition.reference_frame
                or stack.id != body.stack_id or stack.revision != body.stack_revision
                or value['recording_snapshot']['id'] != body.recording_id):
            raise ValueError('Stack/recording/binding snapshots differ from the selected revisions')
        mapping = next((item for item in binding.channels if item.channel_id == body.lidar_channel_id), None)
        if mapping is None:
            raise ValueError('The pinned binding is missing its LiDAR channel')
        inputs = [StackReplayInput.model_validate(item) for item in value['inputs']]
        if (len(inputs) != len(body.ego_ordinals)
                or value['kernel_version'] != stack.kernel_version
                or value['kernel_version'] != stack_kernel_version(stack.definition)):
            raise ValueError('Pinned stack replay denominator/kernel identity is invalid')
        previous = None
        for index, item in enumerate(inputs):
            if (item.index != index or item.ego.ordinal != body.ego_ordinals[index]
                    or item.ego.channel_id != body.ego_channel_id or item.ego.decoder_id != body.ego_decoder_id
                    or item.ego.decoder_version != body.ego_decoder_version
                    or int(item.at_ns) != int(item.ego.log_time_ns) + int(body.ego_log_time_offset_ns)
                    or previous is not None and int(item.at_ns) <= previous):
                raise ValueError('Pinned ego selection differs from its request/time ordering')
            previous = int(item.at_ns)
            for message in (item.ego, item.lidar):
                if message and (message.recording_id != body.recording_id or message.recording_sha256 != binding.recording_sha256):
                    raise ValueError('Pinned source message belongs to a different recording')
            if item.lidar:
                aligned = int(item.lidar.log_time_ns) + int(mapping.log_time_offset_ns)
                if (item.lidar.channel_id != body.lidar_channel_id or item.lidar.decoder_id != mapping.decoder_id
                        or item.lidar.decoder_version != mapping.decoder_version or item.lidar_aligned_time_ns != str(aligned)
                        or aligned > int(item.at_ns) or item.lidar_age_ns != str(int(item.at_ns)-aligned)):
                    raise ValueError('Pinned LiDAR selection differs from its declared clock/role')
            elif item.lidar_age_ns is not None or item.lidar_aligned_time_ns is not None:
                raise ValueError('Missing LiDAR cannot acquire an aligned observation time')
        content = canonical_bytes(value)
        if len(content) > PREPARED_LIMIT:
            raise ValueError('Stack replay input snapshot exceeds 4 MiB')
        return cls(content)


def _subject(session, row):
    subject = session.get(StackReplayRow, row.id)
    if row.kind != 'stack_replay' or subject is None:
        raise HTTPException(404, 'Stack replay not found')
    return subject


def _prepared(row):
    try:
        prepared = PreparedStackReplay.from_payload(row.prepared)
        value = prepared.to_payload()
        if (prepared.input_hash != row.input_hash or value['request'] != row.request or digest(row.request) != row.request_hash
                or row.adapter_id != RUNTIME_ID or row.adapter_version != RUNTIME_VERSION):
            raise ValueError('Queue request differs from the prepared replay')
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Stack replay input snapshot failed its integrity check') from error


def _points_url(identifier, index):
    return f'/api/v1/stack-replays/{identifier}/ticks/{index}/points'


def _tick_hash(tick):
    value = tick.model_dump(mode='json', exclude={'output_hash', 'started_at', 'finished_at'})
    if value['points'] is not None:
        value['points'].pop('download_url')
    return digest(value)


def _validated_tick_output(tick, prepared):
    from .stack_schemas import StackPacket
    packet = StackPacket.model_validate(tick.packet) if tick.packet is not None else None
    if packet is not None and (packet.sequence != tick.index or packet.at_ns != tick.input.at_ns):
        raise ValueError('Kernel packet does not match its pinned tick')
    if tick.output is not None:
        output = validate_stack_output(tick.output)
        expected = prepared['stack_snapshot']
        if (packet is None or output.sequence != tick.index or output.at_ns != tick.input.at_ns
                or output.kernel_version != prepared['kernel_version']
                or output.kernel_version != expected['kernel_version']
                or output.input_points_sha256 != (packet.lidar.points_sha256 if packet.lidar else None)
                or [value.model_dump(mode='json') for value in output.components] != expected['component_metadata']):
            raise ValueError('Kernel output identity/components/consumed hash differ from its pinned input')
        return packet, output
    return packet, None


def _validate_state_prefix(tick, previous):
    if tick.output is None or tick.output['kernel_version'] != 'stack-kernel/2':
        return
    before, after = tick.output['state_before'], tick.output['state_after']
    if tick.index == 0:
        from .stack_v2_components import initial_state
        if before != initial_state().model_dump(mode='json'):
            raise ValueError('First stateful stack tick must start a fresh session')
    elif (previous is None or previous.status != 'completed' or previous.output is None
            or previous.output['kernel_version'] != 'stack-kernel/2'
            or before != previous.output['state_after']):
        raise ValueError('Stateful stack output is missing its exact completed state prefix')
    if after['last_sequence'] != tick.index or after['last_at_ns'] != tick.input.at_ns:
        raise ValueError('Stateful stack output state does not identify its current tick')


def _ticks(session, row, prepared):
    entries = list(session.scalars(select(StackReplayTickRow).where(StackReplayTickRow.job_id == row.id).order_by(StackReplayTickRow.index)))
    try:
        if len(entries) != len(prepared['inputs']):
            raise ValueError('Stored ticks omit selected inputs')
        values = []
        for index, entry in enumerate(entries):
            tick = StackReplayTick.model_validate(entry.payload)
            if (entry.payload_hash != digest(entry.payload) or entry.index != index or tick.index != index
                    or tick.input.model_dump(mode='json') != prepared['inputs'][index]
                    or entry.recording_id != tick.input.ego.recording_id or entry.ego_ordinal != tick.input.ego.ordinal
                    or entry.lidar_ordinal != (tick.input.lidar.ordinal if tick.input.lidar else None)):
                raise ValueError('Tick input identity differs from its pinned source')
            if tick.status in {'not_attempted', 'running'}:
                if (tick.packet is not None or tick.output is not None or tick.points is not None or tick.output_hash
                        or tick.finished_at or tick.error or (tick.status == 'not_attempted') != (tick.started_at is None)):
                    raise ValueError('Unfinished tick contains output evidence')
            else:
                if not tick.started_at or not tick.finished_at or tick.output_hash != _tick_hash(tick):
                    raise ValueError('Tick output differs from its hash/lifecycle')
                if tick.status == 'completed' and (tick.packet is None or tick.output is None or tick.error):
                    raise ValueError('Completed tick lacks kernel input/output evidence')
                if tick.status == 'failed' and (not tick.error or tick.output is not None):
                    raise ValueError('Failed tick must explain absent kernel output')
            if (entry.points_storage_key is None) != (tick.points is None):
                raise ValueError('Tick point bytes/descriptor relationship is inconsistent')
            if tick.points and tick.points.download_url != _points_url(row.id, index):
                raise ValueError('Tick point download identity is inconsistent')
            _validated_tick_output(tick, prepared)
            _validate_state_prefix(tick, values[-1] if values else None)
            values.append(tick)
        return values
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Stack tick evidence failed its integrity check') from error


def _unsealed_detail(session, row):
    subject, prepared = _subject(session, row), _prepared(row)
    body = prepared['request']
    if any(getattr(subject, name) != body[name] for name in ('stack_id', 'stack_revision', 'recording_id', 'binding_revision', 'lidar_channel_id', 'ego_channel_id')):
        raise HTTPException(409, 'Stack replay subject relationships differ from pinned inputs')
    ticks = _ticks(session, row, prepared)
    counts = StackReplayCounts(total=len(ticks), **{name: sum(tick.status == name for tick in ticks) for name in ('running', 'completed', 'failed', 'not_attempted')})
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == row.id).order_by(JobEventRow.sequence.desc()).limit(jobs.EVENT_LIMIT)))
    events_total = session.scalar(select(func.count()).select_from(JobEventRow).where(JobEventRow.job_id == row.id))
    common = {name: getattr(row, name) for name in StackReplaySummary.model_fields
        if name not in {'label', 'stack_id', 'stack_revision', 'stack_name', 'recording_id', 'binding_revision', 'mode', 'counts'}}
    return StackReplayDetail(**common, label=body['label'], stack_id=body['stack_id'], stack_revision=body['stack_revision'],
        stack_name=prepared['stack_snapshot']['name'], recording_id=body['recording_id'], binding_revision=body['binding_revision'], counts=counts,
        request=body, stack_snapshot=prepared['stack_snapshot'], recording_snapshot=prepared['recording_snapshot'], binding_snapshot=prepared['binding_snapshot'],
        inputs=prepared['inputs'], ticks=ticks, events=[{'at': item.at, 'type': item.type, 'message': item.message} for item in reversed(events)], events_total=events_total)


def seal_terminal(session, row):
    session.flush()
    subject = _subject(session, row)
    if row.status not in jobs.TERMINAL or subject.manifest is not None:
        raise HTTPException(409, 'Only an unsealed terminal stack replay can be sealed')
    prepared = _prepared(row)
    for tick in _ticks(session, row, prepared):
        if tick.status == 'running':
            tick.status, tick.finished_at = 'failed', row.finished_at
            tick.error = f'Job {row.status} during this tick; no completed kernel output was published. ' + (row.error or '')
            tick.output_hash = _tick_hash(tick)
            entry = session.get(StackReplayTickRow, (row.id, tick.index))
            entry.payload, entry.payload_hash = tick.model_dump(mode='json'), digest(tick.model_dump(mode='json'))
            jobs._event(session, row, 'tick_failed', f'Started tick {tick.index} ended without output ({row.status}).', row.finished_at)
    session.flush()
    content = canonical_bytes(_unsealed_detail(session, row).model_dump(mode='json', exclude=MANIFEST_FIELDS))
    if len(content) > MANIFEST_LIMIT:
        raise HTTPException(413, 'Stack replay terminal manifest exceeds 32 MiB')
    subject.manifest, subject.manifest_hash, subject.manifest_size_bytes = content, hashlib.sha256(content).hexdigest(), len(content)


def detail(session, row):
    session.flush()
    value, subject = _unsealed_detail(session, row), _subject(session, row)
    if row.status in jobs.TERMINAL:
        content = canonical_bytes(value.model_dump(mode='json', exclude=MANIFEST_FIELDS))
        if (content != subject.manifest or len(content) != subject.manifest_size_bytes or hashlib.sha256(content).hexdigest() != subject.manifest_hash):
            raise HTTPException(409, 'Terminal stack replay manifest failed its integrity check')
        return value.model_copy(update={'manifest_hash': subject.manifest_hash, 'manifest_size_bytes': subject.manifest_size_bytes,
            'download_url': f'/api/v1/stack-replays/{row.id}/export'})
    if subject.manifest is not None or subject.manifest_hash is not None or subject.manifest_size_bytes is not None:
        raise HTTPException(409, 'A nonterminal replay contains sealed evidence')
    return value


def submit(db, registry, decoders, body, key):
    with jobs._write(db) as session:
        return submit_in_session(session, registry, decoders, body, key)


def submit_in_session(session, registry, decoders, body, key):
    """Publish within a caller transaction; no independent commit or execution."""
    if not key or len(key) > 200:
        raise HTTPException(422, 'A stable Idempotency-Key of at most 200 characters is required')
    request, storage_key = body.model_dump(mode='json'), 'stack-replay:' + key

    existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == storage_key))
    if existing:
        if existing.kind != 'stack_replay' or existing.request_hash != digest(request):
            raise HTTPException(409, 'Idempotency-Key already identifies another stack replay request')
        return detail(session, existing)
    stack = get_stack(session, body.stack_id, body.stack_revision)
    try:
        metadata = registry.validate_definition(stack.definition)
        if [item.model_dump(mode='json') for item in metadata] != [item.model_dump(mode='json') for item in stack.component_metadata]:
            raise ValueError('Pinned stack component metadata differs from the installed versions')
        sources = prepare_sources(session, decoders, body, stack.model_dump(mode='json'))
        prepared = PreparedStackReplay.from_payload({'request': request, 'stack_snapshot': stack.model_dump(mode='json'), 'kernel_version': stack.kernel_version, **sources})
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    now = utcnow()
    row = JobRow(id=uid('stack_replay'), kind='stack_replay', idempotency_key=storage_key, request_hash=digest(request),
        request=request, prepared=prepared.to_payload(), input_hash=prepared.input_hash, adapter_id=RUNTIME_ID, adapter_version=RUNTIME_VERSION,
        status='queued', created_at=now, updated_at=now, cancellation_supported=True, resource_required=False,
        outcome_uncertain=False, evidence_eligible=False)
    session.add(row)
    session.flush()
    session.add(StackReplayRow(job_id=row.id, **{name: request[name] for name in ('stack_id', 'stack_revision', 'recording_id', 'binding_revision', 'lidar_channel_id', 'ego_channel_id')}))
    session.flush()
    for value in sources['inputs']:
        item = StackReplayInput.model_validate(value)
        tick = StackReplayTick(index=item.index, status='not_attempted', input=item)
        payload = tick.model_dump(mode='json')
        session.add(StackReplayTickRow(job_id=row.id, index=item.index, recording_id=body.recording_id,
            ego_ordinal=item.ego.ordinal, lidar_ordinal=item.lidar.ordinal if item.lidar else None, payload=payload, payload_hash=digest(payload)))
    jobs._event(session, row, 'queued', 'Pinned stack, binding, original ego and predecessor LiDAR inputs; commands will not be applied to a vehicle.', now)
    return detail(session, row)


def start_tick(db, claim, index):
    with jobs._write(db) as session:
        row = jobs._live(session, claim)
        _prepared(row)
        entry = session.get(StackReplayTickRow, (row.id, index))
        if row.kind != 'stack_replay' or row.dispatched_at is None or entry is None or entry.payload_hash != digest(entry.payload) or entry.payload['status'] != 'not_attempted':
            raise HTTPException(409, 'Tick is not a new input in this dispatched replay')
        tick = StackReplayTick.model_validate(entry.payload)
        tick.status, tick.started_at = 'running', utcnow()
        entry.payload, entry.payload_hash = tick.model_dump(mode='json'), digest(tick.model_dump(mode='json'))
        row.updated_at = tick.started_at
        jobs._event(session, row, 'tick_started', f'Stack input tick {index} started.', tick.started_at)
        return tick.started_at


def publish_tick(db, claim, tick, points):
    packet, output = _validated_tick_output(tick, claim.prepared.to_payload())
    if output is not None:
        tick.output = output.model_dump(mode='json')
    if tick.status == 'completed' and (packet is None or tick.output is None or tick.error):
        raise ValueError('A completed tick requires validated kernel packet/output')
    if points is not None:
        if packet is None or packet.lidar is None:
            raise ValueError('Consumed point bytes need a LiDAR packet descriptor')
        desc = packet.lidar
        if (len(points) != desc.size_bytes or len(points) != desc.point_count * 12 or len(points) > 20_000 * 12
                or hashlib.sha256(points).hexdigest() != desc.points_sha256
                or any(not math.isfinite(value) for point in struct.iter_unpack('<fff', points) for value in point)):
            raise ValueError('Consumed point bytes differ from the kernel packet')
        tick.points = StackReplayPoints(sha256=desc.points_sha256, size_bytes=len(points), point_count=desc.point_count,
            frame_id=desc.frame_id, download_url=_points_url(claim.job.id, tick.index))
    elif packet is not None and packet.lidar is not None:
        raise ValueError('Available LiDAR packet must preserve exact consumed bytes')
    if output is not None and output.kernel_version == 'stack-kernel/2':
        from .stack_v2_runtime import validate_v2_source_evidence
        validate_v2_source_evidence(claim.prepared.to_payload()['stack_snapshot']['definition'],
            packet, points, output)
    if len(canonical_bytes(tick.model_dump(mode='json'))) > TICK_LIMIT:
        raise ValueError('Stack tick evidence exceeds 128 KiB')
    staged = None
    try:
        if points is not None:
            fd, name = tempfile.mkstemp(prefix='points_', suffix='.xyz', dir=db.stack_replay_staging_dir)
            staged = Path(name)
            with os.fdopen(fd, 'wb') as stream:
                if stream.write(points) != len(points):
                    raise OSError('Incomplete staged point-buffer write')
        with jobs._write(db) as session:
            row = jobs._live(session, claim)
            if row.kind != 'stack_replay' or row.dispatched_at is None or row.input_hash != claim.prepared.input_hash:
                raise ValueError('Tick publication requires its exact live dispatched claim')
            prepared = _prepared(row)
            entry = session.get(StackReplayTickRow, (row.id, tick.index))
            if (entry is None or entry.payload_hash != digest(entry.payload) or entry.payload['status'] != 'running'
                    or tick.input.model_dump(mode='json') != prepared['inputs'][tick.index]):
                raise HTTPException(409, 'Tick is already published or does not match its pinned input')
            previous = session.get(StackReplayTickRow, (row.id, tick.index - 1)) if tick.index else None
            if previous is not None and previous.payload_hash != digest(previous.payload):
                raise ValueError('Stateful stack prefix failed its integrity check')
            _validate_state_prefix(tick, StackReplayTick.model_validate(previous.payload) if previous else None)
            tick.started_at, tick.finished_at = entry.payload['started_at'], utcnow()
            tick.output_hash = _tick_hash(tick)
            if staged is not None:
                saved = sum(item.payload['points']['size_bytes'] for item in session.scalars(select(StackReplayTickRow).where(StackReplayTickRow.job_id == row.id)) if item.payload.get('points'))
                if saved + len(points) > JOB_POINTS_LIMIT:
                    raise ValueError('Replay consumed point buffers exceed 128 MiB')
                key = uid('points') + '.xyz'
                destination = db.stack_replay_dir / key
                destination.hardlink_to(staged)
                track_created_artifact(session, destination)
                entry.points_storage_key = key
            entry.payload, entry.payload_hash = tick.model_dump(mode='json'), digest(tick.model_dump(mode='json'))
            row.updated_at = tick.finished_at
            jobs._event(session, row, 'tick_' + tick.status, f'Stack tick {tick.index} {tick.status}; no vehicle control was applied.', tick.finished_at)
            session.flush()
            jobs._live(session, claim)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def execute_claim(db, claim, registry, decoders, should_cancel):
    from .stack_runtime import StackSession
    from .stack_replay_sources import load_packet
    prepared = claim.prepared.to_payload()
    if should_cancel():
        raise AdapterCancelledBeforeDispatch()
    definition = validate_stack_definition(prepared['stack_snapshot']['definition'])
    metadata = registry.validate_definition(definition)
    if (prepared['kernel_version'] != stack_kernel_version(definition)
            or prepared['kernel_version'] != prepared['stack_snapshot']['kernel_version']
            or [item.model_dump(mode='json') for item in metadata] != prepared['stack_snapshot']['component_metadata']):
        raise HTTPException(422, 'Pinned kernel/components are not available; no stack tick was executed')
    engine = StackSession(definition, registry=registry)
    for value in prepared['inputs']:
        if should_cancel():
            raise AdapterCancelled()
        item = StackReplayInput.model_validate(value)
        started = start_tick(db, claim, item.index)
        tick = StackReplayTick(index=item.index, status='failed', input=item, started_at=started)
        points = None
        try:
            packet, points = load_packet(db, decoders, prepared, item)
            tick.packet = packet.model_dump(mode='json')
            output = engine.step(packet, points_bytes=points)
            tick.output, tick.status = output.model_dump(mode='json'), 'completed'
        except (AdapterCancelled, AdapterOwnershipLost):
            raise
        except Exception as error:
            tick.error = f'{type(error).__name__}: {str(error)[:1500]}'
        publish_tick(db, claim, tick, points)
        if prepared['kernel_version'] == 'stack-kernel/2' and tick.status == 'failed':
            raise RuntimeError('Stateful stack tick failed; later selected inputs remain not attempted because the required state prefix is unavailable')
    with jobs._write(db) as session:
        row = jobs._live(session, claim)
        value = _unsealed_detail(session, row)
        if value.counts.not_attempted or value.counts.running:
            raise ValueError('Cannot finish while selected ticks remain unattempted')
        if row.cancel_requested_at:
            jobs._event(session, row, 'cancel_not_honoured', 'All tick outcomes arrived before cancellation acknowledgement; evidence retained.', utcnow())
        row.error = f'{value.counts.failed} ticks failed; inspect individual errors.' if value.counts.failed else None
        jobs._terminal(session, row, 'failed' if value.counts.failed else 'completed', utcnow(), 'Every selected open-loop tick outcome is stored. Commands did not alter the recording.')
        return detail(session, row)


def points_bytes(session, db, identifier, index):
    value = detail(session, jobs._row(session, identifier))
    if not 0 <= index < len(value.ticks) or value.ticks[index].points is None:
        raise HTTPException(404, 'This tick has no saved consumed point buffer')
    entry = session.get(StackReplayTickRow, (identifier, index))
    desc = value.ticks[index].points
    try:
        if not re.fullmatch(r'points_[0-9a-f]{32}\.xyz', entry.points_storage_key):
            raise ValueError('Invalid managed point key')
        fd = os.open(db.stack_replay_dir / entry.points_storage_key, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != desc.size_bytes or not 0 <= info.st_size <= 20_000*12:
                raise ValueError('Point file type/size mismatch')
            content = stream.read(20_000*12+1)
        if len(content) != desc.size_bytes or len(content) != desc.point_count*12 or hashlib.sha256(content).hexdigest() != desc.sha256:
            raise ValueError('Point content hash mismatch')
        return content
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(409, 'Consumed point buffer failed its integrity check') from error


def register_stack_replay_routes(api, db, registry, decoders):
    @api.get('/stack-replay-sources/{recording_id}', response_model=StackReplaySources, tags=['stack replay'])
    def sources(recording_id: str):
        with db.read_session() as session:
            return discover_sources(session, decoders, recording_id)

    @api.post('/stack-replays', response_model=StackReplayDetail, status_code=202, tags=['stack replay'])
    def create(body: StackReplayCreate, idempotency_key: str = Header(min_length=1, max_length=200)):
        return submit(db, registry, decoders, body, idempotency_key)

    @api.get('/stack-replays', response_model=StackReplayPage, tags=['stack replay'])
    def list_replays(status: JobStatus | None = None, limit: int = Query(100, ge=1, le=100), before_seq: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            query = select(JobRow).where(JobRow.kind == 'stack_replay').order_by(JobRow.submission_seq.desc())
            if status is not None:
                query = query.where(JobRow.status == status)
            if before_seq is not None:
                query = query.where(JobRow.submission_seq < before_seq)
            rows = list(session.scalars(query.limit(limit+1)))
            return StackReplayPage(items=[StackReplaySummary.model_validate(detail(session, row).model_dump(include=set(StackReplaySummary.model_fields))) for row in rows[:limit]],
                next_before_seq=rows[limit-1].submission_seq if len(rows)>limit else None)

    @api.get('/stack-replays/{replay_id}', response_model=StackReplayDetail, tags=['stack replay'])
    def get(replay_id: str):
        return jobs.job_get(db, replay_id, kind='stack_replay')

    @api.post('/stack-replays/{replay_id}/cancel', response_model=StackReplayDetail, tags=['stack replay'])
    def cancel(replay_id: str):
        return jobs.cancel_job(db, replay_id, kind='stack_replay')

    @api.get('/stack-replays/{replay_id}/export', tags=['stack replay'])
    def export(replay_id: str):
        with db.read_session() as session:
            row = jobs._row(session, replay_id)
            value = detail(session, row)
            if value.status not in jobs.TERMINAL:
                raise HTTPException(409, 'Replay export requires a terminal outcome')
            return Response(_subject(session, row).manifest, media_type='application/json',
                headers={'Content-Disposition': f'attachment; filename="{value.id}.stack-replay.json"'})

    @api.get('/stack-replays/{replay_id}/ticks/{index}/points', tags=['stack replay'])
    def points(replay_id: str, index: int):
        with db.read_session() as session:
            content = points_bytes(session, db, replay_id, index)
        return Response(content, media_type='application/octet-stream', headers={'Content-Disposition': f'attachment; filename="{replay_id}-{index}.xyz"'})
