# CARLA substep 실제 비교 · 2026-09-26

## 결론과 적용 범위

고정 throttle의 동일 준비 입력과 일치한 관측 전환 상태에서, physics substep 상한을
0.01초에서 0.005초로 바꾸자 추가 2초 구간의 가속도 부호 교대가 **39/39회에서 0/39회**로
줄었다. 이 조건의 저속 교대 응답이 substep 설정에 민감하다는 실제 엔진 근거다.
타이어·접촉·solver 중 유일한 물리 원인을 확정하거나, 차량 모델 보정·제동 품질·실차 정확도
인수를 완료한 결과는 아니다. 전체 상용 gate와 기존 주행 실패 판정은 유지한다.

본문 수치표는 `.runtime/substep-20260926`의 아래 **두 실행만** 다룬다. 다른 폴더의 같은 날
commissioning이나 비교 실행을 이 두 건에 합산하지 않는다. 말미의 r1 반복은 별도로 구분한다.

| 구분 | Experiment ID | substep 상한 |
| --- | --- | ---: |
| 기준군: 같은 설정 다시 적용 | `8b5670f3-1e00-4c02-8622-914a38f3e0ca` | 0.01초 |
| 변경군 | `87d010bd-f7bc-4464-8011-8c170573f778` | 0.005초 |

## 동일 입력과 시간 경계

엔진 보고 build는 `1ae5356`, 지도는 `Town10HD_Opt`, adapter는 `0.8.0`이다.
각 실행의 원본 준비 225명령·226관측과 추가 40명령·41관측을 모두 보존했다.
두 실행은 동일한 새 source bundle 5개와 설치 모듈 hash를 사용한다. 이 두 실행의 reference는
재가동된 엔진에서 수집한 새 commissioning `3b7d8d20-9600-4f8a-a636-9dba63133c2e`이며,
첫 진입 준비 실패·225명령·226관측·제동 계측 0건을 그대로 보존한다. 기존 9월 14일
reference의 bytes와 hash는 수정하지 않았다. 새 world ID를 별도로 고정하고 map hash·
settings·weather·기존 actor inventory가 원래 환경과 같음을 별도 읽기로 확인했다.

추가 구간의 throttle 요청은 양쪽 모두 정확히 `0.12510625523063976`이며 brake와 steer는 0이다.
직접 비교한 전환 상태의 위치·속도·회전·가속도·angular velocity·cached throttle·raw RPM/wheel
상태 차이는 선언된 비교 항목 모두 0이었다. 관측된 상태의 일치이지 숨은 엔진 상태 전체의
동일성이나 통계적 독립성을 증명하지는 않는다.

**앞선 무프레임 진행 가정은 폐기했다.** 설치된 CARLA의 `apply_settings()`는 두 설정 모두에서
1프레임, 약 0.05초를 진행했다. 따라서 protocol/2는 첫 명령을 ACK받은 뒤 설정을 적용하고,
이 내부 tick을 40개 계측 구간의 첫 구간으로 기록한다. 나머지 39구간만 명시적으로 tick한다.
기준군에도 같은 API로 0.01초 설정을 다시 적용하므로 API 호출 유무가 비교 변수에 섞이지 않는다.
API 반환 frame은 적용 전 frame이며, 별도 snapshot의 실제 다음 frame과 구분한다.
`fixed_delta_seconds=0.05`, `max_substeps=10`은 양쪽에서 유지했다.

기존 protocol/1 입력과 sequence hash는 기존 형태로 읽는다. 새 의미는 protocol/2·intent/2·report/2로
분리돼 있으며, 과거 결과를 새로운 시간 해석으로 덮어쓰지 않는다.

## 실제 수치

아래 가속도는 원본 종방향 속도의 인접 표본 차이를 실제 timestamp 간격으로 나눈 값이다.
Jerk는 그 인접 구간 가속도 차이를 같은 시간 간격으로 나눈 값이다. 평활화·표본 제외·보간은 없다.
이 수치를 과거 플랫폼의 다른 주행·급제동 시험 지표와 같은 시험 결과로 합치지 않는다.

| 추가 2초의 관측값 | 0.01초 기준군 | 0.005초 변경군 |
| --- | ---: | ---: |
| 시작 속도, m/s | 1.943290 | 1.943290 |
| 종료 속도, m/s | 2.029427 | 2.050877 |
| 구간 가속도 최소, m/s² | -0.176571 | +0.034786 |
| 구간 가속도 최대, m/s² | +0.272808 | +0.109734 |
| 구간 가속도 표준편차, m/s² (모표준편차) | 0.210178 | 0.016074 |
| 인접 가속도 부호 교대 | 39/39 | 0/39 |
| 속도 차분 기반 최대 절대 jerk, m/s³ | 8.592635 | 0.547525 |

변경군에서도 종료 속도는 목표 정속 2m/s와 같지 않다. 이번 실험은 고정 throttle의 입력 분리이며,
정속 제어기 인수나 제동 pulse 시험이 아니다. 추가 2초에서 관찰된 교대 가속도의 제거를 장시간·다른 속도·
다른 차종·원래 close-lead 조건의 통과로 확대하지 않는다.

## 근거 확인

두 프로세스는 exit 0·`collected`·`cleanup=confirmed`로 끝났다. 각 원본 4파일을 원격 SHA와 대조해
회수했으며, 원본 event chain·terminal report projection·명령 ACK·다음 frame·source bundle·
reservation release를 분석기가 검증했다. 분석 결과는 2/2 verified, 비교 결과는 `comparable`이다.
직접 비교한 고정 조건은 선언된 substep 상한을 제외하고 같다.

별도 실행 후 읽기에서 같은 새 world의 원래 23actor·map·settings·weather 일치와 quarantine 부재를
확인했다. 운영 `.data/platform.db`의 SHA-256도 작업 시작 전과 같다. 기존 Run의 실패 판정이나
production CARLA adapter의 기본 substep을 이 실험으로 교체하지 않았다.

관련 Python 회귀 5개 모듈의 **124개가 55.76초에 통과**했다. 전체 플랫폼 시험 통과 수가 아니다.
Chrome headless에서 실제 보고서의 계측 82행, 준비 452행, 한 실행 선택 41행, 모두 해제 0행과
`comparable` 표시를 확인했으며 page error는 0개였다. 이 브라우저 검사는 실제 원본 보고서 UI이며
새 시뮬레이션이나 운영 플랫폼 Job을 생성하지 않았다.

## 추가 독립 원본 검토

프로젝트 runner/analyzer 및 캡처 Python을 import하지 않는 별도 검토기로 실제 원본
두 건을 다시 확인했다. 합계 6,824개 event의 chain·terminal report seal·예약 해제,
전체 명령의 ACK/float32 readback/다음 frame, 첫 ACK 이후의 설정 변경 순서, 두 실행의
고정 조건과 개입 전 raw kinematics의 정확한 일치가 확인됐다. 수치는 위 표와 일치한다.

2026-09-26 23:10 KST의 독립 사후 조회에서 world/map/settings/weather·원래 23actor
복원, store ID 유지, quarantine 부재와 production source 92개 hash 일치를 확인했다.
로컬 `.data` 749파일은 추가·누락·내용 변경이 없고 DB quick_check=ok, FK 오류=0이다.
Run 74·Job 81·Artifact 349·Recording 11은 그대로다.

별도 복구 경계 모듈의 4개 fake-world 시험도 7.45초에 통과했다. 설정 적용 전 실패,
적용 후 응답 실패, 2frame 진행을 정상 완료로 만들지 않고 정리하며, 알 수 없는 외부
설정은 임의로 복원 대상으로 채택하지 않고 격리를 유지하는 범위다. 실제 장애 주입
인수나 전체 플랫폼 시험 통과를 뜻하지 않는다.

[독립 검토·보존 결과와 원본 hash](validation/carla-substep-independent-review-2026-09-26.json).
`.runtime`의 원본은 Git에 포함되지 않으므로 별도 보존이 필요하며, 요약 JSON만으로
원본 데이터를 복원할 수는 없다.

## 산출물

- 대화형 비교 보고서: `.runtime/substep-20260926/analysis-v2/index.html`
- 전체 분석·원본 연결: `.runtime/substep-20260926/analysis-v2/analysis.json`
- 별도 수치 재계산: `.runtime/substep-20260926/independent-numeric-summary.json`
- 원본 회수: `.runtime/substep-20260926/{baseline-001,treatment-0005}/capture/`
- 실행 후 world 확인: `.runtime/substep-20260926/after-world.json`
- 브라우저 확인: `.runtime/substep-20260926/browser-check.json`

다음 품질 작업은 0.005초를 별도 후보 설정으로 고정한 뒤 원래의 엄격한 정속 준비와
close-lead/제동 시험을 다시 수행하는 것이다. 현재 운영 기본값을 조용히 변경하거나 기존
판정 기준을 완화하지 않는다.

## 최종 재검증 기록

[기계 판독 검증 기록](validation/carla-substep-comparison-2026-09-26.json)에 위 두 실행의
원본 4파일씩, 실행 ID, 설정 readback, 직접 handoff 비교, 실행 후 world와 소스 보존을
연결했다. 별도 읽기에서 원격 release 92/92파일과 회수한 두 실행의 원본 bytes가 일치했고,
로컬 `.data`의 작업 전 749/749파일이 동일하며 새 파일·변경·유실은 0개였다.

최종 Python 6개 모듈의 **135개가 57.19초에 통과**했다. 이는 앞선 124개에 새 counterexample
11개를 포함해 다시 실행한 범위이며 두 결과를 합산하지 않는다. 마지막 보고서는
`.runtime/substep-comparison-20260926-5c6d783a/analysis-final/index.html`이다.
Chrome에서 기준군/변경군 label, 계측 82행·준비 452행·단일 선택 41행·선택 해제 0행과
`comparable`을 확인했고 page error는 0개였다.

동시 실행 중 요청한 `dbc6a504-2329-42e9-90c3-f60c02b730ee`는 engine lock에서
`StoreConflict`로 거절됐다. 이 요청은 준비/계측 명령 0건이며 원본 오류 보고서를 별도로
보존했다. 위 두 실제 비교 실행이나 주행 성능 실패에 합산하지 않는다.

## 별도 반복 r1 · 원본과 구간 가속도 독립 확인

`.runtime/substep-comparison-2026-09-26-r1/`에는 위 표와 합산하지 않는 별도 두 실행을 보존했다.
기준군은 `981945f4-b256-4cbe-b611-fae61c5153bb`, 변경군은
`e616c267-7da7-42d7-a814-5379ea589509`이다. 양쪽 모두 준비 225명령/226관측과
추가 40명령/41관측을 완료했고 cleanup은 confirmed다. 시작 관측 상태의 선언된 차이는 모두 0이다.

원본 XY 속도와 yaw, 실제 timestamp로 독립 재계산한 구간 가속도 범위는
기준군 −0.176571..+0.272808m/s², 변경군 +0.034786..+0.109734m/s²였다.
최대–최소 차이는 0.449379→0.074947m/s²로 **83.322% 감소**했고 부호 교대는
39/39→0/39로 재현됐다. 이 반복도 숨은 상태의 동일성이나 통계적 독립성을 입증하지 않는다.

원격 회수 파일·고정 소스 16개를 SHA로 대조했고, 별도 읽기에서 실행 전 world 전체 상태와
worker PID가 같았다. 기존 DB 및 과거 참조 입력/소스 10개 hash도 변하지 않았다.
관련 5개 모듈 회귀 124개는 55.59초에 통과했으며, 실제 보고서 브라우저에서 두 실행 82행,
한 실행 41행, 모두 해제 0행과 page error 0개를 확인했다. 이 회귀는 전체 플랫폼 시험이 아니다.

이 반복의 보고서는 `analysis/index.html`, 전체 결과는 `analysis/analysis.json`,
독립 계산은 `independent-verification.json`, 원격 사후 조회는 `remote-final.json`,
브라우저 확인은 `browser-check.json`이다. 모두 위 r1 폴더 아래에 있다.
`sources/`와 `source-hashes.json`을 실제 실행 소스로 사용하며 이후 작업 폴더의 소스 변경을
이 실행에 소급 귀속하지 않는다.

## 새 episode에서 준비 실패부터 재확인한 별도 실행 묶음

`.runtime/substep-20260926-c3d687b0`의 세 실행은 위 두 실행 및 r1 반복과 별도다.
새 commissioning `3b7d8d20-9600-4f8a-a636-9dba63133c2e`에서 기존 protocol3의
225명령/226관측 준비 실패를 다시 보존했다. 이 새 원본을 기준으로 한 10ms 대조
`ec9bc7fc-9164-4d29-a1a3-ce94b2c97410`과 5ms 변경
`819e47f6-b587-483d-9cfe-89fece1c7fc7`은 각각 40명령/41관측을 완료했다.
직접 비교한 전환 상태 차이는 0이고 분석은 2/2 verified·comparable이다.
구간 가속도 부호 교대 39/39→0/39, 속도 차분 기반 최대 절대 jerk
8.592635→0.547525m/s³를 독립 재계산으로 확인했다. 세 실행 모두 cleanup=confirmed이며
commissioning 실패를 계측 또는 제동 성능 통과로 바꾸지 않았다.

이 묶음의 최종 관련 7개 Python 모듈 회귀는 139개/64.38초 통과이며 전체 플랫폼 검사는 아니다.
Chrome에서 실제 비교 보고서의 계측 82행, 준비 452행, 한 실행 41행, 모두 해제 0행과
page error 0개를 확인했다. 23:15 KST 별도 읽기에서 원래 world·23actor·settings·weather,
production source 92개, quarantine 부재와 로컬 `.data` 749파일 불변을 확인했다.
Run 74·Job 81·Artifact 349·Recording 11, DB quick_check=ok·FK 오류=0이다.

보고서는 이 묶음의 `comparison-report/index.html`이며, 원본·분석·시험·브라우저 확인의
경로와 hash는 [별도 인수 기록](validation/carla-substep-current-episode-2026-09-26.json)에 있다.
이 반복들을 독립 확률 시행으로 합산하거나 기존 주행 판정·운영 기본값을 변경하지 않는다.
