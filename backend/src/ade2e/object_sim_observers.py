"""Observers of actual engine traces; no invented values when inputs are missing."""
import math

VERSION='object-trace-observers/1'

def _center(a):
    c,s=math.cos(a['heading_rad']),math.sin(a['heading_rad'])
    return a['x']+a.get('bb_x',0)*c-a.get('bb_y',0)*s,a['y']+a.get('bb_x',0)*s+a.get('bb_y',0)*c

def _corners(a):
    x,y=_center(a);c,s=math.cos(a['heading_rad']),math.sin(a['heading_rad']);l,w=a['length_m']/2,a['width_m']/2
    return [(x+u*c-v*s,y+u*s+v*c) for u,v in [(-l,-w),(l,-w),(l,w),(-l,w)]]

def _axes(a,b):
    return [(math.cos(x['heading_rad']),math.sin(x['heading_rad'])) for x in (a,b)]+[(-math.sin(x['heading_rad']),math.cos(x['heading_rad'])) for x in (a,b)]

def _interval(corners,axis):
    points=[x*axis[0]+y*axis[1] for x,y in corners];return min(points),max(points)

def _overlap(a,b):
    ca,cb=_corners(a),_corners(b)
    return all(max(_interval(ca,v)[0],_interval(cb,v)[0])<=min(_interval(ca,v)[1],_interval(cb,v)[1]) for v in _axes(a,b))

def _point_segment(p,a,b):
    vx,vy=b[0]-a[0],b[1]-a[1];length=vx*vx+vy*vy
    t=max(0,min(1,((p[0]-a[0])*vx+(p[1]-a[1])*vy)/length)) if length else 0
    return math.hypot(p[0]-a[0]-t*vx,p[1]-a[1]-t*vy)

def _distance(a,b):
    if _overlap(a,b):return 0.
    ca,cb=_corners(a),_corners(b)
    return min([_point_segment(p,cb[i],cb[(i+1)%4]) for p in ca for i in range(4)]+[_point_segment(p,ca[i],ca[(i+1)%4]) for p in cb for i in range(4)])

def _ttc(a,b):
    # Continuous SAT under constant heading and velocity; diagnostic kinematic estimate.
    ca,cb=_corners(a),_corners(b);enter=0.;exit=math.inf
    for axis in _axes(a,b):
        amin,amax=_interval(ca,axis);bmin,bmax=_interval(cb,axis)
        velocity=(b['vx_mps']-a['vx_mps'])*axis[0]+(b['vy_mps']-a['vy_mps'])*axis[1]
        if abs(velocity)<1e-9:
            if max(amin,bmin)>min(amax,bmax):return None
            continue
        t1,t2=(amin-bmax)/velocity,(amax-bmin)/velocity
        enter=max(enter,min(t1,t2));exit=min(exit,max(t1,t2))
        if enter>exit:return None
    return enter if exit>=0 else None

def evaluate_observers(scenario,frames):
    result=[]
    for observer in scenario['observers']:
        kind=observer['kind'];aid=observer['actor_id'];threshold=observer.get('threshold');reason=None;value=None;units=None
        states=[];pairs=[]
        try:
            if len(frames)<2:raise ValueError('At least two observed frames are required')
            for frame in frames:
                actor=next((a for a in frame['actors'] if a['id']==aid),None)
                if not actor:raise ValueError('Observed actor is missing')
                states.append((frame['time_s'],actor))
                pairs += [(actor,other) for other in frame['actors'] if other['id']!=aid]
            if kind in ('collision','minimum_distance','minimum_ttc') and not pairs:
                raise ValueError('No other actor exists; relative observer is unevaluated')
            if kind=='collision':value=any(_overlap(a,b) for a,b in pairs);units='boolean'
            elif kind=='minimum_distance':value=min(_distance(a,b) for a,b in pairs);units='m'
            elif kind=='minimum_ttc':
                values=[x for a,b in pairs if (x:=_ttc(a,b)) is not None]
                if not values:raise ValueError('No finite constant-velocity box intersection predicted')
                value=min(values);units='s'
            elif kind=='lane_departure':
                width=scenario['map']['lane_width_m']
                value=any(abs(a['lane_offset_m'])+(abs(math.cos(a['heading_rad']))*a['width_m']+abs(math.sin(a['heading_rad']))*a['length_m'])/2>width/2 for _,a in states)
                units='boolean'
            elif kind=='maximum_speed':value=max(a['speed_mps'] for _,a in states);units='m/s'
            elif kind=='maximum_acceleration':value=max(math.hypot(a['ax_mps2'],a['ay_mps2']) for _,a in states);units='m/s²'
            elif kind=='maximum_jerk':
                value=max(math.hypot(b['ax_mps2']-a['ax_mps2'],b['ay_mps2']-a['ay_mps2'])/(t2-t1) for (t1,a),(t2,b) in zip(states,states[1:]));units='m/s³'
            elif kind=='goal_arrival':
                goal=observer['goal'];value=min(math.hypot(a['x']-goal['x'],a['y']-goal['y']) for _,a in states);units='m'
        except (KeyError,TypeError,ValueError,ZeroDivisionError) as error:reason=str(error)
        status='unevaluated' if reason else 'observed'
        if not reason and (kind in ('collision','lane_departure') or threshold is not None):
            passed=not value if kind in ('collision','lane_departure') else value>=threshold if kind in ('minimum_distance','minimum_ttc') else value<=threshold
            status='passed' if passed else 'failed'
        result.append({'observer':observer,'value':value,'units':units,'status':status,'reason':reason,'evaluator_version':VERSION,
                       'method':'constant_heading_velocity_obb_projection' if kind=='minimum_ttc' else 'observed_engine_state'})
    return result
