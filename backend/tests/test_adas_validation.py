"""Campaign authoring contracts; native execution is checked separately."""
import copy
import json
import pytest
from ade2e.adas_module_schemas import AdasModuleConfig
from ade2e.adas_validation_schemas import AdasCampaignConfig, AdasPreboardConfig, campaign_plan
from ade2e.experiment_jobs import native_file_limit
from test_open_isp_api import env


def test_full_long_plan_preserves_geometry_clock_and_deterministic_faults():
    config=AdasCampaignConfig(engine='adas_validation_campaign',long_frames=1800).model_dump()
    a=campaign_plan(config);b=campaign_plan(config)
    assert a==b and len(a)==18
    assert a[0]['config']['max_cycles']>2**32
    for item in a:
        c=AdasModuleConfig.model_validate(item['config'])
        assert (c.width,c.height,c.fps,c.clock_hz,c.fifo_depth)==(1920,1080,30,100000000,4096)
        assert [ch.source_pattern for ch in c.channels]==['gradient','color_bars','impulse','gradient']
        for fault in c.faults[:7]:
            assert any(w.start_cycle<=fault.at_cycle<w.end_cycle for w in c.trace_windows)
    for seed in config['seeds']:
        repeated=next(x for x in a if x['id']=='repeated-reset-'+str(seed))
        assert len(repeated['config']['faults'])==8
        assert {f['channel'] for f in repeated['config']['faults']}=={0,1,2,3}
        simultaneous=next(x for x in a if x['id']=='simultaneous-'+str(seed))
        assert len({f['at_cycle'] for f in simultaneous['config']['faults']})==1
    assert next(x for x in a if x['id']=='repeated-reset-42')['config']['faults']!=next(x for x in a if x['id']=='repeated-reset-7')['config']['faults']


@pytest.mark.parametrize('change',[{'long_frames':1801},{'seeds':[42,42]},{'stall_cycles':[6000,5000]},
    {'simultaneous_channels':[0]},{'preview_frames':[300]},{'reset_count':33}])
def test_campaign_rejects_ambiguous_or_unbounded_plan(change):
    with pytest.raises(ValueError):AdasCampaignConfig(engine='adas_validation_campaign',**change)


def test_extended_smoke_is_explicitly_different_and_init_zero_is_deterministic():
    c=AdasCampaignConfig(engine='adas_validation_campaign',mode='extended_smoke',seeds=[0,1]).model_dump()
    plan=campaign_plan(c)
    assert plan[0]['config']['width']==128
    assert next(x for x in plan if x['id']=='init-random-0')['config']['init_seed']==1


def test_long_preview_and_raw_repeat_contract():
    c=AdasModuleConfig(engine='adas_module_rtl',frames=1800,max_cycles=6100000000,preview_frames=[0,1799])
    assert c.frames==1800
    for change in ({'preview_frames':[0,0]},{'preview_frames':[1800]},{'init_seed':0}):
        with pytest.raises(ValueError):AdasModuleConfig.model_validate(c.model_dump()|change)
    with pytest.raises(ValueError):AdasModuleConfig(engine='adas_module_rtl',channels=[dict(channel=i,raw_repeat=True) for i in range(4)])


def test_preboard_reference_clocks_and_no_external_binding():
    c=AdasPreboardConfig(engine='adas_preboard_checks')
    assert c.source_period_ns!=c.sink_period_ns
    with pytest.raises(ValueError):AdasPreboardConfig(engine='adas_preboard_checks',source_period_ns=13)
    with pytest.raises(ValueError):AdasPreboardConfig(engine='adas_preboard_checks',board='pretend-ready')


def test_public_plan_save_restore_and_artifact_limit(env):
    db,client,registry,sdk=env
    c=AdasCampaignConfig(engine='adas_validation_campaign',mode='extended_smoke',long_frames=30,
        seeds=[42,7],reset_count=2,preview_frames=[0,29]).model_dump()
    preview=sdk.preview_adas_campaign_plan(c)
    assert preview['total']==len(preview['items'])+1
    saved=sdk.save_engineering_lab_test(name='Pre-board campaign',config=c,idempotency_key='campaign-write')
    assert sdk.engineering_lab_test(saved['id'],revision=1)==saved
    assert sdk.engineering_lab_executions()['items']==[]
    payload=dict(spec=dict(kind='engineering_lab',adapter_id='native-engineering-lab'),input=dict(test=dict(config=c)))
    assert native_file_limit(payload)==8192
    payload['spec']['adapter_id']='unknown'
    assert native_file_limit(payload)==256


def test_live_progress_is_bounded_provisional_and_project_scoped(env):
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env
    registry.register(EngineeringHandler(db))
    if not sdk.engineering_lab_readiness().get('engines',{}).get('adas_validation_campaign',{}).get('available'):
        pytest.skip('Native preparation tools unavailable')
    config=AdasCampaignConfig(engine='adas_validation_campaign',mode='extended_smoke',long_frames=30).model_dump()
    test=sdk.save_engineering_lab_test(name='Progress contract',config=config,idempotency_key='progress-save')
    job=sdk.execute_engineering_lab_test(test,idempotency_key='progress-queue')
    progress=sdk.adas_campaign_progress(job['id'])
    assert progress['provisional'] and progress['completed']==0 and 'verdict' not in progress
    directory=db.experiment_dir/'attempts'/job['id'];directory.mkdir(parents=True)
    path=directory/'campaign-progress.json'
    path.write_text(json.dumps(dict(completed=1,current_case='../../private')))
    progress=sdk.adas_campaign_progress(job['id'])
    assert progress['completed']==1 and progress['current_case'] is None
    path.write_text(json.dumps(dict(completed=1,current_case=['invalid'])))
    assert sdk.adas_campaign_progress(job['id'])['current_case'] is None
    case=directory/'cases'/'long-continuous';case.mkdir(parents=True)
    (case/'progress.json').write_text(json.dumps(dict(cycles=100000000,negative=-1,too_large=1e20,verdict='passed')))
    path.write_text(json.dumps(dict(completed=1,current_case='long-continuous')))
    progress=sdk.adas_campaign_progress(job['id'])
    assert progress['counters']=={'cycles':100000000} and 'verdict' not in progress
    path.write_text('{partial write')
    assert sdk.adas_campaign_progress(job['id'])['completed']==0
    response=client.get('/api/v1/hil-sim/engineering-lab/executions/'+job['id']+'/campaign-progress?project_id=other')
    assert response.status_code==404


def test_actual_preboard_public_worker_and_export(env):
    from axiom_engineering import EngineeringHandler
    from ade2e.worker import run_once
    from ade2e.adapters import Registry
    db,client,registry,sdk=env
    registry.register(EngineeringHandler(db))
    if not sdk.engineering_lab_readiness().get('engines',{}).get('adas_preboard_checks',{}).get('available'):
        pytest.skip('Native Icarus/Verilator unavailable')
    config=AdasPreboardConfig(engine='adas_preboard_checks',boundary_words=32).model_dump()
    saved=sdk.save_engineering_lab_test(name='Actual four-state boundary',config=config,idempotency_key='preboard-save')
    job=sdk.execute_engineering_lab_test(saved,idempotency_key='preboard-native')
    run_once(db,Registry(load_plugins=False),'preboard-native-test',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='completed',result
    assert result['summary']['verdict']=='passed'
    assert result['fidelity']=='adas_preboard_reference'
    assert result['summary']['board_ready'] is False
    assert result['summary']['board_example']['selection']=='user_authorized_example_not_connected'
    assert 'board-example/pll-25-to-100.v' in {a['name'] for a in result['artifacts']}
    exported=sdk.export_engineering_lab_execution(job['id'])
    assert exported['execution']['id']==job['id'] and result['manifest_hash']
    assert exported['prepared']['input']['test']['sha256']==saved['sha256']
