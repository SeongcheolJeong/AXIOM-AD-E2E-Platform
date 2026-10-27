"""HTTP connector to actual retained RAW processing, with verified evidence closure.

No CameraE2E or numerical optics imports enter the platform core. The external
service owns RAW/ISP validation. This adapter verifies its frozen plan, complete
export and every retained native artifact instead of inventing physical claims.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request,HTTPRedirectHandler,build_opener
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile

MAX_JSON=8*1024**2
MAX_NATIVE=256*1024**2
MAX_NATIVE_FILES=256

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('CameraE2E API redirects are not permitted')

urlopen=build_opener(NoRedirect()).open

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def _artifact_descriptor(artifact):
    if not isinstance(artifact,dict):raise ValueError('Invalid external artifact descriptor')
    identity=artifact.get('id');name=artifact.get('name');sha=artifact.get('sha256');size=artifact.get('bytes');media=artifact.get('media_type')
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',identity):raise ValueError('Invalid external artifact identity')
    if not isinstance(name,str) or not name or '/' in name or '\\' in name or len(name)>140:raise ValueError('Unsafe external artifact name')
    if not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha):raise ValueError('Invalid external artifact SHA-256')
    if type(size) is not int or size<=0 or size>MAX_NATIVE:raise ValueError('External native artifact exceeds its accepted bound')
    if not isinstance(media,str) or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+',media):raise ValueError('Invalid external artifact media type')
    return identity,name,sha,size

def _references(value):
    if isinstance(value,dict):
        if {'id','name','sha256','bytes'}<=set(value):
            yield (value['id'],value['name'],value['sha256'],value['bytes'])
        for item in value.values():yield from _references(item)
    elif isinstance(value,list):
        for item in value:yield from _references(item)

def _forecast(frozen,cost):
    if not isinstance(cost,dict) or cost!=frozen.get('cost'):raise ValueError('External cost differs from the pinned comparison')
    cases=1+len(frozen['candidates']);shape=frozen['base_replay']['source']['raw_shape']
    if not isinstance(shape,list) or len(shape)!=3 or any(type(item) is not int or item<=0 for item in shape):raise ValueError('Invalid retained RAW frame geometry')
    frames=shape[0];arrays=cost.get('stored_arrays_per_case')
    if not 2<=cases<=13 or frames>16 or arrays not in {2,4}:raise ValueError('Unsupported retained comparison workload')
    if cost.get('processor_invocations')!=cases or cost.get('candidate_count')!=cases-1 or cost.get('max_preview_count')!=cases*frames:
        raise ValueError('External frame/candidate workload differs from its frozen plan')
    inputs=frozen['input_artifacts']
    if not isinstance(inputs,list):raise ValueError('External input closure is missing')
    unique={}
    for item in inputs:
        identity,_,sha,size=_artifact_descriptor(item)
        if identity in unique and unique[identity]!=(sha,size):raise ValueError('External input aliases disagree about their bytes')
        unique[identity]=(sha,size)
    file_bound=len(unique)+cases*(arrays+frames)+3  # summary, export, dispatch receipt
    if file_bound>MAX_NATIVE_FILES:raise ValueError('Retained RAW/ISP evidence exceeds the preflight native file bound of 256')
    fields=('source_artifact_bytes','output_array_bytes_bound','png_bytes_bound','summary_bytes_limit')
    if any(type(cost.get(name)) is not int or cost[name]<0 for name in fields):raise ValueError('External byte forecast is invalid')
    byte_bound=sum(cost[name] for name in fields)
    if byte_bound>MAX_NATIVE:raise ValueError('Retained RAW/ISP evidence exceeds its preflight native byte bound')
    return {'native_file_count_bound':file_bound,'native_artifact_bytes_bound':byte_bound,
            'native_total_bytes_bound':byte_bound+MAX_JSON+16384}

def _verify_export(snapshot,identity,export):
    comparison=export.get('comparison')
    if not isinstance(comparison,dict) or comparison.get('id')!=identity or comparison.get('plan_hash')!=snapshot['external_plan_hash']:
        raise ValueError('External export lost the pinned execution/plan identity')
    expected=snapshot['external_frozen_request']
    observed={key:comparison.get(key) for key in expected}
    if observed!=expected or hashlib.sha256(canonical(observed)).hexdigest()!=snapshot['external_plan_hash']:
        raise ValueError('External export changed its frozen input/profile/provenance closure')
    summary=comparison['result']['summary']
    if (summary.get('plan_hash')!=snapshot['external_plan_hash'] or summary.get('source_id')!=expected['source_id']
            or summary.get('selector')!=expected['selector'] or summary.get('isp_model_id')!=expected['isp_model_id']
            or summary.get('raw_sha256')!=snapshot['raw_sha256'] or summary.get('selected_array_sha256')!=snapshot['selected_array_sha256']):
        raise ValueError('External ISP processing changed the original selected RAW binding')
    planned=[{'candidate_id':'baseline','profile':expected['baseline_profile']},*expected['candidates']]
    actual=[summary.get('baseline'),*summary.get('candidates',[])]
    if len(planned)!=len(actual):raise ValueError('External result changed its planned candidate count')
    for before,after in zip(planned,actual):
        if (not isinstance(after,dict) or before['candidate_id']!=after.get('candidate_id') or before['profile']!=after.get('profile')
                or after.get('profile_hash')!=hashlib.sha256(canonical(before['profile'])).hexdigest()):
            raise ValueError('External result changed its candidate profile identity')
        required_stages={'rgb_frames','pre_tone_code_rgb_frames'}
        if expected['cost']['stored_arrays_per_case']==4:required_stages|={'ccm_code_rgb_frames','temporal_gate_mask_frames'}
        arrays=after.get('arrays')
        if not isinstance(arrays,dict) or set(arrays)!=required_stages:raise ValueError('External candidate is missing its actual native ISP stage arrays')
        shape=expected['base_replay']['source']['raw_shape']
        for stage,descriptor in arrays.items():
            if (descriptor.get('shape')!=(shape if stage=='temporal_gate_mask_frames' else shape+[3])
                    or descriptor.get('dtype')!=('bool' if stage=='temporal_gate_mask_frames' else 'float64')):
                raise ValueError('External candidate native array geometry/type differs from selected RAW')
    if summary.get('physical_validation')!='not_established' or summary.get('model_promotion') is not False:
        raise ValueError('External result exceeded the accepted software-only/no-model-promotion evidence scope')
    artifacts=export.get('artifacts')
    if not isinstance(artifacts,list):raise ValueError('External native evidence closure is missing')
    descriptors={_artifact_descriptor(item) for item in artifacts}
    required=set(_references([expected['input_artifacts'],expected['source_export'],expected['base_replay'],summary]))
    if not required<=descriptors:raise ValueError('External export omitted required source, stage or candidate evidence')
    unique={item[0] for item in descriptors}
    if len(unique)+2>snapshot['workload']['native_file_count_bound'] or len(unique)+2>MAX_NATIVE_FILES:
        raise ValueError('External native closure exceeded its accepted file forecast')
    return summary

class RetainedISPHandler:
    def __init__(self,*,database=None):
        self.url=os.environ.get('AXIOM_CAMERA_E2E_URL','').rstrip('/')
        self.token=os.environ.get('AXIOM_CAMERA_E2E_TOKEN','')

    def metadata(self):
        return ExperimentHandlerMetadata(id='camera-e2e-retained-isp',version='1',kinds=['isp_compare'],
            status='available' if self.url else 'not_configured',fidelity='external_retained_raw_code_domain_isp',
            resource_id='camera-e2e-isp',cancellation_supported=False,
            description='CameraE2E original saved RAW and actual supported ISP filter comparisons; external provenance retained, physical calibration not inferred.')

    def _request(self,path,body=None,*,file=None,limit=MAX_JSON):
        parsed=urlsplit(self.url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('CameraE2E connector requires the explicitly configured loopback API (SSH forwarding may provide it)')
        if not self.token and body is not None:
            # The local service publishes a process-lifetime CSRF token through
            # bootstrap. Keep it in memory; never pin or export the token.
            bootstrap=self._request('/api/v1/bootstrap')
            self.token=bootstrap.get('token','')
            if not self.token:raise ValueError('External local platform token is unavailable')
        headers={'Content-Type':'application/json'}
        if body is not None:headers['x-platform-token']=self.token
        request=Request(self.url+path,data=canonical(body) if body is not None else None,headers=headers)
        with urlopen(request,timeout=30) as response:
            if file is None:
                raw=response.read(limit+1)
                if len(raw)>limit:raise ValueError('External metadata exceeds the accepted bound')
                return json.loads(raw)
            sha=hashlib.sha256();total=0
            with Path(file).open('xb') as target:
                for chunk in iter(lambda:response.read(1024*1024),b''):
                    total+=len(chunk)
                    if total>limit:raise ValueError('External native artifact exceeds the accepted bound')
                    target.write(chunk);sha.update(chunk)
            return {'sha256':sha.hexdigest(),'size_bytes':total}

    def sources(self):return self._request('/api/v1/retained-isp/sources')

    def prepare(self,spec):
        if spec.project_id!='default':raise ValueError('External CameraE2E assets currently belong to the default project; explicit project binding is required elsewhere')
        if spec.kind!='isp_compare' or spec.comparison_kind!='same_raw_isp_ab' or spec.model is not None or spec.sensor is not None or spec.evaluator is not None:
            raise ValueError('Retained ISP adapter requires same_raw_isp_ab with no unconnected model, sensor or accuracy evaluator')
        if set(spec.input)!={'specification'}:raise ValueError('Use one explicit versioned CameraE2E specification')
        proposal=self._request('/api/v1/retained-isp/comparisons/plan',spec.input['specification'])
        frozen=proposal['frozen_request']
        if hashlib.sha256(canonical(frozen)).hexdigest()!=proposal['plan_hash']:
            raise ValueError('External RAW/ISP plan digest differs from its frozen definition')
        source=frozen['base_replay']['source']
        if not re.fullmatch(r'[a-f0-9]{64}',source['raw_sha256']) or not re.fullmatch(r'[a-f0-9]{64}',source['selected_array_sha256']):
            raise ValueError('External RAW source did not pin original and selected array hashes')
        raw_artifact=source.get('raw_artifact')
        _artifact_descriptor(raw_artifact)
        if raw_artifact['sha256']!=source['raw_sha256']:raise ValueError('Original retained RAW blob differs from its pinned source hash')
        forecast=_forecast(frozen,proposal['cost'])
        return {'schema_version':'camera-e2e-isp-input/1','specification':spec.input['specification'],
            'external_plan_hash':proposal['plan_hash'],'external_frozen_request':frozen,'raw_sha256':source['raw_sha256'],
            'selected_array_sha256':source['selected_array_sha256'],'seed':spec.seed,
            'external_origin_hash':hashlib.sha256(self.url.encode()).hexdigest(),'workload':proposal['cost']|forecast,
            'limitations':['Original RAW source provenance determines measured/simulated/estimated scope.',
                'Same saved RAW and supported code-domain ISP filters; baseline is not independent truth.',
                'No detector accuracy without paired reviewed labels and a connected fixed evaluation model.',
                'No physical camera validation, inverse RAW reconstruction or commercial dataset rights are inferred.']}

    def execute(self,snapshot,work_dir,should_cancel):
        if hashlib.sha256(self.url.encode()).hexdigest()!=snapshot['external_origin_hash']:raise ValueError('Configured CameraE2E service changed after preview')
        if should_cancel():raise AdapterCancelled('ISP comparison cancelled before external dispatch')
        fresh=self._request('/api/v1/retained-isp/comparisons/plan',snapshot['specification'])
        if (fresh['plan_hash']!=snapshot['external_plan_hash'] or fresh.get('frozen_request')!=snapshot['external_frozen_request']
                or hashlib.sha256(canonical(fresh['frozen_request'])).hexdigest()!=fresh['plan_hash']):
            raise ValueError('External RAW/profile/environment changed after preview')
        work_dir=Path(work_dir);work_dir.mkdir(parents=True,exist_ok=True)
        journal_path=work_dir/'external-dispatch.json'
        journal={'schema_version':'camera-e2e-dispatch/1','external_plan_hash':snapshot['external_plan_hash'],'status':'dispatching','cleanup_status':'unknown'}
        journal_path.write_bytes(canonical(journal))
        job=self._request('/api/v1/retained-isp/comparisons',{'specification':snapshot['specification'],'plan_hash':snapshot['external_plan_hash']})
        identity=job['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',identity):raise ValueError('Invalid external execution ID')
        journal.update(external_execution_id=identity,status=job['status']);journal_path.write_bytes(canonical(journal))
        deadline=time.monotonic()+600
        while job['status'] in {'queued','running','cancel_requested'}:
            if time.monotonic()>deadline:raise RuntimeError('External ISP comparison timed out; retained external execution requires inspection')
            if should_cancel():
                self._request(f'/api/v1/retained-isp/comparisons/{identity}/cancel',{})
                journal['status']='cancel_requested';journal_path.write_bytes(canonical(journal))
                raise RuntimeError('External cancellation was requested but terminal stop is unconfirmed')
            time.sleep(0.2)
            job=self._request(f'/api/v1/retained-isp/comparisons/{identity}')
        if job['status'] not in {'succeeded','completed'}:raise RuntimeError('External ISP processing failed; no successful processing or quality verdict is inferred')
        export=self._request(f'/api/v1/retained-isp/comparisons/{identity}/export')
        summary=_verify_export(snapshot,identity,export)
        outputs=[];total=0;seen={};receipts=[]
        for index,artifact in enumerate(export['artifacts']):
            identity_a,name,expected_sha,size=_artifact_descriptor(artifact)
            # CameraE2E blob IDs are content addressed. Different source/stage
            # names can legitimately refer to the same bytes. Preserve all
            # aliases in the unmodified export, transfer each blob once.
            if identity_a in seen:
                previous=seen[identity_a]
                if previous['sha256']!=artifact['sha256'] or previous['size_bytes']!=artifact['bytes']:
                    raise ValueError('External artifact aliases disagree about their bytes')
                previous['aliases'].append(artifact['name'])
                continue
            total+=size
            if total>MAX_NATIVE or total>snapshot['workload']['native_artifact_bytes_bound']:raise ValueError('External evidence closure exceeds its accepted bound')
            target=work_dir/f'{index:03d}-{name}'
            observed=self._request(f'/api/v1/artifacts/{identity_a}',file=target,limit=size)
            if observed!={'sha256':artifact['sha256'],'size_bytes':size}:raise ValueError('External artifact content differs from the sealed export')
            record={'external_id':identity_a,'local_name':target.name,'aliases':[name],**observed}
            seen[identity_a]=record;receipts.append(record)
            outputs.append(ExperimentOutputFile(target,target.name,artifact['media_type']))
        exported=work_dir/'camera-e2e-export.json';exported.write_bytes(canonical(export))
        journal.update(status='completed',cleanup_status='confirmed',external_export_sha256=hashlib.sha256(canonical(export)).hexdigest())
        journal_path.write_bytes(canonical(journal))
        outputs.extend([ExperimentOutputFile(exported,exported.name,'application/json'),ExperimentOutputFile(journal_path,journal_path.name,'application/json')])
        return ExperimentExecution(summary={'schema_version':'camera-e2e-isp-result/1','external_execution_id':identity,
            'external_plan_hash':snapshot['external_plan_hash'],'raw_sha256':snapshot['raw_sha256'],
            'selected_array_sha256':snapshot['selected_array_sha256'],'execution_status':'completed',
            'evaluation_status':'diagnostic_only','verification_verdict':'not_evaluated','external_summary':summary,
            'native_artifact_receipts':receipts,'limitations':snapshot['limitations']},artifacts=outputs,
            fidelity='external_retained_raw_code_domain_isp')
