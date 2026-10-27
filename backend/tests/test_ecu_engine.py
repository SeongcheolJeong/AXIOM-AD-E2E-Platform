"""Actual SimPy model checks, including independent arithmetic timing oracles."""
import copy
import pytest

from ade2e.adapters import AdapterCancelledBeforeDispatch
from ade2e.ecu_lab_schemas import EcuProfileConfig, DEFAULT_COMMANDS
from ade2e.fpga_lab_schemas import TimingConfig

pytest.importorskip('axiom_fpga_lab')
from axiom_fpga_lab.ecu import simulate


def setup(frames=6, count=1, profile=None, design=None, **channel):
    pc = EcuProfileConfig(**(dict(width=4, height=2, bits=8) | (profile or {}))).model_dump()
    dc = TimingConfig(**(dict(width=pc['width'], height=pc['height'], bits=pc['bits'], frames=frames,
                             fps=pc['fps'], expected_fps=pc['fps']) | (design or {}))).model_dump()
    channels = [dict(channel=ch, vc=ch, source_offset_us=120000, duplicate_every=0,
                     design=dict(config=copy.deepcopy(dc), seed=42), **channel) for ch in range(count)]
    return pc, channels


def run(pc, channels, commands=None, faults=None, **kwargs):
    return simulate(pc, channels, DEFAULT_COMMANDS if commands is None else commands, faults or [], **kwargs)


def test_independent_line_pipeline_oracle():
    pc, channels = setup(frames=1)
    r = run(pc, channels)
    f = r['frames'][0]
    # 4-byte read / 800MB/s = 5ns. GMSL(4+6)/150MB/s rounds to67ns.
    # CSI(4+6)/400MB/s=25ns; second line GMSL arrival at130100139ns.
    assert f['tx_start_ns'] == 130000005
    assert f['tx_end_ns'] == 130000139
    assert f['received_ns'] == 130100164
    assert f['completed_ns'] == 134100164
    assert r['summary']['completed'] == 1
    assert r['summary']['deadline_exceeded'] == 0


def test_shared_csi_round_robin_and_serial_processor():
    pc, channels = setup(frames=1, count=2, profile={'csi_lanes':1,'csi_lane_Mbps':1,'csi_efficiency_percent':100})
    together = run(pc, channels)
    alone = run(pc, channels[:1])
    a, b = together['frames']
    assert a['received_ns'] > alone['frames'][0]['received_ns']
    # Two channels alternate 80,000ns lines; channel0 wins first simultaneous tie.
    assert b['received_ns']-a['received_ns'] == 80000
    assert b['processing_start_ns'] == a['completed_ns']
    assert together['summary']['completed'] == 2


def test_link_loss_mid_frame_discards_even_after_fast_relock_and_isolates():
    pc, channels = setup(frames=8, count=2, profile={'width':1920,'height':10,'bits':12,'link_lock_us':0})
    r = run(pc, channels, faults=[dict(kind='link_down',channel=0,at_us=130010,duration_us=1)])
    first = next(f for f in r['frames'] if f['channel']==0 and f['frame']==0)
    assert first['loss_reason'] == 'link_loss_partial' and first['received_ns'] is None
    assert r['summary']['channels'][0]['completed'] == 7
    assert r['summary']['channels'][1]['completed'] == 8
    assert r['summary']['channels'][0]['final_state'] == 'normal'
    assert not any(e['kind'] == 'health_degraded' and e['channel']==1 for e in r['events'])
    loss = next(e for e in r['events'] if e['kind']=='fault_link_down')
    recovery = next(e for e in r['events'] if e['kind']=='health_normal' and e['channel']==0 and e['time_ns']>loss['time_ns'])
    completed_after_last_fault = [e for e in r['events'] if e['kind']=='processing_complete' and e['channel']==0 and loss['time_ns']<e['time_ns']<=recovery['time_ns']]
    # Missing-frame timeout may reset health after the first good completion.
    last_degraded = max(e['time_ns'] for e in r['events'] if e['kind']=='health_degraded' and e['channel']==0 and e['time_ns']<recovery['time_ns'])
    assert len([e for e in completed_after_last_fault if e['time_ns']>last_degraded]) == 3


def test_completed_des_frame_survives_later_link_down():
    pc, channels = setup(frames=1, profile={'csi_lanes':1,'csi_lane_Mbps':1,'csi_efficiency_percent':100})
    r = run(pc, channels, faults=[dict(kind='link_down',channel=0,at_us=130110,duration_us=100)])
    f = r['frames'][0]
    assert f['ingress_complete_ns'] < 130110000 < f['received_ns']
    assert f['loss_reason'] is None and f['completed_ns'] is not None


def test_pre_fault_backlog_does_not_count_as_post_relock_recovery():
    pc, channels = setup(frames=3, profile={'fps':120,'processing_us':100000,'processing_deadline_us':1000000})
    r = run(pc, channels, faults=[dict(kind='link_down',channel=0,at_us=150000,duration_us=10000)])
    assert r['summary']['completed'] == 3
    assert all(f['ingress_complete_ns'] < 150000000 for f in r['frames'])
    assert r['summary']['channels'][0]['final_state'] == 'recovering'
    assert not any(e['kind']=='health_normal' for e in r['events'])


def test_stop_discards_partial_frame_and_start_accepts_next_complete_frame():
    pc, channels = setup(frames=8, profile={'width':1920,'height':10,'bits':12})
    commands = DEFAULT_COMMANDS + [dict(command='stop',at_us=130010),dict(command='start',at_us=131000)]
    r = run(pc, channels, commands=commands)
    assert r['frames'][0]['loss_reason'] == 'stopped_mid_frame'
    assert r['summary']['completed'] == 7
    assert r['frames'][1]['completed_ns'] is not None


def test_lock_restart_uses_latest_epoch():
    pc, channels = setup(frames=1, profile={'boot_us':0})
    r = run(pc, channels, commands=[dict(command='configure',at_us=0),dict(command='start',at_us=1)],
            faults=[dict(kind='link_down',channel=0,at_us=1000,duration_us=2000)])
    locked = [e['time_ns'] for e in r['events'] if e['kind']=='link_locked']
    assert locked == [13000000]


def test_before_ready_and_duplicate_identifiers():
    pc, channels = setup(frames=8)
    channels[0]['source_offset_us'] = 0
    r = run(pc, channels)
    assert [f['loss_reason'] for f in r['frames'][:3]] == ['ecu_not_ready']*3
    assert r['summary']['completed'] == 5
    pc, channels = setup(frames=6)
    channels[0]['duplicate_every'] = 2
    r = run(pc, channels)
    assert r['summary']['dropped'] == 3
    assert all(f['loss_reason']=='duplicate_frame_id' for f in r['frames'][1::2])
    assert r['summary']['channels'][0]['missing_frame_ids'] == [1, 3]
    assert r['summary']['channels'][0]['duplicate_frames'] == 3
    channels[0]['duplicate_every'] = 1
    r = run(pc, channels)
    assert [f['frame_id'] for f in r['frames']] == [0]*6
    assert r['summary']['dropped'] == 5


def test_bounded_des_receive_queues_and_processing_deadline():
    pc, channels = setup(frames=1, profile={'des_buffer_bytes':4,'csi_lanes':1,'csi_lane_Mbps':1,'csi_efficiency_percent':100})
    r = run(pc, channels)
    assert r['frames'][0]['loss_reason'] == 'des_overflow'
    assert r['summary']['channels'][0]['peak_des_bytes'] == 4
    pc, channels = setup(frames=10, profile={'processing_us':100000,'receive_queue_frames':1,'processing_deadline_us':50000})
    r = run(pc, channels)
    assert any(f['loss_reason']=='receive_queue_full' for f in r['frames'])
    assert r['summary']['peak_receive_queue_frames'] == 1
    assert r['summary']['deadline_exceeded'] > 0
    assert r['summary']['completed']+r['summary']['dropped'] == 10
    assert all(e['receive_queue_frames'] <= 1 for e in r['events'])


def test_processor_stall_pauses_inflight_work_exactly():
    pc, channels = setup(frames=1)
    baseline = run(pc, channels)
    stalled = run(pc, channels, faults=[dict(kind='processor_stall',at_us=131000,duration_us=5000,channel=None)])
    assert stalled['frames'][0]['completed_ns'] == baseline['frames'][0]['completed_ns']+5000000


def test_exact_processing_deadline_passes_and_queue_wait_is_included():
    pc, channels = setup(frames=1, profile={'processing_deadline_us':4000})
    assert run(pc, channels)['summary']['deadline_exceeded'] == 0
    pc, channels = setup(frames=1, count=2, profile={'processing_deadline_us':5000})
    r = run(pc, channels)
    assert not r['frames'][0]['processing_deadline_exceeded']
    assert r['frames'][1]['processing_deadline_exceeded']


def test_reset_invalidates_active_processing_then_reconfigures():
    pc, channels = setup(frames=10)
    commands = DEFAULT_COMMANDS + [dict(command='reset',at_us=131000),dict(command='configure',at_us=231000),dict(command='start',at_us=241000)]
    r = run(pc, channels, commands=commands)
    assert r['frames'][0]['loss_reason'] == 'reset_discard'
    assert r['frames'][0]['completed_ns'] is None
    assert r['frames'][-1]['status'] == 'completed'
    assert r['summary']['channels'][0]['final_state']=='normal'


def test_heartbeat_loss_event_driven_and_no_false_recovery_between_adjacent_outages():
    pc, channels = setup(frames=6, profile={'heartbeat_period_us':1,'heartbeat_timeout_us':3})
    faults = [dict(kind='heartbeat_loss',at_us=140000,duration_us=2000,channel=None),dict(kind='heartbeat_loss',at_us=142000,duration_us=2000,channel=None)]
    r = run(pc, channels, faults=faults)
    heartbeat = [e for e in r['events'] if e['kind'].startswith('heartbeat_')]
    assert [(e['kind'],e['time_ns']) for e in heartbeat] == [('heartbeat_timeout',140002000),('heartbeat_recovered',144000000)]
    assert len(r['events']) < 200
    assert r['summary']['completed'] == 6


def test_seed_reproducibility_ordered_causes_and_cancellation():
    pc, channels = setup(design={'jitter_us':1000})
    r = run(pc, channels, seed=42)
    assert r == run(pc, channels, seed=42)
    assert r != run(pc, channels, seed=43)
    channels[0]['design']['seed'] = 43
    assert [f['trigger_ns'] for f in r['frames']] != [f['trigger_ns'] for f in run(pc, channels, seed=43)['frames']]
    assert [e['time_ns'] for e in r['events']] == sorted(e['time_ns'] for e in r['events'])
    assert all(e['cause_event_id'] is None or e['cause_event_id'] < e['id'] for e in r['events'])
    with pytest.raises(AdapterCancelledBeforeDispatch):
        run(pc, channels, should_cancel=lambda:True)


def test_profile_owns_receiver_expectation_and_no_idle_tail_timeout():
    pc, channels = setup()
    normal = run(pc, channels)
    channels[0]['design']['config']['expected_fps'] = 120
    modified = run(pc, channels)
    assert normal['frames'] == modified['frames']
    assert modified['summary']['final_state']=='normal'
    assert not any(e['kind']=='input_timeout' for e in modified['events'])


@pytest.mark.parametrize('change', ['raw','mapping','budget','fault_scope','overlap','horizon'])
def test_reject_unsupported_combined_inputs(change):
    pc, channels = setup()
    faults = []
    if change=='raw': channels[0]['design']['config']['bits']=12
    elif change=='mapping': channels.append(copy.deepcopy(channels[0]))
    elif change=='budget':
        channels *= 4
        channels = [dict(c,channel=i,vc=i,design={'config':dict(c['design']['config'],frames=40),'seed':42}) for i,c in enumerate(channels)]
    elif change=='fault_scope': faults=[dict(kind='link_down',at_us=1000,duration_us=100,channel=3)]
    elif change=='overlap': faults=[dict(kind='link_down',at_us=1000,duration_us=100,channel=0),dict(kind='link_down',at_us=1001,duration_us=100,channel=0)]
    else:
        pc['processing_us']=1000000
        channels[0]['design']['config']['frames']=100
    with pytest.raises(ValueError): run(pc,channels,faults=faults)
