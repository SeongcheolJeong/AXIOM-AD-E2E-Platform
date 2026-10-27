"""Bounded authored roads compiled by scenariogeneration; no simulator imports."""
import base64
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree as ET

COMPILER_ID='axiom.pyoscx.opendrive'
COMPILER_VERSION='1.0.0+scenariogeneration.0.16.7'
DEPENDENCY_VERSION='0.16.7'
LOADED_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def compile_opendrive(document,geometry):
    if version('scenariogeneration')!=DEPENDENCY_VERSION:
        raise ValueError('OpenDRIVE compiler requires the pinned scenariogeneration==0.16.7')
    from scenariogeneration import xodr
    # Upstream signal IDs are process-global by default. A per-compilation
    # subclass owns its ID registry so parallel/repeated builds are deterministic.
    class BuildSignal(xodr.Signal):
        _usedIDs = {}
        _IDCounter = {}
    source=json.loads(json.dumps(document));source.pop('original_opendrive',None)
    source['read_only']=False;source['unsupported']=[]
    odr=xodr.OpenDrive(source['name'],revMajor='1',revMinor='6')
    road_ids=geometry['road_ids'];junction_ids={j['id']:i+1 for i,j in enumerate(source['junctions'])}
    sample_roads={r['id']:r for r in geometry['roads']};road_sources={r['id']:r for r in source['roads']}
    road_objects={};lane_objects={}
    for road in source['roads']:
        plan=xodr.PlanView()
        for g,start in zip(road['geometries'],sample_roads[road['id']]['geometry_starts']):
            if g['kind']=='line':geom=xodr.Line(g['length_m'])
            elif g['kind']=='arc':geom=xodr.Arc(-g['curvature_start'],length=g['length_m'])
            else:geom=xodr.Spiral(-g['curvature_start'],-g['curvature_end'],length=g['length_m'])
            plan.add_fixed_geometry(geom,start['x'],-start['y'],-start['heading_rad'],s=start['s_m'])
        center=xodr.Lane(xodr.LaneType.none)
        center.add_roadmark(xodr.RoadMark(xodr.RoadMarkType.solid,width=.15,laneChange=xodr.LaneChange.none))
        section=xodr.LaneSection(0,center)
        for sign in (1,-1):
            for spec in sorted((l for l in road['lanes'] if l['id']*sign>0),key=lambda l:abs(l['id'])):
                lane=xodr.Lane(xodr.LaneType.driving,a=spec['width_m'])
                permission={'none':'none','both':'both','left':'increase' if spec['id']<0 else 'decrease','right':'decrease' if spec['id']<0 else 'increase'}[spec['lane_change']]
                lane.add_roadmark(xodr.RoadMark(xodr.RoadMarkType.broken if permission!='none' else xodr.RoadMarkType.solid,width=.15,laneChange=getattr(xodr.LaneChange,permission)))
                (section.add_left_lane if sign>0 else section.add_right_lane)(lane);lane_objects[(road['id'],spec['id'])]=lane
        lanes=xodr.Lanes();lanes.add_lanesection(section)
        native=xodr.Road(road_ids[road['id']],plan,lanes,road_type=junction_ids[road['junction_id']] if road['junction_id'] else -1,name=road['name'] or road['id'])
        native.add_elevation(0,road['origin']['z'],0,0,0)
        road_objects[road['id']]=native;odr.add_road(native)
    road_links={};junction_connections={}
    for c in source['connections']:
        for rid,lid,target,tid,incoming in ((c['from_road'],c['from_lane'],c['to_road'],c['to_lane'],False),(c['to_road'],c['to_lane'],c['from_road'],c['from_lane'],True)):
            end=('predecessor' if lid<0 else 'successor') if incoming else ('successor' if lid<0 else 'predecessor')
            target_junction=road_sources[target]['junction_id'] if road_sources[rid]['junction_id']!=road_sources[target]['junction_id'] else None
            contact='start' if (tid<0)!=incoming else 'end'
            link=('junction',junction_ids[target_junction],None) if target_junction else ('road',road_ids[target],contact)
            key=(rid,end)
            if key in road_links and road_links[key]!=link:raise ValueError('Ambiguous road end link; use explicit junction connector roads')
            road_links[key]=link
            # Each source driving lane may have one predecessor/successor outside
            # a junction; junction branching is represented by laneLinks below.
            if not target_junction:
                lane=lane_objects[(rid,lid)]
                existing=[v for v in lane.links.links if v.link_type==end]
                if existing and str(existing[0].element_id)!=str(tid):raise ValueError('Ambiguous lane link outside junction')
                if not existing:lane.add_link(end,tid)
        a=road_sources[c['from_road']];b=road_sources[c['to_road']]
        if not a['junction_id'] and b['junction_id']:
            key=(b['junction_id'],c['from_road'],c['to_road'],'start' if c['to_lane']<0 else 'end')
            junction_connections.setdefault(key,[]).append((c['from_lane'],c['to_lane']))
    for (rid,end),(kind,identity,contact) in road_links.items():
        method=getattr(road_objects[rid],'add_'+end)
        method(getattr(xodr.ElementType,kind),identity,getattr(xodr.ContactPoint,contact) if contact else None)
    for junction in source['junctions']:
        obj=xodr.Junction(junction['name'] or junction['id'],junction_ids[junction['id']])
        for index,((jid,origin,target,contact),links) in enumerate(junction_connections.items()):
            if jid!=junction['id']:continue
            connection=xodr.Connection(road_ids[origin],road_ids[target],getattr(xodr.ContactPoint,contact),id=index)
            for a,b in links:connection.add_lanelink(a,b)
            obj.add_connection(connection)
        odr.add_junction(obj)
    for index,spec in enumerate(source['signals']):
        signal=BuildSignal(spec['s_m'],-spec['t_m'],'OpenDRIVE',{'traffic_light':'1000001','stop':'206','yield':'205'}[spec['kind']],id=str(index+1),name=spec['id'],dynamic=xodr.Dynamic.yes if spec['kind']=='traffic_light' else xodr.Dynamic.no,orientation=xodr.Orientation.positive if spec['orientation']=='+' else xodr.Orientation.negative)
        if spec['lane_ids']:signal.add_validity(spec['lane_ids'][0],spec['lane_ids'][0])
        road_objects[spec['road_id']].add_signal(signal)
    root=odr.get_element()
    root.find('header').set('date','1970-01-01T00:00:00Z')
    for index,spec in enumerate(source['signals']):
        element=next(v for v in root.findall('./road/signals/signal') if v.get('id')==str(index+1))
        for lane_id in spec['lane_ids'][1:]:ET.SubElement(element,'validity',fromLane=str(lane_id),toLane=str(lane_id))
    for road in source['roads']:
        element=next(r for r in root.findall('road') if r.get('id')==str(road_ids[road['id']]))
        for lane in road['lanes']:
            native_lane=next(l for l in element.findall('./lanes/laneSection/*/lane') if l.get('id')==str(lane['id']))
            ET.SubElement(native_lane,'speed',sOffset='0',max=str(lane['speed_limit_mps']),unit='m/s')
    # Lossless roundtrip metadata is verified against recompilation on import;
    # it is never trusted over modified XML geometry.
    raw=json.dumps(source,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    ET.SubElement(root,'userData',code='axiom-object-map/1',value=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
    ET.indent(root,space='  ')
    # CARLA 0.9.16's string roundtrip can corrupt non-ASCII literal bytes in
    # header/road names. XML numeric references preserve Unicode semantics while
    # keeping the frozen transport bytes ASCII and exactly hashable.
    return ET.tostring(root,encoding='unicode').encode('ascii','xmlcharrefreplace').decode('ascii')
