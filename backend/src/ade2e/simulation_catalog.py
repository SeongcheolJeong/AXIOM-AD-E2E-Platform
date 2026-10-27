"""Project-scoped immutable native catalogs and directed route preflight."""
from collections import defaultdict
import hashlib
import heapq
import json
import math
from fastapi import HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .simulation_catalog_schemas import AssetDefinition, CatalogCreate, ControllerDefinition, MapDefinition, RouteRequest
from .scenario_v2_schemas import ScenarioCity
from .scenario_v2_xml import canonical_bytes, digest
from .team_access import project_authorize

DEFINITIONS = {'map': MapDefinition, 'asset': AssetDefinition, 'controller': ControllerDefinition}


def get_catalog(session, kind, project_id, identity, revision=None):
    if kind not in DEFINITIONS:
        raise ValueError('Unknown simulation catalog kind')
    if revision is None:
        revision = session.scalar(select(func.max(Row.revision)).where(
            Row.project_id == project_id, Row.kind == kind, Row.id == identity))
    row = session.get(Row, (project_id,kind,identity,revision)) if revision else None
    if row is None:
        raise HTTPException(404, f'{kind.title()} catalog revision not found')
    if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
        raise HTTPException(409, 'Simulation catalog manifest integrity mismatch')
    result = json.loads(row.manifest)
    if (result['id'],result['project_id'],result['revision'],result['kind']) != (identity,project_id,revision,kind):
        raise HTTPException(409, 'Simulation catalog identity differs from its immutable manifest')
    DEFINITIONS[kind].model_validate(result['definition'])
    return {**result,'sha256':row.manifest_hash}


def register_catalog(db, kind, body):
    """Import checked native metadata. Callers must hold project write authority."""
    if not isinstance(body,CatalogCreate):
        body = CatalogCreate.model_validate(body)
    if kind not in DEFINITIONS:
        raise ValueError('Unknown simulation catalog kind')
    definition = DEFINITIONS[kind].model_validate(body.definition.model_dump(mode='json')).model_dump(mode='json')
    try:
        with db.session() as session:
            current = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id,Row.kind == kind,Row.id == body.id)) or 0
            # Content-identical retries are safe and do not create another revision.
            if current:
                existing = get_catalog(session,kind,body.project_id,body.id,current)
                if existing['definition'] == definition and body.expected_revision in {current,current-1}:
                    return existing
            if current != body.expected_revision:
                raise HTTPException(409,'Catalog revision changed; inspect before revising')
            manifest = {'schema_version':'simulation-catalog-revision/1','project_id':body.project_id,
                        'kind':kind,'id':body.id,'revision':current+1,'definition':definition}
            raw = canonical_bytes(manifest)
            session.add(Row(project_id=body.project_id,kind=kind,id=body.id,revision=current+1,
                            manifest=raw,manifest_hash=hashlib.sha256(raw).hexdigest()))
            session.flush()
            result = get_catalog(session,kind,body.project_id,body.id,current+1)
            session.commit()
            return result
    except IntegrityError as error:
        raise HTTPException(409,'Concurrent catalog revision; reload and retry') from error


def catalog_reference(session, kind, project_id, ref):
    value = ref.model_dump() if hasattr(ref,'model_dump') else ref
    result = get_catalog(session,kind,project_id,value['id'],value['revision'])
    if result['sha256'] != value['sha256']:
        raise HTTPException(409,f'{kind.title()} reference SHA-256 differs from pinned catalog revision')
    return result


def validate_city_catalogs(session, project_id, scenario):
    if not isinstance(scenario,ScenarioCity):
        return {'status':'not_applicable','references':[],'warnings':[]}
    native_map = catalog_reference(session,'map',project_id,scenario.map)
    references = [{'kind':'map',**scenario.map.model_dump()}]
    warnings = []
    signal_kinds={s['id']:s['kind'] for s in native_map['definition']['signals']}
    if any(signal_kinds.get(s.id)!='traffic_light' for s in scenario.signals):
        raise HTTPException(422,'Signal schedule references an unknown or non-traffic-light map signal')
    for actor in scenario.actors:
        asset = catalog_reference(session,'asset',project_id,actor.asset)
        if asset['definition']['actor_kind'] != actor.kind:
            raise HTTPException(422,f'Actor {actor.id} kind differs from the pinned asset')
        references.append({'kind':'asset',**actor.asset.model_dump()})
        if actor.controller.ref:
            controller = catalog_reference(session,'controller',project_id,actor.controller.ref)
            if controller['definition']['mode'] != actor.controller.kind:
                raise HTTPException(422,f'Actor {actor.id} controller mode differs from its catalog revision')
            references.append({'kind':'controller',**actor.controller.ref.model_dump()})
            if controller['definition']['status'] != 'available':
                warnings.append(f'Actor {actor.id}: controller is not configured; authoring is allowed, execution must fail closed')
        if asset['definition']['dimensions'] is None:
            warnings.append(f'Actor {actor.id}: asset bounds have not been measured; native spawned bounds must be recorded')
    if native_map['definition']['rights']['redistribution'] != 'permitted':
        warnings.append('Map redistribution rights are not confirmed by this catalog')
    return {'status':'validated','references':references,'warnings':warnings}


def _distance(a,b):
    return math.sqrt(sum((a[k]-b[k])**2 for k in ('x','y','z')))


def plan_route(definition, body):
    """Snap against directed native nodes, then preserve that graph's topology."""
    if not isinstance(body,RouteRequest):
        body = RouteRequest.model_validate(body)
    nodes = {n['id']:n for n in definition['nodes']}
    snaps = []
    for index,pose in enumerate([body.start,*body.via,body.goal]):
        point = pose.model_dump()
        ranked = sorted(((_distance(point,node),identity) for identity,node in nodes.items()))
        nearest_distance,nearest_id = ranked[0]
        if nearest_distance > body.max_snap_distance_m:
            raise HTTPException(422,f'Route point {index} is outside the lane snap tolerance ({nearest_distance:.2f} m)')
        # Heading is explicit: do not silently choose an opposing lane to make
        # an otherwise disconnected route appear reachable.
        candidates = [(distance,identity) for distance,identity in ranked
                      if distance <= body.max_snap_distance_m and
                      abs(math.atan2(math.sin(point['heading_rad']-nodes[identity]['heading_rad']),
                                     math.cos(point['heading_rad']-nodes[identity]['heading_rad']))) <= math.pi/2]
        if not candidates:
            raise HTTPException(422,f'Route point {index} faces against the native lane direction')
        distance,identity = candidates[0]
        snaps.append({'input':point,'node_id':identity,'distance_m':distance,
                      'pose':{k:nodes[identity][k] for k in ('x','y','z','heading_rad')}})
    full = []
    total = 0.
    for start,end in zip(snaps,snaps[1:]):
        origin,target = start['node_id'],end['node_id']
        costs = {origin:0.};previous = {};queue = [(0.,origin)]
        while queue:
            cost,identity = heapq.heappop(queue)
            if cost != costs[identity]:
                continue
            if identity == target:
                break
            for successor in nodes[identity]['next_ids']:
                candidate = cost + _distance(nodes[identity],nodes[successor])
                if candidate < costs.get(successor,math.inf):
                    costs[successor]=candidate;previous[successor]=identity
                    heapq.heappush(queue,(candidate,successor))
        if target not in costs:
            raise HTTPException(422,'Route is unreachable in the pinned directed lane graph')
        segment = [target]
        while segment[-1] != origin:
            segment.append(previous[segment[-1]])
        segment.reverse()
        full.extend(segment if not full else segment[1:]);total += costs[target]
    if len(full) < 2:
        raise HTTPException(422,'Start and goal resolve to the same lane node; choose a distinct goal')
    if len(full) > 2000:
        raise HTTPException(422,'Route exceeds the 2000 waypoint contract; select a shorter route')
    result = {'schema_version':'native-lane-route/1','map_revision':body.revision,'map_sha256':body.sha256,
              'node_ids':full,'route':[{k:nodes[i][k] for k in ('x','y','z','heading_rad')} for i in full],
              'distance_m':total,'snaps':snaps,'fidelity':'registered_lane_graph_routing'}
    return {**result,'route_hash':digest(result)}


def map_lanes(definition):
    """Render separate native edge chains, never join roads by proximity."""
    groups = defaultdict(list)
    for node in definition['nodes']:
        groups[(node['road_id'],node['section_id'],node['lane_id'])].append(node)
    lanes = []
    for key,group in groups.items():
        nodes = {node['id']:node for node in group}
        incoming = {target for node in group for target in node['next_ids'] if target in nodes}
        pending = set(nodes)
        seeds = [node['id'] for node in group if node['id'] not in incoming] + [node['id'] for node in group]
        for seed in seeds:
            if seed not in pending:
                continue
            chain = [];current=seed
            while current in pending:
                pending.remove(current);node=nodes[current];chain.append(node)
                successors = [i for i in node['next_ids'] if i in pending]
                if not successors:
                    break
                current=successors[0]
            lanes.append({'id':f'{key[0]}:{key[1]}:{key[2]}:{seed}','road_id':key[0],
                          'section_id':key[1],'lane_id':key[2],'width_m':chain[0]['lane_width_m'],
                          'points':[{k:n[k] for k in ('x','y','z','heading_rad')} for n in chain]})
    return lanes


def register_simulation_catalog_routes(api,db):
    def routes(kind,path):
        @api.get(path,tags=['Simulation catalogs'],name=f'list_{kind}_catalog',
                 description=f'List current project-scoped {kind} catalog revisions. Pin id, revision and sha256 before authoring. Map lists omit large native geometry; read the selected revision for original geometry.')
        def listing(request:Request,project_id:str='default',limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0)):
            project_authorize(request,db,project_id)
            with db.read_session() as session:
                selected=list(session.execute(select(Row.id,func.max(Row.revision)).where(
                    Row.project_id==project_id,Row.kind==kind).group_by(Row.id).order_by(Row.id).limit(limit+1).offset(offset)))
                items=[]
                for identity,revision in selected[:limit]:
                    item=get_catalog(session,kind,project_id,identity,revision)
                    if kind=='map':
                        value=item['definition']
                        item['definition']={k:v for k,v in value.items() if k not in {'nodes','opendrive'}}
                        item['definition'].update(node_count=len(value['nodes']),opendrive_sha256=value['opendrive']['sha256'])
                    items.append(item)
                return {'items':items,'next_offset':offset+limit if len(selected)>limit else None}

        @api.post(path,status_code=201,tags=['Simulation catalogs'],name=f'register_{kind}_catalog',
                  description=f'Append checked {kind} metadata as an immutable project revision. expected_revision=0 creates; later writes require the current revision. Authored metadata and rights declarations are not native execution or physical validation.')
        def create(body:CatalogCreate,request:Request):
            project_authorize(request,db,body.project_id,'Engineer')
            if kind=='map' and getattr(body.definition,'generated',None) is not None:
                raise HTTPException(422,'Generated provenance must be produced by /object-maps/{identity}/build')
            if kind=='map' and getattr(body.definition,'external',None) is not None:
                raise HTTPException(422,'External map provenance must be published from a successful /map-sources native qualification')
            try:
                return register_catalog(db,kind,body)
            except ValueError as error:
                raise HTTPException(422,str(error)) from error

        @api.get(path+'/{identity}',tags=['Simulation catalogs'],name=f'read_{kind}_catalog',
                 description=f'Read an exact project-scoped {kind} catalog revision with verified manifest SHA-256. Original map OpenDRIVE bytes and native lane topology are preserved.')
        def read(identity:str,request:Request,project_id:str='default',revision:int|None=Query(None,ge=1)):
            project_authorize(request,db,project_id)
            with db.read_session() as session:
                result=get_catalog(session,kind,project_id,identity,revision)
            if kind=='map':
                result['lanes']=map_lanes(result['definition'])
            return result

    for kind,path in [('map','/maps'),('asset','/assets'),('controller','/controllers')]:
        routes(kind,path)

    @api.post('/maps/{identity}/route',tags=['Simulation catalogs'],
              openapi_extra={'x-axiom-min-role':'Viewer','x-axiom-read-only':True},
              description='Read-only route planning through the pinned directed native lane graph. Snap explicit start/via/goal poses; reject distant, opposite-facing or unreachable selections. A planned route is not a driven trajectory or a test pass.')
    def route(identity:str,body:RouteRequest,request:Request):
        project_authorize(request,db,body.project_id)
        with db.read_session() as session:
            result=get_catalog(session,'map',body.project_id,identity,body.revision)
        if result['sha256'] != body.sha256:
            raise HTTPException(409,'Route map hash differs from the pinned catalog revision')
        return plan_route(result['definition'],body)
