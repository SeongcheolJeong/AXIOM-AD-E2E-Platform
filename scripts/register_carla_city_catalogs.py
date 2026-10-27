#!/usr/bin/env python3
"""Register captured native map/blueprint metadata through the versioned API.

This does not load a map, spawn actors or infer redistribution rights. Capture
files come from the optional city_native.py --catalog operator command.
"""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalogs',nargs='+',type=Path)
    parser.add_argument('--api',default='http://127.0.0.1:8031/api/v1')
    parser.add_argument('--project',default='default')
    parser.add_argument('--token-file',type=Path)
    args=parser.parse_args();token=args.token_file.read_text().strip() if args.token_file else os.environ.get('ADE2E_API_TOKEN')
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    def request(path,body=None):
        data=None if body is None else json.dumps(body).encode()
        req=urllib.request.Request(args.api.rstrip('/')+path,data=data,headers=headers)
        with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
    def register(kind,identity,definition,*,preserve_existing=False):
        items=request('/'+kind+'?project_id='+args.project+'&limit=1000')['items']
        current=next((item for item in items if item['id']==identity),None)
        if preserve_existing and current:return {k:current[k] for k in ('id','revision','sha256')}
        value=request('/'+kind,{'project_id':args.project,'id':identity,'expected_revision':current['revision'] if current else 0,'definition':definition})
        return {k:value[k] for k in ('id','revision','sha256')}
    refs={'maps':[],'assets':[],'controllers':[]};seen=set()
    for path in args.catalogs:
        data=json.loads(path.read_bytes());definition=data['map']
        identity='carla-'+definition['name'].lower().replace('_','-')
        refs['maps'].append(register('maps',identity,definition))
        for asset in data['assets']:
            identity=re.sub('[^A-Za-z0-9_-]','-',asset['blueprint_id'])
            if identity in seen:continue
            seen.add(identity);refs['assets'].append(register('assets',identity,asset))
    for mode in ('route_follow','traffic','timed_replay','static','autoware'):
        configured=mode!='autoware'
        definition={'resource_kind':'controller','name':{'route_follow':'Native lane route feedback','traffic':'Lead-gap and native signal response','timed_replay':'Timed position replay (no physical tracking)','static':'Stationary native asset','autoware':'Autoware GT planning/control (requires native qualification)'}[mode],
            'adapter_id':'carla-city-object','adapter_version':'carla-city-object/1.0.0','mode':mode,'status':'available' if configured else 'not_configured',
            'description':'Explicit '+mode+' ownership; native qualification scope is preserved separately from authoring.',
            'capabilities':[] if not configured else ['end_stop','native_trace']+(['control_math:axiom-city-pure-pursuit/2','rear_axle_steering','preserved_origin_metrics'] if mode in ('route_follow','traffic') else []),
            'image_digest':'sha256:00aae4988d137f042de42e744d24616054c9d1917c6b64562eabae3eb6a6fe17' if mode=='autoware' else None,
            'rights':{'license':'Apache-2.0' if mode=='autoware' else 'Workspace implementation; see repository licensing',
                'source_url':'https://github.com/autowarefoundation/autoware' if mode=='autoware' else 'local:axiom-city-control',
                'redistribution':'unknown','notes':'Code rights do not cover engine content, maps or external datasets.'}}
        # Catalog discovery must not replace operator-pinned implementations or
        # turn an already configured Autoware controller back off.
        refs['controllers'].append(register('controllers','city-'+mode.replace('_','-'),definition,preserve_existing=True))
    print(json.dumps(refs,indent=2))


if __name__=='__main__':main()
