"""Independent assertions over collected wire/ECU/CARLA evidence, not authored outputs."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--evidence', type=Path, required=True)
a = p.parse_args()
records = json.loads((a.evidence / 'final-records.json').read_text())
report = {}
sessions = set()
for kind, record in records.items():
    if kind in ('feedback_loss', 'fixed_normal'):
        continue
    folder = a.evidence / 'native' / record['execution']
    trace = json.loads((folder / 'vecu-trace.json').read_text())
    cause = json.loads((folder / 'hil-causality.json').read_text())['cases'][0]
    for side, rows in trace['sides'].items():
        assert len(rows) == 360
        session = rows[0]['reply']['bridge']['session']
        assert session not in sessions
        sessions.add(session)
        assert all(not r['reply']['bridge']['validity_errors'] for r in rows)
        assert all(r['reply']['bridge']['feedback_sample_phase_ms'] == 12 for r in rows)
    rows = trace['sides']['faulted']
    events = [e for r in rows for e in r['reply']['bridge']['events']]
    def select(name):
        return [e for e in events if e['kind'] == name]
    def during(e):
        return e.get('time_ms') is not None and 3000 <= e['time_ms'] < 7000
    generated = {e['sequence']: e for e in select('object_generated')}
    tx = select('object_tx_accepted')
    received = select('object_rx')
    # The independent ADAS receiver reports the exact transmitted bytes' hash.
    assert {(e['sequence'], e['payload_sha256']) for e in tx} == {
        (e['sequence'], e['payload_sha256']) for e in received}
    checks = {'separate_normal_fault_sessions': True, 'UDP_tx_rx_payload_hashes_match': True}
    if kind == 'udp_delay':
        delayed = [e for e in tx if 60 <= e['sequence'] < 140]
        latencies = [e['time_ms'] - generated[e['sequence']]['time_ms'] for e in delayed]
        assert len(select('object_queued')) == 80 and len(delayed) == 72
        assert 399 <= min(latencies) <= max(latencies) < 405
        assert all(e['payload_sha256'] == generated[e['sequence']]['payload_sha256'] for e in delayed)
        checks.update(delayed_packets=72, pending_packets_discarded_at_recovery=8,
                      measured_generated_to_tx_ms=[min(latencies), max(latencies)])
    elif kind == 'udp_drop':
        assert len([e for e in select('injection_drop') if during(e)]) == 80
        assert not any(during(e) for e in tx)
        checks['no_object_tx_during_fault'] = True
    elif kind == 'udp_repeat':
        repeats = [e for e in tx if during(e)]
        assert len(repeats) == len(select('packet_repeat')) == 80
        assert {e['sequence'] for e in repeats} == {59}
        assert {e['payload_sha256'] for e in repeats} == {generated[59]['payload_sha256']}
        assert max(r['reply']['duplicate_packet_count'] for r in rows) == 80
        checks['same_prior_packet_sent_and_detected_80_times'] = True
    elif kind == 'restbus_drop':
        assert len([e for e in select('injection_drop') if during(e)]) == 400
        assert not any(during(e) and e['can_id'] in (512, 513) for e in select('can_tx_accepted'))
        assert cause['stages']['detection']['reason'] == 'heartbeat_timeout'
        checks['both_Restbus_CAN_IDs_suppressed'] = True
    elif kind == 'can_command_drop':
        upstream = [e for e in select('can_command_observed') if during(e)]
        assert len(upstream) == 80
        assert not any(during(e) and e['interface'] == 'axiomhb' and e['can_id'] == 256
                       for e in select('can_tx_accepted'))
        assert cause['stages']['detection']['ecu'] == 'brake'
        assert cause['stages']['detection']['reason'] == 'command_watchdog'
        assert cause['stages']['can_command'] is None
        checks['80_upstream_commands_and_zero_forwarded_to_Brake'] = True
    # Report observed recovery; do not equate a brake command with immediate vehicle motion.
    released = next(r for r in rows[140:] if r['reply']['control']['brake'] < 1)
    moving = next(r for r in rows[140:] if r['next_ego']['speed_mps'] > .1)
    report[kind] = dict(execution=record['execution'], checks=checks,
                       detection_reason=cause['stages']['detection']['reason'],
                       measured_from_injection_ms=cause['latency_ms'],
                       first_brake_release_input_ms=released['step'] * 50,
                       first_speed_over_0_1_mps_model_ms=(moving['step'] + 1) * 50,
                       physical_validation=False)
(a.evidence / 'wire-and-response-checks.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
