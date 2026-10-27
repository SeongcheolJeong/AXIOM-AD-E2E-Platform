"""Campaign-selected controls over authored adapters; no real CARLA execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ade2e import jobs, service, suites
from ade2e.job_models import JobRow
from ade2e.database import RunRow
from ade2e.schemas import RunCreate
from ade2e.suite_models import CampaignRow, CampaignMemberRow
from ade2e.suite_schemas import CampaignCreate
from ade2e.worker import run_once
from test_simulation_context import context
from test_stack_replays import definition
from test_suites import make_suite, finish, baseline


def submit(context, suite, *, key='control-campaign', control=None):
    body = {'suite_id': suite['id'], 'suite_revision': suite['revision'], 'label': key,
        'adapter_id': 'carla', 'control': control or context[3].control.model_dump(mode='json')}
    response = context[0].post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), body


def stable(report):
    return {k: v for k, v in report.items() if k != 'evaluated_at'}


def totals(db):
    with db.read_session() as session:
        return tuple(session.scalar(select(func.count()).select_from(t)) for t in (CampaignRow, CampaignMemberRow, JobRow))


def test_context1_full_evaluation_pins_shared_revisions_per_case_seed_and_failed_criterion(context):
    client, db, registry, _, adapter, stack, rig, rig_body = context
    suite, suite_body, scenario, _ = make_suite(client, threshold=1)
    suite_body['cases'][1]['parameter_overrides'] = {'ego_speed_mps': 4.0}
    revised = client.post(f"/api/v1/suites/{suite['id']}/revisions", json={**suite_body, 'expected_revision': 1})
    assert revised.status_code == 201, revised.text
    suite = revised.json()
    queued, body = submit(context, suite)
    plan = deepcopy(queued['execution_plan'])
    assert len({m['input_hash'] for m in plan['members']}) == 2
    assert len({m['context_hash'] for m in plan['members']}) == 2
    assert not adapter.requests and not adapter.preflights  # preparation never executes remote preflight
    client.post(f"/api/v1/stacks/{stack['id']}/revisions", json={**definition(), 'expected_revision': 1, 'note': 'new head'}).raise_for_status()
    client.post(f"/api/v1/sensor-rigs/{rig['id']}/revisions", json={**rig_body, 'expected_revision': 1, 'note': 'new head'}).raise_for_status()
    client.patch('/api/v1/scenarios/' + scenario['id'], json={'seed': 999}).raise_for_status()
    report = finish(client, queued)
    assert report['status'] == 'finished' and report['evaluation_complete']
    assert report['verdict'] == 'failed' and report['counts']['checks_failed'] == 2
    assert report['execution_plan'] == plan
    for i, (case, request) in enumerate(zip(report['cases'], adapter.requests)):
        assert request.scenario.seed == 40+i
        assert request.context.sensor_profile.attributes['noise_seed'] == str(40+i)
        assert request.context.stack_snapshot.revision == request.context.rig_snapshot.revision == 1
        assert case['execution_context']['control_context_hash'] == case['expected_execution']['context_hash']
        assert case['execution_context']['input_hash'] == case['expected_execution']['input_hash'] == request.input_hash
        assert case['checks'][0]['status'] == 'failed'
    assert adapter.requests[1].scenario.parameters.ego_speed_mps == 4
    registry._adapters.pop('carla')
    replay = client.post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': body['label']})
    assert replay.status_code == 202 and stable(replay.json()) == stable(report)
    body['control']['stack_revision'] = 2
    assert client.post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': body['label']}).status_code == 409


def test_campaign_resolves_assets_once_in_one_session_and_atomic_insert_rollback(context, monkeypatch):
    suite, *_ = make_suite(context[0])
    original = suites.resolve_simulation_control
    sessions = []
    def resolve(session, selection):
        sessions.append(session)
        return original(session, selection)
    monkeypatch.setattr(suites, 'resolve_simulation_control', resolve)
    insert, calls = jobs.insert_prepared_job, []
    before = totals(context[1])
    def fail_second(session, *args, **kwargs):
        calls.append(session)
        if len(calls) == 2:
            raise RuntimeError('second member insert failed')
        return insert(session, *args, **kwargs)
    monkeypatch.setattr(jobs, 'insert_prepared_job', fail_second)
    with pytest.raises(RuntimeError, match='second member'):
        submit(context, suite)
    assert len(sessions) == 1 and all(s is sessions[0] for s in calls)
    assert totals(context[1]) == before


def test_later_unsupported_case_has_no_jobs_or_campaign(context):
    client, db = context[:2]
    suite, body, *_ = make_suite(client)
    unsupported = client.post('/api/v1/scenarios', json={'name': 'Unsupported junction', 'category': 'pedestrian_crossing', 'duration_s': 2, 'parameters': {'trigger_time_s': 1}}).json()
    criterion = client.post('/api/v1/requirements', json={'name': 'Collision', 'metric_name': 'collision_count', 'operator': 'eq', 'threshold': 0, 'scenario_ids': [unsupported['id']]}).json()
    body['cases'][1].update(scenario_id=unsupported['id'], requirement_ids=[criterion['id']])
    response = client.post('/api/v1/suites', json=body)
    assert response.status_code == 201, response.text
    suite = response.json()
    before = totals(db)
    body = {'suite_id': suite['id'], 'suite_revision': 1, 'label': 'unsupported', 'adapter_id': 'carla', 'control': context[3].control.model_dump(mode='json')}
    response = client.post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': 'unsupported'})
    assert response.status_code == 422 and 'case-1' in response.text
    assert totals(db) == before and not context[4].requests and not context[4].preflights


def test_control_requires_carla_and_missing_revision_inserts_nothing(context):
    suite, *_ = make_suite(context[0])
    before = totals(context[1])
    body = {'suite_id': suite['id'], 'suite_revision': 1, 'label': 'bad', 'control': context[3].control.model_dump(mode='json')}
    assert context[0].post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': 'bad'}).status_code == 422
    body['adapter_id'] = 'carla'; body['control']['rig_revision'] = 999
    assert context[0].post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': 'bad'}).status_code == 404
    assert totals(context[1]) == before


def test_concurrent_identical_submission_has_one_independent_plan(context):
    suite, *_ = make_suite(context[0])
    body = CampaignCreate(suite_id=suite['id'], suite_revision=1, label='concurrent', adapter_id='carla', control=context[3].control)
    before = totals(context[1])
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(lambda _: suites.create_campaign(context[1], context[2], body, 'concurrent'), range(3)))
    assert len({r.id for r in reports}) == 1
    assert all(r.execution_plan == reports[0].execution_plan for r in reports)
    assert totals(context[1]) == tuple(a+b for a,b in zip(before, (1,2,2)))


@pytest.mark.parametrize('mutation', ['plan_hash', 'missing_plan', 'unexpected_member'])
def test_plan_tamper_cannot_be_reported_or_sealed(context, mutation):
    suite, *_ = make_suite(context[0])
    report, _ = submit(context, suite)
    with context[1].session.begin() as session:
        row = session.scalar(select(CampaignRow).where(CampaignRow.id == report['id']))
        if mutation == 'missing_plan': row.execution_plan = None
        elif mutation == 'plan_hash': row.execution_plan = {**row.execution_plan, 'plan_hash': '0'*64}
    if mutation == 'unexpected_member':
        other = jobs.submit_job(context[1], context[2], RunCreate(scenario_id=context[3].scenario_id), 'outside')
        with context[1].session.begin() as session:
            session.add(CampaignMemberRow(campaign_id=report['id'], case_key='unexpected', job_id=other.id))
    assert context[0].get('/api/v1/campaigns/'+report['id']).status_code == 409
    assert context[0].post('/api/v1/baselines', json={'campaign_id': report['id'], 'name': 'Rejected', 'note': 'Rejected tampered source'}, headers={'Idempotency-Key': 'reject'}).status_code == 409


def test_missing_member_retains_expected_identity_and_full_denominator(context):
    suite, *_ = make_suite(context[0])
    report, _ = submit(context, suite)
    with context[1].session.begin() as session:
        row = session.get(CampaignMemberRow, (report['id'], 'case-1'))
        session.delete(row)
    result = context[0].get('/api/v1/campaigns/'+report['id']).json()
    assert result['counts']['total'] == result['counts']['checks_total'] == 2
    assert result['counts']['missing'] == 1
    assert result['cases'][1]['expected_execution'] == report['execution_plan']['members'][1]
    assert result['cases'][1]['checks'][0]['status'] == 'not_evaluated'


def test_job_and_run_consistently_changed_do_not_replace_campaign_intent(context):
    client, db, _, _, _, stack, *_ = context
    suite, *_ = make_suite(client, count=1)
    first, _ = submit(context, suite)
    first = finish(client, first)
    client.post(f"/api/v1/stacks/{stack['id']}/revisions", json={**definition(), 'expected_revision': 1, 'note': 'different intent'}).raise_for_status()
    control = context[3].control.model_dump(mode='json'); control['stack_revision'] = 2
    other, _ = submit(context, suite, key='other', control=control)
    other = finish(client, other)
    with db.session.begin() as session:
        target = session.scalar(select(JobRow).where(JobRow.id == first['cases'][0]['job_id']))
        source = session.scalar(select(JobRow).where(JobRow.id == other['cases'][0]['job_id']))
        for field in ('prepared', 'request', 'request_hash', 'input_hash', 'run_id'):
            setattr(target, field, deepcopy(getattr(source, field)))
    result = client.get('/api/v1/campaigns/'+first['id']).json()
    assert result['verdict'] == 'incomplete' and not result['evaluation_complete']
    assert 'independently pinned' in result['cases'][0]['reason']
    assert result['cases'][0]['checks'][0]['status'] == 'not_evaluated'


def test_context_changes_are_not_comparable_and_old_baseline_bytes_remain(context):
    client, _, _, _, _, stack, *_ = context
    suite, *_ = make_suite(client, count=1, threshold=1)
    first, _ = submit(context, suite)
    first = finish(client, first); sealed = baseline(client, first)
    original = client.get(sealed['download_url']).content
    same, _ = submit(context, suite, key='same'); same = finish(client, same)
    compare = client.get(f"/api/v1/campaigns/{same['id']}/compare", params={'baseline_id': sealed['id']}).json()
    assert compare['counts']['unchanged_fail'] == 1
    client.post(f"/api/v1/stacks/{stack['id']}/revisions", json={**definition(), 'expected_revision': 1, 'note': 'new pinned stack'}).raise_for_status()
    control = context[3].control.model_dump(mode='json'); control['stack_revision'] = 2
    changed, _ = submit(context, suite, key='changed', control=control); changed = finish(client, changed)
    compare = client.get(f"/api/v1/campaigns/{changed['id']}/compare", params={'baseline_id': sealed['id']}).json()
    assert compare['counts']['not_comparable'] == 1
    assert client.get(sealed['download_url']).content == original


def test_context2_native_authored_campaign_pins_recipe_and_mount_and_evaluates(context, monkeypatch):
    """Real core queue and optional adapter over fake World; no simulator claims."""
    pytest.importorskip('axiom_carla.runner')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'integrations/carla/tests'))
    from test_configured_sensor_live import install_configured_world
    from axiom_carla.runner import CarlaRunner
    from axiom_carla.remote import RemoteCarlaAdapter
    class AuthoredAdapter(CarlaRunner):
        def metadata(self):
            return RemoteCarlaAdapter('http://authored.invalid').metadata().model_copy(update={
                'name': 'Authored configured-world fixture', 'description': 'Protocol fixture; no simulator'})
    client, db, registry, _, _, _, rig, rig_body = context
    registry.register(AuthoredAdapter(), replace=True)
    rig_body = deepcopy(rig_body); rig_body['sensors'][0]['transform']['translation_m'] = [2.5,.4,1.5]
    revised = client.post(f"/api/v1/sensor-rigs/{rig['id']}/revisions", json={**rig_body, 'expected_revision': 1})
    assert revised.status_code == 201, revised.text
    recipe_body = {'name': 'Campaign explicit sensor', 'note': 'Authored recipe for testing', 'parameters': {'horizontal_fov': 180.0}}
    recipe = client.post('/api/v1/simulation-sensor-configurations', json=recipe_body, headers={'Idempotency-Key': 'recipe'})
    assert recipe.status_code == 201, recipe.text
    recipe = recipe.json()
    control = context[3].control.model_dump(mode='json'); control.pop('sensor_profile_id')
    control.update(schema_version='simulation-control-selection/2', rig_revision=2,
        sensor_configuration_id=recipe['id'], sensor_configuration_revision=1)
    suite, suite_body, scenario, _ = make_suite(client, threshold=1)
    changed = client.patch('/api/v1/scenarios/'+scenario['id'], json={'map_name': 'Town01',
        'parameters': {'ego_speed_mps': 2, 'target_speed_mps': 2}})
    assert changed.status_code == 200, changed.text
    for case in suite_body['cases']: case['scenario_revision'] = changed.json()['revision']
    response = client.post('/api/v1/suites', json=suite_body)
    assert response.status_code == 201, response.text
    suite = response.json()
    queued, _ = submit(context, suite, control=control)
    plan = queued['execution_plan']
    client.post(f"/api/v1/simulation-sensor-configurations/{recipe['id']}/revisions", json={**recipe_body, 'expected_revision': 1, 'parameters': {'horizontal_fov': 180, 'range': 90}}, headers={'Idempotency-Key': 'recipe-r2'}).raise_for_status()
    for case in queued['cases']:
        install_configured_world(monkeypatch)
        result = run_once(db, registry, 'authored-campaign-worker', job_id=case['job_id'])
        assert result.status == 'completed', result
    report = client.get('/api/v1/campaigns/'+queued['id']).json()
    assert report['evaluation_complete'] and report['verdict'] == 'failed', [(c['reason'], [(x['reason'], x['unit']) for x in c['checks']]) for c in report['cases']]
    assert report['execution_plan'] == plan
    for case in report['cases']:
        run = client.get('/api/v1/runs/'+case['run_id']).json()
        actual = run['execution_context']
        assert actual['schema_version'] == 'simulation-control-context/2'
        assert actual['rig_snapshot']['revision'] == 2 and actual['sensor_configuration_snapshot']['revision'] == 1
        assert actual['sensor_profile']['mount']['translation_m'] == [2.5,.4,1.5]
        assert actual['context_hash'] == case['expected_execution']['context_hash']
        assert any(a['kind'] == 'sensor_configuration' for a in run['artifacts'])
        assert case['checks'][0]['measurement_contract'] == 'carla-observed-contact-count/1'
        assert case['checks'][0]['unit'] == 'observed contacts' and case['checks'][0]['expected_unit'] == 'count'
    sealed = baseline(client, report)
    assert client.get(sealed['download_url']).status_code == 200


def test_cancel_cannot_affect_substituted_job_and_missing_member_is_allowed(context):
    suite, *_ = make_suite(context[0])
    report, _ = submit(context, suite)
    outside = jobs.submit_job(context[1], context[2], RunCreate(scenario_id=context[3].scenario_id), 'cancel-outside')
    with context[1].session.begin() as session:
        member = session.get(CampaignMemberRow, (report['id'], 'case-0'))
        member.job_id = outside.id
    response = context[0].post(f"/api/v1/campaigns/{report['id']}/cancel")
    assert response.status_code == 409, response.text
    assert context[0].get('/api/v1/jobs/'+outside.id).json()['status'] == 'queued'
    assert context[0].get('/api/v1/jobs/'+report['cases'][1]['job_id']).json()['status'] == 'queued'
    with context[1].session.begin() as session:
        member = session.get(CampaignMemberRow, (report['id'], 'case-0'))
        session.delete(member)
    response = context[0].post(f"/api/v1/campaigns/{report['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()['counts']['missing'] == response.json()['counts']['cancelled'] == 1
    assert context[0].get('/api/v1/jobs/'+outside.id).json()['status'] == 'queued'


def test_legacy_request_report_and_manifest_omit_new_fields_exactly(context):
    client = context[0]
    suite, *_ = make_suite(client, count=1)
    body = {'suite_id': suite['id'], 'suite_revision': 1, 'adapter_id': 'reference', 'label': 'legacy'}
    assert CampaignCreate(**body, control=None).model_dump(mode='json') == body
    first = client.post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': 'legacy'})
    assert first.status_code == 202
    report = finish(client, first.json())
    assert 'execution_plan' not in report and 'expected_execution' not in report['cases'][0]
    with context[1].read_session() as session:
        row = session.scalar(select(CampaignRow).where(CampaignRow.id == report['id']))
        assert row.request_hash == service.digest(body) and row.execution_plan is None
    sealed = baseline(client, report)
    original = client.get(sealed['download_url']).content
    assert b'execution_plan' not in original and b'expected_execution' not in original and b'measurement_contract' not in original
    context[2]._adapters.clear()
    repeated = client.post('/api/v1/campaigns', json={**body, 'control': None}, headers={'Idempotency-Key': 'legacy'})
    assert repeated.status_code == 202 and stable(repeated.json()) == stable(report)
    assert client.get(sealed['download_url']).content == original


def test_legacy_campaign_cannot_evaluate_unselected_context_even_valid_run(context):
    client, db = context[:2]
    suite, *_ = make_suite(client, count=1)
    body = {'suite_id': suite['id'], 'suite_revision': 1, 'adapter_id': 'carla', 'label': 'old'}
    queued = client.post('/api/v1/campaigns', json=body, headers={'Idempotency-Key': 'old'}).json()
    controlled, _ = submit(context, suite)
    controlled = finish(client, controlled)
    with db.session.begin() as session:
        target = session.scalar(select(JobRow).where(JobRow.id == queued['cases'][0]['job_id']))
        source = session.scalar(select(JobRow).where(JobRow.id == controlled['cases'][0]['job_id']))
        target.status, target.run_id, target.input_hash = 'completed', source.run_id, source.input_hash
    report = client.get('/api/v1/campaigns/'+queued['id']).json()
    assert not report['evaluation_complete'] and report['verdict'] == 'incomplete'
    assert report['cases'][0]['reason'] == 'This campaign revision does not pin a stack control selection'


def test_agent_contract_exposes_optional_control_and_required_key(context):
    response = context[0].get('/api/v1/agent/tools')
    assert response.status_code == 200, response.text
    tools = response.json()
    item = next(t for t in tools['tools'] if t['name'] == 'submit_test_campaign') if isinstance(tools, dict) else next(t for t in tools if t['name'] == 'submit_test_campaign')
    assert item['required_headers'] == ['Idempotency-Key']
    assert 'control' in str(item)
    assert 'optional' in context[0].get('/llms.txt').text


def test_context2_last_case_exceeds_full_duration_budget_and_entire_campaign_is_rejected(context):
    from test_simulation_context import ContextAdapter
    from ade2e.simulation_sensor_capabilities import simulation_sensor_capabilities
    class CapabilityAdapter(ContextAdapter):
        def metadata(self): return super().metadata().model_copy(update={'version': '0.8.0'})
        def execution_context_capabilities(self): return simulation_sensor_capabilities()
    client, db, registry = context[:3]
    registry.register(CapabilityAdapter(db), replace=True)
    config = client.post('/api/v1/simulation-sensor-configurations', json={
        'name': 'Large but supported recipe', 'note': 'Capacity boundary fixture',
        'parameters': {'points_per_second': 128000}}, headers={'Idempotency-Key': 'large-recipe'})
    assert config.status_code == 201, config.text
    control = context[3].control.model_dump(mode='json'); control.pop('sensor_profile_id')
    control.update(schema_version='simulation-control-selection/2', sensor_configuration_id=config.json()['id'], sensor_configuration_revision=1)
    suite, body, scenario, _ = make_suite(client)
    changed = client.patch('/api/v1/scenarios/'+scenario['id'], json={'duration_s': 4})
    assert changed.status_code == 200
    body['cases'][1]['scenario_revision'] = changed.json()['revision']
    suite = client.post('/api/v1/suites', json=body).json()
    before = totals(db)
    response = client.post('/api/v1/campaigns', json={'suite_id': suite['id'], 'suite_revision': 1,
        'label': 'Full budget', 'adapter_id': 'carla', 'control': control}, headers={'Idempotency-Key': 'budget'})
    assert response.status_code == 422 and 'case-1' in response.text and 'full-run evidence budget' in response.text
    assert totals(db) == before


def test_null_case_seed_uses_saved_seed_without_changing_original_request_shape(context):
    client, db = context[:2]
    suite, body, scenario, _ = make_suite(client, count=1)
    body['cases'][0]['seed'] = None
    suite = client.post('/api/v1/suites', json=body).json()
    queued, _ = submit(context, suite)
    expected = queued['cases'][0]['expected_execution']
    with db.read_session() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == expected['job_id']))
        prepared = service.PreparedRun.from_payload(row.prepared)
        assert row.request.get('seed') is None
        assert prepared.scenario.seed == scenario['seed']
        assert prepared.execution_context.sensor_profile.attributes['noise_seed'] == str(scenario['seed'])
        assert service.run_request_hash(RunCreate.model_validate(row.request)) == expected['request_hash']


@pytest.mark.parametrize('unit,value,version,status,contract', [
    ('observed contacts', 0, '0.8.0', 'passed', True),
    ('observed contacts', 2, '0.8.0', 'failed', True),
    ('observed contacts', -1, '0.8.0', 'not_evaluated', False),
    ('observed contacts', .5, '0.8.0', 'not_evaluated', False),
    ('count', -1, '0.8.0', 'not_evaluated', False),
    ('count', .5, '0.8.0', 'not_evaluated', False),
    ('count', 0, '0.8.0', 'passed', False),
    ('contacts', 0, '0.8.0', 'not_evaluated', False),
    ('observed contacts', 0, '0.4.0', 'not_evaluated', False),
    ('observed contacts', 0, 'unknown-test-version', 'not_evaluated', False),
])
def test_contact_measurement_contract_is_narrow_and_preserves_raw_evidence(context, monkeypatch, unit, value, version, status, contract):
    from test_simulation_context import ContextAdapter
    from ade2e.reference import compare_metric
    def change_metric(result):
        collision = next(m for m in result.metrics if m.name == 'collision_count')
        collision.unit, collision.value = unit, value
        collision.passed = compare_metric(value, collision.operator, collision.threshold, name='collision_count')
        return result
    if version == '0.8.0':
        pytest.importorskip('axiom_carla.runner')
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'integrations/carla/tests'))
        from test_live_stack import install_live_world
        from axiom_carla.runner import CarlaRunner
        from axiom_carla.remote import RemoteCarlaAdapter
        install_live_world(monkeypatch)
        class ContactAdapter(CarlaRunner):
            def metadata(self):
                return RemoteCarlaAdapter('http://authored.invalid').metadata().model_copy(update={
                    'name': 'Authored metric boundary fixture', 'description': 'No simulator; adversarial returned metrics'})
            def execute_with_context(self, request): return change_metric(super().execute_with_context(request))
        adapter = ContactAdapter()
    else:
        class ContactAdapter(ContextAdapter):
            def metadata(self): return super().metadata().model_copy(update={'version': version})
            def execute_with_context(self, request): return change_metric(super().execute_with_context(request))
        adapter = ContactAdapter(context[1])
    context[2].register(adapter, replace=True)
    suite, body, scenario, _ = make_suite(context[0], count=1)
    changed = context[0].patch('/api/v1/scenarios/'+scenario['id'], json={'map_name':'Town01',
        'parameters':{'ego_speed_mps':2, 'target_speed_mps':2}}).json()
    body['cases'][0]['scenario_revision'] = changed['revision']
    suite = context[0].post('/api/v1/suites',json=body).json()
    queued, _ = submit(context, suite)
    report = finish(context[0], queued)
    assert report['cases'][0]['job_status'] == 'completed', report['cases'][0]['reason']
    check = report['cases'][0]['checks'][0]
    assert check['status'] == status
    assert check['unit'] == unit and check['value'] == value and check['expected_unit'] == 'count'
    assert ('measurement_contract' in check) == contract
    if contract: assert check['measurement_contract'] == 'carla-observed-contact-count/1'
    raw = next(m for m in report['cases'][0]['metrics'] if m['name'] == 'collision_count')
    assert raw['unit'] == unit and raw['value'] == value


def test_metric_contract_change_prevents_comparison_even_with_same_context(context):
    from ade2e.suite_schemas import CampaignDetail, BaselineDetail
    suite, *_ = make_suite(context[0], count=1)
    queued, _ = submit(context, suite)
    report = finish(context[0], queued)
    sealed = baseline(context[0], report)
    current = CampaignDetail.model_validate(report)
    prior = BaselineDetail.model_validate(sealed)
    # Same metric values and input/context hashes, different method attribution.
    current.cases[0].checks[0].measurement_contract = 'carla-observed-contact-count/1'
    comparison = suites.compare_reports(current, prior)
    assert comparison.counts.not_comparable == 1
    assert 'Pinned metric measurement contract changed' in comparison.rows[0].reasons
    current = CampaignDetail.model_validate(report)
    current.execution_plan = None
    comparison = suites.compare_reports(current, prior)
    assert comparison.counts.not_comparable == 1
    assert 'Pinned metric measurement contract changed' in comparison.rows[0].reasons
