"""Native catalog contract fixtures; not actual simulator execution evidence."""
import math
from types import SimpleNamespace as NS

from axiom_carla.city_native import native_lane_relations


class Location:
    def __init__(self,x,y):self.x,self.y=x,y
    def distance(self,other):return math.hypot(self.x-other.x,self.y-other.y)


def waypoint(identity,lane=-1,y=0,yaw=0,change=0,junction=False,lane_type='Driving'):
    w=NS(id=identity,road_id=1,section_id=0,lane_id=lane,s=10,lane_change=change,
         is_junction=junction,junction_id=7,lane_type=lane_type,
         transform=NS(location=Location(10,y),rotation=NS(yaw=yaw)))
    w.get_left_lane=lambda:None
    w.get_right_lane=lambda:None
    return w


def test_observed_left_edge_and_permission_are_separate():
    w=waypoint('a',change=2);left=waypoint('b',lane=-2,y=-3.5)
    w.get_left_lane=lambda:left
    lanes={(1,0,-2):[left]}
    assert native_lane_relations(w,lanes)=={'left_id':'b','lane_change_left':True}
    w.lane_change=0
    assert native_lane_relations(w,lanes)=={'left_id':'b'}


def test_opposing_or_non_driving_or_unsampled_lane_is_not_connected():
    w=waypoint('a',change=3);left=waypoint('b',lane=1,y=-3.5,yaw=180)
    w.get_left_lane=lambda:left
    assert native_lane_relations(w,{(1,0,1):[left]})=={}
    left.transform.rotation.yaw=0;left.lane_type='Sidewalk'
    assert native_lane_relations(w,{(1,0,1):[left]})=={}
    left.lane_type='Driving'
    assert native_lane_relations(w,{})=={}


def test_junction_group_does_not_invent_right_of_way_or_lateral_edge():
    w=waypoint('a',change=3,junction=True)
    assert native_lane_relations(w,{})=={'conflict_ids':['native-junction-7']}
    w.junction_id=-1
    assert native_lane_relations(w,{})=={}


def test_nearby_sample_must_match_native_neighbor_location():
    w=waypoint('a',change=1);right=waypoint('b',lane=-2,y=3.5)
    w.get_right_lane=lambda:right
    sample=waypoint('sample',lane=-2,y=20)
    assert native_lane_relations(w,{(1,0,-2):[sample]})=={}
