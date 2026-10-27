"""Real retained-log batch replay and explicit Case selection, over the existing queue.

One A/B pair per original interval supplies every candidate window. Counts and
ranking never turn repeated frames into independent logs or safety evidence.
"""
import copy
from threading import RLock

from fastapi import Header, HTTPException, Request

from . import cases, log_sim, log_sim_events, log_sim_replays as replay
from .case_schemas import CaseCreate, RecordingSelection
from .log_sim_batch_schemas import BatchAction, BatchSelection, BatchSubmit, BundleCreate
from .project_recordings import authorize_recording_source
from .replay_spec_schemas import ReferenceController, ReplaySpecCreate
from .replay_specs import create_replay_spec
from .scene_sequences import create_scene_sequence
from .service import digest, utcnow
from .team_access import project_authorize

BATCH = 'log-sim-batch'
BUNDLE = 'log-sim-case-bundle'
LOCK = RLock()
PROFILES = {
    'baseline': {'id': 'batch-balanced', 'label': 'A · 차간 1.5초',
                 'controller': ReferenceController(cruise_speed_mps=8., stop_buffer_m=2., time_headway_s=1.5).model_dump()},
    'candidate': {'id': 'batch-headway', 'label': 'B · 차간 3초',
                  'controller': ReferenceController(cruise_speed_mps=8., stop_buffer_m=2., time_headway_s=3.).model_dump()},
}
LIMITATIONS = replay.LIMITATIONS + [
    'Candidates are review windows ranked by control-request differences, not confirmed failures.',
    'One original source interval has one shared A/B execution pair; Case windows are not independent log runs.',
]


def _source_access(request, db, project, source):
    project_authorize(request, db, project)
    if source['kind'] in {'nuscenes', 'recorded'}:
        authorize_recording_source(request, db, project, source['recording_ref']['id'])


def _scope(source):
    value = source.get('source_scope') or {}
    if not isinstance(value, dict):
        return {'use_scope': value, 'commercial_use': 'unknown'}
    return {key: value.get(key) for key in ('use_scope', 'commercial_use', 'source_dataset', 'converter_version')}


def _counts(rows, candidates=()):
    return dict(source_count=len(rows), distinct_scenes=len({r['scene_id'] for r in rows}),
        supported_sources=sum(r['status'] != 'unsupported' for r in rows),
        unsupported_sources=sum(r['status'] == 'unsupported' for r in rows),
        completed_sources=sum(r['status'] == 'completed' for r in rows),
        failed_sources=sum(r['status'] in {'failed', 'cancelled', 'not_evaluated', 'submission_failed'} for r in rows),
        frame_count=sum(r.get('frame_count') or 0 for r in rows),
        duration_s=sum(r.get('duration_s') or 0 for r in rows), candidate_count=len(candidates))


def _library_counts(items):
    return dict(distinct_scenes=len(items), unavailable_scene_count=sum(not item['segments'] for item in items),
        original_frame_count=sum(item.get('original_frame_count') or 0 for item in items),
        available_frame_count=sum(item.get('available_frame_count') or 0 for item in items),
        excluded_frame_count=sum(item.get('excluded_frame_count') or 0 for item in items))


def prepare_batch(db, request, body):
    project_authorize(request, db, body.project_id)
    rows, prepared, seen_sequences = [], {}, set()
    profiles = PROFILES
    if body.comparison_ref:
        from .log_sim_beta import settings_profiles
        with db.read_session() as session:
            profiles = settings_profiles(session, body.project_id, body.comparison_ref)
    library_scenes, library_hash = [], None
    if body.library_id:
        from .log_sim_library import library_snapshot
        with db.read_session() as session:
            library = library_snapshot(session, body.project_id, body.library_id)
        library_hash = library['manifest_hash']
        selected = set(body.selected_scene_ids or [item['id'] for item in library['items']
            if any(segment['source_id'] in body.source_ids for segment in item['segments'])])
        library_scenes = [item for item in library['items'] if item['id'] in selected]
        if len(library_scenes) != len(selected) or {segment['source_id'] for item in library_scenes for segment in item['segments']} != set(body.source_ids):
            raise HTTPException(422, 'Select complete original scenes and their exact library source intervals')
    for identity in body.source_ids:
        with db.read_session() as session:
            source = log_sim._source(session, body.project_id, identity)
        _source_access(request, db, body.project_id, source)
        metadata = source.get('library_metadata') or {}
        row = dict(source_id=identity, source_hash=source['source_hash'], title=source['title'],
            source_kind=source['kind'], recording_ref=source.get('recording_ref'),
            scene_id=metadata.get('scene_token') or (source.get('recording_ref') or {}).get('id') or identity,
            scene_name=metadata.get('scene_name') or source['title'],
            duration_s=source.get('duration_s', 0), frame_count=source.get('frame_count', 0),
            source_use_scope=_scope(source), status='ready', reason=None)
        try:
            p, definition = replay.prepare_source(db, body.project_id, identity, profiles)
            if p['sequence_sha256'] in seen_sequences:
                raise HTTPException(422, 'Duplicate original sequence selected; it is not an independent log interval')
            seen_sequences.add(p['sequence_sha256'])
            with db.read_session() as session:
                frames, receipt = log_sim_events.source_frames(db, session, body.project_id, source)
            analysis = log_sim_events.discover_events(source, frames, receipt)
            row.update(frame_count=p['frame_count'], sequence_sha256=p['sequence_sha256'],
                analysis_hash=analysis['analysis_hash'], rule_candidate_count=len(analysis['items']))
            prepared[identity] = (p, definition, analysis)
        except HTTPException as error:
            if error.status_code in {401, 403, 404}:
                raise
            row.update(status='unsupported', reason=str(error.detail))
        rows.append(row)
    result = dict(schema_version='log-sim-batch-preview/1', project_id=body.project_id,
        library_id=body.library_id, source_ids=body.source_ids, sources=rows,
        library_manifest_hash=library_hash, library_scenes=library_scenes,
        selected_scene_ids=sorted(item['id'] for item in library_scenes),
        profiles=profiles, mode='open_loop', learned_model=False, seed=42,
        adapter_id='reference-planning-replay', adapter_version='1', counts=_counts(rows),
        planned_execution_count=2*len(prepared), limitations=LIMITATIONS)
    if body.comparison_ref:
        result['comparison_ref'] = body.comparison_ref.model_dump()
    if library_scenes:
        result['counts'].update(_library_counts(library_scenes))
    return result | {'preview_hash': digest(result)}, prepared


def _authorize_saved(db, request, value, role='Viewer'):
    project = value['project_id']
    project_authorize(request, db, project, role)
    for row in value['sources']:
        if row['source_kind'] in {'nuscenes', 'recorded'}:
            authorize_recording_source(request, db, project, row['recording_ref']['id'])


def _new_pair(db, batch, row, identity):
    p, definition = replay.prepare_source(db, batch['project_id'], row['source_id'], batch['profiles'])
    if p['source_hash'] != row['source_hash'] or p['sequence_sha256'] != row['sequence_sha256']:
        raise HTTPException(409, 'Pinned batch source changed; create a new preview')
    with db.session.begin() as session:
        sequence = create_scene_sequence(session, db, definition, expected_snapshot_sha256=p['sequence_sha256'])
        specs = {side: create_replay_spec(session, db, ReplaySpecCreate(name='Log batch '+side,
            project_id=batch['project_id'], sequence_id=sequence.id, sequence_sha256=sequence.sha256,
            mode='open_loop', controller=profile['controller'], observers=['speed'], note=LIMITATIONS[1])).id
            for side, profile in p['profiles'].items()}
        pair = {key: p[key] for key in ('source_hash', 'source_kind', 'recording_ref', 'case_ref', 'case_indices',
            'frame_count', 'case_frame_count', 'sequence_sha256', 'profiles', 'source_use_scope', 'limitations')}
        pair.update(schema_version='log-controller-comparison/1', id=identity, project_id=batch['project_id'],
            source_id=row['source_id'], request=None, created_at=utcnow(), sequence_id=sequence.id,
            replay_spec_ids=specs, execution_ids={}, mode='open_loop', learned_model=False, seed=42, batch_id=batch['id'])
        replay._put(session, batch['project_id'], replay.COMPARISON, identity, pair)
    return pair


def _submit_sources(db, registry, batch):
    for row in batch['sources']:
        if row['status'] == 'unsupported':
            continue
        pair_id = row['comparison_id']
        try:
            with db.read_session() as session:
                saved = log_sim._catalog(session, batch['project_id'], replay.COMPARISON, pair_id)
            pair = saved['definition'] if saved else _new_pair(db, batch, row, pair_id)
            replay._launch(db, registry, pair)
            row.update(status='queued', reason=None)
        except (HTTPException, ValueError) as error:
            row.update(status='submission_failed', reason=str(error.detail if isinstance(error, HTTPException) else error))
        with db.session.begin() as session:
            replay._put(session, batch['project_id'], BATCH, batch['id'], batch)


def window_metrics(samples):
    count = len(samples)
    differences = sum(s['baseline']['decision'] != s['candidate']['decision'] for s in samples)
    opposites = sum(s['baseline']['acceleration_mps2'] * s['candidate']['acceleration_mps2'] < -1e-8 for s in samples)
    return dict(sample_count=count, decision_difference_count=differences,
        decision_difference_ratio=differences/count if count else 0,
        opposite_acceleration_count=opposites, opposite_acceleration_ratio=opposites/count if count else 0,
        max_abs_acceleration_delta_mps2=max((abs(s['baseline']['acceleration_mps2']-s['candidate']['acceleration_mps2']) for s in samples), default=0),
        baseline_max_deceleration_mps2=max((max(0., -s['baseline']['acceleration_mps2']) for s in samples), default=0),
        candidate_max_deceleration_mps2=max((max(0., -s['candidate']['acceleration_mps2']) for s in samples), default=0))


def _spans(samples, predicate):
    group = []
    for sample in samples:
        if not predicate(sample):
            if group:
                yield group
                group = []
            continue
        if group and sample['time_s']-group[-1]['time_s'] > 1.0:
            yield group
            group = []
        group.append(sample)
    if group:
        yield group


def _candidates(batch, row, report):
    result = report.get('result') or {}
    if result.get('status') != 'comparable':
        return []
    samples, values = result['samples'], []

    def add(kind, label, start, end, event_ids=()):
        selected = [s for s in samples if start-1e-7 <= s['time_s'] <= end+1e-7]
        if not selected:
            return
        identity = digest(dict(batch_id=batch['id'], source_id=row['source_id'], kind=kind,
            start_index=selected[0]['index'], end_index=selected[-1]['index'], event_ids=event_ids))
        values.append(dict(id='logcand_'+identity[:32], source_id=row['source_id'], source_hash=row['source_hash'],
            scene_id=row['scene_id'], scene_name=row['scene_name'], label=label, kind=kind,
            time_s=selected[0]['time_s'], end_time_s=selected[-1]['time_s'],
            start_index=selected[0]['index'], end_index=selected[-1]['index'],
            comparison_id=report['id'], metrics=window_metrics(selected), event_ids=list(event_ids),
            status='evaluated', review_status='unreviewed', verdict='not_evaluated',
            output_hash=result['output_hash'], batch_id=batch['id']))

    for event in row.get('events', []):
        add(event['kind'], event['label'], event['time_s'], event['end_time_s'], [event['id']])
    for kind, label, predicate in [
        ('decision_divergence', '제어 판단이 갈린 구간', lambda s: s['baseline']['decision'] != s['candidate']['decision']),
        ('opposite_acceleration', '가속·감속 요청이 반대인 구간', lambda s: s['baseline']['acceleration_mps2']*s['candidate']['acceleration_mps2'] < -1e-8),
    ]:
        for span in _spans(samples, predicate):
            add(kind, label, span[0]['time_s'], span[-1]['time_s'])
    return values


def report_batch(db, session, batch):
    rows, candidates = [], []
    for stored in batch['sources']:
        row = {key: value for key, value in stored.items() if key != 'events'}
        if row['status'] != 'unsupported':
            try:
                saved = log_sim._catalog(session, batch['project_id'], replay.COMPARISON, row['comparison_id'])
                if saved:
                    pair = replay._report(db, session, saved['definition'])
                    row.update(status=pair['status'], execution_ids=pair['execution_ids'],
                        executions=pair['executions'], reason=(pair.get('result') or {}).get('reason'))
                    if pair['status'] == 'completed' and (pair.get('result') or {}).get('status') != 'comparable':
                        row['status'] = 'not_evaluated'
                    if stored['status'] == 'submission_failed' and len(pair['execution_ids']) < 2:
                        row.update(status='submission_failed', reason=stored['reason'])
                    candidates.extend(_candidates(batch, stored, pair))
            except (HTTPException, ValueError, KeyError) as error:
                row.update(status='not_evaluated', reason=str(error.detail if isinstance(error, HTTPException) else error))
        rows.append(row)
    candidates.sort(key=lambda c: (-c['metrics']['decision_difference_ratio'],
        -c['metrics']['opposite_acceleration_ratio'], -c['metrics']['max_abs_acceleration_delta_mps2'],
        c['source_id'], c['time_s'], c['kind'], c['id']))
    for index, candidate in enumerate(candidates, 1):
        candidate['rank'] = index
    active = any(row['status'] in {'ready', 'queued', 'running'} for row in rows)
    status = 'running' if active else 'completed' if all(row['status'] in {'completed', 'unsupported'} for row in rows) else 'completed_with_errors'
    if active and all(row['status'] in {'queued', 'unsupported'} for row in rows):
        status = 'queued'
    result = {key: value for key, value in batch.items() if key not in {'sources', 'request'}}
    result.update(sources=rows, candidates=candidates, counts=_counts(rows, candidates), status=status,
        ranking='decision_difference_ratio,opposite_acceleration_ratio,max_abs_acceleration_delta_mps2; stable source/time ties')
    if batch.get('library_scenes'):
        result['counts'].update(_library_counts(batch['library_scenes']))
    result['report_hash'] = digest(result)
    return result


def _bundle_access(db, request, value, role='Viewer'):
    project_authorize(request, db, value['project_id'], role)
    with db.read_session() as session:
        batch = replay._get(session, value['project_id'], BATCH, value['batch_id'])
    _authorize_saved(db, request, batch, role)
    return batch


def register_log_sim_batch_routes(api, db, registry):
    @api.post('/log-sim/batches/preview', tags=['Log Sim'], openapi_extra={'x-axiom-read-only': True})
    def preview(body: BatchSelection, request: Request):
        return prepare_batch(db, request, body)[0]

    @api.post('/log-sim/batches', status_code=202, tags=['Log Sim'])
    def submit(body: BatchSubmit, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        identity = 'logbatch_'+digest({'project': body.project_id, 'key': idempotency_key})[:32]
        with LOCK:
            with db.read_session() as session:
                prior = log_sim._catalog(session, body.project_id, BATCH, identity)
            if prior:
                batch = prior['definition']
                _authorize_saved(db, request, batch, 'Engineer')
                if batch['request'] != body.model_dump(exclude={'comparison_ref'} if body.comparison_ref is None else set()):
                    raise HTTPException(409, 'Idempotency key identifies another batch')
            else:
                p, prepared = prepare_batch(db, request, body)
                if p['preview_hash'] != body.preview_hash:
                    raise HTTPException(409, 'Batch source inputs changed; preview again')
                if not prepared:
                    raise HTTPException(422, 'No selected source supports batch replay')
                batch = p | dict(schema_version='log-sim-batch/1', id=identity, created_at=utcnow(), request=body.model_dump(exclude={'comparison_ref'} if body.comparison_ref is None else set()))
                for row in batch['sources']:
                    row['attempts'] = []
                    if row['status'] != 'unsupported':
                        row['comparison_id'] = 'logcmp_'+digest({'batch': identity, 'source': row['source_id'], 'attempt': 0})[:32]
                        row['events'] = prepared[row['source_id']][2]['items']
                with db.session.begin() as session:
                    replay._put(session, body.project_id, BATCH, identity, batch)
            _submit_sources(db, registry, batch)
            with db.read_session() as session:
                return report_batch(db, session, batch)

    @api.get('/log-sim/batches', tags=['Log Sim'])
    def listing(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            batches = replay._values(session, project_id, BATCH)[:20]
            for batch in batches:
                _authorize_saved(db, request, batch)
            return {'items': [report_batch(db, session, batch) for batch in batches]}

    @api.get('/log-sim/batches/{identity}', tags=['Log Sim'])
    def read(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            batch = replay._get(session, project_id, BATCH, identity)
            _authorize_saved(db, request, batch)
            return report_batch(db, session, batch)

    @api.post('/log-sim/batches/{identity}/retry', status_code=202, tags=['Log Sim'])
    def retry(identity: str, body: BatchAction, request: Request,
              idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        with LOCK:
            with db.read_session() as session:
                batch = replay._get(session, body.project_id, BATCH, identity)
                _authorize_saved(db, request, batch, 'Engineer')
                current = report_batch(db, session, batch)
            retry_key = digest({'batch': identity, 'key': idempotency_key})
            if retry_key not in batch.get('retry_keys', []):
                for row, state in zip(batch['sources'], current['sources']):
                    if state['status'] in {'failed', 'cancelled', 'not_evaluated'}:
                        old_id = row['comparison_id']
                        with db.read_session() as session:
                            old_pair = replay._get(session, body.project_id, replay.COMPARISON, old_id)
                        row['attempts'].append(old_id)
                        new_id = 'logcmp_'+digest({'batch': identity, 'source': row['source_id'], 'retry': retry_key})[:32]
                        pair = copy.deepcopy(old_pair)
                        pair.update(id=new_id, execution_ids={}, created_at=utcnow())
                        with db.session.begin() as session:
                            replay._put(session, body.project_id, replay.COMPARISON, new_id, pair)
                        row.update(comparison_id=new_id, status='ready', reason=None)
                batch.setdefault('retry_keys', []).append(retry_key)
                with db.session.begin() as session:
                    replay._put(session, body.project_id, BATCH, identity, batch)
            _submit_sources(db, registry, batch)
            with db.read_session() as session:
                return report_batch(db, session, batch)

    @api.post('/log-sim/batches/{identity}/case-bundles', status_code=201, tags=['Log Sim'])
    def save_bundle(identity: str, body: BundleCreate, request: Request,
                    idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        bundle_id = 'logbundle_'+digest({'project': body.project_id, 'key': idempotency_key})[:32]
        payload = body.model_dump() | {'batch_id': identity}
        with LOCK:
            with db.read_session() as session:
                old = log_sim._catalog(session, body.project_id, BUNDLE, bundle_id)
                batch = replay._get(session, body.project_id, BATCH, identity)
                _authorize_saved(db, request, batch, 'Engineer')
                report = report_batch(db, session, batch)
            if old:
                bundle = old['definition']
                if bundle['request'] != payload:
                    raise HTTPException(409, 'Idempotency key identifies another Case bundle')
                if bundle['status'] == 'completed':
                    return bundle
            else:
                if report['status'] in {'queued', 'running'} or report['report_hash'] != body.report_hash:
                    raise HTTPException(409, 'Use the current finished batch report before selecting Cases')
                candidates = {value['id']: value for value in report['candidates']}
                if any(key not in candidates for key in body.candidate_ids):
                    raise HTTPException(422, 'Selected candidate is not an evaluated member of this batch')
                bundle = dict(schema_version='log-sim-case-bundle/1', id=bundle_id, project_id=body.project_id,
                    batch_id=identity, name=body.name, created_at=utcnow(), request=payload,
                    candidates=[candidates[key] for key in body.candidate_ids], items=[], status='saving',
                    report_hash=body.report_hash, review_status='unreviewed', verdict='not_evaluated')
                with db.session.begin() as session:
                    replay._put(session, body.project_id, BUNDLE, bundle_id, bundle)
            sources = {row['source_id']: row for row in batch['sources']}
            for candidate in bundle['candidates']:
                if any(item['candidate_id'] == candidate['id'] for item in bundle['items']):
                    continue
                row = sources[candidate['source_id']]
                with db.read_session() as session:
                    current = log_sim._source(session, body.project_id, row['source_id'])
                if current['source_hash'] != row['source_hash']:
                    raise HTTPException(409, 'Original source changed before saving this Case')
                ref = row['recording_ref']
                origin = int(ref['time_origin_ns'])
                selection = RecordingSelection(recording_id=ref['id'], channel_id=ref['ego_channel_id'],
                    start_time_ns=str(origin+round(candidate['time_s']*1e9)),
                    end_time_ns=str(origin+round(candidate['end_time_s']*1e9)))
                note = f"Log Sim batch {identity}; candidate {candidate['id']}; source {row['source_hash']}; report {body.report_hash}. Recorded-object reference open-loop investigation, unreviewed; safety not evaluated."
                case = cases.create_case(db, CaseCreate(title=(row['scene_name']+' · '+candidate['label'])[:180],
                    note=note, source=selection), 'log-bundle:'+digest({'bundle': bundle_id, 'candidate': candidate['id']}))
                log_sim._store(db, body.project_id, log_sim.CASE_KIND, case['id'], dict(source_id=row['source_id'],
                    source_hash=row['source_hash'], case_id=case['id'], case_revision=case['revision'], case_manifest_hash=case['manifest_hash'], comparison_id=candidate['comparison_id'], candidate_id=candidate['id'], report_hash=body.report_hash))
                bundle['items'].append(dict(candidate_id=candidate['id'], source_id=row['source_id'],
                    case_id=case['id'], case_revision=case['revision'], case_manifest_hash=case['manifest_hash'],
                    title=case['title'], time_s=candidate['time_s'], end_time_s=candidate['end_time_s'],
                    comparison_id=candidate['comparison_id']))
                with db.session.begin() as session:
                    replay._put(session, body.project_id, BUNDLE, bundle_id, bundle)
            bundle['status'] = 'completed'
            with db.session.begin() as session:
                replay._put(session, body.project_id, BUNDLE, bundle_id, bundle)
            return bundle

    @api.get('/log-sim/case-bundles', tags=['Log Sim'])
    def bundles(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = replay._values(session, project_id, BUNDLE)[:20]
        for value in values:
            _bundle_access(db, request, value)
        return {'items': values}

    @api.get('/log-sim/case-bundles/{identity}', tags=['Log Sim'])
    def bundle_detail(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            value = replay._get(session, project_id, BUNDLE, identity)
        _bundle_access(db, request, value)
        return value
