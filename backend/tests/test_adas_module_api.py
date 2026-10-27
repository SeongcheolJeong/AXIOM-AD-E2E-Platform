"""Four-camera authoring/RAW identity and optional actual native queue integration."""
import copy
import hashlib
import json
import pytest
from ade2e.adapters import Registry
from ade2e.adas_module_schemas import AdasModuleConfig, adas_templates
from ade2e.apollo_lab import asset_path
from ade2e.engineering_lab import resolve_adas_assets
from ade2e.service import digest
from ade2e.worker import run_once
from test_open_isp_api import env, ref, BASE


def config(**changes):
    values=dict(engine='adas_module_rtl', width=16, height=16, frames=2,
                raster_width=20, raster_height=20, pixel_rate_hz=50000000,
                max_cycles=6000, trace_windows=[dict(start_cycle=0,end_cycle=512)])
    values.update(changes)
    return AdasModuleConfig(**values).model_dump()


def save(sdk, definition=None, key='adas-save', **kwargs):
    return sdk.save_engineering_lab_test(name='ADAS module test', config=definition or config(),
                                        idempotency_key=key, **kwargs)


@pytest.mark.parametrize('change', [
    {'width':9}, {'width':1922}, {'height':1082}, {'frames':1801}, {'fps':60},
    {'clock_hz':200000000}, {'fifo_depth':8192}, {'pipeline_id':'arbitrary'},
    {'hardware_target':'physical-sa8650'}, {'pixel_rate_hz':100000001},
    {'width':1920,'height':1080,'raster_width':2200,'raster_height':1125,'pixel_rate_hz':74250001},
    {'raster_width':8}, {'channels':[{'channel':i} for i in (0,1,2,2)]},
    {'channels':[{'channel':i} for i in (0,1,2)]},
    {'channels':[{'channel':i,'gains':[128,128,128,1.5]} for i in range(4)]},
    {'channels':[{'channel':i,'source_kind':'raw_asset'} for i in range(4)]},
    {'channels':[{'channel':i,'raw_frame_index':1} for i in range(4)]},
    {'faults':[{'channel':4,'kind':'reset','at_cycle':300,'duration_cycles':2}]},
    {'faults':[{'channel':0,'kind':'reset','at_cycle':300,'duration_cycles':2}]},
    {'faults':[{'channel':0,'kind':'output_stall','at_cycle':5999,'duration_cycles':2}]},
    {'faults':[{'channel':0,'kind':'input_gap','at_cycle':300,'duration_cycles':0}]},
    {'faults':[{'channel':0,'kind':'reset','at_cycle':300,'duration_cycles':4},
               {'channel':0,'kind':'reset','at_cycle':302,'duration_cycles':2}]},
    {'csr_updates':[{'channel':0,'at_cycle':300}]},
    {'csr_updates':[{'channel':0,'at_cycle':6000,'black_level':1}]},
    {'csr_updates':[{'channel':0,'at_cycle':300,'black_level':1},{'channel':0,'at_cycle':300,'black_level':2}]},
    {'trace_windows':[{'start_cycle':100,'end_cycle':99}]},
    {'trace_windows':[{'start_cycle':0,'end_cycle':100},{'start_cycle':99,'end_cycle':200}]},
    {'max_cycles':20000,'trace_windows':[{'start_cycle':0,'end_cycle':16385}]},
    {'source_path':'/tmp/unregistered.raw'},
])
def test_adas_config_rejects_unimplemented_or_ambiguous_requests(change):
    value=config();value.update(change)
    with pytest.raises(ValueError):AdasModuleConfig.model_validate(value)


def test_adas_templates_defaults_and_sdk_revision_roundtrip(env):
    db,client,registry,sdk=env
    presets={t['id']:t for t in sdk.engineering_lab_templates()['items'] if t['config']['engine']=='adas_module_rtl'}
    assert set(presets)=={t['id'] for t in adas_templates()}
    default=presets['adas-normal']['config']
    assert (default['width'],default['height'],default['frames'],default['fps'])==(1920,1080,30,30)
    assert default['pixel_rate_hz']==default['raster_width']*default['raster_height']*30
    for preset in presets.values():AdasModuleConfig.model_validate(preset['config'])
    test=save(sdk)
    assert save(sdk)==test
    assert test['config_hash']==digest(test['config'])
    assert sdk.engineering_lab_test(test['id'],revision=1)==test
    changed=copy.deepcopy(test['config']);changed['channels'][2]['gains'][0]=192
    revised=save(sdk,changed,key='revision',test_id=test['id'],expected_revision=1)
    assert revised['revision']==2 and revised['config_hash']!=test['config_hash']
    assert sdk.engineering_lab_test(test['id'],revision=1)==test
    assert client.post(BASE+'/tests/'+test['id']+'/preview',json={k:test[k] for k in ('project_id','revision','sha256')}).status_code==422
    assert sdk.engineering_lab_executions()['items']==[]


def test_extended_artifact_count_is_only_for_pinned_adas_simulation():
    from ade2e.experiment_jobs import native_file_limit
    payload=dict(spec=dict(kind='engineering_lab',adapter_id='native-engineering-lab'),
                 input=dict(test=dict(config=dict(engine='adas_module_rtl',pipeline_id='adas-camera-4ch/1'))))
    assert native_file_limit(payload)==512
    for field,value in [('kind','fpga_build'),('adapter_id','untrusted-adapter')]:
        wrong=copy.deepcopy(payload);wrong['spec'][field]=value
        assert native_file_limit(wrong)==256
    for field,value in [('engine','isp_rtl'),('pipeline_id','external')]:
        wrong=copy.deepcopy(payload);wrong['input']['test']['config'][field]=value
        assert native_file_limit(wrong)==256


def test_unsupported_retained_revision_does_not_hide_valid_saved_tests(env):
    from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
    from ade2e.service import canonical_bytes
    db,client,registry,sdk=env
    supported=save(sdk)
    # A content-valid development revision saved before reset was constrained to
    # its implemented write-one-pulse semantics. Preserve bytes rather than migrate.
    original={k:v for k,v in supported.items() if k!='sha256'}
    original['id']='engt_retained_reset_duration'
    original['config']=copy.deepcopy(original['config'])
    original['config']['faults']=[dict(channel=0,kind='reset',at_cycle=300,duration_cycles=2)]
    original['config_hash']=digest(original['config'])
    raw=canonical_bytes(original);sha=digest(original)
    with db.session() as session:
        session.add(Row(project_id='default',kind='engineering-test',id=original['id'],revision=1,manifest=raw,manifest_hash=sha));session.commit()
    history=sdk.engineering_lab_tests()
    assert history['items']==[supported]
    assert history['unavailable_revisions'][0]['id']==original['id']
    assert 'duration_cycles must be 1' in history['unavailable_revisions'][0]['error']
    assert client.get(BASE+'/tests/'+original['id'],params={'revision':1}).status_code==409
    response=client.post(BASE+'/tests/'+original['id']+'/preview',json=dict(project_id='default',revision=1,sha256=sha))
    assert response.status_code==409
    with db.read_session() as session:
        assert session.get(Row,('default','engineering-test',original['id'],1)).manifest==raw


def raw_config(sdk, frames=2):
    raw=bytes([64,0])*16*16*frames
    asset=sdk.upload_apollo_lab_raw(raw,metadata=dict(width=16,height=16,bits=12,packing='uint16le',bayer='RGGB'))
    definition=config(frames=frames)
    definition['channels'][0].update(source_kind='raw_asset',raw_asset_ref=ref(asset))
    return definition,asset,raw


def test_adas_per_channel_raw_asset_hash_shape_and_explicit_frame_range(env):
    db,client,registry,sdk=env
    definition,asset,raw=raw_config(sdk)
    test=save(sdk,definition)
    with db.read_session() as session:
        assert resolve_adas_assets(session,test)=={'0':asset}
    for key, value, status in [('bayer_pattern','BGGR',422),('raw_frame_index',1,422),
                              ('raw_asset_ref',ref(asset)|{'sha256':'0'*64},409),
                              ('raw_asset_ref',ref(asset)|{'id':'raw_missing'},404)]:
        bad=copy.deepcopy(definition);bad['channels'][0][key]=value
        response=client.post(BASE+'/tests',json=dict(name='Invalid ADAS RAW',config=bad),headers={'Idempotency-Key':key+str(status)})
        assert response.status_code==status,response.text
    bad=copy.deepcopy(definition);bad['width']=8
    assert client.post(BASE+'/tests',json=dict(name='No resize',config=bad),headers={'Idempotency-Key':'dimensions'}).status_code==422
    assert client.get(BASE+'/tests/'+test['id'],params={'revision':1,'project_id':'other'}).status_code in (403,404)
    other=client.post('/api/v1/projects',json={'name':'Separate RAW project'}).json()['id']
    foreign=sdk.upload_apollo_lab_raw(bytes([65,0])*16*16*2,
        metadata=dict(width=16,height=16,bits=12,packing='uint16le',bayer='RGGB'),project_id=other)
    bad=copy.deepcopy(definition);bad['channels'][0]['raw_asset_ref']=ref(foreign)
    response=client.post(BASE+'/tests',json=dict(name='Foreign RAW',config=bad),headers={'Idempotency-Key':'foreign-raw'})
    assert response.status_code==404,response.text


def register_native(env):
    pytest.importorskip('axiom_isp_rtl.adas')
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env
    registry.register(EngineeringHandler(db))
    if not sdk.engineering_lab_readiness().get('engines',{}).get('adas_module_rtl',{}).get('available'):
        pytest.skip('Native ADAS Verilator adapter unavailable')
    return db,client,registry,sdk


def test_actual_adas_worker_sdk_raw_source_export_and_history(env,tmp_path):
    db,client,registry,sdk=register_native(env)
    definition,asset,raw=raw_config(sdk)
    test=save(sdk,definition)
    assert sdk.preview_engineering_lab_test(test)['fidelity']=='adas_module_rtl_reference'
    job=sdk.execute_engineering_lab_test(test,idempotency_key='adas-native')
    assert sdk.execute_engineering_lab_test(test,idempotency_key='adas-native')['id']==job['id']
    run_once(db,Registry(load_plugins=False),'adas-api-native',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='completed',result
    assert result['summary']['verdict']=='passed',result['summary']
    assert result['summary']['acceptance_1080p30'] is False
    assert result['summary']['acceptance_1080p30_passed'] is False
    assert result['summary']['committed_mismatch_count']==0
    assert result['summary']['unexpected_mismatch_count']==0
    assert result['fidelity']=='adas_module_rtl_reference'
    assert result['summary']['engine']=='adas_module_rtl'
    assert result['summary']['validation_domain']=='adas_module'
    assert not any('.inputs' in a['name'] for a in result['artifacts'])
    a=next(a for a in result['artifacts'] if a['name']=='provenance.json')
    path=sdk.download_experiment_artifact(job['id'],a['id'],tmp_path/'provenance.json')
    p=json.loads(path.read_bytes())
    assert p['raw_assets']['0']['content_sha256']==hashlib.sha256(raw).hexdigest()
    assert p['test_ref']==ref(test)
    assert sdk.export_engineering_lab_execution(job['id'])['execution']['id']==job['id']
    history=sdk.engineering_lab_executions()['items'][0]
    assert history['engine']=='adas_module_rtl' and history['operation']=='simulate'
    assert history['design_hash']==result['summary']['design_hash']


def test_adas_queued_raw_tamper_fails_without_publishing(env):
    db,client,registry,sdk=register_native(env)
    definition,asset,raw=raw_config(sdk)
    test=save(sdk,definition)
    job=sdk.execute_engineering_lab_test(test,idempotency_key='adas-tamper')
    asset_path(db,asset).write_bytes(bytes(len(raw)))
    run_once(db,Registry(load_plugins=False),'adas-api-tamper',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='failed' and result['failure_http_status']==422
    assert 'RAW original hash changed' in result['error']
    assert result['summary'] is None and result['artifacts']==[]


def test_adas_build_has_independent_domain_and_missing_vendor_fails_closed(env):
    pytest.importorskip('axiom_isp_rtl.adas_build')
    from axiom_isp_rtl.handler import BuildHandler
    db,client,registry,sdk=register_native(env)
    registry.register(BuildHandler(db))
    test=save(sdk)
    for profile in ['gw2a-gowin-template/1','cyclonev-quartus-template/1','unknown']:
        response=client.post(BASE+'/tests/'+test['id']+'/build-preview',json={k:test[k] for k in ('project_id','revision','sha256')}|{'build_profile_id':profile})
        assert response.status_code==422,response.text
    assert sdk.engineering_lab_executions()['items']==[]
    profiles=sdk.engineering_lab_readiness()['build_profiles']
    ecp5=next(p for p in profiles if p['id']=='ecp5-85k-cabga381-6-ooc/1')
    assert 'adas_module_rtl' in ecp5['engines']
    if ecp5['available']:
        preview=sdk.preview_engineering_lab_build(test)
        assert preview['experiment']['kind']=='fpga_build'
        assert preview['fidelity']=='fpga_ooc_implementation'
