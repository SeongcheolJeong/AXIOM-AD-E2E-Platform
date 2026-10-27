import copy
import pytest
from ade2e.hil_model_coupling import sample_measurements
from axiom_hil_gateway.model_monitor import ModelMonitor
from axiom_hil_gateway.hooks import validate_runtime_faults


def policy(boundary='power'):
    return dict(boundary=boundary,min_hold_ms=500,recovery_samples=3,power_min_v=8,analog_min_v=2,analog_max_v=3)


def test_short_voltage_excursion_is_not_lost_or_stretched_and_tail_is_explicit():
    data={'samples':[{'time':0,'v(out)':12},{'time':.003,'v(out)':6},{'time':.007,'v(out)':12},{'time':.01,'v(out)':12}]}
    before=copy.deepcopy(data);packets=sample_measurements('power',data,0,3000,6000)
    assert packets[59]['phase']=='before' and packets[60]['min_v']==12
    assert packets[61]['min_v']==6 and packets[61]['last_v']==12
    assert packets[61]['native_time_ms']==10 and packets[62]['phase']=='tail_hold'
    assert packets[62]['min_v']==12 and data==before
    assert packets[62]['window_start_ms']==packets[62]['window_end_ms']==10
    monitor=ModelMonitor(policy());report=monitor.step(packets[61],61,3055)
    assert report['detected'] and report['protect']
    for i in range(62,71):assert monitor.step(packets[i],i,i*50+5)['protect']
    assert monitor.step(packets[71],71,3555)['recovered']


def test_normal_waveform_does_not_force_braking_and_future_samples_do_not_leak():
    packets=sample_measurements('analog',{'samples':[{'time':0,'v(out)':2.5},{'time':.101,'v(out)':4}]},0,3000,6000)
    monitor=ModelMonitor(policy('analog'))
    for i in range(63):assert not monitor.step(packets[i],i,i*50+5)['protect']
    assert monitor.step(packets[63],63,3155)['detected']


def test_channel_abort_is_retained_until_observed_commit_and_other_channels_are_isolated():
    frames=[dict(channel=0,frame=0,status='aborted',terminal_ns=5_000_000,loss_reason='fifo_overflow'),
        dict(channel=1,frame=0,status='committed',terminal_ns=32_000_000,loss_reason=''),
        dict(channel=0,frame=1,status='committed',terminal_ns=65_000_000,loss_reason='')]
    packets=sample_measurements('fpga',dict(frames=frames),0,3000,6000)
    assert packets[61]['abort_count']==1 and packets[61]['last_status']=='aborted'
    assert packets[62]['commit_count']==1 and packets[62]['last_status']=='committed'
    other=sample_measurements('fpga',dict(frames=frames),1,3000,6000)
    assert all(p['abort_count']==0 for p in other)


def test_missing_measurement_is_protected_and_not_a_valid_fresh_sample():
    monitor=ModelMonitor(policy());report=monitor.step(None,60,3005)
    assert report['missing'] and report['protect'] and report['basis']=='measurement_missing'
    assert monitor.step({'sequence':59},60,3055)['missing']


@pytest.mark.parametrize('first',['power','fpga'])
def test_independent_recovery_keeps_braking_until_both_members_clear(first):
    monitor=ModelMonitor(dict(boundary='multiple',combination='any_protect',members={n:policy(n) for n in ('power','fpga')}))
    def sample(i,power_bad=False,fpga_bad=False):
        return dict(sequence=i,samples=dict(power=dict(sequence=i,phase='native_replay',min_v=6 if power_bad else 12),
            fpga=dict(sequence=i,phase='native_replay',abort_count=int(fpga_bad),last_status='aborted' if fpga_bad else 'committed')))
    report=monitor.step(sample(0,True,True),0,0)
    assert set(report['active_boundaries'])=={'power','fpga'}
    for i in range(1,11):
        report=monitor.step(sample(i,power_bad=first!='power',fpga_bad=first!='fpga'),i,i*50)
        assert report['protect'] and not report['recovered']
    assert report['members'][first]['recovered']
    assert report['active_boundaries']==['fpga' if first=='power' else 'power']
    for i in range(11,20):assert monitor.step(sample(i),i,i*50)['protect']
    report=monitor.step(sample(20),20,1000)
    assert report['recovered'] and not report['protect'] and report['active_boundaries']==[]


def test_missing_one_member_cannot_be_hidden_by_other_members_recovery():
    monitor=ModelMonitor(dict(boundary='multiple',members={n:policy(n) for n in ('power','fpga')}))
    for i in range(15):
        report=monitor.step(dict(sequence=i,samples=dict(power=dict(sequence=i,phase='native_replay',min_v=12))),i,i*50)
        assert report['missing'] and report['protect'] and report['members']['fpga']['basis']=='measurement_missing'
    assert not report['members']['power']['protect']


def test_physical_model_replay_cannot_dispatch():
    with pytest.raises(ValueError,match='physical driver'):
        validate_runtime_faults({'device':{'target':'hardware'},'model_input':{'samples':[]},'faults':[]})


def test_bounded_capture_restores_gc_on_failure_and_leaves_nonmodel_capture_unchanged(monkeypatch):
    import gc
    from axiom_hil_gateway import carla_native
    original=gc.isenabled();seen=[]
    def capture(*args):seen.append(gc.isenabled());raise RuntimeError('native failure')
    monkeypatch.setattr(carla_native.city_native,'capture',capture)
    with pytest.raises(RuntimeError):carla_native.capture_model_loop({'hil':{'runtime':{'model_input':{'samples':[1]}}}},None,None,None,None,None)
    assert not seen[-1] and gc.isenabled()==original
    with pytest.raises(RuntimeError):carla_native.capture_model_loop({'hil':{}},None,None,None,None,None)
    assert seen[-1]==original


def test_gateway_large_init_uses_registered_bound_and_still_rejects_oversize(tmp_path,monkeypatch):
    # Protocol transport fixture only, not a native ECU execution.
    import sys
    from axiom_hil_gateway import carla_native
    from axiom_carla import hil_gateway
    script="""import json,sys
for line in sys.stdin:
 v=json.loads(line);op=v['op']
 print(json.dumps(dict(schema_version='hil-dut-reply/1',op=op,status='ready' if op=='init' else 'closed',dut_version='hil-gateway/1')),flush=True)
 if op=='close':break
"""
    monkeypatch.setattr(carla_native.hil_gateway,'command',lambda *args:[sys.executable,'-u','-c',script])
    runtime=dict(kind='hil_gateway',dut_version='hil-gateway/1')
    process=carla_native.DutProcess(dict(op='init',bulk='x'*300000),tmp_path/'stderr.log',runtime=runtime)
    try:
        with pytest.raises(ValueError,match='protocol bound'):
            process.exchange(dict(op='init',bulk='x'*hil_gateway.MAX_MESSAGE))
        process.gateway=False
        with pytest.raises(ValueError,match='protocol bound'):
            process.exchange(dict(op='init',bulk='x'*300000))
        process.gateway=True
    finally:process.close()


def test_fpga_recurrence_on_power_release_tick_has_no_aggregate_release():
    monitor=ModelMonitor(dict(boundary='multiple',members={n:policy(n) for n in ('power','fpga')}))
    reports=[]
    # FPGA first clears at 3550; power clears at 3850, exactly when FPGA recurs.
    for i in range(30):
        now=3000+i*50;power_bad=now<=3350;fpga_bad=now in (3050,3850)
        sample=dict(sequence=i,samples=dict(power=dict(sequence=i,phase='native_replay',min_v=6 if power_bad else 12),
            fpga=dict(sequence=i,phase='native_replay',abort_count=int(fpga_bad),last_status='aborted' if fpga_bad else 'committed')))
        reports.append(monitor.step(sample,i,now))
    assert reports[11]['members']['fpga']['recovered']
    handoff=reports[17]
    assert handoff['members']['power']['recovered'] and handoff['members']['fpga']['detected']
    assert handoff['protect'] and not handoff['recovered'] and handoff['active_boundaries']==['fpga']
    assert all(r['protect'] and not r['recovered'] for r in reports[1:27])
    assert reports[27]['recovered'] and not reports[27]['protect']


@pytest.mark.parametrize('earliest_clear',[True,False])
def test_power_recurrence_at_measurement_boundary_exposes_both_handoff_outcomes(earliest_clear):
    monitor=ModelMonitor(dict(boundary='multiple',members={n:policy(n) for n in ('power','fpga')}))
    reports=[]
    for i in range(70):
        now=3000+i*50
        power_bad=3050<=now<=3350 or 4400<=now<=4700
        fpga_bad=now in (3050,3850)
        sample=dict(sequence=i,samples=dict(power=dict(sequence=i,phase='native_replay',min_v=6 if power_bad else 12),
            fpga=dict(sequence=i,phase='native_replay',abort_count=int(fpga_bad),last_status='aborted' if fpga_bad else 'committed')))
        # Both monotonic schedules are legal. A 4350 source sag enters the 4400
        # measurement envelope; a 500ms latch can clear on4350 or4400.
        clock=now-1 if now==4350 and not earliest_clear else now
        reports.append(monitor.step(sample,i,clock))
    assert reports[27]['protect'] is (not earliest_clear)
    assert reports[27]['recovered'] is earliest_clear
    assert reports[28]['members']['power']['detected'] and reports[28]['protect']
    if not earliest_clear:
        assert reports[28]['members']['fpga']['recovered'] and not reports[28]['recovered']
        assert all(r['protect'] for r in reports[1:44])
    else:
        assert reports[28]['detected']  # A gap cannot be described as continuous protection.
    assert reports[44]['recovered']
