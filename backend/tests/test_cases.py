"""I1 source identity, persistence, revision and no-execution boundaries."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import select

from ade2e.case_models import CaseRow, CaseRevisionRow, SegmentRow
from ade2e.database import Database, SchemaVersionRow, SCHEMA_VERSION
from ade2e.recording_models import RecordingMessageRow
from test_detection_evaluations import context, evaluation_context, execute, submit


def setup_case(evaluation_context):
    (client, app, *_), _, _, _, _ = evaluation_context
    result = execute(evaluation_context, submit(evaluation_context))
    body = {'title': 'Investigate a prediction', 'note': 'Observed example, not confirmed cause.',
        'source': {'kind': 'evaluation', 'evaluation_id': result.id, 'image_id': result.report.rows[0].image_id}}
    response = client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'create-case'})
    assert response.status_code == 201, response.text
    return client, app, body, response.json(), result


def revision_body(value, **changes):
    return dict(expected_revision=value['revision'], title=value['title'], note='Investigation continues',
        assignee='Engineer (declared)', state='investigating', **changes)


def test_evaluation_to_case_exact_source_export_and_repeated_create(evaluation_context):
    client, app, body, value, result = setup_case(evaluation_context)
    assert value['segment']['selector']['kind'] == 'exact_ordinal'
    assert value['segment']['sample_count'] == 1
    assert value['evaluations'][0]['manifest_hash'] == result.manifest_hash
    assert not value['workflow_state_is_test_verdict']
    raw = client.get(value['download_url']).content
    assert hashlib.sha256(raw).hexdigest() == value['manifest_hash']
    assert client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'create-case'}).json()['id'] == value['id']
    assert client.post('/api/v1/cases', json={**body, 'title': 'Different'}, headers={'Idempotency-Key': 'create-case'}).status_code == 409
    samples = client.get(f'/api/v1/cases/{value["id"]}/samples').json()
    assert samples['total'] == 1 and samples['samples'][0]['sha256'] == value['segment']['samples'][0]['sha256']
    assert client.get('/api/v1/cases', params={'evaluation_id': result.id}).json()['items'][0]['id'] == value['id']
    with app.state.db.session() as session:
        assert session.scalar(select(SchemaVersionRow.version)) == SCHEMA_VERSION


def test_append_revision_is_idempotent_and_prior_bytes_never_change(evaluation_context):
    client, app, _, value, _ = setup_case(evaluation_context)
    original = client.get(value['download_url']).content
    body = revision_body(value)
    path = f'/api/v1/cases/{value["id"]}/revisions'
    first = client.post(path, json=body, headers={'Idempotency-Key': 'revision-1'})
    assert first.status_code == 201, first.text
    assert first.json()['revision'] == 2 and first.json()['previous_manifest_hash'] == value['manifest_hash']
    assert client.post(path, json=body, headers={'Idempotency-Key': 'revision-1'}).json() == first.json()
    assert client.post(path, json=body, headers={'Idempotency-Key': 'stale-new-key'}).status_code == 409
    assert client.get(value['download_url']).content == original
    assert client.post('/api/v1/cases', json={'title': value['title'], 'note': value['note'], 'source':
        {'kind': 'evaluation', **{k: value['evaluations'][0][k] for k in ['evaluation_id', 'image_id']}}},
        headers={'Idempotency-Key': 'create-case'}).json()['revision'] == 1


def test_inclusive_log_interval_pins_all_messages_and_links_matching_evaluation(evaluation_context):
    client, app, _, evaluation_case, result = setup_case(evaluation_context)
    source = evaluation_case['segment']
    body = {'title': 'Raw interval', 'note': 'Declared log-clock interval', 'source': {'kind': 'recording',
        'recording_id': source['recording_id'], 'channel_id': source['channel_id'],
        'start_time_ns': '0', 'end_time_ns': str(2**64-1)}}
    response = client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'raw'})
    assert response.status_code == 201, response.text
    value = response.json(); assert value['segment']['sample_count'] >= 3
    req = revision_body(value, add_evaluation={'kind': 'evaluation', 'evaluation_id': result.id,
        'image_id': result.report.rows[1].image_id})
    linked = client.post(f'/api/v1/cases/{value["id"]}/revisions', json=req, headers={'Idempotency-Key': 'link'})
    assert linked.status_code == 201, linked.text
    assert len(linked.json()['evaluations']) == 1
    first = client.get(f'/api/v1/cases/{value["id"]}/samples?limit=1').json()
    second = client.get(f'/api/v1/cases/{value["id"]}/samples?limit=1&offset=1').json()
    assert first['samples'][0]['ordinal'] != second['samples'][0]['ordinal']


def test_other_source_image_cannot_be_attached_and_no_partial_revision(evaluation_context):
    client, app, _, value, result = setup_case(evaluation_context)
    body = revision_body(value, add_evaluation={'kind': 'evaluation', 'evaluation_id': result.id,
        'image_id': result.report.rows[1].image_id})
    assert client.post(f'/api/v1/cases/{value["id"]}/revisions', json=body,
        headers={'Idempotency-Key': 'wrong'}).status_code == 422
    assert client.get(f'/api/v1/cases/{value["id"]}').json()['revision'] == 1


@pytest.mark.parametrize('change', [dict(state='resolved'), dict(state='dismissed'), dict(expected_revision=True)])
def test_resolution_requires_explicit_note_and_revision_is_strict(evaluation_context, change):
    client, _, _, value, _ = setup_case(evaluation_context)
    body = {**revision_body(value), **change}
    assert client.post(f'/api/v1/cases/{value["id"]}/revisions', json=body,
        headers={'Idempotency-Key': 'invalid'}).status_code == 422


def test_case_hash_and_source_index_tampering_are_not_silently_shown(evaluation_context):
    client, app, _, value, _ = setup_case(evaluation_context)
    source = value['segment']
    with app.state.db.session.begin() as session:
        message = session.get(RecordingMessageRow, (source['recording_id'], source['samples'][0]['ordinal']))
        message.sha256 = 'a'*64
    assert client.get(f'/api/v1/cases/{value["id"]}/samples').status_code == 409
    # The original investigation record remains exportable even if raw source is damaged.
    assert client.get(value['download_url']).status_code == 200
    with app.state.db.session.begin() as session:
        row = session.get(CaseRevisionRow, (value['id'], 1)); row.manifest += b' '
    assert client.get(f'/api/v1/cases/{value["id"]}').status_code == 409


@pytest.mark.parametrize('start,end', [('01','2'), ('2','1'), ('0',str(2**64)), (1,'2')])
def test_invalid_or_inexact_time_selection_is_rejected(client, start, end):
    assert client.post('/api/v1/cases', headers={'Idempotency-Key': 'invalid'}, json={
        'title': 'Source', 'note': 'test', 'source': {'kind': 'recording', 'recording_id': 'rec_missing',
        'channel_id': 1, 'start_time_ns': start, 'end_time_ns': end}}).status_code == 422


def test_duplicate_times_nonmonotonic_order_and_interleaved_channel_use_exact_identity(client):
    from test_recordings import make_mcap
    raw, channel, other, _ = make_mcap(messages=[(1, 100, b'{"v":1}',100,0),
        (2, 0, b'opaque',0,0), (1, 0, b'{"v":2}',0,1), (1, 0, b'{"v":3}',0,2)])
    imported = client.post('/api/v1/recordings?name=Nonmonotonic&source_kind=synthetic',
        content=raw, headers={'Content-Type':'application/octet-stream'})
    assert imported.status_code == 201, imported.text
    rid=imported.json()['id']
    response=client.post('/api/v1/cases', headers={'Idempotency-Key':'duplicate-times'}, json={
        'title':'Both original zero-time messages','note':'No time or file-ordinal fallback',
        'source':{'kind':'recording','recording_id':rid,'channel_id':channel,'start_time_ns':'0','end_time_ns':'0'}})
    assert response.status_code==201,response.text
    value=response.json(); assert [r['ordinal'] for r in value['segment']['samples']]==[2,3]
    for ordinal in (0,2,3):
        response=client.get(f'/api/v1/recordings/{rid}/samples',params={'channel_id':channel,'from_ordinal':ordinal,'limit':1})
        assert response.status_code==200,response.text
        assert response.json()['samples'][0]['ordinal']==ordinal
    assert client.get(f'/api/v1/recordings/{rid}/samples',params={'channel_id':channel,'from_ordinal':1}).status_code==404
    assert client.get(f'/api/v1/recordings/{rid}/samples',params={'channel_id':channel,'from_ordinal':2,'at_ns':'0'}).status_code==422


def test_empty_interval_and_excessive_size_refuse_without_creating_partial_case(evaluation_context, monkeypatch):
    from ade2e import cases
    from sqlalchemy import func
    client, app, _, value, _=setup_case(evaluation_context)
    source=value['segment']
    body={'title':'Selection','note':'No partial cases','source':{'kind':'recording',
        'recording_id':source['recording_id'],'channel_id':source['channel_id'],'start_time_ns':'0','end_time_ns':'0'}}
    assert client.post('/api/v1/cases',json=body,headers={'Idempotency-Key':'empty'}).status_code==422
    monkeypatch.setattr(cases,'MAX_SAMPLES',1)
    body['source']['end_time_ns']=str(2**64-1)
    assert client.post('/api/v1/cases',json=body,headers={'Idempotency-Key':'large'}).status_code==413
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(CaseRow))==1
        assert session.scalar(select(func.count()).select_from(SegmentRow))==1


def test_unreviewed_evaluation_row_remains_unreviewed_not_a_failure_claim(evaluation_context):
    from test_detection_evaluations import revise
    (client, app, *_), _, _, _, body=evaluation_context
    data=revise(evaluation_context,lambda v:v['images'][0].update(review_status='unreviewed'))
    result=execute(evaluation_context,submit(evaluation_context,body=body.model_copy(update={'dataset_revision':data['revision']})))
    response=client.post('/api/v1/cases',headers={'Idempotency-Key':'draft'},json={'title':'Draft evidence',
        'note':'Review is still required','source':{'kind':'evaluation','evaluation_id':result.id,'image_id':result.report.rows[0].image_id}})
    assert response.status_code==201,response.text
    assert response.json()['evaluations'][0]['row_status']=='unreviewed'
    assert response.json()['evaluations'][0]['diagnostics'] is None


def test_case_api_and_tools_preserve_authentication_and_key_contract(evaluation_context):
    client, app, _, value, _=setup_case(evaluation_context)
    catalog=client.get('/api/v1/agent/tools').json()['tools']
    for name in ('create_investigation_case','revise_investigation_case'):
        entry=next(x for x in catalog if x['name']==name)
        assert entry['required_headers']==['Idempotency-Key'] and not entry['read_only']
    app.state.settings.api_token='test-case-private-token'
    assert client.get('/api/v1/cases').status_code==401
    assert client.get(value['download_url']).status_code==401
    assert client.get('/api/v1/cases',headers={'Authorization':'Bearer test-case-private-token'}).status_code==200
