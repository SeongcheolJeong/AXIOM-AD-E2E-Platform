"""Multi-source curation is real persistence, not a model-accuracy benchmark."""
from copy import deepcopy
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from ade2e import collections
from ade2e.collection_models import CollectionRow, CollectionRevisionRow, CollectionDatasetRow
from ade2e.dataset_models import DatasetRow
from ade2e.job_models import JobRow
from ade2e.recording_models import RecordingMessageRow
from test_cases import setup_case
from test_detection_evaluations import context, evaluation_context, revise
from test_sensors import make_recording


def request(client, path, body, key='one', expected=201):
    response = client.post('/api/v1'+path, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == expected, response.text
    return response.json()


def setup(evaluation_context):
    client, app, _, case, baseline = setup_case(evaluation_context)
    dataset = evaluation_context[2]
    definition = {'name':'Mixed investigation and normal data', 'purpose':'Curate original sources, no road accuracy claim',
        'sources':[{'kind':'case','case_id':case['id'],'revision':1,'reason':'Investigated frame'},
                   {'kind':'dataset','dataset_id':dataset['id'],'revision':1,'split':'all','reason':'Keep normal and reviewed negatives too'}]}
    return client, app, case, baseline, definition


def create(client, definition, key='collection'):
    preview = request(client, '/collections/preview', definition, expected=200)
    body = {**definition,'expected_preview_hash':preview['preview_hash']}
    return request(client, '/collections', body, key), body


def decisions(collection, *, labels=True):
    choices=[]
    for row in collection['selection']['rows']:
        if not row['images']:
            choices.append({'key':row['key'],'action':'exclude','reason':'No supported image captured; retained in curation receipt'})
        else:
            image=row['images'][0]
            option=next((o for o in row['label_options'] if o['member']['image_id']==image['id']),None)
            choices.append({'key':row['key'],'action':'include','image_id':image['id'],'split':'test',
                'label_option_id':option['id'] if labels and option else None})
    return {'collection_revision':collection['revision'],'name':'Frozen collection dataset',
        'note':'Explicit selected label revisions only','labels':['target','absent-class'],'decisions':choices}


def publish(client, collection, body=None, key='dataset'):
    body=body or decisions(collection)
    path=f'/collections/{collection["id"]}/datasets'
    preview=request(client,path+'/preview',body,expected=200)
    selected={**body,'expected_preview_hash':preview['preview_hash']}
    return request(client,path,selected,key),selected


def counts(app):
    with app.state.db.session() as s:
        return [s.scalar(select(func.count()).select_from(cls)) for cls in (CollectionRow,CollectionRevisionRow,DatasetRow,CollectionDatasetRow,JobRow)]


def test_case_dataset_overlap_and_reviewed_negative_keep_full_lineage(evaluation_context):
    client,app,case,baseline,body=setup(evaluation_context)
    initial=counts(app)
    preview=request(client,'/collections/preview',body,expected=200)
    assert counts(app)==initial and preview['jobs_created']==preview['writes_created']==0
    assert preview['summary']['expanded_references']==4 and preview['summary']['unique_messages']==3
    value,request_body=create(client,body)
    assert request(client,'/collections',request_body,'collection')==value
    raw=client.get(value['download_url']).content
    assert hashlib.sha256(raw).hexdigest()==value['manifest_hash']
    receipt,publication_body=publish(client,value)
    ds=client.get('/api/v1/datasets/'+receipt['dataset']['id']).json()
    assert ds['stats']['images']==ds['stats']['reviewed']==3 and ds['stats']['negative_images']==1
    assert ds['images']==evaluation_context[2]['images'] or {m['image_id'] for m in ds['images']}=={m['image_id'] for m in evaluation_context[2]['images']}
    assert receipt['proposal']['denominator']=={'collection_messages':3,'included':3,'excluded':0}
    assert request(client,f'/collections/{value["id"]}/datasets',publication_body,'dataset')==receipt
    assert counts(app)[-1]==initial[-1]
    assert client.get(case['download_url']).content==client.get('/api/v1/cases/'+case['id']+'/export?revision=1').content
    assert client.get('/api/v1/collection-datasets',params={'dataset_id':ds['id']}).json()['items']==[receipt]
    assert client.get(value['download_url']).content==raw


def test_no_implicit_labels_or_negative_images(evaluation_context):
    client,_,_,_,body=setup(evaluation_context);collection,_=create(client,body)
    receipt,_=publish(client,collection,decisions(collection,labels=False))
    dataset=client.get('/api/v1/datasets/'+receipt['dataset']['id']).json()
    assert dataset['stats']['reviewed']==dataset['stats']['negative_images']==dataset['stats']['annotations']==0
    assert dataset['stats']['unreviewed']==3
    assert all(m['annotation_source']=='unknown' for m in dataset['images'])


def test_case_revision_and_dataset_label_changes_do_not_rewrite_collection(evaluation_context):
    client,_,case,_,body=setup(evaluation_context);value,_=create(client,body)
    original=client.get(value['download_url']).content
    request(client,f'/cases/{case["id"]}/revisions',{'expected_revision':1,'title':case['title'],'note':'Changed now',
        'assignee':'New reviewer','state':'investigating'},'case-change')
    revised=revise(evaluation_context,lambda d:d['images'][0].update(annotations=[],reviewer='New reviewer',review_note='Reviewed changed labels'))
    assert revised['revision']==2
    assert client.get(value['download_url']).content==original
    receipt,_=publish(client,value)
    assert receipt['proposal']['stats']['annotations']==2
    next_body=deepcopy(body);next_body['sources'][0]['revision']=2;next_body['sources'][1]['revision']=2
    p=request(client,'/collections/preview',next_body,expected=200)
    new=request(client,f'/collections/{value["id"]}/revisions',
        {**next_body,'expected_revision':1,'expected_preview_hash':p['preview_hash']},'revise')
    assert new['revision']==2 and new['previous_manifest_hash']==value['manifest_hash']
    assert client.get(value['download_url']).content==original
    assert client.post(f'/api/v1/collections/{value["id"]}/revisions',json={**next_body,'expected_revision':1,'expected_preview_hash':p['preview_hash']},headers={'Idempotency-Key':'stale'}).status_code==409


@pytest.mark.parametrize('fault',['missing_decision','extra_decision','wrong_image','wrong_labels','expanded_taxonomy','wrong_label_source','empty_reason','all_excluded','cross_recording_split'])
def test_invalid_curation_never_creates_partial_dataset(evaluation_context,fault):
    client,app,_,_,source=setup(evaluation_context);value,_=create(client,source)
    body=decisions(value);first=body['decisions'][0]
    if fault=='missing_decision':body['decisions'].pop()
    elif fault=='extra_decision':body['decisions'].append({**first,'key':'0'*64})
    elif fault=='wrong_image':first['image_id']='dataset_image_absent'
    elif fault=='wrong_labels':body['labels']=['different']
    elif fault=='expanded_taxonomy':body['labels'].append('unreviewed-new-class')
    elif fault=='wrong_label_source':first['label_option_id']='0'*64
    elif fault=='empty_reason':body['decisions'][0]={'key':first['key'],'action':'exclude','reason':'   '}
    elif fault=='all_excluded':body['decisions']=[{'key':d['key'],'action':'exclude','reason':'Leave out'} for d in body['decisions']]
    else:first['split']='train'
    before=counts(app)
    response=client.post(f'/api/v1/collections/{value["id"]}/datasets/preview',json=body)
    assert response.status_code==422,response.text
    assert counts(app)==before


def test_nonimage_case_is_retained_and_requires_exclusion(evaluation_context):
    client,_,_,_,body=setup(evaluation_context)
    recording,channels,_=make_recording(client,camera_frame='other-camera')
    case=request(client,'/cases',{'title':'Opaque source','note':'Not an image',
        'source':{'kind':'recording','recording_id':recording['id'],'channel_id':channels[2],
                  'start_time_ns':'0','end_time_ns':str(2**64-1)}},'opaque-case')
    body['sources'].append({'kind':'case','case_id':case['id'],'revision':1,'reason':'Keep unsupported source visible'})
    value,_=create(client,body)
    assert value['selection']['summary']['unique_messages']==4 and value['selection']['summary']['without_captured_image']==1
    receipt,_=publish(client,value)
    assert receipt['proposal']['denominator']=={'collection_messages':4,'included':3,'excluded':1}
    assert receipt['proposal']['exclusions'][0]['reason']


def test_multiple_recordings_and_direct_images_need_no_failure_case(evaluation_context):
    client,_,_,_,body=setup(evaluation_context)
    recording,channels,_=make_recording(client,camera_frame='other-camera')
    image=request(client,'/dataset-images',{'recording_id':recording['id'],'channel_id':channels[0],'ordinal':1,
        'decoder_id':'foxglove-json','decoder_version':'1'},'capture')
    body['sources'].append({'kind':'image','image_id':image['id'],'reason':'Normal unannotated image, not a failure'})
    value,_=create(client,body)
    assert value['selection']['summary']['recordings']==2 and value['selection']['summary']['unique_messages']==4
    assert value['selection']['summary']['duplicate_png_groups']>=1
    receipt,_=publish(client,value)
    assert receipt['proposal']['stats']['images']==4 and receipt['proposal']['stats']['unreviewed']==1
    split=decisions(value);split['decisions'][-1]['split']='train'
    result=client.post(f'/api/v1/collections/{value["id"]}/datasets/preview',json=split)
    assert result.status_code==422 and 'Identical PNG' in result.text


def test_corrupt_source_blocks_new_dataset_but_not_historical_export(evaluation_context):
    client,app,_,_,body=setup(evaluation_context);value,_=create(client,body)
    source=value['selection']['rows'][0]['source']
    with app.state.db.session.begin() as session:
        session.get(RecordingMessageRow,(source['recording_id'],source['ordinal'])).sha256='0'*64
    assert client.get(value['download_url']).status_code==200
    before=counts(app)
    assert client.post(f'/api/v1/collections/{value["id"]}/datasets/preview',json=decisions(value)).status_code==409
    assert counts(app)==before


def test_atomic_publication_rollback_and_preview_hash(evaluation_context,monkeypatch):
    client,app,_,_,body=setup(evaluation_context);value,_=create(client,body)
    path=f'/collections/{value["id"]}/datasets';definition=decisions(value)
    preview=request(client,path+'/preview',definition,expected=200)
    before=counts(app);original=collections._insert_revision
    def fail(*args,**kwargs):
        original(*args,**kwargs);raise OSError('Injected publication failure')
    with monkeypatch.context() as m:
        m.setattr(collections,'_insert_revision',fail)
        with pytest.raises(OSError):request(client,path,{**definition,'expected_preview_hash':preview['preview_hash']})
    assert counts(app)==before
    assert client.post('/api/v1'+path,json={**definition,'expected_preview_hash':'0'*64},headers={'Idempotency-Key':'bad'}).status_code==409
    assert counts(app)==before


def test_concurrent_requests_reuse_one_collection_and_dataset(evaluation_context):
    client,app,_,_,source=setup(evaluation_context)
    preview=request(client,'/collections/preview',source,expected=200)
    body={**source,'expected_preview_hash':preview['preview_hash']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:request(client,'/collections',body,'shared'),range(2)))
    assert results[0]==results[1] and counts(app)[0]==1
    value=results[0];body=decisions(value);path=f'/collections/{value["id"]}/datasets'
    preview=request(client,path+'/preview',body,expected=200);body['expected_preview_hash']=preview['preview_hash']
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda _:request(client,path,body,'shared'),range(2)))
    assert receipts[0]==receipts[1] and counts(app)[3]==1
    assert client.post('/api/v1'+path,json={**body,'name':'Another request'},headers={'Idempotency-Key':'shared'}).status_code==409


def test_size_bounds_are_not_partial_results(evaluation_context,monkeypatch):
    client,app,_,_,body=setup(evaluation_context);before=counts(app)
    monkeypatch.setattr(collections,'MAX_ROWS',2)
    assert client.post('/api/v1/collections/preview',json=body).status_code==413
    assert counts(app)==before


def test_manifest_corruption_fails_closed(evaluation_context):
    client,app,_,_,body=setup(evaluation_context);value,_=create(client,body)
    with app.state.db.session.begin() as s:
        s.get(CollectionRevisionRow,(value['id'],1)).manifest += b' '
    assert client.get(value['download_url']).status_code==409
