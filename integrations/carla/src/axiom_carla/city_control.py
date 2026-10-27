"""Engine-independent, versioned low-speed route tracking mathematics.

No state teleporting is used by the physical controller. Timed replay is a
separate execution mode, not a claim of vehicle control performance.
"""
from __future__ import annotations
import bisect
import math

VERSION = 'axiom-city-pure-pursuit/2'


def lengths(route):
    distances = [0.0]
    for a, b in zip(route, route[1:]):
        distances.append(distances[-1] + math.hypot(b['x']-a['x'], b['y']-a['y']))
    if len(route) < 2 or distances[-1] <= .1:
        raise ValueError('A route needs at least two distinct points')
    return distances


def project(point, route, cumulative=None, previous=None):
    cumulative = cumulative or lengths(route)
    candidates = []
    for i, (a, b) in enumerate(zip(route, route[1:])):
        segment = cumulative[i+1] - cumulative[i]
        if segment < 1e-6:
            continue
        if previous is not None and (cumulative[i+1] < previous-4 or cumulative[i] > previous+40):
            continue
        dx, dy = b['x']-a['x'], b['y']-a['y']
        fraction = max(0., min(1., ((point['x']-a['x'])*dx+(point['y']-a['y'])*dy)/(segment*segment)))
        x, y = a['x']+fraction*dx, a['y']+fraction*dy
        error = math.hypot(point['x']-x, point['y']-y)
        candidates.append((error, cumulative[i]+fraction*segment, i, x, y))
    if not candidates:
        raise ValueError('No route segment inside the continuity window')
    error, progress, index, x, y = min(candidates)
    return {'error_m': error, 'progress_m': progress, 'segment': index, 'x': x, 'y': y,
            'remaining_m': max(0., cumulative[-1]-progress)}


def interpolate(route, cumulative, progress):
    i = min(len(route)-2, max(0, bisect.bisect_right(cumulative, progress)-1))
    span = cumulative[i+1]-cumulative[i]
    f = max(0., min(1., (progress-cumulative[i])/span)) if span else 0.
    a, b = route[i:i+2]
    return {k: a.get(k, 0.)+f*(b.get(k, 0.)-a.get(k, 0.)) for k in ('x','y','z')}


def feedback(state, route, speed_mps, *, wheelbase_m=2.7, max_steer_rad=.6,
             rear_axle_offset_m=0., previous=None, previous_steering=None,
             end_behavior='stop', stop=False):
    if (not all(math.isfinite(v) for v in (speed_mps,wheelbase_m,max_steer_rad,rear_axle_offset_m))
        or not 0<=speed_mps<=12 or not 0<wheelbase_m<=12 or not 0<max_steer_rad<math.pi/2
        or not 0<=rear_axle_offset_m<=wheelbase_m):
        raise ValueError('Unsupported city controller speed/physics')
    cumulative = lengths(route)
    # Progress, goal distance and longitudinal stopping retain the authored
    # actor-origin convention. Bicycle steering geometry is rear-axle based.
    projection = project(state, route, cumulative, previous)
    steering_point={**state,'x':state['x']-rear_axle_offset_m*math.cos(state['heading_rad']),
                    'y':state['y']-rear_axle_offset_m*math.sin(state['heading_rad'])}
    steering_projection=project(steering_point,route,cumulative,
        previous if rear_axle_offset_m==0 and previous_steering is None else previous_steering)
    lookahead = max(3., min(8., 2.+state['speed_mps']*.8))
    target = interpolate(route, cumulative, min(cumulative[-1], steering_projection['progress_m']+lookahead))
    dx, dy = target['x']-steering_point['x'], target['y']-steering_point['y']
    local_y = -math.sin(state['heading_rad'])*dx+math.cos(state['heading_rad'])*dy
    distance2 = max(.01, dx*dx+dy*dy)
    steer_angle = math.atan2(2*wheelbase_m*local_y, distance2)
    desired = speed_mps
    if end_behavior == 'stop':
        desired = min(desired, math.sqrt(2*1.5*max(0.,projection['remaining_m']-.5)))
    endpoint_distance = math.hypot(state['x']-route[-1]['x'], state['y']-route[-1]['y'])
    reached = projection['remaining_m'] <= 1.0 and endpoint_distance <= 2.0
    if stop or (reached and end_behavior == 'stop'):
        desired = 0.
    error = desired-state['speed_mps']
    control = {'throttle': min(.6,max(0.,.34*error+.12)) if desired>.05 and error>-.15 else 0.,
               'brake': min(1.,max(0.,-.6*error)) if error<-.15 else (1. if desired<=.05 else 0.),
               'steer': max(-1., min(1.,steer_angle/max_steer_rad))}
    if control['brake'] > 0:
        control['throttle'] = 0.
    return control, {**projection, 'target':target,'desired_speed_mps':desired,
                     'steering_reference':'rear_axle' if rear_axle_offset_m else 'actor_origin',
                     'steering_point':{k:steering_point[k] for k in ('x','y')},
                     'steering_progress_m':steering_projection['progress_m'],'steering_error_m':steering_projection['error_m'],
                     'goal_distance_m':endpoint_distance,'goal_reached':reached,
                     'state':'stopped' if desired==0 and state['speed_mps']<=.2 else ('braking' if desired==0 else 'tracking')}


def replay_pose(trajectory, elapsed, end_behavior='stop'):
    if len(trajectory)<2:
        raise ValueError('Timed replay needs two or more poses')
    if end_behavior=='repeat':
        elapsed %= trajectory[-1]['time_s']
    if elapsed <= trajectory[0]['time_s']:
        return dict(trajectory[0]), 0.
    if elapsed >= trajectory[-1]['time_s']:
        return dict(trajectory[-1]), 0.
    times=[p['time_s'] for p in trajectory]
    i=bisect.bisect_right(times,elapsed)-1
    a,b=trajectory[i:i+2]; dt=b['time_s']-a['time_s']; f=(elapsed-a['time_s'])/dt
    pose={k:a[k]+f*(b[k]-a[k]) for k in ('x','y','z')}
    delta=(b['heading_rad']-a['heading_rad']+math.pi)%(2*math.pi)-math.pi
    pose['heading_rad']=a['heading_rad']+f*delta
    return pose, math.hypot(b['x']-a['x'],b['y']-a['y'])/dt
