"""Generate callable tools from the same versioned OpenAPI used by the UI."""
import copy
from .team_access import TEAM_PREFIXES

READ_ONLY_POST = {'/driving-policies/preflight','/driving-policies/inspect-result',
    '/experiments/preview','/verification-plans/preview','/scene-sequences/preview',
    '/replay-specs/{identifier}/preflight','/replay-specs/{replay_spec_id}/preflight'}

def catalog(app,request,legacy_tools,usage_notes):
    schema=app.openapi();overrides={(t['method'].lower(),t['path']):t for t in legacy_tools}
    principal=request.state.principal;result=[]
    for path,operations in sorted(schema['paths'].items()):
        if not path.startswith('/api/v1/') or path=='/api/v1/agent/tools':continue
        for method,operation in operations.items():
            if method not in {'get','post','patch','delete','put'}:continue
            if principal.mode=='project':
                if operation.get('x-axiom-operator-only',False):continue
                allowed=path=='/api/v1/team/whoami' or any(path==prefix or path.startswith(prefix+'/') for prefix in TEAM_PREFIXES)
                if not allowed:continue
                if path=='/api/v1/projects' and method!='get':continue
                if '/tokens' in path and principal.role!='Admin':continue
                if method!='get':
                    required=operation.get('x-axiom-min-role','Engineer')
                    if required=='Admin' and principal.role!='Admin':continue
                    if required=='Reviewer' and principal.role not in {'Reviewer','Admin'}:continue
                    if required=='Engineer' and principal.role not in {'Engineer','Admin'}:continue
            tool=copy.deepcopy(overrides.get((method,path),{}))
            params=operation.get('parameters',[])
            request_schema={'type':'object','properties':{},'required':[]}
            for location in ('path','query','header'):
                group=[p for p in params if p.get('in')==location]
                if group:
                    request_schema['properties'][location]={'type':'object','properties':{p['name']:p.get('schema',{}) for p in group},
                        'required':[p['name'] for p in group if p.get('required')]}
                    if any(p.get('required') for p in group):request_schema['required'].append(location)
            body=operation.get('requestBody',{})
            if body:
                request_schema['properties']['body']=body.get('content',{}).get('application/json',{}).get('schema',{'type':'string','format':'binary'})
                if body.get('required'):request_schema['required'].append('body')
            tool.update(method=method.upper(),path=path,operation_id=operation.get('operationId'),
                openapi_input_schema=request_schema,
                request_content_types=list(body.get('content',{})),
                required_headers=sorted({'Idempotency-Key' if p['name'].lower()=='idempotency-key' else p['name'] for p in params if p.get('in')=='header' and p.get('required')}),
                authorization={'mode':principal.mode,'project_id':principal.project_id,'role':principal.role})
            tool.setdefault('name',operation.get('operationId',method+'_'+path.replace('/','_')))
            tool.setdefault('description',operation.get('description') or operation.get('summary','Versioned API operation'))
            tool.setdefault('input_schema',request_schema)
            tool.setdefault('read_only',method=='get' or path.removeprefix('/api/v1') in READ_ONLY_POST or operation.get('x-axiom-read-only',False))
            tool['idempotency_scope']=path if 'Idempotency-Key' in tool['required_headers'] else None
            result.append(tool)
    return {'schema_version':'agent-catalog/2','source':'current_openapi','tools':result,'usage_notes':usage_notes,
        'openapi_schema_version':schema.get('info',{}).get('version'),'components':schema.get('components',{}),
        'authorization':{'mode':principal.mode,'project_id':principal.project_id,'role':principal.role}}
