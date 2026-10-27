"""Bounded authored lane-change and traffic-signal scheduling contracts.

An authored lane change is a fault-injection/test path, not a claim that a
traffic planner found a legal maneuver. Native feedback must still track it.
"""
from __future__ import annotations
import math

VERSION='axiom-city-maneuvers/1'


def angle(value):return math.atan2(math.sin(value),math.cos(value))


def validate_lane_change(actor,definition):
    if actor['kind']!='vehicle' or actor['controller']['kind'] not in ('route_follow','traffic'):
        raise ValueError('Authored lane changes require one physical vehicle route controller')
    route=actor['route'];nodes=definition['nodes']
    if len(route)<3:raise ValueError('Lane change needs a bounded smooth authored path')
    samples=[];heading=route[0]['heading_rad'];previous_tangent=None;previous_lateral=0.
    lateral_total=-math.sin(heading)*(route[-1]['x']-route[0]['x'])+math.cos(heading)*(route[-1]['y']-route[0]['y'])
    lateral_sign=1 if lateral_total>=0 else -1
    for a,b in zip(route,route[1:]):
        distance=math.hypot(b['x']-a['x'],b['y']-a['y'])
        if distance<1e-5:continue
        tangent=math.atan2(b['y']-a['y'],b['x']-a['x'])
        if abs(angle(tangent-a['heading_rad']))>.2:
            raise ValueError('Authored lane-change path must advance with its native lane direction')
        longitudinal=math.cos(heading)*(b['x']-a['x'])+math.sin(heading)*(b['y']-a['y'])
        lateral=-math.sin(heading)*(b['x']-route[0]['x'])+math.cos(heading)*(b['y']-route[0]['y'])
        if longitudinal<=0 or lateral_sign*(lateral-previous_lateral)<-.02:
            raise ValueError('Supported lane change advances once into the adjacent lane without weaving')
        if previous_tangent is not None and abs(angle(tangent-previous_tangent))/distance>.12:
            raise ValueError('Authored lane-change path has a sharp curvature discontinuity')
        previous_tangent=tangent;previous_lateral=lateral
        if distance>6:raise ValueError('Authored lane-change path spacing must not exceed 6m')
        count=max(1,math.ceil(distance/.5))
        for index in range(count):
            f=index/count
            samples.append({k:a[k]+f*(b[k]-a[k]) for k in ('x','y','z')}|
                {'heading_rad':a['heading_rad']+f*angle(b['heading_rad']-a['heading_rad'])})
    samples.append(route[-1]);matches=[]
    if len(samples)>2000:raise ValueError('Lane change exceeds 1000m corridor validation bound')
    for point in samples:
        candidates=[]
        for node in nodes:
            if node.get('is_junction'):continue
            if abs(node['z']-point['z'])>1 or abs(angle(point['heading_rad']-node['heading_rad']))>math.pi/4:continue
            dx,dy=point['x']-node['x'],point['y']-node['y'];h=node['heading_rad']
            lateral=abs(-math.sin(h)*dx+math.cos(h)*dy);longitudinal=abs(math.cos(h)*dx+math.sin(h)*dy)
            if longitudinal<=2.1 and lateral<=node['lane_width_m']/2+.12:
                candidates.append((math.hypot(dx,dy),node))
        if not candidates:raise ValueError('Authored lane change leaves the same-direction native driving corridor')
        matches.append(min(candidates,key=lambda item:item[0])[1])
    roads={(n['road_id'],n['section_id']) for n in matches};lanes={n['lane_id'] for n in matches}
    if len(roads)!=1 or len(lanes)!=2:
        raise ValueError('Supported authored lane change crosses exactly two lanes on one road section')
    left,right=sorted(lanes)
    if left*right<=0 or right-left!=1:raise ValueError('Lane change must use adjacent same-direction native lanes')
    if matches[0]['lane_id']==matches[-1]['lane_id']:raise ValueError('Lane change must finish in the adjacent lane')
    if abs(angle(route[-1]['heading_rad']-route[0]['heading_rad']))>.4:
        raise ValueError('Lane-change profile does not support turns or junctions')
    return {'source':'authored_adjacent_lane_corridor','road_id':matches[0]['road_id'],'section_id':matches[0]['section_id'],
        'from_lane':matches[0]['lane_id'],'to_lane':matches[-1]['lane_id'],'sample_count':len(samples),
        'legal_right_of_way_assessed':False}


def scheduled_state(schedule,elapsed):
    state=schedule['initial_state']
    for event in schedule.get('changes',[]):
        if event['time_s']>elapsed+1e-9:break
        state=event['state']
    return state


def lane_change_gap(actor,state,others):
    """A gate on starting an authored maneuver, never a hidden trajectory edit."""
    required=actor['controller'].get('required_gap_m',0.)
    if required<=0:return {'permitted':True,'required_gap_m':0.,'nearest_actor_id':None,'gap_m':None}
    h=actor['route'][-1]['heading_rad'];end=actor['route'][-1];candidates=[]
    for other in others:
        if other['id']==actor['id']:continue
        lateral=abs(-math.sin(h)*(other['x']-end['x'])+math.cos(h)*(other['y']-end['y']))
        gap=abs(math.cos(h)*(other['x']-state['x'])+math.sin(h)*(other['y']-state['y']))
        if lateral<=2.5:candidates.append((gap,other['id']))
    nearest=min(candidates) if candidates else None
    return {'permitted':nearest is None or nearest[0]>=required,'required_gap_m':required,
        'distance_basis':'longitudinal_actor_origin_separation_not_bumper_clearance',
        'nearest_actor_id':nearest[1] if nearest else None,'gap_m':nearest[0] if nearest else None}
