"""Fail-closed OpenDRIVE import; unsupported source bytes remain available."""
import base64
import hashlib
import json
import math
import re
from xml.etree import ElementTree as ET
from .object_maps_schemas import ObjectMapDefinition
from .simulation_catalog_schemas import OpenDriveContent
from .object_maps_geometry import advance, preview_map


def _canonical_xml(content):
    return ET.canonicalize(content,strip_text=True)


def import_opendrive(content,name,rights):
    raw=content.encode();sha=hashlib.sha256(raw).hexdigest()
    if re.search(r'<!\s*(DOCTYPE|ENTITY)',content,re.I):raise ValueError('DTD/entity declarations are not allowed in OpenDRIVE imports')
    try:root=ET.fromstring(content)
    except ET.ParseError as error:raise ValueError(f'Invalid OpenDRIVE XML: {error}') from error
    if root.tag!='OpenDRIVE':raise ValueError('Expected an OpenDRIVE document root')
    original={'content':content,'sha256':sha};reasons=[]
    metadata=root.findall("userData[@code='axiom-object-map/1']")
    if metadata:
        try:
            if len(metadata)!=1:raise ValueError('Duplicate authored-source metadata')
            data=base64.b64decode(metadata[0].get('value',''),validate=True)
            if len(data)>2000000 or hashlib.sha256(data).hexdigest()!=metadata[0].get('sha256'):raise ValueError('Authored source metadata hash is invalid')
            document=ObjectMapDefinition.model_validate(json.loads(data))
            from axiom_opendrive import compile_opendrive
            regenerated=compile_opendrive(document.model_dump(mode='json'),preview_map(document))
            if _canonical_xml(regenerated)!=_canonical_xml(content):raise ValueError('OpenDRIVE differs from embedded authored source; manual edits require unsupported-source review')
            # Rights are supplied by the importing user, not inherited as proof.
            document.rights=rights;document.name=name;document.original_opendrive=OpenDriveContent.model_validate(original)
            return document
        except (ValueError,TypeError,KeyError,ImportError) as error:
            reasons.append(str(error))
    else:
        allowed={
            'OpenDRIVE':set(),'header':{'name','revMajor','revMinor','date','north','south','east','west','vendor'},
            'road':{'name','id','length','junction','rule'},'link':set(),'planView':set(),
            'geometry':{'s','x','y','hdg','length'},'line':set(),'arc':{'curvature'},'spiral':{'curvStart','curvEnd'},
            'elevationProfile':set(),'elevation':{'s','a','b','c','d'},'lateralProfile':set(),
            'lanes':set(),'laneSection':{'s'},'left':set(),'right':set(),'center':set(),
            'lane':{'id','type','level'},'width':{'sOffset','a','b','c','d'},
            'roadMark':{'sOffset','type','weight','color','width','laneChange'},'speed':{'sOffset','max','unit'},
        }
        children={'OpenDRIVE':{'header','road'},'road':{'link','planView','elevationProfile','lateralProfile','lanes'},'planView':{'geometry'},'geometry':{'line','arc','spiral'},'elevationProfile':{'elevation'},'lanes':{'laneSection'},'laneSection':{'left','center','right'},'left':{'lane'},'center':{'lane'},'right':{'lane'},'lane':{'link','width','roadMark','speed'}}
        for element in root.iter():
            if element.text and element.text.strip():reasons.append(f'Unsupported text content in {element.tag}')
            if any(child.tag not in children.get(element.tag,set()) for child in element):reasons.append(f'Unsupported nested structure in {element.tag}')
            if element.tag not in allowed:reasons.append(f'Unsupported OpenDRIVE element: {element.tag}')
            elif set(element.attrib)-allowed[element.tag]:reasons.append(f'Unsupported attributes on {element.tag}: {sorted(set(element.attrib)-allowed[element.tag])}')
        roads=[]
        if not reasons:
            try:
                for r in root.findall('road'):
                    if r.get('junction','-1')!='-1':raise ValueError('Generic junction import requires preservation; use authored connector editor')
                    if r.get('rule','RHT')!='RHT':raise ValueError('Only right-hand traffic is editable')
                    geoms=r.findall('./planView/geometry')
                    if not geoms:raise ValueError('Road has no reference geometry')
                    first=geoms[0];x=float(first.get('x'));y=-float(first.get('y'));h=-float(first.get('hdg'));initial=(x,y,h)
                    z=0.;elev=r.findall('./elevationProfile/elevation')
                    if elev:
                        if len(elev)!=1 or any(float(elev[0].get(k,0))!=0 for k in ('s','b','c','d')):raise ValueError('Variable elevation profiles are not editable')
                        z=float(elev[0].get('a'))
                    geometries=[];s=0.
                    for g in geoms:
                        if abs(float(g.get('s'))-s)>1e-5 or math.hypot(float(g.get('x'))-x,-float(g.get('y'))-y)>1e-4 or abs(math.atan2(math.sin(-float(g.get('hdg'))-h),math.cos(-float(g.get('hdg'))-h)))>1e-5:raise ValueError('Discontinuous reference geometry is not editable')
                        if len(g)!=1:raise ValueError('Geometry must have exactly one primitive')
                        primitive=g[0];length=float(g.get('length'));k0=k1=0.
                        if primitive.tag=='arc':k0=k1=-float(primitive.get('curvature'))
                        elif primitive.tag=='spiral':k0=-float(primitive.get('curvStart'));k1=-float(primitive.get('curvEnd'))
                        geometries.append(dict(kind=primitive.tag,length_m=length,curvature_start=k0,curvature_end=k1))
                        x,y,h=advance(x,y,h,length,k0,k1);s+=length
                    if abs(float(r.get('length'))-s)>1e-4:raise ValueError('Road length disagrees with reference geometry')
                    sections=r.findall('./lanes/laneSection')
                    if len(sections)!=1 or float(sections[0].get('s'))!=0:raise ValueError('Multiple lane sections are not editable')
                    lanes=[]
                    for lane in sections[0].findall('./*/lane'):
                        lid=int(lane.get('id'))
                        if lid==0:
                            if lane.get('type','none')!='none':raise ValueError('Center lane geometry is not editable')
                            continue
                        if lane.get('type')!='driving' or lane.get('level','false')!='false':raise ValueError('Only ordinary driving lanes are editable')
                        widths=lane.findall('width')
                        if len(widths)!=1 or any(float(widths[0].get(k,0))!=0 for k in ('sOffset','b','c','d')):raise ValueError('Variable lane width is not editable')
                        marks=lane.findall('roadMark')
                        if len(marks)>1 or (marks and (float(marks[0].get('sOffset',0))!=0 or marks[0].get('type') not in ('solid','broken'))):raise ValueError('Variable/custom road markings are not editable')
                        if marks and (marks[0].get('weight','standard')!='standard' or marks[0].get('color','standard')!='standard' or abs(float(marks[0].get('width',.15))-.15)>1e-8):raise ValueError('Custom road marking appearance is not editable')
                        permission=marks[0].get('laneChange','none') if marks else 'none'
                        if marks and ((permission=='none')!=(marks[0].get('type')=='solid')):raise ValueError('Independent road marking style and lane-change rule are not editable')
                        mode={'none':'none','both':'both','increase':'left' if lid<0 else 'right','decrease':'right' if lid<0 else 'left'}.get(permission)
                        if mode is None:raise ValueError('Unknown lane-change rule')
                        speed=lane.findall('speed')
                        if len(speed)>1 or (speed and (float(speed[0].get('sOffset',0))!=0 or speed[0].get('unit','m/s')!='m/s')):raise ValueError('Variable/non-metric speed limits are not editable')
                        lanes.append(dict(id=lid,width_m=float(widths[0].get('a')),lane_change=mode,speed_limit_mps=float(speed[0].get('max')) if speed else 12))
                    roads.append(dict(id='road-'+r.get('id'),name=r.get('name',''),origin=dict(x=initial[0],y=initial[1],z=z,heading_rad=initial[2]),geometries=geometries,lanes=lanes))
                if not roads:raise ValueError('No editable roads were found')
                return ObjectMapDefinition(name=name,rights=rights,roads=roads,original_opendrive=original)
            except (ValueError,TypeError,KeyError) as error:reasons.append(str(error))
    return ObjectMapDefinition(name=name,rights=rights,read_only=True,unsupported=list(dict.fromkeys(reasons))[:256],original_opendrive=original)
