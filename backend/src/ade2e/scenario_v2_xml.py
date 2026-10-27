"""Authored OpenSCENARIO 1.2/OpenDRIVE subset, with lossless fail-closed import."""
import hashlib
import itertools
import json
from copy import deepcopy
from xml.etree import ElementTree as ET
from .scenario_v2_schemas import ScenarioV2

EXPORTER_VERSION = 'axiom-osc-subset/1'

def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()

def _node(parent, tag, **attrs):
    return ET.SubElement(parent,tag,{k:str(v) for k,v in attrs.items()})

def road_xml(scenario: ScenarioV2):
    root=ET.Element('OpenDRIVE')
    _node(root,'header',revMajor=1,revMinor=4,name=scenario.map.id,version=1,date='2026-10-07',north=0,south=0,east=0,west=0)
    road=_node(root,'road',name='AXIOM authored straight road',length=scenario.map.length_m,id=1,junction=-1)
    _node(road,'link')
    geometry=_node(_node(road,'planView'),'geometry',s=0,x=0,y=0,hdg=0,length=scenario.map.length_m)
    _node(geometry,'line')
    lanes=_node(road,'lanes'); section=_node(lanes,'laneSection',s=0)
    center=_node(_node(section,'center'),'lane',id=0,type='none',level='false'); _node(center,'link')
    right=_node(section,'right')
    for identifier in (-1,-2):
        lane=_node(right,'lane',id=identifier,type='driving',level='false');_node(lane,'link')
        _node(lane,'width',sOffset=0,a=scenario.map.lane_width_m,b=0,c=0,d=0)
        _node(lane,'roadMark',sOffset=0,type='broken' if identifier==-1 else 'solid',weight='standard',color='standard',width=.12,laneChange='both')
    return ET.tostring(root,encoding='unicode',xml_declaration=True)

def _position(parent,pose):
    _node(_node(parent,'Position'),'WorldPosition',x=pose.x,y=pose.y,z=0,h=pose.heading_rad,p=0,r=0)

def _trigger(parent,trigger,name):
    group=_node(parent,'ConditionGroup');condition=_node(group,'Condition',name=name,delay=0,conditionEdge='rising')
    if trigger.kind == 'time':
        _node(_node(condition,'ByValueCondition'),'SimulationTimeCondition',value=trigger.value,rule=trigger.rule)
    else:
        by=_node(condition,'ByEntityCondition');entities=_node(by,'TriggeringEntities',triggeringEntitiesRule='any')
        _node(entities,'EntityRef',entityRef=trigger.actor_id)
        ec=_node(by,'EntityCondition')
        if trigger.kind == 'distance':
            _node(ec,'RelativeDistanceCondition',entityRef=trigger.target_actor_id,relativeDistanceType='euclidianDistance',value=trigger.value,freespace='true',rule=trigger.rule)
        else: _node(ec,'SpeedCondition',value=trigger.value,rule=trigger.rule)

def _speed(parent,target,transition):
    speed=_node(_node(parent,'LongitudinalAction'),'SpeedAction')
    _node(speed,'SpeedActionDynamics',dynamicsShape='step' if transition == 0 else 'linear',value=transition,dynamicsDimension='time')
    _node(_node(speed,'SpeedActionTarget'),'AbsoluteTargetSpeed',value=target)

def scenario_xml(scenario: ScenarioV2):
    root=ET.Element('OpenSCENARIO')
    header=_node(root,'FileHeader',revMajor=1,revMinor=2,date='2026-10-07T00:00:00',description=scenario.name,author='AXIOM')
    props=_node(header,'Properties')
    _node(props,'Property',name='axiom_exporter',value=EXPORTER_VERSION)
    _node(props,'Property',name='axiom_scenario_v2',value=canonical_bytes(scenario.model_dump(mode='json')).decode())
    _node(root,'ParameterDeclarations');_node(root,'CatalogLocations')
    _node(_node(root,'RoadNetwork'),'LogicFile',filepath='road.xodr')
    entities=_node(root,'Entities')
    for actor in scenario.actors:
        obj=_node(entities,'ScenarioObject',name=actor.id)
        if actor.kind == 'vehicle':
            model=_node(obj,'Vehicle',name=actor.id,vehicleCategory='car')
        else: model=_node(obj,'Pedestrian',name=actor.id,model='none',mass=75,pedestrianCategory='pedestrian')
        box=_node(model,'BoundingBox');_node(box,'Center',x=0,y=0,z=actor.dimensions.height_m/2)
        _node(box,'Dimensions',width=actor.dimensions.width_m,length=actor.dimensions.length_m,height=actor.dimensions.height_m)
        if actor.kind == 'vehicle':
            _node(model,'Performance',maxSpeed=70,maxAcceleration=20,maxDeceleration=20)
            axles=_node(model,'Axles')
            _node(axles,'FrontAxle',maxSteering=.6,wheelDiameter=.6,trackWidth=1.6,positionX=1.4,positionZ=.3)
            _node(axles,'RearAxle',maxSteering=0,wheelDiameter=.6,trackWidth=1.6,positionX=-1.4,positionZ=.3)
        _node(model,'Properties')
    storyboard=_node(root,'Storyboard');actions=_node(_node(storyboard,'Init'),'Actions')
    for actor in scenario.actors:
        private=_node(actions,'Private',entityRef=actor.id)
        _position(_node(_node(private,'PrivateAction'),'TeleportAction'),actor.pose)
        _speed(_node(private,'PrivateAction'),actor.speed_mps,0)
        if actor.route:
            route=_node(_node(_node(private,'PrivateAction'),'RoutingAction'),'AssignRouteAction')
            route=_node(route,'Route',name=actor.id+'_route',closed='false');_node(route,'ParameterDeclarations')
            for pose in actor.route:
                _position(_node(route,'Waypoint',routeStrategy='shortest'),pose)
    story=_node(storyboard,'Story',name='behaviors');_node(story,'ParameterDeclarations')
    from .scenario_v2_schemas import Trigger
    for actor in scenario.actors:
        for index,action in enumerate(actor.actions):
            act=_node(story,'Act',name=f'{actor.id}_{index}')
            group=_node(act,'ManeuverGroup',name=f'{actor.id}_{index}_group',maximumExecutionCount=1)
            _node(_node(group,'Actors',selectTriggeringEntities='false'),'EntityRef',entityRef=actor.id)
            maneuver=_node(group,'Maneuver',name='maneuver');_node(maneuver,'ParameterDeclarations')
            event=_node(maneuver,'Event',name=f'{actor.id}_{index}_event',priority='overwrite',maximumExecutionCount=1)
            private=_node(_node(event,'Action',name=action.kind),'PrivateAction')
            if action.kind in ('speed','stop'):
                _speed(private,0 if action.kind=='stop' else action.target_speed_mps,action.transition_s)
            elif action.kind == 'lane_change':
                lane=_node(_node(private,'LateralAction'),'LaneChangeAction')
                _node(lane,'LaneChangeActionDynamics',dynamicsShape='sinusoidal',value=action.transition_s,dynamicsDimension='time')
                _node(_node(lane,'LaneChangeTarget'),'AbsoluteTargetLane',value=action.target_lane)
            else:
                follow=_node(_node(private,'RoutingAction'),'FollowTrajectoryAction')
                trajectory=_node(_node(follow,'TrajectoryRef'),'Trajectory',name=f'{actor.id}_path',closed='false')
                _node(trajectory,'ParameterDeclarations');polyline=_node(_node(trajectory,'Shape'),'Polyline')
                for point in action.path: _position(_node(polyline,'Vertex',time=point.time_s),point)
                _node(_node(follow,'TimeReference'),'Timing',domainAbsoluteRelative='absolute',scale=1,offset=0)
                _node(follow,'TrajectoryFollowingMode',followingMode='position')
            _trigger(_node(event,'StartTrigger'),action.trigger,'action_trigger')
            _trigger(_node(act,'StartTrigger'),Trigger(value=0),'act_start')
            _node(act,'StopTrigger')
    _trigger(_node(storyboard,'StopTrigger'),Trigger(value=scenario.duration_s),'duration')
    return ET.tostring(root,encoding='unicode',xml_declaration=True)

def import_xml(xml: str):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('DTD/entities are unsupported')
    root=ET.fromstring(xml)
    if root.tag != 'OpenSCENARIO': raise ValueError('Expected OpenSCENARIO document')
    values={p.attrib.get('name'):p.attrib.get('value') for p in root.findall('./FileHeader/Properties/Property')}
    if values.get('axiom_exporter') != EXPORTER_VERSION or 'axiom_scenario_v2' not in values:
        raise ValueError('Import supports only the AXIOM exported OpenSCENARIO subset; arbitrary external XML is unsupported')
    scenario=ScenarioV2.model_validate_json(values['axiom_scenario_v2'])
    # Compare all nodes and attributes: unknown or modified behavior cannot be silently discarded.
    def structure(node):
        if (node.text or '').strip() or (node.tail or '').strip(): raise ValueError('Unexpected XML text')
        return node.tag,sorted(node.attrib.items()),[structure(child) for child in node]
    if structure(root) != structure(ET.fromstring(scenario_xml(scenario))):
        raise ValueError('XML differs from its pinned supported manifest; unsupported or modified content rejected')
    return scenario

def sweep_variants(scenario: ScenarioV2):
    data=scenario.model_dump(mode='json');axes=scenario.sweep
    for values in itertools.product(*(axis.values for axis in axes)):
        variant=deepcopy(data);variant['sweep']=[];selection=[]
        for axis,value in zip(axes,values):
            actor=next(a for a in variant['actors'] if a['id']==axis.actor_id)
            path=axis.field.split('.')
            if len(path)==1: actor[path[0]]=value
            else: actor[path[0]][path[1]]=value
            selection.append({'actor_id':axis.actor_id,'field':axis.field,'value':value})
        parsed=ScenarioV2.model_validate(variant)
        yield {'scenario':parsed.model_dump(mode='json'),'configuration_hash':digest(parsed.model_dump(mode='json')),'selection':selection}
