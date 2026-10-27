"""Acceptance joins the sealed legacy counterexample, bounded campaign, and product links."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
root=Path('docs/validation/hil-release-qualification-20261018');read=lambda p:json.loads(p.read_text())
campaign=read(root/'campaign.json');matrix=read(root/'phase-matrix.json');b=read(root/'4350/binding.json');r=read(root/'4350/result.json');case=read(root/'4350/checks.json')
old=read(Path('docs/validation/hil-power-recurrence-20261018/checks.json'));old_r=read(Path('docs/validation/hil-power-recurrence-20261018/result.json'))
assert campaign['all_passed'] and matrix['qualified_cases_with_gap']==0 and (root/'api-checks.json').is_file()
q=dict(project=b['project_id'],mode='vecu',tab='bridge',binding=b['id'],revision=1,execution=r['id'],step=105,point='model_power',modelPair=read(root/'4350/power-pair.json')['id'])
url='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='model_runs',compareModel=old['execution']))
normal='http://127.0.0.1:5340/#hil-sim?'+urlencode(q|dict(axis='normal_fault'))
files=['backend/src/ade2e/hil_model_coupling.py','integrations/hil_gateway/src/axiom_hil_gateway/model_monitor.py','frontend/src/components/vecu/HilModelLoop.tsx','frontend/src/components/vecu/HilModelRunComparison.tsx','frontend/src/components/vecu/hilModelComparison.ts']
receipt=dict(schema_version='hil-release-qualification-acceptance/1',recorded_at=datetime.now(timezone.utc).isoformat(),execution=r['id'],manifest_sha256=r['manifest_hash'],binding_ref=r['summary']['binding_ref'],previous_execution=old['execution'],
 comparison_url=url,normal_fault_url=normal,qualification=b['model_link']['policy']['release_qualification'],
 legacy_installation_sha256=old_r['summary']['installation_sha256'],installation_sha256=r['summary']['installation_sha256'],
 model_monitor_only_upgrade_verified=True,source_hashes={v:hashlib.sha256(Path(v).read_bytes()).hexdigest() for v in files},
 unit=dict(evidence='authored schedules with sealed original monitor code',cases=matrix['case_count'],before_gaps=matrix['legacy_cases_with_gap'],after_gaps=0),
 native=dict(cases=4,onsets_ms=[4349,4350,4351,4399],totals=campaign['totals'],all_passed_5ms=True,physical_validation=False),
 local_tests=dict(backend_adapter_sdk=637,frontend_interpretation=28,production_build='passed_with_existing_bundle_size_warning'),
 limits=dict(period_ms=50,input_age_ms=10,source_onset_fixture_ms=[4301,4400],future_fault_prediction=False,hard_realtime=False,physical_validation=False,browser_workflow_verified=False),
 campaign='campaign.json',phase_matrix='phase-matrix.json',api_checks='api-checks.json')
(root/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
rows='\n'.join(f"|{c['onset_ms']/1000:.3f}초 |{c['group_recovery_ms']/1000:.6f}초 |{c['motion_carla_ms']/1000:.2f}초 |0 |" for c in sorted(campaign['cases'],key=lambda c:c['onset_ms']))
md=f'''# 50ms 측정 경계 보호 해제 개선 · 기존/개선 A/B

[동일4.35초 고장의 기존 A / 개선 B]({url}) · [개선 코드의 정상/고장 A/B]({normal})

**해제 후보 이후의 연속 두 완료 측정구간을 확인한 뒤 전체 제동을 해제한다.**
첫 완료 구간은 후보 처리 시각과 겹칠 수 있으므로 두 구간을 확인해 후보 이후
온전한50ms 구간 하나를 포함한다.500ms 개별 보호·정상3표본·8V 검출 임계값은
변경하지 않았다. 참조 ADAS 프로세스가 판단하고,실제 vCAN으로전달된Brake 보고
bytes만CARLA에 적용한다. 호스트가 미래 고장 일정으로 제동을 생성하지 않는다.

## 해제 계약

- 모든 개별 보호가 해소돼도 전체 보호를 `해제 검증 대기`로 유지한다.
- 일련번호가 연속이며 입력 시각 대비나이가0–10ms인 두새 완료 구간을 확인한다.
- 새 이상이면 후보를 취소한다.누락·반복·미래·건너뜀·지연 표본은 확인에 쓰지 않고
  제동을 유지하며 해당 벤치 입력 유효성 오류를 기록한다.
- 개별 이상이 없는 것과 전체 제동 해제를 구분한다.새 `release_qualification`
  계약과`release_guard`상태·후보·확인 cursor를 binding과실행에 보존한다.
- 원본이 끝난 뒤 마지막 관측을 유지하는 모델 재생 가정은 기존과 같다.

기존 봉인결과는 변경하지 않았다.이전 즉시 해제binding은읽기·영상·export가
그대로 가능하다. 새코드에서새 실행하려면개선 계약의binding을 저장해야 하며,
예전 계약의 재실행은원본검증 불일치로거절한다. 조용히기존개정을 바꾸지 않는다.

## 기존 공백 재현과 타이밍 행렬

원격 설치의 기존 `model_monitor.py`를보존하고SHA
`4f62394e6606a98b49b2b97233c2a3550b2efd26b4b473f1e59f05daa1a43445`를
이전 실행과 대조했다.이를직접불러온단위재현에서4.35–4.40초50ms 공백을 재현했다.
주입4301–4400ms의100개시각×ECU처리위상0/1/5/9/10ms와교대0.25ms지터를 적용했다.

| authored 단위검증 |기존 코드 |개선 코드 |
|---|---:|---:|
| 동일500개조합 중중간보호해제 |255 |0 |

이것은 참조감시로직의단위재현이며 실제 CAN/CARLA500회 실행이라는 뜻이 아니다.
[전체 행렬과 대표시계열](phase-matrix.json).

![단위 보호판정과 실제CAN출력 분리](../../../reports/hil-release-qualification-20261018/release-can-waveforms.png)

위 패널은 authored 단위보호판정이고,아래패널은별도로수행한기존/개선CARLA실행의
실제 vCAN 명령payload다. 기존4.35초CARLA실행은우연히공백이없었던실행이므로,
그영상을공백재현으로다시표시하지않았다. [파형 출처](../../../reports/hil-release-qualification-20261018/waveform-provenance.json).

## native모델·가상CAN·CARLA 반복

동일한seed42·18초/360입력·개별보호정책과기존4×1080p30/100MHz FPGA native
원본을 유지했다.4.35초는기존전원원본을재사용했고,나머지세시각은각각새ngspice
1.8초/최대20µs A/B를계산했다.4.349/4.350/4.351/4.399초를주입해측정경계양쪽과
뒤쪽시각을실행했다.각각새기준/고장CARLA를독립실행했고5ms조건과양측cleanup을통과했다.

| 전원재발시각 |전체보호해제(ECU경과) |CARLA재이동관측 |중간제동해제 |
|---|---|---|---|
{rows}

[네실행의독립검사](campaign.json):차량제어{campaign['totals']['wire_controls']}개를
수신Brake CAN bytes와대조,원본native측정구간{campaign['totals']['native_envelopes']}개를
대조했다.전체보호구간ADAS CAN{campaign['totals']['protected_can']}건·Brake출력
{campaign['totals']['protected_brake']}건이모두100%였다.각실행의검사와원본artifact는
`4349/`·`4350/`·`4351/`·`4399/`에보존했다.

동일4.35초고장실행의비교:

| 관측 |A·기존 |B·개선 |
|---|---|---|
| 전체보호해제 |{old['group_recovery'][-1]['time_ms']/1000:.6f}초 |{case['group_recovery_ms']/1000:.6f}초 |
| 제동해제입력표본 |{old['brake_release_input_ms']/1000:.2f}초 |{case['release_input_ms']/1000:.2f}초 |
| CARLA재이동관측 |{old['first_motion_carla_model_ms']/1000:.2f}초 |{case['motion_carla_ms']/1000:.2f}초 |

개별보호해제시각은50ms처리위상·지터에따라달라질수있다.예를들어B는입력5.20초에
개별보호가모두해제돼도검증대기중제동을유지하고5.30초확인표본뒤전체를해제했다.
서로다른실행의절대시계를빼물리ECU지연으로해석하지않는다.

![동일고장기존/개선CARLA A/B](../../../reports/hil-release-qualification-20261018/release-logic-ab.gif)

두영상모두동일고장조건이다.입력3.50–6.40초·영상3.55–6.45초,0.5배속이다.
각실행의scene/ZIP/member SHA와원래실행ID를유지했다.
[영상출처](../../../reports/hil-release-qualification-20261018/comparison-provenance.json).

![개별보호해제뒤전체검증대기](../../../reports/hil-release-qualification-20261018/release-pending-ab.png)

## 제품·API·검사

결과화면은개별보호해제와전체해제검증대기를구분한다.기존/개선비교는v1
execution/artifact API로두출처를검증해실행기차이가`model_monitor.py`와해당
설치파일에만있는지확인한다.임의실행기변경을같은조건으로통과시키지않는다.
화면에보호로직변경을명시하고,CAN제동파형·검출/후보/전체해제버튼·공통영상커서를
제공한다.정확한binding개정·실행·비교축·cursor를URL로복원한다.

- [동일조건기존/개선검사](4350/comparison-checks.json):출처기반로직변경허용,
  정상360개제어동일,입력5.25초A제동해제/B검증대기·제동유지.
- [API/SDK](api-checks.json):네개정·preview·execution·인과artifact·봉인export,
  기존결과원형유지와구계약새preview거절.
- [backend/adapter/SDK637개](backend-tests.log),[UI해석28개](frontend-tests.log),
  [production build](frontend-build.log) 통과.기존bundle-size경고는남는다.

검증범위는50ms측정·입력나이최대10ms·기록한주입/처리위상이다.확인된최신구간
이후에발생한새고장을미래예측하거나모든물리적타이밍을안전보장하는로직은아니다.
가상CAN과독립참조ECU·봉인native출력재생·CARLA검증이며실물ECU/ADC·전기적
전원결합·하드실시간·기능안전인수는포함하지않는다.브라우저정책차단으로일반
클릭·새로고침인수는미검증이며페이지관측을우회하지않았다.

[실행·버전·설치·코드근거](receipt.json)
'''
(root/'acceptance.md').write_text(md)
print(json.dumps(dict(comparison_url=url,normal_fault_url=normal,totals=campaign['totals']),ensure_ascii=False))
