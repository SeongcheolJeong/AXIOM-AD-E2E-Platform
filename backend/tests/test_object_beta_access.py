"""Invited beta admission/isolation contracts; these do not run CARLA."""
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlalchemy import select, update

from ade2e import experiment_jobs, jobs
from ade2e.database import Database, SchemaVersionRow, SCHEMA_VERSION
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.job_models import JobRow
from ade2e.team_models import ObjectBetaPolicyRow
from test_experiment_core import env, AuthoredHandler, definition, create_submit

RESOURCE = 'carla-city-2100'


def opt_in(env, project='default'):
    db, handlers, client = env
    handlers.register(AuthoredHandler(resource=RESOURCE), replace=True)
    response = client.post(f'/api/v1/projects/{project}/object-beta-policy', json={'resource_id': RESOURCE})
    assert response.status_code == 200, response.text
    return response.json()


def test_atomic_admission_same_key_retry_and_cancellation_release(env):
    db, handlers, client = env
    opt_in(env)
    frozen = experiment_jobs.create(db, handlers, ExperimentCreate.model_validate(definition()), 'definition')

    def submit(key):
        try:
            return experiment_jobs.submit(db, handlers, frozen['id'], frozen['preview_hash'], key)
        except HTTPException as error:
            return error

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(submit, ['same-browser-retry'] * 8))
    assert len({item.id for item in outcomes}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes += list(pool.map(submit, [f'new-{index}' for index in range(8)]))
    accepted = {item.id for item in outcomes if not isinstance(item, HTTPException)}
    rejected = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(accepted) == 2 and len(rejected) == 7
    assert all(item.status_code == 429 and item.headers['Retry-After'] == '10' for item in rejected)
    # A prior accepted identity still resolves while the quota is full.
    assert not isinstance(submit('same-browser-retry'), HTTPException)
    jobs.cancel_job(db, sorted(accepted)[0], kind='experiment')
    assert not isinstance(submit('after-cancel'), HTTPException)


def test_one_active_plus_two_waiting_and_shared_engine_lock(env):
    db, handlers, client = env
    opt_in(env)
    _, first = create_submit(client, key='first')
    capabilities = {('experiment', 'authored-contract', '1')}
    claim = jobs.claim_job(db, 'worker-one', capabilities=capabilities)
    assert claim and claim.job.id == first['id']
    _, second = create_submit(client, key='second')
    _, third = create_submit(client, key='third')
    assert jobs.claim_job(db, 'worker-two', capabilities=capabilities) is None
    queued = client.get('/api/v1/projects/default/object-beta-policy').json()
    assert (queued['active_count'], queued['queued_count'], queued['can_submit']) == (1, 2, False)
    # A cancel request cannot relinquish the active slot before engine acknowledgement.
    jobs.cancel_job(db, claim.job.id, kind='experiment')
    assert jobs.claim_job(db, 'worker-two', capabilities=capabilities) is None
    jobs.mark_dispatched(db, claim)  # Acknowledges pre-dispatch cancellation.
    next_claim = jobs.claim_job(db, 'worker-two', capabilities=capabilities)
    assert next_claim and next_claim.job.id == second['id']
    other = client.post('/api/v1/projects', json={'name': 'Participant two'}).json()['id']
    opt_in(env, other)
    create_submit(client, definition(other), key='other')
    assert jobs.claim_job(db, 'worker-three', capabilities=capabilities) is None


def test_opt_in_only_and_pinned_resource_no_handler_bypass(env):
    db, handlers, client = env
    # No policy: the normal research project retains existing unbounded queue behavior.
    for index in range(4):
        create_submit(client, key=f'research-{index}')
    project = client.post('/api/v1/projects', json={'name': 'Beta participant'}).json()['id']
    opt_in(env, project)
    handlers.register(AuthoredHandler('resource-free-bypass'))
    frozen = experiment_jobs.create(db, handlers, ExperimentCreate.model_validate(
        definition(project, 'resource-free-bypass')), 'unsupported')
    response = client.post(f'/api/v1/experiments/{frozen["id"]}/executions',
                           json={'preview_hash': frozen['preview_hash']}, headers={'Idempotency-Key': 'unsupported'})
    assert response.status_code == 422 and 'CARLA Object Sim' in response.json()['detail']


def test_policy_isolation_principal_scope_and_operator_only_configuration(env):
    _, _, client = env
    project = client.post('/api/v1/projects', json={'name': 'One person'}).json()['id']
    opt_in(env, project)
    tokens = [client.post(f'/api/v1/projects/{project}/tokens', json={
        'user_label': 'same-label', 'role': 'Engineer'}).json() for _ in range(2)]
    principals = []
    for token in tokens:
        client.headers['Authorization'] = 'Bearer ' + token['token']
        who = client.get('/api/v1/team/whoami').json()
        principals.append(who['principal_id'])
        assert who['principal_id'] not in {token['id'], token['token']}
        assert client.get('/api/v1/team/whoami').json()['principal_id'] == who['principal_id']
        assert client.get(f'/api/v1/projects/{project}/object-beta-policy').status_code == 200
        assert client.get('/api/v1/projects/default/object-beta-policy').status_code == 404
        assert client.post(f'/api/v1/projects/{project}/object-beta-policy', json={
            'enabled': False, 'resource_id': RESOURCE}).status_code == 403
    assert principals[0] != principals[1]
    # Multiple credentials/browsers in one project share a single allowance.
    create_submit(client, definition(project), key='browser-two-1')
    create_submit(client, definition(project), key='browser-two-2')
    client.headers['Authorization'] = 'Bearer ' + tokens[0]['token']
    assert client.get(f'/api/v1/projects/{project}/object-beta-policy').json()['can_submit'] is False


def test_schema19_upgrade_preserves_project_and_adds_empty_opt_in_table(env):
    db, _, client = env
    project = client.post('/api/v1/projects', json={'name': 'Existing project'}).json()['id']
    with db.engine.begin() as connection:
        connection.execute(update(SchemaVersionRow).values(version=19))
        ObjectBetaPolicyRow.__table__.drop(connection)
    db.initialize()
    with db.read_session() as session:
        assert session.scalar(select(SchemaVersionRow.version)) == SCHEMA_VERSION == 20
        assert session.scalar(select(ObjectBetaPolicyRow)) is None
    assert project in {item['id'] for item in client.get('/api/v1/projects').json()['items']}


def test_enable_cannot_admit_preexisting_unrestricted_queue(env):
    _, _, client = env
    create_submit(client, key='before-policy')
    response = client.post('/api/v1/projects/default/object-beta-policy', json={'resource_id': RESOURCE})
    assert response.status_code == 409
    assert client.get('/api/v1/projects/default/object-beta-policy').json()['enabled'] is False
