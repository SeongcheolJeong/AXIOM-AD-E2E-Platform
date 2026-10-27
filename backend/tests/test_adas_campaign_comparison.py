"""Offline comparison contract tests; these authored fixtures are not native runs."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('adas_campaign_comparison', Path(__file__).resolve().parents[2]/'examples/compare_adas_module_campaigns.py')
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


@pytest.fixture
def campaigns():
    from ade2e.adas_module_schemas import AdasModuleConfig
    config=AdasModuleConfig(engine='adas_module_rtl',trace_windows=[]).model_dump()
    runs={}
    for case in comparison.CASES:
        frames={}
        for channel in range(4):
            for frame in range(30):
                aborted=case in comparison.FAULTS and (channel,frame)==(0,0)
                version=int(case=='adas-wb' and channel==0 and frame>=3)
                mismatches=194 if aborted and case=='adas-input-gap' else 0
                output=('b' if version else 'a')*64
                frames[channel,frame]=dict(channel=channel,frame=frame,status='aborted' if aborted else 'committed',
                    input_sha256='c'*64,output_sha256=output,reference_sha256=output,source_pixels=1920*1080,source_complete=True,
                    loss_reason=comparison.FAULTS[case] if aborted else '',config_version=version,
                    epoch=int(case in comparison.FAULTS and channel==0 and frame>0),terminal_output_epoch_checked=True,
                    received_pixels=300 if aborted else 1916*1076,mismatch_count=mismatches,
                    mismatch_disposition='invalidated_input_gap' if mismatches else 'none',
                    first_input_ns=100,first_output_ns=200,last_output_ns=990,terminal_ns=1000,latency_ns=900,fifo_peak_pixels=2)
        channels=[]
        for channel in range(4):
            values=[f for f in frames.values() if f['channel']==channel]
            channels.append(dict(channel=channel,input_pixels=1920*1080*30,output_pixels=sum(f['received_pixels'] for f in values),
                mismatch_count=sum(f['mismatch_count'] for f in values),committed_frames=sum(f['status']=='committed' for f in values),
                aborted_frames=sum(f['status']=='aborted' for f in values),fifo_peak_pixels=2))
        discarded=sum(f['mismatch_count'] for f in frames.values())
        summary=dict(verdict='passed',design_hash='d'*64,completed_frames=119 if case in comparison.FAULTS else 120,
            aborted_frames=int(case in comparison.FAULTS),committed_mismatch_count=0,unexpected_mismatch_count=0,
            unexpected_disposition_count=0,protocol_error_count=0,missing_frames=0,unadmitted_frames=0,
            discarded_mismatch_count=discarded,expected_discarded_mismatch_count=discarded,
            acceptance_1080p30_passed=case not in comparison.FAULTS,config_hash='e'*64)
        test=dict(id='test-'+case,revision=1,sha256='f'*64,project_id='default',config=copy.deepcopy(config),config_hash='e'*64,seed=42)
        item=dict(test=test,config=copy.deepcopy(config),job_id='job-'+case,summary=summary,status='completed',url='http://old/#'+case)
        provenance=dict(test_ref={k:test[k] for k in ('id','revision','sha256')},seed=42,config_hash='e'*64,design_hash='d'*64,
            tool_identity=dict(source_sha256={'core.sv':'1'*64},shared_runner_sha256='2'*64))
        runs[case]=dict(item=item,provenance=provenance,frames=frames,manifest=dict(frames=list(frames.values()),channels=channels,config=copy.deepcopy(config)))
    build=copy.deepcopy(runs['adas-normal'])
    build['item'].update(job_id='job-build',summary=dict(design_hash='d'*64,config_hash='e'*64,build_profile_id='ecp5',seed=42,
        target_clock_hz=100000000,fmax_mhz=82.14,verdict='failed',synthesis_status='passed',place_route_status='passed',
        timing_status='failed',timing_met=False,resources=dict(lut=100,ff=200,bram=48,dsp=4)))
    build['provenance']['tool_identity']=dict(profile={'id':'ecp5','sha256':'3'*64},tool_sha256={'nextpnr':'4'*64})
    runs['ecp5-ooc']=build
    receipt=dict(campaign_id='old',api_url='http://api:8160',ui_url='http://ui:5260',project_id='default',
        schema_version='adas-module-validation/1',completed_at='2026-10-17',smoke=False,cases=list(comparison.CASES),runs={k:v['item'] for k,v in runs.items()})
    before=dict(receipt=receipt,runs=runs,path='old.json',sha256='5'*64)
    after=copy.deepcopy(before);after.update(path='new.json',sha256='6'*64);after['receipt'].update(campaign_id='new',ui_url='http://ui:5270')
    for name,run in after['runs'].items():
        run['item']['summary']['design_hash']='7'*64
        run['item']['job_id']='new-'+run['item']['job_id']
        run['provenance']['design_hash']='7'*64
        if name!='ecp5-ooc':
            run['provenance']['tool_identity']['source_sha256']['core.sv']='8'*64
            for frame in run['frames'].values():
                for field in ('first_output_ns','terminal_ns','latency_ns'):
                    frame[field]+=20
    after['runs']['ecp5-ooc']['item']['summary'].update(fmax_mhz=110.5,verdict='passed',timing_status='passed',timing_met=True,resources=dict(lut=110,ff=220,bram=48,dsp=4))
    return before,after


def test_preserves_pixels_and_reports_latency_resource_and_build_links(campaigns):
    before,after=campaigns
    result=comparison.compare_campaigns(before,after)
    assert result['verdict']=='passed' and not result['errors']
    assert result['cases']['adas-normal']['latency_delta_ns']['latency_ns']['min']==20
    assert result['build']['resource_delta']['ff']==20
    assert 'buildCompare=job-build' in result['links']['build_new_vs_old']
    assert result['links']['build_new_vs_old'].startswith('http://ui:5270/')
    assert 'compare=job-adas-wb' in result['links']['adas-wb']['new_pixel_a_old_pixel_b']


@pytest.mark.parametrize('field,value',[
    ('output_sha256','9'*64),('input_sha256','9'*64),('status','aborted'),
    ('config_version',2),('epoch',2),('terminal_output_epoch_checked',False),
    ('received_pixels',1),('reference_sha256',None),('source_complete',False),
])
def test_changed_recovered_frame_cannot_be_hidden_by_timing_success(campaigns,field,value):
    before,after=campaigns
    after['runs']['adas-reset']['frames'][0,1][field]=value
    result=comparison.compare_campaigns(before,after)
    assert result['validation_gates']['new_ooc_100mhz']=='passed'
    assert result['validation_gates']['behavior_preserved']=='failed'
    assert any('adas-reset' in e for e in result['errors'])


def test_partial_abort_count_and_hash_drift_is_explicit_not_committed_corruption(campaigns):
    before,after=campaigns;run=after['runs']['adas-input-gap'];frame=run['frames'][0,0]
    frame.update(output_sha256='9'*64,reference_sha256='0'*64,received_pixels=295,mismatch_count=193)
    run['manifest']['channels'][0]['output_pixels']-=5
    run['manifest']['channels'][0]['mismatch_count']-=1
    run['item']['summary'].update(discarded_mismatch_count=193,expected_discarded_mismatch_count=193)
    result=comparison.compare_campaigns(before,after)
    assert result['verdict']=='passed'
    assert result['cases']['adas-input-gap']['discarded_partial_changes'][0]['changes']['mismatch_count']==dict(before=194,after=193)


def test_unexplained_discarded_errors_fail(campaigns):
    before,after=campaigns
    after['runs']['adas-reset']['frames'][0,0]['mismatch_count']=1
    assert comparison.compare_campaigns(before,after)['verdict']=='failed'


@pytest.mark.parametrize('mutation',['source','seed','profile','tools','missing-counter','build-design'])
def test_source_and_build_comparability_fail_closed(campaigns,mutation):
    before,after=campaigns
    if mutation=='source':after['runs']['adas-normal']['item']['test']['config']['fps']=29
    elif mutation=='seed':after['runs']['adas-normal']['item']['test']['seed']=99
    elif mutation=='profile':after['runs']['ecp5-ooc']['provenance']['tool_identity']['profile']['sha256']='0'*64
    elif mutation=='tools':after['runs']['ecp5-ooc']['provenance']['tool_identity']['tool_sha256']['nextpnr']='0'*64
    elif mutation=='missing-counter':after['runs']['adas-normal']['manifest']['channels'][0]['output_pixels']-=1
    else:after['runs']['ecp5-ooc']['item']['summary']['design_hash']='0'*64
    assert comparison.compare_campaigns(before,after)['validation_gates']['behavior_preserved']=='failed'


def test_different_api_requires_verified_copy_before_cross_links(campaigns):
    before,after=campaigns;after['receipt']['api_url']='http://api:8170'
    assert 'build_new_vs_old' not in comparison.compare_campaigns(before,after)['links']
    result=comparison.compare_campaigns(before,after,dict(api_url='http://api:8170',executions=[]))
    assert result['links']['build_new_vs_old'].startswith('http://ui:5270/')


def write_evidence(tmp_path,campaign):
    receipt=copy.deepcopy(campaign['receipt'])
    for case,run in campaign['runs'].items():
        folder=tmp_path/case;folder.mkdir()
        item=receipt['runs'][case];files={'provenance.json':run['provenance'],'summary.json':item['summary'],'test.json':item['test']}
        if case!='ecp5-ooc':files['frames.json']=run['manifest']
        artifacts=[]
        for name,data in files.items():
            path=folder/name;path.write_text(json.dumps(data))
            artifacts.append(dict(name=name,path=str(path),sha256=comparison.checksum(path),size_bytes=path.stat().st_size))
        item['artifacts_verified']=artifacts
        export=dict(execution=dict(id=item['job_id'],status='completed',summary=item['summary'],artifacts=artifacts),
            prepared=dict(input=dict(test=item['test'],tool_identity=run['provenance']['tool_identity'])))
        path=folder/'export.json';path.write_text(json.dumps(export));item.update(export_path=str(path),export_sha256=comparison.checksum(path))
        if case!='ecp5-ooc':item['frame_manifest']=dict(sha256=comparison.checksum(folder/'frames.json'))
    path=tmp_path/'receipt.json';path.write_text(json.dumps(receipt));return path


def test_hash_verification_rejects_modified_native_bytes(campaigns,tmp_path):
    path=write_evidence(tmp_path,campaigns[0]);verified=comparison.load_campaign(path)
    assert sum(r['artifact_count'] for r in verified['runs'].values())==27
    (tmp_path/'adas-normal'/'frames.json').write_text('{}')
    with pytest.raises(ValueError,match='artifact bytes differ'):
        comparison.load_campaign(path)


def test_receipt_summary_cannot_override_sealed_execution(campaigns,tmp_path):
    path=write_evidence(tmp_path,campaigns[0]);receipt=json.loads(path.read_text())
    receipt['runs']['ecp5-ooc']['summary']['timing_status']='passed';path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='summary differs from sealed export'):
        comparison.load_campaign(path)


def test_existing_report_is_never_overwritten(tmp_path):
    path=tmp_path/'comparison.json';path.write_text('original')
    with pytest.raises(ValueError,match='exists'):
        comparison.main(['--output',str(path)])
    assert path.read_text()=='original'


def test_pixel_preservation_does_not_imply_timing_closure(campaigns):
    before,after=campaigns
    after['runs']['ecp5-ooc']['item']['summary'].update(verdict='failed',place_route_status='failed')
    report=comparison.compare_campaigns(before,after)
    assert report['validation_gates']==dict(behavior_preserved='passed',new_ooc_100mhz='failed',physical_hil='not_configured')
    assert report['verdict']=='failed'


def test_old_explicit_default_omissions_preserve_configuration_meaning(campaigns):
    before,after=campaigns
    for case in comparison.CASES:
        config=before['runs'][case]['item']['test']['config']
        for field in ('init_mode','init_seed','preview_frames'):config.pop(field)
        for channel in config['channels']:channel.pop('raw_repeat')
    result=comparison.compare_campaigns(before,after)
    assert result['verdict']=='passed'
    assert 'init_mode' not in before['runs']['adas-normal']['item']['test']['config']


def test_randomized_init_is_not_treated_as_old_zero_default(campaigns):
    before,after=campaigns
    after['runs']['adas-normal']['item']['test']['config']['init_mode']='randomized'
    assert comparison.compare_campaigns(before,after)['verdict']=='failed'


def test_changed_implementation_option_is_explicit_but_preserves_target(campaigns):
    before,after=campaigns
    after['runs']['ecp5-ooc']['provenance']['tool_identity']['implementation_options']={'placer_heap_timingweight':30}
    report=comparison.compare_campaigns(before,after)
    assert report['verdict']=='passed'
    assert report['build']['implementation_options']==dict(before={},after={'placer_heap_timingweight':30})


def external_evidence(tmp_path,campaign):
    import shutil
    complete=write_evidence(tmp_path,campaign)
    receipt=json.loads(complete.read_text());item=receipt['runs'].pop('ecp5-ooc')
    complete.write_text(json.dumps(receipt))
    root=tmp_path/'external.json';folder=tmp_path/'external-artifacts/ooc';folder.mkdir(parents=True)
    shutil.copyfile(item['export_path'],folder/'execution-export.json')
    for artifact in item['artifacts_verified']:shutil.copyfile(artifact['path'],folder/artifact['name'])
    item['artifacts_verified']=[{k:v for k,v in x.items() if k!='path'} for x in item['artifacts_verified']]
    item.pop('export_path');item['operation']='build'
    root.write_text(json.dumps(dict(schema_version='adas-preboard-validation/1',runs=dict(ooc=item))))
    return complete,root


def test_external_ooc_receipt_uses_original_verified_test_and_export(campaigns,tmp_path):
    complete,external=external_evidence(tmp_path,campaigns[1]);original=complete.read_bytes()
    loaded=comparison.load_campaign(complete,external)
    assert loaded['external_build_receipt']['sha256']==comparison.checksum(external)
    assert loaded['runs']['ecp5-ooc']['item']['test']==campaigns[1]['runs']['ecp5-ooc']['item']['test']
    assert complete.read_bytes()==original


def test_external_ooc_different_design_fails_before_comparison(campaigns,tmp_path):
    complete,external=external_evidence(tmp_path,campaigns[1])
    data=json.loads(external.read_text());data['runs']['ooc']['summary']['design_hash']='0'*64
    external.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='External OOC design hash differs'):
        comparison.load_campaign(complete,external)


def test_external_ooc_changed_native_bytes_fail_hash_verification(campaigns,tmp_path):
    complete,external=external_evidence(tmp_path,campaigns[1])
    (tmp_path/'external-artifacts/ooc/summary.json').write_text('{}')
    with pytest.raises(ValueError,match='artifact bytes differ'):
        comparison.load_campaign(complete,external)
