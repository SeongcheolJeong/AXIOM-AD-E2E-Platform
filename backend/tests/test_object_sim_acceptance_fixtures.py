"""Authoring fixture checks; no execution API or native simulator is invoked."""
import importlib.util
from pathlib import Path
import sys

from ade2e.object_maps_schemas import ObjectMapDefinition
from ade2e.object_maps_geometry import preview_map
from ade2e.scenario_v2_schemas import parse_scenario


ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
try:
    spec=importlib.util.spec_from_file_location('object_sim_acceptance',ROOT/'scripts/object_sim_acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
finally:sys.path.pop(0)


def fixture():
    source=ObjectMapDefinition.model_validate(module.map_source())
    preview=preview_map(source)
    assert preview['buildable']
    native={'id':'map-fixture','revision':1,'sha256':'a'*64,'definition':{'nodes':preview['nodes']}}
    asset={'id':'asset-fixture','revision':1,'sha256':'b'*64,'definition':{'blueprint_id':'vehicle.toyota.prius'}}
    return native,asset


def test_all_frozen_cases_and_three_seeds_validate_public_reactive_schema():
    native,asset=fixture();all_cases=[]
    for seed in module.SEEDS:
        cases=module.case_definitions(native,asset,seed)
        assert {c['case'] for c in cases}==set(module.CASES)
        for item in cases:
            scenario=parse_scenario(item['scenario'])
            assert scenario.seed==seed and scenario.timestep_s==.05
            all_cases.append(scenario)
    assert len(all_cases)==18


def test_eight_actor_case_requires_actual_motion_and_case_goals_do_not_mask_closure():
    native,asset=fixture();cases={c['case']:c for c in module.case_definitions(native,asset,42)}
    eight=cases['integration_eight']
    assert len(eight['scenario']['actors'])==8
    assert len(eight['expectations']['required_motion_actor_ids'])==8
    assert sum(c['metric']=='distance_travelled' for c in eight['criteria'])==8
    assert cases['closed_route_hold']['scenario']['acceptance']['require_goal'] is False
    assert cases['closed_route_hold']['expectations']['maximum_ego_travel_m']==.5
    assert cases['risk_brake']['scenario']['actors'][0]['behavior']['risk_mode'] is True


def test_behavior_exercise_cannot_pass_from_native_job_completion_alone():
    native,asset=fixture();item=module.case_definitions(native,asset,42)[3]
    result=module.inspect_behavior_scene({'planner_events':[],'metrics':[],'camera_records':[]},item)
    assert result['passed'] is False
    assert not any(c['passed'] for c in result['checks'])


def test_explicit_attempt_uses_new_idempotency_keys_without_changing_frozen_scenario_or_criteria(tmp_path):
    native,asset=fixture();item=module.case_definitions(native,asset,42)[0]
    item['configuration_hash']=module.digest(item['scenario'])
    item['saved_scenario']={'id':'scenario-fixed','revision':1,'manifest_hash':'manifest-fixed'}
    item['verification_plan']={'id':'criteria-fixed','revision':1,'manifest_hash':'criteria-hash-fixed'}
    suite={'schema_version':module.VERSION,'id':'suite-fixed','project_id':'default','authoring_complete':True,'cases':[item]}
    suite['suite_sha256']=module.digest(suite)
    class MockAPI:
        def __init__(self):self.calls=[]
        def request(self,path,body=None,key=None):
            self.calls.append((path,body,key))
            if path.startswith('/scenario-v2/'):return {'manifest_hash':'manifest-fixed'}
            if path=='/experiments':return {'id':'exp-fixture','preview_hash':'preview-fixture'}
            if path.endswith('/executions'):return {'id':'job-fixture','status':'cancelled','error':'authored test cancellation'}
            raise AssertionError(path)
    api=MockAPI();before=module.digest(suite)
    first=module.run(api,suite,tmp_path/'first.json')
    second=module.run(api,suite,tmp_path/'second.json',attempt_label='gap-correction-r3')
    assert module.digest(suite)==before
    experiment_calls=[call for call in api.calls if call[0]=='/experiments']
    assert experiment_calls[0][1]==experiment_calls[1][1]
    assert experiment_calls[0][2]!=experiment_calls[1][2]
    assert experiment_calls[1][2].endswith(':attempt-gap-correction-r3:experiment')
    assert first['attempt_label'] is None and second['attempt_label']=='gap-correction-r3'
    assert first['trials'][0]['classification']==second['trials'][0]['classification']=='infrastructure_failure'
