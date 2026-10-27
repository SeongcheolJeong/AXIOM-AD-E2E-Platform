"""I3 executes the actual object-reference engine, not a camera or driving AI."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select, func

from ade2e import case_retests as retests, jobs
from ade2e.case_models import CaseRevisionRow, CaseRow
from ade2e.database import ScenarioRow, RequirementRow
from ade2e.job_models import JobRow
from ade2e.suite_models import SuiteRow, CampaignRow, CampaignMemberRow, BaselineRow
from ade2e.worker import run_once
from test_recordings import make_mcap


@pytest.fixture
def source_case(client):
    data, channel, *_ = make_mcap(messages=[(1, 100, b'{"speed":2}', 100, 0)])
    rec = client.post('/api/v1/recordings?name=I3-source&source_kind=synthetic', content=data,
                      headers={'Content-Type': 'application/octet-stream'}).json()
    response = client.post('/api/v1/cases', json={'title': 'Investigate source', 'note': 'Authored log is provenance only',
        'source': {'kind': 'recording', 'recording_id': rec['id'], 'channel_id': channel,
                   'start_time_ns': '100', 'end_time_ns': '100'}}, headers={'Idempotency-Key': 'source'})
    assert response.status_code == 201, response.text
    return response.json()


def definition(case, **changes):
    return {'expected_revision': case['revision'], 'name': 'Derived reference retest',
        'note': 'Manually assumed speeds and gaps; not a reconstruction or sensor test',
        'template_id': 'reference-follow/1', 'duration_s': 2,
        'parameters': {'ego_speed_mps': 2, 'target_speed_mps': 0, 'initial_gap_m': 20,
                       'trigger_time_s': 1, 'friction': .8, 'response_delay_s': .4},
        'variants': [{'key': 'base', 'name': 'Base', 'seed': 42},
                     {'key': 'near', 'name': 'Near', 'seed': 42, 'parameter_overrides': {'initial_gap_m': 10}}],
        'criteria': [{'name': 'Explicit zero-distance failure', 'metric_name': 'distance_m', 'operator': 'lte', 'threshold': 0}],
        'acknowledge_reference_only': True, **changes}


def post(client, path, body, key='one', status=201):
    r = client.post('/api/v1' + path, json=body, headers={'Idempotency-Key': key})
    assert r.status_code == status, r.text
    return r.json()


def plan(client, case, **changes):
    return post(client, f'/cases/{case["id"]}/retests', definition(case, **changes))


def action_body(case):
    return {'expected_revision': case['revision'], 'plan_hash': case['retests'][0]['definition_hash']}


def launch(client, case, key='launch'):
    return post(client, f'/cases/{case["id"]}/retests/{case["retests"][0]["id"]}/campaigns', action_body(case), key, 202)


def report(client, case):
    r = client.get(f'/api/v1/cases/{case["id"]}/retests', params={'revision': case['revision']})
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, case):
    for m in case['retests'][0]['attempts'][-1]['members']:
        run_once(client.app.state.db, client.app.state.registry, 'i3-worker', job_id=m['job_id'])


def counts(client):
    with client.app.state.db.session() as s:
        return {c.__tablename__: s.scalar(select(func.count()).select_from(c)) for c in
                (ScenarioRow, RequirementRow, SuiteRow, CampaignRow, CampaignMemberRow, JobRow, CaseRevisionRow, BaselineRow)}


def test_case_to_definition_queue_failure_capture_and_originals_unchanged(client, source_case):
    original = client.get(source_case['download_url']).content
    defined = plan(client, source_case)
    assert defined['state'] == source_case['state'] == 'open' and defined['revision'] == 2
    p = defined['retests'][0]
    assert p['definition']['source']['case_manifest_hash'] == source_case['manifest_hash']
    assert counts(client)['execution_jobs'] == 0
    queued = launch(client, defined)
    pending = report(client, queued)
    assert pending['plans'][0]['attempts'][0]['report']['counts']['pending'] == 2
    assert client.get(pending['download_url']).status_code == 200
    finish(client, queued)
    # Progress invalidates live snapshot exports; failed runs remain complete.
    assert client.get(pending['download_url']).status_code == 409
    result = report(client, queued)['plans'][0]['attempts'][0]['report']
    assert result['status'] == 'finished' and result['verdict'] == 'failed' and result['evaluation_complete']
    assert result['counts']['checks_failed'] == 2 and result['counts']['completed'] == 2
    path = f'/cases/{queued["id"]}/retests/{p["id"]}/campaigns/{result["id"]}/capture'
    captured = post(client, path, action_body(queued), 'capture')
    output = report(client, captured)
    raw = client.get(output['download_url']).content
    assert hashlib.sha256(raw).hexdigest() == output['manifest_hash']
    assert output['plans'][0]['attempts'][0]['evidence_mode'] == 'captured'
    assert client.get(output['download_url']).content == raw
    assert captured['state'] == 'open'
    assert client.get(source_case['download_url']).content == original


def test_create_launch_capture_retries_and_stale_revision(client, source_case):
    defined = plan(client, source_case)
    assert plan(client, source_case) == defined
    post(client, f'/cases/{source_case["id"]}/retests', definition(source_case, name='Changed'), status=409)
    queued = launch(client, defined)
    assert launch(client, defined) == queued
    before = counts(client)
    post(client, f'/cases/{defined["id"]}/retests/{defined["retests"][0]["id"]}/campaigns', action_body(defined), 'new-stale', 409)
    assert counts(client) == before
    finish(client, queued)
    p = queued['retests'][0]; cid = p['attempts'][0]['campaign_id']
    path=f'/cases/{queued["id"]}/retests/{p["id"]}/campaigns/{cid}/capture'
    result=post(client,path,action_body(queued),'capture')
    assert post(client,path,action_body(queued),'capture')==result


@pytest.mark.parametrize('boundary', ['scenario', 'suite', 'publish'])
def test_plan_authoring_is_atomic(client, source_case, monkeypatch, boundary):
    before=counts(client)
    target, name = (retests, 'scenario_create') if boundary=='scenario' else (retests.suites, 'create_suite_in_session') if boundary=='suite' else (retests, '_publish')
    original=getattr(target,name)
    def fail(*a,**k):
        original(*a,**k); raise RuntimeError('I3 injected authoring fault')
    monkeypatch.setattr(target,name,fail)
    with pytest.raises(RuntimeError,match='I3 injected'): plan(client,source_case)
    assert counts(client)==before
    assert client.get('/api/v1/cases/'+source_case['id']).json()['revision']==1


def test_queue_and_case_link_commit_together(client, source_case, monkeypatch):
    defined=plan(client,source_case);before=counts(client)
    original=retests._publish
    def fail(*a,**k):
        original(*a,**k);raise RuntimeError('Injected case publication failure')
    with monkeypatch.context() as m:
        m.setattr(retests,'_publish',fail)
        with pytest.raises(RuntimeError):launch(client,defined)
    assert counts(client)==before
    queued=launch(client,defined);assert counts(client)['execution_jobs']==2
    assert len(queued['retests'][0]['attempts'])==1


@pytest.mark.parametrize('change', [{'template_id':'carla'}, {'acknowledge_reference_only':False},
    {'acknowledge_reference_only':1}, {'variants':[]}, {'criteria':[]}, {'duration_s':1}, {'expected_revision':True},
    {'sensor_model':'invented'}, {'duration_s':float('inf')}])
def test_unsupported_definition_refused(client, source_case, change):
    body=definition(source_case,**change)
    if change.get('duration_s')==float('inf'):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):retests.RetestCreate.model_validate(body)
    else: post(client,f'/cases/{source_case["id"]}/retests',body,status=422)
    assert counts(client)['test_suites']==0


def test_cancelled_and_unexecuted_variants_are_not_passes(client, source_case):
    defined=plan(client,source_case);queued=launch(client,defined)
    p=queued['retests'][0];cid=p['attempts'][0]['campaign_id']
    post(client,f'/cases/{queued["id"]}/retests/{p["id"]}/campaigns/{cid}/capture',action_body(queued),'early',409)
    client.post(f'/api/v1/campaigns/{cid}/cancel').raise_for_status()
    result=report(client,queued)['plans'][0]['attempts'][0]['report']
    assert result['verdict']=='incomplete' and result['counts']['cancelled']==2
    assert result['counts']['checks_not_evaluated']==2 and not result['evaluation_complete']
    captured=post(client,f'/cases/{queued["id"]}/retests/{p["id"]}/campaigns/{cid}/capture',action_body(queued),'cancelled')
    assert report(client,captured)['plans'][0]['attempts'][0]['report']['verdict']=='incomplete'


def test_conditions_preserved_but_not_promoted_to_simulated_coverage(client, source_case):
    taxonomy=client.get('/api/v1/case-condition-taxonomy').json()
    annotated=post(client,f'/cases/{source_case["id"]}/conditions',{
        'expected_revision':1,'taxonomy_id':taxonomy['id'],'taxonomy_revision':1,'taxonomy_hash':taxonomy['manifest_hash'],
        'values':{'lighting':'night','target':'pedestrian','target_size':'small'},'review_status':'reviewed',
        'author':'Fixture declaration','note':'This synthetic telemetry does not actually measure these scene labels.'},'tags')
    defined=plan(client,annotated,criteria=[{'name':'Distance exists','metric_name':'distance_m','operator':'gte','threshold':0}])
    assert defined['conditions']==annotated['conditions']
    report0=client.get('/api/v1/case-retest-activity').json()
    assert report0['denominator']['never_submitted_plans']==1
    queued=launch(client,defined);finish(client,queued)
    activity=client.get('/api/v1/case-retest-activity').json()
    assert activity['rows'][0]['test_verdicts']=={'passed':2}
    assert activity['denominator']['verified_sensor_condition_cells']==0
    assert not activity['rows'][0]['sensor_condition_coverage_eligible']
    assert activity['rows'][0]['source_conditions']==annotated['conditions']
    assert client.get(report0['download_url']).status_code==409
    assert client.get('/api/v1/case-retest-activity?recording_id=unknown').status_code==404


def test_plan_tampering_and_foreign_campaign_fail_closed(client,source_case):
    defined=plan(client,source_case);queued=launch(client,defined)
    with client.app.state.db.session.begin() as s:
        row=s.get(CaseRevisionRow,(queued['id'],queued['revision']))
        value=json.loads(row.manifest);value['retests'][0]['definition']['suite']['name']='altered'
        from ade2e.cases import _seal
        for k,v in _seal(value).items():setattr(row,k,v)
    assert client.get('/api/v1/cases/'+queued['id']).status_code==409


def test_auth_and_schema_stability(client,source_case):
    plan(client,source_case)
    from ade2e.database import SCHEMA_VERSION, SchemaVersionRow
    with client.app.state.db.session() as s:assert s.scalar(select(SchemaVersionRow.version))==SCHEMA_VERSION
    client.app.state.settings.api_token='test-secret'
    for path in ('/case-retest-templates','/case-retest-activity',f'/cases/{source_case["id"]}/retests'):
        assert client.get('/api/v1'+path).status_code==401


def test_capture_rolls_back_baseline_and_case_together(client,source_case,monkeypatch):
    queued=launch(client,plan(client,source_case));finish(client,queued);before=counts(client)
    p=queued['retests'][0];cid=p['attempts'][0]['campaign_id']
    original=retests._publish
    def fail(*a,**k):
        original(*a,**k);raise RuntimeError('Capture publication fault')
    with monkeypatch.context() as m:
        m.setattr(retests,'_publish',fail)
        with pytest.raises(RuntimeError):
            post(client,f'/cases/{queued["id"]}/retests/{p["id"]}/campaigns/{cid}/capture',action_body(queued),'capture-fault')
    assert counts(client)==before
    assert report(client,queued)['plans'][0]['attempts'][0]['capture'] is None


def test_replaced_reference_adapter_and_wrong_plan_hash_do_not_enqueue(client,source_case):
    from ade2e.reference import ReferenceAdapter
    defined=plan(client,source_case);p=defined['retests'][0];before=counts(client)
    class Replacement(ReferenceAdapter):pass
    path=f'/cases/{defined["id"]}/retests/{p["id"]}/campaigns'
    post(client,path,{**action_body(defined),'plan_hash':'f'*64},'bad-hash',409)
    client.app.state.registry.register(Replacement(),replace=True)
    post(client,path,action_body(defined),'replacement',422)
    assert counts(client)==before


def test_attempt_membership_tampering_is_visible_not_a_pass(client,source_case):
    queued=launch(client,plan(client,source_case));finish(client,queued)
    cid=queued['retests'][0]['attempts'][0]['campaign_id']
    with client.app.state.db.session.begin() as session:
        member=session.get(CampaignMemberRow,(cid,'base'));session.delete(member)
    assert client.get(f'/api/v1/cases/{queued["id"]}/retests').status_code==409
    assert client.get('/api/v1/case-retest-activity').status_code==409


def test_ordinary_case_and_condition_revisions_preserve_plans(client,source_case):
    defined=plan(client,source_case);p=deepcopy(defined['retests'][0])
    revised=post(client,f'/cases/{defined["id"]}/revisions',{'expected_revision':defined['revision'],
        'title':defined['title'],'note':'Another investigator note','state':'investigating'},'ordinary-note')
    taxonomy=client.get('/api/v1/case-condition-taxonomy').json()
    revised=post(client,f'/cases/{defined["id"]}/conditions',{'expected_revision':revised['revision'],
        'taxonomy_id':taxonomy['id'],'taxonomy_revision':1,'taxonomy_hash':taxonomy['manifest_hash'],
        'values':{'lighting':'night'},'author':'Declared','note':'Test-only annotation','review_status':'draft'},'later-condition')
    assert revised['schema_version']=='axiom-investigation-case/3' and revised['retests'][0]==p
    assert revised['retests'][0]['definition']['source_conditions'] is None
    assert client.get(f'/api/v1/cases/{revised["id"]}/retests').status_code==200


def test_agent_tools_mark_compute_and_explicit_keys(client):
    tools=client.get('/api/v1/agent/tools').json()['tools']
    for name in ('create_case_retest','submit_case_retest','capture_case_retest'):
        entry=next(t for t in tools if t['name']==name)
        assert entry['required_headers']==['Idempotency-Key'] and not entry['read_only']
    assert next(t for t in tools if t['name']=='read_case_retest_activity')['read_only']
