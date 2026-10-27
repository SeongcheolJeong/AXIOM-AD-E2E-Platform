"""Authored monitor schedules, independent of actual Linux/CARLA acceptance."""
import copy
import pytest
from axiom_hil_gateway.model_monitor import ModelMonitor
GUARD=dict(algorithm='fresh-window-v2',period_ms=50,fresh_windows=2,max_age_ms=10)
def policy(qualified):
    p=dict(boundary='multiple',combination='any_protect',members={n:dict(boundary=n,min_hold_ms=500,recovery_samples=3,power_min_v=8,analog_min_v=2,analog_max_v=3) for n in ('power','fpga')})
    if qualified:p['release_qualification']=GUARD
    return p

def sample(seq,power_bad=False,fpga_bad=False):
    return dict(sequence=seq,samples=dict(power=dict(sequence=seq,phase='native_replay',min_v=6 if power_bad else 12),
        fpga=dict(sequence=seq,phase='native_replay',abort_count=int(fpga_bad),last_status='aborted' if fpga_bad else 'committed')))

def exercise(qualified,phase,recurrence=4350):
    m=ModelMonitor(policy(qualified));reports=[]
    # Envelope of a 300ms sag includes the value at its left edge. Phase is the
    # ECU scheduling offset within the existing0..10ms valid observation age.
    for seq in range(125):
        t=seq*50
        power_bad=3050<=t<=3350 or recurrence<t<=recurrence+350
        r=m.step(sample(seq,power_bad,t in (3050,3850)),seq,t+phase(seq))
        reports.append(r)
    return reports

def test_original_gap_is_reproduced_and_completed_windows_block_it():
    before=exercise(False,lambda seq:5);after=exercise(True,lambda seq:5)
    assert before[87]['recovered'] and not before[87]['protect']
    assert before[88]['detected'] and before[88]['protect']
    assert after[87]['release_guard']['pending'] and not after[87]['active_boundaries']
    assert after[88]['active_boundaries']==['power'] and not after[88]['recovered']
    assert all(r['protect'] for r in after[61:105])
    assert after[105]['release_guard']['pending']
    assert after[106]['recovered'] and not after[106]['protect']

@pytest.mark.parametrize('recurrence',range(4301,4401))
@pytest.mark.parametrize('phase',[0,1,5,9,10])
def test_every_source_ms_around_release_boundary_and_ecu_phase_has_no_gap(recurrence,phase):
    r=exercise(True,lambda seq:phase+(0.0 if seq%2 else -.25) if phase else .0,recurrence)
    # Two complete windows include one full50ms interval after the candidate.
    # This does not assert knowledge of faults after the confirmed observation.
    last_bad=max(i for i in range(125) if recurrence<i*50<=recurrence+350)
    assert all(v['protect'] for v in r[61:last_bad+10])
    assert any(v['recovered'] for v in r[last_bad+10:])

@pytest.mark.parametrize('mutation',['stale','future','duplicate','missing','jump'])
def test_bad_confirmation_input_cannot_release(mutation):
    m=ModelMonitor(policy(True))
    for seq in range(88):
        m.step(sample(seq,3050<=seq*50<=3350,seq*50 in (3050,3850)),seq,seq*50+5)
    assert m.latched and m.release_candidate
    seq=88;data=sample(seq);now=seq*50+5
    if mutation=='stale':now=seq*50+11
    if mutation=='future':now=seq*50-1
    if mutation=='duplicate':seq=87;data=sample(seq);now=seq*50+5
    if mutation=='missing':data=None
    if mutation=='jump':seq=89;data=sample(seq);now=seq*50+5
    r=m.step(data,seq,now)
    assert r['protect'] and not r['recovered'] and not r['release_guard']['fresh']


def test_fpga_recurs_on_final_confirmation_tick_cancels_candidate_and_requalifies():
    m=ModelMonitor(policy(True));rows=[]
    for seq in range(130):
        t=seq*50
        power_bad=3050<=t<=3350 or 4400<=t<=4700
        fpga_bad=t in (3050,3850,5300)
        rows.append(m.step(sample(seq,power_bad,fpga_bad),seq,t+5))
    assert rows[105]['release_guard']['pending']
    assert rows[105]['release_guard']['candidate']['required_sequence']==106
    assert rows[106]['members']['fpga']['detected']
    assert rows[106]['release_guard']['candidate'] is None
    assert rows[106]['release_guard']['reason']=='active_fault'
    assert all(r['protect'] and not r['recovered'] for r in rows[61:118])
    assert rows[116]['release_guard']['pending'] and rows[116]['release_guard']['candidate']['sequence']==116
    assert rows[118]['recovered'] and rows[118]['release_guard']['confirmed_sequence']==118


@pytest.mark.parametrize('phase',[0,1,5,9,10])
def test_power_recurs_during_new_wait_cancels_again_until_all_recovered(phase):
    m=ModelMonitor(policy(True));rows=[]
    for seq in range(150):
        t=seq*50
        power_bad=3050<=t<=3350 or 4400<=t<=4700 or 5900<=t<=6200
        rows.append(m.step(sample(seq,power_bad,t in (3050,3850,5300)),seq,t+phase))
    assert rows[117]['release_guard']['pending'] and rows[117]['release_guard']['candidate']['required_sequence']==118
    assert rows[118]['members']['power']['detected']
    assert rows[118]['release_guard']['candidate'] is None and rows[118]['release_guard']['reason']=='active_fault'
    assert rows[118]['active_boundaries']==['power']
    assert all(r['protect'] and not r['recovered'] for r in rows[61:136])
    assert rows[134]['release_guard']['candidate']['required_sequence']==136
    assert rows[136]['recovered'] and not rows[136]['protect']


def test_processing_phase_can_defer_candidate_one_tick_without_changing_hold_or_release_rules():
    m=ModelMonitor(policy(True));rows=[]
    for seq in range(150):
        t=seq*50;phase=5.51 if seq==106 else 5.36 if seq==116 else 5.08
        power_bad=3050<=t<=3350 or 4400<=t<=4700 or 5900<=t<=6200
        rows.append(m.step(sample(seq,power_bad,t in (3050,3850,5300)),seq,t+phase))
    assert rows[116]['members']['fpga']['hold_remaining_ms']==pytest.approx(.15)
    assert not rows[116]['release_guard']['pending'] and rows[116]['protect']
    candidate=rows[117]['release_guard']['candidate']
    assert candidate['sequence']==117 and candidate['required_sequence']==119
    assert rows[118]['release_guard']['reason']=='active_fault' and rows[118]['release_guard']['candidate'] is None
    assert all(r['protect'] and not r['recovered'] for r in rows[61:136])
    assert rows[136]['recovered'] and not rows[136]['protect']


@pytest.mark.parametrize('phase',[0,1,5,9,10])
def test_fpga_recurs_after_third_power_sag_and_cancels_pending_release_again(phase):
    m=ModelMonitor(policy(True));rows=[]
    for seq in range(165):
        t=seq*50
        power_bad=3050<=t<=3350 or 4400<=t<=4700 or 5900<=t<=6200
        fpga_bad=t in (3050,3850,5300,6800)
        rows.append(m.step(sample(seq,power_bad,fpga_bad),seq,t+phase))
    candidate=rows[135]['release_guard']['candidate']
    assert rows[135]['release_guard']['pending'] and candidate['required_sequence']==136
    assert rows[136]['members']['fpga']['detected']
    assert rows[136]['release_guard']['candidate'] is None and rows[136]['release_guard']['reason']=='active_fault'
    assert rows[136]['active_boundaries']==['fpga'] and not rows[136]['members']['power']['protect']
    assert all(r['protect'] and not r['recovered'] for r in rows[61:148])
    assert rows[146]['release_guard']['candidate']['required_sequence']==148
    assert rows[148]['recovered'] and not rows[148]['protect']
