"""Versioned map authoring API; compilation is bounded CPU work, not world creation."""
import hashlib
import importlib.util
import json
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
from fastapi import HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from .object_maps_schemas import ObjectMapBuild, ObjectMapCreate, ObjectMapDefinition, ObjectMapImport, ObjectMapPreview
from .object_maps_geometry import pose_at, preview_map
from .object_maps_import import import_opendrive
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .simulation_catalog import register_catalog
from .simulation_catalog_schemas import MapDefinition
from .scenario_v2_xml import canonical_bytes
from .team_access import project_authorize

KIND='object-map-source'


def reference(value):return {key:value[key] for key in ('id','revision','sha256')}


def source_get(session,project_id,identity,revision=None):
    if revision is None:revision=session.scalar(select(func.max(Row.revision)).where(Row.project_id==project_id,Row.kind==KIND,Row.id==identity))
    row=session.get(Row,(project_id,KIND,identity,revision)) if revision else None
    if row is None:raise HTTPException(404,'Object map source revision not found')
    if hashlib.sha256(row.manifest).hexdigest()!=row.manifest_hash:raise HTTPException(409,'Object map source integrity mismatch')
    value=json.loads(row.manifest)
    if (value['project_id'],value['id'],value['revision'],value['kind'])!=(project_id,identity,revision,KIND):raise HTTPException(409,'Object map source identity mismatch')
    ObjectMapDefinition.model_validate(value['definition'])
    return {**value,'sha256':row.manifest_hash}


def source_save(db,body):
    if not isinstance(body,ObjectMapCreate):body=ObjectMapCreate.model_validate(body)
    definition=body.definition.model_dump(mode='json')
    # Validate complexity before persistence, while preserving editable invalid
    # connectivity as a draft with explicit preview errors.
    preview_map(body.definition)
    try:
        with db.session() as session:
            current=session.scalar(select(func.max(Row.revision)).where(Row.project_id==body.project_id,Row.kind==KIND,Row.id==body.id)) or 0
            if current:
                existing=source_get(session,body.project_id,body.id,current)
                if existing['definition']==definition and body.expected_revision in (current,current-1):return existing
            if current!=body.expected_revision:raise HTTPException(409,'Object map source revision changed; reload before saving')
            manifest={'schema_version':'object-map-source-revision/1','project_id':body.project_id,'kind':KIND,'id':body.id,'revision':current+1,'definition':definition}
            raw=canonical_bytes(manifest)
            session.add(Row(project_id=body.project_id,kind=KIND,id=body.id,revision=current+1,manifest=raw,manifest_hash=hashlib.sha256(raw).hexdigest()));session.flush()
            result=source_get(session,body.project_id,body.id,current+1);session.commit();return result
    except IntegrityError as error:raise HTTPException(409,'Concurrent map source revision; reload before saving') from error


def compiler_info():
    try:installed=version('scenariogeneration')
    except PackageNotFoundError:installed=None
    available=importlib.util.find_spec('axiom_opendrive') is not None and installed=='0.16.7'
    return {'id':'axiom.pyoscx.opendrive','version':'1.0.0+scenariogeneration.0.16.7','available':available,'dependency':'scenariogeneration==0.16.7','dependency_license':'MPL-2.0','installation':'pip install -e integrations/opendrive','fidelity':'compiled_opendrive_not_executed'}


def compile_source(source,server_version):
    definition=ObjectMapDefinition.model_validate(source['definition']);geometry=preview_map(definition)
    if not geometry['buildable']:raise HTTPException(422,{'message':'Map source is not buildable','issues':geometry['issues']})
    if not compiler_info()['available']:raise HTTPException(503,{'message':'Optional OpenDRIVE compiler is unavailable or not pinned','compiler':compiler_info()})
    import axiom_opendrive
    from . import object_maps_geometry
    from axiom_opendrive import COMPILER_ID, COMPILER_VERSION, compile_opendrive
    compiler_sha256=hashlib.sha256(Path(axiom_opendrive.__file__).read_bytes()).hexdigest()
    geometry_sha256=hashlib.sha256(Path(object_maps_geometry.__file__).read_bytes()).hexdigest()
    if compiler_sha256!=axiom_opendrive.LOADED_SOURCE_SHA256 or geometry_sha256!=object_maps_geometry.LOADED_SOURCE_SHA256:
        raise HTTPException(503,'Map compiler source changed while the API was running; restart this isolated API before building')
    try:xml=compile_opendrive(definition.model_dump(mode='json'),geometry)
    except ValueError as error:raise HTTPException(422,str(error)) from error
    opendrive_hash=hashlib.sha256(xml.encode()).hexdigest()
    build_manifest={'source':reference(source),'compiler_id':COMPILER_ID,'compiler_version':COMPILER_VERSION,'opendrive_sha256':opendrive_hash,'server_version':server_version,'road_ids':geometry['road_ids'],'compiler_sha256':compiler_sha256,'geometry_sha256':geometry_sha256}
    build_hash=hashlib.sha256(canonical_bytes(build_manifest)).hexdigest()
    roads={r.id:r for r in definition.roads};signals=[]
    for index,s in enumerate(definition.signals):
        p=pose_at(roads[s.road_id],s.s_m,s.t_m)
        native_road=geometry['road_ids'][s.road_id]
        applicable=s.lane_ids or [l.id for l in roads[s.road_id].lanes if (l.id<0)==(s.orientation=='+')]
        # Sampled lane nodes nearest to the stop line for each affected lane.
        at=pose_at(roads[s.road_id],s.s_m)
        node_ids=[]
        for lane_id in applicable:
            candidates=[n for n in geometry['nodes'] if n['road_id']==native_road and n['lane_id']==lane_id]
            if candidates:node_ids.append(min(candidates,key=lambda n:(n['x']-at['x'])**2+(n['y']-at['y'])**2)['id'])
        signals.append({'id':str(index+1),'pose':p,'kind':'other' if s.kind=='yield' else s.kind,'regulatory_kind':s.kind,'node_ids':node_ids})
    spawn=[]
    for lane in geometry['lanes']:
        if not lane['closed'] and lane['points']:spawn.append(lane['points'][min(2,len(lane['points'])-1)])
    value={'name':definition.name,'native_name':'generated__'+build_hash[:16],'server_version':server_version,'opendrive':{'content':xml,'sha256':opendrive_hash},'nodes':geometry['nodes'],'signals':signals,'spawn_points':spawn[:5000],'rights':definition.rights.model_dump(mode='json'),'generated':{'source':reference(source),'compiler_id':COMPILER_ID,'compiler_version':COMPILER_VERSION,'compiler_sha256':compiler_sha256,'source_schema':'object-map/1','build_hash':build_hash,'road_ids':geometry['road_ids'],'environment':'procedural_roads_only'}}
    return MapDefinition.model_validate(value),geometry,build_manifest


def register_object_map_routes(api,db):
    @api.get('/object-maps/capabilities',tags=['Object map authoring'],description='Bounded editable road and OpenDRIVE compiler support. Availability is local compilation only, not a simulator health probe.')
    def capabilities(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        return {'schema_version':'object-map-capabilities/1','source_schema':'object-map/1','compiler':compiler_info(),'geometry':['line','arc','spiral'],'road_model':'constant-width lanes with explicit connector roads','limits':{'roads':128,'lanes_per_road':12,'sampled_lane_nodes':50000,'speed_limit_mps_max':12},'environment':'procedural_roads_only','unsupported':['variable width/elevation import','arbitrary OpenDRIVE roundtrip editing','generic native collision objects','photorealistic surroundings']}

    @api.get('/object-maps',tags=['Object map authoring'])
    def listing(request:Request,project_id:str='default',limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0)):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            selected=list(session.execute(select(Row.id,func.max(Row.revision)).where(Row.project_id==project_id,Row.kind==KIND).group_by(Row.id).order_by(Row.id).limit(limit+1).offset(offset)))
            items=[source_get(session,project_id,identity,revision) for identity,revision in selected[:limit]]
        return {'items':items,'next_offset':offset+limit if len(selected)>limit else None}

    @api.post('/object-maps',status_code=201,tags=['Object map authoring'],openapi_extra={'x-axiom-min-role':'Engineer'},description='Append an immutable editable map source revision. Saving is distinct from compilation and native execution.')
    def save(body:ObjectMapCreate,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        try:return source_save(db,body)
        except ValueError as error:raise HTTPException(422,str(error)) from error

    @api.post('/object-maps/preview',tags=['Object map authoring'],openapi_extra={'x-axiom-min-role':'Viewer','x-axiom-read-only':True},description='Read-only sampled 2D/3D road geometry and build diagnostics; no world or map catalog is modified.')
    def preview(body:ObjectMapPreview,request:Request):
        project_authorize(request,db,body.project_id)
        try:return preview_map(body.definition)
        except ValueError as error:raise HTTPException(422,str(error)) from error

    @api.post('/object-maps/import',tags=['Object map authoring'],openapi_extra={'x-axiom-min-role':'Viewer','x-axiom-read-only':True},description='Read-only OpenDRIVE parse. Unsupported elements preserve the original document as read-only; this does not save or execute it.')
    def importing(body:ObjectMapImport,request:Request):
        project_authorize(request,db,body.project_id)
        try:
            value=import_opendrive(body.content,body.name,body.rights)
            return {'definition':value.model_dump(mode='json'),'preview':preview_map(value),'source_sha256':hashlib.sha256(body.content.encode()).hexdigest(),'editable':not value.read_only}
        except ValueError as error:raise HTTPException(422,str(error)) from error

    @api.get('/object-maps/{identity}',tags=['Object map authoring'])
    def read(identity:str,request:Request,project_id:str='default',revision:int|None=Query(None,ge=1)):
        project_authorize(request,db,project_id)
        with db.read_session() as session:result=source_get(session,project_id,identity,revision)
        return {**result,'preview':preview_map(result['definition'])}

    @api.post('/object-maps/{identity}/build',status_code=201,tags=['Object map authoring'],openapi_extra={'x-axiom-min-role':'Engineer'},description='Compile a pinned source revision into an immutable /maps catalog revision. Bounded deterministic CPU work; no CARLA world is created. Native world generation uses the existing simulation queue.')
    def build(identity:str,body:ObjectMapBuild,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        with db.read_session() as session:source=source_get(session,body.project_id,identity,body.revision)
        if source['sha256']!=body.sha256:raise HTTPException(409,'Source hash differs from pinned map revision')
        definition,geometry,manifest=compile_source(source,body.server_version)
        catalog_id='authored-'+identity[:36]+'-'+hashlib.sha256(identity.encode()).hexdigest()[:8]
        result=register_catalog(db,'map',{'id':catalog_id,'project_id':body.project_id,'expected_revision':body.expected_map_revision,'definition':definition.model_dump(mode='json')})
        return {'source':reference(source),'map':result,'map_ref':reference(result),'preview':geometry,'compiler':compiler_info(),'build_manifest':manifest,'execution_status':'not_executed'}

    @api.get('/object-maps/{identity}/export',tags=['Object map authoring'],description='Export exact original XML for a preserved import, or compile the authored source to OpenDRIVE. Compilation/export is not native simulation.')
    def exporting(identity:str,request:Request,project_id:str='default',revision:int|None=Query(None,ge=1),format:str=Query('opendrive',pattern='^(opendrive|source_opendrive|json)$')):
        project_authorize(request,db,project_id)
        with db.read_session() as session:source=source_get(session,project_id,identity,revision)
        if format=='json':raw=canonical_bytes(source['definition']);media='application/json'
        elif format=='source_opendrive' or source['definition']['read_only']:
            original=source['definition']['original_opendrive']
            if not original:raise HTTPException(404,'This map has no imported original OpenDRIVE')
            raw=original['content'].encode();media='application/xml'
        else:
            value,_,_=compile_source(source,'export-only');raw=value.opendrive.content.encode();media='application/xml'
        return Response(raw,media_type=media,headers={'X-Content-SHA256':hashlib.sha256(raw).hexdigest()})
