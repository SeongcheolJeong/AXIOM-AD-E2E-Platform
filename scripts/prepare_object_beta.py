#!/usr/bin/env python3
"""Prepare pinned Object Sim beta templates and run explicit native A/B trials.

Preparation imports observed catalogs and selected native graph paths through
normal APIs. It never starts an engine or submits a simulation implicitly.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import time
import uuid

from carla_city_acceptance import API, digest, ref, pose, write, now, length

PRESETS=('lead_stop_start','cut_in','urban_left_turn','urban_right_turn')
TERMINAL={'completed','failed','cancelled','interrupted','rejected'}


def scenario_ref(value):
    return {k:value[k] for k in ('id','revision','configuration_hash','manifest_hash')}


def forward(nodes,start,metres):
    result=[start];travel=0.;seen={start['id']}
    while travel<metres:
        choices=[nodes[i] for i in result[-1]['next_ids'] if i not in seen]
        if not choices:break
        previous=result[-1]
        def heading_difference(n):
            delta=n['heading_rad']-previous['heading_rad']
            return abs(math.atan2(math.sin(delta),math.cos(delta))),n['id']
        nxt=min(choices,key=heading_difference)
        travel+=math.hypot(nxt['x']-previous['x'],nxt['y']-previous['y'])
        result.append(nxt);seen.add(nxt['id'])
    return result


def definitions(native_map,vehicle,selected_paths,right_source):
    nodes={n['id']:n for n in native_map['definition']['nodes']}
    paths={k:[nodes[n['id']] for n in v] for k,v in selected_paths.items()}
    def actor(identity,path,speed=4):
        route=[pose(n) for n in path]
        return {'id':identity,'role':'ego' if identity=='ego' else 'other','kind':'vehicle',
            'asset':ref(vehicle),'pose':route[0],'route':route,'destination':route[-1],
            'speed_mps':speed,'controller':{'kind':'reactive','end_behavior':'stop','obey_signals':True},
            'behavior':{'schema_version':'object-behavior/1','risk_mode':False,'actions':[]},
            'planner':{'schema_version':'object-planner/1','allow_lane_changes':False}}
    def scene(name,actors,duration=30):
        return {'schema_version':'scenario-v2/3','name':name,
            'description':'CARLA native low-speed object/map reference control. Behavior exercise and ego outcome are separate observations.',
            'seed':42,'map':ref(native_map),'duration_s':duration,'timestep_s':.05,'actors':actors,
            'sensors':[{'id':identity,'actor_id':'ego','kind':'camera_rgb',
                'pose':{'x':x,'y':0,'z':z,'heading_rad':0},'width':640,'height':360,'fov_deg':90,'period_s':.1}
                for identity,x,z in [('front',2.8,1.7),('chase',-8,3)]],
            'signals':[{'id':s['id'],'initial_state':'green','changes':[]} for s in native_map['definition']['signals'] if s['kind']=='traffic_light'],
            'acceptance':{'max_lateral_error_p95_m':.75,'max_goal_error_m':2,'max_final_speed_mps':.2,'require_goal':True,'require_no_contacts':True}}
    straight=paths['straight'];extended=straight+forward(nodes,straight[-1],20)[1:]
    index=next(i for i in range(1,len(straight)) if length(straight[:i+1])>=12)
    lead=actor('lead',extended[index:])
    lead['behavior']['actions']=[{'id':'lead_wait','kind':'wait','trigger':{'kind':'time','value':2,'operator':'gte'},'duration_s':4,'timeout_s':14,'on_timeout':'fail'}]
    values={'lead_stop_start':{'scenario':scene('앞차 정지·재출발',[actor('ego',straight),lead]),'actor_roles':{'ego':'ego','lead':'lead'}}}
    # Native same-direction edges and permissions are required, not inferred from proximity.
    base=paths['cut_in'];base=base+forward(nodes,base[-1],40)[1:]
    index=next(i for i in range(1,len(base)) if length(base[:i+1])>=12)
    target=base[index]
    candidates=[nodes[target[s+'_id']] for s in ('left','right') if target.get(s+'_id')]
    start=next((n for n in candidates if any(n.get(s+'_id') and nodes[n[s+'_id']]['lane_id']==target['lane_id'] and n.get('lane_change_'+s) for s in ('left','right'))),None)
    if start is None:raise ValueError('Selected cut-in path lacks observed legal adjacent-lane permission')
    intruder=actor('intruder',forward(nodes,start,64),3)
    intruder['destination']=pose(base[-1]);intruder['planner']['allow_lane_changes']=True
    intruder['behavior']={'schema_version':'object-behavior/1','risk_mode':True,'actions':[{
        'id':'cut_in','kind':'cut_in','trigger':{'kind':'time','value':1,'operator':'gte'},
        'target_lane_node_id':target['id'],'duration_s':0,'timeout_s':28,'on_timeout':'fail'}]}
    ego_end=next(i for i in range(1,len(base)) if length(base[:i+1])>=60)
    values['cut_in']={'scenario':scene('주변 차량 끼어들기',[actor('ego',base[:ego_end+1]),intruder],40),'actor_roles':{'ego':'ego','intruder':'intruder'}}
    values['urban_left_turn']={'scenario':scene('도심 좌회전·교차 차량',[actor('ego',paths['left_turn']),actor('crossing',paths['crossing'])],35),'actor_roles':{'ego':'ego','crossing':'crossing'}}
    right=copy.deepcopy(right_source['scenario']);right['map']=ref(native_map)
    for a in right['actors']:a['asset']=ref(vehicle)
    right['sensors']=scene('',[])['sensors']
    values['urban_right_turn']={'scenario':right,'actor_roles':right_source['actor_roles']}
    return values


def prepare(args,api):
    catalog=json.loads(args.catalog.read_text());source=json.loads(args.source.read_text());paths=json.loads(args.paths.read_text())
    fingerprint=digest({'catalog':catalog,'source':source,'paths':paths})
    if args.output.exists():
        receipt=json.loads(args.output.read_text())
        if receipt['source_hash']!=fingerprint or receipt['api']!=args.api:raise ValueError('Preparation receipt pins another source/API')
    else:
        project=args.project or api.request('/projects',{'name':args.name})['id']
        receipt={'schema_version':'object-beta-preparation/1','created_at':now(),'api':args.api,'source_hash':fingerprint,'project_id':project,'presets':{},'catalogs':{}}
        write(args.output,receipt)
    project=receipt['project_id']
    def register(kind,identity,definition):
        key=kind+'/'+identity
        if key not in receipt['catalogs']:
            receipt['catalogs'][key]=api.request('/'+kind,{'project_id':project,'id':identity,'expected_revision':0,'definition':definition})
            write(args.output,receipt)
        return receipt['catalogs'][key]
    native_map=register('maps','carla-town10hd-opt-beta',catalog['map'])
    selected=next(a for a in catalog['assets'] if a['blueprint_id']=='vehicle.toyota.prius')
    vehicle=register('assets','vehicle-toyota-prius',selected)
    values=definitions(native_map,vehicle,paths,source['right_turn'])
    for identity,value in values.items():
        if identity in receipt['presets']:continue
        saved=api.request('/scenario-v2',{'project_id':project,'scenario':value['scenario']},'beta-prepare:'+project+':'+identity+':'+digest(value['scenario'])[:16])
        bound=api.request('/object-sim/presets/'+identity+'/bind',{'project_id':project,'expected_revision':0,'scenario_ref':scenario_ref(saved),'actor_roles':value['actor_roles']})
        receipt['presets'][identity]={'scenario_ref':scenario_ref(saved),'actor_roles':value['actor_roles'],'binding_revision':bound['revision']}
        write(args.output,receipt)
    receipt.update(complete=True,completed_at=now());write(args.output,receipt)
    return {'project_id':project,'presets':list(receipt['presets']),'complete':True}


def rehearse(args,api):
    prepared=json.loads(args.prepared.read_text());project=prepared['project_id']
    if prepared['api']!=args.api or not prepared.get('complete'):raise ValueError('Use a complete preparation for this API')
    result=json.loads(args.output.read_text()) if args.output.exists() else {'schema_version':'object-beta-native/1','id':uuid.uuid4().hex,'project_id':project,'trials':[]}
    if result['project_id']!=project:raise ValueError('Receipt belongs to another project')
    no_publish=getattr(args,'no_publish',False)
    result.update(completion_scope='execution_completion_only_not_behavior_acceptance',
                  behavior_review_status='pending',publication_mode='review_only' if no_publish else 'automatic_after_execution')
    result.setdefault('complete',False)
    result.setdefault('execution_complete',False)
    result.setdefault('published_presets',[])
    variants={'lead_stop_start':({'lead_wait_s':2},{'lead_wait_s':4}), 'cut_in':({'cut_in_start_s':1},{'cut_in_start_s':2}),
        'urban_left_turn':({'crossing_speed_mps':4},{'crossing_speed_mps':3}), 'urban_right_turn':({'lead_wait_s':0},{'lead_wait_s':6})}
    for identity in args.presets or PRESETS:
        for side,parameters in zip(('A','B'),variants[identity]):
            key=identity+':'+side;trial=next((t for t in result['trials'] if t['key']==key),None)
            if trial is None:
                preview=api.request('/object-sim/presets/'+identity+'/preview',{'project_id':project,'parameters':parameters})
                saved=api.request('/scenario-v2',{'project_id':project,'scenario':preview['scenario']},result['id']+key+':scenario')
                spec={'project_id':project,'name':identity+' '+side,'kind':'object_sim','adapter_id':'carla-city-object','adapter_version':'carla-city-object/1.0.0','seed':saved['scenario']['seed'],'input':{'scenario_id':saved['id'],'scenario_revision':saved['revision'],'configuration_hash':saved['configuration_hash']}}
                experiment=api.request('/experiments',spec,result['id']+key+':experiment')
                job=api.request('/experiments/'+experiment['id']+'/executions',{'preview_hash':experiment['preview_hash']},result['id']+key+':execution')
                trial={'key':key,'preset':identity,'side':side,'parameters':parameters,'scenario_ref':scenario_ref(saved),'execution_id':job['id']};result['trials'].append(trial);write(args.output,result)
                print(json.dumps({'submitted':key,'execution_id':job['id']}),flush=True)
            deadline=time.monotonic()+args.timeout
            while True:
                job=api.request('/experiments/executions/'+trial['execution_id'])
                trial.update({k:job.get(k) for k in ('status','error','summary','manifest_hash','outcome_uncertain')});write(args.output,result)
                if job['status'] in TERMINAL:break
                if time.monotonic()>deadline:raise TimeoutError('Execution preserved; resume using same receipt')
                time.sleep(3)
            print(json.dumps({'finished':key,'status':job['status'],'error':job.get('error')}),flush=True)
            summary=job.get('summary') or {}
            trial.update(execution_complete=job['status']=='completed' and job.get('outcome_uncertain') is False,
                         evaluation_status=summary.get('evaluation_status','unavailable'),
                         verification_verdict=summary.get('verification_verdict','not_evaluated'),
                         behavior_review_status='pending')
            write(args.output,result)
            report=api.request('/object-sim/executions/'+trial['execution_id']+'/behavior?project_id='+project)
            trial['behavior_report']=report
            write(args.output,result)
            if (report.get('schema_version')!='object-sim-behavior/1' or report.get('project_id')!=project
                    or report.get('execution_id')!=trial['execution_id'] or report.get('scenario_ref')!=trial['scenario_ref']):
                raise ValueError('Behavior report differs from the exact trial project/execution/scenario revision')
            if report.get('status')=='available':
                source=report.get('source') or {}
                if (source.get('execution_id')!=trial['execution_id'] or source.get('manifest_hash')!=job.get('manifest_hash')
                        or source.get('scenario_ref')!=trial['scenario_ref'] or not source.get('artifact_sha256')):
                    raise ValueError('Behavior report source differs from the sealed trial evidence')
            if job['status']!='completed':raise RuntimeError('Native execution failed; inspect retained evidence before proceeding')
            if job.get('outcome_uncertain') is not False:raise RuntimeError('Native execution outcome is uncertain; inspect retained evidence before proceeding')
        a,b=[next(t for t in result['trials'] if t['key']==identity+':'+s) for s in ('A','B')]
        if no_publish:continue
        # Each binding's baseline must match its exact scenario revision.
        item=next(i for i in api.request('/object-sim/presets?project_id='+project)['items'] if i['id']==identity)
        api.request('/object-sim/presets/'+identity+'/bind',{'project_id':project,'expected_revision':item['binding_revision'],
            'scenario_ref':b['scenario_ref'],'actor_roles':prepared['presets'][identity]['actor_roles'],
            'saved_execution_id':b['execution_id'],'saved_comparison_execution_id':a['execution_id']})
        if identity not in result['published_presets']:result['published_presets'].append(identity)
        write(args.output,result)
    result['execution_complete']=len(result['trials'])==8 and all(t.get('execution_complete') for t in result['trials'])
    result['complete']=result['execution_complete'];write(args.output,result)
    return {'complete':result['complete'],'trials':len(result['trials'])}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for command in ('prepare','rehearse'):
        c=sub.add_parser(command);c.add_argument('--api',default='http://127.0.0.1:8063/api/v1');c.add_argument('--token-file',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
        if command=='prepare':
            c.add_argument('--catalog',type=Path,required=True);c.add_argument('--source',type=Path,required=True);c.add_argument('--paths',type=Path,required=True);c.add_argument('--project');c.add_argument('--name',default='Object Sim Beta · curator')
        else:
            c.add_argument('--prepared',type=Path,required=True);c.add_argument('--presets',nargs='+',choices=PRESETS);c.add_argument('--timeout',type=float,default=900)
            c.add_argument('--no-publish',action='store_true',help='Retain exact-pinned native reports for review without updating preset baseline bindings')
    a=p.parse_args();api=API(a.api,a.token_file.read_text().strip());print(json.dumps((prepare if a.command=='prepare' else rehearse)(a,api),ensure_ascii=False))


if __name__=='__main__':main()
