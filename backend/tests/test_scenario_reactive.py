"""Reactive authoring compatibility and public API, not native simulation."""
import copy
import hashlib
import pytest
from pydantic import ValidationError
from ade2e.scenario_v2_schemas import parse_scenario, ScenarioV2Create
from ade2e.scenario_v2_yaml import scenario_yaml, parse_scenario_yaml
from ade2e.simulation_catalog_schemas import MapDefinition
from ade2e.city_motion import scenario_fidelity, MIXED
from test_scenario_city import map_definition, pose


def document():
    ref={'id':'fixture','revision':1,'sha256':'a'*64}
    return {'schema_version':'scenario-v2/3','name':'Reactive test','map':ref,
        'actors':[{'id':'ego','role':'ego','asset':ref,'pose':pose(),'route':[pose(),pose(9)],
                   'controller':{'kind':'reactive'},'behavior':{'actions':[{'id':'wait','kind':'wait','duration_s':2},
                        {'id':'go','kind':'resume','trigger':{'kind':'action_complete','action_id':'wait'}}]}}]}


def test_reactive_yaml_api_union_preserves_behavior_and_settings():
    model=parse_scenario(document())
    assert parse_scenario_yaml(scenario_yaml(model)).model_dump()==model.model_dump()
    payload=ScenarioV2Create(project_id='default',scenario=document()).model_dump()
    assert payload['scenario']['actors'][0]['behavior']['actions'][1]['kind']=='resume'
    assert payload['scenario']['actors'][0]['planner']['headway_s']==2


def test_legacy_schema_cannot_silently_adopt_reactive_owner():
    value=document();value['schema_version']='scenario-v2/2'
    value['actors'][0].pop('behavior')
    with pytest.raises(ValidationError,match='scenario-v2/3'):parse_scenario(value)


@pytest.mark.parametrize('change', ['missing_actor','self_actor','risk','clock','dual_owner'])
def test_reactive_rejects_invalid_executable_intent(change):
    value=document();actor=value['actors'][0]
    if change in {'missing_actor','self_actor'}:
        actor['behavior']['actions'][0]['target_actor_id']='missing' if change=='missing_actor' else 'ego'
    elif change=='risk':actor['behavior']['actions'][0]['kind']='emergency_brake'
    elif change=='clock':value['timestep_s']=.1
    else:actor['controller']['kind']='route_follow'
    with pytest.raises(ValidationError):parse_scenario(value)


def test_old_catalog_hash_not_changed_by_new_optional_fields():
    original=MapDefinition.model_validate(map_definition()).model_dump(mode='json')
    assert 'generated' not in original
    assert not {'left_id','closed','conflict_ids','priority'} & original['nodes'][0].keys()
    expanded=copy.deepcopy(original)
    expanded['nodes'][0].update(left_id=None,closed=False,priority=None,conflict_ids=[])
    assert MapDefinition.model_validate(expanded).model_dump(mode='json')==original


def test_reactive_and_replay_is_mixed_diagnostic_fidelity():
    assert scenario_fidelity({'actors':[{'controller':{'kind':'reactive'}},
        {'controller':{'kind':'timed_replay'}}]})==MIXED


def test_public_catalog_cannot_fabricate_compiler_provenance():
    from test_scenario_city import ScenarioCityApiTests
    harness=ScenarioCityApiTests();harness.setUp()
    try:
        definition=map_definition()
        definition['generated']={'source':{'id':'nonexistent','revision':1,'sha256':'a'*64},
            'compiler_id':'fake','compiler_version':'fake','build_hash':'b'*64}
        result=harness.client.post('/api/v1/maps',json={'id':'fabricated','definition':definition})
        assert result.status_code==422
        assert '/object-maps/' in result.json()['detail']
    finally:harness.tearDown()
