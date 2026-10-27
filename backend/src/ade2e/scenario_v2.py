"""Immutable behavior authoring, lossless subset interchange and workload previews."""
import hashlib,json,uuid
from fastapi import Header,HTTPException,Query,Request,Response
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from .scenario_v2_models import ScenarioV2Row,ScenarioV2RevisionRow
from .scenario_v2_schemas import ScenarioV2Create,ScenarioV2Revise,ScenarioV2Import,SweepPreview,ScenarioV2,ScenarioCity,ScenarioYamlInput,parse_scenario
from .scenario_v2_yaml import parse_scenario_yaml,scenario_yaml,yaml_result
from .simulation_catalog import validate_city_catalogs,catalog_reference
from .scenario_v2_templates import templates
from .scenario_v2_xml import canonical_bytes,digest,scenario_xml,road_xml,import_xml,sweep_variants,EXPORTER_VERSION
from .team_access import project_authorize
from .city_motion import PHYSICAL,MIXED,REPLAY

def get_scenario(session,identity,revision=None):
    row=session.get(ScenarioV2Row,identity)
    if not row: raise HTTPException(404,'Scenario v2 not found')
    rev=session.get(ScenarioV2RevisionRow,(identity,revision or row.revision))
    if not rev: raise HTTPException(404,'Scenario v2 revision not found')
    if hashlib.sha256(rev.manifest).hexdigest()!=rev.manifest_hash:
        raise HTTPException(409,'Scenario v2 manifest integrity mismatch')
    manifest=json.loads(rev.manifest)
    if (manifest['id'] != row.id or manifest['project_id'] != row.project_id or manifest['revision'] != rev.revision):
        raise HTTPException(409,'Scenario v2 identity differs from its immutable manifest')
    parse_scenario(manifest['scenario'])
    return {**manifest,'manifest_hash':rev.manifest_hash,'current_revision':row.revision,
            'configuration_hash':digest(manifest['scenario'])}

def save_scenario(db,body,key,identity=None):
    request=body.model_dump(mode='json')
    if body.source_yaml is None:request.pop('source_yaml',None)
    else:
        try:source=parse_scenario_yaml(body.source_yaml)
        except ValueError as error:raise HTTPException(422,str(error)) from error
        if source.model_dump(mode='json')!=body.scenario.model_dump(mode='json'):
            raise HTTPException(422,'Original YAML differs from the submitted normalized scenario')
    request_hash=digest(request)
    storage_key='scenario_v2:'+key
    try:
        with db.session() as session:
            previous=session.scalar(select(ScenarioV2RevisionRow).where(ScenarioV2RevisionRow.request_key==storage_key))
            if previous:
                if previous.request_hash!=request_hash or (identity and previous.scenario_id!=identity):
                    raise HTTPException(409,'Idempotency key already has different scenario content')
                return get_scenario(session,previous.scenario_id,previous.revision)
            if identity:
                row=session.get(ScenarioV2Row,identity)
                if not row: raise HTTPException(404,'Scenario v2 not found')
                if row.revision!=body.expected_revision:raise HTTPException(409,'Scenario v2 revision changed; inspect before revising')
                revision=row.revision+1
                result=session.execute(update(ScenarioV2Row).where(ScenarioV2Row.id==identity,ScenarioV2Row.revision==body.expected_revision).values(revision=revision))
                if result.rowcount!=1:raise HTTPException(409,'Scenario v2 concurrent revision conflict')
                project_id=row.project_id
            else:
                identity='scenario2_'+uuid.uuid4().hex;revision=1;project_id=body.project_id
                session.add(ScenarioV2Row(id=identity,project_id=project_id,revision=revision));session.flush()
            validate_city_catalogs(session,project_id,body.scenario)
            manifest={'schema_version':'scenario-v2-revision/1','id':identity,'project_id':project_id,'revision':revision,
                'scenario':body.scenario.model_dump(mode='json'),'source_kind':'authored_object_kinematics','exporter_version':EXPORTER_VERSION}
            if isinstance(body.scenario,ScenarioCity):
                manifest.update(schema_version='scenario-v2-revision/2',source_kind='authored_native_city_scenario',
                                exporter_version='axiom-scenario-yaml/1')
            if body.source_yaml is not None:
                manifest['source_yaml']=body.source_yaml
                manifest['source_yaml_sha256']=hashlib.sha256(body.source_yaml.encode('utf-8')).hexdigest()
            raw=canonical_bytes(manifest)
            session.add(ScenarioV2RevisionRow(scenario_id=identity,revision=revision,request_key=storage_key,request_hash=request_hash,
                manifest=raw,manifest_hash=hashlib.sha256(raw).hexdigest()));session.flush()
            session.commit()
            return get_scenario(session,identity,revision)
    except IntegrityError as error:raise HTTPException(409,'Concurrent scenario request; retry with the same idempotency key') from error

def register_scenario_v2_routes(api,db):
    @api.get('/object-behaviors',tags=['Object Sim'],
             openapi_extra={'x-axiom-min-role':'Viewer','x-axiom-read-only':True})
    def object_behavior_catalog():
        from .object_behaviors import behavior_catalog
        return behavior_catalog()

    @api.get('/scenario-v2/capabilities',tags=['Object Sim'])
    def capabilities():
        return {'schema_version':'scenario-v2/3','supported_schema_versions':['scenario-v2/1','scenario-v2/2','scenario-v2/3'],
          'engines':{'esmini':'optional_configured_adapter_for_schema_1','carla':'optional_carla_city_object_adapter_for_schema_2'},
          'legacy_flat_fields_scope':{'schema_version':'scenario-v2/1','adapter_id':'esmini-object',
            'fields':['actions','triggers','fidelity','limits','limitations'],
            'note':'These flat fields retain the legacy contract. Use schema_profiles for schema-specific authoring and execution scope.'},
          'schema_profiles':{
            'scenario-v2/3':{
              'schema_version':'scenario-v2/3','adapter_id':'carla-city-object',
              'coordinate_system':'carla_world_m_x_forward_y_right_z_up',
              'map_kinds':['pinned_native_city_map','generated_opendrive'],
              'controller_modes':['reactive','route_follow','traffic','autoware','static','timed_replay'],
              'behavior_catalog_url':'/api/v1/object-behaviors','map_authoring_url':'/api/v1/object-maps',
              'limits':{'actors':32,'duration_s':120,'speed_mps':12,'timestep_s':.05,'planning_hz':10},
              'physical_sensor_validation':False,
              'limitations':['Ground-truth low-speed reactive planner; no V2X or learned human behavior.',
                'Procedural OpenDRIVE maps have basic roads, not a photorealistic city.',
                'Native preflight and complete execution evidence remain required.']},
            'scenario-v2/1':{
              'schema_version':'scenario-v2/1','adapter_id':'esmini-object',
              'coordinate_system':'world_m_x_forward_y_left_z_up','map_kinds':['authored_straight_road'],
              'actor_kinds':['vehicle','pedestrian'],
              'actions':['speed','stop','lane_change','follow_path'],'triggers':['time','distance','object_state:speed'],
              'controller_modes':[],'motion_scope':'authored actions and paths; no native vehicle feedback controller',
              'schema_bounds':{'actors':{'min':1,'max':8},'duration_s':{'min':1,'max':30},
                'timestep_s':{'min':.02,'max':.2},'actor_speed_mps':{'min':0,'max':60},
                'route_points_per_actor':{'max':32},'actions_per_actor':{'max':16},'observers':{'max':32}},
              'fidelities':['authored_object_kinematics'],'physical_sensor_validation':False,
              'interchange':{'yaml':True,'openscenario':'lossless AXIOM exported OpenSCENARIO 1.2 subset',
                'opendrive':'authored straight road'},
              'sweep':{'supported':True,'max_configurations':64,'max_jobs':128},
              'execution_preflight':{'required':True,'availability':'not_probed_by_this_endpoint',
                'capabilities_url':'/api/v1/experiments/capabilities','preview_url':'/api/v1/experiments/preview',
                'note':'Authoring validity does not establish adapter configuration, executable availability or successful execution.'},
              'limitations':['No optical or weather simulation','No road reconstruction','No standards-wide compliance',
                'No learned SUT controller in this adapter']},
            'scenario-v2/2':{
              'schema_version':'scenario-v2/2','adapter_id':'carla-city-object',
              'coordinate_system':'carla_world_m_x_forward_y_right_z_up','map_kinds':['pinned_native_city_map'],
              'actor_kinds':['vehicle','pedestrian','static'],
              'controller_modes':['timed_replay','route_follow','traffic','autoware','static'],
              'route_modes':['native_lane_graph','authored_lane_change'],
              'catalog_refs':'immutable map, asset and configured controller revisions with SHA-256',
              'schema_bounds':{'actors':{'min':1,'max':32},'duration_s':{'min':1,'max':120},
                'timestep_s':{'min':.02,'max':.1},'actor_speed_mps':{'min':0,'max':30},
                'route_points_per_actor':{'max':2000},'trajectory_points_per_actor':{'max':2400},
                'sensors':{'max':8},'signals':{'max':128},'signal_changes_per_signal':{'max':32},'observers':{'max':64}},
              'sensor_kinds':['camera_rgb'],'signal_states':['red','yellow','green'],
              'signal_clock':'command_index_times_timestep; last command at duration_s - timestep_s',
              'fidelities':[PHYSICAL,MIXED,REPLAY],
              'fidelity_selection':'Derived from frozen controller modes: physical without replay; mixed with replay and physical route controllers; otherwise timed position replay.',
              'physical_sensor_validation':False,
              'interchange':{'yaml':True,'openscenario':'unsupported; no lossless native-city exporter',
                'opendrive':'pinned native map original'},
              'sweep':{'supported':False,'alternative':'explicit immutable scenario revisions and normal experiment submissions'},
              'execution_preflight':{'required':True,'availability':'not_probed_by_this_endpoint',
                'capabilities_url':'/api/v1/experiments/capabilities','preview_url':'/api/v1/experiments/preview',
                'bounds':{'actor_speed_mps_max':12,'autoware_speed_mps_max':8,'ticks_max':2400},
                'constraints':['Configured optional native adapter and exclusive healthy endpoint are required.',
                  'Pinned map, asset, controller and runner versions must match the native server.',
                  'Native directed routes or the supported adjacent-lane corridor must pass route preflight.',
                  'Duration must contain integral timesteps and complete sensor evidence must fit the episode budget.',
                  'Legacy observers are not executed; use frozen city acceptance and experiment verification metrics.',
                  'Autoware requires its configured ground-truth bridge, mapped native town and supported vehicle/control settings.']},
              'limitations':['Native CARLA physics and rendered RGB are simulator evidence, not measured sensor or real-vehicle validation.',
                'Timed replay actor metrics and mixed replay contact absence cannot qualify physical motion or safety.',
                'Authored adjacent-lane paths and observed signal states do not establish a legal maneuver or stop-line verdict.',
                'Only the declared day/dry environment and bounded controller/sensor contracts are supported; schema validity is not execution readiness.']}},
          'actions':['speed','stop','lane_change','follow_path'],'triggers':['time','distance','object_state:speed'],
          'map_kinds':['authored_straight_road','pinned_native_city_map'],'import_scope':'YAML schema 1/2; XML only lossless AXIOM exported OpenSCENARIO 1.2 subset',
          'fidelity':'authored_object_kinematics','limits':{'actors':8,'duration_s':30,'sweep_configurations':64,'sweep_jobs':128},
          'limitations':['No optical or weather simulation','No road reconstruction','No standards-wide compliance','No learned SUT controller in this adapter']}

    @api.post('/scenario-v2/yaml/validate',tags=['Object Sim'],
              openapi_extra={'x-axiom-min-role':'Viewer','x-axiom-read-only':True},
              description='Read-only data-only YAML validation against scenario schema and exact project catalog revisions. Returns canonical YAML and semantic configuration hash; no scenario, simulation or evaluation is created.')
    def validate_yaml(body:ScenarioYamlInput,request:Request):
        project_authorize(request,db,body.project_id)
        try:scenario=parse_scenario_yaml(body.yaml)
        except ValueError as error:raise HTTPException(422,str(error)) from error
        with db.read_session() as session:validation=validate_city_catalogs(session,body.project_id,scenario)
        return {**yaml_result(scenario),'catalog_validation':validation}

    @api.post('/scenario-v2/yaml/import',status_code=201,tags=['Object Sim'],
              description='Create an immutable scenario revision from data-only YAML, preserving original text and a separate normalized configuration hash. Requires Engineer role and Idempotency-Key; does not execute.')
    def import_yaml(body:ScenarioYamlInput,request:Request,idempotency_key:str=Header(min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        try:scenario=parse_scenario_yaml(body.yaml)
        except ValueError as error:raise HTTPException(422,str(error)) from error
        return save_scenario(db,ScenarioV2Create(project_id=body.project_id,scenario=scenario,source_yaml=body.yaml),idempotency_key)

    @api.get('/scenario-v2/templates',tags=['Object Sim'])
    def template_listing():return {'items':templates()}

    @api.post('/scenario-v2/import',status_code=201,tags=['Object Sim'])
    def importing(body:ScenarioV2Import,request:Request,idempotency_key:str=Header(min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        try:scenario=import_xml(body.xml)
        except (ValueError,ETParseError) as error:raise HTTPException(422,str(error)) from error
        return save_scenario(db,ScenarioV2Create(project_id=body.project_id,scenario=scenario),idempotency_key)

    @api.post('/scenario-v2',status_code=201,tags=['Object Sim'])
    def create(body:ScenarioV2Create,request:Request,idempotency_key:str=Header(min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        return save_scenario(db,body,idempotency_key)

    @api.get('/scenario-v2',tags=['Object Sim'])
    def listing(request:Request,project_id:str='default',limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=100000)):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            rows=list(session.scalars(select(ScenarioV2Row).where(ScenarioV2Row.project_id==project_id).order_by(ScenarioV2Row.id).limit(limit+1).offset(offset)))
            return {'items':[get_scenario(session,r.id) for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None}

    def authorized(request,identity,revision=None,role='Viewer'):
        with db.read_session() as session:result=get_scenario(session,identity,revision)
        project_authorize(request,db,result['project_id'],role)
        return result

    @api.get('/scenario-v2/{identity}',tags=['Object Sim'])
    def read(identity:str,request:Request,revision:int|None=Query(None,ge=1)):
        return authorized(request,identity,revision)

    @api.post('/scenario-v2/{identity}/revisions',status_code=201,tags=['Object Sim'])
    def revise(identity:str,body:ScenarioV2Revise,request:Request,idempotency_key:str=Header(min_length=1,max_length=180)):
        authorized(request,identity,role='Engineer');return save_scenario(db,body,idempotency_key,identity)

    @api.get('/scenario-v2/{identity}/export',tags=['Object Sim'])
    def exporting(identity:str,request:Request,revision:int|None=Query(None,ge=1),format:str=Query('openscenario',pattern='^(openscenario|opendrive|manifest|yaml|source_yaml)$')):
        result=authorized(request,identity,revision);scenario=parse_scenario(result['scenario'])
        if format=='manifest':return result
        if format in {'yaml','source_yaml'}:
            if format=='source_yaml' and 'source_yaml' not in result:
                raise HTTPException(404,'This revision was authored without original YAML')
            value=result['source_yaml'] if format=='source_yaml' else scenario_yaml(scenario)
            return Response(value,media_type='application/yaml',headers={'Content-Disposition':f'attachment; filename="{identity}.yaml"',
                'X-Content-SHA256':hashlib.sha256(value.encode('utf-8')).hexdigest()})
        if isinstance(scenario,ScenarioCity):
            if format=='openscenario':raise HTTPException(422,'Native city schema has no lossless OpenSCENARIO exporter; export YAML instead')
            with db.read_session() as session:
                value=catalog_reference(session,'map',result['project_id'],scenario.map)['definition']['opendrive']['content']
            return Response(value,media_type='application/xml',headers={'X-Content-SHA256':hashlib.sha256(value.encode()).hexdigest()})
        value=scenario_xml(scenario) if format=='openscenario' else road_xml(scenario)
        return Response(value,media_type='application/xml',headers={'Content-Disposition':f'attachment; filename="{identity}.{"xosc" if format=="openscenario" else "xodr"}"','X-Content-SHA256':hashlib.sha256(value.encode()).hexdigest()})

    @api.post('/scenario-v2/{identity}/sweep-preview',tags=['Object Sim'])
    def preview(identity:str,body:SweepPreview,request:Request,revision:int|None=Query(None,ge=1)):
        result=authorized(request,identity,revision);scenario=parse_scenario(result['scenario'])
        if isinstance(scenario,ScenarioCity):raise HTTPException(422,'City scenarios use explicit frozen revisions; legacy kinematic sweep is unsupported')
        variants=list(sweep_variants(scenario))
        if len(variants)*len(body.seeds)>128:raise HTTPException(422,'Sweep exceeds 128 jobs; no jobs submitted')
        jobs=[{'configuration_hash':v['configuration_hash'],'selection':v['selection'],'seed':seed,'scenario':v['scenario']} for v in variants for seed in body.seeds]
        data={'scenario_id':identity,'scenario_revision':result['revision'],'scenario_manifest_hash':result['manifest_hash'],'jobs':jobs,'job_count':len(jobs),
              'configured_seconds':sum(j['scenario']['duration_s'] for j in jobs),'submitted_jobs':0,'seed_note':'Authored deterministic behaviors do not become independent stochastic scenes by changing seed'}
        return {**data,'preview_hash':digest(data)}

from xml.etree.ElementTree import ParseError as ETParseError
