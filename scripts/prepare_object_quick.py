#!/usr/bin/env python3
"""Copy four pinned quick-demo inputs into an existing project, without execution."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import urllib.parse

from carla_city_acceptance import API, digest, now, ref
from object_sim_demo import write, scenario_ref

PRESETS=('obstacle_bypass','gap_release','junction_eight','urban_city')
BINDING_FIELDS=('binding_revision','scenario_ref','saved_execution_id','saved_comparison_execution_id')


def read_catalog(api,kind,project,pin):
    value=api.request('/'+kind+'/'+pin['id']+'?'+urllib.parse.urlencode({'project_id':project,'revision':pin['revision']}))
    if value['project_id']!=project or ref(value)!=pin:raise ValueError('Catalog differs from exact source pin: '+kind)
    return value


def verify_sources(api,selection):
    """Verify all source inputs before making any target mutation."""
    if set(selection['presets'])!=set(PRESETS):raise ValueError('Select exactly the four quick presets')
    project=selection['source_project_id'];scenarios={};catalogs={}
    def remember(kind,pin):
        key=kind+':'+digest(pin)
        if key not in catalogs:catalogs[key]=read_catalog(api,kind,project,pin)
        return catalogs[key]
    for identity,chosen in selection['presets'].items():
        pin=chosen['scenario_ref'];saved=api.request('/scenario-v2/'+pin['id']+'?revision='+str(pin['revision']))
        if (saved['project_id']!=project or scenario_ref(saved)!=pin or digest(saved['scenario'])!=pin['configuration_hash']):
            raise ValueError('Scenario differs from exact source pin: '+identity)
        scenarios[identity]=saved
        native=remember('maps',saved['scenario']['map'])
        if native['definition'].get('generated'):remember('object-maps',native['definition']['generated']['source'])
        for actor in saved['scenario']['actors']:
            remember('assets',actor['asset'])
            if actor['controller'].get('ref'):remember('controllers',actor['controller']['ref'])
    return scenarios,catalogs


def binding_pins(items):
    return {item['id']:{k:item.get(k) for k in BINDING_FIELDS} for item in items}


class CatalogCopy:
    def __init__(self,api,project,receipt,path,sources):
        self.api,self.project,self.receipt,self.path,self.sources=api,project,receipt,path,sources
        self.current={}
    def existing(self,kind):
        if kind not in self.current:
            values=self.api.items(kind,self.project)
            self.current[kind]={value['id']:read_catalog(self.api,kind,self.project,ref(value)) for value in values}
        return self.current[kind]
    def register(self,kind,original):
        definition=original['definition'];current=self.existing(kind)
        exact=next((value for value in current.values() if value['definition']==definition),None)
        if exact:return exact,'reused_exact_definition'
        identity=original['id']
        if identity in current:identity='quick-'+identity[:100]+'-'+digest(definition)[:12]
        if identity in current:raise ValueError('Namespaced catalog collision; no existing revision will be overwritten')
        created=self.api.request('/'+kind,{'project_id':self.project,'id':identity,'expected_revision':0,'definition':definition})
        if created['definition']!=definition:raise ValueError('Created catalog definition differs from source')
        current[identity]=created
        return created,'registered' if identity==original['id'] else 'registered_namespaced'
    def copy(self,kind,pin):
        key=kind+':'+digest(pin);old=self.receipt['catalogs'].get(key)
        if old:
            read_catalog(self.api,kind,self.project,old['target_ref'])
            return old['target_ref']
        original=self.sources[key];generated=original['definition'].get('generated') if kind=='maps' else None
        if generated:
            authored=self.copy('object-maps',generated['source'])
            # Build output identity is deterministic for this new project source.
            identity='authored-'+authored['id'][:36]+'-'+hashlib.sha256(authored['id'].encode()).hexdigest()[:8]
            existing=self.existing('maps').get(identity)
            if existing and (existing['definition'].get('generated') or {}).get('source')!=authored:
                raise ValueError('Generated map ID belongs to another source; existing revision preserved')
            if existing:created,operation=existing,'reused_generated_source'
            else:
                built=self.api.request('/object-maps/'+authored['id']+'/build',{
                    'project_id':self.project,'revision':authored['revision'],'sha256':authored['sha256'],
                    'expected_map_revision':0,'server_version':original['definition']['server_version']})
                created,operation=built['map'],'rebuilt_from_exact_authored_source'
                self.existing('maps')[created['id']]=created
            for field in ('opendrive','nodes','signals','spawn_points','rights','server_version'):
                if created['definition'][field]!=original['definition'][field]:
                    raise ValueError('Rebuilt map changed original geometry or provenance field: '+field)
            for field in ('compiler_id','compiler_version','compiler_sha256','road_ids'):
                if created['definition']['generated'][field]!=generated[field]:
                    raise ValueError('Rebuilt map compiler differs from the selected source: '+field)
        else:created,operation=self.register(kind,original)
        self.receipt['catalogs'][key]={'kind':kind,'source_ref':pin,'target_ref':ref(created),'operation':operation,
                                     'source_definition_sha256':digest(original['definition'])}
        write(self.path,self.receipt)
        return ref(created)


def prepare(args,source=None,target=None):
    source=source or API(args.source_api,args.source_token_file.read_text().strip())
    target=target or API(args.api,args.token_file.read_text().strip())
    selection=json.loads(args.selection.read_text())
    if selection.get('source_api',args.source_api)!=args.source_api:raise ValueError('Selection belongs to another source API')
    scenarios,catalogs=verify_sources(source,selection)
    before=target.request('/object-sim/presets?project_id='+args.project)['items']
    fingerprint=digest(selection)
    if args.output.exists():
        receipt=json.loads(args.output.read_text())
        if (receipt['selection_sha256'],receipt['target_api'],receipt['project_id'])!=(fingerprint,args.api,args.project):
            raise ValueError('Preparation receipt belongs to another selection/API/project')
    else:
        if any(item.get('binding_revision') is not None for item in before if item['id'] in PRESETS):
            raise ValueError('A requested quick preset is already bound; existing bindings will not be replaced')
        receipt={'schema_version':'object-sim-quick-preparation/1','created_at':now(),'selection_sha256':fingerprint,
            'source_api':args.source_api,'source_project_id':selection['source_project_id'],'target_api':args.api,
            'project_id':args.project,'presets':{},'catalogs':{},'native_execution_submitted':False,
            'preserved_bindings':{key:value for key,value in binding_pins(before).items() if key not in PRESETS},'complete':False}
        write(args.output,receipt)
    copier=CatalogCopy(target,args.project,receipt,args.output,catalogs)
    for identity in PRESETS:
        chosen=selection['presets'][identity]
        if identity in receipt['presets']:
            pin=receipt['presets'][identity]['scenario_ref'];value=target.request('/scenario-v2/'+pin['id']+'?revision='+str(pin['revision']))
            if value['project_id']!=args.project or scenario_ref(value)!=pin:raise ValueError('Prepared target scenario changed')
            continue
        scenario=copy.deepcopy(scenarios[identity]['scenario']);scenario['map']=copier.copy('maps',scenario['map'])
        for actor in scenario['actors']:
            actor['asset']=copier.copy('assets',actor['asset'])
            if actor['controller'].get('ref'):actor['controller']['ref']=copier.copy('controllers',actor['controller']['ref'])
        created=target.request('/scenario-v2',{'project_id':args.project,'scenario':scenario},
            'quick-prepare:'+args.project+':'+identity+':'+fingerprint[:16])
        current=next(item for item in target.request('/object-sim/presets?project_id='+args.project)['items'] if item['id']==identity)
        if current.get('binding_revision') is not None:
            if current['scenario_ref']!=scenario_ref(created):raise ValueError('Quick preset was bound concurrently; existing binding preserved')
            preview=target.request('/object-sim/presets/'+identity+'/preview',{'project_id':args.project})
            if preview['actor_roles']!=chosen['actor_roles']:raise ValueError('Existing quick preset roles differ')
            revision=current['binding_revision']
        else:
            bound=target.request('/object-sim/presets/'+identity+'/bind',{'project_id':args.project,'expected_revision':0,
                'scenario_ref':scenario_ref(created),'actor_roles':chosen['actor_roles']})
            revision=bound['revision']
        receipt['presets'][identity]={'scenario_ref':scenario_ref(created),'source_scenario_ref':chosen['scenario_ref'],
            'source_binding_revision':chosen.get('source_binding_revision'),'actor_roles':chosen['actor_roles'],
            'binding_revision':revision,'native_execution_submitted':False}
        write(args.output,receipt)
    after=binding_pins(target.request('/object-sim/presets?project_id='+args.project)['items'])
    if any(after.get(key)!=value for key,value in receipt['preserved_bindings'].items()):
        raise ValueError('Another preset binding changed during preparation; inspect before continuing')
    receipt.update(complete=True,completed_at=now());write(args.output,receipt)
    return {'complete':True,'project_id':args.project,'presets':list(receipt['presets']),'native_execution_submitted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('api','source-api','project'):parser.add_argument('--'+flag,required=True)
    for flag in ('token-file','source-token-file','selection','output'):parser.add_argument('--'+flag,type=Path,required=True)
    print(json.dumps(prepare(parser.parse_args()),ensure_ascii=False))


if __name__=='__main__':main()
