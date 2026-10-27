"""Authored lease/probe fixtures; no simulator is started by these checks."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from ade2e.job_models import EngineResourceRow
from axiom_carla import city, city_control, city_native, city_maneuvers
from test_city_contract import fixture_pair, point


@pytest.fixture
def prepared_queue(monkeypatch):
    snapshot,_=fixture_pair(False);scenario=snapshot['scenario']
    resource=SimpleNamespace(state='reserved',job_id='owned_job',owner_token='owned_lease')
    row=SimpleNamespace(kind='experiment',status='running',dispatched_at='2026-10-10T12:00:00+00:00',
        outcome_uncertain=False,resource_required=True,resource_key='carla-city-2160',owner_token='owned_lease',
        adapter_id='carla-city-object',adapter_version=city.VERSION,
        lease_expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),prepared={})
    class Session:
        def get(self,cls,key):
            assert cls is EngineResourceRow and key=='carla-city-2160'
            return resource
        def scalar(self,query):
            assert list(query.compile().params.values())==['owned_job']
            return row
    handler=city.CityExperimentHandler(database=SimpleNamespace(read_session=lambda:nullcontext(Session())))
    handler.port=2160;row.prepared={'input':{'operator_transport_hash':handler.transport_hash()}}
    original={'project_id':'default','scenario':scenario,'id':'scenario2_fixture','revision':1,'manifest_hash':'b'*64}
    native={'sha256':'a'*64,'definition':{'server_version':'fixture-build','native_name':'FixtureTown',
        'opendrive':{'sha256':'c'*64},'nodes':[dict(point(),id='start',next_ids=['end']),dict(point(9),id='end',next_ids=[])]}}
    monkeypatch.setattr(city,'get_scenario',lambda *args:original)
    monkeypatch.setattr(city,'validate_city_catalogs',lambda *args:None)
    monkeypatch.setattr(city,'catalog_reference',lambda session,kind,*args:native if kind=='map' else {'definition':{'blueprint_id':'vehicle.fixture'}})
    probe={'native_script_sha256':city.file_sha(city_native.__file__),'control_script_sha256':city.file_sha(city_control.__file__),
        'maneuver_script_sha256':city.file_sha(city_maneuvers.__file__),'adapter_version':city.VERSION,
        'dynamic_actor_ids':[123,124],'server_version':'fixture-build','available_maps':['FixtureTown'],
        'vehicle_blueprints':['vehicle.fixture'],'walker_blueprints':[],'static_blueprints':[]}
    monkeypatch.setattr(handler,'native_probe',lambda:probe)
    spec=SimpleNamespace(kind='object_sim',comparison_kind='single',model=None,sensor=None,evaluator=None,
        project_id='default',seed=scenario['seed'],input={'scenario_id':'scenario2_fixture','scenario_revision':1,
            'configuration_hash':city.digest(scenario)})
    return handler,spec,probe,resource,row


def test_preview_can_queue_behind_own_live_dispatch_without_changing_frozen_input(prepared_queue):
    handler,spec,probe,resource,_=prepared_queue
    busy=handler.prepare(spec)
    probe['dynamic_actor_ids']=[];resource.state='idle'
    idle=handler.prepare(spec)
    assert busy==idle, 'Engine occupancy must not race the preview/create immutable input hash'
    assert busy['workload']['empty_world_check']=='exclusive_native_dispatch'
    assert busy['workload']['exclusive_endpoint'] is True


@pytest.mark.parametrize('mutation',[
    lambda resource,row:setattr(resource,'state','idle'),
    lambda resource,row:setattr(resource,'state','needs_reconciliation'),
    lambda resource,row:setattr(row,'status','completed'),
    lambda resource,row:setattr(row,'dispatched_at',None),
    lambda resource,row:setattr(row,'outcome_uncertain',True),
    lambda resource,row:setattr(row,'lease_expires_at','2000-01-01T00:00:00+00:00'),
    lambda resource,row:setattr(row,'owner_token','another_lease'),
    lambda resource,row:setattr(row,'resource_key','carla-city-2200'),
    lambda resource,row:row.prepared['input'].update(operator_transport_hash='another_transport'),
])
def test_preview_still_rejects_unowned_stale_or_uncertain_occupied_world(prepared_queue,mutation):
    handler,spec,_,resource,row=prepared_queue;mutation(resource,row)
    with pytest.raises(ValueError,match='foreign actors'):handler.prepare(spec)


def test_native_capture_still_rejects_foreign_actors_before_world_mutation(tmp_path):
    class World:
        def get_actors(self):return [SimpleNamespace(type_id='vehicle.foreign')]
        def get_settings(self):pytest.fail('Occupied native world reached a mutable capture setup')
    client=SimpleNamespace(get_world=lambda:World())
    lease_state={'clean':False}
    with pytest.raises(RuntimeError,match='foreign dynamic actors'):
        city_native.capture({'scenario':{},'source':{}},tmp_path/'capture',None,client,lease_state,None)
    assert lease_state['clean'] is False
