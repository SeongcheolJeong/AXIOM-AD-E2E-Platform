"""Publish a receipt only after all native, wire, comparison and API checks pass."""
import hashlib,json,shutil
from datetime import datetime,timezone
from pathlib import Path
p=Path('docs/validation/hil-pending-fpga-20261018');read=lambda n:json.loads((p/n).read_text())
r=read('result.json');b=read('binding.json');checks=read('checks.json');api=read('api-checks.json');compare=read('comparison-checks.json')
assert checks['no_transient_release'] and compare['compatibility']=='passed' and len(api['cases'])==2
shutil.copyfile('/tmp/hil-pending-fpga-build-final.log',p/'build.log')
assert '545 passed' in (p/'tests.log').read_text() and 'pass 29' in (p/'frontend-tests.log').read_text() and 'built in' in (p/'build.log').read_text()
pair=read('fpga-pair.json');power=read('power-pair.json')
base='http://127.0.0.1:5340/#hil-sim?project='+b['project_id']+'&mode=vecu&tab=bridge&binding='+b['id']+'&revision=1&execution='+r['id']+'&step=106&point=model_fpga&modelPair='+power['id']
url=base+'&axis=model_runs&compareModel='+checks['previous_execution']
files=['backend/src/ade2e/hil_boundary_models.py','integrations/hil_gateway/src/axiom_hil_gateway/model_monitor.py','frontend/src/components/vecu/HilBoundaryModels.tsx','frontend/src/components/vecu/HilModelLoop.tsx','frontend/src/components/vecu/HilModelRunComparison.tsx','frontend/src/components/vecu/hilModelComparison.ts']
receipt=dict(schema_version='hil-pending-fpga-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=r['id'],manifest_sha256=r['manifest_hash'],
 binding_ref=r['summary']['binding_ref'],previous_execution=checks['previous_execution'],comparison_url=url,normal_fault_url=base+'&axis=normal_fault',
 installation_sha256=r['summary']['installation_sha256'],adapter_sources=r['summary']['adapter_sources'],source_hashes={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
 fpga_pair=pair['id'],fpga_jobs={s:dict(execution=read('fpga-'+s+'-result.json')['id'],manifest_sha256=read('fpga-'+s+'-result.json')['manifest_hash'],config_hash=read('fpga-'+s+'-result.json')['summary']['config_hash']) for s in ('baseline','faulted')},
 native=dict(width=1920,height=1080,channels=4,fps=30,clock_hz=100000000,frames_per_channel=75,source_seconds=2.5,seed=42,baseline_committed=300,faulted_committed=297,faulted_aborted=3,committed_pixel_errors=0,protocol_errors=0),
 checks=checks,tests=dict(backend_adapter_sdk=545,frontend=29,build='passed_with_existing_bundle_size_warning'),
 limits=dict(browser_workflow_verified=False,physical_validation=False,hard_realtime=False,pixel_perception=False,bidirectional_rtl_spice_coupling=False,source_tail='hold_last_measured_state_after_native_completion',fpga_source_end_vehicle_ms=5500,power_source_end_vehicle_ms=4750))
(p/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
late=checks['late_detection'];cancel=checks['cancellation_readback_ms'];cand=checks['cancelled_candidate'];fc=checks['final_candidate'];counts=checks['counts']
text=f'''# 5.25초 FPGA 재주입 · 해제 후보 취소 인수

## 실행 결과

해제 검증 대기 중 실제 native RTL의 CH0 출력 정체200µs를 추가했다. 후보가
취소됐고, 보호 구간 CAN 제동/Brake 출력에 순간 해제가 없었다. 재발 고장에 대한
개별 회복과 새 두 완료구간 확인 뒤에만 전체 보호가 해제됐다.

- A(직전 결과): `{checks['previous_execution']}`. 5.25초 재주입 없음.
- B(새 실행): `{r['id']}`. `{b['id']}` r1.
- [제품 A/B 링크]({url}) · [정상/고장 링크]({base}&axis=normal_fault).
- A/B는 각각의 **faulted** 측을 비교한다. 정상/고장 축과 혼합하지 않는다.

| 관측 | A · 직전 결과 | B · 5.25초 재주입 |
|---|---|---|
| 입력5.25초 | 해제 후보 대기·제동100% | 해제 후보 대기·제동100% |
| 입력5.30초 | 제동 해제 | FPGA 이상·후보 취소·제동100% |
| 전체 보호 해제(ECU 시계) | {checks['previous_group_recovery_ms']/1000:.9f}s | {checks['group_recovery_ms']/1000:.9f}s |
| Brake 해제 적용 입력 시점 | 5.30s | {checks['release_input_ms']/1000:.2f}s |
| CARLA 첫 재이동 관측(속도>0.1m/s) | {checks['previous_motion_carla_ms']/1000:.2f}s | {checks['motion_carla_ms']/1000:.2f}s |

B의 후보는 sequence{cand['sequence']}, 요구{cand['required_sequence']}였다.
native 마지막 abort는 frame67, `{checks['native_aborts'][-1]['terminal_ns']/1e6:.6f}ms`이며
vehicle5.25초의 정체에서 나온 실제 FIFO overflow다. 50ms 측정 창이 이를
입력5.30초에 포함했다. 참조 ADAS 진단의 재검출은 `{late['time_ms']/1000:.9f}s`다.
후보 취소는 연속 ECU 보고 상태의 `pending → active_fault`, candidate=null로
확인했다. 게이트웨이 상태 readback은 `{cancel/1000:.9f}s`이며, 이를 정확한
ECU 내부 취소 시각으로 바꿔 부르지 않는다. 새 후보 sequence{fc['sequence']}의
요구{fc['required_sequence']}까지 제동을 유지했다.

## 원본과 변경 범위

기존 FPGA 측정 원본은1초뿐이므로5.25초(native2.25초) 정체를 넣을 수 없었다.
새 normal/faulted 양쪽 모두 **4×1920×1080 RAW12·30FPS·100MHz·75프레임/채널,
2.5초 실제 native RTL 입력**으로 확장했다. 시간 축소나 해상도 축소가 아니다.
native full-pixel check는 정상300commit, 고장297commit/3abort, 픽셀·프로토콜
오류0이며 CH1/2/3은 각각75commit이다. 이전 두 정체5ms/100µs,
800ms/200µs에2250ms/200µs를 추가했다. 기존 power 원본과500ms·정상3표본·
fresh-window-v2 정책, 실행기/게이트웨이 설치는 동일하다. native 설계 해시는
`{read('fpga-faulted-result.json')['summary']['design_hash']}`다.

새300프레임 결과의 verdict는passed다. 기존 표준1초120프레임 인수 세트의
`acceptance_1080p30` 플래그를 바꾸거나 새 합성/배치배선 통과로 표시하지 않는다.
기존 결과·export를 보존했다. 정상 side의360개 제어는 직전 정상과 모두 같았다.

## 근거와 검증

- 실제 ADAS CAN0x100 {counts['protected_can']}건, Brake CAN0x101 {counts['protected_brake']}건:
  첫 모델 검출부터 최종 해제 전까지 모두 제동100%, 중간0% 없음.
- 모든720개 차량 제어를 Brake 수신 bytes와 대조, {counts['native_envelopes']}개 native
  측정 envelope를 frames/voltage 원본과 독립 대조했다.
- 고장 시작5개 및 실제 예정/주입 시각·5ms 조건, 양쪽 CARLA 정리 확인.
- source→pair→binding 개정/해시, API/SDK GET/preview/export/causality 및 기존 export 보존 확인.
- backend/adapter/SDK545개·UI 해석29개·production build 통과(기존 bundle size 경고).
- UI는 실제 FPGA 입력 프레임 수와 ‘해제 후보 취소 관측’ seek를 제공한다.
  브라우저 클릭·새로고침 인수는 기존 정책 차단으로 미검증이다.

[검사](checks.json) · [API](api-checks.json) · [A/B](comparison-checks.json) ·
[receipt](receipt.json) · [CAN 파형](../../../reports/hil-pending-fpga-20261018/pending-fpga-can.png) ·
[CARLA GIF](../../../reports/hil-pending-fpga-20261018/pending-fpga-ab.gif).

## 해석 경계

native RTL/SPICE 봉인 측정값을 참조 ECU 프로세스·vCAN·CARLA에1:1 재생했다.
CARLA 영상은 차량 반응 관찰용10Hz이며 RAW 픽셀 인식 결과가 아니다. 영상 시각은
입력 커서보다50ms 뒤 차량 스텝이며 Linux monotonic ECU/관측 시각과 구분한다.
native FPGA는vehicle5.5초, 전원은4.75초에 완료된다. 그 뒤는 기존 계약대로
마지막 관측 상태를 유지한다. 최종 보호 해제 구간의 매 창이 추가 native 센서
측정이라는 뜻은 아니다. 물리 ECU/FPGA, 전원/ADC, 하드 실시간·안전 인증이나
임의 미래 고장에 대한 무지연 보장을 포함하지 않는다.
'''
(p/'acceptance.md').write_text(text)
section=f'''## 5.25초 FPGA 재주입 · 해제 후보 취소 · 2026-10-18

해제 검증 대기 중5.25초에CH0 정체200µs를 추가해native RTL·참조ECU·vCAN·CARLA를
새로 실행했다. 기존1초 source로는 늦은 고장을 주입할 수 없어4×1080p30·100MHz를
유지한75프레임/채널·2.5초 원본을 양쪽 새로 만들었다. 정상300commit,
고장297commit/3abort, 다른 채널75commit씩, 픽셀/프로토콜 오류0이다.

입력5.25초의해제 후보가5.30초FPGA 재검출로취소됐다. 보호 중CAN{counts['protected_can']}건·
Brake{counts['protected_brake']}건이모두제동100%를유지했다. 전체해제는
{checks['previous_group_recovery_ms']/1000:.6f}→{checks['group_recovery_ms']/1000:.6f}초,
CARLA 재이동은{checks['previous_motion_carla_ms']/1000:.2f}→{checks['motion_carla_ms']/1000:.2f}초다.
같은정상side360개제어가일치했고720개차량제어·{counts['native_envelopes']}개측정창을원본과대조했다.
UI에실제입력프레임수설정과‘해제후보취소관측’을추가했다. 후보취소는ECU 보고
상태전이의관측이며내부정확취소시각으로표시하지않는다.545개로컬검사·UI29개·build통과.

브라우저워크플로는정책차단으로미검증이다.봉인native측정재생이며source완료뒤는
마지막관측상태유지다.실물ECU/전원·하드실시간·안전검증과구분한다.
[CAN·영상A/B와검증근거](validation/hil-pending-fpga-20261018/acceptance.md).

'''
for name in ('docs/status.md','docs/roadmap.md'):
 path=Path(name);s=path.read_text();first,rest=s.split('\n',1)
 if '## 5.25초 FPGA 재주입' not in s:path.write_text(first+'\n\n'+section+rest.lstrip('\n'))
print(json.dumps(dict(execution=r['id'],comparison_url=url),ensure_ascii=False,indent=2))
