# 지표와 검증 근거의 의미

현재 지표는 **참조 모델/실험적 CARLA 어댑터의 관측값**입니다. 규제·인증·실차 안전 한계를 나타내지 않습니다. 판정 기준은 각 실행의 `metrics`에 포함된 `operator`와 `threshold`를 따릅니다. 서로 다른 어댑터의 수치를 같은 측정법이라고 가정하지 않습니다.

## 참조 실행기 v0.1

고정 50 ms 간격으로 2D 위치·속도를 적분합니다. 마지막 간격은 요청한 실행 시간에 맞춥니다. 자차와 상대 객체는 축과 나란한 직사각형이며 자차는 직선으로 주행합니다. 진짜 인지 결과 대신 상대 객체의 실제 모델 상태를 사용하는 지연된 제동 규칙입니다.

시드는 초기 gap에 ±0.15 m, trigger에 ±0.05 s의 의도적인 변화를 줍니다. 같은 입력·시드·실행기 버전은 같은 궤적과 output hash를 생성합니다. 다른 지도·날씨·시간 이름을 선택하는 것은 분류 메타데이터 변경이며 이 모델에서 렌더링이나 마찰을 자동 변경하지 않습니다.

| 지표 | 계산 | 참조 기본 조건 | 범위/해석 |
|---|---|---|---|
| `collision_count` | 자차와 지정 target의 bounding box 접촉 및 간격 사이의 선형 swept 교차 | `== 0` | 첫 접촉을 1회로 기록; 충돌 후 물리·다중 접촉 횟수 없음. 배경 장식 actor는 평가 대상 아님 |
| `min_ttc_s` | 상대 위치/속도를 고정했을 때 두 직사각형이 처음 겹치는 시간의 실행 중 최솟값 | `>= 1.0 s` | 좌우 방향과 종방향 overlap 시간 구간의 교집합; 움직임 변화 이후 실제 충돌 시간을 예측한다는 보증 없음 |
| `max_deceleration_mps2` | `max(0, -a_ego(t))` | `<= 6.0 m/s²` | 마찰·지연·제동 명령으로부터 계산한 모델 가속도 |
| `max_jerk_mps3` | 연속 샘플의 `abs(a[i]-a[i-1]) / dt` 최댓값 | `<= 15.0 m/s³` | 이산 시간 간격과 속도 0 제한의 영향을 받음 |
| `distance_m` | 0에서 시작한 자차 종방향 최종 위치 | `>= 0 m` | 정보 표시용 조건; 교통 목적 달성·경로 완료를 판정하지 않음 |

`min_ttc_s = null`은 **이 모델의 일정 상대 속도 가정에서 교차가 예측되지 않음**을 표현합니다. 수학적 무한대 의미로 `gte`는 통과할 수 있지만 `lte`나 유한 값 `eq`는 통과하지 않습니다. 다른 지표의 null은 누락이며 합격할 수 없습니다.

기준 구현은 `backend/src/ade2e/reference.py`입니다. 연속 시간 충돌, 고충실도 타이어·조향·충돌 동역학, 보행자 인지, 가림, 센서 노이즈를 구현했다고 해석하지 않습니다.

## CARLA 실험적 어댑터

위와 같은 공통 metric name을 사용하지만 관측 방법은 다릅니다. `collision_count`는 측정 시간 안에 collision sensor가 보고한 **서로 다른 접촉 actor 수**이며, settling 중 늦게 도착한 이벤트는 timestamp로 제외합니다. `min_ttc_s`는 가까운 같은 방향 선행차에 대한 종방향 gap/closing speed 근사입니다. 감속·jerk는 관측 속도의 차분이고 주행 거리는 연속 XY 위치 차이의 길이를 더합니다.

실험적 기본 조건은 collision 0, TTC ≥ 1.5 s, 감속 ≤ 6 m/s², jerk ≤ 10 m/s³입니다. 참조 모델과 판정 조건도 다르므로 비교 보고서에서는 adapter·metric definition·threshold를 함께 고정해야 합니다. 버전별 metric registry와 사용자가 선택한 공통 observer 실행은 다음 단계입니다.

단위시험은 가짜 world/transport와 encoding의 계약을 검사합니다. 이후 실제 GPU 서버에서 제한된 RGB/LiDAR 수집, live LiDAR의80명령/81관측·ACK/다음 physics와0.5 원격 프로세스 종료·응답 유실 회수를 별도 확인했습니다. 정확한 버전·실행 조건과 새 context/2 장애 인수의 미완료 범위는 [개발 상태](status.md)에 있습니다. 이 근거가 collision callback의 완전성, 모든 장애의 복구, CARLA 물리 정확도 또는 실측 센서 상관성을 입증하지는 않습니다.

### 고정 CARLA campaign의 접촉 수 판정

새 controlled campaign은 `carla-observed-contact-count/1`을 실행 계획에 고정합니다. 현재 허용 adapter는 CARLA0.8.0입니다. `collision_count`의 원본 `observed contacts` 단위와 유한·비음수 정수 값을 그대로 두고 suite의 `count` 기준에 적용합니다. 이는 단위 이름만 바꾸는 전역 규칙이 아니며, 다른 버전·단위·누락·잘못된 개수는 이 계약으로 평가하지 않습니다. 적용한 check에는 `measurement_contract`가 기록됩니다.

계수 대상은 계측 시간 안에 callback으로 보고된 서로 다른 접촉 actor입니다. 같은 actor의 반복 접촉 횟수나 센서가 보고하지 못한 충돌을 뜻하지 않습니다. 접촉 수0 기준의 통과는 **관측값의 기준 충족**이며 stream 완전성·무충돌 보장이 아닙니다. 과거 campaign 및 원본 run의 단위·값·판정 기준을 소급 변경하지 않습니다. [캠페인 사용법](campaign-control.md).

## 실행 판정과 요구사항

- `status=completed`: 요청한 전체 시간과 필수 지표가 유효한 형식으로 반환됨. metric predicate가 값과 일치해야 합니다.
- `verdict=passed/failed`: 해당 실행에 기록된 지표별 예제 조건의 모두 통과 여부.
- `status=error`: 불완전 trajectory, 누락 지표, 상충 판정, 실행 예외 등. 오류는 검증 근거가 아닙니다.
- 요구사항: **가장 최신 실행**이 현재 scenario revision·parameters·seed와 일치하는 정상 완료 기준 실행인 경우만 predicate를 계산합니다. 과거 합격으로 현재 누락·실패를 가리지 않습니다.

커버리지 셀에서 `total`은 등록된 시험 수, `tested`는 현재 기준 실행이 있는 시험 수입니다. `tested = passed + failed`, `total = tested + untested`를 유지합니다. 등록하지 않은 ODD 공간은 이 분모에 들어가지 않습니다. 대시보드 pass rate는 저장된 실행 기록 전체에 대한 통과 비율이므로 차량의 실제 안전 확률로 읽지 않습니다.

## 무결성·재현과 범위

실행은 immutable scenario snapshot, seed, adapter ID/version, fidelity, input/output SHA-256을 보관합니다. 아티팩트는 내용의 SHA-256과 크기를 확인한 후 다운로드합니다. SHA-256은 bytes가 변하지 않았는지 확인하는 수단이며, 외부 출처의 진위·서명·모델 타당성·승인자의 책임을 대신하지 않습니다.

과거 실행을 다시 평가하는 baseline·observer versioning, 서명/감사, 요구사항 변경 영향, 동일한 입력으로 서로 다른 engine을 비교할 유효 범위는 상용화 로드맵의 별도 항목입니다.
