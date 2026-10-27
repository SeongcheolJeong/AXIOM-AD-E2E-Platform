"""Read-only derivation from recorded ECU/transport/vehicle observations.

No detection or control policy is executed here. Missing links remain missing;
an aggregate 'degraded' state is never substituted for an ECU detection event.
"""
import math
from .service import digest

VERSION = 'vecu-network-causality/1'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def derive_causality(trace, snapshot):
    test, network = snapshot['test'], snapshot['network']
    rows = trace.get('sides', {}).get('faulted')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Faulted control trace is missing')
    period = network['control_period_ms']
    command_id, heartbeat_id = network['can']['command_id'], network['can']['heartbeat_id']
    events, controls = [], []
    for index, row in enumerate(rows):
        net = row.get('reply', {}).get('network', {})
        if not finite(net.get('time_ms')):
            raise ValueError('Network observation clock is missing')
        for position, event in enumerate(net.get('events', [])):
            if finite(event.get('time_ms')):
                events.append((event, index, f'sides.faulted[{index}].reply.network.events[{position}]'))
        for position, step in enumerate(net.get('controller_steps', [])):
            if finite(step.get('request', {}).get('time_s')):
                controls.append((step, index, f'sides.faulted[{index}].reply.network.controller_steps[{position}]'))
    events.sort(key=lambda item: item[0]['time_ms'])
    controls.sort(key=lambda item: item[0]['request']['time_s'])

    def point(time_ms, index, pointer, **evidence):
        return dict(time_ms=round(time_ms, 6), step=index, pointer=pointer, evidence=evidence)

    def event_point(item, **evidence):
        return point(item[0]['time_ms'], item[1], item[2], event=item[0], **evidence)

    def brake_bytes(event):
        data = event.get('data')
        return (data[2] | data[3] << 8) / 10000 if isinstance(data, list) and len(data) == 8 and all(type(b) is int and 0 <= b <= 255 for b in data) else None

    cases = []
    for fault_index, fault in enumerate(test['faults']):
        kind, start, end = fault['kind'], fault['start_ms'], fault['end_ms']
        case = dict(fault_index=fault_index, fault=fault, status='not_observed', detection_basis=None,
                    stages={key: None for key in ('injection', 'detection', 'can_tx', 'can_rx', 'application', 'vehicle', 'slowdown', 'stop')})
        stages = case['stages']
        injection = next((e for e in events if e[0].get('kind') == 'fault_start' and e[0].get('fault') == fault), None)
        if injection:
            stages['injection'] = event_point(injection)
        if kind not in ('udp_delay', 'restbus_drop'):
            case.update(status='unsupported', note='이 고장의 검출→명령 인과 추적은 지원하지 않습니다. 리셋 주입을 ECU 이상 검출로 간주하지 않습니다.')
            cases.append(case)
            continue
        if not injection:
            case['note'] = '설정 시작 시각은 있지만 원본 고장 주입 이벤트가 없어 지연을 계산하지 않습니다.'
            cases.append(case)
            continue
        detection = None
        if kind == 'udp_delay':
            limit = network['policy']['max_object_age_ms']
            candidates = [(c, i, p) for c, i, p in controls if start <= c['request']['time_s'] * 1000 < end
                          and c.get('reply', {}).get('state') == 'degraded'
                          and finite(c['reply'].get('object_age_s')) and c['reply']['object_age_s'] * 1000 > limit + 1e-5]
            if candidates:
                control, index, pointer = candidates[0]
                packet = control['request'].get('object_packet', {})
                detection = point(control['request']['time_s'] * 1000, index, pointer,
                                  state=control['reply']['state'], object_age_ms=round(control['reply']['object_age_s'] * 1000, 6),
                                  threshold_ms=limit, object_sequence=packet.get('sequence'), source_time_s=packet.get('time_s'))
                case['detection_basis'] = 'ADAS 객체 입력의 생성 후 경과 시간이 허용 한도를 초과한 C++ 제어 실행'
        else:
            timeout = next((e for e in events if e[0].get('kind') == 'heartbeat_timeout' and e[0].get('ecu') == 'adas' and start <= e[0]['time_ms'] < end), None)
            if timeout:
                last = next((e for e in reversed(events) if e[0].get('kind') == 'can_rx' and e[0].get('ecu') == 'adas'
                             and e[0].get('can_id') == heartbeat_id and e[0]['time_ms'] < timeout[0]['time_ms']), None)
                detection = event_point(timeout, last_heartbeat_ms=last[0]['time_ms'] if last else None,
                                        observed_silence_ms=timeout[0]['time_ms'] - last[0]['time_ms'] if last else None)
                case['detection_basis'] = 'ADAS 생존 신호 미수신 시간 초과 이벤트 (객체 입력 정책과 별도)'
        stages['detection'] = detection
        if detection:
            # Require the matching ADAS control activation, then actual CAN bytes.
            activation = next((e for e in events if e[0].get('kind') == 'control' and e[0].get('ecu') == 'adas'
                               and detection['time_ms'] <= e[0]['time_ms'] < end
                               and (e[0].get('heartbeat_override') is True if kind == 'restbus_drop' else e[0].get('state') == 'degraded')), None)
            tx = next((e for e in events if activation and e[0]['time_ms'] == activation[0]['time_ms']
                       and e[0].get('kind') == 'can_tx' and e[0].get('ecu') == 'adas' and e[0].get('can_id') == command_id
                       and brake_bytes(e[0]) is not None and brake_bytes(e[0]) >= .999), None)
            if tx:
                stages['can_tx'] = event_point(tx, brake=brake_bytes(tx[0]), activation_pointer=activation[2])
                rx = next((e for e in events if e[0]['time_ms'] >= tx[0]['time_ms'] and e[0].get('kind') == 'can_rx'
                           and e[0].get('ecu') == 'brake' and e[0].get('can_id') == command_id and e[0].get('data') == tx[0].get('data')
                           and e[0].get('transport_time_ns') == round(tx[0]['time_ms'] * 1e6)), None)
                if rx:
                    stages['can_rx'] = event_point(rx, brake=brake_bytes(rx[0]))
                    for index, row in enumerate(rows):
                        net, reply = row['reply']['network'], row['reply']
                        if net['time_ms'] < rx[0]['time_ms'] or net.get('brake', {}).get('last_command_ms') != rx[0]['time_ms']:
                            continue
                        if net['brake'].get('command_hex') != bytes(rx[0]['data']).hex() or reply.get('control', {}).get('brake', 0) < .999:
                            continue
                        stages['application'] = point(net['time_ms'], index, f'sides.faulted[{index}].reply.control', brake=reply['control']['brake'])
                        vehicle = row.get('next_ego', {})
                        if test['mode'] == 'carla_closed_loop' and finite(vehicle.get('speed_mps')) and vehicle.get('control') == reply['control']:
                            stages['vehicle'] = point(net['time_ms'] + period, index, f'sides.faulted[{index}].next_ego',
                                                      speed_mps=vehicle['speed_mps'], brake=vehicle['control']['brake'], observation='first_control_readback_not_deceleration_onset')
                        break
        application = stages['application']
        if application and test['mode'] == 'carla_closed_loop':
            for index in range(application['step'], len(rows)):
                row = rows[index]
                before, after = row.get('request', {}).get('ego', {}).get('speed_mps'), row.get('next_ego', {}).get('speed_mps')
                if stages['slowdown'] is None and finite(before) and finite(after) and before - after >= .01:
                    stages['slowdown'] = point(row['reply']['network']['time_ms'] + period, index, f'sides.faulted[{index}].next_ego.speed_mps',
                                               before_mps=before, after_mps=after, criterion='speed drop >= 0.01 m/s within one vehicle step; not physical brake onset')
                if stages['stop'] is None and finite(after) and after < .1:
                    stages['stop'] = point(row['reply']['network']['time_ms'] + period, index, f'sides.faulted[{index}].next_ego.speed_mps', speed_mps=after, criterion='speed < 0.1 m/s')
                if stages['stop'] and stages['slowdown']:
                    break
        case['latency_ms'] = {f'injection_to_{key}': round(value['time_ms'] - stages['injection']['time_ms'], 6) if value else None
                              for key, value in stages.items() if key != 'injection'}
        case['latency_ms']['detection_to_can_tx'] = round(stages['can_tx']['time_ms'] - detection['time_ms'], 6) if detection and stages['can_tx'] else None
        case['status'] = 'observed' if all(stages[k] for k in ('injection', 'detection', 'can_tx', 'can_rx', 'application')) else 'incomplete'
        if case['status'] != 'observed':
            case['note'] = '원본에서 연결되지 않은 단계는 미관측입니다. 설정 시각이나 전체 degraded 상태로 대체하지 않습니다.'
        cases.append(case)
    comparison_identity = dict(mode=test['mode'], seed=test['seed'], network_ref=test['network_ref'],
                               scenario_ref=test.get('hil_test_ref') or test.get('input_ref'), runtime_sha256=snapshot.get('runtime', {}).get('manifest_sha256'))
    return dict(schema_version=VERSION, side='faulted', cases=cases, comparison_identity=comparison_identity,
                comparison_identity_sha256=digest(comparison_identity), control_period_ms=period, transport_tick_ms=network['tick_ms'],
                physical_validation=False, classification='derived_recorded_causality_not_new_execution')
