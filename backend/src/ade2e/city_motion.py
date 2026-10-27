"""Engine-independent motion evidence classification from frozen intent."""

PHYSICAL='native_carla_object_physics'
MIXED='native_carla_mixed_physics_and_replay'
REPLAY='native_carla_timed_position_replay'
FIDELITIES=frozenset((PHYSICAL,MIXED,REPLAY))


def scenario_fidelity(scenario):
    modes={actor['controller']['kind'] for actor in scenario['actors']}
    if 'timed_replay' not in modes:return PHYSICAL
    return MIXED if modes & {'route_follow','traffic','autoware','reactive'} else REPLAY
