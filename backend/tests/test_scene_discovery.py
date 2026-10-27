"""Actual captured RGB inputs through scene discovery; not semantic accuracy."""
import hashlib
import io

from PIL import Image, ImageDraw
import pytest

from ade2e import scene_features
from test_datasets import api, capture, member


def post(client, path, body, key='test', code=201):
    response = client.post('/api/v1'+path, json=body, headers={'Idempotency-Key':key})
    assert response.status_code == code, response.text
    return response.json()


@pytest.fixture
def context(api):
    client, db, rid = api
    assets = [capture(api,i) for i in range(3)]
    dataset = post(client,'/datasets',{'name':'Appearance inputs','labels':['declared-object'],
        'note':'Original authored pixels', 'images':[member(a) for a in assets]})
    case = post(client,'/cases',{'title':'All source messages','note':'Retain uncaptured input',
        'source':{'kind':'recording','recording_id':rid,'channel_id':1,'start_time_ns':'0','end_time_ns':str(2**64-1)}})
    definition={'name':'Scene sources','purpose':'Inspect all originals','sources':[
        {'kind':'dataset','dataset_id':dataset['id'],'revision':1,'reason':'Three captured images'},
        {'kind':'case','case_id':case['id'],'revision':1,'reason':'Include uncaptured message'}]}
    p=post(client,'/collections/preview',definition,code=200)
    collection=post(client,'/collections',{**definition,'expected_preview_hash':p['preview_hash']})
    selection={'collection_id':collection['id'],'collection_revision':1,'name':'Appearance index','note':'Not semantic detection'}
    return client,db,collection,assets,selection


def make_index(ctx, key='index'):
    p=post(ctx[0],'/scene-indexes/preview',ctx[4],code=200)
    assert p['computed_images']==p['writes_created']==0
    return post(ctx[0],'/scene-indexes',{**ctx[4],'expected_preview_hash':p['preview_hash'],'confirm_local_analysis':True},key)


def search(ctx,index,**query):
    return post(ctx[0],f'/scene-indexes/{index["id"]}/search',query,code=200)


def row_at(index, ordinal):
    return next(r for r in index['rows'] if r['source']['ordinal']==ordinal)


def test_full_denominator_actual_pixels_and_stable_similarity(context):
    index=make_index(context)
    assert index['counts']=={'indexed':3,'not_captured':1}
    first,duplicate,blue=(row_at(index,n) for n in (0,1,2))
    assert first['features']==duplicate['features']
    assert first['features']['metrics']['mean_code']==round((240*.2126+20*.7152+30*.0722)/255,9)
    assert 'dark_image' in [p['tag'] for p in blue['features']['proposals']]
    assert all('night' not in str(r['features']) for r in index['rows'])
    result=search(context,index,anchor_key=first['key'])
    matches=[r for r in result['rows'] if r['matched']]
    assert [r['source']['ordinal'] for r in matches]==[1,2]
    assert matches[0]['distance']==0 and matches[0]['identical_pixels_to_query']
    assert matches[1]['distance']>0
    assert result['denominator']=={'all_sources':4,'indexed':3,'unindexed':1,'matched':2,'filtered_out':2,'reviewed':0,'pending_or_draft':4}
    assert result['duplicate_pixel_groups']==[[first['key'],duplicate['key']]]
    filtered=search(context,index,anchor_key=first['key'],exclude_exact_pixels=True)
    assert [r['source']['ordinal'] for r in filtered['rows'] if r['matched']]==[2]
    assert search(context,index,anchor_key=first['key'],exclude_same_recording=True)['denominator']['matched']==0
    exported=context[0].get(result['download_url'])
    assert hashlib.sha256(exported.content).hexdigest()==result['manifest_hash']
    assert len(exported.json()['rows'])==4
    assert make_index(context)==index


def test_reviewed_declarations_are_separate_from_rules_and_old_queries(context):
    index=make_index(context);blue=row_at(index,2);path=f'/scene-indexes/{index["id"]}/reviews'
    old=search(context,index,lighting='night')
    assert old['denominator']['matched']==0
    body={'expected_review_revision':0,'source_key':blue['key'],'lighting':'night','weather':'rain',
        'accepted_feature_tags':['dark_image'],'status':'draft','reviewer':'Declared reviewer','note':'Workflow declaration only'}
    assert post(context[0],path,body,'draft')['revision']==1
    assert search(context,index,lighting='night')['denominator']['matched']==0
    assert post(context[0],path,{**body,'expected_review_revision':1,'status':'reviewed'},'review')['revision']==2
    current=search(context,index,lighting='night',feature_tag='dark_image',feature_scope='reviewed')
    assert current['denominator']['matched']==1 and current['denominator']['pending_or_draft']==3
    assert current['query']['review_revision']==2
    assert context[0].get(old['download_url']).status_code==200
    exported=context[0].get(current['download_url']).content
    post(context[0],path,{**body,'expected_review_revision':2,'status':'reviewed','lighting':'day'},'changed')
    assert context[0].get(current['download_url']).content==exported
    assert search(context,index,lighting='night')['denominator']['matched']==0
    assert post(context[0],path,body,'draft')['revision']==1


def test_selected_results_create_collection_with_complete_origin(context):
    index=make_index(context);result=search(context,index,anchor_key=row_at(index,0)['key'])
    body={'query':result['query'],'expected_search_hash':result['manifest_hash'],
        'selected_keys':[r['key'] for r in result['rows'] if r['matched']],
        'name':'Similar sources','purpose':'Review before evaluation'}
    path=f'/scene-indexes/{index["id"]}/collections'
    saved=post(context[0],path,body,'selected')
    assert post(context[0],path,body,'selected')==saved
    assert len(saved['not_selected_keys'])==2 and len(saved['search']['rows'])==4
    assert saved['labels_copied'] is False
    collection=context[0].get('/api/v1/collections/'+saved['collection']['id']).json()
    assert collection['selection']['summary']['unique_messages']==2
    assert all(not r['label_options'] for r in collection['selection']['rows'])
    origins=context[0].get('/api/v1/scene-selections',params={'collection_id':collection['id']}).json()
    assert len(origins['items'])==1
    assert hashlib.sha256(context[0].get(saved['download_url']).content).hexdigest()==saved['manifest_hash']


def test_layout_distance_and_lossless_encoding_equivalence():
    base=Image.new('RGB',(64,32),(15,20,25))
    ImageDraw.Draw(base).rectangle((10,8,30,25),fill=(220,40,30))
    near=base.copy();ImageDraw.Draw(near).rectangle((11,8,31,25),fill=(220,40,30))
    different=Image.new('RGB',(64,32),(30,210,180))
    def feature(image,compression=6):
        raw=io.BytesIO();image.save(raw,format='PNG',compress_level=compression)
        return scene_features.analyze(raw.getvalue())
    original=feature(base)
    assert original==feature(base,0)
    assert 0 < scene_features.distance(original,feature(near)) < scene_features.distance(original,feature(different))
    assert scene_features.distance(original,original)==0


@pytest.mark.parametrize('changes',[{'confirm_local_analysis':False},{'confirm_local_analysis':1},{'expected_preview_hash':'0'*64}])
def test_unapproved_or_stale_workload_returns_no_index(context,changes):
    client=context[0];preview=post(client,'/scene-indexes/preview',context[4],code=200)
    body={**context[4],'confirm_local_analysis':True,'expected_preview_hash':preview['preview_hash'],**changes}
    response=client.post('/api/v1/scene-indexes',json=body,headers={'Idempotency-Key':'invalid'})
    assert response.status_code in (409,422)
    assert client.get('/api/v1/scene-indexes').json()['items']==[]


@pytest.mark.parametrize('query',[{'anchor_key':'0'*64},{'exclude_same_recording':True},{'review_revision':True},{'review_revision':999}])
def test_invalid_queries_do_not_choose_a_different_source(context,query):
    index=make_index(context)
    response=context[0].post(f'/api/v1/scene-indexes/{index["id"]}/search',json=query)
    assert response.status_code in (404,422)


def test_review_rejects_unindexed_source_and_unproposed_feature(context):
    index=make_index(context)
    for ordinal,tags in [(3,[]),(0,['bright_image'])]:
        response=context[0].post(f'/api/v1/scene-indexes/{index["id"]}/reviews',headers={'Idempotency-Key':str(ordinal)},
            json={'expected_review_revision':0,'source_key':row_at(index,ordinal)['key'],'accepted_feature_tags':tags,
                  'status':'reviewed','reviewer':'Test declaration','note':'Test invalid selection'})
        assert response.status_code==422
    assert context[0].get(f'/api/v1/scene-indexes/{index["id"]}/reviews').json()['revision']==0


def test_collection_rejects_query_image_and_stale_search_snapshot(context):
    index=make_index(context);first=row_at(index,0)
    result=search(context,index,anchor_key=first['key'])
    before=context[0].get('/api/v1/collections').json()['total']
    body={'query':result['query'],'expected_search_hash':result['manifest_hash'],'selected_keys':[first['key']],
          'name':'Explicit selection','purpose':'Only matched results may be used'}
    path=f'/api/v1/scene-indexes/{index["id"]}/collections'
    assert context[0].post(path,json=body,headers={'Idempotency-Key':'self'}).status_code==422
    body.update(selected_keys=[row_at(index,1)['key']],expected_search_hash='0'*64)
    assert context[0].post(path,json=body,headers={'Idempotency-Key':'stale'}).status_code==409
    assert context[0].get('/api/v1/collections').json()['total']==before


def test_concurrent_identical_creation_publishes_one_index(context):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:make_index(context),range(2)))
    assert results[0]==results[1]
    assert len(context[0].get('/api/v1/scene-indexes').json()['items'])==1


def test_agent_discovery_marks_read_and_compute_separately(context):
    tools={item['name']:item for item in context[0].get('/api/v1/agent/tools').json()['tools']}
    assert tools['search_scenes']['read_only'] and tools['preview_scene_index']['read_only']
    assert not tools['create_scene_index']['read_only']
    assert tools['create_scene_index']['required_headers']==['Idempotency-Key']


def test_source_read_failure_stays_visible_and_has_no_descriptor(context, monkeypatch):
    from fastapi import HTTPException
    from ade2e import collections
    original = collections._verify_selected_bytes
    def verify(db, session, assets):
        if any(a.input.ordinal == 0 for a in assets):
            raise HTTPException(409, 'Test-only unavailable original')
        return original(db, session, assets)
    monkeypatch.setattr(collections, '_verify_selected_bytes', verify)
    index = make_index(context)
    row = row_at(index, 0)
    assert row['status'] == 'unavailable' and row['features'] is None
    assert len(search(context, index)['rows']) == 4
    assert search(context, index)['denominator']['unindexed'] == 2


def test_selection_receipt_failure_does_not_leave_orphan_collection(context, monkeypatch):
    from ade2e import collections
    index = make_index(context)
    result = search(context, index)
    before = context[0].get('/api/v1/collections').json()['total']
    original = collections._seal
    def seal(value):
        if value.get('schema_version') == 'scene-collection-selection/1':
            raise RuntimeError('Test-only receipt publication failure')
        return original(value)
    monkeypatch.setattr(collections, '_seal', seal)
    with pytest.raises(RuntimeError, match='Test-only'):
        post(context[0], f'/scene-indexes/{index["id"]}/collections', {
            'query': result['query'], 'expected_search_hash': result['manifest_hash'],
            'selected_keys': [row_at(index, 0)['key']], 'name': 'Atomic publication', 'purpose': 'Keep receipt and collection together'})
    assert context[0].get('/api/v1/collections').json()['total'] == before


def test_other_feature_runtime_does_not_rescore_old_index(context, monkeypatch):
    index = make_index(context)
    old = scene_features.recipe()
    monkeypatch.setattr(scene_features, 'recipe', lambda: {**old, 'source_sha256': '0'*64})
    response = context[0].post(f'/api/v1/scene-indexes/{index["id"]}/search', json={'anchor_key': row_at(index, 0)['key']})
    assert response.status_code == 409
    assert context[0].get(index['download_url']).status_code == 200
