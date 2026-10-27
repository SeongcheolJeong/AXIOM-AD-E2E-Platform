"""Case-linked, full-prefix recorded stack comparisons; no vehicle actuation.

Reuse the shared stack replay queue. Exact ego/LiDAR inputs and binding stay
fixed; a saved stack revision is the only execution variable. Unknown inputs
remain unknown and completion is never a driving-performance verdict.
"""
from copy import deepcopy
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import select

from . import jobs, stack_replays
from .base_schemas import StrictModel
from .case_models import CaseRow, CaseRevisionRow
from .case_schemas import Identity
from .cases import _get, _publish, _sealed
from .recording_models import RecordingMessageRow
from .recordings import _get_recording, read_message_bytes
from .service import canonical_bytes, digest, uid, utcnow
from .stack_replay_schemas import StackReplayCreate
from .stack_replay_sources import message_input, prepare_sources
from .stacks import get_stack

VERSION = 'case-stack-replay/1'
SCOPE = 'Recorded ego/LiDAR open-loop requests only; no changed motion, sensor conditions or driving verdict.'
MAX_ATTEMPTS = 8
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024


class StackReplaySelection(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    baseline_replay_id: Identity
    stack_id: Identity
    stack_revision: int = Field(ge=1, strict=True)
    note: str = Field(min_length=1, max_length=1500)

    @field_validator('note')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Describe the purpose of this recorded stack comparison')
        return value.strip()


class StackReplaySubmit(StackReplaySelection):
    expected_preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_open_loop: bool = Field(strict=True)

    @field_validator('confirm_open_loop')
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError('Explicitly acknowledge full-prefix compute without vehicle feedback')
        return value


class StackReplayCapture(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def _task(session, identifier):
    row = jobs._row(session, identifier)
    if row.kind != 'stack_replay':
        raise HTTPException(422, 'Select a recorded stack replay, not another job kind')
    return stack_replays.detail(session, row)


def _matches_source(case, baseline):
    """Every pinned case message must occur as an exact replay input."""
    source = case['segment']
    selected = {(m.channel_id, m.ordinal): m for i in baseline.inputs for m in (i.ego, i.lidar) if m}
    if (source['recording_id'] != baseline.recording_id
            or source['recording_sha256'] != baseline.recording_snapshot['sha256']):
        raise HTTPException(422, 'Baseline replay belongs to a different recording')
    for sample in source['samples']:
        message = selected.get((source['channel_id'], sample['ordinal']))
        if (message is None or message.message_sha256 != sample['sha256']
                or message.log_time_ns != sample['log_time_ns']
                or message.publish_time_ns != sample['publish_time_ns']
                or message.message_size_bytes != sample['size_bytes']):
            raise HTTPException(422, 'Every case message must be an exact ego or LiDAR input of the baseline; no time fallback')


def _new(prior):
    value = deepcopy({k: v for k, v in prior.items() if k not in ('manifest_hash', 'manifest_size_bytes', 'download_url')})
    value.update(schema_version='axiom-investigation-case/5', revision=prior['revision'] + 1,
                 created_at=utcnow(), previous_manifest_hash=prior['manifest_hash'])
    value.setdefault('stack_replays', [])
    return value


def _current(session, case_id, expected_revision):
    case = _get(session, case_id)
    if case['revision'] != expected_revision:
        raise HTTPException(409, 'Case changed; reload before preview or submission')
    return case


def _retry(session, case_id, operation, body, key):
    storage = 'case-stack:' + digest({'case_id': case_id, 'operation': operation, 'key': key})
    request_hash = digest({'case_id': case_id, 'operation': operation, 'body': body.model_dump(mode='json')})
    old = session.scalar(select(CaseRevisionRow).where(CaseRevisionRow.request_key == storage))
    if old and (old.case_id != case_id or old.request_hash != request_hash):
        raise HTTPException(409, 'Idempotency-Key identifies a different stack replay request')
    return (_get(session, case_id, old.revision) if old else None), storage, request_hash


def _proposal(db, session, registry, decoders, case, body):
    baseline = _task(session, body.baseline_replay_id)
    if (baseline.status != 'completed' or not baseline.manifest_hash
            or baseline.counts.completed != baseline.counts.total):
        raise HTTPException(422, 'Baseline must be a sealed, completed full stack replay')
    _matches_source(case, baseline)
    stack = get_stack(session, body.stack_id, body.stack_revision)
    if stack.kernel_version != baseline.stack_snapshot['kernel_version']:
        raise HTTPException(422, 'This comparison requires the same explicit stack kernel contract')
    try:
        installed = registry.validate_definition(stack.definition)
        if [x.model_dump(mode='json') for x in installed] != [x.model_dump(mode='json') for x in stack.component_metadata]:
            raise ValueError('Installed stack components differ from their pinned declarations')
        request = StackReplayCreate.model_validate({**baseline.request.model_dump(mode='json'),
            'label': ('Case stack replay: ' + case['title'])[:180],
            'stack_id': stack.id, 'stack_revision': stack.revision})
        sources = prepare_sources(session, decoders, request, stack.model_dump(mode='json'))
        prepared = stack_replays.PreparedStackReplay.from_payload({'request': request.model_dump(mode='json'),
            'stack_snapshot': stack.model_dump(mode='json'), 'kernel_version': stack.kernel_version, **sources})
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)[:1000]) from error
    if (sources['inputs'] != [i.model_dump(mode='json') for i in baseline.inputs]
            or sources['binding_snapshot'] != baseline.binding_snapshot
            or sources['recording_snapshot'] != baseline.recording_snapshot):
        raise HTTPException(409, 'Current temporal selection or binding differs from the baseline')
    original = _get_recording(session, baseline.recording_id)
    unique = {(m.channel_id, m.ordinal): m for i in baseline.inputs for m in (i.ego, i.lidar) if m}
    if sum(m.message_size_bytes for m in unique.values()) > MAX_SOURCE_BYTES:
        raise HTTPException(413, 'Full-prefix source payload exceeds 128 MiB; no truncation is performed')
    for message in unique.values():
        entry = session.get(RecordingMessageRow, (baseline.recording_id, message.ordinal))
        if entry is None or message_input(original, entry, message.decoder_id, message.decoder_version) != message:
            raise HTTPException(409, 'Pinned source message index changed')
        read_message_bytes(db, original, entry)
    case_samples = {(case['segment']['channel_id'], s['ordinal']) for s in case['segment']['samples']}
    ticks = [i.index for i in baseline.inputs if any(m and (m.channel_id, m.ordinal) in case_samples for m in (i.ego, i.lidar))]
    return {'schema_version': VERSION, 'scope': SCOPE, 'source': {'case_id': case['id'],
        'case_revision': case['revision'], 'case_manifest_hash': case['manifest_hash'], 'segment_hash': case['segment_hash']},
        'source_conditions': case.get('conditions'), 'note': body.note,
        'baseline_id': baseline.id, 'baseline_manifest_hash': baseline.manifest_hash,
        'prepared': prepared.to_payload(), 'prepared_hash': prepared.input_hash,
        'workload': {'total_ticks': len(baseline.inputs), 'case_tick_indices': ticks,
            'unique_source_messages': len(unique), 'source_bytes': sum(m.message_size_bytes for m in unique.values()),
            'state_policy': 'Reset once at the beginning of the entire pinned baseline; replay every prefix tick.'}}


def preview(db, registry, decoders, case_id, body):
    with db.read_session() as session:
        proposal = _proposal(db, session, registry, decoders, _current(session, case_id, body.expected_revision), body)
        return {**proposal, 'preview_hash': digest(proposal), 'jobs_created': 0}


def submit(db, registry, decoders, case_id, body, key):
    with jobs._write(db) as session:
        retry, storage, request_hash = _retry(session, case_id, 'submit', body, key)
        if retry:
            return retry
        prior = _current(session, case_id, body.expected_revision)
        if len(prior.get('stack_replays', [])) >= MAX_ATTEMPTS:
            raise HTTPException(413, 'Case supports at most eight recorded stack attempts')
        proposal = _proposal(db, session, registry, decoders, prior, body)
        if digest(proposal) != body.expected_preview_hash:
            raise HTTPException(409, 'Preview changed; inspect the complete source workload again')
        task = stack_replays.submit_in_session(session, registry, decoders,
            StackReplayCreate.model_validate(proposal['prepared']['request']), storage)
        if task.input_hash != proposal['prepared_hash']:
            raise HTTPException(409, 'Prepared stack input changed during submission')
        value = _new(prior)
        value['stack_replays'].append({'id': uid('csreplay'), 'definition': proposal,
            'definition_hash': digest(proposal), 'job_id': task.id, 'input_hash': task.input_hash, 'capture': None})
        return _publish(session, session.get(CaseRow, case_id), value, storage, request_hash)


def comparison(baseline, candidate):
    """Compare output requests only; do not label braking changes as improvement."""
    reasons = []
    if (baseline.inputs != candidate.inputs or baseline.binding_snapshot != candidate.binding_snapshot
            or baseline.recording_snapshot != candidate.recording_snapshot
            or baseline.stack_snapshot['kernel_version'] != candidate.stack_snapshot['kernel_version']):
        reasons.append('Pinned temporal inputs, binding or kernel contract differ')
    complete = all(t.status == 'completed' and t.output is not None for t in [*baseline.ticks, *candidate.ticks])
    complete = complete and baseline.status == candidate.status == 'completed' and len(baseline.ticks) == len(candidate.ticks)
    left = {t.index: t for t in baseline.ticks}; right = {t.index: t for t in candidate.ticks}
    for index in left.keys() & right.keys():
        a, b = left[index], right[index]
        if a.status == b.status == 'completed' and (a.packet != b.packet or
                (a.points.sha256 if a.points else None) != (b.points.sha256 if b.points else None)):
            reasons.append(f'Decoded input packet/point bytes differ at tick {index}')
    status = 'not_comparable' if reasons else 'comparable' if complete else 'not_evaluated'
    if not complete:
        reasons.append('Both full replay sequences must complete; missing/failed ticks remain in the denominator')
    rows = []
    for index in range(len(baseline.inputs)):
        a, b = left.get(index), right.get(index)
        def side(tick):
            return {'status': tick.status if tick else 'missing',
                'mode': tick.output['decision']['mode'] if tick and tick.output else None,
                'control': tick.output['control'] if tick and tick.output else None,
                'unavailable_reasons': tick.packet.get('unavailable_reasons', []) if tick and tick.packet else [],
                'error': tick.error if tick else 'Missing tick'}
        aa, bb = side(a), side(b)
        delta = ({k: bb['control'][k] - aa['control'][k] for k in ('throttle', 'brake', 'steer')}
                 if status == 'comparable' else None)
        changed = (aa['mode'] != bb['mode'] or any(delta.values())) if delta is not None else None
        rows.append({'index': index, 'at_ns': baseline.inputs[index].at_ns, 'baseline': aa, 'candidate': bb,
            'control_delta': delta, 'request_changed': changed})
    return {'status': status, 'reasons': reasons, 'total_ticks': len(rows), 'rows': rows,
        'changed_request_ticks': sum(r['request_changed'] is True for r in rows) if status == 'comparable' else None,
        'baseline_manifest_hash': baseline.manifest_hash, 'candidate_manifest_hash': candidate.manifest_hash,
        'control_application': 'not_applied_open_loop', 'driving_verdict': None,
        'meaning': 'Changed requests on identical recorded input, not changed vehicle response or improved driving.'}


def validate_links(session, case):
    plans = case.get('stack_replays', [])
    if not plans:
        return
    try:
        if (case['schema_version'] != 'axiom-investigation-case/5' or not isinstance(plans, list)
                or len(plans) > MAX_ATTEMPTS or len({p['id'] for p in plans}) != len(plans)):
            raise ValueError('Invalid stack replay list')
        for plan in plans:
            d = plan['definition']; source = d['source']
            old = session.get(CaseRevisionRow, (case['id'], source['case_revision']))
            if (digest(d) != plan['definition_hash'] or d['schema_version'] != VERSION or d['scope'] != SCOPE
                    or source['case_id'] != case['id'] or source['segment_hash'] != case['segment_hash']
                    or not 1 <= source['case_revision'] < case['revision'] or old is None
                    or old.manifest_hash != source['case_manifest_hash']
                    or _sealed(old).get('conditions') != d['source_conditions']):
                raise ValueError('Source case or definition differs')
            baseline = _task(session, d['baseline_id']); _matches_source(case, baseline)
            row = jobs._row(session, plan['job_id']); task = stack_replays.detail(session, row)
            if (baseline.manifest_hash != d['baseline_manifest_hash'] or baseline.status != 'completed'
                    or row.prepared != d['prepared'] or row.input_hash != plan['input_hash']
                    or plan['input_hash'] != d['prepared_hash'] or digest(d['prepared']) != d['prepared_hash']
                    or task.inputs != baseline.inputs or task.binding_snapshot != baseline.binding_snapshot
                    or task.recording_snapshot != baseline.recording_snapshot):
                raise ValueError('Execution or exact input identity differs')
            if plan['capture']:
                c = plan['capture']; compared = comparison(baseline, task)
                if (task.status not in jobs.TERMINAL or not task.manifest_hash
                        or task.manifest_hash != c['manifest_hash'] or digest(compared) != c['comparison_hash']
                        or compared['status'] != c['comparison_status']):
                    raise ValueError('Captured evidence differs')
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, 'Case stack replay source or execution linkage failed its integrity check') from error


def capture(db, case_id, plan_id, body, key):
    with jobs._write(db) as session:
        retry, storage, request_hash = _retry(session, case_id, 'capture:' + plan_id, body, key)
        if retry:
            return retry
        prior = _current(session, case_id, body.expected_revision); value = _new(prior)
        plan = next((p for p in value['stack_replays'] if p['id'] == plan_id), None)
        if plan is None:
            raise HTTPException(404, 'Stack replay is not linked to this case')
        if plan['definition_hash'] != body.plan_hash or plan['capture'] is not None:
            raise HTTPException(409, 'Plan hash differs or this attempt is already captured')
        task = _task(session, plan['job_id'])
        if task.status not in jobs.TERMINAL or not task.manifest_hash:
            raise HTTPException(409, 'Wait for sealed terminal replay evidence')
        compared = comparison(_task(session, plan['definition']['baseline_id']), task)
        plan['capture'] = {'manifest_hash': task.manifest_hash, 'comparison_hash': digest(compared),
            'comparison_status': compared['status'], 'captured_at': utcnow()}
        return _publish(session, session.get(CaseRow, case_id), value, storage, request_hash)


def report(session, case_id, revision=None):
    case = _get(session, case_id, revision); results = []
    for plan in case.get('stack_replays', []):
        baseline = _task(session, plan['definition']['baseline_id']); task = _task(session, plan['job_id'])
        results.append({**plan, 'status': task.status, 'error': task.error,
            'counts': task.counts.model_dump(mode='json'), 'manifest_hash': task.manifest_hash,
            'comparison': comparison(baseline, task)})
    return {'schema_version': 'case-stack-replay-report/1', 'scope': SCOPE, 'case_id': case_id,
        'case_revision': case['revision'], 'case_manifest_hash': case['manifest_hash'], 'case_state': case['state'],
        'plans': results, 'limitations': ['Full recorded baseline prefix is replayed from a fresh stack session.',
            'Offsets, ego state, route and rig are source declarations, not acquisition or calibration validation.',
            'Unknown/stale/empty sensor inputs and cancelled/failed ticks are retained.',
            'No vehicle, simulator, GPU model or learned E2E policy is controlled; no safety/ODD coverage is certified.']}


def register_case_stack_routes(api, db, registry, decoders):
    @api.post('/cases/{case_id}/stack-replays/preview', tags=['case stack replay'])
    def preview_route(case_id: str, body: StackReplaySelection):
        return preview(db, registry, decoders, case_id, body)

    @api.post('/cases/{case_id}/stack-replays', status_code=202, tags=['case stack replay'])
    def submit_route(case_id: str, body: StackReplaySubmit, idempotency_key: str = Header(min_length=1, max_length=180)):
        return submit(db, registry, decoders, case_id, body, idempotency_key)

    @api.post('/cases/{case_id}/stack-replays/{plan_id}/capture', status_code=201, tags=['case stack replay'])
    def capture_route(case_id: str, plan_id: str, body: StackReplayCapture, idempotency_key: str = Header(min_length=1, max_length=180)):
        return capture(db, case_id, plan_id, body, idempotency_key)

    @api.get('/cases/{case_id}/stack-replays', tags=['case stack replay'])
    def report_route(case_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            data = report(session, case_id, revision); raw = canonical_bytes(data)
            if len(raw) > MAX_REPORT_BYTES:
                raise HTTPException(413, 'Stack comparison report exceeds 16 MiB')
            query = urlencode({'revision': data['case_revision'], 'expected_hash': digest(data)})
            return {**data, 'manifest_hash': digest(data), 'manifest_size_bytes': len(raw),
                'download_url': f'/api/v1/cases/{case_id}/stack-replays/export?{query}'}

    @api.get('/cases/{case_id}/stack-replays/export', tags=['case stack replay'])
    def export_route(case_id: str, revision: int = Query(..., ge=1), expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
        with db.read_session() as session:
            data = report(session, case_id, revision); raw = canonical_bytes(data)
            if len(raw) > MAX_REPORT_BYTES:
                raise HTTPException(413, 'Stack comparison report exceeds 16 MiB')
            if digest(data) != expected_hash:
                raise HTTPException(409, 'Replay state changed; refresh before exporting')
            return Response(raw, media_type='application/json', headers={'X-Content-Type-Options': 'nosniff',
                'Content-Disposition': f'attachment; filename="case-stack-{expected_hash[:12]}.json"'})
