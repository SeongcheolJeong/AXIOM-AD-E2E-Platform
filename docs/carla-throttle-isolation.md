# CARLA throttle 입력과 응답 진동 비교

2026-09-14 · `carla-throttle-isolation-protocol/1`

이 도구는 저속 응답의 진동에 제어 입력 변화가 관여하는지 조사하는 선택형 native
개발자 실험입니다. 플랫폼 Job/Run 또는 제동 품질 판정이 아닙니다. 기존
[protocol3의 정속 준비 실패](carla-brake-road-entry.md)를 유지하며, 준비 기준을
통과했다거나 제동 모델을 보정했다고 표시하지 않습니다.

## 실험 조건

원본 commissioning의 225개 준비 요청을 그대로 재생합니다. 새 ego마다 정착 과정과
설정·명령·관측을 새로 수집하며, 시뮬레이터 내부 상태를 복제했다고 가정하지 않습니다.

| 항목 | 고정 조건 |
| --- | --- |
| 엔진·환경 | CARLA build1ae5356·adapter0.8, 기존 Town10HD_Opt·spawn·weather·physics readback |
| 준비 | 원본 225개 요청의 전체 throttle/brake/steer/flags/gear 재생; velocity injection 없음 |
| PI 상태 | 새로운 관측의 frame/time/속도로 계산, ACK·다음 frame·readback 뒤 확정 |
| 요청 비교 | 각 준비 단계의 PI 제안과 원본 요청이 native float32 및 flag/gear에서 일치해야 함 |
| 전환 검사 | 원본과 같은 정착 tick 수·목록 갱신 tick 수·전체 cached gear 이력, 초기 및 전환 상태 비교 |
| PI 분기 | 기존 Kp0.08·Ki0.05·throttle 제한 및 slew를 같은 새 상태에서 40단계 계속 |
| 고정 분기 | 마지막 **요청** throttle을 40단계 고정; cached float32를 요청값으로 치환하지 않음 |
| 공통 | 20Hz·2초, brake/steer0, 자동 forward 요청; 새로운 제동 pulse 없음 |
| 중단 | 기존 3m/s·30m·횡방향1m·heading5° 경계, 접촉 알림·소유권·동기화·원본 필드 오류 |

각 분기는 준비 225명령/226관측과 연장 40명령/41관측을 보존합니다. 경계 F0는
같은 physics snapshot이므로 준비+연장의 고유 physics 관측은 266개입니다. 첫 연장 명령의 응답은
F1이며, F0의 가속도·cached control은 마지막 준비 입력의 응답으로 남습니다.

기록된 요청은 float64 표현 그대로 재생합니다. 새 PI 제안이 같은 float32로 표현되는
경우에만 송신하며, 다음 단계의 `previous_throttle`은 실제 요청값입니다. 제안·요청·
ACK·readback·확정 상태를 따로 보존합니다. 아직 보내지 않은 제안이 불일치하면 그
명령은 `not_attempted`이고, PI 분기의 미래 throttle은 실행 전에 만들어 채우지 않습니다.

## 비교 자격

새 실행을 원본에 대조하는 전환 검사와, 두 새 실행을 서로 직접 대조하는 비교 검사를
분리합니다. 각각 원본 허용차 안에 있더라도 서로의 차이는 허용차를 초과할 수 있습니다.
동일 소스·환경·준비 이력과 다음 raw 상태 허용차를 두 실행 사이에서 다시 요구합니다.

- 위치 거리 15mm, 속도 벡터 거리 0.005m/s, 축별 wrapped 회전 차이 0.05°
- 종방향 가속도 차이 0.02m/s², angular velocity 벡터 차이 0.05°/s
- cached throttle 차이 0.0005, 같은 양수 cached gear와 나머지 제어 필드
- 양수 raw RPM 및 같은 부호의 4개 wheel omega, 기준 절댓값 대비 차이 각각 2% 이내

정착 직후의 초기 상태 비교는 정지 중 RPM/gear0을 허용합니다. 연장 직전 상태에는
유효한 회전 상태를 요구합니다. 절대 world frame·시간·actor ID는 새 실행마다 달라도
되지만, 각 실행의 연속 frame·실제 dt와 관측 index는 일치해야 합니다. 보간·평활화·
위치 이동·좋은 반복만 선택하는 자동 재시도는 없습니다.

고정 입력에서도 가속도 부호 교대가 남으면 입력 교대가 진동에 반드시 필요하다는
가설을 반박할 근거가 됩니다. 타이어·접촉·solver 중 하나를 원인으로 확정할 수는
없습니다. PI 분기에서만 진동이 남아도 feedback 관여의 단서이며 이득 오류의 증명은
아닙니다. RPM/omega는 raw API 값으로 표시하며 힘·토크·물리 단위나 실차 정확도를
새로 주장하지 않습니다.

## 실행과 원본 보존

먼저 기존 report·event chain·입력·source bundle·physics 근거·예약 해제 영수증을
검증한 뒤 새 입력을 작성합니다. 입력 작성과 분석은 CARLA에 접속하지 않습니다.
아래 경로는 사용할 원본과 새 폴더로 지정합니다.

```bash
.venv/bin/python scripts/prepare_carla_throttle_isolation_intent.py \
  --reference-capture ORIGINAL_CAPTURE \
  --reference-source-dir ORIGINAL_SOURCES \
  --source-evidence ORIGINAL_VEHICLE_RESPONSE.json \
  --source-dir NEW_CAPTURED_SOURCES \
  --branch continue_pi --output NEW_PI_INTENT.json
```

고정 분기는 `--branch hold_throttle`과 별도의 출력 파일로 작성합니다. 새 source bundle은
`carla_brake_protocol.py`, `diagnose_carla_brake_response.py`,
`diagnose_carla_self_hit.py`, `carla_throttle_isolation_protocol.py`,
`diagnose_carla_throttle_isolation.py`의 정확한 다섯 파일입니다. 원본 세 파일은 원래
bundle로 보존합니다. 새 runner는 공통 정리·소유권 구현을 재사용하지만 원본 실행기와
파일 hash가 같다고 주장하지 않습니다.

원격에서는 기존 optional CARLA 환경과 같은 engine lock·execution store를 사용합니다.
새 실험 ID별 marker와 quarantine을 기록하고, 정리 및 봉인 report 게시 이후에만
예약 해제 영수증을 남깁니다. 불확실한 정리는 다음 실행을 막습니다. 플랫폼 worker나
CARLA를 재시작해서 실패를 지우지 않습니다.

설치된 CARLA Python과 adapter를 import할 수 있는 Linux 환경에서 실행합니다.
`CARLA_PYTHON`은 그 Python의 경로, 나머지 대문자 경로는 실제 캡처·입력·출력 경로로
바꿉니다. 기존 exclusive worker 환경과 전용 엔진 사용을 먼저 확인해야 합니다.

```bash
env ADE2E_CARLA_EXCLUSIVE=1 "$CARLA_PYTHON" \
  NEW_CAPTURED_SOURCES/diagnose_carla_throttle_isolation.py \
  --intent NEW_PI_INTENT.json --reference-capture ORIGINAL_CAPTURE \
  --runtime-dir WORKER_RUNTIME --output NEW_PI_CAPTURE
```

이 명령은 한 번의 실험만 실행합니다. 같은 입력·출력 경로를 재사용하지 않습니다.
각 실행이 종료되고 정리·예약 해제가 확인된 뒤 다른 분기를 실행합니다. 두 결과를
회수한 Mac에서는 다음 명령으로 오프라인 비교 보고서를 만듭니다.

```bash
.venv/bin/python scripts/analyze_carla_throttle_isolation.py \
  --trial NEW_PI_CAPTURE --trial NEW_HOLD_CAPTURE \
  --source-dir NEW_CAPTURED_SOURCES \
  --reference-capture ORIGINAL_CAPTURE --reference-source-dir ORIGINAL_SOURCES \
  --source-evidence ORIGINAL_VEHICLE_RESPONSE.json --output NEW_ANALYSIS_DIRECTORY
```

원본 파일·이전 보고서와 플랫폼 DB는 수정하지 않습니다. 분석 결과는 별도의 디렉터리에
JSON·HTML과 정확한 원본 다운로드 파일로 보존합니다.

## 실제 두 분기 결과

두 실행은 정착 26tick, 준비 225명령/226관측, 연장 40명령/41관측을 모두 완료했습니다.
각 3,413개의 봉인 event와 예약 해제 영수증을 보존했습니다. 원본 기준과 새 실행,
두 새 실행 사이의 선언한 전환 상태 차이는 모두 0이었습니다. 두 분기의 준비 요청
이력은 같으며, 완전한 내부 상태나 통계적으로 독립인 반복 실험을 입증한 것은 아닙니다.

| 실제 관측 · 추가 2초 | PI 계속 | 마지막 요청 throttle 고정 |
| --- | ---: | ---: |
| 시작 속도 m/s | 1.943290 | 1.943290 |
| 종료 속도 m/s | 2.008349 | 2.029427 |
| 요청 throttle 범위 | 0.121181652–0.124940842 | 0.12510625523063976 고정 |
| 구간 가속도 최소 m/s² | −0.194610 | −0.176571 |
| 구간 가속도 최대 m/s² | +0.270581 | +0.272808 |
| 인접 구간 가속도 부호 교대 | 39/39 | 39/39 |

**입력 throttle을 고정해도 가속도 교대가 남았습니다.** 따라서 이 조건에서는
추가 2초 동안 throttle 요청의 교대가 진동을 유지하는 데 필수라고 볼 수 없습니다. 두 분기 모두
양·음 가속도가 교대로 나타났으며, 2초의 종료 속도 차이만으로 PI 이득이나 제동 모델을
결정하지 않습니다. 두 분기는 같은 PI 준비를 거쳤으므로 그 준비가 남긴 과도응답의
가능성은 배제하지 않습니다. 접촉·타이어·solver 원인은 아직 분리하지 못했습니다.

PI 실행은 `18687bdb-9898-4afa-9ce9-164f89da0f4c`, 고정 실행은
`08d3ee53-0ef2-471c-8038-686510c36afd`입니다. 각각의 원본 보고서 SHA는
`8fa8458c607f0886b7ce71c92a07a5dbb62d2d897e37b43b1c6d6b14c7cdf8c8`,
`1843a4ed1794065cf6aa5a7f6281791d9d7d35b06e6abd8b1c5ceb1bcf000067`입니다.

실행 뒤 별도 원격 조회에서 같은 worker·CARLA PID와 기존 source 92개, 원래 23actor·
world·map·settings·weather, quarantine 없음이 확인됐습니다. 기존 49개 테이블의
3,394행과 관리 파일 745개도 보존됐고 FK 오류는 0입니다. 운영 worker heartbeat는
행 bytes 동일성 비교에서 제외합니다. 플랫폼 실행 결과를 새로 만든 실험은 아닙니다.

2026-09-14 당시 다음 과제였던 substep 비교는 **2026-09-26에 별도 protocol/2로 실제
수행했습니다.** 초기 구현의 '설정 적용이 frame을 진행시키지 않는다'는 가정은 실제
설치된 CARLA에서 맞지 않아 폐기했습니다. v1 원본 protocol/intent/sequence bytes와
기존 기록을 새 시간 해석으로 바꾸지는 않습니다.

새 비교는 동일한 225개 준비 요청과 F0 handoff까지 기존 0.01s 설정을 유지합니다.
그 뒤 첫 고정 throttle 명령을 ACK받고 설정 API를 호출하며, API 내부에서 진행한
1frame을 첫 계측 구간으로 기록합니다. 기준군도 같은 API로 0.01s를 다시 적용하고,
변경군만 0.005s를 요청합니다. 나머지 39구간은 명시적으로 tick하므로 양쪽 모두
40명령/41관측입니다. `fixed_delta_seconds=0.05`, `max_substeps=10`은 유지하고
설정·frame·원본을 모두 보존합니다. 선언한 1frame보다 더 진행하거나 readback이
다르면 실패로 남깁니다.

실제 두 실행의 가속도 부호 교대는 39/39→0/39였습니다. 이는 제한된 2초 조건의
substep 민감도이며, 기존 protocol3 준비 실패와 미실행 제동 pulse 8개는 그대로입니다.
[실제 비교 범위와 결과](carla-substep-observed-pair-20260926.md).

기존·신규 관련 9개 시험 모듈의 **480개 시험이 85.22초에 통과**했고 실행 전후 소스
hash가 같습니다. 작성한 입력과 fake native의 계약시험이며 위 실제 CARLA 두 건과
구분합니다. 전체 플랫폼 시험을 다시 실행한 결과는 아닙니다. 실제 비교 HTML에서는
연장 82행, 한 분기 41행, 준비 452행, 정착 52행과 선택 해제 시 0행·빈 그래프를
확인했습니다. 원본·분석 코드·보고서 33파일은 HTTP 응답 bytes와 저장본이 일치합니다.
Engine 가속도의 F0는 준비 응답이므로 위 구간 가속도 39/39 비교와 별도로 유지합니다.

[실행·시험·브라우저 검증 기록](validation/carla-throttle-isolation-2026-09-14.json).
