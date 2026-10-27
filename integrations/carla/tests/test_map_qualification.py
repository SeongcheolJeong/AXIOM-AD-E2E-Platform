"""Authored CARLA API doubles; these do not establish native execution."""
from types import SimpleNamespace
import json
import pytest
from axiom_carla import city_native
from axiom_carla import city
from test_city_contract import fixture_pair


@pytest.fixture
def native(tmp_path, monkeypatch):
    text='<OpenDRIVE><road id="1"/></OpenDRIVE>'
    data={'nodes':[{'x':0,'y':0,'z':0}], 'spawn_points':[]}
    world=SimpleNamespace(get_actors=lambda:[],get_map=lambda:SimpleNamespace(to_opendrive=lambda:text))
    calls=[]
    client=SimpleNamespace(get_world=lambda:world,get_server_version=lambda:'build',
        generate_opendrive_world=lambda content,parameters:calls.append(content) or world)
    carla=SimpleNamespace(OpendriveGenerationParameters=lambda **kw:kw)
    monkeypatch.setattr(city_native,'map_definition',lambda *_:data)
    monkeypatch.setattr(city_native,'CANCELLED',False)
    payload={'schema_version':'carla-map-qualification-input/1','server_version':'build','opendrive':text,
        'candidate_sha256':city_native.sha(text.encode()),'source':{'fixture':True},
        'source_ref':{'id':'s','revision':1,'sha256':'a'*64},'conversion_ref':{'id':'c','revision':1,'sha256':'b'*64}}
    return payload,tmp_path/'attempt',carla,client,{'clean':False},data,calls


def test_load_readback_exact_bytes_has_no_driving_claim(native):
    payload,out,carla,client,state,_,calls=native
    result=city_native.qualify_map(payload,out,carla,client,state)
    report=json.loads((out/'map-qualification.json').read_bytes())
    assert len(calls)==1 and state['clean'] and result['cleanup_status']=='confirmed'
    assert report['driving_evaluated'] is False and report['physical_validation'] is False
    assert report['input_opendrive_sha256']==report['native_opendrive_sha256']


def test_candidate_corruption_blocks_world_generation(native):
    payload,out,carla,client,state,_,calls=native
    payload['candidate_sha256']='0'*64
    with pytest.raises(ValueError,match='hash mismatch'):
        city_native.qualify_map(payload,out,carla,client,state)
    assert calls==[]


def test_readback_difference_never_publishes_success(native):
    payload,out,carla,client,state,_,_=native
    client.get_world().get_map=lambda:SimpleNamespace(to_opendrive=lambda:'changed')
    with pytest.raises(ValueError,match='differs'):
        city_native.qualify_map(payload,out,carla,client,state)
    assert json.loads((out/'state.json').read_bytes())['status']=='failed'
    assert json.loads((out/'state.json').read_bytes())['native_opendrive_sha256']==city_native.sha(b'changed')
    assert (out/'native-readback.xodr').read_bytes()==b'changed'
    assert not (out/'map-qualification.json').exists()


def test_global_coordinates_block_qualification(native):
    payload,out,carla,client,state,data,_=native
    data['nodes'][0]['x']=100000
    with pytest.raises(ValueError,match='coordinate'):
        city_native.qualify_map(payload,out,carla,client,state)


def test_driving_scene_retains_exact_qualified_external_lineage():
    snapshot,scene=fixture_pair(False)
    provenance={'source':{'id':'source','revision':1,'sha256':'a'*64},'qualification_execution_id':'job_fixture'}
    snapshot['external_map_provenance']=provenance
    scene['map']['external']=dict(provenance)
    assert city.validate_scene(scene,snapshot) is scene
    scene['map']['external']['qualification_execution_id']='another-job'
    with pytest.raises(ValueError,match='external map provenance'):
        city.validate_scene(scene,snapshot)
