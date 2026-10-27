import copy,tempfile,unittest
from pathlib import Path
from fastapi import APIRouter,Depends,FastAPI,Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.scenario_v2 import register_scenario_v2_routes
from ade2e.scenario_v2_schemas import ScenarioV2
from ade2e.scenario_v2_templates import templates
from ade2e.scenario_v2_xml import import_xml,scenario_xml,sweep_variants
from ade2e.object_sim_observers import evaluate_observers
from ade2e.team_access import Principal

class ScenarioV2ContractTests(unittest.TestCase):
    def test_all_templates_roundtrip_and_reject_modified_or_extra_behavior(self):
        for item in templates():
            scenario=ScenarioV2.model_validate(item['scenario']);xml=scenario_xml(scenario)
            self.assertEqual(import_xml(xml).model_dump(),scenario.model_dump())
            with self.assertRaises(ValueError):import_xml(xml.replace('</OpenSCENARIO>','<UnknownAction /></OpenSCENARIO>'))
            with self.assertRaises(ValueError):import_xml(xml.replace('filepath="road.xodr"','filepath="unapproved.xodr"'))
        with self.assertRaises(ValueError):import_xml('<!DOCTYPE foo><OpenSCENARIO/>')
        with self.assertRaises(ValueError):import_xml('<OpenSCENARIO><FileHeader/></OpenSCENARIO>')

    def test_links_sweep_validation_and_no_silent_parameter_clipping(self):
        value=templates()[0]['scenario'];value['sweep']=[{'actor_id':'ego','field':'speed_mps','values':[10,12]}]
        variants=list(sweep_variants(ScenarioV2.model_validate(value)))
        self.assertEqual([v['scenario']['actors'][0]['speed_mps'] for v in variants],[10,12])
        self.assertNotEqual(variants[0]['configuration_hash'],variants[1]['configuration_hash'])
        value['sweep'][0]['values']=[999]
        with self.assertRaises(ValueError):list(sweep_variants(ScenarioV2.model_validate(value)))
        value=templates()[0]['scenario'];value['actors'][0]['actions']=[{'kind':'speed','target_speed_mps':4,'trigger':{'kind':'distance','actor_id':'ego','target_actor_id':'missing','value':5}}]
        with self.assertRaises(ValueError):ScenarioV2.model_validate(value)

    def test_collision_distance_ttc_geometry_and_missing_inputs(self):
        scenario=templates()[1]['scenario'];scenario['observers']=[{'kind':k,'actor_id':'ego','threshold':None,'goal':None} for k in ('collision','minimum_distance','minimum_ttc')]
        a={'id':'ego','x':0.,'y':0.,'heading_rad':0.,'length_m':4.,'width_m':2.,'vx_mps':10.,'vy_mps':0.}
        b={**a,'id':'other','x':10.,'vx_mps':5.}
        frames=[{'time_s':t,'actors':[a,b]} for t in (0.,.1)]
        result=evaluate_observers(scenario,frames)
        self.assertFalse(result[0]['value']);self.assertAlmostEqual(result[1]['value'],6);self.assertAlmostEqual(result[2]['value'],1.2)
        b['x']=3
        self.assertTrue(evaluate_observers(scenario,frames)[0]['value'])
        missing=copy.deepcopy(frames);del missing[0]['actors'][0]['width_m']
        self.assertTrue(all(r['status']=='unevaluated' for r in evaluate_observers(scenario,missing)))

class ScenarioV2ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.db=Database(Settings(data_dir=Path(self.directory.name),seed_examples=False));self.db.initialize()
        self.principal=Principal('legacy')
        def auth(request:Request):request.state.principal=self.principal
        router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)]);register_scenario_v2_routes(router,self.db)
        app=FastAPI();app.include_router(router);self.client=TestClient(app)
    def tearDown(self):self.db.engine.dispose();self.directory.cleanup()

    def test_immutable_revisions_idempotent_create_preview_and_xml(self):
        body={'project_id':'default','scenario':templates()[1]['scenario']}
        first=self.client.post('/api/v1/scenario-v2',json=body,headers={'Idempotency-Key':'create'});self.assertEqual(first.status_code,201,first.text)
        created=first.json();identity=created['id']
        again=self.client.post('/api/v1/scenario-v2',json=body,headers={'Idempotency-Key':'create'});self.assertEqual(again.json()['id'],identity)
        self.assertEqual(self.client.get('/api/v1/scenario-v2/'+identity).json()['manifest_hash'],created['manifest_hash'])
        body['scenario']['actors'][0]['speed_mps']=12
        second=self.client.post(f'/api/v1/scenario-v2/{identity}/revisions',json={'expected_revision':1,'scenario':body['scenario']},headers={'Idempotency-Key':'revise'})
        self.assertEqual(second.status_code,201,second.text);self.assertEqual(second.json()['revision'],2)
        self.assertEqual(self.client.get(f'/api/v1/scenario-v2/{identity}?revision=1').json()['scenario']['actors'][0]['speed_mps'],10)
        stale=self.client.post(f'/api/v1/scenario-v2/{identity}/revisions',json={'expected_revision':1,'scenario':body['scenario']},headers={'Idempotency-Key':'stale'});self.assertEqual(stale.status_code,409)
        xml=self.client.get(f'/api/v1/scenario-v2/{identity}/export?format=openscenario&revision=1');self.assertEqual(xml.status_code,200)
        imported=self.client.post('/api/v1/scenario-v2/import',json={'xml':xml.text},headers={'Idempotency-Key':'import'});self.assertEqual(imported.status_code,201,imported.text)
        self.assertEqual(imported.json()['scenario'],created['scenario'])
        preview=self.client.post(f'/api/v1/scenario-v2/{identity}/sweep-preview',json={'seeds':[42,43,44]});self.assertEqual(preview.json()['job_count'],3)
        self.assertEqual(preview.json()['submitted_jobs'],0)

    def test_viewer_cannot_write_or_access_another_project(self):
        body={'project_id':'default','scenario':templates()[0]['scenario']}
        created=self.client.post('/api/v1/scenario-v2',json=body,headers={'Idempotency-Key':'create'}).json()
        self.principal=Principal('project','default','Viewer','viewer')
        self.assertEqual(self.client.get('/api/v1/scenario-v2/'+created['id']).status_code,200)
        self.assertEqual(self.client.post('/api/v1/scenario-v2',json=body,headers={'Idempotency-Key':'viewer'}).status_code,403)
        self.principal=Principal('project','another','Viewer','other')
        self.assertEqual(self.client.get('/api/v1/scenario-v2/'+created['id']).status_code,404)

if __name__=='__main__':unittest.main()
