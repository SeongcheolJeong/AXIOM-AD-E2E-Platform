"""Write acceptance from sealed observations; never promote the non-triggering first attempt."""
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
import hashlib,json

root=Path('docs/validation/hil-model-recurrence-20261018');p=root/'overflow-200us'
read=lambda name:json.loads((p/name).read_text())
run=read('result.json');checks=read('checks.json');comparison=read('comparison-checks.json');b=read('binding.json')
assert checks['no_transient_release_on_power_clear'] and comparison['compatibility']=='passed'
recovery=checks['member_recovery'];power=next(e for e in recovery if e['measurement']['boundary']=='power')
fpga=[e for e in recovery if e['measurement']['boundary']=='fpga'];repeat=next(e for e in checks['detections'] if e['reason']=='model_fpga' and e['time_ms']>3800)
q=dict(project=b['project_id'],mode='vecu',tab='bridge',binding=b['id'],revision=b['revision'],execution=run['id'],step=82,point='model_fpga',modelPair=read('power-pair.json')['id'])
url='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='model_runs',compareModel=checks['previous_execution']))
normal='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='normal_fault'))
paths=['backend/src/ade2e/hil_boundary_models.py','backend/src/ade2e/hil_model_coupling.py','backend/src/ade2e/hil_bridge.py','frontend/src/components/vecu/HilBoundaryModels.tsx','frontend/src/components/vecu/HilModelRunComparison.tsx','frontend/src/components/vecu/hilModelComparison.ts']
receipt=dict(schema_version='hil-recurrent-fpga-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=run['id'],manifest_sha256=run['manifest_hash'],binding_ref=run['summary']['binding_ref'],
 comparison_url=url,normal_fault_url=normal,previous_execution=checks['previous_execution'],native_frame_count=120,native_resolution='4x1920x1080 RAW12 30fps',clock_hz=100000000,
 native_recurrent_stall=dict(at_us=800000,duration_us=200,channel=0),replay_start_ms=3000,first_attempt='nontriggering-attempt.json',checks='overflow-200us/checks.json',
 source_hashes={v:hashlib.sha256(Path(v).read_bytes()).hexdigest() for v in paths},
 validation=dict(native_carla='passed_5ms_budget',all_wire_controls_checked=checks['all_wire_outputs_checked'],post_start_native_measurements_checked=checks['post_start_measurements_checked'],
 uninterrupted_braking_wire_events_checked=checks['uninterrupted_braking_wire_events_checked'],backend_adapter_sdk_tests=47,frontend_interpretation_tests=25,production_build='passed_with_existing_bundle_size_warning',browser_workflow_verified=False,physical_validation=False,hard_realtime=False,pixel_perception=False,bidirectional_model_coupling=False))
(root/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
old=json.loads(Path('docs/validation/hil-model-recovery-order-20261018/checks.json').read_text())
md=f'''# 3.8초 FPGA 정체 재발 · 전원 보호 해제 교차 검증

[기존 고장 A / 이번 재주입 B 열기]({url}) · [이번 정상/고장 A/B]({normal})

A는 직전 FPGA 먼저·전원 나중 회복 실행의 고장 측이다. B는 같은 시험·seed42·
전원 원본·감시 정책에 FPGA 재주입을 추가한 새 실행의 고장 측이다.
4×1920×1080 RAW12·30FPS·100MHz·1초 입력/120프레임 조건을 유지했다.

## 실제 주입과 검출

- 첫 FPGA 정체: CH0, 차량축3.005초,100µs. 첫 보호가 해제된 뒤3.8초에 다시 주입.
- 재주입: native800,000µs,지속200µs. 재생 시작3초를 더한 차량축3.800–3.8002초.
- 전원: 기존 native400ms ngspice A/B와6V 강하3.003–3.303초를 그대로 재사용.
- 경계별 보호 정책: 마지막 이상 뒤 최소500ms,정상3표본 이상. 전체 보호는 OR.
- native 정상120완료,고장118완료/2무효. 모든 출력 픽셀 검사,비의도 오류·프로토콜 오류0.
  비고장3채널 각30프레임 commit. 프레임0과24의 FIFO overflow를 원본에서 확인.

**첫100µs 재주입은 새 프레임 초기 버퍼가 흡수했다.** 해당 native/CARLA 결과와
ECU 재검출 미관측을 [별도 보존](nontriggering-attempt.json)했다. 실행의 passed는
스케줄·통신 유효성을 뜻하며 재발 검출 인수 통과로 취급하지 않았다.
시작 시각을 옮기지 않고 지속을200µs로 늘린 새 immutable 시험과 실행을 만들었다.

## A/B 관측

| 항목 | A · 기존,재주입 없음 | B · 이번,3.8초 재주입 |
|---|---|---|
| FPGA 재검출 |없음 |{repeat['time_ms']/1000:.6f}초 · frame_abort/fifo_overflow |
| 전원 보호 해제 |{next(e for e in old['member_recovery'].values() if e['measurement']['boundary']=='power')['time_ms']/1000:.6f}초 |{power['time_ms']/1000:.6f}초 · FPGA 보호 유지 |
| 전체 보호 해제 |{old['group_recovery']['time_ms']/1000:.6f}초 |{checks['group_recovery']['time_ms']/1000:.6f}초 |
| 제동 해제 입력 표본 |{old['brake_release_input_ms']/1000:.2f}초 |{checks['brake_release_input_ms']/1000:.2f}초 |
| 재이동 CARLA 모델 관측 |{old['first_motion_carla_model_ms']/1000:.2f}초 |{checks['first_motion_carla_model_ms']/1000:.2f}초 |

입력4.10초에서 A는 움직이고 B는FPGA 보호·Brake100%·정지를 유지한다.
FPGA 재검출이 전원 보호 해제와 교차해도 전체 보호 해제 이벤트는 마지막에
한 번만 발생했다. 개별 경계 해제가 전체 해제로 잘못 전달되지 않았다.

ECU 시각은 각 실행의 Linux monotonic 경과 시간이다. 입력 cursor는50ms,
CARLA 영상은 해당 입력 적용 뒤 모델 촬영 시각이다. 50ms 구간 측정 때문에
3.8초 source 주입을 즉시 ECU가 검출하는 것으로 표시하지 않는다.

![기존/재주입 CARLA A/B](../../../reports/hil-model-recurrence-20261018/recurrent-fpga-ab.gif)

원본 CARLA chase2.50–5.55초,0.5배속. 두 영상 모두 고장 측이다.
각 실행·scene·archive·member SHA를 검증했고 표시는480×270으로 축소했다.
[영상 출처](../../../reports/hil-model-recurrence-20261018/comparison-provenance.json).

![입력4.10초 · A 재출발 / B 제동 유지](../../../reports/hil-model-recurrence-20261018/recurrent-fpga-brake-held.png)

## 제품과 인수 근거

FPGA 모델에 순서·중복·회복 창을 검증하는 재주입 일정 편집을 추가했다.
저장·native A/B·모델 연결·injection 지도·인과 분석은 동일한 v1 API/SDK를 쓴다.
빈 재주입 목록은 기존 single-stall serialization/hash를 유지한다.
실행 비교 화면은 FPGA 재검출을 첫 검출과 구분하고 공통 영상 커서로 이동한다.
기존 `axis=model_runs&compareModel` URL을 재사용한다.

- [독립 검사](overflow-200us/checks.json):모든720개 차량 제어를 수신 Brake CAN bytes와
  대조하고1200개 감시 시작 후 측정값을 native 원본과 대조했다. 전체 보호 구간의
  ADAS CAN과 Brake CAN 관측을 모두 검사해 중간 제동0이 없음을 확인했다.
- [실행 비교](overflow-200us/comparison-checks.json):원본·seed·정책·실행기·설치·fidelity
  동일성,정상360개 제어 동일,고장 측 지연된 제동 해제와4.10초 상태 차이를 확인했다.
- [API/SDK](overflow-200us/api-checks.json):binding 개정,preview,export 해시와 반복 주입 표식을 확인했다.
- [47개 backend/adapter/SDK](backend-tests.log),[25개 UI 해석](frontend-tests.log),
  [production build](frontend-build.log) 통과. 기존 bundle-size 경고는 남는다.

브라우저 정책 차단으로 일반 클릭·새로고침 인수는 미검증이며 페이지 관측을
우회하지 않았다. native 출력 재생·독립 참조 ECU·가상 CAN·CARLA 범위다.
실물 ECU·전원/ADC·전기적 FPGA 결합·하드 실시간 검증은 포함하지 않는다.
모델 종료 뒤 마지막 관측값 유지 가정과 영상/입력/ECU 시간 구분을 유지한다.

[실행·설정·출처 근거](receipt.json)
'''
(root/'acceptance.md').write_text(md)
print(json.dumps(dict(comparison_url=url,normal_fault_url=normal),ensure_ascii=False))
