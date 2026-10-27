"""Seal the measured late FPGA recurrence after independent acceptance checks."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
p=Path('docs/validation/hil-recurrent-fpga-6750-20261018');read=lambda n:json.loads((p/n).read_text())
r=read('result.json');b=read('binding.json');checks=read('checks.json');api=read('api-checks.json');compare=read('comparison-checks.json')
assert checks['no_transient_release'] and compare['compatibility']=='passed' and len(api['cases'])==2
assert '625 passed' in (p/'tests.log').read_text() and 'pass 29' in (p/'frontend-tests.log').read_text() and 'built in' in (p/'build.log').read_text()
power=read('power-pair.json');fpga=read('fpga-pair.json');jobs=read('fpga-model-jobs.json')
base='http://127.0.0.1:5340/#hil-sim?project='+b['project_id']+'&mode=vecu&tab=bridge&binding='+b['id']+'&revision=1&execution='+r['id']+'&step=136&point=model_fpga&modelPair='+power['id']
url=base+'&axis=model_runs&compareModel='+checks['previous_execution']
files=['backend/src/ade2e/hil_boundary_models.py','backend/src/ade2e/hil_model_coupling.py','integrations/hil_gateway/src/axiom_hil_gateway/model_monitor.py','frontend/src/components/vecu/hilModelComparison.ts','integrations/hil_gateway/tests/test_release_qualification.py']
old=json.loads(Path('docs/validation/hil-pending-power-20261018/result.json').read_text())
assert r['summary']['adapter_sources']==old['summary']['adapter_sources'] and r['summary']['installation_sha256']==old['summary']['installation_sha256']
old_link=next(v for v in old['summary']['model_link']['members'] if v['boundary']=='power')
new_link=next(v for v in r['summary']['model_link']['members'] if v['boundary']=='power');assert old_link==new_link
f=read('fpga-faulted-result.json')['summary'];a=read('fpga-baseline-result.json')['summary']
assert a['completed_frames']==540 and f['completed_frames']==536 and f['aborted_frames']==4
receipt=dict(schema_version='hil-recurrent-fpga-6750-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=r['id'],manifest_sha256=r['manifest_hash'],
 binding_ref=r['summary']['binding_ref'],source_ref=r['summary']['source_ref'],seed=r['summary']['seed'],previous_execution=checks['previous_execution'],comparison_url=url,normal_fault_url=base+'&axis=normal_fault',
 installation_sha256=r['summary']['installation_sha256'],adapter_sources=r['summary']['adapter_sources'],source_hashes={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
 native=dict(fpga_pair=fpga['id'],fpga_jobs={s:dict(execution=jobs[s]['id'],manifest_sha256=read('fpga-'+s+'-result.json')['manifest_hash'],config_hash=read('fpga-'+s+'-result.json')['summary']['config_hash']) for s in ('baseline','faulted')},
  design_hash=f['design_hash'],frames_per_channel=135,channels=4,width=1920,height=1080,fps=30,clock_hz=100000000,source_seconds=4.5,normal_commit=540,faulted_commit=536,faulted_abort=4,
  common_prefix_frames_checked_per_side=300,power_pair=power['id'],power_source_unchanged=True,power_native_seconds=4),
 qualification=b['model_link']['policy']['release_qualification'],checks=checks,local_tests=dict(backend_adapter_sdk_spice=625,frontend=29,build='passed_with_existing_bundle_size_warning'),
 limits=dict(browser_workflow_verified=False,physical_validation=False,hard_realtime=False,pixel_perception=False,bidirectional_rtl_spice_coupling=False,
  source_tail='hold_last_measured_state_after_native_completion',fpga_source_end_vehicle_ms=7500,power_source_end_vehicle_ms=7000))
(p/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
counts=checks['counts'];late=checks['late_detection'];candidate=checks['cancelled_candidate'];fc=checks['final_candidate'];sample=late['measurement']['sample'];abort=checks['native_aborts'][-1]
text=f'''# 6.75초 FPGA 재주입 · 해제 후보 재취소 인수

## 완료한 흐름

A는 직전 전원5.85초 재발까지의 결과이고, B는 CH0 출력 정체200µs를
vehicle6.75초에 추가한 결과다. 입력6.75초의 해제 후보가6.80초 측정의
FPGA 이상으로 취소됐다. 마지막 재발 고장 회복과 새 두 완료 구간 확인 뒤에만
전체 보호가 풀렸고, 그 사이 제동0%·일시 재출발은 없었다.

- A: `{checks['previous_execution']}`.
- B: `{r['id']}` / `{b['id']}` r1 / manifest `{r['manifest_hash']}`.
- [제품 A/B 링크]({url}) · [새 정상/고장 링크]({base}&axis=normal_fault).
- A/B는 각 실행의 **faulted** 측끼리 비교하며 정상/고장 축과 혼합하지 않는다.

| 관측 | A · 기존 결과 | B · FPGA6.75초 추가 |
|---|---|---|
| 입력6.75초 | 해제 검증 대기·제동100% | 해제 검증 대기·제동100% |
| 입력6.80초 | 제동 해제 | FPGA 이상·후보 취소·제동100% |
| 전체 보호 해제 · ECU 시각 | {checks['previous_group_recovery_ms']/1000:.9f}s | {checks['group_recovery_ms']/1000:.9f}s |
| Brake 해제 적용 입력 시점 | 6.80s | {checks['release_input_ms']/1000:.2f}s |
| CARLA 첫 재이동 · 속도>0.1m/s | {checks['previous_motion_carla_ms']/1000:.2f}s | {checks['motion_carla_ms']/1000:.2f}s |

## 검출·취소 근거

실제 native RTL에서 CH0 frame112가 FIFO4096픽셀 한계에 도달해
`{abort['loss_reason']}`로 abort됐다. native terminal은
`{abort['terminal_ns']/1e6:.6f}ms`이며 vehicle6.75초의200µs 정체에서 나온다.
참조 ADAS의 진단 근거는 `{late['measurement']['basis']}`:
해당50ms 측정 창의 abort_count={sample['abort_count']},
last_status={sample['last_status']}다. 짧은 abort가 다음 commit으로 덮여
누락되지 않았음을 원본 frames.json과 대조했다. 진단 시각은
`{late['time_ms']/1000:.9f}s`다.

해제 후보는 sequence{candidate['sequence']}, 요구{candidate['required_sequence']},
생성 시각 `{candidate['time_ms']/1000:.9f}s`였다. 입력136에서
`pending → active_fault`, candidate=null, active=fpga를 관측했다.
이 취소는 연속 ECU 상태 보고의 전이이며 gateway readback 시각
`{checks['cancellation_readback_ms']/1000:.9f}s`를 정확한 내부 취소 이벤트
시각으로 꾸미지 않는다. 모든 원인의 개별 hold500ms/정상3표본 조건을 확인하고,
새 후보{fc['sequence']}→{fc['required_sequence']}까지 전체 제동을 유지했다.
처리 위상에 따라 후보 생성이6.70/6.75초로 달라질 수 있으므로 고정 예상 순번이
아닌 실제 후보·필요 순번+2와 최소 hold를 검증했다.

## native 입력 확장과 동일 조건

기존 FPGA 원본은2.5초라 vehicle6.75초(native3.75초)의 고장을 넣을 수 없다.
새 양쪽 원본은4×1920×1080 RAW12·30FPS·100MHz,135프레임/채널·4.5초다.
입력/출력 해상도나 속도를 낮추거나 시간 축소를 하지 않았다. 출력 경계 crop은
기존1916×1076 그대로이며 모든 accepted RGB12 성분을 독립 정수 참조로 검사했다.

- 정상540commit, 고장536commit/4abort, 미설명 폐기/픽셀 불일치/프로토콜 오류0.
- CH1/2/3은 각각135commit. CH0 abort frame은0/24/67/112이며 다음 SOF에서 회복했다.
- 이전 두 native side와 공통300프레임씩의 RAW/RGB/참조 해시·상태·terminal·epoch가 동일했다.
- 기존5ms/100µs,800ms/200µs,2250ms/200µs에3750ms/200µs만 추가했다.
- 전원4초 native pair `{power['id']}`와 이전 세 강하 원본은 그대로 재사용했다.
- 원본 시험·seed42·ADAS/Brake 코드·설치·500ms/3표본·fresh-window-v2 정책이 같다.
- native 설계 해시 `{f['design_hash']}`. 새 합성/배치배선 결과로 부르지 않는다.
  표준1초120프레임 인수 플래그를 새540프레임 시험으로 덮어쓰지 않았다.

## 검증과 보존

- 새 정상 side360개 제어가 직전 정상과 동일하다. 고장 측 제어도 입력6.75초까지 같다.
- 보호 구간의 ADAS CAN0x100 {counts['protected_can']}건·Brake CAN0x101
  {counts['protected_brake']}건 모두 제동100%, 순간0% 없음.
- 전체720개 차량 제어를 실제 Brake 수신 bytes와,1,200개 측정 envelope를
  봉인 frames/voltage 원본과 독립 대조했다.
- 일곱 고장 시작 표식의 실제 주입 오차와 기존5ms 조건, 두 CARLA 실행의 정리 완료 확인.
- API/SDK 저장·GET·preview·causality·export와 A/B 호환성, 기존 원본/export 보존 확인.
- backend/adapter/SDK/SPICE625개·UI29개·production build 통과(기존 bundle 경고).
- 브라우저 클릭·새로고침 워크플로는 기존 정책 차단으로 미검증이다.

[검사](checks.json) · [API](api-checks.json) · [A/B](comparison-checks.json) · [receipt](receipt.json) ·
[CAN 파형](../../../reports/hil-recurrent-fpga-6750-20261018/recurrent-fpga-6750-can.png) ·
[CARLA 영상](../../../reports/hil-recurrent-fpga-6750-20261018/recurrent-fpga-6750-ab.gif).

## 시간·fidelity 경계

참조 ECU·가상 CAN·CARLA와 봉인 native RTL/SPICE 측정의1:1 재생이다. 영상은
차량 반응 관찰용10Hz이며 픽셀 인지나 실물 ADC/ECU 전원·물리 CAN 결과가 아니다.
ECU Linux monotonic, 입력 커서, 차량 스텝/영상 시각을 구분한다. 영상은 입력
커서 뒤50ms 차량 스텝이며 재이동 시각은 차량 상태의>0.1m/s 기준이다.
전원은vehicle7초, FPGA는약7.5초에 원본이 끝나고 이후 마지막 관측 상태를 유지한다.
최종 FPGA 해제 구간은native 재생이며 전원은tail_hold다. 양방향 RTL/SPICE·
연속 live 센서·물리 FPGA/ECU·하드 실시간·안전 인증 결과는 아니다.
'''
(p/'acceptance.md').write_text(text)
section=f'''## 6.75초 FPGA 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.85초 전원 강하 원본을 유지하고, CH0에6.75초200µs 정체를 추가한 새
native RTL·ECU·vCAN·CARLA A/B를 실행했다. 기존2.5초 source를4.5초·채널당
135프레임으로 확장했다. 4×1080p30·100MHz 그대로 정상540commit,
고장536commit/4abort, 비고장 채널135commit씩이며 픽셀/프로토콜 오류0이다.
이전 원본과 공통300프레임씩의 해시·상태·terminal·epoch도 일치했다.

입력6.75초의 해제 후보가6.80초 FPGA 재검출로 취소됐고, 보호 중 CAN{counts['protected_can']}건·
Brake{counts['protected_brake']}건 모두 제동100%였다. 전체 해제는
{checks['previous_group_recovery_ms']/1000:.6f}→{checks['group_recovery_ms']/1000:.6f}초,
CARLA 재이동은{checks['previous_motion_carla_ms']/1000:.2f}→{checks['motion_carla_ms']/1000:.2f}초다.
보호 정책은 유지하고 실제 후보 순번/완료 구간과 최소 hold를 검증했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
625개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, 원본 종료 뒤 tail_hold와 물리/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 근거](validation/hil-recurrent-fpga-6750-20261018/acceptance.md).

'''
for name in ('docs/status.md','docs/roadmap.md'):
 path=Path(name);s=path.read_text();first,rest=s.split('\n',1)
 if '## 6.75초 FPGA 재주입' not in s:path.write_text(first+'\n\n'+section+rest.lstrip('\n'))
print(json.dumps(dict(execution=r['id'],comparison_url=url),ensure_ascii=False,indent=2))
