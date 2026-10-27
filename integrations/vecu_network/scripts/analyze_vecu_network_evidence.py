#!/usr/bin/env python3
"""Audit retained, actually executed CARLA Network Lab evidence; never execute a DUT.

The export is a JSON manifest, not a ZIP. Referenced artifacts are read from the
local immutable service store. Every byte is hashed, including every camera
archive and BGRA member. Results compare observed CARLA response states, not a
fixed synthetic replay. NetworkAudit is an independent post-execution oracle.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import urllib.request
import zipfile

from ade2e.service import digest
from axiom_carla import hil_network
from axiom_carla.hil_network import NetworkAudit

CASES = ['udp_delay', 'udp_drop', 'udp_repeat', 'can_command_drop',
         'restbus_drop', 'ecu_reset', 'os_event_drop']
RETAINED_NONPASS = ['job_6d4037852879487cb1ebda8036b0f091',
                   'job_2ea34c0a26c44dff82828283ffad3252',
                   'job_d8e9f4239c41471d8bd17e2224159945',
                   'job_7fdd54c7af8c451f977a52a1db04200b']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def api_json(base, path):
    with urllib.request.urlopen(base.rstrip('/') + path, timeout=60) as response:
        return json.load(response)


def first(rows, predicate, after=3.):
    return next((r['request']['time_s'] for r in rows
                 if r['request']['time_s'] >= after and predicate(r)), None)


def side_metrics(rows, scene):
    events = [event for row in rows for event in row['reply']['network']['events']]
    response = first(rows, lambda r: r['reply']['state'] != 'tracking')
    recovery = (first(rows, lambda r: r['reply']['state'] == 'tracking', response + .001)
                if response is not None else None)
    cpp = [v for row in rows for v in row['reply']['network']['controller_steps']]
    actual_ego = next(m for m in scene['metrics'] if m['actor_id'] == 'ego')
    return {
        'steps': len(rows), 'initial_degraded_steps_before_3s': sum(
            r['request']['time_s'] < 3 and r['reply']['state'] != 'tracking' for r in rows),
        'first_degraded_after_fault_s': response,
        'first_full_brake_after_fault_s': first(rows, lambda r: r['next_ego']['control']['brake'] >= .9999),
        'first_tracking_after_response_s': recovery,
        'first_brake_release_after_recovery_s': (first(rows, lambda r: r['next_ego']['control']['brake'] < .9999, recovery)
                                                if recovery is not None else None),
        'first_observed_speed_above_0_1_after_recovery_s': (first(rows, lambda r: r['next_ego']['speed_mps'] > .1, recovery)
                                                         if recovery is not None else None),
        'first_tracking_at_or_after_7s': first(rows, lambda r: r['reply']['state'] == 'tracking', 7.),
        'post_fault_state_counts': dict(collections.Counter(r['reply']['state'] for r in rows
                                                           if r['request']['time_s'] >= 3)),
        'final_state': rows[-1]['reply']['state'],
        'native_cpp_activations': len(cpp),
        'os_control_tasks': rows[-1]['reply']['network']['stats']['os_control_tasks'],
        'network_stats': rows[-1]['reply']['network']['stats'],
        'event_counts': dict(collections.Counter(e['kind'] for e in events)),
        'brake_can_command_rx': sum(e['kind'] == 'can_rx' and e.get('can_id') == 0x100
                                    and e.get('participant') == 'brake' for e in events),
        'bsw_operations': sum(e['layer'] == 'bsw' for e in events),
        'actual_ego_metrics': actual_ego,
        'sampled_response': [{
            'input_time_s': rows[i]['request']['time_s'], 'response_time_s': (i + 1) * .05,
            'ecu_state': rows[i]['reply']['state'],
            'brake_reason': rows[i]['reply']['network']['brake']['reason'],
            'applied_brake': rows[i]['next_ego']['control']['brake'],
            'observed_speed_mps': rows[i]['next_ego']['speed_mps'],
            'observed_x': rows[i]['next_ego']['x'], 'observed_y': rows[i]['next_ego']['y'],
        } for i in [59, 60, 62, 65, 70, 80, 100, 139, 140, 143, 148, 160, 359]],
    }


def verify_side(side, rows, artifacts, read, prepared, summary, network_events):
    scene = read(side + '-city-scene.json')
    hil = read(side + '-hil-trace.json')
    receipt = read(side + '-native-receipt.json')
    require(receipt['status'] == 'completed' and receipt['cleanup_status'] == 'confirmed'
            and receipt['hil_dut_exit_confirmed'], side + ': native process or cleanup incomplete')
    require(receipt['drop_count'] == 0 and receipt['frame_count'] == 360
            and receipt['camera_count'] == 360, side + ': incomplete actual CARLA capture')
    for item in receipt['artifacts']:
        declared = artifacts[side + '-' + item['name']]
        require(all(declared[k] == item[k] for k in ('sha256', 'size_bytes', 'media_type')),
                side + ': original receipt artifact differs')
    require(len(rows) == len(scene['frames']) == len(hil['rows']) == 360, side + ': step denominator')
    require(scene['hil']['camera_is_dut_input'] is False
            and scene['hil']['object_source'] == 'carla_actor_ground_truth', 'Object input boundary changed')
    spec = next(a for a in prepared['carla']['city_snapshot']['scenario']['actors'] if a['role'] == 'ego')
    init = dict(op='init', schema_version='hil-dut-init/1', route=spec['route'],
                target_speed_mps=spec['speed_mps'], physics=scene['vehicle_physics'][spec['id']],
                dut_profile=hil['dut_profile'], network_runtime=hil['runtime'])
    require(sha(Path(hil_network.__file__)) == prepared['adapter_sources']['axiom_carla/hil_network.py'],
            'Retained source-pinned NetworkAudit changed; use matching environment')
    audit = NetworkAudit(init)
    for i, (row, native, original) in enumerate(zip(rows, scene['frames'], hil['rows'])):
        require(row['step'] == row['request']['sequence'] == i and row['time_ns'] == i * 50000000,
                side + ': outer timeline mismatch')
        require(abs(row['request']['time_s'] - i * .05) < 1e-8, side + ': input clock mismatch')
        require(row['request'] == original['request'] and row['reply'] == original['reply'],
                side + ': compact trace differs from original HIL trace')
        ego = next(a for a in native['actors'] if a['id'] == spec['id'])
        require(row['next_ego'] == original['next_ego'] == ego, side + ': observed CARLA state differs')
        require(row['reply']['control'] == original['requested_control'] == ego['control'],
                side + ': ECU command differs from CARLA applied control')
        require(all(abs(original['applied_control'][k] - ego['control'][k]) < 1e-5
                    for k in ('throttle', 'brake', 'steer')), side + ': native control readback differs')
        require(all(a['error'] is None for a in original['command_acknowledgements']),
                side + ': CARLA command batch rejected')
        audit.check(row['request'], row['reply'])
    events = [e for r in rows for e in r['reply']['network']['events']]
    require(events == network_events, side + ': standalone network trace differs')
    require(digest([{k: v for k, v in r.items() if k != 'wall_latency_ms'} for r in rows]) ==
            summary['sides'][side]['logical_sha256'], side + ': logical trace hash differs')
    require(digest([r['request'] for r in rows]) == summary['sides'][side]['input_sha256'],
            side + ': input trace hash differs')
    require(rows[-1]['reply']['network']['stats'] == summary['network']['sides'][side],
            side + ': network summary differs')
    # Verify every retained BGRA image against the native scene's manifest.
    cameras = collections.Counter()
    members = 0
    member_bytes = 0
    codecs = collections.Counter()
    seen_members = set()
    archives = {}
    try:
        for record in scene['camera_records']:
            name = side + '-' + record['archive']
            if name not in archives:
                archives[name] = zipfile.ZipFile(artifacts[name]['path'])
            require((name, record['member']) not in seen_members, side + ': repeated camera member')
            seen_members.add((name, record['member']))
            info = archives[name].getinfo(record['member'])
            require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    side + ': unsupported camera archive compression')
            codecs['stored' if info.compress_type == zipfile.ZIP_STORED else 'deflated'] += 1
            raw = archives[name].read(record['member'])
            member_bytes += len(raw)
            require(len(raw) == record['size_bytes'] == record['width'] * record['height'] * 4,
                    side + ': invalid BGRA byte count')
            require(hashlib.sha256(raw).hexdigest() == record['sha256'], side + ': camera member hash differs')
            require(abs(record['time_s'] - (record['index'] + 1) * .05) < 1e-8,
                    side + ': camera time is not post-control response time')
            require(record['frame'] == scene['frames'][record['index']]['frame'], side + ': camera frame mismatch')
            cameras[record['sensor_id']] += 1
            members += 1
        require(sum(len(z.infolist()) for z in archives.values()) == members,
                side + ': camera archive contains unreferenced members')
    finally:
        for archive in archives.values():
            archive.close()
    require(dict(cameras) == {'front': 180, 'chase': 180}, side + ': missing camera frames')
    return side_metrics(rows, scene), dict(
        native_external_execution_id=receipt['external_execution_id'], audited_steps=360,
        native_control_readback='passed', independent_network_audit='passed',
        original_receipt_hashes='passed', camera_members_verified=members,
        camera_frames_by_sensor=dict(cameras), camera_member_compression=dict(codecs),
        raw_bgra_bytes_verified=member_bytes, cleanup_status=receipt['cleanup_status'])


def analyze(case, args):
    path = args.evidence / (case + '-result.json')
    result = json.loads(path.read_bytes())
    export_path = args.evidence / (case + '-export.json')
    if not export_path.exists():
        export_path = args.evidence / (case + '-export.zip')  # Legacy filename; bytes must still be JSON.
    export = json.loads(export_path.read_bytes())
    require(sha(export_path) == result['manifest_hash'], case + ': export manifest SHA differs')
    require(export['schema_version'] == 1, case + ': unexpected export schema')
    require(export['execution']['id'] == result['id'] and export['execution']['artifacts'] == result['artifacts'],
            case + ': result/export identity differs')
    actual = api_json(args.api, '/api/v1/virtual-ecu-network/executions/' + result['id'] +
                      '?project_id=' + result['project_id'])
    require(actual == result, case + ': saved receipt differs from current API receipt')
    summary = result['summary']
    require(result['status'] == 'completed' and summary['mode'] == 'carla_closed_loop'
            and summary['checks']['driving_safety'] == 'not_evaluated', case + ': scope/status differs')
    prepared = export['prepared']['input']
    require(prepared['test']['faults'][0]['kind'] == case and
            prepared['test']['faults'][0]['start_ms'] == 3000 and
            prepared['test']['faults'][0]['end_ms'] == 7000, case + ': fault definition differs')
    artifacts = {}
    for artifact in result['artifacts']:
        local = args.artifacts / artifact['id']
        require(not local.is_symlink() and local.is_file(), case + ': artifact missing')
        require(local.stat().st_size == artifact['size_bytes'] and sha(local) == artifact['sha256'],
                case + ': artifact SHA/size differs: ' + artifact['name'])
        artifacts[artifact['name']] = {**artifact, 'path': local}
    def read(name):
        return json.loads(artifacts[name]['path'].read_bytes())
    trace, net = read('vecu-trace.json'), read('network-trace.json')
    require(read('vecu-provenance.json') == prepared, case + ': provenance snapshot differs')
    require(read('vecu-summary.json') == summary, case + ': summary artifact differs')
    require(read('network-summary.json') == summary['network'], case + ': network summary artifact differs')
    require(digest(net['sides']) == summary['network']['logical_sha256'], case + ': network events hash differs')
    metrics, side_checks = {}, {}
    for side in ['baseline', 'faulted']:
        metrics[side], side_checks[side] = verify_side(side, trace['sides'][side], artifacts,
                                                     read, prepared, summary, net['sides'][side])
    a, b = trace['sides']['baseline'], trace['sides']['faulted']
    max_speed = max(abs(x['next_ego']['speed_mps'] - y['next_ego']['speed_mps']) for x, y in zip(a, b))
    max_position = max(math.hypot(x['next_ego']['x'] - y['next_ego']['x'],
                                 x['next_ego']['y'] - y['next_ego']['y']) for x, y in zip(a, b))
    require(max_speed > .01 and max_position > .01, case + ': no observed CARLA dynamics difference')
    require(metrics['baseline']['first_degraded_after_fault_s'] is None, case + ': baseline not healthy')
    require(metrics['faulted']['first_degraded_after_fault_s'] is not None and
            metrics['faulted']['final_state'] == 'tracking', case + ': missing fault response/recovery')
    m = metrics['faulted']; counts = m['event_counts']; stats = m['network_stats']
    require(counts.get('fault_start') == counts.get('fault_end') == 1, case + ': missing fault edges')
    expected = {'udp_delay': ('udp_queued', 80), 'udp_drop': ('udp_drop', 80),
                'udp_repeat': ('udp_duplicate', 80), 'can_command_drop': ('can_drop', 80),
                'restbus_drop': ('can_drop', 800)}
    if case in expected:
        kind, count = expected[case]
        require(counts.get(kind) == count, case + ': injection event denominator differs')
    if case in ('can_command_drop', 'os_event_drop'):
        require(stats['watchdog_events'] >= 1 and stats['udp_received'] == 359,
                case + ': watchdog/input-path evidence differs')
    if case == 'restbus_drop':
        require(stats['heartbeat_timeouts'] == 1, case + ': heartbeat timeout missing')
    if case == 'ecu_reset':
        require(stats['resets'] == 1 and b[-1]['reply']['network']['epoch'] == 1 and
                m['first_tracking_after_response_s'] < 7, case + ': 200ms reset/recovery differs')
    if case == 'os_event_drop':
        require(m['native_cpp_activations'] == 279 and m['os_control_tasks'] == 279,
                case + ': OS loss did not gate native C++ activations')
    return dict(case=case, execution_id=result['id'], test_ref=summary['test_ref'],
                manifest_sha256=result['manifest_hash'], native_install_sha256=summary['network']['manifest_sha256'],
                network_ref=summary['network_ref'], seed=summary['seed'], fault=prepared['test']['faults'][0],
                metrics=metrics, actual_vehicle_comparison=dict(
                    maximum_observed_speed_delta_mps=max_speed, maximum_observed_position_delta_m=max_position,
                    final_distance_delta_m=metrics['faulted']['actual_ego_metrics']['distance_travelled_m'] -
                                           metrics['baseline']['actual_ego_metrics']['distance_travelled_m']),
                ui_url=args.ui.rstrip('/') + '/#hil-sim?project=' + result['project_id'] +
                    '&mode=vecu&tab=network&test=' + summary['test_ref']['id'] + '&revision=1&execution=' + result['id'] + '&step=70',
                checks=dict(export_media_type='application/json', export_sha256='passed', api_receipt='passed',
                            artifact_count=len(artifacts), artifact_bytes=sum(a['size_bytes'] for a in artifacts.values()),
                            artifact_hashes='passed', sides=side_checks, driving_safety='not_evaluated'),
                artifacts=[{k: v for k, v in a.items() if k != 'path'} for a in artifacts.values()])


def markdown(report):
    text = ['# Actual CARLA network fault A/B — 2026-10-17', '',
        'These are fresh native CARLA closed-loop executions, 18 s × 20 Hz per side, seed 42. '
        'SIL Kit frames, autoas Com/PduR/CanIf, Trampoline tasks and the C++ controller ran as native processes. '
        'The post-run audit checked every control against the recorded CARLA response. ', '',
        'All fault windows are [3, 7) s. Initial 0–0.2 s fail-safe braking is excluded from fault-response metrics. '
        'Times below are ECU input times sampled every 50 ms; the corresponding captured response is at input + 50 ms. '
        'The reset case restarts ADAS after 200 ms and may recover before the fault window ends.', '',
        '| Fault | Degraded | Full brake | Tracking after response | Tracking ≥7 s | C++ / OS | Brake CAN RX | UDP duplicates | Max speed Δ |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for case in report['cases']:
        m = case['metrics']['faulted']; comp = case['actual_vehicle_comparison']
        value = lambda x: '—' if x is None else f'{x:.2f} s'
        text.append(f"| {case['case']} | {value(m['first_degraded_after_fault_s'])} | "
                    f"{value(m['first_full_brake_after_fault_s'])} | {value(m['first_tracking_after_response_s'])} | "
                    f"{value(m['first_tracking_at_or_after_7s'])} | {m['native_cpp_activations']} / {m['os_control_tasks']} | "
                    f"{m['brake_can_command_rx']} | {m['network_stats']['udp_duplicates']} | "
                    f"{comp['maximum_observed_speed_delta_mps']:.3f} m/s |")
    text += ['', '| Fault | Speed at response 4.05 s, A / B | Speed at response 7.45 s, A / B | Final distance, A / B |',
        '|---|---:|---:|---:|']
    for case in report['cases']:
        a, b = case['metrics']['baseline'], case['metrics']['faulted']
        sample = lambda side, t: next(v for v in side['sampled_response'] if abs(v['input_time_s'] - t) < 1e-8)['observed_speed_mps']
        text.append(f"| {case['case']} | {sample(a, 4.):.3f} / {sample(b, 4.):.3f} m/s | "
                    f"{sample(a, 7.4):.3f} / {sample(b, 7.4):.3f} m/s | "
                    f"{a['actual_ego_metrics']['distance_travelled_m']:.3f} / {b['actual_ego_metrics']['distance_travelled_m']:.3f} m |")
    text += ['', 'Each baseline is independently executed; compare its paired fault run. CARLA camera images are '
        'observations of the response. Inputs to the reference application are CARLA actor ground-truth objects, '
        'not image perception. No physical ECU or network PHY, CAN arbitration/WCET, full AUTOSAR qualification, '
        'or driving-safety validation is claimed. `driving_safety = not_evaluated`.', '',
        'The export is a JSON manifest referencing immutable artifacts. This audit verifies the manifest SHA, '
        'API receipt, all artifact SHA/size values, original native receipts, every camera archive/member, '
        '360 independent NetworkAudit checks per side, and actual native control readback. '
        'Earlier captures retain ZIP STORED containers; newer network captures use lossless DEFLATE. '
        'Both preserve every BGRA byte and per-image hash. ', '',
        '| Fault | Execution / retained UI |', '|---|---|']
    text += [f"| {c['case']} | [{c['execution_id']}]({c['ui_url']}) |" for c in report['cases']]
    text += ['', 'Prior failed/cancelled executions are retained separately and excluded from successful comparisons:']
    text += [f"- `{r['id']}` — {r['status']}: {r.get('error') or 'cancelled before execution'}"
             for r in report['retained_nonpassing_executions']]
    text += ['', f"Native installation: `{report['native_install_sha256']}`.", '',
             'Machine-readable metrics, identities, artifact hashes and validation counts are in '
             '`carla-comparison.json` and `carla-checks.json`.', '']
    return '\n'.join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path('e2e/.runtime/vecu-network-acceptance-20261017'))
    parser.add_argument('--artifacts', type=Path, default=Path('e2e/.runtime/vecu-network-20261017/experiments/artifacts'))
    parser.add_argument('--output', type=Path, default=Path('docs/validation/vecu-network-20261017'))
    parser.add_argument('--api', default='http://127.0.0.1:8230')
    parser.add_argument('--ui', default='http://127.0.0.1:5330')
    parser.add_argument('--nonpassing-execution', action='append', default=[])
    parser.add_argument('--cases', nargs='+', choices=CASES, default=CASES)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version='vecu-network-carla-evidence-audit/1', status='running', cases=[],
                  analyzer_sha256=sha(Path(__file__)), retained_nonpassing_executions=[])
    try:
        for case in args.cases:
            result = analyze(case, args)
            report['cases'].append(result)
            print(json.dumps(dict(case=case, status='passed', execution=result['execution_id'],
                                  fault_metrics=result['metrics']['faulted'])), flush=True)
        ids = {c['native_install_sha256'] for c in report['cases']}
        require(len(ids) == 1, 'Cases used different native installations')
        report['native_install_sha256'] = ids.pop()
        for job in dict.fromkeys(RETAINED_NONPASS + args.nonpassing_execution):
            r = api_json(args.api, '/api/v1/virtual-ecu-network/executions/' + job +
                         '?project_id=project_9f48a965d4194be0b18859e334e007bb')
            require(r['status'] in ('failed', 'cancelled'), 'Prior failed/cancelled status changed')
            report['retained_nonpassing_executions'].append({k: r.get(k) for k in
                ('id', 'status', 'error', 'input_hash', 'manifest_hash', 'created_at', 'finished_at', 'outcome_uncertain')})
        report['status'] = 'passed'; report['complete_seven_case_suite'] = args.cases == CASES
        checks = dict(status='passed', complete_seven_case_suite=report['complete_seven_case_suite'],
            executions=len(report['cases']), actual_carla_sides=len(report['cases'])*2,
            audited_steps=len(report['cases'])*720, camera_members_verified=len(report['cases'])*720,
            artifacts_verified=sum(c['checks']['artifact_count'] for c in report['cases']),
            artifact_bytes_verified=sum(c['checks']['artifact_bytes'] for c in report['cases']),
            native_install_sha256=report['native_install_sha256'], driving_safety='not_evaluated',
            cases={c['case']: c['checks'] for c in report['cases']})
        dump(args.output/'carla-checks.json', checks)
        (args.output/'carla-comparison.md').write_text(markdown(report))
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        dump(args.output/'carla-comparison.json', report)
        raise
    dump(args.output/'carla-comparison.json', report)
    print(json.dumps(checks), flush=True)


if __name__ == '__main__':
    main()
