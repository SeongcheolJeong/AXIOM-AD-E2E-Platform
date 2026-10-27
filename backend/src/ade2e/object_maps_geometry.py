"""Deterministic authoring preview and graph geometry, never a driven trajectory."""
import math
import hashlib
from pathlib import Path
from collections import defaultdict
from .object_maps_schemas import ObjectMapDefinition
LOADED_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def advance(x,y,h,length,k0,k1):
    if abs(k1-k0)<1e-12:
        if abs(k0)<1e-12:return x+length*math.cos(h),y+length*math.sin(h),h
        return x+(math.sin(h+k0*length)-math.sin(h))/k0,y-(math.cos(h+k0*length)-math.cos(h))/k0,h+k0*length
    # Composite Simpson integration of the analytic clothoid heading.
    count=max(2,math.ceil(length/.25));count+=count%2;ds=length/count
    rate=(k1-k0)/length
    def heading(s):return h+k0*s+.5*rate*s*s
    cx=cy=0.
    for i in range(count+1):
        weight=1 if i in (0,count) else 4 if i%2 else 2
        angle=heading(i*ds);cx+=weight*math.cos(angle);cy+=weight*math.sin(angle)
    return x+cx*ds/3,y+cy*ds/3,heading(length)


def sample_road(road,spacing=2.):
    x,y,z,h=road.origin.x,road.origin.y,road.origin.z,road.origin.heading_rad
    s=0.;points=[dict(x=x,y=y,z=z,heading_rad=h,s_m=s)];starts=[]
    for geometry in road.geometries:
        starts.append(dict(x=x,y=y,z=z,heading_rad=h,s_m=s))
        count=math.ceil(geometry.length_m/spacing)
        for i in range(1,count+1):
            local=geometry.length_m*i/count;k=geometry.curvature_start+(geometry.curvature_end-geometry.curvature_start)*local/geometry.length_m
            px,py,ph=advance(x,y,h,local,geometry.curvature_start,k)
            points.append(dict(x=px,y=py,z=z,heading_rad=ph,s_m=s+local))
        x,y,h=points[-1]['x'],points[-1]['y'],points[-1]['heading_rad'];s+=geometry.length_m
    return {'id':road.id,'points':points,'geometry_starts':starts}


def lane_offset(road,lane):
    side=[v for v in road.lanes if v.id*lane.id>0 and abs(v.id)<abs(lane.id)]
    return (-1 if lane.id>0 else 1)*(sum(v.width_m for v in side)+lane.width_m/2)


def offset_pose(point,offset,reverse=False):
    h=point['heading_rad']
    return dict(x=point['x']-math.sin(h)*offset,y=point['y']+math.cos(h)*offset,z=point['z'],heading_rad=math.atan2(math.sin(h+(math.pi if reverse else 0)),math.cos(h+(math.pi if reverse else 0))))


def pose_at(road,s_m,t_m=0):
    x,y,z,h=road.origin.x,road.origin.y,road.origin.z,road.origin.heading_rad
    for geometry in road.geometries:
        local=min(s_m,geometry.length_m)
        k=geometry.curvature_start+(geometry.curvature_end-geometry.curvature_start)*local/geometry.length_m
        x,y,h=advance(x,y,h,local,geometry.curvature_start,k)
        s_m-=local
        if s_m<=1e-9:break
    return offset_pose(dict(x=x,y=y,z=z,heading_rad=h),t_m)


def preview_map(definition):
    if not isinstance(definition,ObjectMapDefinition):definition=ObjectMapDefinition.model_validate(definition)
    issues=[]
    if definition.read_only:
        return {'schema_version':'object-map-preview/1','buildable':False,'read_only':True,'roads':[],'lanes':[],'issues':[{'severity':'error','code':'unsupported_import','message':reason} for reason in definition.unsupported] or [{'severity':'error','code':'read_only','message':'Preserved OpenDRIVE import is read-only'}],'nodes':[],'fidelity':'authored_road_geometry'}
    if not definition.roads:issues.append({'severity':'error','code':'empty_map','message':'Add at least one road before building'})
    count=sum((1+sum(math.ceil(g.length_m/2) for g in r.geometries))*len(r.lanes) for r in definition.roads)
    if count>50000:raise ValueError('Map exceeds the 50,000 sampled lane-node bound; shorten roads or reduce lanes')
    roads={r.id:r for r in definition.roads};road_ids={r.id:i+1 for i,r in enumerate(definition.roads)}
    samples={r.id:sample_road(r) for r in definition.roads};nodes=[];lanes=[];by_lane={};by_id={}
    for road in definition.roads:
        max_width=max(sum(l.width_m for l in road.lanes if l.id*side>0) for side in (-1,1))
        if any(max(abs(g.curvature_start),abs(g.curvature_end))*max_width>=.8 for g in road.geometries):
            issues.append({'severity':'error','code':'lane_fold','road_id':road.id,'message':'Curve radius is too small for the road width; offset lanes would fold'})
        for lane in sorted(road.lanes,key=lambda l:l.id):
            point_samples=samples[road.id]['points'];offset=lane_offset(road,lane);ordered=[]
            for index,point in enumerate(point_samples):
                node={'id':f'r{road_ids[road.id]}:l{lane.id}:p{index}',**offset_pose(point,offset,lane.id>0),'road_id':road_ids[road.id],'section_id':0,'lane_id':lane.id,'lane_width_m':lane.width_m,'is_junction':road.junction_id is not None,'next_ids':[],'closed':lane.closed,'speed_limit_mps':lane.speed_limit_mps,'priority':lane.priority,'conflict_ids':[]}
                nodes.append(node);ordered.append(node);by_id[node['id']]=node
            # Physical adjacency stays in reference-line sample order.
            by_lane[(road.id,lane.id)]=ordered
            travel=ordered if lane.id<0 else list(reversed(ordered))
            for current,nxt in zip(travel,travel[1:]):current['next_ids'].append(nxt['id'])
            lanes.append({'id':f'{road.id}:{lane.id}','road_id':road.id,'lane_id':lane.id,'width_m':lane.width_m,'closed':lane.closed,'points':[{k:p[k] for k in ('x','y','z','heading_rad')} for p in travel]})
        for lane in road.lanes:
            for direction,delta in (('left',1 if lane.id<0 else -1),('right',-1 if lane.id<0 else 1)):
                other=lane.id+delta
                if lane.id*other<=0 or (road.id,other) not in by_lane:continue
                for node,adjacent in zip(by_lane[(road.id,lane.id)],by_lane[(road.id,other)]):
                    node[direction+'_id']=adjacent['id']
                    node['lane_change_'+direction]=lane.lane_change in ('both',direction) and not road.junction_id
    for c in definition.connections:
        origin=by_lane[(c.from_road,c.from_lane)][-1 if c.from_lane<0 else 0]
        target=by_lane[(c.to_road,c.to_lane)][0 if c.to_lane<0 else -1]
        distance=math.dist([origin[k] for k in ('x','y','z')],[target[k] for k in ('x','y','z')])
        angle=abs(math.atan2(math.sin(origin['heading_rad']-target['heading_rad']),math.cos(origin['heading_rad']-target['heading_rad'])))
        if distance>.5 or angle>.2:
            issues.append({'severity':'error','code':'disconnected_lane','road_id':c.from_road,'message':f'Connection {c.from_road}/{c.from_lane} → {c.to_road}/{c.to_lane} has {distance:.2f} m gap and {angle:.2f} rad heading discontinuity'})
        origin['next_ids'].append(target['id'])
    # Reject fan-out outside explicit junction connector roads: OpenDRIVE has
    # exactly one road/junction link at each reference-line end.
    ends=defaultdict(set)
    for c in definition.connections:
        for source,lid,target,tid,incoming in ((c.from_road,c.from_lane,c.to_road,c.to_lane,False),(c.to_road,c.to_lane,c.from_road,c.from_lane,True)):
            end=('start' if lid<0 else 'end') if incoming else ('end' if lid<0 else 'start')
            target_junction=roads[target].junction_id if roads[source].junction_id!=roads[target].junction_id else None
            ends[(source,end)].add(('junction',target_junction) if target_junction else ('road',target,'start' if (tid<0)!=incoming else 'end'))
    for (rid,end),targets in ends.items():
        if len(targets)>1:issues.append({'severity':'error','code':'ambiguous_road_link','road_id':rid,'message':f'{rid} {end} branches without a common explicit junction; add connector roads in one junction'})
    # A conservative junction conflict group is shared by connector nodes and
    # approach nodes within 12m. It is an explicit road-authoring group, never a
    # hidden future actor trajectory. Parallel paths may yield conservatively.
    for road in definition.roads:
        if road.junction_id:
            for lane in road.lanes:
                for node in by_lane[(road.id,lane.id)]:node['conflict_ids']=['junction:'+road.junction_id]
    for c in definition.connections:
        target=roads[c.to_road]
        if target.junction_id and not roads[c.from_road].junction_id:
            approach=by_lane[(c.from_road,c.from_lane)]
            travel=approach if c.from_lane<0 else list(reversed(approach))
            for node in travel[-7:]:node['conflict_ids']=sorted(set(node['conflict_ids']+['junction:'+target.junction_id]))
    if definition.objects:issues.append({'severity':'error','code':'native_object_unsupported','message':'Generic OpenDRIVE objects are preserved in authoring but not instantiated by this CARLA adapter. Use scenario static actors with explicit asset references before building.'})
    issues.append({'severity':'warning','code':'procedural_environment','message':'Generated map contains basic roads, not the original Town buildings, terrain or photorealistic assets'})
    return {'schema_version':'object-map-preview/1','buildable':not any(i['severity']=='error' for i in issues),'read_only':False,'roads':list(samples.values()),'lanes':lanes,'issues':issues,'nodes':nodes,'road_ids':road_ids,'fidelity':'authored_road_geometry'}
