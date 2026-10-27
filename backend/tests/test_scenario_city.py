"""Authoring/catalog contracts only: these tests do not run CARLA or Autoware."""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from ade2e.database import Database, Settings
from ade2e.scenario_v2 import register_scenario_v2_routes
from ade2e.scenario_v2_schemas import ScenarioCity,ScenarioV2,CityActor,Actor
from ade2e.city_motion import FIDELITIES
from ade2e.scenario_v2_yaml import parse_scenario_yaml, scenario_yaml, yaml_result
from ade2e.simulation_catalog import register_simulation_catalog_routes
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import Principal
from ade2e.agent_catalog import catalog


RIGHTS={'license':'test-fixture-only','redistribution':'restricted','notes':'Authored contract fixtures; not native map evidence'}
XODR='<OpenDRIVE><header name="test-fixture"/></OpenDRIVE>'

def pose(x=0,y=0,z=0,heading_rad=0):
    return dict(x=x,y=y,z=z,heading_rad=heading_rad)

def map_definition():
    return {'resource_kind':'map','name':'Authored graph fixture','native_name':'FixtureTown',
            'server_version':'contract-test','opendrive':{'content':XODR,'sha256':hashlib.sha256(XODR.encode()).hexdigest()},
            'nodes':[dict(id=str(i),**pose(x=i*3),road_id=1,section_id=0,lane_id=-1,lane_width_m=3.5,
                          next_ids=[str(i+1)] if i<3 else []) for i in range(4)],
            'spawn_points':[pose()],'rights':RIGHTS}

def asset_definition():
    return {'resource_kind':'asset','name':'Fixture vehicle','blueprint_id':'vehicle.fixture',
            'actor_kind':'vehicle','rights':RIGHTS}

def reference(value):
    return {key:value[key] for key in ('id','revision','sha256')}


class ScenarioCityApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.db=Database(Settings(data_dir=Path(self.directory.name),seed_examples=False));self.db.initialize()
        self.principal=Principal('legacy')
        def auth(request:Request):request.state.principal=self.principal
        router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
        register_simulation_catalog_routes(router,self.db);register_scenario_v2_routes(router,self.db)
        app=FastAPI();app.include_router(router)
        @app.get('/api/v1/agent/tools',dependencies=[Depends(auth)])
        def agent_tools(request:Request):return catalog(app,request,[],[])
        self.client=TestClient(app)
        self.native_map=self.create('maps','fixture-town',map_definition())
        self.asset=self.create('assets','fixture-vehicle',asset_definition())
        self.scenario={'schema_version':'scenario-v2/2','name':'Native city contract fixture','map':reference(self.native_map),
            'actors':[{'id':'ego','role':'ego','asset':reference(self.asset),'pose':pose(),
                       'route':[pose(),pose(9)],'controller':{'kind':'route_follow'}}]}

    def tearDown(self):
        self.db.engine.dispose();self.directory.cleanup()

    def create(self,kind,identity,definition,expected_revision=0):
        result=self.client.post('/api/v1/'+kind,json={'id':identity,'definition':definition,'expected_revision':expected_revision})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()

    def test_capabilities_separate_native_schema_and_legacy_flat_scope_without_runtime_claim(self):
        response=self.client.get('/api/v1/scenario-v2/capabilities')
        self.assertEqual(response.status_code,200,response.text)
        value=response.json();profiles=value['schema_profiles']
        self.assertEqual(set(profiles),set(value['supported_schema_versions']))
        legacy=profiles['scenario-v2/1'];city=profiles['scenario-v2/2']
        self.assertEqual(value['legacy_flat_fields_scope']['schema_version'],'scenario-v2/1')
        self.assertEqual(value['actions'],legacy['actions'])
        self.assertEqual(value['triggers'],legacy['triggers'])
        self.assertEqual(value['limitations'],legacy['limitations'])
        self.assertEqual(value['fidelity'],'authored_object_kinematics')
        self.assertEqual(value['limits'],{'actors':8,'duration_s':30,'sweep_configurations':64,'sweep_jobs':128})
        # Published authoring bounds must describe the actual schemas, not the
        # older adapter or the stricter runtime acceptance profile.
        for profile,model,actor in [(legacy,ScenarioV2,Actor),(city,ScenarioCity,CityActor)]:
            props=model.model_json_schema()['properties'];actor_props=actor.model_json_schema()['properties']
            bounds=profile['schema_bounds']
            self.assertEqual(bounds['actors'],{'min':props['actors']['minItems'],'max':props['actors']['maxItems']})
            for field in ('duration_s','timestep_s'):
                self.assertEqual(bounds[field],{'min':props[field]['minimum'],'max':props[field]['maximum']})
            self.assertEqual(bounds['actor_speed_mps'],{'min':actor_props['speed_mps']['minimum'],'max':actor_props['speed_mps']['maximum']})
            self.assertTrue(profile['execution_preflight']['required'])
            self.assertEqual(profile['execution_preflight']['availability'],'not_probed_by_this_endpoint')
            self.assertFalse(profile['physical_sensor_validation'])
        self.assertEqual(city['schema_bounds']['actors']['max'],32)
        self.assertEqual(city['schema_bounds']['duration_s']['max'],120)
        self.assertEqual(set(city['controller_modes']),{'timed_replay','route_follow','traffic','autoware','static'})
        self.assertEqual(set(city['fidelities']),FIDELITIES)
        self.assertNotIn(value['fidelity'],city['fidelities'])
        self.assertFalse(city['sweep']['supported'])
        self.assertEqual(city['execution_preflight']['bounds']['actor_speed_mps_max'],12)
        self.assertGreater(city['schema_bounds']['actor_speed_mps']['max'],city['execution_preflight']['bounds']['actor_speed_mps_max'])
        self.assertIn('Legacy observers are not executed',' '.join(city['execution_preflight']['constraints']))
        self.assertIn('stop-line verdict',' '.join(city['limitations']))

    def test_yaml_semantic_hash_original_source_and_immutable_revision(self):
        first='# Original author comment\n'+json.dumps(self.scenario)
        validated=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':first})
        self.assertEqual(validated.status_code,200,validated.text)
        normalized=validated.json()
        self.assertEqual(normalized['catalog_validation']['status'],'validated')
        again=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':normalized['canonical_yaml']})
        self.assertEqual(again.json()['configuration_hash'],normalized['configuration_hash'])
        saved=self.client.post('/api/v1/scenario-v2/yaml/import',json={'yaml':first},headers={'Idempotency-Key':'yaml-create'})
        self.assertEqual(saved.status_code,201,saved.text)
        result=saved.json();identity=result['id'];original_hash=result['manifest_hash']
        self.assertEqual(result['source_yaml'],first)
        self.assertEqual(result['configuration_hash'],normalized['configuration_hash'])
        exported=self.client.get(f'/api/v1/scenario-v2/{identity}/export?format=source_yaml')
        self.assertEqual(exported.text,first)
        self.assertEqual(exported.headers['X-Content-SHA256'],hashlib.sha256(first.encode()).hexdigest())
        self.scenario['name']='Changed goal'
        revised=self.client.post(f'/api/v1/scenario-v2/{identity}/revisions',json={'expected_revision':1,'scenario':self.scenario},
            headers={'Idempotency-Key':'revise'})
        self.assertEqual(revised.status_code,201,revised.text)
        self.assertEqual(self.client.get(f'/api/v1/scenario-v2/{identity}?revision=1').json()['manifest_hash'],original_hash)
        self.assertEqual(self.client.get(f'/api/v1/scenario-v2/{identity}/export?format=source_yaml').status_code,404)
        self.assertEqual(self.client.get(f'/api/v1/scenario-v2/{identity}/export?format=openscenario').status_code,422)
        self.assertEqual(self.client.get(f'/api/v1/scenario-v2/{identity}/export?format=opendrive').text,XODR)
        self.assertEqual(self.client.post(f'/api/v1/scenario-v2/{identity}/sweep-preview',json={}).status_code,422)

    def test_reject_unsafe_duplicates_cycles_unknown_and_mismatched_original(self):
        invalid=['name: first\nname: second','x: &x [*x]','!!python/object/apply:os.system ["echo unsafe"]',
                 '<<: {name: bad}','1: bad','[]','---\nname: first\n---\nname: second']
        for value in invalid:
            with self.subTest(value=value):
                response=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':value})
                self.assertEqual(response.status_code,422,response.text)
        bad={**self.scenario,'silent_unknown':True}
        self.assertEqual(self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':json.dumps(bad)}).status_code,422)
        altered={**self.scenario,'name':'Different document'}
        response=self.client.post('/api/v1/scenario-v2',json={'scenario':self.scenario,'source_yaml':json.dumps(altered)},
                                  headers={'Idempotency-Key':'mismatch'})
        self.assertEqual(response.status_code,422,response.text)

    def test_catalog_revision_integrity_hashes_and_native_graph_invariants(self):
        bad=map_definition();bad['opendrive']['sha256']='0'*64
        self.assertEqual(self.client.post('/api/v1/maps',json={'id':'bad','definition':bad}).status_code,422)
        bad=map_definition();bad['nodes'][0]['next_ids']=['missing']
        self.assertEqual(self.client.post('/api/v1/maps',json={'id':'bad','definition':bad}).status_code,422)
        latest=map_definition();latest['name']='New named revision'
        created=self.create('maps','fixture-town',latest,1)
        self.assertEqual(created['revision'],2)
        first=self.client.get('/api/v1/maps/fixture-town?revision=1').json()
        self.assertEqual(first['sha256'],self.native_map['sha256'])
        self.assertEqual(first['lanes'][0]['points'],[pose(float(i*3)) for i in range(4)])
        wrong=copy.deepcopy(self.scenario);wrong['map']['sha256']='0'*64
        self.assertEqual(self.client.post('/api/v1/scenario-v2',json={'scenario':wrong},headers={'Idempotency-Key':'badref'}).status_code,409)
        with self.db.session() as session:
            row=session.scalar(select(SimulationCatalogRevisionRow).where(SimulationCatalogRevisionRow.kind=='asset'))
            row.manifest=b'{}';session.commit()
        self.assertEqual(self.client.get('/api/v1/assets/fixture-vehicle').status_code,409)

    def test_route_direction_reachability_via_and_exact_map_hash(self):
        body={'revision':1,'sha256':self.native_map['sha256'],'start':pose(),'goal':pose(9),'via':[pose(3)]}
        response=self.client.post('/api/v1/maps/fixture-town/route',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['node_ids'],['0','1','2','3'])
        self.assertEqual(response.json()['distance_m'],9)
        for changed in [dict(start=pose(9),goal=pose()),dict(goal=pose(100)),dict(start=pose(heading_rad=math.pi))]:
            failure=self.client.post('/api/v1/maps/fixture-town/route',json={**body,**changed,'via':[]})
            self.assertEqual(failure.status_code,422,failure.text)
        self.assertEqual(self.client.post('/api/v1/maps/fixture-town/route',json={**body,'sha256':'0'*64}).status_code,409)

    def test_project_authorization_and_controller_mode_binding(self):
        self.principal=Principal('project','default','Viewer','viewer')
        self.assertEqual(self.client.get('/api/v1/maps').status_code,200)
        self.assertEqual(self.client.post('/api/v1/maps',json={'id':'new','definition':map_definition()}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/scenario-v2/yaml/import',json={'yaml':json.dumps(self.scenario)},
                                         headers={'Idempotency-Key':'viewer'}).status_code,403)
        self.principal=Principal('project','another','Engineer','other')
        self.assertEqual(self.client.get('/api/v1/maps/fixture-town').status_code,404)
        self.principal=Principal('legacy')
        controller=self.create('controllers','autoware-fixture',{'resource_kind':'controller','name':'Unconfigured GT planning',
            'adapter_id':'autoware-fixture','adapter_version':'test','mode':'autoware','rights':RIGHTS})
        self.scenario['actors'][0]['controller']['ref']=reference(controller)
        mismatch=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':json.dumps(self.scenario)})
        self.assertEqual(mismatch.status_code,422,mismatch.text)
        self.scenario['actors'][0]['controller']['kind']='autoware'
        valid=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':json.dumps(self.scenario)})
        self.assertEqual(valid.status_code,200,valid.text)
        self.assertTrue(any('not configured' in w for w in valid.json()['catalog_validation']['warnings']))

    def test_one_motion_owner_temporal_bounds_and_sensor_clock(self):
        normalized=ScenarioCity.model_validate(self.scenario)
        self.assertEqual(parse_scenario_yaml(scenario_yaml(normalized)),normalized)
        for change in [dict(route=[]),dict(trajectory=[{**pose(), 'time_s':0},{**pose(9),'time_s':1}]),
                       dict(controller={'kind':'route_follow','wait_s':21}),
                       dict(controller={'kind':'route_follow','wait_s':3,'stop_time_s':2})]:
            bad=copy.deepcopy(self.scenario);bad['actors'][0].update(change)
            with self.assertRaises(ValueError):ScenarioCity.model_validate(bad)
        bad=copy.deepcopy(self.scenario);bad['sensors']=[{'id':'camera','period_s':.07}]
        with self.assertRaises(ValueError):ScenarioCity.model_validate(bad)
        bad['sensors'][0]['period_s']=.1
        self.assertEqual(len(ScenarioCity.model_validate(bad).sensors),1)
        self.assertEqual(ScenarioCity.model_validate({**self.scenario,'seed':2**31-1}).seed,2**31-1)
        with self.assertRaises(ValueError):ScenarioCity.model_validate({**self.scenario,'seed':2**31})
        with self.assertRaises(ValueError):ScenarioCity.model_validate({**self.scenario,'seed':True})

    def test_agent_openapi_catalog_exposes_same_contract_and_viewer_read_only_posts(self):
        self.principal=Principal('project','default','Viewer','viewer')
        result=self.client.get('/api/v1/agent/tools').json()
        operations={(tool['method'],tool['path']):tool for tool in result['tools']}
        for path in ('/api/v1/scenario-v2/yaml/validate','/api/v1/maps/{identity}/route'):
            self.assertTrue(operations[('POST',path)]['read_only'])
        self.assertNotIn(('POST','/api/v1/maps'),operations)
        self.assertNotIn(('POST','/api/v1/scenario-v2/yaml/import'),operations)
        components=result['components']['schemas']
        self.assertIn('ScenarioCity',components)
        self.assertIn('CityAcceptance',components)
        self.assertEqual(components['CatalogCreate']['properties']['definition']['discriminator']['propertyName'],'resource_kind')

    def test_signal_schedule_membership_clock_and_additive_default_hash_stability(self):
        old=ScenarioCity.model_validate(self.scenario).model_dump(mode='json')
        explicit=copy.deepcopy(old);explicit['signals']=[]
        explicit['actors'][0]['route_mode']='native_lane_graph'
        explicit['actors'][0]['controller']['required_gap_m']=0
        self.assertEqual(ScenarioCity.model_validate(explicit).model_dump(mode='json'),old)
        value=copy.deepcopy(self.scenario)
        value['signals']=[{'id':'opendrive-light-1','initial_state':'red','changes':[{'time_s':4,'state':'green'}]}]
        response=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':json.dumps(value)})
        self.assertEqual(response.status_code,422,response.text)
        changed=map_definition();changed['signals']=[{'id':'opendrive-light-1','pose':pose(9),'kind':'traffic_light'}]
        native_map=self.create('maps','fixture-town',changed,1);value['map']=reference(native_map)
        response=self.client.post('/api/v1/scenario-v2/yaml/validate',json={'yaml':json.dumps(value)})
        self.assertEqual(response.status_code,200,response.text)
        for schedules in [value['signals']*2,
            [{'id':'opendrive-light-1','initial_state':'red','changes':[{'time_s':4,'state':'green'},{'time_s':4,'state':'red'}]}],
            [{'id':'opendrive-light-1','initial_state':'red','changes':[{'time_s':21,'state':'green'}]}]]:
            with self.assertRaises(ValueError):ScenarioCity.model_validate({**value,'signals':schedules})

    def test_authored_lane_change_is_physical_explicit_and_has_one_owner(self):
        value=copy.deepcopy(self.scenario);actor=value['actors'][0]
        actor['route_mode']='authored_lane_change';actor['controller']['required_gap_m']=12
        parsed=ScenarioCity.model_validate(value)
        self.assertEqual(parsed.model_dump(mode='json')['actors'][0]['route_mode'],'authored_lane_change')
        self.assertEqual(parsed.actors[0].controller.required_gap_m,12)
        for changes in [{'kind':'pedestrian'}, {'controller':{'kind':'autoware','ref':reference(self.asset)}},
                        {'route_mode':'native_lane_graph'}]:
            bad=copy.deepcopy(value);bad['actors'][0].update(changes)
            with self.assertRaises(ValueError):ScenarioCity.model_validate(bad)

    def test_signal_changes_must_be_issued_by_the_last_command_tick(self):
        value={**copy.deepcopy(self.scenario),'duration_s':1.,'timestep_s':.1}
        def scheduled(time_s):
            return {**value,'signals':[{'id':'opendrive-light-1','initial_state':'red',
                'changes':[{'time_s':time_s,'state':'green'}]}]}
        for time_s in [.9,.9+5e-10]:
            with self.subTest(accepted=time_s):
                parsed=ScenarioCity.model_validate(scheduled(time_s))
                self.assertEqual(parsed.signals[0].changes[0].time_s,time_s)
        for time_s in [.9+1e-8,.95,1.]:
            with self.subTest(rejected=time_s):
                with self.assertRaisesRegex(ValueError,'last command time'):
                    ScenarioCity.model_validate(scheduled(time_s))


if __name__=='__main__':unittest.main()
