"""Recording segment ↔ investigation ↔ sealed evaluation, with no new inference.

Segments use either an inclusive log-time interval (all duplicate timestamps
included) or the exact ordinal of an evaluated image. No nearest-time matching,
label copying, inferred free space or silent truncation is performed.
"""
import hashlib
import json

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import select

from . import jobs
from .case_models import CaseEvaluationRow, CaseRevisionRow, CaseRow, SegmentRow
from .case_schemas import CaseCreate, CaseRevisionCreate, CaseState, EvaluationSelection
from .datasets import get_dataset_image
from .detection_evaluations import detail as evaluation_detail
from .recording_models import RecordingChannelRow, RecordingMessageRow, RecordingRow
from .recordings import _get_recording, _sample, read_message_bytes
from .service import canonical_bytes, digest, uid, utcnow

MAX_SAMPLES = 2000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
MAX_EVALUATIONS = 32


def _sealed(row):
    raw = row.manifest
    if len(raw) > MAX_MANIFEST or len(raw) != row.manifest_size_bytes or hashlib.sha256(raw).hexdigest() != row.manifest_hash:
        raise HTTPException(409, 'Investigation evidence failed its integrity check')
    value = json.loads(raw)
    if canonical_bytes(value) != raw:
        raise HTTPException(409, 'Investigation manifest is not canonical')
    return value


def _seal(value):
    raw = canonical_bytes(value)
    if len(raw) > MAX_MANIFEST:
        raise HTTPException(413, 'Investigation evidence exceeds 2 MiB; select a smaller interval')
    return dict(manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest(), manifest_size_bytes=len(raw))


def _descriptor(row):
    return dict(ordinal=row.ordinal, log_time_ns=str(int(row.log_time_key)),
        publish_time_ns=str(int(row.publish_time_key)), sequence=row.sequence,
        size_bytes=row.payload_length, sha256=row.sha256)


def _evaluation(session, selection):
    evaluation = evaluation_detail(session, jobs._row(session, selection.evaluation_id))
    if evaluation.status not in jobs.TERMINAL or evaluation.manifest_hash is None:
        raise HTTPException(409, 'Only a sealed terminal evaluation can be linked')
    image = next((x for x in evaluation.dataset_snapshot['image_assets'] if x['id'] == selection.image_id), None)
    source = next((x for x in evaluation.source_context['rows'] if x['image_id'] == selection.image_id), None)
    if image is None or source is None:
        raise HTTPException(422, 'Image is not in the evaluation selected split')
    asset = get_dataset_image(session, selection.image_id)
    # Use the existing checked image manifest, not user-supplied source IDs.
    if asset.input.model_dump(mode='json') != image['input'] or asset.image.sha256 != image['image']['sha256']:
        raise HTTPException(409, 'Stored image differs from the pinned evaluation input')
    report_row = next((x for x in evaluation.report.rows if x.image_id == selection.image_id), None) if evaluation.report else None
    link = dict(evaluation_id=evaluation.id, name=evaluation.name, manifest_hash=evaluation.manifest_hash,
        image_id=selection.image_id, image_sha256=asset.image.sha256, dataset_id=evaluation.dataset_id,
        dataset_revision=evaluation.dataset_revision, split=evaluation.request.split,
        row_status=report_row.status if report_row else source['status'],
        diagnostics=report_row.diagnostics.model_dump(mode='json') if report_row and report_row.diagnostics else None,
        input=asset.input.model_dump(mode='json'), evaluation_status=evaluation.status)
    return link, asset.input


def _make_segment(session, db, selection):
    link = None
    if isinstance(selection, EvaluationSelection):
        link, source = _evaluation(session, selection)
        recording_id, channel_id = source.recording_id, source.channel_id
        query = select(RecordingMessageRow).where(RecordingMessageRow.recording_id == recording_id,
            RecordingMessageRow.channel_id == channel_id, RecordingMessageRow.ordinal == source.ordinal)
        selector = {'kind': 'exact_ordinal', 'ordinal': source.ordinal}
    else:
        recording_id, channel_id = selection.recording_id, selection.channel_id
        query = select(RecordingMessageRow).where(RecordingMessageRow.recording_id == recording_id,
            RecordingMessageRow.channel_id == channel_id,
            RecordingMessageRow.log_time_key >= f'{int(selection.start_time_ns):020d}',
            RecordingMessageRow.log_time_key <= f'{int(selection.end_time_ns):020d}')
        selector = {'kind': 'inclusive_log_time', 'start_time_ns': selection.start_time_ns, 'end_time_ns': selection.end_time_ns}
    recording = _get_recording(session, recording_id)
    channel = session.get(RecordingChannelRow, (recording_id, channel_id))
    if channel is None:
        raise HTTPException(404, 'Channel does not exist in the selected recording')
    if recording.payload['sha256'] != recording.sha256 or recording.payload['index']['source_sha256'] != recording.sha256:
        raise HTTPException(409, 'Recording identity mismatch')
    messages = list(session.scalars(query.order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal).limit(MAX_SAMPLES + 1)))
    if not messages:
        raise HTTPException(422, 'No original messages in this selection; no future fallback is used')
    if len(messages) > MAX_SAMPLES or sum(m.payload_length for m in messages) > MAX_SOURCE_BYTES:
        raise HTTPException(413, 'Segment exceeds 2000 messages or 64 MiB; select a smaller interval')
    for message in messages:
        read_message_bytes(db, recording, message)
    samples = [_descriptor(m) for m in messages]
    value = dict(schema_version='axiom-source-segment/1', id=uid('segment'), created_at=utcnow(),
        recording_id=recording_id, recording_sha256=recording.sha256, recording_name=recording.payload['name'],
        source_kind=recording.payload['source_kind'], source_kind_verified=False,
        index_sha256=recording.payload['index']['sha256'], channel_id=channel_id, channel=channel.payload,
        clock_domain='recording_log_time', selector=selector, samples=samples,
        start_time_ns=min(samples, key=lambda x: int(x['log_time_ns']))['log_time_ns'],
        end_time_ns=max(samples, key=lambda x: int(x['log_time_ns']))['log_time_ns'],
        sample_count=len(samples), capture_clock_validated=False)
    if link:
        _link_matches(value, link)
    row = SegmentRow(id=value['id'], recording_id=recording_id, channel_id=channel_id, **_seal(value))
    session.add(row); session.flush()
    return value, row.manifest_hash, [link] if link else []


def _link_matches(segment, link):
    source = link['input']
    match = next((s for s in segment['samples'] if s['ordinal'] == source['ordinal']), None)
    if (source['recording_id'] != segment['recording_id'] or source['recording_sha256'] != segment['recording_sha256']
            or source['channel_id'] != segment['channel_id'] or match is None
            or source['message_sha256'] != match['sha256'] or source['log_time_ns'] != match['log_time_ns']
            or source['publish_time_ns'] != match['publish_time_ns']):
        raise HTTPException(422, 'Evaluation image does not belong to this exact source segment')


def _get(session, identifier, revision=None):
    head = session.get(CaseRow, identifier)
    if head is None:
        raise HTTPException(404, 'Investigation case not found')
    row = session.get(CaseRevisionRow, (identifier, revision or head.revision))
    if row is None:
        raise HTTPException(404, 'Case revision not found')
    value = _sealed(row)
    segment_row = session.get(SegmentRow, head.segment_id)
    segment = _sealed(segment_row) if segment_row else None
    links = set(session.execute(select(CaseEvaluationRow.evaluation_id, CaseEvaluationRow.image_id).where(
        CaseEvaluationRow.case_id == identifier, CaseEvaluationRow.revision == row.revision)).all())
    if (value['id'] != head.id or value['revision'] != row.revision or segment is None
            or segment['id'] != head.segment_id or value['segment'] != segment
            or segment['recording_id'] != segment_row.recording_id or segment['channel_id'] != segment_row.channel_id
            or value['segment_hash'] != segment_row.manifest_hash
            or links != {(x['evaluation_id'], x['image_id']) for x in value['evaluations']}
            or row.revision == head.revision and (head.title != value['title'] or head.state != value['state'])):
        raise HTTPException(409, 'Investigation relationships differ from the sealed evidence')
    if row.revision > 1:
        previous = session.get(CaseRevisionRow, (identifier, row.revision - 1))
        if previous is None or value['previous_manifest_hash'] != previous.manifest_hash:
            raise HTTPException(409, 'Investigation revision chain differs from the prior manifest')
    for link in value['evaluations']:
        _link_matches(segment, link)
    from .case_conditions import validate_annotation
    validate_annotation(value)
    from .case_retests import validate_links
    validate_links(session, value)
    from .case_model_replays import validate_links as validate_model_replays
    validate_model_replays(session, value)
    from .case_stack_replays import validate_links as validate_stack_replays
    validate_stack_replays(session, value)
    from .neural_perception import validate_case_link
    validate_case_link(session, value)
    from .object_scene_cases import validate_case_link as validate_object_scene_case
    validate_object_scene_case(session, value)
    return {**value, 'manifest_hash': row.manifest_hash, 'manifest_size_bytes': row.manifest_size_bytes,
        'download_url': f'/api/v1/cases/{identifier}/export?revision={row.revision}'}


def _publish(session, head, value, key, request_hash):
    row = CaseRevisionRow(case_id=head.id, revision=value['revision'], request_key=key,
        request_hash=request_hash, **_seal(value))
    session.add(row); session.flush()
    for link in value['evaluations']:
        session.add(CaseEvaluationRow(case_id=head.id, revision=value['revision'],
            evaluation_id=link['evaluation_id'], image_id=link['image_id']))
    head.revision, head.title, head.state = value['revision'], value['title'], value['state']
    session.flush()
    return _get(session, head.id, value['revision'])


def create_case(db, body, key):
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        existing = session.scalar(select(CaseRow).where(CaseRow.creation_key == key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key already identifies a different case request')
            return _get(session, existing.id, 1)
        segment, segment_hash, links = _make_segment(session, db, body.source)
        value = dict(schema_version='axiom-investigation-case/1', id=uid('case'), revision=1, created_at=utcnow(),
            title=body.title, note=body.note, assignee=body.assignee, state='open', disposition_note='',
            segment=segment, segment_hash=segment_hash, evaluations=links, previous_manifest_hash=None,
            workflow_state_is_test_verdict=False, assignee_is_authenticated_identity=False)
        head = CaseRow(id=value['id'], revision=1, segment_id=segment['id'], title=body.title, state='open',
            created_at=value['created_at'], creation_key=key, request_hash=request_hash)
        session.add(head); session.flush()
        return _publish(session, head, value, 'create:' + head.id, request_hash)


def revise_case(db, identifier, body, key):
    request_hash = digest({'case_id': identifier, **body.model_dump(mode='json')})
    with jobs._write(db) as session:
        existing = session.scalar(select(CaseRevisionRow).where(CaseRevisionRow.request_key == key))
        if existing:
            if existing.request_hash != request_hash or existing.case_id != identifier:
                raise HTTPException(409, 'Revision Idempotency-Key identifies a different request')
            return _get(session, identifier, existing.revision)
        prior = _get(session, identifier)
        if prior['revision'] != body.expected_revision:
            raise HTTPException(409, 'Case revision changed; reload before editing')
        links = list(prior['evaluations'])
        if body.add_evaluation:
            link, _ = _evaluation(session, body.add_evaluation)
            _link_matches(prior['segment'], link)
            if not any((x['evaluation_id'], x['image_id']) == (link['evaluation_id'], link['image_id']) for x in links):
                links.append(link)
        if len(links) > MAX_EVALUATIONS:
            raise HTTPException(413, 'Case supports at most 32 pinned evaluation images')
        value = {k: v for k, v in prior.items() if k not in ('manifest_hash', 'manifest_size_bytes', 'download_url')}
        value.update(revision=prior['revision'] + 1, created_at=utcnow(), title=body.title, note=body.note,
            assignee=body.assignee, state=body.state, disposition_note=body.disposition_note,
            evaluations=links, previous_manifest_hash=prior['manifest_hash'])
        return _publish(session, session.get(CaseRow, identifier), value, key, request_hash)


def register_case_routes(api, db):
    from .case_conditions import (DiagnosticTag, ReviewState, census, filter_rows, parse_filters,
                                 register_condition_routes, summary)
    register_condition_routes(api, db)
    @api.post('/cases', status_code=201, tags=['investigation cases'])
    def post_case(body: CaseCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return create_case(db, body, idempotency_key)

    @api.get('/cases', tags=['investigation cases'])
    def list_cases(recording_id: str | None = None, evaluation_id: str | None = None, image_id: str | None = None,
                   state: CaseState | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   q: str = Query('', max_length=300), condition: list[str] = Query(default=[]),
                   review: ReviewState = 'all', diagnostic: DiagnosticTag | None = None):
        parse_filters(condition)
        with db.read_session() as session:
            query = select(CaseRow).join(SegmentRow, CaseRow.segment_id == SegmentRow.id)
            if recording_id: query = query.where(SegmentRow.recording_id == recording_id)
            if state: query = query.where(CaseRow.state == state)
            if evaluation_id or image_id:
                query = query.where(select(CaseEvaluationRow.case_id).where(CaseEvaluationRow.case_id == CaseRow.id,
                    CaseEvaluationRow.revision == CaseRow.revision,
                    *([CaseEvaluationRow.evaluation_id == evaluation_id] if evaluation_id else []),
                    *([CaseEvaluationRow.image_id == image_id] if image_id else [])).exists())
            query = query.order_by(CaseRow.created_at.desc(), CaseRow.id)
            if q.strip() or condition or review != 'all' or diagnostic:
                rows = filter_rows(census(session, query), q=q, conditions=condition, review=review, diagnostic=diagnostic)
                return {'items': [{k: v for k, v in r.items() if k != '_text'} for r in rows[offset:offset + limit]],
                    'total_matches': len(rows), 'next_offset': offset + limit if offset + limit < len(rows) else None}
            heads = list(session.scalars(query.offset(offset).limit(limit + 1)))
            items = []
            for head in heads[:limit]:
                value = _get(session, head.id)
                items.append(summary(value))
            return {'items': items, 'next_offset': offset + limit if len(heads) > limit else None}

    @api.get('/cases/{case_id}', tags=['investigation cases'])
    def get_case(case_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            return _get(session, case_id, revision)

    @api.post('/cases/{case_id}/revisions', status_code=201, tags=['investigation cases'])
    def post_revision(case_id: str, body: CaseRevisionCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return revise_case(db, case_id, body, idempotency_key)

    @api.get('/cases/{case_id}/export', tags=['investigation cases'])
    def export_case(case_id: str, revision: int = Query(..., ge=1)):
        with db.read_session() as session:
            value = _get(session, case_id, revision)
            row = session.get(CaseRevisionRow, (case_id, revision))
            return Response(row.manifest, media_type='application/json', headers={
                'Content-Disposition': f'attachment; filename="{value["id"]}.r{revision}.case.json"',
                'X-Content-Type-Options': 'nosniff'})

    @api.get('/cases/{case_id}/samples', tags=['investigation cases'])
    def source_samples(case_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        with db.read_session() as session:
            value = _get(session, case_id)
            segment = value['segment']; recording = _get_recording(session, segment['recording_id'])
            channel = session.get(RecordingChannelRow, (recording.id, segment['channel_id']))
            if (recording.sha256 != segment['recording_sha256'] or channel is None or channel.payload != segment['channel']
                    or recording.payload['index']['sha256'] != segment['index_sha256']):
                raise HTTPException(409, 'Source identity differs from the pinned segment')
            samples, warnings = [], []
            for descriptor in segment['samples'][offset:offset + limit]:
                entry = session.get(RecordingMessageRow, (recording.id, descriptor['ordinal']))
                if entry is None or entry.channel_id != segment['channel_id'] or _descriptor(entry) != descriptor:
                    raise HTTPException(409, 'Source message index differs from the pinned segment')
                raw = read_message_bytes(db, recording, entry)
                samples.append(_sample(entry, raw, channel.payload['decoder'], warnings).model_dump(mode='json'))
            return {'segment_id': segment['id'], 'total': segment['sample_count'], 'samples': samples,
                'next_offset': offset + limit if offset + limit < segment['sample_count'] else None, 'warnings': warnings}
