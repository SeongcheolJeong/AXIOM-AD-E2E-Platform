# Shared stack / recorded replay / optional live control contract

구현 갱신: 2026-09-13. API prefix는 `/api/v1`, 공통 인증 정책을 사용합니다. Schema 8은 stack tables를 추가하고 기존 single-host SQLite queue 부모 테이블을 재구성합니다. 업그레이드 전에 DB와 관리 파일을 함께 백업하고 동일 버전 API·worker를 사용합니다. 이 계약은 ASAM/ROS 자동차 제어 표준 구현이나 주행 안전 인증이 아닙니다.

## 고정 stack definition

`StackCreate`는 `name`, `description`(기본 빈 문자열), 필수 nonempty `note`, `definition`을 받습니다. `definition`은 다음 세 역할과 공통 reference frame을 갖습니다.

```json
{
  "schema_version": "1",
  "reference_frame": "ego",
  "perception": {"id": "lidar-corridor", "version": "1", "parameters": {"ground_reference_z_m": null, "ground_reference_source": ""}},
  "policy": {"id": "stopping-headway", "version": "1", "parameters": {"allow_no_obstacle_observed_motion": false}},
  "controller": {"id": "proportional-control", "version": "1", "parameters": {}}
}
```

`GET /stack-components`는 metadata 배열을 반환합니다. 각 항목에는 `id/name/version/role/status`, `parameter_schema`, `default_parameters`, `implementation`, `limitations`가 있습니다. Editor와 agent는 실제 metadata를 조회하고 parameters를 작성합니다. 등록되지 않은 ID/version, 잘못된 역할·status·parameter는 거절합니다.

저장된 snapshot은 definition·검토 note·ID/revision/created_at·nested definition과 일치하는 `kernel_version`·실제 component metadata 세 개를 포함합니다. Outer snapshot의 `schema_version`은 두 kernel 모두 `"1"`입니다. `StackDetail`은 `manifest_hash`, `manifest_size_bytes`, `download_url`을 추가합니다. Revision은 immutable이며 256 KiB canonical manifest 상한을 적용합니다. 새 revision은 `expected_revision`이 현재 head와 같아야 합니다. 충돌은 409이며 기존 revision을 수정하지 않습니다.

`POST /stacks`와 새 revision 작성 시 각 `validate_parameters()`의 **전체 normalized 결과**를 `definition`에 저장합니다. Metadata의 advertised defaults를 단순 복사하거나 생략된 `{}`를 그대로 저장하지 않습니다. 명시적인 `null` ground도 포함하며, 완전한 저장 parameters를 다시 검증했을 때 달라지는 validator 또는 authoring 도중 metadata drift는 422로 거절합니다. 기존 revision의 GET/export는 현재 registry로 재정규화하지 않고 당시의 저장 bytes를 유지합니다.

| Method / route | 결과 |
| --- | --- |
| `GET /stack-components` | 사용 가능한 component metadata 배열 |
| `POST /stacks` | 201, revision 1의 `StackDetail` |
| `GET /stacks` | 현재 stack detail 배열 |
| `GET /stacks/{id}?revision=N` | 지정 revision, 생략 시 현재 head |
| `POST /stacks/{id}/revisions` | `StackCreate + expected_revision`, 201 새 revision |
| `GET /stacks/{id}/revisions` | 기존 revision 목록 |
| `GET /stacks/{id}/export?revision=N` | 검증된 저장 canonical bytes |

Python plugin entrypoint는 `ade2e.stack_components`입니다. `StackRegistry.register(component, replace=False)`로 명시적 교체가 가능하며 metadata/version·parameter 검증을 유지합니다. 역할별 public Protocol은 `PerceptionComponent.observe`, `PolicyComponent.plan`, `ControllerComponent.control`입니다. `StackSession(definition, registry).step(packet, points_bytes)`는 CARLA import 없이 동작합니다. 각 stage 실행 후 metadata를 다시 확인하므로 step 중 발생한 drift도 실행을 거절합니다. 한 component가 packet/parameters를 수정해 다음 stage 입력을 바꾸지 못하도록 경계를 복사합니다.

## 명시적 kernel 2 definition과 compatibility

같은 endpoint에서 다음 **nested definition**을 선택합니다. 생략한 parameter는 해당 버전 validator의 전체 normalized 값으로 저장됩니다. 이 기본 선언은 ground와 brake response가 unknown이며 주행을 허용하지 않습니다.

```json
{
  "schema_version": "2",
  "kernel_version": "stack-kernel/2",
  "reference_frame": "ego",
  "perception": {"id": "lidar-corridor", "version": "2", "parameters": {}},
  "policy": {"id": "stopping-headway", "version": "2", "parameters": {}},
  "controller": {"id": "proportional-control", "version": "2", "parameters": {}}
}
```

Registry key는 정확한 `(id, version)`이고 role·status·kernel도 검증합니다. Metadata의 `implementation.kernel` 생략은 legacy kernel/1이며 kernel/2는 명시적 `"stack-kernel/2"`만 호환됩니다. 서로 다른 kernel의 component를 섞거나 reference frame을 ego 외로 설정하면 거절합니다. 현재 v2 확장도 고정 grid geometry와 stopping envelope의 안전 경계를 따라야 하며 임의 출력 shape를 허용하는 plugin ABI가 아닙니다.

Legacy definition·manifest·tick은 자동 업그레이드하지 않습니다. 저장 v1 output의 생략 optional key를 `null`로 추가하는 것도 원본 tick hash를 바꾸므로 금지합니다. 저장 dict의 검증과 versioned OpenAPI 표시는 수행하되 원본 key·값·canonical bytes를 보존합니다. 새 schema2는 명시적으로 새 revision에만 들어갑니다.

## 원본 recording과 시간 선택

`GET /stack-replay-sources/{recording_id}`는 지원되는 `lidar`/`ego` 채널과 decoder ID/version, schema hash, message count를 반환합니다. Topic 이름이나 source label로 지원을 추정하지 않습니다.

현재 ego decoder는 `axiom-ego-json@1`이며 다음만 허용합니다.

- Channel message encoding `json`, schema encoding `jsonschema`, 이름 `axiom.EgoState.v1`.
- 완전하고 hash가 일치하는 schema 정의. 문서용 `$schema/title/description`을 제외한 구조가 공개 `EGO_SCHEMA`와 일치해야 합니다.
- Payload 필수 필드: `timestamp_ns`, `frame_id`, `speed_mps`, `route_heading_error_rad`, `pitch_rad`, `roll_rad`, `source`. 추가 필드·중복 key·nonfinite JSON·숫자로 가장한 문자열/boolean은 거절합니다.
- `timestamp_ns`는 canonical uint64 decimal string, speed는 0..100m/s, 각도는 ±π의 JSON number입니다. Source 문자열은 선언일 뿐 독립적인 provenance 확인이 아닙니다. Payload 상한은 64 KiB입니다.

LiDAR는 pin된 recording binding의 지원 decoder를 사용합니다. Rig reference frame이 stack과 같아야 하고, RH Z-up convention·sensor frame ID·명시된 sensor-to-rig transform이 필요합니다. Native decoder 결과의 **전체 유효 점**을 변환하며 20,000점 초과, invalid/drop/skipped/stride>1 또는 frame 불일치는 unavailable입니다. Display preview의 일부 점으로 대체하지 않습니다. Point bytes는 float32 little-endian XYZ이며 `size = count × 12`; 좌표의 각 축은 finite ±1,000,000m로 제한합니다.

제출 요청은 다음 필드를 요구합니다.

| 필드 | 계약 |
| --- | --- |
| `label`, `stack_id`, `stack_revision` | 이름과 고정 stack revision |
| `recording_id`, `binding_revision` | 원본 recording과 고정 binding revision |
| `lidar_channel_id`, `ego_channel_id` | 서로 다른 명시적 역할 채널 |
| `ego_ordinals` | 중복 없는 1..128개의 file ordinal, 각각 0..99999 |
| `ego_decoder_id/version` | 기본·현재 지원 `axiom-ego-json` / `1` |
| `ego_log_time_offset_ns` | 기본 `"0"`, canonical signed decimal string |

각 ego ordinal의 `log_time + ego offset`은 uint64 범위이며 **요청 순서대로 엄격히 증가**해야 합니다. 이 aligned time이 tick cursor입니다. LiDAR도 binding의 `log_time + offset`을 사용하며, cursor 이하의 최신 메시지를 선택합니다. 동일 시각이면 마지막 file ordinal을 선택합니다. 미래 sample을 가져오거나 누락 frame을 보간하지 않습니다. 원본 capture clock과 aligned log clock의 일치는 추정하지 않습니다.

`StackPacket`은 `sequence/at_ns`, optional `ego/lidar`, `unavailable_reasons`입니다. 두 descriptor의 `timestamp_ns`는 age 검사를 위한 aligned clock, `capture_time_ns`는 원래 payload capture clock입니다. Binding max age와 kernel max age를 모두 적용하며, 별도로 두 aligned timestamp의 절대 차이를 `max_input_skew_ms`(기본 50ms)와 비교합니다. Ego 샘플 선택 자체가 cursor를 정하므로 별도 capture-clock freshness를 입증하지는 않습니다. LiDAR descriptor는 원본 `source_message_sha256`과 변환된 `points_sha256`, count/size/frame을 구분합니다.

## durable 실행과 고정 근거

| Method / route | 결과 |
| --- | --- |
| `POST /stack-replays` | 필수 `Idempotency-Key`, 202 queued detail |
| `GET /stack-replays?status=...&limit=100&before_seq=...` | `{items,next_before_seq}` |
| `GET /stack-replays/{id}` | 전체 선택 입력·tick·분모·events·고정 snapshot |
| `POST /stack-replays/{id}/cancel` | queued 즉시 취소 또는 실행 중 cooperative 요청 |
| `GET /stack-replays/{id}/export` | terminal sealed manifest exact bytes |
| `GET /stack-replays/{id}/ticks/{index}/points` | 저장된 전체 소비 XYZ bytes |

제출 시 원본 recording/hash/index, 역할 채널/schema, rig/binding, 선택 메시지 ordinal/time/hash/size, stack/kernel/component metadata를 고정합니다. 같은 endpoint/key/body의 재제출은 동일 job을 반환하고 다른 body는 충돌합니다. 응답 유실 후 새 key로 자동 재실행하지 않습니다. `stack_replay`는 별도 subject이며 simulation `GET /jobs/{id}`에 섞이지 않습니다.

Worker capability는 `recorded-stack@1`입니다. Claim 전후의 metadata/version 일치와 입력 hash를 확인하며, tick을 계산할 때 실제 원본 bytes와 snapshot을 다시 검증합니다. 선택 tick 모두가 `not_attempted/running/completed/failed` 분모에 남습니다. Missing/stale source의 `unknown_stop`은 **의도된 정지 계산을 완료한 tick**이며 올바른 입력을 관측했다는 뜻은 아닙니다. Component 계약 위반 등 계산 실패는 `failed`로 남습니다.

Tick publication은 lease/token fence 아래 packet sequence/time, component identity, 소비 byte SHA/size/count를 확인합니다. 실제 point bytes는 별도 파일로 저장하고, canonical tick payload와 hash가 이를 참조합니다. Tick payload 상한은 128 KiB, job의 point 파일 합은 128 MiB입니다. Terminal manifest는 32 MiB 상한으로 seal하며 이후 조회는 현재 source를 재계산하지 않습니다.

취소는 완료 tick을 지우지 않고 미실행 분모를 보존합니다. 실행 중 worker 중단/lease expiry는 interruption 근거이며 자동 재시도하지 않습니다. 이 replay는 외부 vehicle에 명령을 보내지 않으며 각 tick의 `control_application`은 항상 `not_applied_open_loop`입니다. [공통 queue lifecycle](jobs-contract.md).

Kernel 2도 queue runtime identity `recorded-stack@1`을 사용하되 prepared payload·stack snapshot·nested definition·각 output의 kernel이 모두 일치해야 합니다. 새 session은 첫 선택 tick부터 전체 prefix를 순서대로 실행합니다. `state_before`는 직전 `state_after`와 같아야 하며 최초 state, sequence/time, latch·plan 연속성을 publication과 read에서 확인합니다. 중간 tick을 임의로 재계산하거나 worker 실패 뒤 state를 복구해 자동 resume하지 않습니다. 취소·실패 이후 tick은 미실행 분모로 남습니다.

## Legacy kernel 1 출력과 판단 경계

`StackOutput`은 kernel version·sequence/at_ns·`observation/decision/control`·`input_points_sha256`·component metadata·warnings·elapsed_ms를 보존합니다. Elapsed time은 계산 wall time이며 실시간 vehicle 응답 성능이 아닙니다.

- Observation은 `obstacle_observed/no_obstacle_observed/unknown`, nearest point와 전방 surface distance, 전체/전방/ground/obstacle count 및 ground bin coverage를 구분합니다. 전방 x>=0의 hit를 보존하고 distance는 `max(0,x-ego_front_x_m)`입니다. Ground reference는 source mount로부터 추정하지 않는 stack parameter로 기본값은 `null`입니다. 값을 선언하려면 nonblank `ground_reference_source`가 필요하며 unknown ground는 정지합니다.
- `supported_distance_m`은 실제 forward ground point 중 가장 먼 x에서 ego front를 뺀 값입니다. 설정한 lookahead나 obstacle 거리로 대체하지 않습니다. 여전히 관측 sample의 extent이며 연속 free-space 증명이 아닙니다.
- Policy는 stationary-surface 가정 아래 `minimum_gap + max(v*headway, v*response_time + v²/(2*assumed_deceleration))`를 사용합니다. 주행 후보 명령은 `v=max(current_speed,target_speed)`에서 이 거리를 다시 계산하여 observed support를 넘으면 정지하고 기존 obstacle 관측을 보존합니다. 상대 속도·물체 ID·실제 TTC를 추정하지 않습니다.
- Unknown/불충분 coverage는 kernel에서 최종 정지를 강제합니다. `no_obstacle_observed`에서 주행하려면 `allow_no_obstacle_observed_motion=true`가 명시되어야 합니다. Coverage는 empty-space 보장이 아닙니다.
- Control은 throttle/brake `[0,1]`, steer `[-1,1]`, **positive left**입니다. Throttle과 brake를 동시에 사용하지 않으며 unknown 정지는 throttle 0 / brake 1 / steer 0입니다. CARLA bridge는 최종 actuator convention을 변환하고 다음 tick 적용·관측 결과를 별도로 기록합니다.

이 구성에는 learned RGB detection, 객체 분류·tracking, 물체 속도 추정, sensor-model 검증, 폐루프 안전성 판정, 실차 actuator calibration이 포함되지 않습니다. 기존 CARLA capture와 authored fixture replay는 서로 다른 근거입니다. [기록 replay 사용 흐름](stacks.md).

## Kernel 2 출력과 판단 경계

`StackOutputV2`는 `kernel_version="stack-kernel/2"`, 공통 identity/point hash/control에 별도 `observation.schema_version="2"`, `decision.schema_version="2"`, `state_before/state_after`를 저장합니다. V1 parser로 해석하지 않습니다.

- `observation`은 `input_valid`, `sampled/invalid`, 전체·forward·ground count를 구분합니다. `positive_surface`는 height-band hit의 count, 실제 최근접 XYZ, `max(0,x-ego_front_x_m)`만 보존하며 물체 ID·속도를 뜻하지 않습니다. Invalid hash/frame/time 입력은 positive evidence를 만들지 못합니다.
- `coverage.sample_model="ground-hit-grid/1"`은 1m longitudinal × 0.8m lateral, 세 band/전체 폭 2.4m, 최소 hit 2개를 고정합니다. 최대 256 cell이며 마지막 row의 잘린 bounds도 명시합니다. 각 cell은 실제 hit count/XYZ extents/state/reasons를 갖고 unknown_regions는 unknown cell과 정확히 일치합니다. Contiguous prefix는 최초 미충족 row에서 멈추며 distant hit가 gap을 채우지 않습니다. `free_space_certified`는 항상 false입니다.
- `decision`은 prefix와 positive surface 중 제한 horizon, reserve 이후 available distance, current/planned stopping bound를 보존합니다. `stopping_bound_model="positive_accel_latency_jerk_release_then_zero_accel_s_curve/1"`은 허용 양의 가속도와 latency, jerk 제한으로 가속을 0까지 해제한 뒤 S-curve 정지를 포함하는 **assumed_uncalibrated** 모델입니다. 미확인 공간이나 실차 brake authority에 대한 보장이 아닙니다.
- `stop_plan`은 초기 가속도 0 가정과 `symmetric-s-curve-stop/1`의 latency/감속 증가/일정 감속/감속 해제 duration·distance를 명시합니다. `state_before/state_after`는 plan, mode, latch reason, 이전 속도/요청 가속도와 마지막 sequence/time을 보존합니다. `applied_dt_s`와 속도 차분 가속도는 첫 관측에서 null일 수 있습니다. Stored nominal plan과 conservative stopping bound를 구분합니다.
- Controller B=`assumed_full_brake_deceleration_mps2`의 기본값은 null이고, 유효한 값에는 `brake_response_source`가 필요합니다. B unknown은 관측 grid를 지우지 않으며 계산한 후보 plan이 진단용으로 남을 수 있습니다. 최종 mode는 unknown_stop, target 0, throttle 0/brake 1입니다. 주행 요청에는 유효한 관측·ODD·current/planned envelope·명시적 미검출 주행 opt-in 등이 함께 필요합니다.
- Latch reset policy는 `new_session_only`입니다. Unknown/emergency fallback에서 부드러운 제동을 위해 정지를 늦추지 않습니다. 화면의 plan cursor는 저장된 근거를 선택할 뿐 session을 진행하거나 재시작하지 않습니다.

Kernel 2 CARLA live는 adapter **0.7.0**에서만 허용하며 이전 adapter의 schema2 입력은 world 변경 전에 거절합니다. Baseline/dense/near recipe의 지원 버전은 `/simulation-sensor-profiles`에서 확인하고 `carla-lidar-ego-near/1`은 명시 선택합니다. 기존 v1 archive는 원래 adapter와 bytes를 유지합니다. 실제 v2 CARLA trial 인수는 아직 대기 중이며 최종 실행 결과는 [status](status.md)를 따릅니다.

## 선택적 CARLA live 실행 · 기존 kernel 1 경로

`POST /jobs`와 호환 `/runs`는 `adapter_id="carla"`에서 optional `control`을 받습니다.
`mode="stack"`, `stack_id/stack_revision`, `rig_id/rig_revision`, `lidar_sensor_key`,
`sensor_profile_id="carla-lidar-ego/1"`을 명시합니다. 생략·null은 기존 GT controller이며,
`/stack-replays`의 기록 선택 계약을 변경하지 않습니다. 두 CARLA 모드는 같은 큐 자원을 공유합니다.

`simulation-control-context/1`은 전체 stack·rig·sensor recipe와 context hash를 고정합니다.
Context 실행에는 `simulation-input/2` hash를 사용하고 기존 context 없는 입력/hash는 유지합니다.
지원 범위는 직선 lane_follow/emergency_brake, 2–4초·20Hz와 명시된 roof mount입니다.
Component/version/recipe 불일치는 world 변경 전에 거절하며 unknown ground는 그대로 정지 계산합니다.

Native 경로는 같은 `StackSession`에 실제 LiDAR·ego/map oracle을 입력하고,
`steer=-stack.steer`의 동기 batch 응답과 다음 physics frame을 구분합니다.
N개 명령·N+1개 관측, 전체 원본 MCAP·소비 XYZ와 부분 실패를 Run artifact에 보존합니다.
RGB는 생성·소비하지 않으며 주변 GT는 lead script와 observer에만 사용합니다.
정확한 trace·원격 correlation·실패 의미는 [live 계약 안내](carla-live-stack.md),
전체 호출은 [실행 예제](../examples/carla_stack_workflow.py), 실제 인수 결과는 [status](status.md)를 따릅니다.
