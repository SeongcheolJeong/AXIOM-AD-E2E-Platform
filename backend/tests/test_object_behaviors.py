import pytest
from pydantic import ValidationError
from ade2e.object_behavior_schemas import ObjectBehaviorPlan,ObjectPlannerSettings
from ade2e.object_behaviors import behavior_catalog


def test_risk_actions_require_explicit_risk_mode():
    value={'actions':[{'id':'cut','kind':'cut_in','target_lane_node_id':'next'}]}
    with pytest.raises(ValidationError,match='risk_mode'):ObjectBehaviorPlan.model_validate(value)
    assert ObjectBehaviorPlan.model_validate({**value,'risk_mode':True}).actions[0].kind=='cut_in'


def test_behavior_rejects_duplicate_actions_future_dependency_and_missing_trigger_target():
    with pytest.raises(ValidationError,match='unique'):
        ObjectBehaviorPlan.model_validate({'actions':[{'id':'a','kind':'stop'},{'id':'a','kind':'resume'}]})
    with pytest.raises(ValidationError,match='earlier action'):
        ObjectBehaviorPlan.model_validate({'actions':[{'id':'a','kind':'stop','trigger':{'kind':'action_complete','action_id':'future'}}]})
    with pytest.raises(ValidationError,match='target_actor_id'):
        ObjectBehaviorPlan.model_validate({'actions':[{'id':'a','kind':'stop','trigger':{'kind':'ttc','value':2}}]})


def test_schema_exposes_only_supported_bounded_parameters():
    with pytest.raises(ValidationError):ObjectPlannerSettings.model_validate({'max_curvature_inv_m':float('nan')})
    with pytest.raises(ValidationError):ObjectBehaviorPlan.model_validate({'actions':[{'id':'fast','kind':'target_speed','speed_mps':13}]})
    with pytest.raises(ValidationError):ObjectBehaviorPlan.model_validate({'actions':[{'id':'lane','kind':'lane_change'}]})
    with pytest.raises(ValidationError):ObjectBehaviorPlan.model_validate({'actions':[{'id':'hold','kind':'wait','duration_s':20,'timeout_s':10}]})


def test_catalog_and_public_contract_agree_and_reveal_fidelity_limits():
    catalog=behavior_catalog()
    assert len(catalog['behaviors'])==19
    kinds=catalog['behavior_schema']['$defs']['BehaviorAction']['properties']['kind']['enum']
    assert set(kinds)=={item['id'] for item in catalog['behaviors']}
    assert catalog['limits']['max_speed_mps']==12
    assert catalog['limits']['negotiation']=='observed_gap_and_priority_no_v2x'
    assert catalog['fidelity']=='simulator_ground_truth_low_speed_reactive'
