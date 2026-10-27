"""Observed A/B acceptance, including the known measurement/latch phase limitation."""
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
import hashlib,json
p=Path('docs/validation/hil-power-recurrence-20261018');read=lambda n:json.loads((p/n).read_text())
r=read('result.json');b=read('binding.json');c=read('checks.json');old=json.loads(Path('docs/validation/hil-model-recurrence-20261018/overflow-200us/checks.json').read_text())
assert c['no_transient_release'] and read('comparison-checks.json')['compatibility']=='passed'
power_detection=[e for e in c['detections'] if e['reason']=='model_power'][-1]
fpga_clear=[e for e in c['member_recovery'] if e['measurement']['boundary']=='fpga'][-1]
q=dict(project=b['project_id'],mode='vecu',tab='bridge',binding=b['id'],revision=1,execution=r['id'],step=98,point='model_power',modelPair=read('power-pair.json')['id'])
url='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='model_runs',compareModel=old['execution']))
normal='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='normal_fault'))
files=['backend/src/ade2e/hil_boundary_models.py','backend/src/ade2e/hil_model_coupling.py','frontend/src/components/vecu/HilBoundaryModels.tsx','integrations/hil_gateway/tests/test_model_loop.py']
risk=dict(schema_version='hil-protection-phase-limitation/1',source='authored ModelMonitor unit fixture, not an additional native CARLA run',
 test='test_power_recurrence_at_measurement_boundary_exposes_both_handoff_outcomes',observed_execution=r['id'],observed_run_has_transient_release=False,
 model_period_ms=50,case='A4350 source sag enters the4400 measurement envelope; FPGA500ms recovery can clear at4350 or4400 depending on sub-ms scheduling phase.',
 unit_earliest_clear='4350 aggregate clear,4400 new protection: possible50ms command gap',unit_later_clear='4400 same-tick protection handoff: continuous braking',
 general_no_gap_guarantee=False,remaining_work='Define and validate aggregate release qualification against bounded input observation age and all schedule phases; preserve existing results and do not extend holds silently.',physical_validation=False)
(p/'phase-limitation.json').write_text(json.dumps(risk,ensure_ascii=False,indent=2)+'\n')
checks=dict(backend_adapter_sdk_spice_tests=59,frontend_interpretation_tests=26,production_build='passed_with_existing_bundle_size_warning',
 backend_command='.venv/bin/python -m pytest backend/tests/test_hil_bridge.py integrations/hil_gateway/tests/test_model_loop.py sdk/python/tests/test_sdk_hil_bridge.py backend/tests/test_engineering_spice.py -q',
 frontend_command='node --test frontend/tests/hil-model-comparison.test.ts frontend/tests/hil-injection-guide.test.ts frontend/tests/vecu-network-observation.test.ts',
 browser_workflow_verified=False,physical_validation=False)
(p/'local-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
receipt=dict(schema_version='hil-power-recurrence-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=r['id'],manifest_sha256=r['manifest_hash'],
 binding_ref=r['summary']['binding_ref'],previous_execution=old['execution'],comparison_url=url,normal_fault_url=normal,
 source_hashes={n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in files},
 voltage_model=dict(native_stop_ms=1750,maximum_step_us=20,repeated_start_ms=1350,repeated_end_ms=1650,injected_source_v=6,threshold_v=8,replay_start_ms=3000,time_scale=1),
 fpga_source='Unchanged sealed prior4x1080p30/100MHz RTL A/B with100us initial and200us recurrent CH0 stalls',
 protection_policy='Unchanged per-boundary500ms minimum and3normal samples, aggregate OR',
 execution_verdict=r['summary']['verdict'],protection_verdict=c['protection_verdict'],no_transient_release_observed=True,general_no_gap_guarantee=False,
 all_wire_outputs_checked=720,post_start_measurements_checked=1200,braking_wire_events_checked=c['braking_wire_events_checked'],
 validation=checks,known_phase_limitation='phase-limitation.json',checks='checks.json',comparison_checks='comparison-checks.json',api_checks='api-checks.json')
(p/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
md=f'''# 4.35초 전원 강하 재발 · FPGA 보호 해제와 교차

[기존 고장 A / 전원 재주입 B]({url}) · [이번 정상/고장 A/B]({normal})

**이번 실행에서는 중간 제동 해제가 없었다.** 전체 보호 구간의 ADAS CAN44건·
Brake 보고 출력220건이 모두100%였고, 모든720개 차량 제어가 수신 Brake bytes와
일치했다. 이 결과는 이번 일정·위상의 관측이며 모든 스케줄 위상을 보장하지 않는다.

## 설정과 원본

기존 실행은3.8초 FPGA 재발까지 포함한 `{old['execution']}`이다. 새 시험은 같은
원본 차량 시험·seed42·FPGA 원본·보호 정책을 유지하고 전원 재주입만 추가했다.

| 조건 | A · 기존 | B · 이번 |
|---|---|---|
| FPGA |CH0:3.005초100µs,3.8초200µs |동일 봉인 Verilator 원본 재사용 |
| 전원 첫 강하 |6V,3.003–3.303초 |동일 source pulse |
| 전원 재주입 |없음 |6V,4.350–4.650초 |
| native 회로 범위 |400ms,최대10µs step |1,750ms,최대20µs step |
| 보호 |각 경계 최소500ms·정상3표본,OR |동일 |

회로는12V source·2Ω 배선·100µF·120Ω 부하의 예시다. 8V 미만 출력 최솟값을
참조 ECU가 감시한다. 원본 시간1,350–1,650ms의 새 pulse를3초부터1:1 재생했다.
회로 전체를 새 ngspice A/B로 계산했으며 원본을 늘여서 재생하지 않았다.
최대20µs 간격과87,556개 native 고장 측 관측점을 보존했다. 첫/마지막 강하 뒤
실제 native 전압 회복과 원본 종료 후 마지막 관측값 유지 가정을 구분한다.
FPGA는4×1920×1080 RAW12·30FPS·100MHz·1초120프레임 원본을 유지한다.
정상120완료,고장118완료/2무효,비의도 픽셀·프로토콜 오류0이다.

## 검출·제동·회복

| 관측 | A · 기존 | B · 이번 |
|---|---|---|
| 전원 재검출 |없음 |{power_detection['time_ms']/1000:.6f}초 |
| 마지막 FPGA 보호 해제 |{[e for e in old['member_recovery'] if e['measurement']['boundary']=='fpga'][-1]['time_ms']/1000:.6f}초 |{fpga_clear['time_ms']/1000:.6f}초·전원 보호 유지 |
| 전체 보호 해제 |{old['group_recovery']['time_ms']/1000:.6f}초 |{c['group_recovery'][-1]['time_ms']/1000:.6f}초 |
| 제동 해제 입력 표본 |{old['brake_release_input_ms']/1000:.2f}초 |{c['brake_release_input_ms']/1000:.2f}초 |
| CARLA 재이동 관측 |{old['first_motion_carla_model_ms']/1000:.2f}초 |{c['first_motion_carla_model_ms']/1000:.2f}초 |

입력4.35초에는FPGA 보호·제동100%,4.40초에는전원 보호·제동100%였다.
FPGA 보호 해제와 전원 재검출 보고가 같은 처리 주기에 발생했다. 개별 이벤트
로그의 출력 순서와 무관하게 전체 보호는 새 전원 상태를 포함해 계산됐고,
중간 CAN 제동0과 중간 전체 보호 해제 이벤트는 없었다. 입력4.90초에서는
A가재출발하고 B는전원 보호·Brake100%·정지를 유지한다.

주입 예정 시각,실제 gateway 표식,ECU 경과 시각,50ms 입력 표본,CARLA 촬영
시각은 별개다.4.35초 source 강하를 즉시 ECU 검출한 것으로 표시하지 않는다.
검출 근거는 native 전압 구간 최솟값<8V이며 실물 ADC 보고가 아니다.

![기존/전원 재주입 CARLA A/B](../../../reports/hil-power-recurrence-20261018/recurrent-power-ab.gif)

두 영상 모두 고장 측,입력3.50–6.40초·영상3.55–6.45초,0.5배속이다.
원본 실행·scene·ZIP·member SHA를 확인한640×360 chase 영상을480×270으로
표시했다. [영상 출처](../../../reports/hil-power-recurrence-20261018/comparison-provenance.json).

![입력4.90초 A 재출발/B 전원 보호](../../../reports/hil-power-recurrence-20261018/recurrent-power-comparison.png)

## 인수와 남은 경계

전원 재주입 일정·해석 종료·최대 간격을 화면과 동일v1 API/SDK에서 편집·저장하고,
정상/고장 native 실행→복합 binding→새 CARLA 실행→기존 실행 비교로 연결했다.
단일/복합 주입 표식 모두 각 전원·FPGA 반복을 보존한다. 기본 단일 전원과 기존
FPGA plan serialization/hash는 현재 코드에서 동일함을 확인했다.

- [독립 검사](checks.json):native 출력 대조1200개,차량제어/CAN bytes720개,
  네 번의 검출,경계별 복귀·전체OR·CAN/Brake264건 무중단 제동·최종 재출발.
- [실행 비교](comparison-checks.json):동일 원본·seed·정책·실행기·설치·fidelity,
  정상360개 제어 동일,4.90초 고장 측 제동/보호/차량 차이.
- [API/SDK](api-checks.json):개정 복원·preview·봉인export·인과artifact·단독 전원 반복 표식.
- [로컬 검사](local-checks.json):backend/adapter/SDK/SPICE59개·UI26개와build 통과.

**일반적인 무공백 보장은 아직 아니다.** 50ms 측정 구간 때문에4.35초 source
강하는4.40초 입력에 관측된다. FPGA500ms 보호가4.35초 처리 주기에 풀리는
다른 합법적 스케줄 위상에서는50ms command 공백이 가능함을 별도의 authored
unit fixture에서 확인했다. 이번 native/CARLA 실행은 그 경우가 아니었다.
[두 위상 검사와 남은 release qualification 과제](phase-limitation.json)를 보존했다.
보호 시간을 늘리거나 미래 측정값을 미리 전달해 이번 결과를 통과시키지 않았다.

브라우저 클릭·새로고침은 기존 정책 차단으로 미검증이며 페이지 관측을 우회하지
않았다. 봉인 native 출력 재생·독립 참조 ECU·vCAN·CARLA 기능 검증이다.
실물 ECU/ADC·전원과FPGA의 전기적 결합·하드 실시간·안전 검증은 포함하지 않는다.

[실행·설정·코드 출처](receipt.json)
'''
(p/'acceptance.md').write_text(md)
print(json.dumps(dict(comparison_url=url,normal_fault_url=normal),ensure_ascii=False))
