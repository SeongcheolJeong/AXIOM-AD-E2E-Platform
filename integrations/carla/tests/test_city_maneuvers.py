"""Authored geometry/trigger tests, not native driving evidence."""
import copy
import math
import pytest
from axiom_carla.city_maneuvers import validate_lane_change,scheduled_state,lane_change_gap


def fixture():
    nodes=[{'id':f'{lane}:{x}','x':float(x),'y':y,'z':0.,'heading_rad':0.,'road_id':1,'section_id':0,
            'lane_id':lane,'lane_width_m':3.5,'is_junction':False} for lane,y in ((-1,0.),(-2,3.5)) for x in range(0,42,2)]
    route=[]
    for x in range(0,41):
        t=x/40;route.append({'x':float(x),'y':3.5*(3*t*t-2*t*t*t),'z':0.,'heading_rad':math.atan(3.5*6*t*(1-t)/40)})
    actor={'id':'cut-in','kind':'vehicle','route':route,'controller':{'kind':'route_follow','required_gap_m':6}}
    return actor,{'nodes':nodes}


def test_smooth_adjacent_lane_change_retains_authored_scope():
    actor,definition=fixture();result=validate_lane_change(actor,definition)
    assert result['from_lane']==-1 and result['to_lane']==-2
    assert result['legal_right_of_way_assessed'] is False


@pytest.mark.parametrize('change',['offroad','opposing','junction','jump','reverse'])
def test_cannot_bypass_native_corridor_with_authored_label(change):
    actor,definition=fixture()
    if change=='offroad':actor['route'][20]['y']=10
    if change=='opposing':
        for n in definition['nodes']:
            if n['lane_id']==-2:n['heading_rad']=math.pi
    if change=='junction':
        for n in definition['nodes']:n['is_junction']=True
    if change=='jump':actor['route']=actor['route'][::10]
    if change=='reverse':actor['route'].reverse()
    with pytest.raises(ValueError):validate_lane_change(actor,definition)


def test_schedule_exact_tick_and_gap_gating_without_replanning():
    schedule={'initial_state':'red','changes':[{'time_s':5.,'state':'green'},{'time_s':8.,'state':'yellow'}]}
    assert [scheduled_state(schedule,t) for t in (0.,4.95,5.,7.99,8.)]==['red','red','green','green','yellow']
    actor,_=fixture();state={'id':'cut-in','x':10.,'y':0.}
    assert not lane_change_gap(actor,state,[{'id':'ego','x':6.,'y':3.5}])['permitted']
    assert lane_change_gap(actor,state,[{'id':'ego','x':0.,'y':3.5}])['permitted']


def test_corridor_membership_does_not_allow_sharp_repeated_weaving():
    actor,definition=fixture()
    points=[(0.,0.),(4.,3.5),(8.,0.),(12.,3.5),(16.,3.5)]
    actor['route']=[{'x':x,'y':y,'z':0.,'heading_rad':math.atan2(points[min(i+1,4)][1]-y,points[min(i+1,4)][0]-x)} for i,(x,y) in enumerate(points)]
    with pytest.raises(ValueError):validate_lane_change(actor,definition)
