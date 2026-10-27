"""Seal the measured power-recurrence receipt and bounded acceptance notes."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
p=Path('docs/validation/hil-pending-power-20261018');read=lambda n:json.loads((p/n).read_text())
r=read('result.json');b=read('binding.json');checks=read('checks.json');api=read('api-checks.json');compare=read('comparison-checks.json')
assert checks['no_transient_release'] and compare['compatibility']=='passed' and len(api['cases'])==2
assert '620 passed' in (p/'tests.log').read_text() and 'pass 29' in (p/'frontend-tests.log').read_text() and 'built in' in (p/'build.log').read_text()
power=read('power-pair.json');jobs=read('power-model-jobs.json');fpga=read('fpga-pair.json');fpga_jobs=read('fpga-model-jobs.json')
base='http://127.0.0.1:5340/#hil-sim?project='+b['project_id']+'&mode=vecu&tab=bridge&binding='+b['id']+'&revision=1&execution='+r['id']+'&step=118&point=model_power&modelPair='+power['id']
url=base+'&axis=model_runs&compareModel='+checks['previous_execution']
files=['backend/src/ade2e/hil_boundary_models.py','backend/src/ade2e/engineering_lab_schemas.py','backend/src/ade2e/hil_model_coupling.py','integrations/engineering_lab/src/axiom_engineering/spice.py','integrations/hil_gateway/src/axiom_hil_gateway/model_monitor.py','frontend/src/components/vecu/hilModelComparison.ts']
old=json.loads(Path('docs/validation/hil-pending-fpga-20261018/result.json').read_text())
assert r['summary']['adapter_sources']==old['summary']['adapter_sources'] and r['summary']['installation_sha256']==old['summary']['installation_sha256']
old_link=next(v for v in old['summary']['model_link']['members'] if v['boundary']=='fpga')
new_link=next(v for v in r['summary']['model_link']['members'] if v['boundary']=='fpga');assert old_link==new_link
receipt=dict(schema_version='hil-pending-power-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=r['id'],manifest_sha256=r['manifest_hash'],
 binding_ref=r['summary']['binding_ref'],source_ref=r['summary']['source_ref'],seed=r['summary']['seed'],previous_execution=checks['previous_execution'],comparison_url=url,normal_fault_url=base+'&axis=normal_fault',
 installation_sha256=r['summary']['installation_sha256'],adapter_sources=r['summary']['adapter_sources'],source_hashes={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
 native=dict(power_pair=power['id'],power_jobs={s:dict(execution=jobs[s]['id'],manifest_sha256=read('power-'+s+'-result.json')['manifest_hash'],config_hash=read('power-'+s+'-result.json')['summary']['config_hash'],samples=read('power-'+s+'-result.json')['summary']['sample_count']) for s in ('baseline','faulted')},
  maximum_step_us=20,source_stop_ms=4000,late_sag_vehicle_ms=[5850,6150],source_sag_v=6,threshold_v=8,fpga_pair=fpga['id'],fpga_jobs={s:fpga_jobs[s]['id'] for s in ('baseline','faulted')},fpga_source_unchanged=True),
 qualification=b['model_link']['policy']['release_qualification'],checks=checks,
 local_tests=dict(backend_adapter_sdk_spice=620,frontend=29,build='passed_with_existing_bundle_size_warning'),
 limits=dict(requested_steps=250000,native_points=250000,native_bytes=64*1024*1024,coupling_artifact_bytes=32*1024*1024,
  browser_workflow_verified=False,physical_validation=False,hard_realtime=False,pixel_perception=False,bidirectional_rtl_spice_coupling=False,source_tail='hold_last_measured_state_after_native_completion',fpga_source_end_vehicle_ms=5500,power_source_end_vehicle_ms=7000))
(p/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
counts=checks['counts'];late=checks['late_detection'];candidate=checks['cancelled_candidate'];fc=checks['final_candidate'];sample=late['measurement']['sample']
text=f'''# 5.85초 전원 재주입 · 해제 후보 재취소 인수

## 완료한 흐름

직전 FPGA5.25초 재발 결과를 A로 보존하고, B에는 전원6V·300ms 강하를
vehicle5.85–6.15초에 추가했다. 입력5.85초에는 해제 후보가 대기했고,
5.90초에 전원 이상이 관측되면서 후보가 취소됐다. 재발 고장 회복과 새 두 완료
측정구간 확인 뒤에만 제동이 해제됐다. 그 사이 제동0%·일시 재출발이 없었다.

- A: `{checks['previous_execution']}` / 기존 FPGA5.25초 재발까지.
- B: `{r['id']}` / `{b['id']}` r1 / manifest `{r['manifest_hash']}`.
- [제품 A/B 링크]({url}) · [새 정상/고장 링크]({base}&axis=normal_fault).
- A/B는 각 실행의 **faulted** 측끼리 비교한다. 새 실행 자체의 정상/고장은 별도 축이다.

| 관측 | A · 기존 결과 | B · 전원5.85초 추가 |
|---|---|---|
| 입력5.85초 | 해제 검증 대기·제동100% | 해제 검증 대기·제동100% |
| 입력5.90초 | 제동 해제 | 전원 이상·후보 취소·제동100% |
| 전체 보호 해제 · ECU 시각 | {checks['previous_group_recovery_ms']/1000:.9f}s | {checks['group_recovery_ms']/1000:.9f}s |
| Brake 해제 적용 입력 시점 | 5.90s | {checks['release_input_ms']/1000:.2f}s |
| CARLA 첫 재이동 · 속도>0.1m/s | {checks['previous_motion_carla_ms']/1000:.2f}s | {checks['motion_carla_ms']/1000:.2f}s |

## 검출·후보 취소 근거

추가 강하는 실제 ngspice 예시 전원 회로 출력에서 나왔다. 참조 ADAS의 새 진단은
`{late['measurement']['basis']}`: 해당50ms 구간의 최저전압
`{sample['min_v']:.9f}V < 8V`다. native 전압 구간은
`{sample['window_start_ms']}–{sample['window_end_ms']}ms`이고,
ECU 진단 시각은 `{late['time_ms']/1000:.9f}s`다.

해제 후보는 sequence{candidate['sequence']}, 요구{candidate['required_sequence']},
생성 시각 `{candidate['time_ms']/1000:.9f}s`였다. 입력118에서
`pending → active_fault`, candidate=null, active=power 상태를 확인했다.
이는 연속 ECU 보고 상태의 취소 관측이며 게이트웨이 readback 시각
`{checks['cancellation_readback_ms']/1000:.9f}s`를 정확한 ECU 내부 취소 시각으로
꾸미지 않는다. 마지막 개별 보호가 풀린 뒤 sequence{fc['sequence']}에서 새 후보를
만들고 요구{fc['required_sequence']}까지 보호를 유지했다.

이번 실행에서는 입력5.80초에 기존500ms hold가
`{checks['fpga_hold_remaining_at_5800_ms']:.6f}ms` 남아 후보가 다음5.85초 주기에
생성됐다. 최초 독립 검사기는 요구순번118을 고정 예상해 실패했다.
[원본 검사 로그](verification-initial.log)를 보존했다. 보고된 후보 생성 순번+2,
500ms 최소 hold, 취소 전후 보호 유지, 다음 후보 재검증으로 검사를 수정하고
가변 처리 위상 단위 재현도 추가했다. ECU 로직·정책·일정이나5ms 허용값을
바꾸거나 이 문제 때문에 새 주행으로 결과를 대체하지 않았다.

## 실행·검증 범위

기존 전원 원본은 native1.75초이며 입력 설정은 최대2초여서 native2.85초의
새 고장을 넣을 수 없었다. 양쪽 native 전원 원본을4초로 확장했다. 기존
최대20µs·12V/6V·2Ω·100µF·120Ω·이전 두 강하 일정은 유지했다.
정상200,008개, 고장200,080개 실제 adaptive 표본을 보존했다. 요청/실제 표본
예산은250,000, native 파일64MiB, 모델 연결 artifact32MiB로 제한한다.
기존 설정의 serialization/hash와 보존 결과는 유지한다. SPICE verdict의
`observed`는 파형 획득을 뜻하며 회로 설계 합격 판정으로 바꾸지 않는다.

FPGA는 원본 pair `{fpga['id']}`와 두 native 실행을 그대로 재사용했다.
4×1920×1080 RAW12·30FPS·100MHz·75프레임/채널, 고장297commit/3abort 및
다른 채널75commit씩의 결과를 새로 실행했다고 부르지 않는다. ADAS/Brake 코드,
게이트웨이 설치, 원본 시험·seed42·500ms/정상3표본·fresh-window-v2 정책이 같다.

- 새 정상 side의360개 차량 제어는 직전 정상과 동일했다. 고장 측 제어도 입력5.85초까지 같다.
- 보호 구간의 ADAS CAN0x100 {counts['protected_can']}건·Brake CAN0x101
  {counts['protected_brake']}건 모두 제동100%였다.
- 모든720개 차량 제어를 Brake bytes와 대조하고, {counts['native_envelopes']}개 측정
  envelope를 봉인 frames/voltage 원본과 독립 대조했다.
- 여섯 고장 시작 표식의 실제 주입 오차와 기존5ms 조건, 두 CARLA 실행의 정리 완료 확인.
- API/SDK 저장·GET·preview·causality·export, A/B 호환성 및 기존 원본/export 보존 확인.
- backend/adapter/SDK/SPICE620개·UI29개·production build 통과(기존 bundle 경고).
- 브라우저 클릭·새로고침 워크플로는 기존 정책 차단으로 미검증이다.

[검사](checks.json) · [API](api-checks.json) · [A/B](comparison-checks.json) · [receipt](receipt.json) ·
[CAN 파형](../../../reports/hil-pending-power-20261018/pending-power-can.png) ·
[CARLA 영상](../../../reports/hil-pending-power-20261018/pending-power-ab.gif).

## 시간과 fidelity 경계

참조 ECU 프로세스·가상 CAN·CARLA와 봉인 native RTL/SPICE 측정의1:1 재생이다.
CARLA 영상은 차량 반응 관찰용10Hz이며 픽셀 인지·실물 ADC/ECU 전원·물리 CAN
검증이 아니다. ECU Linux monotonic 시각, 입력 커서, 차량 스텝/영상 시각을 구분한다.
영상은 입력 커서 뒤50ms 차량 스텝이며 첫 재이동 시각은 차량 상태의>0.1m/s 기준이다.
FPGA는vehicle약5.5초, 전원은7초에 native source가 끝나고 이후 마지막 관측값을
유지한다. 최종 전원 해제는native 재생 구간에 있지만 FPGA는 tail_hold다.
양방향 RTL/SPICE·연속 live 센서·하드 실시간·안전 인증 결과는 아니다.
'''
(p/'acceptance.md').write_text(text)
section=f'''## 5.85초 전원 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.25초 FPGA 재발 원본을 재사용하고, native 전원 해석을4초로 늘려
5.85–6.15초6V 강하를 추가한 새 ECU·vCAN·CARLA A/B를 실행했다.
최대20µs와 보호 정책은 유지했다. 실제 native 표본은 정상200,008개/고장200,080개다.
요청/실제 표본250,000개·native64MiB·연결 artifact32MiB의 제한을 유지한다.

입력5.85초의 해제 후보가5.90초 전원 재검출로 취소됐고, 보호 중 CAN{counts['protected_can']}건·
Brake{counts['protected_brake']}건 모두 제동100%였다. 전체 해제는
{checks['previous_group_recovery_ms']/1000:.6f}→{checks['group_recovery_ms']/1000:.6f}초,
CARLA 재이동은{checks['previous_motion_carla_ms']/1000:.2f}→{checks['motion_carla_ms']/1000:.2f}초다.
후보 생성이 처리 위상에 따라 한 주기 늦어질 수 있음을 실제 보고 순번과
가변 위상 단위 재현으로 검증했다. 최초 고정순번 검사 실패도 보존했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
620개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, source 완료 뒤 tail_hold와 실물/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 검증 근거](validation/hil-pending-power-20261018/acceptance.md).

'''
for name in ('docs/status.md','docs/roadmap.md'):
 path=Path(name);s=path.read_text();first,rest=s.split('\n',1)
 if '## 5.85초 전원 재주입' not in s:path.write_text(first+'\n\n'+section+rest.lstrip('\n'))
print(json.dumps(dict(execution=r['id'],comparison_url=url),ensure_ascii=False,indent=2))
