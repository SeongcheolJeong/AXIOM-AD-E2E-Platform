"""Observed stages, preserving unknown detection times and clock mappings."""
def derive(sides, snapshot):
    rows = sides.get('faulted', [])
    events = []
    for row in rows:
        for index, event in enumerate(row['reply']['bridge']['events']):
            events.append(dict(event, step=row['step'], pointer=f"/sides/faulted/{row['step']}/reply/bridge/events/{index}"))

    def observed_time(event):
        # Receive time orders reports but is never substituted for the ECU's event time.
        stamp = event.get('time_ms')
        return stamp if stamp is not None else event.get('gateway_received_time_ms')

    events.sort(key=lambda event: observed_time(event) if observed_time(event) is not None else float('inf'))
    cases = []
    for index, fault in enumerate(snapshot['binding']['injections']):
        injection = next((e for e in events if e['kind'] == 'fault_start' and e.get('fault_index') == index), None)
        start = injection['time_ms'] if injection else None
        silent_command = fault['kind'] in ('can_command_drop', 'ecu_reset', 'os_event_drop')
        reason = fault['kind'] if fault['kind'].startswith('model_') else 'command_watchdog' if silent_command else 'heartbeat_timeout' if fault['kind'] == 'restbus_drop' else 'object_age'
        def following(kinds, predicate=lambda e: True, after=None):
            minimum = observed_time(after) if after else start
            return next((e for e in events if minimum is not None and observed_time(e) is not None
                         and observed_time(e) >= minimum and e['kind'] in kinds and predicate(e)), None)

        detection = following({'detection'}, lambda e: e.get('reason') == reason)
        command = None if silent_command else following(
            {'can_command_observed', 'can_tx'},
            lambda e: (e['kind'] == 'can_command_observed' or e.get('ecu') == 'adas') and e.get('control', {}).get('brake') == 1,
            detection)
        # Observable output remains visible even when ECU internal diagnostics are absent.
        brake = following({'brake_output_observed'}, lambda e: e.get('control', {}).get('brake') == 1, command or detection)
        vehicle = None
        if brake:
            for row in rows:
                stamp = row.get('vehicle_observed_monotonic_ns')
                bridge = row['reply']['bridge']
                if stamp and row.get('next_ego', {}).get('control', {}).get('brake') == 1:
                    ms = (stamp - bridge['t0_monotonic_ns']) / 1e6
                    if ms >= brake['time_ms']:
                        vehicle = dict(kind='carla_response', time_ms=ms, step=row['step'], evidence_source='carla_readback', speed_mps=row['next_ego']['speed_mps'])
                        break
        stages = dict(injection=injection, detection=detection, can_command=command, brake_output=brake, vehicle=vehicle)
        note = 'ECU 내부 검출 시점 미관측 · 이후 출력 관측만으로 고장과의 인과를 확정하지 않습니다.'
        if detection:
            note = '참조 프로세스의 진단 보고' if detection.get('evidence_source') == 'reference_process' else 'ECU 진단 보고'
            if detection.get('time_ms') is None:
                note += ' · ECU 시계 관계 미확인으로 검출 지연 계산 불가'
        if silent_command:
            note += ' · ADAS 명령 미수신에 대한 Brake watchdog 반응; ADAS가 새 제동 명령을 보낸 것으로 해석하지 않습니다.'
        if fault['kind'].startswith('model_'):
            note += ' · 봉인된 native 모델 출력의 50ms 구간 관측을 참조 ECU가 판정합니다. 픽셀 인지·실물 ADC·양방향 모델 연동은 아닙니다.'
            if snapshot['binding'].get('model_couplings'):note += ' · 이 경계의 회복만으로 전체 제동이 해제되지 않습니다. 모든 모델 보호 조건 해제가 필요합니다.'
        # The independent reference ECU can apply its frozen schedule before the
        # gateway wakes to record fault_start. Match the scheduled interval, never
        # move the ECU timestamp forward to the gateway observation.
        application = None
        if fault['kind'] in ('ecu_reset','os_event_drop'):
            scheduled = fault.get('start_ms', start)
            end = fault.get('end_ms', float('inf'))
            application = next((e for e in events if scheduled is not None
                and e.get('time_ms') is not None and scheduled <= e['time_ms'] < end
                and e.get('ecu') == 'adas'
                and (e['kind'] == 'reset_asserted' if fault['kind']=='ecu_reset'
                     else e['kind'] == 'task_suppressed' and e.get('reason') == 'os_event_drop')), None)
        cases.append(dict(fault=fault, stages=stages, detection_status='observed' if detection else 'unobserved',
            model_recovery=following({'model_recovered'},lambda e:e.get('measurement',{}).get('boundary')==fault['kind'].removeprefix('model_')) if fault['kind'].startswith('model_') else None,
            model_group_recovery=following({'model_group_recovered'}) if snapshot['binding'].get('model_couplings') else None,
            reference_hook_application=application,
            latency_ms={k: (event['time_ms'] - start if start is not None and event is not None and event.get('time_ms') is not None else None) for k, event in stages.items()},
            note=note))
    return dict(schema_version='hil-bridge-causality/1', cases=cases, physical_validation=False,
                comparison_axis='normal_fault_within_one_target', source_ref=snapshot['binding']['source_ref'],
                clock_basis='monotonic observations; unknown clock mapping remains unobserved')
