from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from mcap.writer import Writer, CompressionType
from PIL import Image
import pytest
from sqlalchemy import event, select

from ade2e.database import Settings
from ade2e.main import create_app
from ade2e.dataset_models import DatasetImageRow, DatasetMembershipRow, DatasetRevisionRow, DatasetRow
from ade2e.dataset_schemas import DatasetAnnotation
from ade2e import datasets, jobs


def source_bytes():
    stream = io.BytesIO()
    writer = Writer(stream, compression=CompressionType.ZSTD)
    writer.start(profile='json')
    schema = writer.register_schema('foxglove.CompressedImage','jsonschema',json.dumps({
        'type':'object','properties':{'timestamp':{'type':'object'},'frame_id':{'type':'string'},
        'format':{'type':'string'},'data':{'type':'string','contentEncoding':'base64'}}}).encode())
    channel = writer.register_channel('/camera/test/image','json',schema)
    for index, color in enumerate([(240,20,30),(240,20,30),(20,30,240),None]):
        png = io.BytesIO()
        if color:
            Image.new('RGB',(32,16),color).save(png,format='PNG')
        body = {'timestamp':{'sec':1700000000,'nsec':index},'frame_id':'camera_optical','format':'png',
                'data':base64.b64encode(png.getvalue()).decode() if color else 'invalid!'}
        writer.add_message(channel,1700000000000000000+index,json.dumps(body).encode(),1700000000000000000+index,index)
    writer.finish()
    return stream.getvalue()


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(data_dir=tmp_path,seed_examples=False))
    with TestClient(app) as client:
        response = client.post('/api/v1/recordings',params={'name':'Authored image source','source_kind':'synthetic','source':'Original test pixels'},
                               content=source_bytes(),headers={'Content-Type':'application/octet-stream'})
        assert response.status_code == 201,response.text
        yield client, app.state.db, response.json()['id']


def capture(api, ordinal=0):
    client, db, recording_id = api
    result = client.post('/api/v1/dataset-images',json={'recording_id':recording_id,'channel_id':1,'ordinal':ordinal,
                         'decoder_id':'foxglove-json','decoder_version':'1'})
    assert result.status_code in (200,201),result.text
    return result.json()


def member(asset, *, reviewed=False, split='test', boxes=None):
    return dict(image_id=asset['id'],split=split,review_status='reviewed' if reviewed else 'unreviewed',
        reviewer='Fixture declaration' if reviewed else '',review_note='Original authored label check' if reviewed else '',
        annotation_source='synthetic' if reviewed else 'unknown',annotations=boxes or [])


def box(label='red', id='a1', coords=(2,3,12,13), crowd=False):
    return dict(id=id,label=label,bbox_xyxy=list(coords),iscrowd=crowd)


def definition(images):
    return dict(name='Original authored detection dataset',labels=['red','blue'],note='Contract test, not road accuracy',images=images)


def create(api, body):
    response = api[0].post('/api/v1/datasets',json=body)
    assert response.status_code == 201,response.text
    return response.json()


def test_capture_uses_actual_pixels_exact_clocks_and_deduplicates(api):
    client, db, rid = api
    first = capture(api)
    assert capture(api) == first
    assert first['input']['log_time_ns'] == '1700000000000000000'
    assert first['input']['recording_id'] == rid and first['input']['decoder_id'] == 'foxglove-json'
    response = client.get(first['image']['download_url'])
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == first['image']['sha256']
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (32,16) and image.getpixel((10,10)) == (240,20,30)
    assert client.get('/api/v1/dataset-images/'+first['id']).json() == first
    assert len(list(db.dataset_image_dir.iterdir())) == 1
    assert list(db.dataset_staging_dir.iterdir()) == []


def test_concurrent_capture_converges_on_same_immutable_image(api):
    with ThreadPoolExecutor(max_workers=2) as pool:
        images = list(pool.map(lambda _:capture(api),range(2)))
    assert images[0] == images[1]
    assert len(list(api[1].dataset_image_dir.iterdir())) == 1


def test_reviewed_negative_is_distinct_from_unreviewed_empty(api):
    a, b = capture(api,0),capture(api,2)
    value = create(api,definition([member(a),member(b,reviewed=True)]))
    assert value['stats'] == dict(images=2,reviewed=1,unreviewed=1,annotations=0,negative_images=1,
                                 duplicate_image_groups=0,splits=dict(train=0,validation=0,test=2,unspecified=0))


def test_revision_is_immutable_and_review_changed_labels_is_explicit(api):
    client, db, _ = api
    asset = capture(api)
    body = definition([member(asset,reviewed=True,boxes=[box()])])
    first = create(api,body)
    before = client.get(first['download_url']).content
    changed = deepcopy(body);changed['images'][0]['annotations'][0]['bbox_xyxy']=[4,4,15,15]
    rejected=client.post(f"/api/v1/datasets/{first['id']}/revisions",json={**changed,'expected_revision':1})
    assert rejected.status_code == 422
    changed['images'][0]['review_note']='Reviewed the deliberately adjusted authored box'
    second=client.post(f"/api/v1/datasets/{first['id']}/revisions",json={**changed,'expected_revision':1})
    assert second.status_code == 201,second.text
    assert second.json()['revision'] == 2
    assert client.get(first['download_url']).content == before
    assert hashlib.sha256(before).hexdigest() == first['manifest_hash']
    assert client.get(f"/api/v1/datasets/{first['id']}").json()['revision'] == 2
    assert [r['revision'] for r in client.get(f"/api/v1/datasets/{first['id']}/revisions").json()] == [2,1]
    assert client.post(f"/api/v1/datasets/{first['id']}/revisions",json={**changed,'expected_revision':1}).status_code == 409


def test_taxonomy_addition_requires_renewed_review_or_unreviewed(api):
    asset=capture(api)
    body=definition([member(asset,reviewed=True)])
    original=create(api,body)
    changed={**body,'labels':['red','blue','new class'],'expected_revision':1}
    route=f"/api/v1/datasets/{original['id']}/revisions"
    assert api[0].post(route,json=changed).status_code == 422
    changed['images'][0]['review_status']='unreviewed'
    result=api[0].post(route,json=changed)
    assert result.status_code == 201 and result.json()['stats']['negative_images'] == 0


def test_split_only_revision_preserves_image_review(api):
    body=definition([member(capture(api),reviewed=True)])
    original=create(api,body);body['images'][0]['split']='validation'
    result=api[0].post(f"/api/v1/datasets/{original['id']}/revisions",json={**body,'expected_revision':1})
    assert result.status_code == 201 and result.json()['stats']['reviewed'] == 1


def test_dataset_read_stays_consistent_during_normal_revision_commit(api):
    client, db, _ = api
    body = definition([member(capture(api), reviewed=True)])
    original = create(api, body)
    route = f"/api/v1/datasets/{original['id']}"
    fired = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        def interleave(connection, cursor, statement, parameters, context, executemany):
            nonlocal fired
            if not fired and 'FROM datasets' in statement and 'datasets.revision' in statement:
                fired = True
                response = pool.submit(lambda: client.post(route + '/revisions', json={
                    **body, 'expected_revision': 1, 'name': 'Concurrently committed revision two'})).result(timeout=10)
                assert response.status_code == 201, response.text
        event.listen(db.engine, 'after_cursor_execute', interleave)
        try:
            response = client.get(route)
        finally:
            event.remove(db.engine, 'after_cursor_execute', interleave)
    assert fired
    assert response.status_code == 200, response.text
    assert response.json() == original
    assert client.get(route).json()['revision'] == 2


def test_duplicate_pixels_cannot_cross_assigned_splits(api):
    a,b=capture(api,0),capture(api,1)
    assert a['id'] != b['id'] and a['image']['sha256'] == b['image']['sha256']
    body=definition([member(a,split='train'),member(b,split='test')])
    assert api[0].post('/api/v1/datasets',json=body).status_code == 422
    body['images'][0]['split']='test'
    assert create(api,body)['stats']['duplicate_image_groups'] == 1


@pytest.mark.parametrize('field,value',[('reviewer',''),('review_note',' '),('annotation_source','unknown')])
def test_review_requires_current_declaration(api,field,value):
    item=member(capture(api),reviewed=True);item[field]=value
    assert api[0].post('/api/v1/datasets',json=definition([item])).status_code == 422


@pytest.mark.parametrize('change',['duplicate_ids','duplicate_labels','unknown_label','outside_bounds','unknown_field','too_many_boxes','blank_label'])
def test_invalid_dataset_authoring_fails_closed(api,change):
    item=member(capture(api),boxes=[box()]);body=definition([item])
    if change=='duplicate_ids':item['annotations'].append(box())
    elif change=='duplicate_labels':body['labels']=['red',' red ']
    elif change=='unknown_label':item['annotations'][0]['label']='not declared'
    elif change=='outside_bounds':item['annotations'][0]['bbox_xyxy']=[0,0,33,16]
    elif change=='unknown_field':item['annotations'][0]['ignore']=True
    elif change=='too_many_boxes':item['annotations']=[box(id=f'a{i}') for i in range(101)]
    elif change=='blank_label':body['labels']=[' ']
    assert api[0].post('/api/v1/datasets',json=body).status_code == 422


@pytest.mark.parametrize('coords',[[-1,0,1,1],[0,0,0,1],[0,2,1,1],[False,0,1,1],[0,0,float('nan'),1],[0,0,float('inf'),1]])
def test_annotation_requires_finite_positive_real_coordinates(coords):
    with pytest.raises(ValueError):DatasetAnnotation.model_validate(box(coords=coords))


def test_crowd_is_explicit_boolean(api):
    body=definition([member(capture(api),reviewed=True,boxes=[box(crowd=True)])])
    assert create(api,body)['images'][0]['annotations'][0]['iscrowd'] is True
    body['images'][0]['annotations'][0]['iscrowd']=1
    assert api[0].post('/api/v1/datasets',json=body).status_code == 422


@pytest.mark.parametrize('change',['ordinal','channel','decoder','version','bad_image'])
def test_wrong_message_or_decoder_does_not_create_assets(api,change):
    client,db,rid=api
    body=dict(recording_id=rid,channel_id=1,ordinal=0,decoder_id='foxglove-json',decoder_version='1')
    if change=='ordinal':body['ordinal']=99
    elif change=='channel':body['channel_id']=99
    elif change=='decoder':body['decoder_id']='unknown'
    elif change=='version':body['decoder_version']='999'
    else:body['ordinal']=3
    assert client.post('/api/v1/dataset-images',json=body).status_code in (404,422)
    assert list(db.dataset_image_dir.iterdir())==[] and list(db.dataset_staging_dir.iterdir())==[]


@pytest.mark.parametrize('change',['bytes','symlink','size','manifest','relation','membership','stats'])
def test_integrity_failures_hide_corrupt_assets_or_datasets(api,change,tmp_path):
    client,db,_=api
    asset=capture(api);dataset=create(api,definition([member(asset)]))
    path=db.dataset_image_dir/f"{asset['id']}.png"
    if change=='bytes':
        raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:])
    elif change=='symlink':
        raw=path.read_bytes();path.unlink();other=tmp_path/'external.png';other.write_bytes(raw);path.symlink_to(other)
    elif change=='size':path.write_bytes(b'x')
    else:
        with db.session() as session:
            if change=='manifest':session.get(DatasetImageRow,asset['id']).manifest += b' '
            elif change=='relation':session.get(DatasetImageRow,asset['id']).ordinal=1
            elif change=='membership':session.get(DatasetMembershipRow,(dataset['id'],1,asset['id'])).position=2
            elif change=='stats':
                row=session.get(DatasetRevisionRow,(dataset['id'],1));body=json.loads(row.manifest);body['stats']['negative_images']=1
                row.manifest=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
                row.manifest_hash=hashlib.sha256(row.manifest).hexdigest();row.manifest_size_bytes=len(row.manifest)
            session.commit()
    if change in ('bytes','symlink','size'):
        assert client.get(asset['image']['download_url']).status_code == 409
        assert client.post('/api/v1/datasets',json=definition([member(asset)])).status_code == 409
    else:
        assert client.get(dataset['download_url']).status_code == 409


def test_commit_rollback_removes_only_owned_image(api,monkeypatch):
    client,db,rid=api
    existing=capture(api,0);original=jobs._write
    @contextmanager
    def failed(database):
        with original(database) as session:
            yield session
            raise OSError('injected commit failure')
    monkeypatch.setattr(jobs,'_write',failed)
    with pytest.raises(OSError,match='injected'):
        capture(api,2)
    assert [p.name for p in db.dataset_image_dir.iterdir()]==[f"{existing['id']}.png"]
    assert list(db.dataset_staging_dir.iterdir())==[]
    with db.session() as session:assert len(list(session.scalars(select(DatasetImageRow))))==1


def test_authentication_protects_dataset_reads_writes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path,seed_examples=False,api_token='test-only-token'))) as client:
        assert client.get('/api/v1/datasets').status_code == 401
        assert client.post('/api/v1/dataset-images',json={}).status_code == 401
        assert client.get('/api/v1/datasets',headers={'Authorization':'Bearer test-only-token'}).status_code==200
