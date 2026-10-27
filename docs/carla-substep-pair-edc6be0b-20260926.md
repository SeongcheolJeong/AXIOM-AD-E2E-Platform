# CARLA substep 실제 비교 · 고정 소스 edc6be0b

2026-09-26 · 실제 native 실험 2건 / 원본 기반 독립 수치 재계산

## 결론

동일한 225개 준비 입력과 일치한 관측 전환 상태에서 throttle을 고정하고,
physics substep 상한을 0.01초와 0.005초로 비교했다. 0.005초 조건에서는 추가
2초 구간의 가속도 양·음 교대가 39/39회에서 0/39회로 줄었고, 40개 구간
가속도의 모집단 표준편차는 0.210178에서 0.016074m/s²로 약 92.4% 감소했다.

**이 저속 조건의 응답이 substep 설정에 민감하다는 실제 근거다.** 타이어·접촉·solver
중 하나만을 원인으로 확정하거나, 정속 유지·제동·센서 모델을 인수한 결과는 아니다.
5ms 조건에서도 양의 가속도가 남는다. 운영 adapter의 기본 설정과 기존 주행 실패
판정은 변경하지 않았다.

## 조건과 결과

CARLA build `1ae5356`, adapter `0.8.0`, `Town10HD_Opt`, 단일 Tesla 차량,
동일한 소스/설정/날씨/장착 조건을 사용했다. LiDAR와 선행차, production controller는
사용하지 않는 별도 native 진단이다. `fixed_delta_seconds=0.05`, `max_substeps=10`,
throttle `0.12510625523063976`, brake/steer 0을 고정했다.

| 항목 | 10ms 기준군 | 5ms 변경군 |
| --- | ---: | ---: |
| 실험 ID | `9d57ae8e-b9ff-423f-90ce-bf344686766c` | `14e804df-652c-4a7b-89d3-49b3ba47adbe` |
| 준비 명령 / 관측 | 225 / 226 | 225 / 226 |
| 추가 계측 명령 / 관측 | 40 / 41 | 40 / 41 |
| 시작 속도 m/s | 1.943290 | 1.943290 |
| 종료 속도 m/s | 2.029427 | 2.050877 |
| 구간 가속도 최소 / 최대 m/s² | −0.176571 / +0.272808 | +0.034786 / +0.109734 |
| 가속도 표준편차 m/s² | 0.210178 | 0.016074 |
| 가속도 부호 교대 | 39 / 39 | 0 / 39 |
| 계측 구간 차분 기반 최대 절대 jerk m/s³ | 8.592635 | 0.547525 |
| 실행 상태 / native 종료 코드 | collected / 0 | collected / 0 |
| 소유 actor 정리 | confirmed | confirmed |

가속도는 각 관측의 수평 진행 방향 속도 차이를 실제 source timestamp 차이로 나눠
계산했다. 40개 구간을 모두 포함했으며 평활화·보간·표본 제거를 하지 않았다.
표의 jerk는 이 40개 가속도 사이 39개 차분의 최대 절댓값이며, 준비 구간에서 계측
구간으로 넘어가는 경계의 jerk까지 포함한 전체 주행 최댓값이 아니다. 표준편차는
구간 내 변동량이지 독립 반복 실험에서 구한 불확도가 아니다.

## 실제 API 동작을 반영한 비교

앞선 구현의 “설정 변경 시 world frame 불변” 전제는 실제 `apply_settings()` 동작과
맞지 않았다. 이번 protocol/2는 동일한 F0에서 첫 고정 throttle 명령을 보내 ACK를
확인한 뒤 두 군 모두 설정 API를 호출한다. 기준군도 0.01초를 다시 적용한다.
API 내부의 한 tick은 첫 계측 구간으로 기록하고 나머지 39개 구간만 명시적으로 tick한다.

실제로 기준군은 frame 76450→76451, 변경군은 88261→88262로 각각 정확히 한 frame,
0.05000000074505806초 진행했다. API 반환 frame과 다음 snapshot은 구분해 보존했다.
예상과 다른 frame 진행 또는 다른 설정 변경은 실패로 남긴다. 설정 적용 전/후 전체
readback과 실패 시 복원 후보도 명시적으로 고정했다.

두 실험의 직접 F0 비교에서 위치·속도·회전·가속도·각속도·cached throttle·raw 회전
상태의 선언한 차이는 모두 0이었다. 숨겨진 시뮬레이터 내부 상태까지 동일하거나
통계적으로 독립인 반복임을 입증한 것은 아니다. 새 world episode ID는 신규 입력에
명시적으로 고정했고 과거 reference의 episode ID와 원본 파일은 변경하지 않았다.

## 검증과 보존

실행 도중 checkout의 다른 작업이 갱신되고 있어 별도 디렉터리의 소스를 고정했다.
동일한 고정 소스로 두 실험을 수행했고 업로드·회수 파일 SHA를 대조했다. 원본 event는
각 3,412개이며 두 event chain·보고서·예약 해제 영수증을 검증했다.
오프라인 분석은 `verified 2/2`, 비교는 `comparable`이다.

고정 후보의 관련 시험 **129개**가 통과했다. 이 수에는 설정 적용 전/후 예외,
0/2-frame 응답, 제3자 설정 변경에 대한 5개 반례가 포함되며 실제 CARLA 실행 건수와는
별도다. 다른 세션의 겹치는 시험 수와 합산하지 않는다.

Chrome에서 계측 82행, 한 실험 41행, 선택 해제 0행, 준비 452행, rest 52행 및
계측 복귀 82행을 확인했다. JavaScript page error는 0개였다.

기준군 종료 직후의 첫 비잠금 외부 조회는 다른 진단이 새로 만든 actor 38/39를
관측했다. 이는 기준군 소유 actor 36/37과 다르며 그 조회 기록도 보존했다.
다른 작업의 actor나 예약을 삭제하지 않았다. 후속 조회는 `engine.lock`으로 직렬화해
원래 23개 actor·world/settings/weather, 같은 엔진/worker 프로세스, quarantine 없음을
확인했다. native 실험은 두 건 모두 한 번씩만 실행했다.

운영 DB의 Run 74개·Job 81개·Artifact 349개·Recording 11개는 유지됐고,
`quick_check=ok`, 외래키 오류 0개다. 이는 건수·구조 검사이며 DB 전체 byte 동일성
검증으로 확대하지 않는다. 원래 reference 4개 파일은 고정 복사본과 SHA가 일치한다.

## 증거 위치

- [기계 판독 요약·실험 ID·SHA·직접 비교](validation/carla-substep-pair-edc6be0b-20260926.json)
- 원본/분석: `.runtime/substep-owned-20260926-edc6be0b/`
- 상세 HTML: `.runtime/substep-owned-20260926-edc6be0b/analysis/index.html`
- 독립 재계산: `independent-numerics.json`, 브라우저 확인: `browser-verification.json`
- 고정 소스와 반례 시험: `candidate/scripts/`, 실행 시험 로그: `frozen-tests.log`

## 다음 제품 작업

5ms를 전역 기본값으로 바로 바꾸지 않는다. 명시적으로 버전이 고정되는 physics 설정
후보로 다루고, 원래 이동 선행차 20m / 정지 선행차 8m 조건의 동일 센서·rig·stack·판정
기준으로 재시험해야 한다. 이때 저속 진동 개선과 관측 부족에 의한 emergency-stop,
차량 제동 응답의 영향은 분리한다. 기존 감속/jerk 실패, brake pulse 인수와 상용 gate는
이번 standalone 비교 결과로 통과 처리하지 않는다.
