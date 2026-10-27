"""Typed plan acceptance with authored metrics; no physical/safety evidence."""
import json
import hashlib
from ade2e.adapters import Registry
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.worker import run_once
from test_experiment_core import env, AuthoredHandler, definition, create_submit

class MetricHandler(AuthoredHandler):
    def __init__(self, identity='metric-contract', kind='object_sim', fidelity='authored_contract_test'):
        super().__init__(identity)
        self.kind, self.fidelity = kind, fidelity
    def metadata(self):
        return ExperimentHandlerMetadata(id=self.identity, version='1', kinds=[self.kind], status='available',
            fidelity=self.fidelity, cancellation_supported=True)
    def execute(self, snapshot, work_dir, should_cancel):
        if self.kind == 'planning_replay':
            trace = work_dir / 'planning-trace.ndjson'
            with trace.open('w') as stream:
                for index in range(130):
                    stream.write(json.dumps({'metrics':{
                        'speed':{'status':'evaluated','value':30 if index==129 else 3,'unit':'m/s'},
                        'acceleration':{'status':'unevaluated' if index<2 else 'evaluated',
                                        'value':None if index<2 else 1,'unit':'m/s2','reason':'initial_step' if index<2 else None}}})+'\n')
            return ExperimentExecution(summary={'visualization': {'ticks': [{'speed':3}]*128,'preview_truncated':True}},
                artifacts=[ExperimentOutputFile(trace,'planning-trace.ndjson','application/x-ndjson')], fidelity=self.fidelity)
        return ExperimentExecution(summary={'sample_count':20,'observers':[{'observer':{'kind':'maximum_speed','actor_id':'ego'},
            'value':20,'units':'m/s','status':'observed','evaluator_version':'authored-metric-test/1'}]}, fidelity=self.fidelity)

def plan_body(kind='object_sim', fidelity='authored_contract_test', metric='maximum_speed', unit='m/s', threshold=10, fraction=1):
    return {'name':'Bounded observer requirement','purpose':'Check authored/reference criteria, not road safety',
            'experiment_kind':kind,'allowed_fidelities':[fidelity], 'criteria':[{'metric':metric,'unit':unit,
                'operator':'lte','threshold':threshold,'required_evaluated_fraction':fraction}]}

def plan(client, body=None, key='plan-one'):
    response = client.post('/api/v1/experiment-verification-plans',json=body or plan_body(),headers={'Idempotency-Key':key})
    assert response.status_code == 201, response.text
    return response.json()

def evaluate(client, identity, execution_ids, revision=1, key='evaluation-one'):
    response = client.post(f'/api/v1/experiment-verification-plans/{identity}/evaluations',
        json={'plan_revision':revision,'execution_ids':execution_ids},headers={'Idempotency-Key':key})
    assert response.status_code == 201, response.text
    return response.json()

def submit_kind(client, handler, key='execution-one'):
    value=definition(adapter=handler.identity)
    value['kind']=handler.kind
    return create_submit(client,value,key)

def test_plan_preserves_denominator_and_distinguishes_missing_infra_sut_unevaluated_and_scope(env):
    db, handlers, client=env
    handlers.register(MetricHandler())
    handlers.register(MetricHandler('other-fidelity',fidelity='unsupported_fidelity_test'))
    handlers.register(AuthoredHandler('failed-contract',fail=True))
    _, failed_criteria=submit_kind(client,handlers.get('metric-contract'),'sut')
    run_once(db,Registry(),'sut-worker',job_id=failed_criteria['id'],experiment_registry=handlers)
    _, wrong_scope=submit_kind(client,handlers.get('other-fidelity'),'scope')
    run_once(db,Registry(),'scope-worker',job_id=wrong_scope['id'],experiment_registry=handlers)
    _, failed_execution=create_submit(client,definition(adapter='failed-contract'),'infra')
    run_once(db,Registry(),'infra-worker',job_id=failed_execution['id'],experiment_registry=handlers)
    _, pending=create_submit(client,definition(),'pending')
    created=plan(client)
    result=evaluate(client,created['id'],[failed_criteria['id'],wrong_scope['id'],failed_execution['id'],pending['id'],'missing-execution'])
    assert result['execution_denominator']==5
    assert result['counts']=={'passed':0,'sut_failure':1,'missing_input':1,'unsupported':1,
                             'infrastructure_failure':1,'unevaluated':1,'context_only':0}
    assert result['verdict']=='failed' and result['is_safety_validation'] is False
    again=evaluate(client,created['id'],[failed_criteria['id'],wrong_scope['id'],failed_execution['id'],pending['id'],'missing-execution'])
    assert again['id']==result['id'] and again['manifest_hash']==result['manifest_hash']
    download=client.get(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations/{result["id"]}/export')
    assert hashlib.sha256(download.content).hexdigest()==result['manifest_hash']

def test_immutable_revisions_evaluation_types_and_sensor_context_never_pass(env):
    db, handlers, client=env
    handlers.register(MetricHandler())
    _, execution=submit_kind(client,handlers.get('metric-contract'))
    run_once(db,Registry(),'metric-worker',job_id=execution['id'],experiment_registry=handlers)
    created=plan(client)
    first=evaluate(client,created['id'],[execution['id']])
    changed=plan_body(threshold=25)
    changed['change_reason']='Explicit threshold change with preserved previous revision'
    revision=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/revisions',json=changed,
                         headers={'Idempotency-Key':'revision-two'})
    assert revision.status_code==201 and revision.json()['revision']==2
    second=evaluate(client,created['id'],[execution['id']],revision=2,key='second-eval')
    assert first['verdict']=='failed' and second['verdict']=='passed'
    assert client.get(f'/api/v1/experiment-verification-plans/{created["id"]}?revision=1').json()['manifest_hash']==created['manifest_hash']
    assert client.post('/api/v1/experiment-verification-plans',json=plan_body(kind='sensor_capture'),
                       headers={'Idempotency-Key':'bad-sensor'}).status_code==422
    assert client.post('/api/v1/experiment-verification-plans',json=plan_body(unit='km/h'),
                       headers={'Idempotency-Key':'bad-unit'}).status_code==422
    handler=MetricHandler('context-sensor','sensor_capture')
    handlers.register(handler)
    _, sensor=submit_kind(client,handler,'sensor')
    run_once(db,Registry(),'sensor-context-worker',job_id=sensor['id'],experiment_registry=handlers)
    body=plan_body(kind='sensor_capture');body['criteria']=[]
    context=plan(client,body,'context-plan')
    context_result=evaluate(client,context['id'],[sensor['id']],key='context-eval')
    assert context_result['counts']['context_only']==1 and context_result['verdict']=='not_evaluated'

def test_planning_full_native_trace_not_truncated_preview_and_missing_steps(env):
    db, handlers, client=env
    handler=MetricHandler('trace-contract','planning_replay','authored_planar_reference')
    handlers.register(handler)
    _, execution=submit_kind(client,handler)
    run_once(db,Registry(),'trace-worker',job_id=execution['id'],experiment_registry=handlers)
    created=plan(client,plan_body(kind='planning_replay',fidelity='authored_planar_reference'))
    result=evaluate(client,created['id'],[execution['id']])
    observation=result['entries'][0]['criteria'][0]['observation']
    assert observation['value']==30 and observation['denominator']==130
    assert result['verdict']=='failed'
    partial=plan(client,plan_body(kind='planning_replay',fidelity='authored_planar_reference',
        metric='maximum_acceleration',unit='m/s2'),'partial-plan')
    incomplete=evaluate(client,partial['id'],[execution['id']],key='partial-eval')
    assert incomplete['verdict']=='not_evaluated' and incomplete['counts']['unevaluated']==1
    observation=incomplete['entries'][0]['criteria'][0]['observation']
    assert observation['denominator']==130 and observation['evaluated']==128

def test_reviewer_can_capture_engineer_cannot_and_no_cross_project_evidence(env):
    db, handlers, client=env
    handlers.register(MetricHandler())
    _, execution=submit_kind(client,handlers.get('metric-contract'))
    run_once(db,Registry(),'metric-worker',job_id=execution['id'],experiment_registry=handlers)
    created=plan(client)
    engineer=client.post('/api/v1/projects/default/tokens',json={'role':'Engineer','user_label':'one'}).json()['token']
    reviewer=client.post('/api/v1/projects/default/tokens',json={'role':'Reviewer','user_label':'two'}).json()['token']
    client.headers['Authorization']='Bearer '+engineer
    assert client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[execution['id']]},headers={'Idempotency-Key':'engineer-eval'}).status_code==403
    client.headers['Authorization']='Bearer '+reviewer
    assert evaluate(client,created['id'],[execution['id']],key='reviewer-eval')['verdict']=='failed'
    assert client.post('/api/v1/experiment-verification-plans',json=plan_body(),headers={'Idempotency-Key':'reviewer-create'}).status_code==403
    client.headers['Authorization']='Bearer bootstrap-test-only'
    other_project=client.post('/api/v1/projects',json={'name':'Other boundary'}).json()['id']
    body=plan_body();body['project_id']=other_project
    other=plan(client,body,'other-project-plan')
    assert client.post(f'/api/v1/experiment-verification-plans/{other["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[execution['id']]},headers={'Idempotency-Key':'cross-project'}).status_code==404
