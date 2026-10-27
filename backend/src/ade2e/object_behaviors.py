"""Behavior catalog exposed to GUI, SDK and agents through the same API."""
from .object_behavior_schemas import ObjectBehaviorPlan, ObjectPlannerSettings, RISK_BEHAVIORS

_ENTRIES = [
    ('route_follow','Route following','Follow the selected route with observed traffic and signal response.'),
    ('target_speed','Target speed','Change the desired speed while retaining collision gap limits.'),
    ('follow','Follow vehicle','Maintain a bumper-based following gap; optionally select a target actor.'),
    ('stop','Stop and hold','Brake to zero, hold for the specified duration, then finish the action.'),
    ('resume','Resume driving','Resume the configured or explicitly selected target speed.'),
    ('wait','Wait','Hold the actor for a duration before continuing.'),
    ('lane_change','Lane change','Wait for a safe gap and follow a smooth legal adjacent-lane transition.'),
    ('cut_in','Cut-in fault injection','Use a smaller explicit gap for a legal lateral transition; emergency overlap guard remains.'),
    ('merge','Merge','Accept an observed front/rear gap on the explicitly connected target lane.'),
    ('diverge','Diverge','Select a reachable branch and replan to the unchanged destination.'),
    ('overtake','Overtake and return','Pass a slower actor on a permitted adjacent lane then rejoin the original lane.'),
    ('yield','Yield','Yield to an observed target or intersection conflict until the corridor is clear.'),
    ('cross','Pedestrian crossing','Advance along the authored pedestrian crossing route when conflict is clear.'),
    ('obstacle_bypass','Obstacle bypass and return','Pass an observed stationary obstruction using a legal adjacent lane.'),
    ('reroute','Reroute','Find a directed road-graph route excluding closed nodes; retain the destination.'),
    ('arrive','Arrive','Follow the route until the destination is reached and stopped.'),
    ('emergency_brake','Emergency brake fault injection','Request an immediate zero speed and hold for the action duration.'),
    ('signal_violation','Signal violation fault injection','Temporarily ignore red/yellow signals while retaining physical gap checks.'),
    ('sudden_crossing','Sudden crossing fault injection','Start the authored crossing without the normal yielding gate.'),
]


def behavior_catalog():
    return {'schema_version':'object-behavior-catalog/1',
        'planner_version':'axiom-reactive-planner/1.1',
        'fidelity':'simulator_ground_truth_low_speed_reactive',
        'limits':{'max_speed_mps':12,'planning_hz':10,'control_hz':20,
                  'negotiation':'observed_gap_and_priority_no_v2x',
                  'lane_change_requires':'explicit_same_direction_legal_adjacency'},
        'behaviors':[{'id':kind,'name':name,'description':description,
            'risk':kind in RISK_BEHAVIORS,
            'requires_target_lane':kind in {'lane_change','cut_in','merge','diverge'},
            'actor_kinds':['pedestrian'] if kind in {'cross','sudden_crossing'} else
                (['vehicle'] if kind in {'lane_change','cut_in','merge','diverge','overtake','obstacle_bypass'} else ['vehicle','pedestrian'])}
            for kind,name,description in _ENTRIES],
        'trigger_kinds':['time','distance','relative_speed','ttc','region','signal','action_complete'],
        'behavior_schema':ObjectBehaviorPlan.model_json_schema(),
        'planner_schema':ObjectPlannerSettings.model_json_schema()}
