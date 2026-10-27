"""Deterministic low-speed Object Sim planner, independent of any engine SDK.

Only observed positions, velocities, footprints, graph topology and signal states
are consumed. Other actors' authored future actions/routes are never consulted.
The returned route and speed are requests to a physical feedback controller, not
actor state updates. This module is transported with the native CARLA runner.
"""
from __future__ import annotations

import copy
import heapq
import math

try:
    from . import city_control
except ImportError:
    import city_control

VERSION = 'axiom-reactive-planner/1.1'
LATERAL = {'lane_change','cut_in','merge','diverge','overtake','obstacle_bypass'}
RISK = {'cut_in','emergency_brake','signal_violation','sudden_crossing'}
KINDS = LATERAL | RISK | {'route_follow','target_speed','follow','stop','resume','wait','yield','cross','reroute','arrive'}
DEFAULTS = {'headway_s':2.,'min_gap_m':3.,'max_deceleration_mps2':3.,
    'max_lateral_acceleration_mps2':2.,'max_curvature_inv_m':.12,
    'allow_lane_changes':True,'allow_reroute':True,'obey_signals':True,
    'deadlock_timeout_s':10.,'closed_node_ids':[]}


def _angle(a):
    return math.atan2(math.sin(a),math.cos(a))


def _distance(a,b):
    return math.hypot(a['x']-b['x'],a['y']-b['y'])


def _pose(p):
    return {k:float(p.get(k,0.)) for k in ('x','y','z','heading_rad')}


def _dimensions(s):
    """Native full bbox dimensions are preferred; fallback is explicit in output."""
    d=s.get('dimensions_m') or s.get('dimensions') or {}
    length=d.get('length_m',d.get('length',s.get('length_m',4.8)))
    width=d.get('width_m',d.get('width',s.get('width_m',2.)))
    return float(length),float(width)


def _extent(s,heading):
    length,width=_dimensions(s)
    delta=s.get('heading_rad',0.)-heading
    return abs(math.cos(delta))*length/2+abs(math.sin(delta))*width/2


def _center(s):
    offset=s.get('bbox_center') or {};h=s.get('heading_rad',0.)
    x=offset.get('x',0.);y=offset.get('y',0.)
    return {'x':s['x']+math.cos(h)*x-math.sin(h)*y,
            'y':s['y']+math.sin(h)*x+math.cos(h)*y,
            'z':s.get('z',0.)+offset.get('z',0.)}


def _velocity(s):
    v=s.get('velocity')
    if isinstance(v,dict):
        return float(v.get('x',0.)),float(v.get('y',0.))
    speed=s.get('speed_mps',0.); h=s.get('heading_rad',0.)
    return speed*math.cos(h),speed*math.sin(h)


def _rectangles_overlap(a,b,margin=.2):
    """Separating-axis test for oriented, observed bounding rectangles."""
    ac=_center(a);bc=_center(b)
    if abs(ac['z']-bc['z'])>2.5:return False
    dx=bc['x']-ac['x'];dy=bc['y']-ac['y']
    for heading in (a.get('heading_rad',0),a.get('heading_rad',0)+math.pi/2,
                    b.get('heading_rad',0),b.get('heading_rad',0)+math.pi/2):
        if abs(math.cos(heading)*dx+math.sin(heading)*dy)>_extent(a,heading)+_extent(b,heading)+margin:
            return False
    return True


def bumper_gap(a,b):
    ac=_center(a);bc=_center(b)
    h=a.get('heading_rad',0.); dx=bc['x']-ac['x'];dy=bc['y']-ac['y']
    longitudinal=math.cos(h)*dx+math.sin(h)*dy
    lateral=-math.sin(h)*dx+math.cos(h)*dy
    return {'longitudinal_m':longitudinal,
        'gap_m':abs(longitudinal)-_extent(a,h)-_extent(b,h),
        'lateral_m':lateral,
        'lateral_clearance_m':abs(lateral)-_extent(a,h+math.pi/2)-_extent(b,h+math.pi/2)}


def ttc(a,b):
    gap=bumper_gap(a,b)
    if gap['longitudinal_m']<=0 or gap['lateral_clearance_m']>.5:return None
    av=_velocity(a);bv=_velocity(b);h=a.get('heading_rad',0.)
    closing=(av[0]-bv[0])*math.cos(h)+(av[1]-bv[1])*math.sin(h)
    if gap['gap_m']<=0:return 0.
    return gap['gap_m']/closing if closing>1e-6 else None


def trigger_matches(trigger,state,observations,elapsed,signals,completed):
    kind=trigger.get('kind','time'); threshold=trigger.get('value',0.)
    compare=(lambda v:v>=threshold) if trigger.get('operator','gte')=='gte' else (lambda v:v<=threshold)
    if kind=='time':return compare(elapsed)
    if kind=='region':
        region=trigger.get('region')
        return region is not None and _distance(state,region)<=region.get('radius_m',3)
    if kind=='signal':
        observed=signals.get(trigger.get('signal_id'))
        if isinstance(observed,dict):observed=observed.get('state')
        return observed==trigger.get('signal_state')
    if kind=='action_complete':return trigger.get('action_id') in completed
    other=observations.get(trigger.get('target_actor_id'))
    if other is None:return False
    if kind=='distance':return compare(max(0.,bumper_gap(state,other)['gap_m']))
    if kind=='relative_speed':return compare(abs(state.get('speed_mps',0.)-other.get('speed_mps',0.)))
    if kind=='ttc':
        value=ttc(state,other)
        return value is not None and compare(value)
    raise ValueError('Unknown behavior trigger '+str(kind))


class LaneGraph:
    def __init__(self,definition):
        self.nodes={n['id']:copy.deepcopy(n) for n in definition.get('nodes',[])}
        if not self.nodes:raise ValueError('Reactive planner requires a lane graph')
        self.cells={}
        for node in self.nodes.values():
            cell=(math.floor(node['x']/10),math.floor(node['y']/10))
            self.cells.setdefault(cell,[]).append(node)
            if any(i not in self.nodes for i in node.get('next_ids',[])):
                raise ValueError('Lane graph has unresolved successors')
            for direction in ('left','right'):
                identity=node.get(direction+'_id')
                if identity is not None and identity not in self.nodes:
                    raise ValueError('Lane graph has unresolved adjacent lane')
        self.crossing_zones=self._crossing_zones()

    @staticmethod
    def lane_key(node):
        return (node.get('road_id'),node.get('section_id'),node.get('lane_id'))

    def _crossing_zones(self):
        """Resolve shared junction groups into actual crossing lane intervals.

        A junction membership is not an occupied conflict area: connector roads
        may extend tens of metres past the crossing. Pairwise centerline
        intersections, expanded by the other lane's width, identify the road
        corridor each actor must actually clear. Vehicle extents are added from
        the current observation, not baked into the road definition.
        """
        groups={}
        for node in self.nodes.values():
            for identity in node.get('conflict_ids',[]):
                groups.setdefault(identity,{}).setdefault(self.lane_key(node),[]).append(node)
        zones={}
        for group,lanes in sorted(groups.items()):
            paths=[]
            for key,nodes in sorted(lanes.items()):
                by_id={n['id']:n for n in nodes};incoming={i for n in nodes for i in n.get('next_ids',[]) if i in by_id}
                starts=sorted((n for n in nodes if n['id'] not in incoming),key=lambda n:n['id'])
                if len(starts)!=1:continue  # Branching/loops need explicit separate connector lanes.
                path=[starts[0]];seen={path[0]['id']}
                while True:
                    successors=[by_id[i] for i in path[-1].get('next_ids',[]) if i in by_id and i not in seen]
                    if len(successors)!=1:break
                    path.append(successors[0]);seen.add(path[-1]['id'])
                if len(path)>=2:paths.append((key,path,city_control.lengths(path)))
            for i,(key_a,a,sa) in enumerate(paths):
                for key_b,b,sb in paths[i+1:]:
                    crossings=[]
                    for ia,(a0,a1) in enumerate(zip(a,a[1:])):
                        ax=a1['x']-a0['x'];ay=a1['y']-a0['y'];al=math.hypot(ax,ay)
                        if al<1e-6:continue
                        for ib,(b0,b1) in enumerate(zip(b,b[1:])):
                            bx=b1['x']-b0['x'];by=b1['y']-b0['y'];bl=math.hypot(bx,by)
                            if bl<1e-6:continue
                            cross=ax*by-ay*bx;sine=abs(cross)/(al*bl)
                            # Parallel/opposed disjoint lanes in one junction
                            # do not become a shared mutex. Merge/headway and
                            # observed collision prediction remain independent.
                            if sine<.15:continue
                            dx=b0['x']-a0['x'];dy=b0['y']-a0['y']
                            ta=(dx*by-dy*bx)/cross;tb=(dx*ay-dy*ax)/cross
                            if not -1e-7<=ta<=1+1e-7 or not -1e-7<=tb<=1+1e-7:continue
                            za=a0.get('z',0)+ta*(a1.get('z',0)-a0.get('z',0))
                            zb=b0.get('z',0)+tb*(b1.get('z',0)-b0.get('z',0))
                            if abs(za-zb)>2.5:continue
                            pa=sa[ia]+ta*al;pb=sb[ib]+tb*bl
                            if any(abs(pa-old[0])<.1 and abs(pb-old[1])<.1 for old in crossings):continue
                            crossings.append((pa,pb,sine,a0.get('lane_width_m',3.5),b0.get('lane_width_m',3.5)))
                    for number,(pa,pb,sine,wa,wb) in enumerate(crossings):
                        identity=f'{group}:crossing:{key_a}:{key_b}:{number}'
                        for key,path,cumulative,progress,width in ((key_a,a,sa,pa,wb),(key_b,b,sb,pb,wa)):
                            zones.setdefault(key,[]).append({'id':identity,'group_id':group,
                                'path':path,'cumulative':cumulative,'entry_m':progress-width/(2*sine),
                                'exit_m':progress+width/(2*sine)})
        return zones

    def nearest(self,state,closed=(),*,direction=True,include_closed=False):
        def eligible(n):
            return (n['id'] not in closed and (include_closed or not n.get('closed'))
                and (not direction or abs(_angle(n.get('heading_rad',0)-state.get('heading_rad',0)))<math.pi/2))
        x=state['x'];y=state['y'];cx=math.floor(x/10);cy=math.floor(y/10)
        choices=[]
        for radius in range(4):
            for ix in range(cx-radius,cx+radius+1):
                for iy in range(cy-radius,cy+radius+1):
                    if radius and abs(ix-cx)!=radius and abs(iy-cy)!=radius:continue
                    choices.extend(n for n in self.cells.get((ix,iy),[]) if eligible(n))
            if choices:
                best=min(choices,key=lambda n:(_distance(state,n),n['id']))
                edge=min(x-(cx-radius)*10,(cx+radius+1)*10-x,y-(cy-radius)*10,(cy+radius+1)*10-y)
                if _distance(state,best)<edge-1e-9:return best
        choices=[n for n in self.nodes.values() if eligible(n)]
        return min(choices,key=lambda n:(_distance(state,n),n['id'])) if choices else None

    def path(self,start,goal,closed=()):
        banned=set(closed)
        if start not in self.nodes or goal not in self.nodes:return []
        queue=[(0.,start)];cost={start:0.};parent={}
        while queue:
            distance,identity=heapq.heappop(queue)
            if distance>cost[identity]+1e-9:continue
            node=self.nodes[identity]
            if identity in banned or node.get('closed'):continue
            if identity==goal:
                result=[identity]
                while result[-1]!=start:result.append(parent[result[-1]])
                return [self.nodes[i] for i in reversed(result)]
            for successor in sorted(node.get('next_ids',[])):
                nxt=self.nodes[successor]
                step=max(.01,_distance(node,nxt))/max(.1,nxt.get('speed_limit_mps',12.))
                candidate=distance+step
                if candidate<cost.get(successor,float('inf'))-1e-9:
                    cost[successor]=candidate;parent[successor]=identity
                    heapq.heappush(queue,(candidate,successor))
        return []

    def forward(self,start,distance,closed=()):
        result=[start];travel=0.;seen={start['id']};banned=set(closed)
        while travel<distance:
            last=result[-1]
            options=[self.nodes[i] for i in last.get('next_ids',[]) if i not in seen and i not in banned
                and not self.nodes[i].get('closed')]
            if not options:break
            # Connector turns can share the exact initial tangent with the
            # straight road. Inspect a short, unambiguous graph continuation;
            # selecting by that first tangent alone makes floating point noise
            # alternate between straight and turn occupancy envelopes. This is
            # an observed-heading hypothesis, never another actor's route.
            def continuity(candidate):
                heading=last.get('heading_rad',0.);probe=candidate;distance=0.;visited={probe['id']}
                deviation=abs(_angle(probe.get('heading_rad',0.)-heading))
                for _ in range(16):
                    following=[self.nodes[i] for i in probe.get('next_ids',[]) if i not in visited
                        and i not in banned and not self.nodes[i].get('closed')]
                    if len(following)!=1 or distance>=8:break
                    successor=following[0];distance+=_distance(probe,successor)
                    deviation=max(deviation,abs(_angle(successor.get('heading_rad',0.)-heading)))
                    probe=successor;visited.add(probe['id'])
                return round(deviation,8),candidate['id']
            nxt=options[0] if len(options)==1 else min(options,key=continuity)
            travel+=_distance(last,nxt);result.append(nxt);seen.add(nxt['id'])
        return result

    def adjacent(self,node,target=None):
        choices=[]
        for side in ('left','right'):
            identity=node.get(side+'_id')
            if not identity or not node.get('lane_change_'+side,False):continue
            other=self.nodes[identity]
            if other.get('closed') or other.get('is_junction') or node.get('is_junction'):continue
            if abs(_angle(other.get('heading_rad',0)-node.get('heading_rad',0)))>.4:continue
            if target is not None and (other.get('road_id'),other.get('section_id'),other.get('lane_id'))!=(target.get('road_id'),target.get('section_id'),target.get('lane_id')):continue
            choices.append(other)
        return choices


def _curvature(route):
    maximum=0.
    for a,b,c in zip(route,route[1:],route[2:]):
        ab=_distance(a,b);bc=_distance(b,c);ca=_distance(c,a)
        if min(ab,bc,ca)<1e-6:continue
        cross=abs((b['x']-a['x'])*(c['y']-a['y'])-(b['y']-a['y'])*(c['x']-a['x']))
        maximum=max(maximum,2*cross/(ab*bc*ca))
    return maximum


def _smooth_transition(graph,source,target,state,distance,settings):
    """Quintic blend of legal parallel centerlines, checked along the full path."""
    closed=settings['closed_node_ids']
    a=graph.forward(source,distance+6,closed);b=graph.forward(target,distance+6,closed)
    if len(a)<2 or len(b)<2:return None,'insufficient_lane_length'
    la=city_control.lengths(a);lb=city_control.lengths(b)
    a_start=city_control.project(state,a,la)['progress_m'];b_start=city_control.project(state,b,lb)['progress_m']
    if min(la[-1]-a_start,lb[-1]-b_start)<distance:return None,'insufficient_lane_length'
    result=[]
    for index in range(math.ceil(distance/.75)+1):
        t=min(1,index/math.ceil(distance/.75));s=distance*t
        pa=city_control.interpolate(a,la,a_start+s);pb=city_control.interpolate(b,lb,b_start+s)
        blend=10*t**3-15*t**4+6*t**5
        point={k:pa[k]*(1-blend)+pb[k]*blend for k in ('x','y','z')}
        na=min(a,key=lambda n:_distance(n,pa));nb=min(b,key=lambda n:_distance(n,pb))
        legal=any((n.get('road_id'),n.get('lane_id'))==(nb.get('road_id'),nb.get('lane_id')) for n in graph.adjacent(na))
        if not legal:return None,'lane_change_permission_ends'
        # Between explicitly adjacent driving lanes; refuse shoulders/median jumps.
        if _distance(pa,pb)>(na.get('lane_width_m',3.5)+nb.get('lane_width_m',3.5))/2+.5:
            return None,'adjacent_corridor_disconnected'
        result.append(point)
    # Start at the observed pose so the controller cannot snap the actor to a map node.
    if _distance(state,result[0])>3:return None,'actor_outside_lane_corridor'
    result[0]=_pose(state)
    for p,q in zip(result,result[1:]):p['heading_rad']=math.atan2(q['y']-p['y'],q['x']-p['x'])
    result[-1]['heading_rad']=target.get('heading_rad',0.)
    if abs(_angle(result[0]['heading_rad']-state.get('heading_rad',0)))>.5:return None,'initial_heading_discontinuity'
    if _curvature(result)>settings['max_curvature_inv_m']:return None,'curvature_limit'
    return result,None


class ReactivePlanner:
    """One owner per scenario, called once with the shared pre-tick snapshot.

    `signals` accepts native `{actor_id: {state, signal_id}}` holds as well as
    authored `{signal_id: state}` states for behavior triggers. Signal-based
    speed gates require an actor binding or a mapped signal `node_ids` entry.
    """
    def __init__(self,actors,map_definition,fixed_delta_seconds=.05):
        if not math.isclose(fixed_delta_seconds,.05,abs_tol=1e-9):
            raise ValueError('Reactive planner v1 requires 20Hz physics/control')
        self.graph=LaneGraph(map_definition);self.map=copy.deepcopy(map_definition)
        self.specs={a['id']:copy.deepcopy(a) for a in actors}
        self.memory={};self.last_time=None;self.last_plan=None;self.arrivals={}
        for identity,spec in self.specs.items():
            if spec.get('controller',{}).get('kind')!='reactive':continue
            if not 0<=spec.get('speed_mps',0)<=12:raise ValueError('Reactive speed outside 0–12m/s')
            if len(spec.get('route',[]))<2:raise ValueError('Reactive actor needs a fixed destination route')
            destination=spec.get('destination') or spec['route'][-1]
            goal_node=self.graph.nearest(destination,direction=True,include_closed=True)
            if spec.get('destination') is not None and (goal_node is None or _distance(destination,goal_node)>.25
                or abs(_angle(destination.get('heading_rad',0)-goal_node.get('heading_rad',0)))>.25):
                raise ValueError('Explicit destination must match a lane-graph node and travel direction within 0.25m/rad')
            behavior=spec.get('behavior') or {};settings={**DEFAULTS,**(spec.get('planner') or {})}
            for action in behavior.get('actions',[]):
                if action['kind'] not in KINDS:raise ValueError('Unsupported behavior '+action['kind'])
                if action['kind'] in RISK and not behavior.get('risk_mode'):raise ValueError('Risk action requires explicit risk mode')
                for ref in (action.get('target_actor_id'),action.get('trigger',{}).get('target_actor_id')):
                    if ref is not None and (ref not in self.specs or ref==identity):raise ValueError('Unknown or self target actor')
                node=action.get('target_lane_node_id')
                if node is not None and node not in self.graph.nodes:raise ValueError('Unknown target lane node')
                if action['kind'] in {'cross','sudden_crossing'} and spec.get('kind')!='pedestrian':raise ValueError('Crossing behavior needs a pedestrian')
                if action['kind'] in LATERAL and spec.get('kind')!='vehicle':raise ValueError('Lateral behavior needs a vehicle')
            if any(i not in self.graph.nodes for i in settings['closed_node_ids']):raise ValueError('Unknown closed lane node')
            self.memory[identity]={'route':copy.deepcopy(spec['route']),'original_route':copy.deepcopy(spec['route']),
                'destination':copy.deepcopy(destination),
                'route_revision':0,'index':0,'active_since':None,'completed':set(),'speed':spec.get('speed_mps',3.),
                'settings':settings,'action_route':None,'reason':None,'blocked_since':None,'failed':False,
                'route_history':[], 'stop_latch':False,'terminal_timeout':None,'stopped_since':None,
                'obstruction_since':None,'auto_action':None,'last_reroute_attempt':None,'sign_stops':{},'signs_completed':set(),
                'lead_hold_actor_id':None}

    def step(self,observations,elapsed_s,signals=None):
        if not math.isfinite(elapsed_s) or elapsed_s<0:raise ValueError('Invalid observation time')
        if self.last_time is not None and elapsed_s<self.last_time-1e-9:raise ValueError('Observation time moved backwards')
        observed={s['id']:copy.deepcopy(s) for s in observations} if isinstance(observations,list) else copy.deepcopy(observations)
        if any(i not in observed for i in self.memory):raise ValueError('Snapshot missing controlled actor')
        if self.last_time is not None and elapsed_s-self.last_time<.1-1e-8:
            result=copy.deepcopy(self.last_plan)
            for value in result.values():value['events']=[]
            return result
        signals=signals or {};self._conflict_cache={};self._update_arrivals(observed,elapsed_s)
        result={}
        # The input remains immutable throughout: ordering cannot change observations.
        for identity in sorted(self.memory):result[identity]=self._plan(identity,observed,elapsed_s,signals)
        self.last_time=elapsed_s;self.last_plan=copy.deepcopy(result)
        return result

    def _event(self,identity,time,kind,reason,action=None):
        return {'time_s':time,'actor_id':identity,'type':kind,'reason':reason,
            'action_id':action.get('id') if action else None,'route_revision':self.memory[identity]['route_revision']}

    def _near_conflicts(self,state):
        key=(state.get('id'),state['x'],state['y'],state.get('heading_rad',0.))
        cache=getattr(self,'_conflict_cache',{})
        if key in cache:return cache[key]
        node=self.graph.nearest(state)
        if node is None or _distance(node,state)>8:return {}
        route=self.graph.forward(node,40)
        if len(route)<2:return {}
        cumulative=city_control.lengths(route);origin=city_control.project(state,route,cumulative)['progress_m']
        intervals={};lane_origins={}
        for index,point in enumerate(route):
            lane_origins.setdefault(self.graph.lane_key(point),(point,cumulative[index]-origin))
        for lane,(point,distance) in lane_origins.items():
            for zone in self.graph.crossing_zones.get(lane,[]):
                if lane==self.graph.lane_key(node):
                    center=_center(state)
                    progress=city_control.project(center,zone['path'],zone['cumulative'])['progress_m']
                    # A connector can be only a subsection of the current lane.
                    # Retain signed approach/exit distance beyond its endpoints;
                    # clamping would report every upstream actor at the same line.
                    if progress<=1e-8 or progress>=zone['cumulative'][-1]-1e-8:
                        endpoint=zone['path'][0] if progress<=1e-8 else zone['path'][-1]
                        heading=endpoint.get('heading_rad',0.)
                        progress+=(center['x']-endpoint['x'])*math.cos(heading)+(center['y']-endpoint['y'])*math.sin(heading)
                    entry=zone['entry_m']-progress;exit=zone['exit_m']-progress
                else:
                    offset=city_control.project(point,zone['path'],zone['cumulative'])['progress_m']
                    entry=distance+zone['entry_m']-offset;exit=distance+zone['exit_m']-offset
                extent=_extent(state,point.get('heading_rad',state.get('heading_rad',0.)))
                intervals.setdefault(zone['group_id'],[]).append((entry,exit,extent))
        found={}
        for group,values in intervals.items():
            # Admit once for the union of the actual crossing corridors. Giving
            # a car one pairwise corridor and making it wait for the next permits
            # a four-way circular hold. One narrow shared envelope avoids that
            # partial entry while retaining the same priority/arrival/id order.
            entry=min(v[0] for v in values);exit=max(v[1] for v in values)
            extent=max(v[2] for v in values)
            if exit < -extent-.3 or entry>35:continue
            found[group]={'distance_m':max(0.,entry),'priority':node.get('priority',0),
                'inside':entry<=extent+.3 and exit>=-extent-.3,
                'group_id':group,'entry_distance_m':entry,'exit_distance_m':exit}
        cache[key]=found
        return found

    def _update_arrivals(self,observed,time):
        active=set()
        for identity,state in sorted(observed.items()):
            for conflict,detail in self._near_conflicts(state).items():
                if detail['distance_m']<=20:
                    key=(conflict,identity);active.add(key);self.arrivals.setdefault(key,time)
        self.arrivals={key:value for key,value in self.arrivals.items() if key in active}

    def _yield_conflicts(self,identity,state,observed):
        conflicts=self._near_conflicts(state);blockers=[]
        for conflict,own in conflicts.items():
            if own['inside']:continue  # Clear the junction rather than stop within it.
            for other_id,other in observed.items():
                if other_id==identity:continue
                theirs=self._near_conflicts(other).get(conflict)
                if theirs is None or theirs['distance_m']>20:continue
                a=self.graph.nearest(state);b=self.graph.nearest(other)
                if a and b and (a.get('road_id'),a.get('lane_id'))==(b.get('road_id'),b.get('lane_id')):
                    continue  # Following on the same approach is handled by bumper gap.
                own_order=(-own['priority'],self.arrivals.get((conflict,identity),float('inf')),identity)
                other_order=(-theirs['priority'],self.arrivals.get((conflict,other_id),float('inf')),other_id)
                if theirs['inside'] or other_order<own_order:
                    blockers.append({'actor_id':other_id,'conflict_id':own.get('group_id',conflict),'zone_id':conflict,'distance_m':own['distance_m'],
                        'rule':'occupied_conflict' if theirs['inside'] else 'priority_arrival_id_tiebreak'})
        return blockers

    def _new_route(self,identity,route,time,events,reason,action=None):
        memory=self.memory[identity]
        memory['route_history'].append({'revision':memory['route_revision'],'route':memory['route']})
        memory['route']=route;memory['route_revision']+=1
        events.append(self._event(identity,time,'route_replanned',reason,action))

    def _reroute(self,identity,state,time,events,action=None):
        memory=self.memory[identity];settings=memory['settings']
        if not settings['allow_reroute']:return 'reroute_disabled'
        # The destination is pinned; nearest() never excludes a closed goal.
        start=self.graph.nearest(state,include_closed=True);goal=self.graph.nearest(memory['destination'],direction=False,include_closed=True)
        if start is None or goal is None:return 'no_legal_route'
        if _distance(state,start)>max(2,start.get('lane_width_m',3.5)/2):return 'outside_reroute_corridor'
        route=self.graph.path(start['id'],goal['id'],settings['closed_node_ids'])
        if len(route)<2:return 'no_legal_route'
        new=[_pose(state)]+[_pose(n) for n in route if _distance(n,state)>.5]
        if len(new)<2:return 'no_legal_route'
        if _curvature(new)>settings['max_curvature_inv_m']:return 'reroute_curvature_limit'
        self._new_route(identity,new,time,events,'closed_lane_reroute',action)
        return None

    def _gap(self,state,others,target,settings,risk=False):
        minimum=.75 if risk else settings['min_gap_m'];violations=[]
        for other in others:
            relative=bumper_gap({**state,'heading_rad':target.get('heading_rad',state.get('heading_rad',0.))},other)
            h=target.get('heading_rad',0.);lateral=abs(-math.sin(h)*(other['x']-target['x'])+math.cos(h)*(other['y']-target['y']))
            if lateral>target.get('lane_width_m',3.5)/2+_dimensions(other)[1]/2:continue
            ahead=relative['longitudinal_m']>=0
            closing=max(0.,(state['speed_mps']-other.get('speed_mps',0.)) if ahead else (other.get('speed_mps',0.)-state['speed_mps']))
            entry_margin=0. if risk else _dimensions(state)[0]/2
            required=minimum+entry_margin+closing*(.5 if risk else settings['headway_s'])
            if relative['gap_m']<required:
                violations.append({'actor_id':other['id'],'gap_m':relative['gap_m'],'required_gap_m':required,'position':'front' if ahead else 'rear'})
        return violations

    def _lane_transition(self,identity,state,observed,action,time,events):
        memory=self.memory[identity];settings=memory['settings'];kind=action['kind']
        if not settings['allow_lane_changes']:return 'lane_changes_disabled',{}
        source=self.graph.nearest(state,settings['closed_node_ids'])
        if source is None:return 'no_lane',{}
        specified=self.graph.nodes.get(action.get('target_lane_node_id'))
        targets=self.graph.adjacent(source,specified)
        if kind=='diverge' and specified is not None:
            goal=self.graph.nearest(memory['destination'],direction=False,include_closed=True)
            first=self.graph.path(source['id'],specified['id'],settings['closed_node_ids'])
            second=self.graph.path(specified['id'],goal['id'],settings['closed_node_ids']) if goal else []
            if first and second:
                self._new_route(identity,[_pose(n) for n in first+second[1:]],time,events,'selected_branch',action)
                memory['action_route']={'action_id':action['id'],'endpoint':_pose(specified)}
                return None,{}
            return 'selected_branch_cannot_reach_destination',{}
        if not targets:return 'no_legal_adjacent_lane',{}
        target=targets[0]
        others=[s for i,s in observed.items() if i!=identity]
        violations=self._gap(state,others,target,settings,kind=='cut_in')
        if violations:return 'gap_wait',{'gap_checks':violations}
        length=max(20.,min(40.,state.get('speed_mps',0)*4+16))
        route,error=_smooth_transition(self.graph,source,target,state,length,settings)
        if error:return error,{}
        end=self.graph.nearest(route[-1],settings['closed_node_ids'])
        goal=self.graph.nearest(memory['destination'],direction=False,include_closed=True)
        if end is None or goal is None:return 'no_legal_route',{}
        if kind in {'overtake','obstacle_bypass'}:
            # Pass fully, then make a second independently checked transition.
            obstacle=self._lead(state,memory['route'],others,settings)
            if obstacle is None:return 'no_observed_obstruction',{}
            observed_obstacle=observed[obstacle['actor_id']]
            if kind=='obstacle_bypass' and observed_obstacle.get('speed_mps',0.)>.5:return 'obstruction_not_stationary',{}
            clearance=obstacle['gap_m']+sum(_dimensions(s)[0]/2 for s in (state,observed_obstacle))+settings['min_gap_m']+12
            cruise=self.graph.forward(end,max(10.,clearance-length),settings['closed_node_ids'])
            return_start=cruise[-1]
            return_targets=self.graph.adjacent(return_start,source)
            if not return_targets:return 'no_legal_return_lane',{}
            returning,error=_smooth_transition(self.graph,return_start,return_targets[0],return_start,length,settings)
            if error:return error,{}
            return_end=self.graph.nearest(returning[-1],settings['closed_node_ids'])
            tail=self.graph.path(return_end['id'],goal['id'],settings['closed_node_ids'])
            if not tail:return 'return_lane_cannot_reach_destination',{}
            route=route+[_pose(n) for n in cruise[1:]]+returning[1:]+[_pose(n) for n in tail[1:]]
            endpoint=returning[-1]
            transitions=[{'start':_pose(state),'end':_pose(route[math.ceil(length/.75)]),'target_node_id':target['id']},
                {'start':_pose(return_start),'end':_pose(returning[-1]),'target_node_id':return_targets[0]['id']}]
        else:
            tail=self.graph.path(end['id'],goal['id'],settings['closed_node_ids'])
            if not tail:return 'target_lane_cannot_reach_destination',{}
            endpoint=route[-1];route+= [_pose(n) for n in tail[1:]]
            transitions=[{'start':_pose(state),'end':_pose(endpoint),'target_node_id':target['id']}]
        if _curvature(route)>settings['max_curvature_inv_m']:return 'curvature_limit',{}
        # A smooth legal centerline is not enough: do not route a vehicle body
        # through the obstruction while it is still changing lanes.
        for other in others:
            if other.get('speed_mps',0.)>.3:continue
            for point in route:
                own={**state,**point}
                if _rectangles_overlap(own,other,.3):
                    return 'candidate_hits_observed_obstacle',{'obstacle_actor_id':other['id']}
        self._new_route(identity,route,time,events,kind,action)
        memory['action_route']={'action_id':action['id'],'endpoint':_pose(endpoint),'transitions':transitions,
            'risk':kind=='cut_in'}
        return None,{'target_lane_node_id':target['id'],'planned_curvature_inv_m':_curvature(route)}

    def _active_lateral_gap(self,identity,state,observed):
        memory=self.memory[identity];active=memory['action_route'];settings=memory['settings']
        if not active or not active.get('transitions'):return None
        route=memory['route'];projection=city_control.project(state,route)
        others=[other for key,other in observed.items() if key!=identity]
        for transition in active['transitions']:
            start=city_control.project(transition['start'],route)['progress_m']
            end=city_control.project(transition['end'],route)['progress_m']
            if projection['progress_m']>end-1:continue
            distance=max(0.,start-projection['progress_m'])
            braking=state.get('speed_mps',0.)**2/(2*settings['max_deceleration_mps2'])+settings['min_gap_m']+3
            if distance>max(8.,braking):continue
            template=self.graph.nodes[transition['target_node_id']]
            target=min((n for n in self.graph.nodes.values()
                if (n.get('road_id'),n.get('section_id'),n.get('lane_id'))==
                   (template.get('road_id'),template.get('section_id'),template.get('lane_id'))),
                key=lambda n:_distance(state,n))
            violations=self._gap(state,others,target,settings,active.get('risk',False))
            if violations:
                # Once the body has entered the target corridor, stopping for a
                # stationary rear actor can create mutual headway deadlock. A
                # forward-only crawl increases the observed rear clearance; it
                # never presumes that the other actor will yield in the future.
                center=_center(state);heading=target.get('heading_rad',0.)
                lateral=abs(-math.sin(heading)*(center['x']-target['x'])+math.cos(heading)*(center['y']-target['y']))
                entered=lateral < target.get('lane_width_m',3.5)/2+_extent(state,heading+math.pi/2)-.1
                rear_yield=(distance<.3 and entered and all(v['position']=='rear' and v['gap_m']>=.5
                    and observed[v['actor_id']].get('speed_mps',0.)<=.2 for v in violations))
                return {'gap_checks':violations,'distance_to_transition_m':distance,
                    'target_lane_node_id':target['id'],
                    'resolution':'observed_stationary_rear_yield' if rear_yield else 'wait_before_lane_entry',
                    'speed_limit_mps':1. if rear_yield else math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(distance-2)))}
        return None

    def _lead(self,state,route,others,settings):
        position=city_control.project(_center(state),route);leaders=[]
        for other in others:
            if abs(state.get('z',0)-other.get('z',0))>2.5:continue
            projected=city_control.project(_center(other),route)
            if projected['progress_m']<=position['progress_m']:continue
            tangent=route[min(projected['segment'],len(route)-1)].get('heading_rad',state.get('heading_rad',0.))
            if projected['error_m']>_dimensions(state)[1]/2+_extent(other,tangent+math.pi/2)+.4:continue
            gap=projected['progress_m']-position['progress_m']-_extent(state,tangent)-_extent(other,tangent)
            if gap<80:leaders.append({'actor_id':other['id'],'gap_m':gap,'speed_mps':other.get('speed_mps',0.)})
        return min(leaders,key=lambda x:(x['gap_m'],x['actor_id'])) if leaders else None

    def _stopped_lead_hold(self,identity,leader):
        """Keep a near stopped lead from inducing low-speed throttle creep.

        Only the current observed leader's speed and bumper clearance are used.
        A wider release band prevents noisy standstill observations from toggling
        hold. Clearing this local latch never clears another braking constraint.
        """
        memory=self.memory[identity];previous=memory['lead_hold_actor_id']
        cleared=None
        if previous is not None and (leader is None or leader['actor_id']!=previous):
            memory['lead_hold_actor_id']=None
            cleared={'actor_id':previous,'state':'cleared',
                'release_reason':'lead_missing' if leader is None else 'lead_changed',
                'current_actor_id':leader['actor_id'] if leader else None}
        if leader is None:return False,cleared
        minimum=memory['settings']['min_gap_m']
        detail={**leader,'enter_gap_m':minimum+1.,'release_gap_m':minimum+1.5,
            'enter_speed_mps':.1,'release_speed_mps':.3}
        if memory['lead_hold_actor_id']==leader['actor_id']:
            release=('lead_moving' if leader['speed_mps']>=detail['release_speed_mps'] else
                'gap_opened' if leader['gap_m']>=detail['release_gap_m'] else None)
            if release:
                memory['lead_hold_actor_id']=None
                return False,{**detail,'state':'released','release_reason':release}
            return True,{**detail,'state':'holding'}
        if leader['speed_mps']<=detail['enter_speed_mps'] and leader['gap_m']<=detail['enter_gap_m']:
            memory['lead_hold_actor_id']=leader['actor_id']
            return True,{**detail,'state':'holding'}
        return False,cleared

    def _signal_hold(self,identity,state,route,signals):
        binding=signals.get(identity)
        if isinstance(binding,dict) and binding.get('state') in {'red','yellow'}:
            return {'signal_id':binding.get('signal_id'),'state':binding['state'],'distance_m':binding.get('distance_m',0.)}
        for signal in self.map.get('signals',[]):
            value=signals.get(signal['id'])
            if isinstance(value,dict):value=value.get('state')
            if value not in {'red','yellow'}:continue
            line=self._approach_signal(state,route,signal)
            if line is not None:return {'signal_id':signal['id'],'state':value,'distance_m':line['distance_m']}
        return None

    def _approach_signal(self,state,route,signal):
        bindings=[self.graph.nodes[i] for i in signal.get('node_ids',[]) if i in self.graph.nodes]
        if not bindings:return None
        projection=city_control.project(_center(state),route);choices=[]
        for node in bindings:
            projected=city_control.project(node,route)
            if projected['error_m']>.75:continue
            distance=projected['progress_m']-projection['progress_m']-_extent(state,node.get('heading_rad',0))
            if -1.0<=distance<=35:choices.append({'distance_m':max(0.,distance),'node_id':node['id']})
        return min(choices,key=lambda value:value['distance_m']) if choices else None

    def _road_sign(self,identity,state,route,time,events,conflicts):
        memory=self.memory[identity];settings=memory['settings'];limits=[]
        for signal in self.map.get('signals',[]):
            kind=signal.get('kind')
            # Compiler marks yield explicitly through regulatory_kind while the
            # stable catalog's broad kind remains 'other'. Unknown 'other' is inert.
            if kind=='other':kind=signal.get('regulatory_kind')
            if kind not in {'stop','yield'}:continue
            line=self._approach_signal(state,route,signal)
            if line is None:continue
            key=(signal['id'],line['node_id'])
            if kind=='yield':
                if conflicts:limits.append({**line,'signal_id':signal['id'],'kind':'yield','speed_limit_mps':
                    math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(line['distance_m']-2)))})
                continue
            if key in memory['signs_completed']:continue
            if line['distance_m']<=3 and state.get('speed_mps',0.)<=.2:
                memory['sign_stops'].setdefault(key,time)
                if time-memory['sign_stops'][key]>=1 and not conflicts:
                    memory['signs_completed'].add(key)
                    events.append(self._event(identity,time,'stop_sign_completed',signal['id']))
                    continue
            else:memory['sign_stops'].pop(key,None)
            limits.append({**line,'signal_id':signal['id'],'kind':'stop','mandatory_dwell_s':1,
                'dwell_s':max(0.,time-memory['sign_stops'][key]) if key in memory['sign_stops'] else 0.,
                'speed_limit_mps':math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(line['distance_m']-2)))})
        return min(limits,key=lambda value:value['speed_limit_mps']) if limits else None

    def _crossing_conflicts(self,identity,state,observed,route,speed):
        """Short-horizon constant-velocity occupancy, not another actor's intent."""
        if speed<.1:return []
        length=city_control.lengths(route);projection=city_control.project(state,route,length)
        conflicts=[]
        for other_id,other in observed.items():
            if other_id==identity or _distance(state,other)>45:continue
            h=state.get('heading_rad',0);oh=other.get('heading_rad',0)
            # Same-direction headway is handled separately and must not produce
            # symmetric stops in a platoon. Stationary obstacles use the route gap.
            if abs(_angle(oh-h))<.5 or other.get('speed_mps',0)<.2:continue
            vx,vy=_velocity(other)
            for index in range(1,17):
                horizon=index*.25
                own=city_control.interpolate(route,length,min(length[-1],projection['progress_m']+speed*horizon))
                ahead=city_control.interpolate(route,length,min(length[-1],projection['progress_m']+speed*horizon+.25))
                own.update(id=identity,heading_rad=math.atan2(ahead['y']-own['y'],ahead['x']-own['x']),
                           dimensions_m=state.get('dimensions_m') or state.get('dimensions') or {},
                           bbox_center=state.get('bbox_center') or {})
                predicted={**other,'x':other['x']+vx*horizon,'y':other['y']+vy*horizon}
                if _rectangles_overlap(own,predicted,.4):
                    conflicts.append({'actor_id':other_id,'time_to_conflict_s':horizon,
                        'distance_m':speed*horizon,'prediction':'observed_constant_velocity'})
                    break
        return conflicts

    def _plan(self,identity,observed,time,signals):
        spec=self.specs[identity];state=observed[identity];memory=self.memory[identity];settings=memory['settings']
        events=[];diagnostics={'observation_basis':'current_snapshot_ground_truth',
            'footprint_basis':'native_bbox_xy_yaw_projection' if state.get('dimensions_m') or state.get('dimensions') or state.get('length_m') else 'conservative_default_4.8x2m',
            'negotiation':'priority_arrival_id_tiebreak_no_v2x'}
        actions=(spec.get('behavior') or {}).get('actions',[]);action=None;reason='route_follow';hold=False
        # Immediate speed/resume actions can complete in this planning tick.
        for _ in range(len(actions)+1):
            if memory['terminal_timeout'] is not None:
                reason=memory['terminal_timeout'];hold=True;break
            if memory['index']>=len(actions):break
            action=actions[memory['index']]
            if memory['active_since'] is None:
                if not trigger_matches(action.get('trigger',{}),state,observed,time,signals,memory['completed']):
                    action=None;break
                memory['active_since']=time;events.append(self._event(identity,time,'action_started',action['kind'],action))
            age=time-memory['active_since'];kind=action['kind'];done=False
            duration=action.get('duration_s',0)
            completing_timed=(duration>0 and age>=duration and kind in {'target_speed','resume','route_follow','follow','yield','cross','sudden_crossing','signal_violation'})
            completing_stop=(kind in {'stop','wait','emergency_brake'} and memory['stopped_since'] is not None
                and state.get('speed_mps',0.)<=.2 and time-memory['stopped_since']>=duration)
            if age>=action.get('timeout_s',30)-1e-9 and not completing_timed and not completing_stop:
                policy=action.get('on_timeout','stop');events.append(self._event(identity,time,'action_timeout',policy,action))
                if policy=='skip':done=True
                else:
                    memory['failed']=policy=='fail';memory['stop_latch']=True;reason='action_timeout_'+policy
                    memory['terminal_timeout']=reason;hold=True;break
            elif kind in {'target_speed','resume'}:
                memory['speed']=action.get('speed_mps') if action.get('speed_mps') is not None else spec.get('speed_mps',3.)
                memory['stop_latch']=False;done=age>=action.get('duration_s',0)
            elif kind in {'stop','wait','emergency_brake'}:
                hold=True;reason=kind
                if state.get('speed_mps',0.)<=.2:
                    if memory['stopped_since'] is None:memory['stopped_since']=time
                else:memory['stopped_since']=None
                done=memory['stopped_since'] is not None and time-memory['stopped_since']>=action.get('duration_s',0)
                if done:memory['stop_latch']=kind!='wait'
            elif kind=='reroute':
                error=self._reroute(identity,state,time,events,action)
                if error:reason=error;hold=True
                else:done=True
            elif kind in LATERAL:
                active=memory['action_route']
                if active is None or active['action_id']!=action['id']:
                    error,details=self._lane_transition(identity,state,observed,action,time,events)
                    diagnostics.update(details)
                    if error:reason=error;hold=kind in {'merge','lane_change','cut_in','obstacle_bypass'}
                    else:reason=kind
                else:
                    position=city_control.project(state,memory['route'])
                    endpoint=city_control.project(active['endpoint'],memory['route'])
                    done=position['progress_m']>=endpoint['progress_m']-1 and position['error_m']<1.5
                    reason=kind
            elif kind=='arrive':
                done=_distance(state,memory['destination'])<=2 and state.get('speed_mps',0.)<=.2
            elif kind in {'route_follow','follow','yield','cross','sudden_crossing','signal_violation'}:
                reason=kind
                done=age>=action.get('duration_s',0) if action.get('duration_s',0)>0 else False
                if kind in {'cross','sudden_crossing'}:
                    memory['stop_latch']=False
                    done=done or (_distance(state,memory['destination'])<=1 and state.get('speed_mps',0.)<=.2)
            if done:
                memory['completed'].add(action['id']);memory['index']+=1;memory['active_since']=None
                memory['action_route']=None;memory['stopped_since']=None
                events.append(self._event(identity,time,'action_completed',kind,action));action=None
                # A completed stop still holds in this tick; an explicit resume releases it.
                continue
            break
        if memory['stop_latch']:hold=True;reason=reason if reason!='route_follow' else 'held_after_stop'
        if memory['failed']:hold=True;reason='behavior_failed'
        route=memory['route'];projection=city_control.project(state,route)
        speed=action['speed_mps'] if action and action.get('speed_mps') is not None else memory['speed']
        others=[s for i,s in observed.items() if i!=identity]
        node=self.graph.nearest(state)
        if node:speed=min(speed,node.get('speed_limit_mps',12.))
        curvature=_curvature(route[max(0,projection['segment']-1):projection['segment']+30])
        if curvature>1e-7:speed=min(speed,math.sqrt(settings['max_lateral_acceleration_mps2']/curvature))
        # Closed lanes are assessed along the remaining route, never silently reopened.
        closed=set(settings['closed_node_ids'])|{n['id'] for n in self.graph.nodes.values() if n.get('closed')}
        if closed and memory['action_route'] is None:
            samples=route[projection['segment']:]
            if any((nearest:=self.graph.nearest(p,direction=False,include_closed=True)) and nearest['id'] in closed for p in samples):
                error=self._reroute(identity,state,time,events)
                if error:hold=True;reason=error
                else:route=memory['route'];reason='closed_lane_reroute'
        leader=self._lead(state,route,others,settings)
        if action and action['kind']=='follow' and action.get('target_actor_id'):
            target=self._lead(state,route,[observed[action['target_actor_id']]],settings)
            diagnostics['follow_target']={'actor_id':action['target_actor_id'],'in_route_corridor':target is not None}
            if target is None:hold=True;reason='follow_target_not_ahead'
            # Keep all physical leaders as constraints; an explicit target is never
            # permission to drive through an intervening vehicle.
        auto=memory['auto_action']
        if auto is not None and memory['action_route'] is not None:
            endpoint=city_control.project(memory['action_route']['endpoint'],route)
            if projection['progress_m']>=endpoint['progress_m']-1 and projection['error_m']<1.5:
                events.append(self._event(identity,time,'action_completed','automatic_bypass_rejoined',auto))
                memory['auto_action']=None;memory['action_route']=None
        elif (action is None and leader and leader['speed_mps']<.3
              and self.specs.get(leader['actor_id'],{}).get('kind')=='static'
              and settings['allow_lane_changes'] and settings['allow_reroute']
              and not self._signal_hold(identity,state,route,signals)
              and not self._yield_conflicts(identity,state,observed)):
            if memory['obstruction_since'] is None:memory['obstruction_since']=time
            if time-memory['obstruction_since']>=2:
                auto={'id':'automatic-bypass','kind':'obstacle_bypass'}
                error,details=self._lane_transition(identity,state,observed,auto,time,events)
                diagnostics['automatic_bypass']={'status':error or 'planned',**details}
                if not error:
                    memory['auto_action']=auto;route=memory['route'];reason='automatic_obstacle_bypass'
                    leader=self._lead(state,route,others,settings)
        else:memory['obstruction_since']=None
        if leader:
            diagnostics['lead']=leader
            limit=max(0.,(leader['gap_m']-settings['min_gap_m'])/settings['headway_s'])
            braking=math.sqrt(max(0.,leader['speed_mps']**2+2*settings['max_deceleration_mps2']*(leader['gap_m']-settings['min_gap_m'])))
            limited=min(speed,limit,braking)
            if limited<speed-.05:reason='follow_gap';speed=limited
        lead_stop,lead_hold=self._stopped_lead_hold(identity,leader)
        if lead_hold is not None:diagnostics['lead_hold']=lead_hold
        if lead_stop:
            if not hold:reason='follow_lead_stop'
            hold=True
        lateral=self._active_lateral_gap(identity,state,observed)
        if lateral:
            diagnostics['active_lateral_gap']=lateral
            speed=min(speed,lateral['speed_limit_mps'])
            reason='committed_merge_clearance' if lateral.get('resolution')=='observed_stationary_rear_yield' else 'active_lane_gap_wait'
        conflicts=self._yield_conflicts(identity,state,observed)
        if conflicts and not (action and action['kind']=='sudden_crossing'):
            diagnostics['conflicts']=conflicts
            nearest=min(c['distance_m'] for c in conflicts)
            speed=min(speed,math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(nearest-_dimensions(state)[0]/2-3))))
            reason='yield_conflict'
        road_sign=self._road_sign(identity,state,route,time,events,conflicts) if settings['obey_signals'] else None
        if road_sign:
            diagnostics['road_sign']=road_sign;speed=min(speed,road_sign['speed_limit_mps'])
            reason='stop_sign_wait' if road_sign['kind']=='stop' else 'yield_sign_wait'
        if action and action['kind']=='yield' and action.get('target_actor_id'):
            target=observed[action['target_actor_id']]
            if _distance(state,target)<20:hold=True;reason='yield_target';diagnostics['yield_actor_id']=target['id']
            elif action.get('duration_s',0)==0:
                memory['completed'].add(action['id']);memory['index']+=1;memory['active_since']=None
                events.append(self._event(identity,time,'action_completed','yield_target_clear',action))
        elif action and action['kind']=='yield' and not conflicts and action.get('duration_s',0)==0:
            memory['completed'].add(action['id']);memory['index']+=1;memory['active_since']=None
            events.append(self._event(identity,time,'action_completed','conflict_clear',action))
        predicted=self._crossing_conflicts(identity,state,observed,route,speed)
        if predicted and not (action and action['kind']=='sudden_crossing'):
            diagnostics['predicted_conflicts']=predicted
            for conflict in predicted:
                other=observed[conflict['actor_id']]
                # Right-of-way lets the deterministic winner proceed unless the
                # observed collision is already imminent. Pedestrians yield before
                # entering; vehicles always yield to an observed crossing pedestrian.
                own_pedestrian=spec.get('kind')=='pedestrian'
                other_pedestrian=self.specs.get(other['id'],{}).get('kind')=='pedestrian'
                winner=not conflicts and self._near_conflicts(state) and not own_pedestrian
                if winner and not other_pedestrian and conflict['time_to_conflict_s']>1:continue
                speed=min(speed,math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(conflict['distance_m']-settings['min_gap_m']-3))))
                if own_pedestrian or conflict['time_to_conflict_s']<=1:hold=True
                reason='yield_predicted_crossing'
        signal=self._signal_hold(identity,state,route,signals)
        if signal and settings['obey_signals'] and not (action and action['kind']=='signal_violation'):
            diagnostics['signal']=signal;reason='signal_wait'
            speed=min(speed,math.sqrt(max(0.,2*settings['max_deceleration_mps2']*(signal['distance_m']-2))))
        # A current footprint overlap or imminent observed collision always holds,
        # including risk scripts. No scenario can authorize teleport-based avoidance.
        for other in others:
            gap=bumper_gap(state,other)
            if gap['longitudinal_m']>=0 and gap['gap_m']<.4 and gap['lateral_clearance_m']<.1:
                hold=True;reason='footprint_emergency_hold';diagnostics['emergency_actor_id']=other['id'];break
        if hold:speed=0.
        if speed<.2 and state.get('speed_mps',0.)<.2 and _distance(state,memory['destination'])>2:
            if memory['blocked_since'] is None:memory['blocked_since']=time
            diagnostics['waiting_s']=time-memory['blocked_since']
            if diagnostics['waiting_s']>=settings['deadlock_timeout_s'] and reason not in {'stop','wait','emergency_brake','held_after_stop','signal_wait','stop_sign_wait','yield_sign_wait','yield_conflict'}:
                reason='deadlock_no_legal_progress';diagnostics['resolution']='held_until_observation_or_authored_action_changes'
                if settings['allow_reroute'] and action is None and (memory['last_reroute_attempt'] is None or time-memory['last_reroute_attempt']>=2):
                    memory['last_reroute_attempt']=time
                    occupied=[]
                    for other in others:
                        if other.get('speed_mps',0.)>.3:continue
                        occupied.extend(n['id'] for n in self.graph.nodes.values() if _distance(n,other)<_dimensions(other)[0]/2+1)
                    original_closed=settings['closed_node_ids'];settings['closed_node_ids']=list(set(original_closed)|set(occupied))
                    error=self._reroute(identity,state,time,events)
                    settings['closed_node_ids']=original_closed
                    diagnostics['deadlock_reroute']=error or 'alternate_route_found'
                    if not error:route=memory['route'];reason='deadlock_alternate_route';memory['blocked_since']=None
        else:memory['blocked_since']=None
        if _distance(state,memory['destination'])<=2 and state.get('speed_mps',0.)<=.2:
            speed=0.;reason='arrived'
        if reason!=memory['reason']:
            events.append(self._event(identity,time,'decision_changed',reason,action));memory['reason']=reason
        return {'planner_version':VERSION,'route':copy.deepcopy(route),'speed_mps':max(0.,min(12.,speed)),
            'stop':speed<=.001,'reason':reason,'state':reason,'route_revision':memory['route_revision'],
            'action_id':action.get('id') if action else None,'events':events,'diagnostics':diagnostics,
            'original_route':copy.deepcopy(memory['original_route']),'destination':copy.deepcopy(memory['destination'])}
