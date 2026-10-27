# CARLA shared stack 연결 설계

2026-09-13에 검토한 0.4 설계 기록입니다. 이후 선택적 live stack을 구현했으며 현재 계약은 [사용법](carla-live-stack.md), 실제 인수 결과는 [status](status.md)를 따릅니다. 아래 제안 문구는 설계 당시 논의이며, 기존 GT controller capture와 open-loop replay를 live 실행 근거로 소급하지 않습니다.

## 최소 범위와 입력 계약

기존 `POST /api/v1/jobs` 및 호환 `POST /api/v1/runs`의 simulation 경로를 사용한다. `adapter_id="carla"`, job kind `simulation`을 유지한다. 현재 queue의 독점 자원은 adapter ID이므로 별도 `carla-stack` ID를 만들면 같은 CARLA 서버에 기존 controller와 새 stack이 동시에 접근할 수 있다. 같은 API DB, 단일 전용 CARLA worker 프로세스, 전용 CARLA 서버가 첫 지원 배치다.

`RunCreate`에 선택적 `control: SimulationControlSelection | None`을 추가하는 안을 권장한다. 생략과 `null`은 기존 adapter controller 요청으로 정규화한다. 명시적 stack 요청의 제안 형태는 다음과 같다.

```json
{
  "scenario_id": "scenario-id",
  "adapter_id": "carla",
  "control": {
    "mode": "stack",
    "stack_id": "stack-id",
    "stack_revision": 2,
    "rig_id": "rig-id",
    "rig_revision": 1,
    "lidar_sensor_key": "lidar_roof",
    "sensor_profile_id": "carla-lidar-ego/1"
  }
}
```

모든 revision은 양의 정수이며 `latest`를 허용하지 않는다. 원래 RunCreate의 seed/parameter_overrides 규칙은 유지한다. stack 선택이 있으면 다른 adapter 또는 context hook이 없는 adapter를 422로 거절한다. 처음에는 현재 직선도로 trial에서 2–4초, 20 Hz, 최대 81개 관측을 지원하여 모든 제어 원본을 기존 저장 한도 안에서 보존한다. 이 제한은 0.3 일반 trial의 2–20초 지원을 축소하지 않는다.

`carla-lidar-ego/1`은 첫 배포가 제공하는 고정 센서·좌표 recipe다. 16채널 ray-cast LiDAR, 32,000 points/s, 20 Hz 회전, sensor_tick=0, 80 m 범위와 실제 설정한 나머지 blueprint 속성·mount·clock 변환을 canonical snapshot에 넣는다. 기존 rig revision 중 RH ego frame 및 해당 LiDAR mount와 정확히 일치하는 선언만 받는다. 임의 rig를 골라 실제 mount와 다르게 해석하지 않는다. 리턴 수·dropoff가 달라질 수 있으므로 설정값만으로 유효 점 개수나 완전한 공간 관측을 약속하지 않는다.

`prepare_run`의 짧은 writer transaction에서 stack·rig의 지정 revision과 hash를 검증한 뒤 다음 `PreparedSimulationContext`를 저장한다.

| 필드 | 계약 |
|---|---|
| `schema_version` | `simulation-control-context/1` |
| `mode` | `stack` |
| `stack_snapshot` | 검증한 StackDetail 전체; 이미 저장된 normalized parameters, kernel version, component metadata, manifest hash 포함 |
| `rig_snapshot` | 검증한 지정 RigDetail 전체; source/calibration 선언 상태를 그대로 유지 |
| `lidar_sensor_key` | rig 내부의 정확한 LiDAR key |
| `sensor_profile` | ID/version, blueprint 속성, static mount, frame/clock/좌표 변환 정책의 고정 snapshot 및 hash |
| `ego_source_policy` | `carla-ego-state-and-map-route/1`; 허용 oracle 범위 |
| `context_hash` | 위 필드에서 context_hash 자체만 제외한 canonical SHA-256 |

context와 scenario는 immutable JSON bytes로 PreparedRun에 들어간다. worker는 실행 때 DB의 최신 stack/rig를 읽지 않는다. 처음 지원하는 센서 profile과 다른 rig, frame 또는 stack component metadata는 **엔진 dispatch 전에** 거절한다. `ground_reference_z_m=null`은 지원 불가와 다르다. 그대로 보존하고 kernel의 `unknown_stop`을 실행한다. ground 값이 있으면 기존 계약대로 출처가 필요하며 mount 높이에서 ground를 추정하지 않는다.

## 기존 hash 및 adapter 호환

context가 없는 PreparedRun의 기존 세 필드, request hash, scenario+adapter ID/version input hash를 그대로 사용한다. `RunCreate.model_dump()`에 새 `control:null`이 들어간 것을 기존 request hash에 그대로 넣으면 이전 idempotency key가 충돌하므로, 공통 `run_request_hash()`에서 context 없는 요청은 기존 필드만 canonical화한다. `/jobs`와 `/runs`의 현재 key namespace는 유지한다.

stack 요청에만 `prepared-simulation/2` envelope와 `simulation-input/2` hash를 사용한다. hash 입력은 scenario snapshot, adapter ID/version, context 전체다. JobDetail에는 고정 control 선택과 context hash를 노출하고, 새 RunDetail에는 선택적 typed `execution_context`를 보존한다. 옛 payload·artifact·manifest bytes를 재작성하거나 옛 hash를 새 알고리즘으로 검사하지 않는다. 입력 integrity helper는 해당 버전을 따라 분기한다. 저장 JSON만 확장하는 최소안은 execution_jobs parent table rebuild를 요구하지 않는다.

기존 `SimulationAdapter.execute(Scenario)`는 유지한다. 선택적 typed hook의 제안은 다음과 같다.

```text
validate_execution_context(scenario, context) -> None
execute_with_context(request: SimulationExecutionRequest) -> SimulationResult

SimulationExecutionRequest:
  schema_version = "simulation-execution-request/1"
  execution_id: reserved_run_id
  input_hash: SHA256
  scenario: Scenario
  context: PreparedSimulationContext
```

execution_id는 입력 hash에 포함하지 않는 시도 식별자다. context hook을 기존 execute로 조용히 대체하지 않는다. 로컬 worker는 버전·component·rig validation과 원격 read-only preflight를 `mark_dispatched` 전에 수행한다. 원격 worker도 world 변경 전에 같은 검사를 반복한다. startup 이후 문제를 preflight422로 바꾸지 않는다.

원격 `POST /v2/preflight`와 `POST /v2/simulate`는 위 DTO를 공유하는 제안이다. preflight는 world를 변경하지 않으며 context hash, worker adapter version, kernel version, component metadata identity, 지원 sensor profile, 관측한 client/server build와 map을 응답한다. 실행 응답은 execution_id/input_hash/context_hash를 echo한다. v1 plain Scenario route는 기존 controller를 위한 호환 경로다. backend와 remote adapter를 함께 0.4.0으로 올리고 성공·실패 wire envelope에도 이 식별자를 검증한다. shared kernel의 알고리즘은 변경하지 않으므로 `stack-kernel/1`을 유지한다.

## 실제 tick과 제어의 인과관계

startup 구간은 계속 별도 진단으로 남긴다. 유효한 최초 측정 frame `f0`를 얻은 후 N개의 제어 interval을 실행한다.

1. world snapshot `f_i`에서 ego transform·velocity를 읽고, 같은 frame의 LiDAR callback을 기다린다. 다른 frame·미래 관측을 대신 사용하지 않는다. source clock은 같은 CARLA simulation episode이며 원래 double seconds도 보존한다.
2. 해당 bytes로 원본 MCAP message를 한 번 만들고 SHA를 계산한다. 원본 LiDAR → RH sensor frame → 고정 rig의 ego frame으로 변환한 **전체** XYZ float32 LE buffer를 만든다. 20,000개 초과·비정상 점·크기·좌표/clock 오류는 조용히 줄이지 않고 unavailable 이유로 기록한다.
3. 이 StackPacket과 exact buffer로 같은 `StackSession.step()`을 호출한다. 상태가 있는 component를 매 tick 새로 만들지 않는다.
4. StackControl을 CARLA VehicleControl로 변환하여 전달한다. throttle/brake는 그대로이며 `carla.steer=-stack.steer`다. 적용하려던 명령, RPC 결과, 전달 실패를 각각 보존한다.
5. 명령 전달을 확인한 후 **정확히 한 번** world.tick()을 호출하고 다음 physics snapshot `f_{i+1}`을 기록한다. 실제 control readback, ego 속도·위치·observer 상태가 이 frame에 속한다.

마지막 `f_N`은 결과 관측이며 새 제어 interval을 만들지 않는다. 따라서 완료 run은 N+1개의 실제 physics 관측과 N개의 명령 전이를 가진다. HTTP/RPC ack만으로 차량이 감속했다고 결론 내리지 않는다. CARLA는 한 client만 tick해야 하며 GPU camera는 도착이 지연될 수 있으므로 frame ID를 따른다. [CARLA synchrony 문서](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_synchrony_timestep.md). CARLA 공식 keyboard 예제도 좌회전에 음수 steer를 사용한다. [공식 manual_control.py](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/examples/manual_control.py#L568-L583).

각 `LiveStackTick`의 제안 필드는 `schema_version="carla-live-stack-tick/1"`, sequence, source_world_frame, source_time_s, packet, output, source_refs, points_sha256/size, requested_carla_control, control_acknowledged, next_world_frame, next_time_s, observed_control, next_ego, application_status, error다. `application_status`는 `not_sent | send_outcome_unknown | acknowledged_without_next_observation | next_frame_observed`다. next frame을 보았다는 것은 물리 응답을 관측했다는 뜻이며 요청한 actuator 성능을 달성했다는 판정은 아니다. 실패한 tick도 분모에서 빠지지 않는다.

`source_refs`는 LiDAR/ego 각각 원본 MCAP의 channel, ordinal, message SHA, source timestamp를 가리킨다. LiDAR에는 별도로 raw CARLA XYZI SHA와 변환된 XYZ SHA를 보존한다. 원본 raw bytes, JSON/base64 메시지 bytes, 변환된 XYZ는 다른 표현이므로 같은 SHA라고 표기하지 않는다. exact XYZ는 별도 binary artifact 또는 offset/length index로 보존하고 core가 저장·소비 hash를 대조한다.

## ego·route·sensor 근거의 경계

ego telemetry는 CARLA actor snapshot의 실제 simulation state이며 estimator 결과가 아니다. route heading은 CARLA map/waypoint oracle이다. `ego_source_policy`에는 속도, ego pose/pitch/roll, route heading error만 허용한다. 주변 actor의 위치·velocity·bounding box·semantic LiDAR tag는 stack으로 전달하지 않는다. lead actor의 시나리오 동작과 trajectory/TTC/collision 관찰은 별도 observer 코드가 수행한다.

공간 좌표는 CARLA의 x-forward/y-right/z-up에서 x-forward/y-left/z-up으로 반사한 뒤 고정 sensor-to-ego transform을 적용한다. pose는 변환 행렬로 처리하고 yaw/roll/pitch 부호를 추측하여 복사하지 않는다. heading error도 동일한 RH convention에서 계산한다. ground는 ego 기준의 명시적 flat-road 가정이다. CARLA 차량 원점과 지면이 같다고 간주하지 않는다. source와 nominal calibration 선언을 측정 검증 상태로 격상하지 않는다.

LiDAR가 완전한 callback이라도 물체가 없거나 ground coverage가 부족하면 기존 kernel의 unknown/hold 정책을 따른다. `allow_no_obstacle_observed_motion`은 저장된 stack의 명시적 pilot 가정이다. observed support보다 큰 stopping horizon은 정지를 유지한다. 이는 geometric surface 관측이고 semantic detector·TTC·free-space 검증이 아니다. RGB는 첫 slice에서 선택적 capture이며 제어 입력 목록에는 들어가지 않는다. LiDAR는 필수이므로 전체 sensor capture 비활성화 설정과 stack mode를 함께 허용하지 않는다.

## 원본 MCAP와 Run 보존

현재 RGB/LiDAR MCAP에는 ego 채널이 없다. 이를 추정해서 과거 run에 추가하지 않는다. 새 live run은 모든 제어 tick의 Foxglove PointCloud와 정확한 `axiom.EgoState.v1` JSON 채널을 기록한다. ego 필드는 timestamp_ns, frame_id, speed_mps, route_heading_error_rad, pitch_rad, roll_rad, source이며 기존 stack replay source validator와 같은 schema를 쓴다. 같은 관측의 timestamp/log_time은 `simulation_timestamp_ns()`의 명시적 변환을 사용하고 episode의 원래 double seconds와 world_frame을 manifest에 남긴다. 정수 ns로 변환해도 원본보다 정밀한 시계를 획득한 것은 아니다.

완료 manifest는 고정 context, 실제 client/server build/map, startup 결과, sensor/rig 선언, controller scope, N expected/acknowledged/next-frame-observed counts, trace/MCAP/XYZ artifact SHA 및 크기를 보존한다. 기존 실제 Run trajectory와 다섯 metrics는 유지하며 주변 GT에서 계산한 TTC 등은 observer 근거로 표기한다. fidelity는 `CARLA physics + shared LiDAR geometric stack + ego/map oracle + uncalibrated ray-cast sensor; RGB capture only`를 명시한다. kernel의 계산 완료와 차량·센서 성능의 통과를 같은 판정으로 쓰지 않는다.

성공 MCAP는 기존 run-recording origin import로 가져와 고정 rig binding과 stack replay에 연결할 수 있다. trace의 channel/ordinal/hash를 import 후 검증한다. 녹화 open-loop에서 동일 원본과 stack을 재실행한 계산은 live trace와 비교할 수 있지만 그 replay의 명령은 여전히 `not_applied_open_loop`다. 새 revision으로 실제 물리가 바뀌었는지는 별도 live run으로 확인한다.

중간 실패는 0.3의 `AdapterExecutionError.artifact_payloads` 경로를 재사용한다. 유효한 원본 prefix MCAP, physics prefix, 성공·미확정 tick trace, 실패 phase, 마지막 확인 frame, cleanup 결과를 bounded 진단 artifact로 보존한다. Run은 `error`, 완전 metric 결과는 생성하지 않는다. prefix MCAP는 이미 확보한 message로 finish하여 읽을 수 있게 만들되 실패 전후 빠진 tick을 메우지 않는다. artifact 개수/개별 20 MiB/전체 32 MiB/wire 48 MiB 한도를 모두 검사하며 **source를 저장할 여유를 확보한 뒤 해당 명령을 dispatch**한다. bounded buffer 상한 도달은 진단 가능한 실패다.

현재 origin import는 completed run만 허용한다. 첫 slice의 실패 prefix는 진단 artifact로 조회·다운로드하며, completed로 속여 import하지 않는다. error-origin import를 추가한다면 별도 명시적 partial 상태와 integrity 검증이 필요한 후속 범위다.

## 실패·소유권·비교

현재 heartbeat/fenced publish/lease expiry 정책을 그대로 사용한다. remote timeout·프로세스 사망·결과 식별자 불일치 이후 자동 재실행하지 않는다. 같은 `carla` 자원을 quarantine하고 operator reconcile 전에 다음 controller나 stack을 dispatch하지 않는다. remote lock은 단일 worker 프로세스 배치의 방어이며 별도 API DB 여러 개를 조정하는 전역 lock이라고 주장하지 않는다.

첫 slice는 기존 remote cancellation 의미를 유지해도 된다. dispatch 전 취소는 즉시 취소, 실행 중 취소는 요청 기록이고 응답이 오면 실제 완료/실패 근거를 보존한다. 중단 확인 프로토콜 없이 `cancelled`라고 쓰지 않는다. local lease를 잃은 worker가 remote 결과를 받더라도 기존 Run을 덮어쓰거나 게시하지 않는다. remote execution_id는 상관관계 식별자이며 영속적인 원격 exactly-once 보장으로 표시하지 않는다.

기존 scenario별 최신 evidence는 submission order를 유지한다. 새로운 stack 요청도 실제 simulation attempt로서 오래된 pass를 차단한다. 단, suite campaign에 stack 선택을 확장할 때는 comparison context에 control mode/context hash를 포함해야 한다. 현재 compare_reports는 adapter version/fidelity 등을 검사하지만 stack revision을 독립 조건으로 보지 않는다. 같은 adapter version에서 다른 stack을 무조건 회귀 비교하지 않는다. 최소 slice는 단일 scenario job의 명시적 trace 비교까지이며 기존 고정 campaign 입력에 조용히 새 stack을 삽입하지 않는다.

## 구현 순서 및 인수 조건

1. **0.3 완료 후 compatibility 먼저:** `schemas.py`, `service.py`, `jobs.py`, `job_schemas.py`에 선택적 context와 versioned hash helper를 추가한다. 옛 PreparedRun 복원, 0.2/0.3 idempotency 재요청, export bytes/hash 불변 회귀가 선행한다. 새로운 DTO는 별도 `simulation_context.py`에 두어 suite의 기존 ExecutionContext와 혼동하지 않는다.
2. **submit부터 remote preflight까지:** `adapters.py` optional hook, `worker.py`의 mark_dispatched 전 context 검사, CARLA `remote.py`/`worker.py`/`wire.py`의 v2 DTO·correlation 검사, `version.py`/package 0.4.0을 연결한다. 임의 rig와 설치되지 않은 component가 world를 바꾸기 전에 거절됨을 검사한다.
3. **같은 kernel 실행:** CARLA 전용 `stack_bridge.py`에 ego/points 변환·control mapping을 두고 runner에 observer/controller 분기를 연결한다. `stack_runtime.py`의 알고리즘을 복제하거나 주변 GT 제어를 숨겨 호출하지 않는다. frame f→명령→frame f+1, steer 부호, near-hit, missing/empty/skew/hash 문제, plugin drift를 protocol 시험으로 확인한다.
4. **원본과 trace 저장:** `recording.py`에 모든 LiDAR/ego tick 기록을 추가하고 성공/부분 실패 artifact를 core에서 검증한다. `run_recordings.py`의 성공 origin snapshot에는 새 context hash를 보존한다. 실제 저장 bytes와 trace references, full prefix 실패, wire limit, 늦은 결과·공유 자원 경쟁을 검사한다.
5. **UI/SDK 및 실제 인수:** 고정 stack/rig revision 선택, 실행 scope, actual trajectory와 같은 시간의 source/명령/next-frame trace를 같은 API로 제공한다. 전용 CARLA 서버에서 짧은 실제 run을 수행하고 원본→kernel→명령→다음 physics evidence chain을 확인한다. 같은 bytes의 open-loop 재계산과 실제 제어 전이의 일치를 확인하고, 두 stack revision의 서로 다른 명령 및 각각의 실제 physics 결과를 보존한다. sensor 오류 시험과 authored fixture는 실제 정상 센서 인수와 구분한다.

0.4 첫 인수 후에도 route/localization estimation, semantic perception, 임의 map/rig, 동적 지면 추정, 원격 영속 결과 회수, 분산 resource ownership, safety certification은 완료 범위에 포함되지 않는다.
