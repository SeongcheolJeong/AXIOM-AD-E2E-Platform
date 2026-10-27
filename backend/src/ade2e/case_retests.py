"""Case → authored object-reference test → ordinary campaign → captured evidence.

The log is a provenance anchor, NOT an input to the reference simulator. Manual
scene tags are never promoted to measured sensor/ODD coverage. All writes that
combine authoring, queue submission and case links share one SQLite transaction.
"""
from collections import Counter
from copy import deepcopy
from typing import Literal
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Response
from pydantic import Field, field_validator, field_serializer, model_validator
from sqlalchemy import select

from . import jobs, suites
from .base_schemas import StrictModel
from .case_models import CaseRevisionRow, CaseRow, SegmentRow
from .case_schemas import Text
from .cases import _get, _publish, _sealed
from .reference import ReferenceAdapter
from .scenario_schemas import ScenarioCreate, ScenarioParameters, ParameterOverrides, MetricName, Operator
from .schemas import RequirementCreate
from .service import canonical_bytes, digest, scenario_create, requirement_create, uid, utcnow
from .suite_schemas import SuiteCreate, CampaignCreate, BaselineCreate

PLAN_VERSION = 'axiom-case-retest/1'
MAX_PLANS = 8
MAX_ATTEMPTS = 8
MAX_REPORT_BYTES = 16 * 1024 * 1024
TEMPLATES = {
    'reference-follow/1': ('lane_follow', 'Object following'),
    'reference-lead-brake/1': ('emergency_brake', 'Lead braking'),
    'reference-crossing/1': ('pedestrian_crossing', 'Object crossing'),
}
SCOPE = ('Manually authored object-reference test, inspired by the pinned case. '
         'No original sensor bytes, learned model or source lighting/image-size condition is simulated. '
         'A pass is not resolution of the source perception failure or sensor/ODD coverage.')


class Criterion(StrictModel):
    name: Text
    metric_name: MetricName
    operator: Operator
    threshold: float = Field(ge=0, le=1e6)


class Variant(StrictModel):
    key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
    name: Text
    seed: int = Field(ge=0, le=2**31-1, strict=True)
    parameter_overrides: ParameterOverrides = Field(default_factory=ParameterOverrides)

    @field_serializer('parameter_overrides')
    def serialize_overrides(self, value):
        return value.model_dump(mode='json', exclude_none=True)


class RetestCreate(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    name: Text
    note: str = Field(min_length=1, max_length=2000)
    template_id: Literal['reference-follow/1', 'reference-lead-brake/1', 'reference-crossing/1']
    duration_s: float = Field(ge=2, le=30)
    parameters: ScenarioParameters
    variants: list[Variant] = Field(min_length=1, max_length=8)
    criteria: list[Criterion] = Field(min_length=1, max_length=8)
    acknowledge_reference_only: Literal[True]

    @field_validator('acknowledge_reference_only', mode='before')
    @classmethod
    def explicit_acknowledgment(cls, value):
        if value is not True:
            raise ValueError('Explicit acknowledgment of reference-only limitations is required')
        return value

    @field_validator('note')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Explain the hypothesis and assumptions; logs are not reconstructed')
        return value.strip()

    @model_validator(mode='after')
    def bounded_definition(self):
        if len({v.key for v in self.variants}) != len(self.variants):
            raise ValueError('Variant keys must be unique')
        if len({v.name for v in self.criteria}) != len(self.criteria):
            raise ValueError('Criterion names must be unique')
        for v in self.variants:
            p = {**self.parameters.model_dump(), **v.parameter_overrides.model_dump(exclude_none=True)}
            ScenarioCreate(name=self.name, category=TEMPLATES[self.template_id][0], parameters=p,
                           duration_s=self.duration_s, seed=v.seed)
        return self


class RetestAction(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def templates():
    return {'schema_version': PLAN_VERSION, 'adapter': ReferenceAdapter().metadata().model_dump(mode='json'),
        'templates': [{'id': k, 'category': v[0], 'name': v[1]} for k, v in TEMPLATES.items()],
        'create_schema': RetestCreate.model_json_schema(), 'scope': SCOPE,
        'max_plans_per_case': MAX_PLANS, 'max_attempts_per_plan': MAX_ATTEMPTS,
        'source_reconstruction': False, 'sensor_conditions_verified': False}


def _plans(case):
    return case.get('retests', [])


def validate_links(session, case):
    """Check pins when loading a case; never execute or read the simulator."""
    plans = _plans(case)
    if not plans:
        return
    try:
        if (case['schema_version'] not in ('axiom-investigation-case/3', 'axiom-investigation-case/4', 'axiom-investigation-case/5') or not isinstance(plans, list)
                or len(plans) > MAX_PLANS or len({p['id'] for p in plans}) != len(plans)):
            raise ValueError('Invalid plan list')
        for plan in plans:
            definition = plan['definition']
            source = definition['source']
            prior = session.get(CaseRevisionRow, (case['id'], source['case_revision']))
            if (plan['definition_hash'] != digest(definition) or definition['schema_version'] != PLAN_VERSION
                    or source['case_id'] != case['id'] or source['segment_hash'] != case['segment_hash']
                    or not 1 <= source['case_revision'] < case['revision'] or prior is None
                    or source['case_manifest_hash'] != prior.manifest_hash):
                raise ValueError('Plan source/definition mismatch')
            old = _sealed(prior)
            if definition['source_conditions'] != old.get('conditions'):
                raise ValueError('Plan conditions differ from the source revision')
            request = RetestCreate.model_validate(definition['request'])
            if request.expected_revision != source['case_revision'] or definition['scope'] != SCOPE:
                raise ValueError('Plan contract differs')
            suite = suites.suite_get(session, definition['suite']['id'], definition['suite']['revision'])
            if suite.model_dump(mode='json') != definition['suite'] or definition['adapter'] != ReferenceAdapter().metadata().model_dump(mode='json'):
                raise ValueError('Suite or adapter pin differs')
            attempts = plan['attempts']
            if len(attempts) > MAX_ATTEMPTS or len({a['campaign_id'] for a in attempts}) != len(attempts):
                raise ValueError('Invalid attempts')
            for attempt in attempts:
                campaign = suites._campaign_row(session, attempt['campaign_id'])
                if (campaign.suite_snapshot != definition['suite'] or campaign.adapter_snapshot != definition['adapter']
                        or campaign.request_hash != attempt['request_hash']):
                    raise ValueError('Campaign differs from its plan')
                if attempt['capture']:
                    captured = suites.baseline_get(session, attempt['capture']['id'])
                    if captured.manifest_hash != attempt['capture']['manifest_hash'] or captured.campaign_id != campaign.id:
                        raise ValueError('Captured report differs')
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, 'Retest source, definition or execution link failed its integrity check') from error


def _new_revision(prior):
    value = deepcopy({k: v for k, v in prior.items() if k not in ('manifest_hash', 'manifest_size_bytes', 'download_url')})
    value.update(schema_version='axiom-investigation-case/5' if prior.get('stack_replays') else 'axiom-investigation-case/4' if prior.get('model_replays') else 'axiom-investigation-case/3', revision=prior['revision']+1,
                 created_at=utcnow(), previous_manifest_hash=prior['manifest_hash'])
    value.setdefault('retests', [])
    return value


def _retry(session, case_id, operation, body, key):
    request_hash = digest({'case_id': case_id, 'operation': operation, 'body': body.model_dump(mode='json')})
    storage_key = 'retests:' + digest({'case_id': case_id, 'operation': operation, 'key': key})
    old = session.scalar(select(CaseRevisionRow).where(CaseRevisionRow.request_key == storage_key))
    if old and (old.case_id != case_id or old.request_hash != request_hash):
        raise HTTPException(409, 'Idempotency-Key identifies a different retest request')
    return (_get(session, case_id, old.revision) if old else None), storage_key, request_hash


def _current(session, case_id, expected_revision):
    case = _get(session, case_id)
    if case['revision'] != expected_revision:
        raise HTTPException(409, 'Case revision changed; reload before creating or running a retest')
    return case


def _plan(case, plan_id, expected_hash=None):
    value = next((p for p in _plans(case) if p['id'] == plan_id), None)
    if value is None:
        raise HTTPException(404, 'Retest plan is not linked to this case revision')
    if expected_hash is not None and value['definition_hash'] != expected_hash:
        raise HTTPException(409, 'Retest plan hash differs; reload the exact plan')
    return value


def create_plan(db, case_id, body, key):
    with jobs._write(db) as session:
        retry, storage_key, request_hash = _retry(session, case_id, 'create', body, key)
        if retry:
            return retry
        prior = _current(session, case_id, body.expected_revision)
        if len(_plans(prior)) >= MAX_PLANS:
            raise HTTPException(413, 'Case already has 8 retest plans; no partial definition is created')
        scenario = scenario_create(session, ScenarioCreate(name=body.name, category=TEMPLATES[body.template_id][0],
            description=SCOPE + '\n' + body.note, duration_s=body.duration_s, parameters=body.parameters,
            seed=body.variants[0].seed, tags=['case-derived', 'reference-only']))
        criteria = [requirement_create(session, RequirementCreate(**c.model_dump(), scenario_ids=[scenario.id],
            description='Explicit user criterion for this authored reference test; not a regulatory threshold.')).id for c in body.criteria]
        suite = suites.create_suite_in_session(session, SuiteCreate(name=body.name, description=SCOPE,
            cases=[dict(key=v.key, name=v.name, scenario_id=scenario.id, scenario_revision=1, seed=v.seed,
                parameter_overrides=v.parameter_overrides.model_dump(exclude_none=True), requirement_ids=criteria) for v in body.variants]))
        definition = {'schema_version': PLAN_VERSION, 'source': {'case_id': case_id, 'case_revision': prior['revision'],
            'case_manifest_hash': prior['manifest_hash'], 'segment_hash': prior['segment_hash']},
            'source_conditions': deepcopy(prior.get('conditions')), 'request': body.model_dump(mode='json'),
            'suite': suite.model_dump(mode='json'), 'adapter': ReferenceAdapter().metadata().model_dump(mode='json'),
            'scope': SCOPE, 'source_reconstruction': False, 'sensor_conditions_verified': False}
        value = _new_revision(prior)
        value['retests'].append({'id': uid('retest'), 'definition': definition, 'definition_hash': digest(definition), 'attempts': []})
        return _publish(session, session.get(CaseRow, case_id), value, storage_key, request_hash)


def launch(db, registry, case_id, plan_id, body, key):
    with jobs._write(db) as session:
        retry, storage_key, request_hash = _retry(session, case_id, 'launch:' + plan_id, body, key)
        if retry:
            return retry
        prior = _current(session, case_id, body.expected_revision)
        plan = _plan(prior, plan_id, body.plan_hash)
        if len(plan['attempts']) >= MAX_ATTEMPTS:
            raise HTTPException(413, 'Retest plan already has 8 attempts')
        # Excludes a replacement plugin masquerading as the reference engine.
        adapter = registry.get('reference')
        if type(adapter) is not ReferenceAdapter or adapter.metadata().model_dump(mode='json') != plan['definition']['adapter']:
            raise HTTPException(422, 'The pinned built-in object-reference adapter is not available')
        suite = plan['definition']['suite']
        request = CampaignCreate(suite_id=suite['id'], suite_revision=suite['revision'], adapter_id='reference', label=suite['name'])
        report = suites.create_campaign_in_session(session, registry, request, 'case-retest:' + digest(storage_key))
        value = _new_revision(prior)
        target = _plan(value, plan_id)
        target['attempts'].append({'campaign_id': report.id, 'request_hash': digest(request.model_dump(mode='json')),
            'members': [{'key': c.key, 'job_id': c.job_id} for c in report.cases], 'capture': None})
        # The worker cannot observe committed jobs without this committed link.
        return _publish(session, session.get(CaseRow, case_id), value, storage_key, request_hash)


def _attempt_report(session, plan, attempt):
    captured = attempt['capture']
    report = (suites.baseline_get(session, captured['id']).manifest.report if captured
              else suites.campaign_report(session, attempt['campaign_id']))
    if (report.suite_snapshot.model_dump(mode='json') != plan['definition']['suite']
            or report.adapter_snapshot.model_dump(mode='json') != plan['definition']['adapter']
            or [{'key': c.key, 'job_id': c.job_id} for c in report.cases] != attempt['members']):
        raise HTTPException(409, 'Retest campaign membership or definition differs from the pinned submission')
    payload = report.model_dump(mode='json')
    # Read-time timestamp is not evidence; stable content hashes reflect changes only.
    payload.pop('evaluated_at', None)
    return {**attempt, 'report': payload, 'evidence_mode': 'captured' if captured else 'live',
            'sensor_condition_coverage_eligible': False}


def capture(db, case_id, plan_id, campaign_id, body, key):
    with jobs._write(db) as session:
        retry, storage_key, request_hash = _retry(session, case_id, 'capture:' + plan_id + ':' + campaign_id, body, key)
        if retry:
            return retry
        prior = _current(session, case_id, body.expected_revision)
        plan = _plan(prior, plan_id, body.plan_hash)
        attempt = next((a for a in plan['attempts'] if a['campaign_id'] == campaign_id), None)
        if attempt is None:
            raise HTTPException(404, 'Campaign is not part of this case retest')
        if attempt['capture']:
            raise HTTPException(409, 'This outcome is already captured; no overwrite is performed')
        _attempt_report(session, plan, attempt)
        baseline = suites.create_baseline_in_session(session, BaselineCreate(campaign_id=campaign_id,
            name=plan['definition']['request']['name'], note='Case retest outcome capture, including failures/incomplete evidence. ' + SCOPE),
            'case-retest-capture:' + digest(storage_key))
        value = _new_revision(prior)
        target = next(a for a in _plan(value, plan_id)['attempts'] if a['campaign_id'] == campaign_id)
        target['capture'] = {'id': baseline.id, 'manifest_hash': baseline.manifest_hash}
        return _publish(session, session.get(CaseRow, case_id), value, storage_key, request_hash)


def report(session, case_id, revision=None):
    case = _get(session, case_id, revision)
    return {'schema_version': 'axiom-case-retest-report/1', 'case_id': case['id'], 'case_revision': case['revision'],
        'case_manifest_hash': case['manifest_hash'], 'case_state': case['state'], 'scope': SCOPE,
        'plans': [{**p, 'attempts': [_attempt_report(session, p, a) for a in p['attempts']]} for p in _plans(case)]}


def activity(session, recording_id=None):
    """All planned variants and attempts; never fills scene-condition coverage."""
    if recording_id:
        from .recordings import _get_recording
        _get_recording(session, recording_id)
    query = select(CaseRow).join(SegmentRow, SegmentRow.id == CaseRow.segment_id).order_by(CaseRow.id)
    if recording_id:
        query = query.where(SegmentRow.recording_id == recording_id)
    heads = list(session.scalars(query.limit(1001)))
    if len(heads) > 1000:
        raise HTTPException(413, 'Retest activity is limited to 1000 cases; narrow the recording scope')
    rows, plans, total_attempts, size = [], 0, 0, 0
    for head in heads:
        case = _get(session, head.id)
        size += case['manifest_size_bytes']
        if size > 32 * 1024 * 1024:
            raise HTTPException(413, 'Case evidence exceeds the activity snapshot bound')
        for p in _plans(case):
            plans += 1
            definition = p['definition']; states = Counter(); verdicts = Counter()
            attempts = []
            for a in p['attempts']:
                total_attempts += 1
                if total_attempts > 256:
                    raise HTTPException(413, 'Activity supports 256 attempts; narrow the recording scope')
                outcome = _attempt_report(session, p, a)
                for c in outcome['report']['cases']:
                    states[c['job_status']] += 1; verdicts[c['verdict']] += 1
                attempts.append({'campaign_id': a['campaign_id'], 'evidence_mode': outcome['evidence_mode'],
                    'status': outcome['report']['status'], 'verdict': outcome['report']['verdict'],
                    'counts': outcome['report']['counts'], 'report_hash': digest(outcome['report'])})
            rows.append({'case_id': head.id, 'case_revision': case['revision'], 'case_manifest_hash': case['manifest_hash'],
                'plan_id': p['id'], 'name': definition['request']['name'], 'definition_hash': p['definition_hash'],
                'source_case_revision': definition['source']['case_revision'], 'source_conditions': definition['source_conditions'],
                'planned_variants': len(definition['suite']['cases']), 'attempts': attempts,
                'job_states': dict(states), 'test_verdicts': dict(verdicts), 'sensor_condition_coverage_eligible': False})
    return {'schema_version': 'axiom-case-retest-activity/1', 'scope': SCOPE, 'recording_id': recording_id,
        'denominator': {'all_cases': len(heads), 'cases_with_plans': len({r['case_id'] for r in rows}),
            'plans': plans, 'planned_variants': sum(r['planned_variants'] for r in rows), 'attempts': total_attempts,
            'never_submitted_plans': sum(not r['attempts'] for r in rows), 'verified_sensor_condition_cells': 0},
        'rows': rows, 'independent_scenes_claimed': False,
        'conditions_meaning': 'Source conditions are pinned provenance, not conditions reproduced by the test. Repeated attempts are not new coverage.'}


def _export_descriptor(payload, path, params):
    raw = canonical_bytes(payload)
    if len(raw) > MAX_REPORT_BYTES:
        raise HTTPException(413, 'Retest report exceeds 16 MiB')
    sha = digest(payload)
    return {**payload, 'manifest_hash': sha, 'manifest_size_bytes': len(raw),
        'download_url': path + '?' + urlencode({**params, 'expected_hash': sha})}


def register_retest_routes(api, db, registry):
    @api.get('/case-retest-templates', tags=['case retests'])
    def get_templates():
        return templates()

    @api.post('/cases/{case_id}/retests', status_code=201, tags=['case retests'])
    def post_plan(case_id: str, body: RetestCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return create_plan(db, case_id, body, idempotency_key)

    @api.post('/cases/{case_id}/retests/{plan_id}/campaigns', status_code=202, tags=['case retests'])
    def post_launch(case_id: str, plan_id: str, body: RetestAction, idempotency_key: str = Header(min_length=1, max_length=180)):
        return launch(db, registry, case_id, plan_id, body, idempotency_key)

    @api.post('/cases/{case_id}/retests/{plan_id}/campaigns/{campaign_id}/capture', status_code=201, tags=['case retests'])
    def post_capture(case_id: str, plan_id: str, campaign_id: str, body: RetestAction, idempotency_key: str = Header(min_length=1, max_length=180)):
        return capture(db, case_id, plan_id, campaign_id, body, idempotency_key)

    @api.get('/cases/{case_id}/retests', tags=['case retests'])
    def get_report(case_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            result = report(session, case_id, revision)
            return _export_descriptor(result, f'/api/v1/cases/{case_id}/retests/export', {'revision': result['case_revision']})

    @api.get('/cases/{case_id}/retests/export', tags=['case retests'])
    def export(case_id: str, revision: int = Query(..., ge=1), expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
        with db.read_session() as session:
            result = report(session, case_id, revision)
            checked = _export_descriptor(result, '', {})
            if checked['manifest_hash'] != expected_hash:
                raise HTTPException(409, 'Retest outcome changed; refresh or capture the finished result before export')
            return Response(canonical_bytes(result), media_type='application/json', headers={
                'Content-Disposition': 'attachment; filename="case-retests.json"', 'X-Content-Type-Options': 'nosniff'})

    @api.get('/case-retest-activity', tags=['case retests'])
    def get_activity(recording_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        with db.read_session() as session:
            return _export_descriptor(activity(session, recording_id), '/api/v1/case-retest-activity/export',
                                      {'recording_id': recording_id} if recording_id else {})

    @api.get('/case-retest-activity/export', tags=['case retests'])
    def export_activity(recording_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$'),
                        expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
        with db.read_session() as session:
            result = activity(session, recording_id)
            if _export_descriptor(result, '', {})['manifest_hash'] != expected_hash:
                raise HTTPException(409, 'Retest activity changed; refresh before export')
            return Response(canonical_bytes(result), media_type='application/json', headers={
                'Content-Disposition': 'attachment; filename="case-retest-activity.json"', 'X-Content-Type-Options': 'nosniff'})
