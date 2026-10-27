# 관측 구간에 따른 CARLA 정지 계획 — v2 설계 제안

상태: **설계 제안이며 미구현·미검증이다.** 기존 `lidar-corridor/1`, `stopping-headway/1`, `proportional-control/1`, `stack-kernel/1`의 동작·기준·저장 결과를 변경하지 않는다. 이 문서는 새로운 관측 계약과 명시적으로 선택하는 v2 커널을 제안한다. 아래 절차를 통과하기 전에는 주행 품질 개선이나 안전성 확보를 주장하지 않는다.

## 실제 근거와 문제

진단 원본은 [전체 구간 보고서](validation/carla-close-lead-policy-diagnosis-2026-09-13.json)다. SHA256은 `17745197b263ef0393148cfd75cf66a91563fdfd4fab2428b6122cbf8ea47b22`이다. 두 실제 CARLA 실행의 전체 **160개 명령 구간, 162개 물리 관측**과 원본 파일 해시, 구간별 소비 XYZ 해시, 지면 bin별 표본 수·XYZ 범위·판정 조건을 보존했다. 원본 XYZ와 패킷을 기존 v1 커널로 다시 계산했으며, 처리시간을 제외한 160개 출력 전체가 일치했다. 새 주행이나 대체 알고리즘의 시험은 수행하지 않았다.

| 항목 | baseline close-lead | dense close-lead |
|---|---:|---:|
| 실제 실행 | `run_f37ad4682b1c449d93c88b8e973d308c` | `run_621d9cda9be14d2b9c9e11ed1d1fe440` |
| 첫 최대 제동 요청 | 구간 11, 0.55 s | 구간 12, 0.60 s |
| 당시 지면 bin 표본 수 | `[25, 4, 0]` | `[90, 24, 0]` |
| 관측 표면까지의 전방 여유 | 7.072660 m | 6.930948 m |
| 현재 속도의 정지·차간거리 기준 | 4.927995 m | 4.929668 m |
| 가장 먼 지면 표본의 전방 여유 | 6.330337 m | 6.330367 m |
| 현재/목표 속도 중 큰 값의 요구 거리 | 5.0 m | 5.0 m |
| approach / unknown_stop | 11 / 69 | 14 / 66 |
| obstacle_observed / unknown | 79 / 1 | 80 / 0 |
| obstacle_stop | 0 | 0 |
| 최대 감속 | 7.965472 m/s² | 8.948179 m/s² |
| 최대 jerk | 152.474544 m/s³ | 176.453677 m/s³ |

지면 bin은 ego 좌표의 x 구간 `[2.5, 6.666667)`, `[6.666667, 10.833333)`, `[10.833333, 15]`이다. 양쪽 모두 마지막 bin이 비었을 때만 지면 qualification이 실패했다. 전체 구간에서 지면·전방 점 개수의 최솟값 조건은 통과했고, 기존 `supported_distance_m`도 현재/순항 속도의 요구 거리 이상이었다.

실제 경로는 다음과 같다.

1. `LidarCorridor.observe`는 장애물 표면을 보존하면서 `coverage_qualified=False`를 출력한다.
2. `StoppingHeadway.plan`은 거리 비교보다 먼저 coverage를 검사하므로 `unknown_stop`을 만든다.
3. 커널이 같은 조건을 다시 검사하고 controller 출력과 무관하게 `brake=1`을 강제한다.

따라서 첫 최대 제동은 관측 표면이 정지거리 안에 들어왔거나 기존 최원거리 조건이 부족해서 발생한 것이 아니다. 그렇다고 **가장 먼 표본까지의 공간이 모두 관측됐다는 뜻도 아니다.** 그 값은 표본의 최대 x이며, 연속 구간이나 도로 폭 전체를 보증하지 않는다.

dense 구간 21·22에서는 마지막 bin에 점 하나가 생겼다. y는 각각 `+1.199614286 m`, `-1.199570298 m`로, 반폭 `1.2 m`의 경계에 가깝다. v1은 다시 approach/목표 2 m/s/제동 0을 요청하고 구간 23에서 최대 제동으로 돌아간다. 단순히 coverage bool을 완화하거나 잠깐 유지하는 방식은 이 공간 표본 문제를 해결하지 못한다.

최대 jerk는 최초 제동 요청보다 정지 근처에서 발생했다. baseline의 관측 20→21은 감속 `-7.965472 → -0.341744 m/s²`이며 제동 요청은 계속 1이었다. dense의 관측 21→22는 `-8.948179 → -0.125495 m/s²`이고 제동 해제 구간이다. 이 수치는 기존 50 ms 간격의 **3D 속도 크기 차분**이며 종방향 힘 측정이나 단일 원인 분해가 아니다. 기존 기준은 그대로 유지한다.

## v2가 지킬 경계

- 실제 XYZI는 반환된 hit이다. 발사된 모든 ray, dropout, 가림, 반사 실패를 구분하는 원본은 없다. 미반환 구간을 free space로 채우지 않는다.
- 가까운 양성 표면 근거, 현재 입력의 유효성, 공간별 표본 qualification을 서로 다른 필드로 보존한다.
- 미관측 영역은 목표 속도를 제한한다. 장애물 뒤 지면까지 관측해야만 앞에서 정지할 수 있도록 만들지는 않는다.
- 정지 계획 전체가 명시적으로 qualification된 구간 안에 있어야 한다. 최원점 하나로 내부 공백을 연결하지 않는다.
- 지면·직선 도로·정지 표면·감속 성능은 선언된 가정이다. 현재 센서/차량의 물리 인증으로 표시하지 않는다.
- 누락·stale·future·잘못된 프레임·시간 skew·해시·불완전 데이터·ODD 위반에 대한 정지 경계는 약화하지 않는다. 승차감 제한으로 이런 fallback을 지연하지 않는다.

## 제안하는 공개 관측 계약

새 모델 `CorridorObservationV2`, `schema_version="corridor-observation/2"`를 추가한다. 기존 `CorridorObservation`의 필드나 bool 의미를 바꾸지 않는다. 세부 JSON Schema는 구현 시 이 계약에 맞춰 별도 파일로 공개한다.

| 필드 | 의미·검증 |
|---|---|
| `input_validity` | `valid` 또는 `invalid`; invalid 원인을 빠짐없이 보존한다. 현재 source/clock/frame/byte 검증을 통과해야 valid이다. |
| `source_identity` | 원본 sequence, aligned/capture clock, frame, 소비 XYZ SHA·크기·점 수. 기존 패킷과 정확히 일치해야 한다. |
| `positive_surface` | 최근접 원본 XYZ, bumper 기준 여유 거리, 지지 표본 수·범위. 객체 종류·ID·속도·TTC는 포함하지 않는다. 없으면 null이며 clear를 뜻하지 않는다. |
| `coverage` | 고정된 평가 구간·횡방향 범위·셀 경계·셀별 ground/positive 표본 수와 unknown 원인. 최대 셀 수를 제한한다. |
| `coverage.method` | 예: `ground-support-grid/1`. 표본 수, 내부/횡방향 공백 처리, 경계 포함 규칙과 지면 가정을 정의하는 별도 고정 버전이다. |
| `coverage.qualified_prefix_m` | bumper부터 **고정된 공간 qualification**이 만족되는 구간의 끝. 최원점과 다른 필드다. 0 또는 null의 의미를 구분한다. |
| `unknown_regions` | qualification되지 않은 구간과 이유. 양성 표면 뒤 공간은 `beyond_observed_surface_unresolved`로 보존하고 가림이라고 단정하지 않는다. |
| `assumptions` | ground reference/source, ODD, 센서 profile 및 sampling 규칙의 버전·해시. |

셀은 ground와 positive 표본을 동시에 가질 수 있다. exclusive occupied/free 분류로 원본을 잃지 않는다. 표본의 min/max bounds도 filled volume을 뜻하지 않는다. 입력 프레임 자체가 invalid이면 좌표·표면을 제어 근거로 사용할 수 없다. valid frame에서 지면 qualification만 부족한 경우에는 양성 표면을 없애지 않는다.

공간 평가의 구현 순서는 다음과 같다.

1. 입력 유효성을 검사하고 전체 소비 점 수와 SHA를 고정한다.
2. 고정 ground plane/height band로 ground 후보와 positive surface 후보를 **각각** 계산한다. 가까운 x≥0 표면을 임의의 self-mask로 제거하지 않는다.
3. 고정 격자에 표본을 배치한다. 셀 경계의 포함 규칙과 유한 범위를 검사한다.
4. 점 개수만 아니라 종·횡방향 qualification을 평가한다. 내부 unknown 셀을 최원 표본으로 건너뛰지 않는다.
5. bumper부터 qualification된 prefix를 계산하고 그 밖은 unknown으로 보존한다. 양성 표면이 있으면 정책은 그 앞에서 정지해야 하므로 그 뒤를 새로 qualification할 필요가 없다.

**격자 크기·표본 최솟값은 이 close-lead가 통과하도록 정하지 않는다.** 구현 시작 전에 센서의 각도/높이/지면 footprint와 목표로 하는 검출 크기, 독립적인 내부·측면 공백 반례를 기준으로 선정하여 별도 calibration/qualification profile에 고정한다. 반환 hit만으로 목표 조건을 만족할 수 없다면 coverage를 낮추는 대신 센서 구성 또는 필요한 source evidence를 확장한다. 이 항목은 아직 승인·검증되지 않은 설계 입력이며, 정하지 않은 채 production 기본값을 만들지 않는다.

## 정책과 정지 조건

새 policy는 `stopping-headway/2` 또는 별도 ID로 제공한다. 출력을 `MotionEnvelopeV2`로 확장하고 기존 `StackDecision`과 구분한다.

- `mode`: `bounded_motion`, `controlled_stop`, `emergency_stop`, `hold_unknown`.
- `limiting_reason`: 관측 구간, 양성 표면, ODD 또는 입력 오류 중 실제 제한 원인.
- `qualified_interval_m`, `surface_limit_m`, `uncertainty_reserve_m`, `effective_latency_s`, `assumed_deceleration_mps2`.
- `current_stopping_requirement_m`, `planned_stopping_requirement_m`, `target_speed_mps`.
- smooth stop을 지원한다면 짧은 시간별 speed/acceleration 계획, 시작 상태·시간, 유효기간 및 controller model/version을 포함한다.

기존의 선언된 정지·차간거리 함수를 명시적으로 유지하는 예는 다음과 같다.

`D(v) = G + max(v × T_headway, v × T_latency + v² / (2 × a_assumed))`

`H`는 qualification된 prefix와 관측 표면까지의 거리 중 작은 값에서 불확실성 reserve를 뺀 값이다. 표면이 없더라도 unknown 뒤 공간을 H에 포함하지 않는다.

현재 속도와 계획 속도에서 모두 `D(v) ≤ H`여야 일반 motion을 허용한다. `H < G`이면 양의 속도를 허용하지 않는다. `H ≥ G`일 때 속도 상한은 양수 headway가 있으면 `(H-G)/T_headway`, 그리고 `-a×T_latency + sqrt((a×T_latency)² + 2×a×(H-G))` 중 작은 값이다. 이 식은 고정 가정의 algebraic bound이며 실제 정지 성능 증명이 아니다.

시간 의미도 고정한다.

- 비교는 packet의 aligned simulation clock으로 한다. 원래 capture clock은 별도 보존한다.
- source age, 입력 간 skew, 한 명령 구간 및 선언된 actuator/response 여유를 `T_latency`에 어떻게 포함하는지 명시한다. 한 항목을 누락하거나 이중 계상하지 않는다.
- plan은 시작 sequence/time과 종료 time을 가진다. 다음 입력이 없거나 만료되면 이전 plan을 연장하지 않는다.
- simulation이 pause된 wall time을 임의의 물리 이동 시간으로 바꾸지 않는다. live physical system에 같은 모델을 적용하려면 별도 clock/latency 계약이 필요하다.
- 처음 시작한 tick의 이전 가속도나 planner state를 임의로 측정값으로 만들지 않는다. 초기 상태와 불확실성을 명시한다.

정지 분기는 다음 순서로 평가한다.

1. **Hard invalid input:** 기존 유한값·해시·크기·complete frame·frame·age/skew·attitude 조건 중 하나라도 실패하면 `emergency_stop/hold_unknown`. 새 커널도 최대 제동 fallback을 유지한다.
2. **현재 속도의 정지 영역이 부족:** 양성 표면 또는 qualification된 구간 안에 현재 정지 envelope를 넣을 수 없으면 emergency fallback. comfort bound를 맞추기 위해 감속을 늦추지 않는다.
3. **유효한 제한 구간 안에서 정지 가능:** 짧은 시간 계획으로 속도를 제한하거나 controlled stop한다. 구간 감소를 앞서 반영할 수 있지만 과거 양호한 관측으로 현재 unknown을 덮지 않는다.
4. **정지 후 재출발:** far 점 하나로 다시 순항하지 않는다. 명시적인 restart 정책을 고정한다. 보수적 첫 버전은 hard-unknown stop 후 해당 실행에서 자동 재출발하지 않는 방식을 사용할 수 있다. 자동 재출발을 추가한다면 현재의 완전한 입력·전체 공간 qualification·필요한 관측 지속기간을 모두 요구하고, 그 state/reset을 버전으로 고정한다.

`force_brake`를 작은 값으로 바꾸는 것만으로 controlled stop을 구현하지 않는다. normalized brake와 실제 감속의 관계는 현재 보장되지 않는다. 계획 추종 controller가 필요하다면 새 controller 버전과 차량 response 가정·검증을 함께 제공한다. 기존 `proportional-control/1`은 그대로 남긴다.

## 커널과 불변성

`stack-kernel/1`의 `not coverage_qualified → brake=1` 규칙을 제거하지 않는다. 새 observation을 이해하는 `stack-kernel/2`를 추가하고 새 stack schema/revision에서 명시적으로 선택한다.

- 기존 필드가 없는 스택은 역사적인 kernel1을 의미한다. latest로 해석하지 않는다.
- kernel1/component1 구현과 metadata/parameter schema는 이전 요청과 출력의 해시를 유지해야 한다.
- 새 커널은 policy가 보고한 prefix/거리/시간 envelope를 검증하고, controller가 이를 넘어 가속하거나 invalid input으로 진행하는 것을 거절한다. 플러그인 bool 하나를 근거로 사용하지 않는다.
- components는 ID+version으로 공존해야 한다. 역할과 versioned input/output 계약의 호환성을 검사한다.
- stack snapshot, prepared replay, simulation execution context, worker claim, live/replay artifacts에 kernel 및 component identity를 고정한다. 미지원 버전은 dispatch 전에 실패하며 대체 엔진을 선택하지 않는다.
- 기존 replay는 원본 소비 XYZ·시간·순서를 그대로 재사용한다. 새 스택의 replay는 새 evidence이며, 이전 Run이나 실제 궤적·jerk를 수정하지 않는다.
- stateful planning은 초기 state/reset 규칙을 고정한다. 중간 tick seek가 prefix 없이 동일한 상태를 재현한다고 주장하지 않는다. cancellation/interruption은 기존 전체 분모와 attempted/not-attempted를 유지한다.

예상 구현 경계는 새 observation/decision schemas와 별도 v2 perception/policy/kernel 모듈이다. `stack_runtime.py`는 명시 버전 dispatcher로 제한하고, `stack_schemas.py`, `stacks.py`, `stack_replays.py`, `simulation_context.py`, native bridge 및 evidence validators는 새 계약을 선택·핀·검증하는 부분만 확장한다. 기존 v1 schema와 계산 경로에 새로운 기본값을 주입하지 않는다.

## 독립 반례와 인수 기준

새 policy를 작성한 뒤 그 조건만 반복하는 테스트로 끝내지 않는다. 최소한 다음 독립 입력을 먼저 고정한다.

| 반례 | 요구 결과 |
|---|---|
| far 지면이 없지만 근거리 구간 전체가 정해진 조건으로 qualification됨 | 현재·계획 정지 영역이 그 구간 안인지 평가하며 far 전체 구간을 요구하지 않는다. 공간 근거가 부족하면 정지한다. |
| far 점 1개가 corridor 가장자리에만 있음 | 내부/횡방향 unknown을 qualification으로 바꾸지 않는다. 제동·가속을 반복하지 않는다. |
| 내부 지면 공백 뒤 먼 표본이 있음 | 최대 x로 공백을 연결하지 않는다. |
| 가까운 표면이 있고 그 뒤 지면이 없음 | 양성 hazard를 보존하고 표면 앞에서 멈출 수 있는 envelope만 사용한다. 뒤를 free로 채우지 않는다. |
| 같은 위치에 실제 얇고 낮은 물체가 있지만 ray 사이로 빠짐 | 센서/qualification 가정의 미검출 한계를 노출한다. 참조 GT를 planner 입력으로 넣어 통과시키지 않는다. |
| no-return, baseline 구간 40 같은 obstacle-return 소실 | 빈 도로로 취급하지 않는다. 이전 hit 유지가 필요하면 별도 tracker의 불확실성·만료 계약이 필요하다. |
| missing/stale/future/invalid hash/frame/skew/tilt | 즉시 명시적인 unknown 정지, last-good로 대체하지 않는다. |
| 현재 정지거리가 관측 구간을 넘음 | 가속 금지와 emergency fallback. jerk limit으로 이를 지연하지 않는다. |
| 관측 경계·정지거리 equality, float rounding | 정의된 단위·margin·포함 규칙을 따른다. trial 결과에 맞춘 epsilon을 추가하지 않는다. |
| 지면 높이·경사·차량 자세가 가정을 벗어남 | ODD 실패를 표시하며 잘못된 평면으로 공간을 채우지 않는다. |
| 이동하거나 횡방향으로 진입하는 표면 | 단일 프레임을 객체 속도·TTC라고 부르지 않는다. 허용 범위 밖이면 명시적으로 제한한다. |
| 정지 직전·직후와 일시적인 edge return | 안정된 정지/restart 정책을 검증하고 실제 차량 응답도 별도로 측정한다. |
| registry upgrade, queued v1, replay seek/reset, process interruption | 이전 version/hash/bytes와 전체 분모를 보존하며 fallback하지 않는다. |

검증 단계는 다음과 같다.

1. **원본 회귀:** 이 두 실제 close-lead의 160개 v1 출력이 그대로 재현되고 기존 원본 해시가 모두 유지되어야 한다.
2. **공간·시간 반례:** 위 반례를 알고리즘 작성 전 고정하고, point permutation·해당 구간 밖 unrelated point 추가·해시/시간 변조에도 정의된 동작을 유지하는지 확인한다. 격자와 calibration profile을 이 단계에서 고정한다.
3. **새 open-loop replay:** 원본 모든 tick에 대해 새 관측 grid, unknown regions, limiting envelope, 계획·명령을 저장한다. 이는 관측된 v1 궤적을 사용한 대체 결정이며 새 물리 성능 결과가 아니다.
4. **실제 폐루프 인수:** 별도의 새로운 execution ID로 동일 close-lead, 정지/이동 표면, 여러 속도·거리, missing/stale/occlusion 조건을 실제 CARLA에서 실행한다. 원본 sensor→packet→plan→RPC ACK→다음 physics/response를 전체 분모로 보존한다. actor cleanup과 원래 world settings도 확인한다.
5. **승인 판단:** 가까운 표면에서 충돌 없이 정지하고, 앞 구간 근거가 충분한 상황에서 먼 가림만으로 maximum brake를 요청하지 않으며, 진짜 missing/stale/불충분 정지 영역은 계속 정지해야 한다. 기존 감속·jerk 기준과 관측 collision/GT 지표를 그대로 보고한다. 만족하지 못하면 실패를 남기고 sensing/plant/계획 원인을 구분한다.

현 엔진의 seed 진단이 해결되기 전에는 42/43/44를 독립 stochastic realization으로 세지 않는다. RNG 변경 후에도 같은 seed 재현성과 서로 다른 seed의 센서 변화부터 별도로 확인한다. 같은 scenario/seed라도 서로 다른 정책의 폐루프 센서는 시간이 지나며 달라질 수 있으므로, 단일 A/B 결과를 원인 효과나 상용 안전성으로 일반화하지 않는다.

이번 문서는 제안과 인수 계획만 추가한다. production kernel, controller, metric 정의, 원격 엔진과 기존 결과는 수정하지 않는다.
