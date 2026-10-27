"""Authored topology/API fixtures only; never CARLA or sensor acceptance."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import pytest
from ade2e.scenario_v2_schemas import ScenarioCity
from ade2e.scenario_v2_yaml import parse_scenario_yaml,scenario_yaml

SPEC=importlib.util.spec_from_file_location('carla_city_acceptance',Path(__file__).resolve().parents[1]/'carla_city_acceptance.py')
suite=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(suite)


def point(x=0.,y=0.,h=0.):return dict(x=x,y=y,z=0.,heading_rad=h)


def native_fixture():
    nodes=[]
    for lane,y in [(-2,0),(-1,3.5)]:
        for i in range(33):
            nodes.append(dict(id=f'{lane}:{i}',**point(i*2,y),road_id=10,section_id=0,lane_id=lane,
                lane_width_m=3.5,is_junction=False,next_ids=[f'{lane}:{i+1}'] if i<32 else []))
    return {'name':'Authored fixture','native_name':'FixtureTown','nodes':nodes,'spawn_points':[point()], 'signals':[]}


class API:
    def __init__(self):self.calls=[];self.saved={};self.counter=0;self.failure=False
    def entry(self,identity,definition):return {'id':identity,'revision':1,'sha256':'a'*64,'definition':definition}
    def items(self,kind,project):
        if kind=='maps':return [self.entry('fixture-town',native_fixture())]
        if kind=='assets':return [self.entry('prius',{'blueprint_id':'vehicle.toyota.prius','actor_kind':'vehicle'}),
                                  self.entry('walker',{'blueprint_id':'walker.pedestrian.0001','actor_kind':'pedestrian'})]
        return [self.entry('city-'+mode,{'mode':mode,'status':'available'}) for mode in ('route_follow','traffic','static')]
    def request(self,path,body=None,key=None):
        self.calls.append((path,copy.deepcopy(body),key));self.counter+=1
        if path.startswith('/maps/'):return self.items('maps','default')[0]
        if path.startswith('/scenario-v2/yaml/'):
            parsed=parse_scenario_yaml(body['yaml']);value=parsed.model_dump(mode='json')
            result={'scenario':value,'configuration_hash':suite.digest(value),'canonical_yaml':scenario_yaml(parsed),
                    'catalog_validation':{'warnings':[]}}
            if path.endswith('/import'):
                result.update(id='scenario2_'+str(self.counter),revision=1,manifest_hash='b'*64)
                self.saved[result['id']]=result
            return result
        if path.startswith('/scenario-v2/'):
            return self.saved[path.removeprefix('/scenario-v2/').split('?')[0]]
        if path=='/experiment-verification-plans':return {'id':'plan_'+str(self.counter),'revision':1,'manifest_hash':'c'*64}
        if path=='/experiments':
            if self.failure:raise RuntimeError('API 422 /experiments: unsupported profile')
            return {'id':'experiment_fixture','preview_hash':'d'*64}
        if path.endswith('/executions'):
            return {'id':'job_'+str(self.counter),'status':'completed','manifest_hash':'e'*64,'summary':{},'outcome_uncertain':False}
        if path.endswith('/evaluations'):
            return {'id':'evaluation_'+str(self.counter),'manifest_hash':'f'*64,'verdict':'passed',
                    'counts':{'passed':1},'entries':[{'status':'passed'}]}
        raise AssertionError('Unexpected API path '+path)


def test_same_direction_cut_in_remains_inside_authored_two_lane_corridor():
    definition=native_fixture();path=[n for n in definition['nodes'] if n['lane_id']==-2][:22]
    change=suite.adjacent_cut_in(path,definition)
    assert change['from_lane']==-1 and change['to_lane']==-2
    assert change['route'][0]['y']==pytest.approx(3.5)
    assert change['route'][-1]['y']==pytest.approx(0)
    assert all(0<=p['y']<=3.5 and abs(p['heading_rad'])<.5 for p in change['route'])
    opposite=copy.deepcopy(definition)
    for node in opposite['nodes']:
        if node['lane_id']==-1:node['lane_id']=1;node['heading_rad']=math.pi
    assert suite.adjacent_cut_in(path,opposite) is None


def test_stop_profiles_keep_stop_criterion_when_goal_not_required():
    values=suite.criteria(require_goal=False)
    assert not any(c['metric']=='goal_error' for c in values)
    assert next(c for c in values if c['metric']=='final_speed')['threshold']==.2
    assert next(c for c in values if c['metric']=='native_contact')['actor_id']=='all'


def test_author_pins_new_scenarios_and_plans_without_submitting_experiments():
    api=API();result=suite.build(api,'default',None,'route_follow',True,'fixed-test-suite')
    assert result['coverage']=={'requested_case_slots':9,'authored':5,'draft':0,'unsupported':4}
    assert not any(path.startswith('/experiments') for path,_,_ in api.calls)
    for item in result['cases']:
        if item['status']=='authored':
            assert item['scenario']['seed']==42
            assert ScenarioCity.model_validate(item['scenario'])
            assert item['configuration_hash']==suite.digest(item['scenario'])
    suite.validate_suite(result)
    corrupted=copy.deepcopy(result);next(c for c in corrupted['cases'] if c['status']=='authored')['criteria'][0]['threshold']=True
    with pytest.raises(ValueError,match='hash'):suite.validate_suite(corrupted)


def one_case(api):
    result=suite.build(api,'default',None,'route_follow',True,'fixed-test-suite')
    result['cases']=[next(c for c in result['cases'] if c['status']=='authored')]
    result['coverage']={'requested_case_slots':1,'authored':1,'draft':0,'unsupported':0}
    result['suite_sha256']=suite.digest({k:v for k,v in result.items() if k!='suite_sha256'})
    return result


def test_three_repeats_keep_same_frozen_configuration_and_distinct_stable_keys(tmp_path):
    api=API();manifest=one_case(api)
    result=suite.run(api,manifest,3,tmp_path/'result.json',poll_s=0)
    assert result['complete'] and result['counts']['passed']==3
    assert len({t['configuration_hash'] for t in result['trials']})==1
    assert len({t['seed'] for t in result['trials']})==1
    submitted=[(path,body,key) for path,body,key in api.calls if path.endswith('/executions')]
    assert len(submitted)==3 and len({k for _,_,k in submitted})==3
    assert {suite.digest(body) for _,body,_ in submitted}=={suite.digest(submitted[0][1])}
    assert json.loads((tmp_path/'result.json').read_bytes())==result


def test_native_preflight_failure_is_recorded_and_no_execution_is_queued(tmp_path):
    api=API();manifest=one_case(api);api.calls=[];api.failure=True
    result=suite.run(api,manifest,3,tmp_path/'result.json',poll_s=0)
    assert not result['complete'] and result['counts']['unsupported']==1
    assert not any(path.endswith('/executions') for path,_,_ in api.calls)
    assert result['trials'][0]['status']=='not_dispatched'


def test_run_map_selection_keeps_original_suite_hash_and_unselected_cases(tmp_path):
    api=API();manifest=one_case(api)
    excluded=copy.deepcopy(manifest['cases'][0]);excluded['map']['id']='unselected-town'
    manifest['cases'].append(excluded);manifest['coverage']['requested_case_slots']=2;manifest['coverage']['authored']=2
    manifest['suite_sha256']=suite.digest({k:v for k,v in manifest.items() if k!='suite_sha256'})
    before=copy.deepcopy(manifest);api.calls=[]
    result=suite.run(api,manifest,1,tmp_path/'selected.json',poll_s=0,maps=['fixture-town'])
    assert manifest==before and result['suite_sha256']==before['suite_sha256']
    assert result['selection']=={'map_ids':['fixture-town'],'case_names':[manifest['cases'][0]['case']],'selected_case_slots':1,'excluded_case_slots':1}
    assert len(result['trials'])==1 and result['requested_case_slots']==2
    assert result['worker_mode']=='existing_operator_worker_required'
    api.calls=[]
    with pytest.raises(ValueError,match='absent from the sealed suite'):
        suite.run(api,manifest,1,tmp_path/'unknown.json',maps=['typo-town'])
    assert api.calls==[]


def test_native_red_approach_preserves_every_directed_edge_and_has_minimum_runup():
    definition=native_fixture();base=[n for n in definition['nodes'] if n['lane_id']==-2][12:]
    extended=suite.prepend_native_approach(base,definition)
    assert suite.length(extended)-suite.length(base)>=20
    assert extended[-len(base):]==base
    assert all(b['id'] in a['next_ids'] for a,b in zip(extended,extended[1:]))
    insufficient=[n for n in definition['nodes'] if n['lane_id']==-2][3:]
    assert suite.prepend_native_approach(insufficient,definition) is None


def test_traffic_acceptance_pins_green_and_red_has_native_approach(monkeypatch):
    api=API();native=api.items('maps','default')[0];definition=native['definition']
    definition['signals']=[{'id':'registered-light','kind':'traffic_light','pose':point(60)}]
    lane=[n for n in definition['nodes'] if n['lane_id']==-2]
    # Authored graph fixture supplies a nominated turn; no native geometry claim.
    monkeypatch.setattr(suite,'choose_paths',lambda _:dict(straight=lane[:22],left_turn=lane[12:]))
    items=suite.definitions(native,api.items('assets','default'),api.items('controllers','default'))
    for item in items:
        if item['status']=='draft' and item['case'] in {'lead_stop','pedestrian_crossing','obstacle_response'}:
            assert item['scenario']['signals']==[{'id':'registered-light','initial_state':'green','changes':[]}]
            assert 'isolate_actor_response' in item['authoring']['signal_policy']
    red=next(v for v in items if v['case']=='red_light_stop')
    assert red['status']=='draft' and red['authoring']['native_predecessor_approach_m']>=20
    assert red['scenario']['signals'][0]['initial_state']=='red'
    assert red['criteria']==suite.criteria(require_goal=False)
    assert red['scenario']['duration_s']==18


def test_case_selection_preserves_suite_and_does_not_dispatch_other_categories(tmp_path):
    api=API();manifest=suite.build(api,'default',None,'route_follow',True,'fixed-test-suite');before=copy.deepcopy(manifest)
    api.calls=[]
    result=suite.run(api,manifest,1,tmp_path/'only-straight.json',poll_s=0,case_names=['straight'])
    assert manifest==before and result['suite_sha256']==manifest['suite_sha256']
    assert len(result['trials'])==1 and result['trials'][0]['case']=='straight'
    assert result['selection']['case_names']==['straight']
    assert result['selection']['excluded_case_slots']==8
    api.calls=[]
    with pytest.raises(ValueError,match='case is absent'):
        suite.run(api,manifest,1,tmp_path/'bad-case.json',case_names=['typo-case'])
    assert api.calls==[]
