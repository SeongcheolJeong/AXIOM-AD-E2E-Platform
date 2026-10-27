#!/usr/bin/env python3
"""Author and explicitly execute frozen reactive Object Sim acceptance cases.

`author` uses normal map/scenario/verification APIs and never submits native work.
`run` submits one existing frozen case at a time through the normal execution API.
The three seeds are repeatability coverage, not three independent road conditions.
No direct engine connection, hidden cleanup, criterion adjustment or retry occurs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid

from carla_city_acceptance import API,canonical,digest,ref,pose,write,now

VERSION='object-sim-acceptance/1'
SEEDS=(42,43,44)
CASES=('integration_eight','stop_resume','risk_brake','obstacle_bypass','gap_release','closed_route_hold')


def map_source():
    return {'schema_version':'object-map/1','name':'Object Sim · authored two-lane acceptance road',
        'roads':[{'id':'main','name':'Two same-direction permitted lanes','origin':{'x':0,'y':0,'z':0,'heading_rad':0},
            'geometries':[{'kind':'line','length_m':320}],
            'lanes':[{'id':-1,'width_m':3.5,'speed_limit_mps':8,'lane_change':'both'},
                     {'id':-2,'width_m':3.5,'speed_limit_mps':8,'lane_change':'both'}]}],
        'connections':[],'junctions':[],'signals':[],'objects':[],
        'rights':{'license':'Authored project test geometry; no third-party map content',
                  'source_url':'','redistribution':'permitted','notes':'Procedural roads only; no sensor realism claim.'}}


def route(definition,lane,start,end):
    selected=sorted((n for n in definition['nodes'] if n['lane_id']==lane and start-.01<=n['x']<=end+.01),key=lambda n:n['x'])
    if len(selected)<2:raise ValueError('Compiled map lacks requested bounded lane route')
    return [pose(n) for n in selected]


def behavior(kind,identity='action',time_s=0,**params):
    return {'id':identity,'kind':kind,'trigger':{'kind':'time','value':time_s,'operator':'gte'},
        'timeout_s':50,'on_timeout':'fail',**params}


def case_definitions(native_map,vehicle,seed):
    definition=native_map['definition'];items=[]
    def actor(identity,lane,start,end,speed=4,actions=None,risk=False):
        path=route(definition,lane,start,end)
        return {'id':identity,'kind':'vehicle','role':'ego' if identity=='ego' else 'other',
            'asset':ref(vehicle),'pose':path[0],'route':path,'speed_mps':speed,
            'controller':{'kind':'reactive','end_behavior':'stop','obey_signals':True},
            'behavior':{'schema_version':'object-behavior/1','risk_mode':risk,'actions':actions or []},
            'planner':{'schema_version':'object-planner/1'}}
    def stationary(identity,lane,x):
        value=actor(identity,lane,x,x+4,0)
        value.update(route=[],controller={'kind':'static','end_behavior':'stop'})
        return value
    for category in CASES:
        ego=actor('ego',-1,10,130,5);actors=[ego];duration=40;goal=True;minimum_distance=20
        expectations={'required_events':[],'dynamic_actor_minimum':1,'required_motion_actor_ids':['ego']}
        if category=='integration_eight':
            actors=[];duration=24
            for lane in (-1,-2):
                for index in range(4):
                    identity='ego' if lane==-1 and index==0 else f'npc-{abs(lane)}-{index}'
                    actors.append(actor(identity,lane,10+index*55,46+index*55,3))
            expectations['dynamic_actor_minimum']=8
            expectations['required_motion_actor_ids']=[a['id'] for a in actors]
        elif category=='stop_resume':
            ego['behavior']['actions']=[behavior('stop','stop',2,duration_s=2),
                behavior('resume','resume',speed_mps=5)]
            expectations['required_events']=[{'type':'action_completed','action_id':'stop'},
                                             {'type':'action_completed','action_id':'resume'}]
        elif category=='risk_brake':
            ego['behavior'].update(risk_mode=True,actions=[behavior('emergency_brake','brake',3,duration_s=1),
                behavior('resume','resume',speed_mps=5)])
            expectations['required_events']=[{'type':'action_started','action_id':'brake'},
                                             {'type':'action_completed','action_id':'resume'}]
        elif category in {'obstacle_bypass','gap_release'}:
            ego['behavior']['actions']=[behavior('obstacle_bypass','bypass')]
            actors.append(stationary('parked',-1,45));duration=45
            expectations['required_events']=[{'type':'route_replanned','reason':'obstacle_bypass'},
                                             {'type':'action_completed','action_id':'bypass'}]
            if category=='gap_release':
                actors.append(actor('rear',-2,2,160,7))
                expectations['required_events'].insert(0,{'type':'decision_changed','reason':'gap_wait'})
                expectations['required_motion_actor_ids'].append('rear');expectations['dynamic_actor_minimum']=2
        elif category=='closed_route_hold':
            closed=min((n for n in definition['nodes'] if n['lane_id']==-1),key=lambda n:abs(n['x']-60))
            ego['planner']['closed_node_ids']=[closed['id']]
            duration=8;goal=False;minimum_distance=None
            expectations['required_motion_actor_ids']=[];expectations['dynamic_actor_minimum']=0
            expectations['required_events']=[{'type':'decision_changed','reason':'no_legal_route'}]
            expectations['maximum_ego_travel_m']=.5
        criteria=[{'metric':'native_contact','unit':'boolean','operator':'eq','threshold':False,'actor_id':'all'},
                  {'metric':'lateral_error_p95','unit':'m','operator':'lte','threshold':.75,'actor_id':'ego'},
                  {'metric':'final_speed','unit':'m/s','operator':'lte','threshold':.2,'actor_id':'ego'}]
        if goal:criteria.append({'metric':'goal_error','unit':'m','operator':'lte','threshold':2.,'actor_id':'ego'})
        if minimum_distance is not None:
            for identity in expectations['required_motion_actor_ids']:
                criteria.append({'metric':'distance_travelled','unit':'m','operator':'gte','threshold':minimum_distance,'actor_id':identity})
        scenario={'schema_version':'scenario-v2/3','name':f'Object Sim · {category} · seed {seed}',
            'description':'Frozen authored-road low-speed GT reactive acceptance. Native execution and behavior exercise checked separately. No measured sensor or commercial equivalence claim.',
            'seed':seed,'map':ref(native_map),'duration_s':duration,'timestep_s':.05,'actors':actors,
            'sensors':[{'id':'front','actor_id':'ego','kind':'camera_rgb','width':320,'height':180,'period_s':.2}],
            'acceptance':{'max_lateral_error_p95_m':.75,'max_goal_error_m':2.,'max_final_speed_mps':.2,
                          'require_goal':goal,'require_no_contacts':True}}
        items.append({'case':category,'seed':seed,'scenario':scenario,'criteria':criteria,'expectations':expectations})
    return items


def author(api,project,output,identity=None,seeds=SEEDS,categories=CASES):
    """API source/build/save only. No experiments or execution jobs are created."""
    identity=identity or 'object-accept-'+uuid.uuid4().hex[:16]
    assets=api.items('assets',project)
    vehicle=next((a for a in assets if a['definition']['blueprint_id']=='vehicle.toyota.prius'),None)
    if vehicle is None:raise ValueError('Registered vehicle.toyota.prius asset required; import native catalog first')
    document=map_source();preview=api.request('/object-maps/preview',{'project_id':project,'definition':document})
    if not preview['buildable']:raise ValueError('Authored acceptance road is not buildable')
    source=api.request('/object-maps',{'project_id':project,'id':identity,'expected_revision':0,'definition':document})
    compiled=api.request('/object-maps/'+identity+'/build',{'project_id':project,'revision':source['revision'],'sha256':source['sha256'],'server_version':'1ae5356'})
    native_map=compiled['map']
    suite={'schema_version':VERSION,'id':identity,'project_id':project,'created_at':now(),'map_source':ref(source),'map':ref(native_map),
        'map_build':compiled['build_manifest'],'source_definition_sha256':digest(document),'seeds':list(seeds),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'helper_sha256':hashlib.sha256(Path(__file__).with_name('carla_city_acceptance.py').read_bytes()).hexdigest(),
        'native_execution_submitted':False,'authoring_complete':False,'cases':[],
        'policy':{'stop_on_infrastructure_failure':True,'criteria_frozen_before_execution':True,
                  'seed_repeats_are_not_scene_diversity':True,'road_kind':'authored_straight_two_lane',
                  'excluded':['junction_negotiation','highway','sensor_physical_validation','all_behavior_library_coverage']}}
    write(output,suite)
    for seed in seeds:
        for item in case_definitions(native_map,vehicle,seed):
            if item['case'] not in categories:continue
            prefix=f'{identity}:{item["case"]}:{seed}'
            validated=api.request('/scenario-v2/yaml/validate',{'project_id':project,'yaml':json.dumps(item['scenario'])})
            item['scenario']=validated['scenario'];item['configuration_hash']=validated['configuration_hash']
            saved=api.request('/scenario-v2/yaml/import',{'project_id':project,'yaml':validated['canonical_yaml']},prefix+':scenario')
            item['saved_scenario']={k:saved[k] for k in ('id','revision','manifest_hash','configuration_hash')}
            plan=api.request('/experiment-verification-plans',{'project_id':project,'name':item['scenario']['name'],
                'purpose':'Fixed native object/control criteria plus separate immutable scene-event exercise checks; not physical sensor validation.',
                'experiment_kind':'object_sim','allowed_fidelities':['native_carla_object_physics'],'criteria':item['criteria'],
                'change_reason':'Frozen before any native acceptance execution'},prefix+':verification-plan')
            item['verification_plan']={k:plan[k] for k in ('id','revision','manifest_hash')}
            suite['cases'].append(item);write(output,suite)
    suite['authoring_complete']=True;suite['suite_sha256']=digest(suite);write(output,suite)
    return suite


def validate_suite(suite):
    if suite.get('schema_version')!=VERSION or not suite.get('authoring_complete'):
        raise ValueError('Suite is not completely authored')
    if suite.get('suite_sha256')!=digest({k:v for k,v in suite.items() if k!='suite_sha256'}):
        raise ValueError('Frozen suite hash changed')
    for item in suite['cases']:
        if digest(item['scenario'])!=item['configuration_hash']:raise ValueError('Frozen scenario changed')


def inspect_behavior_scene(scene,item):
    events=scene.get('planner_events',[]);expected=item['expectations'];checks=[]
    for required in expected['required_events']:
        matched=[event for event in events if event.get('actor_id')=='ego' and all(event.get(k)==v for k,v in required.items())]
        checks.append({'criterion':'planner_event','expected':required,'passed':bool(matched),'matched_count':len(matched)})
    metrics={m['actor_id']:m for m in scene.get('metrics',[])}
    moving=[i for i in expected['required_motion_actor_ids'] if metrics.get(i,{}).get('distance_travelled_m',0)>=20]
    checks.append({'criterion':'dynamic_actor_minimum','expected':expected['dynamic_actor_minimum'],'observed':len(moving),
                   'passed':len(moving)>=expected['dynamic_actor_minimum']})
    if 'maximum_ego_travel_m' in expected:
        actual=metrics.get('ego',{}).get('distance_travelled_m')
        checks.append({'criterion':'closure_hold_motion','expected_max':expected['maximum_ego_travel_m'],'observed':actual,
            'passed':actual is not None and actual<=expected['maximum_ego_travel_m']})
    checks.append({'criterion':'original_rgb_present','observed':len(scene.get('camera_records',[])),
                   'passed':len(scene.get('camera_records',[]))>0})
    return {'passed':all(c['passed'] for c in checks),'checks':checks,'event_count':len(events)}


def read_scene(api,job):
    artifact=next((a for a in job.get('artifacts',[]) if a['name']=='city-scene.json'),None)
    if artifact is None:raise ValueError('Completed native execution has no original city scene artifact')
    origin=api.url.split('/api/v1')[0]
    request=urllib.request.Request(origin+artifact['download_url'],headers=api.headers)
    with urllib.request.urlopen(request,timeout=180) as response:raw=response.read()
    if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('Original scene artifact hash mismatch')
    return json.loads(raw),artifact['sha256']


def run(api,suite,output,categories=None,seeds=None,poll_s=2,timeout_s=1800,attempt_label=None):
    validate_suite(suite)
    if attempt_label is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',attempt_label):
        raise ValueError('Attempt label must be 1–64 letters, digits, dot, hyphen or underscore')
    if Path(output).exists():raise ValueError('Choose a new report path; prior execution reports are immutable')
    selected=[item for item in suite['cases'] if (categories is None or item['case'] in categories) and (seeds is None or item['seed'] in seeds)]
    if not selected:raise ValueError('No frozen case selected')
    result={'schema_version':'object-sim-acceptance-results/1','suite_id':suite['id'],'suite_sha256':suite['suite_sha256'],
        'started_at':now(),'complete':False,'selected_case_count':len(selected),'trials':[],
        'attempt_label':attempt_label,'criteria_reused_unchanged':True}
    def save():
        result['report_content_sha256']=digest({k:v for k,v in result.items() if k!='report_content_sha256'});write(output,result)
    save()
    for item in selected:
        saved=item['saved_scenario'];prefix=f'{suite["id"]}:{item["case"]}:{item["seed"]}'
        if attempt_label is not None:prefix+=':attempt-'+attempt_label
        current=api.request('/scenario-v2/'+saved['id']+'?'+urllib.parse.urlencode({'revision':saved['revision']}))
        if current['manifest_hash']!=saved['manifest_hash']:raise ValueError('Stored scenario differs from frozen suite')
        trial={'case':item['case'],'seed':item['seed'],'configuration_hash':item['configuration_hash'],'status':'preflight','classification':'unevaluated'}
        result['trials'].append(trial);save()
        try:
            experiment=api.request('/experiments',{'project_id':suite['project_id'],'name':item['scenario']['name'],'kind':'object_sim',
                'adapter_id':'carla-city-object','adapter_version':'carla-city-object/1.0.0','comparison_kind':'single','seed':item['seed'],
                'input':{'scenario_id':saved['id'],'scenario_revision':saved['revision'],'configuration_hash':item['configuration_hash']}},prefix+':experiment')
            job=api.request('/experiments/'+experiment['id']+'/executions',{'preview_hash':experiment['preview_hash']},prefix+':execution')
            trial.update(experiment_id=experiment['id'],execution_id=job['id'],status=job['status']);save()
            deadline=time.monotonic()+timeout_s
            while job['status'] not in {'completed','failed','cancelled','interrupted'}:
                if time.monotonic()>=deadline:
                    trial.update(classification='outcome_unknown',reason='poll_timeout_existing_job_preserved');save();return result
                time.sleep(poll_s);job=api.request('/experiments/executions/'+job['id'])
                trial['status']=job['status'];save()
            trial.update(execution_manifest_hash=job.get('manifest_hash'),summary=job.get('summary'),outcome_uncertain=job.get('outcome_uncertain'))
            if job['status']!='completed' or job.get('outcome_uncertain'):
                trial.update(classification='infrastructure_failure',error=job.get('error'));result['stop_reason']='terminal_failure_requires_investigation';save();return result
            plan=item['verification_plan']
            evaluation=api.request('/experiment-verification-plans/'+plan['id']+'/evaluations',
                {'plan_revision':plan['revision'],'execution_ids':[job['id']]},prefix+':evaluation')
            trial['verification']={k:evaluation[k] for k in ('id','manifest_hash','verdict','counts','entries')}
            trial['classification']=evaluation['entries'][0]['status']
            scene,scene_hash=read_scene(api,job);trial['scene_sha256']=scene_hash
            trial['behavior_exercise']=inspect_behavior_scene(scene,item)
            if trial['classification']=='passed' and not trial['behavior_exercise']['passed']:trial['classification']='behavior_not_exercised'
            save()
        except (RuntimeError,ValueError) as error:
            trial.update(classification='infrastructure_failure',error=str(error));result['stop_reason']='inspection_or_preflight_failed';save();return result
    result['complete']=True;result['finished_at']=now();save();return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['author','run'])
    parser.add_argument('--api',default='http://127.0.0.1:8032/api/v1');parser.add_argument('--project',default='default')
    parser.add_argument('--token-file',type=Path);parser.add_argument('--suite',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--suite-id')
    parser.add_argument('--case',choices=CASES,action='append',dest='categories')
    parser.add_argument('--seed',type=int,choices=SEEDS,action='append',dest='seeds')
    parser.add_argument('--attempt-label',help='Explicit new execution attempt against identical frozen scenarios and criteria; previous jobs and reports remain unchanged')
    args=parser.parse_args();token=args.token_file.read_text().strip() if args.token_file else os.environ.get('ADE2E_API_TOKEN')
    api=API(args.api,token)
    if args.action=='author':
        if args.attempt_label:parser.error('--attempt-label applies only to run')
        if args.output.exists():parser.error('Author output exists; preserve the prior suite')
        result=author(api,args.project,args.output,args.suite_id,args.seeds or SEEDS,args.categories or CASES)
        print(json.dumps({'output':str(args.output),'suite_sha256':result['suite_sha256'],'authored_cases':len(result['cases']),'native_execution_submitted':False}))
    else:
        if args.suite is None:parser.error('run requires --suite')
        if args.output.resolve()==args.suite.resolve():parser.error('Result must not replace frozen suite')
        result=run(api,json.loads(args.suite.read_bytes()),args.output,args.categories,args.seeds,attempt_label=args.attempt_label)
        print(json.dumps({'output':str(args.output),'complete':result['complete'],'trials':len(result['trials']),'stop_reason':result.get('stop_reason')}))


if __name__=='__main__':main()
