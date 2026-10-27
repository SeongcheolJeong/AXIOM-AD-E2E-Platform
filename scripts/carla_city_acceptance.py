#!/usr/bin/env python3
"""Author or serially run a bounded, immutable native-city acceptance suite.

Draft/author never submit computation. Run uses the normal Experiment API and
waits for each repeat before submitting the next. Generated paths are authored
from registered native topology; they are not evidence that a vehicle drove.
"""
from __future__ import annotations
import argparse
from collections import deque
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION='carla-city-acceptance/1'
CASES=('straight','curve','left_turn','right_turn','lead_stop','pedestrian_crossing','obstacle_response','red_light_stop','cut_in')
FROZEN={'max_lateral_error_p95_m':.5,'max_goal_error_m':2.,'max_final_speed_mps':.2,'require_goal':True,'require_no_contacts':True}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def ref(value):return {k:value[k] for k in ('id','revision','sha256')}
def pose(node):return {k:node[k] for k in ('x','y','z','heading_rad')}
def distance(a,b):return math.hypot(a['x']-b['x'],a['y']-b['y'])
def angle(value):return math.atan2(math.sin(value),math.cos(value))
def length(route):return sum(distance(a,b) for a,b in zip(route,route[1:]))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


class API:
    def __init__(self,url,token=None):
        self.url=url.rstrip('/');self.headers={'Content-Type':'application/json'}
        if token:self.headers['Authorization']='Bearer '+token
    def request(self,path,body=None,key=None):
        headers=dict(self.headers)
        if key:headers['Idempotency-Key']=key
        request=urllib.request.Request(self.url+path,data=None if body is None else canonical(body),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            message=error.read(8000).decode('utf-8',errors='replace')
            raise RuntimeError(f'API {error.code} {path}: {message}') from error
    def items(self,kind,project):
        result=[];offset=0
        while True:
            page=self.request('/'+kind+'?'+urllib.parse.urlencode({'project_id':project,'limit':1000,'offset':offset}))
            result.extend(page['items']);offset=page.get('next_offset')
            if offset is None:return result


def classify_path(nodes):
    headings=[angle(b['heading_rad']-a['heading_rad']) for a,b in zip(nodes,nodes[1:])]
    turn=sum(headings);curvature=sum(abs(v) for v in headings);metres=length(nodes)
    junction=any(n.get('is_junction') for n in nodes)
    result=[]
    if 30<=metres<=46 and curvature<.10 and not junction:result.append('straight')
    if 30<=metres<=72 and .2<=abs(turn)<=1.45 and curvature<=1.7 and max([0,*map(abs,headings)])<.18 and not junction:result.append('curve')
    if 32<=metres<=72 and junction and .85<=abs(turn)<=2.1 and curvature<=2.5:
        result.append('right_turn' if turn>0 else 'left_turn')
    return result


def choose_paths(definition):
    """Bounded graph search, retaining every native edge of the selected path."""
    nodes={node['id']:node for node in definition['nodes']};starts=[]
    for spawn in definition.get('spawn_points',[]):
        candidate=min(nodes.values(),key=lambda n:distance(spawn,n))
        if (not candidate['is_junction'] and distance(spawn,candidate)<=4 and
            abs(angle(spawn['heading_rad']-candidate['heading_rad']))<.3 and candidate['id'] not in starts):starts.append(candidate['id'])
    # Registered spawn points are preferable. A sparse set of additional lane
    # nodes allows discovery on curved roads without inventing extra geometry.
    starts.extend(node['id'] for i,node in enumerate(definition['nodes']) if i%12==0 and not node['is_junction'] and node['id'] not in starts)
    best={};scores={}
    for start in starts[:600]:
        queue=deque([([start],0.)]);visited=0
        while queue and visited<400:
            path,metres=queue.popleft();visited+=1;last=nodes[path[-1]]
            if metres>=30 and not last['is_junction']:
                values=[nodes[i] for i in path]
                for kind in classify_path(values):
                    score=abs(metres-42)+(.03*len(path))
                    if score<scores.get(kind,math.inf):best[kind]=values;scores[kind]=score
                    if kind in {'left_turn','right_turn'} and score<scores.get('red_light_stop',math.inf):
                        approach=prepend_native_approach(values,definition)
                        if approach:
                            best['red_light_stop']=approach;best['red_light_stop_original']=values;scores['red_light_stop']=score
                    if kind=='straight' and score<scores.get('cut_in',math.inf) and adjacent_cut_in(values,definition):
                        best['cut_in']=values;scores['cut_in']=score
            if metres>=72 or len(path)>=120:continue
            for successor in last['next_ids']:
                if successor in path:continue
                next_node=nodes[successor];step=distance(last,next_node)
                if step<.05 or step>6 or abs(angle(next_node['heading_rad']-last['heading_rad']))>1.4:continue
                queue.append((path+[successor],metres+step))
    return best


def prepend_native_approach(route,definition,minimum_m=20.):
    """Extend backwards on real directed edges, without inventing a spawn pose."""
    nodes={node['id']:node for node in definition['nodes']};predecessors={identity:[] for identity in nodes}
    for node in nodes.values():
        for successor in node['next_ids']:predecessors[successor].append(node['id'])
    occupied={n['id'] for n in route};queue=deque([([route[0]['id']],0.)]);visited=0
    while queue and visited<2000:
        reversed_path,metres=queue.popleft();visited+=1;head=nodes[reversed_path[-1]]
        if metres>=minimum_m:
            return [nodes[identity] for identity in reversed(reversed_path)]+route[1:]
        for identity in sorted(predecessors[head['id']]):
            previous=nodes[identity];step=distance(previous,head)
            if (identity in occupied or identity in reversed_path or previous['is_junction']
                or not .05<=step<=6 or abs(angle(previous['heading_rad']-head['heading_rad']))>.3):continue
            queue.append((reversed_path+[identity],metres+step))
    return None


def cumulative(route):
    result=[0.]
    for a,b in zip(route,route[1:]):result.append(result[-1]+distance(a,b))
    return result


def interpolate(route,metres):
    distances=cumulative(route)
    for i,(lo,hi) in enumerate(zip(distances,distances[1:])):
        if metres<=hi or i==len(route)-2:
            f=max(0.,min(1.,(metres-lo)/max(1e-9,hi-lo)));a,b=route[i:i+2]
            value={k:a[k]+(b[k]-a[k])*f for k in ('x','y','z')}
            value['heading_rad']=a['heading_rad']+angle(b['heading_rad']-a['heading_rad'])*f
            return value
    return pose(route[-1])


def adjacent_cut_in(route,definition):
    """One same-direction adjacent lane, explicit authored S-curve geometry."""
    distances=cumulative(route)
    start_index=next((i for i,d in enumerate(distances) if d>=10),None)
    if start_index is None or distances[-1]<36:return None
    start=route[start_index];h=start['heading_rad'];normal=(-math.sin(h),math.cos(h))
    choices=[n for n in definition['nodes'] if n['road_id']==start['road_id'] and n['section_id']==start['section_id']
        and n['lane_id']*start['lane_id']>0 and abs(n['lane_id']-start['lane_id'])==1
        and abs(angle(n['heading_rad']-h))<.1 and 2.5<=distance(n,start)<=5
        and abs((n['x']-start['x'])*math.cos(h)+(n['y']-start['y'])*math.sin(h))<1]
    if not choices:return None
    neighbor=min(choices,key=lambda n:distance(start,n));offset=(neighbor['x']-start['x'])*normal[0]+(neighbor['y']-start['y'])*normal[1]
    if not 2.5<=abs(offset)<=5:return None
    begin=distances[start_index];span=min(24.,distances[-1]-begin-5)
    path=[]
    for i in range(31):
        t=i/30;p=interpolate(route,begin+span*t);blend=1-(10*t**3-15*t**4+6*t**5)
        p['x']+=normal[0]*offset*blend;p['y']+=normal[1]*offset*blend;path.append(p)
    path.extend(pose(p) for p,d in zip(route,distances) if d>begin+span+.2)
    for a,b in zip(path,path[1:]):a['heading_rad']=math.atan2(b['y']-a['y'],b['x']-a['x'])
    path[-1]['heading_rad']=path[-2]['heading_rad']
    return {'route':path,'from_lane':neighbor['lane_id'],'to_lane':start['lane_id'],'road_id':start['road_id'],
            'scope':'authored_adjacent_lane_s_curve_not_traffic_planner_or_legal_right_of_way'}


def criteria(require_goal=True,require_stop=True):
    values=[{'metric':'native_contact','unit':'boolean','operator':'eq','threshold':False,'actor_id':'all'},
            {'metric':'lateral_error_p95','unit':'m','operator':'lte','threshold':.5,'actor_id':'ego'},
            {'metric':'distance_travelled','unit':'m','operator':'gte','threshold':2.,'actor_id':'ego'}]
    if require_goal:values.append({'metric':'goal_error','unit':'m','operator':'lte','threshold':2.,'actor_id':'ego'})
    if require_stop:values.append({'metric':'final_speed','unit':'m/s','operator':'lte','threshold':.2,'actor_id':'ego'})
    return values


def definitions(native_map,assets,controllers,ego_mode='route_follow'):
    definition=native_map['definition'];paths=choose_paths(definition);items=[]
    by_blueprint={a['definition']['blueprint_id']:a for a in assets}
    by_mode={c['definition']['mode']:c for c in controllers}
    vehicle=by_blueprint.get('vehicle.toyota.prius')
    pedestrian=next((a for a in assets if a['definition']['actor_kind']=='pedestrian'),None)
    if vehicle is None:raise ValueError('Prius asset is required for the fixed native acceptance profile')
    for kind in CASES:
        base=paths.get(kind if kind in {'straight','curve','left_turn','right_turn','cut_in'} else 'straight')
        if kind=='cut_in' and base is None:
            items.append({'case':kind,'status':'unsupported','reason':'no_same_direction_adjacent_lane_for_supported_physical_cut_in'});continue
        red_original=None
        if kind=='red_light_stop':
            turns=[paths[k] for k in ('left_turn','right_turn') if k in paths]
            base=paths.get('red_light_stop');red_original=paths.get('red_light_stop_original')
            if base is None:
                for candidate in turns:
                    extended=prepend_native_approach(candidate,definition)
                    if extended is not None:red_original=candidate;base=extended;break
            if turns and base is None:
                items.append({'case':kind,'status':'unsupported','reason':'no_20m_registered_predecessor_approach_for_fixed_red_trial'});continue
        if base is None:
            items.append({'case':kind,'status':'unsupported','reason':'no_bounded_registered_route_with_required_geometry'});continue
        mode=ego_mode if ego_mode=='autoware' or kind in {'straight','curve','left_turn','right_turn'} else 'traffic'
        if mode not in by_mode or by_mode[mode]['definition']['status']!='available':
            items.append({'case':kind,'status':'unsupported','reason':f'{mode}_controller_not_configured'});continue
        def actor(identity,role,selected_asset,route,controller_mode,speed=3):
            return {'id':identity,'kind':selected_asset['definition']['actor_kind'],'role':role,'asset':ref(selected_asset),
                    'pose':pose(route[0]),'route':[pose(p) for p in route],'speed_mps':speed,
                    'controller':{'kind':controller_mode,'ref':ref(by_mode[controller_mode]),'end_behavior':'stop','obey_signals':True}}
        route=[pose(p) for p in base];ego=actor('ego','ego',vehicle,route,mode)
        duration=math.ceil(length(route)/3+8)
        scenario={'schema_version':'scenario-v2/2','name':f'{definition["name"]} · {kind} · {ego_mode} acceptance',
            'description':'Authored native-topology acceptance trial. Criteria frozen before execution; actual evidence is recorded by the native runner.',
            'seed':42,'map':ref(native_map),'duration_s':duration,'timestep_s':.05,'actors':[ego],
            'sensors':[{'id':'front','actor_id':'ego','width':320,'height':180,'period_s':.2}],
            'acceptance':dict(FROZEN)}
        metadata={'route_length_m':length(route),'authored_controller_mode':mode,'native_map_sha256':native_map['sha256']}
        if kind!='red_light_stop' and mode in {'traffic','autoware'}:
            scenario['signals']=[{'id':s['id'],'initial_state':'green','changes':[]} for s in definition['signals'] if s['kind']=='traffic_light']
            metadata['signal_policy']='all_registered_map_lights_fixed_green_to_isolate_actor_response'
        require_goal=True;require_stop=True
        if kind in {'lead_stop','obstacle_response'}:
            at=12 if kind=='lead_stop' else 20;distances=cumulative(base);index=next(i for i,d in enumerate(distances) if d>=at)
            npc=actor('lead','other',vehicle,base[index:],'route_follow',2.)
            if kind=='lead_stop':npc['controller']['stop_time_s']=4.
            else:npc.update(route=[],speed_mps=0,controller={'kind':'static','ref':ref(by_mode['static']),'end_behavior':'stop'})
            scenario['actors'].append(npc);scenario['duration_s']=18;require_goal=False
        elif kind=='pedestrian_crossing':
            if pedestrian is None:
                items.append({'case':kind,'status':'unsupported','reason':'pedestrian_asset_not_registered'});continue
            center=interpolate(route,18);normal=(-math.sin(center['heading_rad']),math.cos(center['heading_rad']))
            crossing=[{**center,'x':center['x']+normal[0]*offset,'y':center['y']+normal[1]*offset,
                       'heading_rad':center['heading_rad']+math.pi/2} for offset in (-5.,0.,5.)]
            walker=actor('crossing','other',pedestrian,crossing,'route_follow',1.4);walker['controller']['wait_s']=1.
            scenario['actors'].append(walker)
        elif kind=='red_light_stop':
            lights=[s for s in definition['signals'] if s['kind']=='traffic_light']
            if not lights:
                items.append({'case':kind,'status':'unsupported','reason':'no_registered_traffic_lights'});continue
            scenario['signals']=[{'id':s['id'],'initial_state':'red','changes':[]} for s in lights]
            scenario['duration_s']=18;require_goal=False
            metadata['signal_policy']='all_registered_map_lights_fixed_red_for_episode'
            metadata['native_predecessor_approach_m']=length(base)-length(red_original)
            metadata['original_route_start_node_id']=red_original[0]['id']
        elif kind=='cut_in':
            cut=adjacent_cut_in(base,definition)
            if cut is None:
                items.append({'case':kind,'status':'unsupported','reason':'no_same_direction_adjacent_lane_for_supported_physical_cut_in'});continue
            npc=actor('cut-in','other',vehicle,cut['route'],'route_follow',2.5)
            npc['route_mode']='authored_lane_change';npc['controller'].update(wait_s=1.,required_gap_m=6.)
            scenario['actors'].append(npc);metadata['lane_change']=cut
        scenario['acceptance']['require_goal']=require_goal
        fixed_criteria=criteria(require_goal,require_stop)
        participant='crossing' if kind=='pedestrian_crossing' else ('cut-in' if kind=='cut_in' else None)
        if participant:
            fixed_criteria.extend([{'metric':'goal_error','unit':'m','operator':'lte','threshold':2.,'actor_id':participant},
                                   {'metric':'final_speed','unit':'m/s','operator':'lte','threshold':.2,'actor_id':participant}])
        items.append({'case':kind,'status':'draft','scenario':scenario,'criteria':fixed_criteria,'authoring':metadata})
    return items


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(canonical(value));temp.replace(path)

def record_results(path,result):
    result['counts']={name:sum(t['classification']==name for t in result['trials']) for name in
                      ('passed','sut_failure','infrastructure_failure','unsupported','unevaluated','outcome_unknown')}
    write(path,result)


def build(api,project,map_ids,ego_mode,author=False,suite_id=None,checkpoint=None):
    suite_id=suite_id or 'city-suite-'+uuid.uuid4().hex
    assets=api.items('assets',project);controllers=api.items('controllers',project);maps=api.items('maps',project)
    selected=[item for item in maps if not map_ids or item['id'] in map_ids]
    if map_ids and set(map_ids)!={m['id'] for m in selected}:raise ValueError('A requested map is not registered in this project')
    if not selected:raise ValueError('No registered city maps')
    suite={'schema_version':VERSION,'id':suite_id,'created_at':now(),'project_id':project,'ego_controller':ego_mode,
           'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'execution_requested':False,'authoring_complete':False,'cases':[],
           'policy':{'repeat_seed':'fixed_scenario_seed','criteria_changes_after_trials':False,
                     'stop_submissions_on_infrastructure_failure':True,'no_scene_diversity_credit_for_repeats':True}}
    for entry in selected:
        native_map=api.request('/maps/'+entry['id']+'?'+urllib.parse.urlencode({'project_id':project,'revision':entry['revision']}))
        for item in definitions(native_map,assets,controllers,ego_mode):
            item['map']=ref(native_map)
            if item['status']=='draft':
                validation=api.request('/scenario-v2/yaml/validate',{'project_id':project,'yaml':json.dumps(item['scenario'])})
                item['scenario']=validation['scenario'];item['configuration_hash']=validation['configuration_hash']
                item['catalog_warnings']=validation['catalog_validation']['warnings']
                if author:
                    prefix=f'{suite_id}:{entry["id"]}:{item["case"]}'
                    saved=api.request('/scenario-v2/yaml/import',{'project_id':project,'yaml':validation['canonical_yaml']},prefix+':scenario')
                    item['saved_scenario']={k:saved[k] for k in ('id','revision','manifest_hash','configuration_hash')}
                    plan=api.request('/experiment-verification-plans',{'project_id':project,'name':item['scenario']['name'],
                        'purpose':'Fixed native-engine acceptance criteria; original contacts, complete tracking and stop/goal diagnostics. No physical sensor or road safety claim.',
                        'experiment_kind':'object_sim','allowed_fidelities':['native_carla_object_physics'],'criteria':item['criteria'],
                        'change_reason':'Acceptance criteria fixed before this suite submits any native computation'},prefix+':plan')
                    item['verification_plan']={k:plan[k] for k in ('id','revision','manifest_hash')};item['status']='authored'
            suite['cases'].append(item)
            if checkpoint:write(checkpoint,{**suite,'suite_sha256':digest(suite)})
    suite['coverage']={'requested_case_slots':len(suite['cases']),'authored':sum(c['status']=='authored' for c in suite['cases']),
                       'draft':sum(c['status']=='draft' for c in suite['cases']),'unsupported':sum(c['status']=='unsupported' for c in suite['cases'])}
    suite['authoring_complete']=True
    return {**suite,'suite_sha256':digest(suite)}


def validate_suite(suite):
    if suite.get('schema_version')!=VERSION or digest({k:v for k,v in suite.items() if k!='suite_sha256'})!=suite.get('suite_sha256'):
        raise ValueError('Suite manifest hash or contract differs; do not change frozen criteria after authoring')
    if not suite.get('authoring_complete'):raise ValueError('Suite authoring is incomplete; inspect its checkpoint before execution')
    for item in suite['cases']:
        if item['status']=='authored' and digest(item['scenario'])!=item['configuration_hash']:
            raise ValueError('Scenario content differs from its frozen suite configuration')


def run(api,suite,repeats,output,poll_s=2,timeout_s=1800,maps=None,case_names=None):
    validate_suite(suite)
    if not 1<=repeats<=3:raise ValueError('The acceptance profile permits one to three fixed repeats')
    available={item['map']['id'] for item in suite['cases']}
    selected_maps=set(maps) if maps else available
    if not selected_maps<=available:raise ValueError('Requested map is absent from the sealed suite: '+', '.join(sorted(selected_maps-available)))
    available_cases={item['case'] for item in suite['cases']}
    selected_cases=set(case_names) if case_names else available_cases
    if not selected_cases<=available_cases:raise ValueError('Requested case is absent from the sealed suite: '+', '.join(sorted(selected_cases-available_cases)))
    cases=[item for item in suite['cases'] if item['map']['id'] in selected_maps and item['case'] in selected_cases]
    if not cases:raise ValueError('Map/case selection has no slots in the sealed suite')
    result={'schema_version':'carla-city-acceptance-results/1','suite_id':suite['id'],'suite_sha256':suite['suite_sha256'],
            'started_at':now(),'repeat_count':repeats,'requested_case_slots':suite['coverage']['requested_case_slots'],
            'selection':{'map_ids':sorted(selected_maps),'case_names':sorted(selected_cases),'selected_case_slots':len(cases),'excluded_case_slots':len(suite['cases'])-len(cases)},
            'unsupported_cases':[{'map':c['map'],'case':c['case'],'reason':c['reason']} for c in cases if c['status']=='unsupported'],
            'worker_mode':'existing_operator_worker_required',
            'trials':[],'complete':False}
    record_results(output,result)
    for item in cases:
        if item['status']!='authored':continue
        selected=item['saved_scenario'];prefix=f'{suite["id"]}:{item["map"]["id"]}:{item["case"]}'
        current=api.request(f'/scenario-v2/{selected["id"]}?revision={selected["revision"]}')
        if current['manifest_hash']!=selected['manifest_hash'] or current['configuration_hash']!=item['configuration_hash']:
            raise ValueError('Stored scenario no longer matches the frozen acceptance suite')
        definition={'project_id':suite['project_id'],'name':item['scenario']['name'],'kind':'object_sim',
            'adapter_id':'carla-city-object','adapter_version':'carla-city-object/1.0.0','comparison_kind':'single',
            'seed':item['scenario']['seed'],'input':{'scenario_id':selected['id'],'scenario_revision':selected['revision'],
                                                 'configuration_hash':item['configuration_hash']}}
        try:experiment=api.request('/experiments',definition,prefix+':experiment')
        except RuntimeError as error:
            result['trials'].append({'map':item['map'],'case':item['case'],'repeat':None,'status':'not_dispatched',
                'classification':'unsupported' if 'API 422 ' in str(error) else 'infrastructure_failure','reason':str(error),
                'configuration_hash':item['configuration_hash'],'seed':item['scenario']['seed']})
            result['stop_reason']='native_preflight_refused_no_execution_submitted';record_results(output,result);return result
        for index in range(repeats):
            job=api.request(f'/experiments/{experiment["id"]}/executions',{'preview_hash':experiment['preview_hash']},prefix+f':repeat-{index+1}')
            trial={'map':item['map'],'case':item['case'],'repeat':index+1,'seed':item['scenario']['seed'],
                   'configuration_hash':item['configuration_hash'],'experiment_id':experiment['id'],'execution_id':job['id'],
                   'status':job['status'],'classification':'unevaluated'}
            result['trials'].append(trial);record_results(output,result);deadline=time.monotonic()+timeout_s
            while job['status'] not in {'completed','failed','cancelled','interrupted'}:
                if time.monotonic()>=deadline:
                    trial['classification']='outcome_unknown';trial['reason']='poll_timeout_existing_execution_preserved';record_results(output,result)
                    return result
                time.sleep(poll_s);job=api.request('/experiments/executions/'+job['id'])
                trial['status']=job['status'];record_results(output,result)
            trial['execution_manifest_hash']=job.get('manifest_hash')
            trial['native_summary']=job.get('summary');trial['outcome_uncertain']=job.get('outcome_uncertain',False)
            plan=item['verification_plan']
            evaluation=api.request(f'/experiment-verification-plans/{plan["id"]}/evaluations',
                {'plan_revision':plan['revision'],'execution_ids':[job['id']]},prefix+f':evaluation-{index+1}')
            trial['verification']={k:evaluation[k] for k in ('id','manifest_hash','verdict','counts','entries')}
            trial['classification']=evaluation['entries'][0]['status'];record_results(output,result)
            participant_checks=[c for c in evaluation['entries'][0].get('criteria',[])
                if (c['criterion']['actor_id'] not in {'ego','all'} and c['criterion']['metric']=='goal_error')
                   or c['criterion']['metric']=='distance_travelled']
            trial['exercise_status']=('not_evaluated' if not participant_checks else
                'not_exercised_as_planned' if any(c['status']!='passed' for c in participant_checks) else 'motion_and_participant_requirements_met')
            record_results(output,result)
            if trial['classification'] in {'infrastructure_failure','unsupported'} or trial['outcome_uncertain']:
                result['stop_reason']='infrastructure_or_unsupported_input_requires_investigation';record_results(output,result);return result
    result['complete']=True;result['finished_at']=now()
    record_results(output,result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['draft','author','run'])
    parser.add_argument('--api',default='http://127.0.0.1:8031/api/v1');parser.add_argument('--project',default='default')
    parser.add_argument('--token-file',type=Path);parser.add_argument('--map',action='append',dest='maps',help='Catalog map ID; repeat to select multiple maps (run preserves the original suite hash)')
    parser.add_argument('--case',action='append',dest='case_names',help='Run only this sealed case category; repeat for multiple categories')
    parser.add_argument('--ego-controller',choices=['route_follow','traffic','autoware'],default='route_follow')
    parser.add_argument('--suite',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--suite-id');parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args();token=args.token_file.read_text().strip() if args.token_file else os.environ.get('ADE2E_API_TOKEN')
    api=API(args.api,token)
    if args.action=='run':
        if args.suite is None:parser.error('run requires --suite from an author operation')
        if args.suite.resolve()==args.output.resolve():parser.error('run output must not overwrite the sealed source suite')
        result=run(api,json.loads(args.suite.read_bytes()),args.repeats,args.output,maps=args.maps,case_names=args.case_names)
        print(json.dumps({'output':str(args.output),'complete':result['complete'],'counts':result.get('counts'),
                          'trials':len(result['trials']),'stop_reason':result.get('stop_reason')}))
    else:
        if args.case_names:parser.error('--case is a run filter; authoring always preserves all case slots')
        result=build(api,args.project,args.maps,args.ego_controller,args.action=='author',args.suite_id,args.output)
        write(args.output,result)
        print(json.dumps({'output':str(args.output),'id':result['id'],'suite_sha256':result['suite_sha256'],'coverage':result['coverage'],
                          'gaps':[{'map':c['map']['id'],'case':c['case'],'reason':c['reason']} for c in result['cases'] if c['status']=='unsupported']}))


if __name__=='__main__':main()
