"""Authored HTTP contract fixtures; not ISP accuracy or physical validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from ade2e.experiment_schemas import ExperimentCreate
from axiom_camera_e2e.retained import RetainedISPHandler,canonical

def descriptor(identity,name,raw,**extra):
    return {'id':identity,'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'media_type':'application/octet-stream',**extra}

@pytest.fixture
def contract():
    payloads={'raw_blob':b'authored original RAW bytes','baseline_blob':b'authored baseline native array',
              'candidate_blob':b'authored candidate native array'}
    raw=descriptor('raw_blob','source-raw.npy',payloads['raw_blob'])
    baseline=descriptor('baseline_blob','baseline-rgb_frames.npy',payloads['baseline_blob'],shape=[2,2,2,3],dtype='float64')
    candidate=descriptor('candidate_blob','candidate-rgb_frames.npy',payloads['candidate_blob'],shape=[2,2,2,3],dtype='float64')
    baseline_pre=baseline|{'name':'baseline-pre_tone_code_rgb_frames.npy'}
    candidate_pre=candidate|{'name':'candidate-pre_tone_code_rgb_frames.npy'}
    specification={'source_id':'authored_source','selector':{'readout_id':'single'},'isp_model_id':'authored_model'}
    cost={'stored_arrays_per_case':2,'processor_invocations':2,'candidate_count':1,'max_preview_count':4,
          'source_artifact_bytes':len(payloads['raw_blob']),'output_array_bytes_bound':1024,
          'png_bytes_bound':1024,'summary_bytes_limit':4096}
    frozen={'source_id':'authored_source','selector':specification['selector'],'isp_model_id':'authored_model',
            'specification':specification,'base_replay':{'source':{'raw_sha256':raw['sha256'],
                'selected_array_sha256':'b'*64,'raw_artifact':raw,'raw_shape':[2,2,2],
                'source_kind':'authored_simulated','source_provenance':{'instrument':'none','claim':'contract_fixture'}}},
            'input_artifacts':[raw],'source_export':{'artifacts':[raw]},'baseline_profile':{'gain':1},
            'candidates':[{'candidate_id':'candidate-000','profile':{'gain':2}}],'cost':cost}
    plan_hash=hashlib.sha256(canonical(frozen)).hexdigest()
    summary={'plan_hash':plan_hash,'source_id':frozen['source_id'],'selector':frozen['selector'],'isp_model_id':frozen['isp_model_id'],
        'raw_sha256':raw['sha256'],'selected_array_sha256':'b'*64,'physical_validation':'not_established','model_promotion':False,
        'baseline':{'candidate_id':'baseline','profile':{'gain':1},'profile_hash':hashlib.sha256(canonical({'gain':1})).hexdigest(),
                    'arrays':{'rgb_frames':baseline,'pre_tone_code_rgb_frames':baseline_pre}},
        'candidates':[{'candidate_id':'candidate-000','profile':{'gain':2},'profile_hash':hashlib.sha256(canonical({'gain':2})).hexdigest(),
                       'arrays':{'rgb_frames':candidate,'pre_tone_code_rgb_frames':candidate_pre}}]}
    exported={'comparison':frozen|{'id':'external_job','plan_hash':plan_hash,'result':{'summary':summary}},
              'artifacts':[raw,baseline,baseline_pre,candidate,candidate_pre]}
    return {'frozen':frozen,'cost':cost,'plan_hash':plan_hash,'frozen_request':frozen,'export':exported,
            'payloads':payloads,'specification':specification}

class ContractHandler(RetainedISPHandler):
    def __init__(self,contract):
        self.url='http://127.0.0.1:8772';self.token='';self.contract=contract;self.calls=[]
    def _request(self,path,body=None,*,file=None,limit=8*1024**2):
        self.calls.append((path,deepcopy(body)))
        if path.endswith('/plan'):
            return {key:deepcopy(self.contract[key]) for key in ('frozen_request','plan_hash','cost')}
        if path.endswith('/export'):return deepcopy(self.contract['export'])
        if '/api/v1/artifacts/' in path:
            raw=self.contract['payloads'][path.rsplit('/',1)[1]]
            Path(file).write_bytes(raw)
            return {'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}
        return {'id':'external_job','status':'completed'}

def spec(contract,**overrides):
    return ExperimentCreate(name='Authored adapter contract',kind='isp_compare',adapter_id='camera-e2e-retained-isp',
        comparison_kind='same_raw_isp_ab',input={'specification':contract['specification']},**overrides)

def test_original_raw_provenance_and_content_addressed_alias_closure(contract,tmp_path):
    handler=ContractHandler(contract);prepared=handler.prepare(spec(contract));result=handler.execute(prepared,tmp_path,lambda:False)
    assert result.fidelity=='external_retained_raw_code_domain_isp'
    assert result.summary['verification_verdict']=='not_evaluated' and result.summary['evaluation_status']=='diagnostic_only'
    assert result.summary['raw_sha256']==contract['frozen']['base_replay']['source']['raw_sha256']
    assert len(result.artifacts)==5  # three unique blobs plus original export and dispatch receipt
    receipts=result.summary['native_artifact_receipts']
    assert len(receipts)==3 and len(receipts[1]['aliases'])==2
    exported=json.loads((tmp_path/'camera-e2e-export.json').read_text())
    assert len(exported['artifacts'])==5
    assert exported['comparison']['base_replay']['source']['source_provenance']['claim']=='contract_fixture'
    assert all('No physical camera validation' in item for item in prepared['limitations'] if item.startswith('No physical'))

@pytest.mark.parametrize('change',['plan_hash','profile','candidate_count','original_raw','missing_stage','missing_source','physical_scope','alias_disagreement'])
def test_changed_export_fails_before_native_publish(contract,tmp_path,change):
    handler=ContractHandler(contract);prepared=handler.prepare(spec(contract))
    exported=contract['export'];comparison=exported['comparison'];summary=comparison['result']['summary']
    if change=='plan_hash':comparison['plan_hash']='c'*64
    elif change=='profile':summary['candidates'][0]['profile']={'gain':99}
    elif change=='candidate_count':summary['candidates']=[]
    elif change=='original_raw':summary['raw_sha256']='c'*64
    elif change=='missing_stage':summary['baseline']['arrays'].pop('pre_tone_code_rgb_frames')
    elif change=='missing_source':exported['artifacts']=exported['artifacts'][1:]
    elif change=='physical_scope':summary['physical_validation']='established'
    elif change=='alias_disagreement':exported['artifacts'][2]['sha256']='c'*64
    with pytest.raises(ValueError):handler.execute(prepared,tmp_path,lambda:False)
    assert not (tmp_path/'camera-e2e-export.json').exists()
    assert (tmp_path/'external-dispatch.json').exists()

def test_input_scope_hash_changes_and_workload_rejected_before_dispatch(contract,tmp_path):
    handler=ContractHandler(contract)
    with pytest.raises(ValueError,match='default project'):handler.prepare(spec(contract,project_id='other'))
    with pytest.raises(ValueError):handler.prepare(spec(contract,model={'unconnected_model':'must-not-evaluate'}))
    prepared=handler.prepare(spec(contract))
    contract['plan_hash']='c'*64
    with pytest.raises(ValueError,match='changed after preview'):handler.execute(prepared,tmp_path,lambda:False)
    assert not (tmp_path/'external-dispatch.json').exists()
    # Twelve candidates and sixteen frames are individually supported by the
    # external service but their complete retained closure can exceed 256 files.
    frozen=contract['frozen'];frozen['candidates']=[{'candidate_id':f'candidate-{index:03d}','profile':{'gain':index}} for index in range(12)]
    frozen['base_replay']['source']['raw_shape']=[16,2,2]
    frozen['input_artifacts']=[descriptor(f'input_{index}',f'input-{index}.npy',b'raw') for index in range(45)]
    frozen['cost'].update(candidate_count=12,processor_invocations=13,max_preview_count=208)
    contract['plan_hash']=hashlib.sha256(canonical(frozen)).hexdigest()
    with pytest.raises(ValueError,match='preflight native file'):handler.prepare(spec(contract))

def test_streamed_payload_hash_mismatch_and_unconfirmed_cancel(contract,tmp_path):
    handler=ContractHandler(contract);prepared=handler.prepare(spec(contract))
    contract['payloads']['candidate_blob']=b'changed native bytes'
    with pytest.raises(ValueError,match='content differs'):handler.execute(prepared,tmp_path,lambda:False)
    assert not (tmp_path/'camera-e2e-export.json').exists()
    second=tmp_path/'cancel';second.mkdir()
    class Running(ContractHandler):
        def _request(self,path,body=None,**kwargs):
            if path.endswith('/plan'):return super()._request(path,body,**kwargs)
            self.calls.append((path,body));return {'id':'external_job','status':'running'}
    running=Running(contract);prepared=running.prepare(spec(contract));checks=iter([False,True])
    with pytest.raises(RuntimeError,match='terminal stop is unconfirmed'):running.execute(prepared,second,lambda:next(checks))
    journal=json.loads((second/'external-dispatch.json').read_text())
    assert journal['cleanup_status']=='unknown' and journal['status']=='cancel_requested'

def test_loopback_connector_never_follows_redirect_or_sends_remote_requests(monkeypatch):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path,self.headers.get('x-platform-token')))
            self.send_response(302);self.send_header('Location','/redirect-target');self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('AXIOM_CAMERA_E2E_URL',f'http://127.0.0.1:{server.server_port}')
        handler=RetainedISPHandler();handler.token='contract-token-only'
        with pytest.raises(ValueError,match='redirects'):handler._request('/source',{})
        assert requests==[('/source','contract-token-only')]
        handler.url='http://external.example'
        with pytest.raises(ValueError,match='loopback'):handler._request('/source',{})
    finally:
        server.shutdown();server.server_close();thread.join()
