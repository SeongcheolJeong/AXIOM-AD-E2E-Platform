# Component stacks · 센서 관측에서 제어 명령까지

구현 갱신: 2026-09-13. 고정한 관측·정책·제어 모듈을 기록 replay와 선택적 CARLA live 실행에서 같은 `StackSession`으로 계산합니다. Legacy kernel/1을 유지하며 kernel/2는 명시적으로 선택합니다. 작업 완료는 근거 저장의 완료이며 안전성·주행 성능 통과와 구분합니다.

| 실행 경로 | 입력·명령의 범위 |
| --- | --- |
| 기록 replay | MCAP LiDAR·ego → 명령 저장. `not_applied_open_loop`이며 이후 기록을 바꾸지 않음 |
| 선택적 CARLA live stack | LiDAR·simulator ego/map oracle → 명령 ACK → 다음 physics frame. RGB 입력·캡처 없음 |
| 기본 CARLA controller | `control` 생략. 기존 주변 GT 기반 제어와 시작·중간·끝 RGB/LiDAR capture |

기존 capture에 없는 ego telemetry를 임의로 생성하지 않습니다. Live 실행의 고정 recipe·revision·부분 실패와 [실행 예제](../examples/carla_stack_workflow.py)는 [CARLA live stack 안내](carla-live-stack.md)를 따릅니다. 실제 배포 인수 여부는 [현재 상태](status.md)에서 확인합니다. [정확한 API 계약](stacks-contract.md).

## 모듈 버전을 나란히 설치하기

Registry는 `(component ID, version)`을 키로 사용합니다. 같은 ID의 새 버전을 등록하면 이전 버전도 남고, UI와 `GET /api/v1/stack-components`에 각각 표시됩니다. 저장된 stack revision과 대기 작업은 원래 버전을 계속 사용합니다. 새 버전을 쓰려면 명시적으로 선택하고 stack의 새 revision을 저장합니다. `replace=True`는 정확히 같은 ID/version만 교체하며 다른 버전을 제거하지 않습니다. API와 worker 양쪽에 선택한 버전이 설치되어 있어야 합니다.

Python `registry.get(id)`는 해당 ID에 버전이 하나일 때만 허용합니다. 여러 버전이 있으면 `registry.get(id, version)`을 사용해야 하며 최신 버전이나 등록 순서로 추측하지 않습니다. 저장된 버전이 없거나 unavailable이면 UI의 Publish를 막고 실행 요청도 거절합니다. 원래 저장된 manifest와 export는 유지됩니다.

문서의 두 stack workflow 예제는 `lidar-corridor/1`, `stopping-headway/1`, `proportional-control/1`을 고정합니다. 다른 버전이 함께 설치돼도 자동으로 변경하지 않으며 정확한 항목이 없거나 중복되면 자원을 만들기 전에 중단합니다. `carla_stack_workflow.py`의 기존 CARLA0.4 제한은 그대로입니다. 이것은 모듈 배포·선택 계약이며 새 주행 알고리즘의 인수는 아닙니다. 현재 kernel/1의 `lidar-corridor`에는 기존 파라미터 계약이 적용되므로 다른 관측 의미·파라미터를 도입하려면 별도 kernel 계약도 검토해야 합니다.

## 기록 replay 화면에서 사용하는 순서

1. **Component stacks**에서 사용할 perception/policy/controller의 ID·version·parameters를 검토하고 stack을 저장합니다. 기본 구성은 `lidar-corridor@1`, `stopping-headway@1`, `proportional-control@1`입니다. 이름·설명·검토 note와 함께 각 component validator가 해석한 **전체 normalized parameters**를 definition에 고정합니다. 생략한 기본값과 명시적인 unknown ground(`null`)도 저장되므로 실행할 때 최신 기본값을 다시 선택하지 않습니다.
2. Data recordings에 MCAP를 가져오고 source provenance를 확인합니다. 재생에는 지원되는 LiDAR 채널과 정확한 `axiom.EgoState.v1` ego 채널이 필요합니다. Topic 이름만으로 역할을 추측하지 않습니다.
3. RH ego reference frame, sensor frame, 명시적인 sensor-to-ego transform을 갖는 rig revision을 만들고 recording binding에 decoder version·clock offset·maximum age를 고정합니다. Mount 높이가 ground reference를 자동으로 결정하지 않습니다.
4. Stack revision·recording·binding revision·LiDAR/ego 채널을 선택하고, 실행할 ego 메시지의 **file ordinal**을 시간 순서대로 지정합니다. 각 tick은 해당 ego의 aligned log time에서 최신 과거 LiDAR를 선택합니다. 미래 sample이나 누락 frame은 보간하지 않습니다.
5. 실행하면 durable queue에 제출됩니다. 입력·정책 snapshot은 이후 authoring 변경과 분리됩니다. 완료·부분 실행·취소 상태와 모든 선택 tick의 분모를 확인합니다.
6. Trace에서 사용한 전체 point buffer, 관측 거리, coverage 사유, 정책과 throttle/brake/steer를 함께 확인합니다. PNG나 schematic trajectory가 이 명령의 폐루프 결과인 것처럼 연결하지 않습니다. Stack manifest, replay manifest와 tick별 XYZ bytes를 내려받아 hash를 확인할 수 있습니다.

## Kernel 2를 명시적으로 사용하기

새 stack 화면의 **Stack kernel** 기본값은 `stack-kernel/1`입니다. `stack-kernel/2`를 선택하면 호환되는 `lidar-corridor/2`, `stopping-headway/2`, `proportional-control/2`와 각 버전의 기본 parameters로 draft를 전환합니다. 이전에 입력한 다른 kernel parameters는 이 전환에서 초기화됩니다. 저장된 revision은 바뀌지 않습니다. API는 nested definition에 `schema_version="2"`, `kernel_version="stack-kernel/2"`를 함께 요구하며, 정확한 kernel/ID/version이 설치되어 있지 않으면 Publish·replay·live 제출을 막습니다.

Kernel 2의 grid는 **1m × 0.8m, 폭 2.4m의 세 구간, cell당 ground hit 2개**라는 고정된 실험용 sampling recipe입니다. 기본 범위는 ego front 2.5m부터 전방 x=15m까지이며 모든 cell의 bounds·실제 hit count·XYZ 최소/최대값을 보존합니다. 해칭한 unknown cell은 먼 point로 채우지 않습니다. `qualified_prefix_m`은 bumper부터 세 lateral cell이 모두 qualified인 연속 구간으로, 첫 gap에서 끝납니다. 0은 바로 앞 구간 미확인, `null`은 invalid input입니다. Qualified도 free-space 증명이 아니며 positive height-band surface의 실제 최근접 XYZ와 관측 개수는 별도 표시합니다.

Ground plane과 그 source를 명시해야 유효한 grid를 얻습니다. Controller의 `assumed_full_brake_deceleration_mps2`(B) 기본값도 unknown(`null`)입니다. B가 없어도 유효한 sample과 positive surface는 보존하지만 주행은 `unknown_stop`, target 0, full brake로 막습니다. B를 선언하려면 `brake_response_source`가 필요하며 이는 실차에서 보정한 값으로 승격되지 않습니다. 미검출 시 주행에는 `allow_no_obstacle_observed_motion=true`도 명시해야 합니다.

Trace는 관측 prefix/positive surface, reserve를 뺀 available distance, 현재·예정 stopping bound를 비교합니다. Bound는 허용한 양의 가속도·응답 지연·jerk 제한에 따른 가속 해제를 포함하는 가정 모델입니다. 저장된 nominal S-curve plan은 초기 가속도 0 가정, latency, 감속 증가/유지/감소 phase를 따로 보여줍니다. 속도 차분으로 얻은 관측 가속도, requested acceleration, state before/after와 latch를 구분하며 누락값은 그래프의 gap으로 남깁니다.

Kernel 2는 **stateful**입니다. Replay 실행은 새 session에서 선택한 전체 입력 prefix를 순서대로 계산하며 중간 state부터 재개하지 않습니다. 화면에서 tick을 선택하는 것은 저장된 state를 읽는 동작입니다. Controlled/unknown/emergency stop latch는 session 중 자동으로 풀리지 않고, 새 실행만 초기 state를 만듭니다. Worker interruption이나 실패 뒤 자동 resume하지 않으며 미실행 tick도 분모에 남습니다.

Kernel 2의 CARLA live 제출은 adapter **0.7.0만** 허용합니다. Near-field recipe `carla-lidar-ego-near/1`도 별도로 명시 선택하며 기본 profile을 자동 교체하지 않습니다. Profile 내용·지원 버전은 API catalog에서 확인합니다. 기존 두 Python workflow 예제는 계속 kernel/1을 고정합니다.

실제 0.7.0 배포에서 near profile의 kernel/1 cruise, kernel/2 cruise, kernel/2 close-lead를 각각 4초 실행했습니다. 세 실행 모두 80명령·81관측과 근거 저장은 `completed`였고, 기존 감속·jerk 기준의 주행 판정은 모두 `failed`였습니다. Kernel/2 두 실행은 각각 80구간 모두 `emergency_stop`이었습니다. 유효 height-band point의 범퍼 기준 표면 거리가 모든 구간에서 0으로 계산되어 정지 envelope를 만족하지 못했고, 저장된 S-curve plan은 생성되지 않았습니다. 따라서 **실제 차량의 S-curve 추종·comfort 개선은 아직 인수되지 않았습니다.**

Near profile은 첫 1m 격자에 ground sample을 추가했지만 전체 가시 영역이나 free-space를 증명하지 않습니다. 범퍼 안쪽 positive point가 자차 표면 반환이라는 설명은 확인되지 않은 후보이며, 근거 없이 제외하지 않습니다. 새 0.7 reader에서 과거 0.5·0.6 archive 4개와 artifact 33개의 원본 bytes가 그대로 유지됨도 별도로 확인했습니다. Pure kernel·authored 입력·mocked world·브라우저 시험과 실제 실행을 구분하며, [현재 상태](status.md)와 [통합 인수 보고서](validation/stack-v2-2026-09-13.json)에 세 실행 및 남은 문제를 기록합니다.

## Legacy kernel 1의 관측과 정지 의미

| 관측 | 의미와 기본 동작 |
| --- | --- |
| `obstacle_observed` | 전방 corridor 안에서 선언한 높이 범위의 실제 point를 관측했습니다. 거리와 근거 point를 보존합니다. Coverage가 부족하면 정지를 유지합니다. |
| `no_obstacle_observed` | 유효한 점군에서 해당 기하 조건의 hit를 찾지 못했습니다. 빈 공간이나 장애물 부재의 증명이 아닙니다. |
| `unknown` | 입력 없음, empty scan, stale/future time, 잘못된 frame/transform/hash/크기, decimation 또는 부족한 관측입니다. 최종 명령은 throttle 0 / brake 1입니다. |

Ground point 개수와 전방 거리 구간의 분포는 입력 품질 조건입니다. `coverage_qualified=true`를 안전 확률이나 free-space 인증으로 읽으면 안 됩니다. Ground reference 기본값은 unknown(`null`)이며 정지합니다. 값을 선언하려면 `ground_reference_source`도 명시해야 합니다. `allow_no_obstacle_observed_motion` 기본값은 false입니다. 이를 true로 저장하면 제한된 flat-road pilot 가정 아래 미검출 시 저속 진행을 허용한 것입니다.

각 입력의 최대 age와 별도로 LiDAR·ego 사이 aligned timestamp 차이를 검사합니다(`max_input_skew_ms`, 기본 50ms). 또한 실제 전방 ground point가 도달한 거리인 `supported_distance_m`을 보존하고, 현재 속도와 목표 속도 중 큰 값에서 필요한 정지거리가 이를 넘으면 정지합니다. 설정한 lookahead 거리 자체를 관측된 거리로 취급하지 않습니다.

거리는 ego frame의 전방 표면 x에서 선언된 bumper 위치를 뺀 값이며 0보다 작아지지 않습니다. 이미 bumper plane 안에 들어온 전방 hit도 버리지 않습니다. 현재 구성은 물체 class·ID·속도를 추정하지 않습니다. 정지거리 계산의 감속도·응답시간은 **설정된 가정**이며 측정된 braking performance나 실제 TTC가 아닙니다. 자차 속도와 route heading error도 source가 제공한 선언이며 인지 결과로 승격하지 않습니다.

## 재현 가능한 API 예제

API와 별도 execution worker가 **같은 프로그램 버전과 workspace data directory**를 사용하도록 준비한 후 저장소 루트에서 실행합니다. 기존 workspace를 업그레이드할 때는 다음 순서를 따릅니다.

1. 실행 중인 작업을 정리하고 API·worker를 정상 종료합니다.
2. SQLite backup과 전체 관리 artifact/recording 디렉터리를 일관된 시점으로 함께 백업합니다.
3. 같은 새 버전의 API·worker를 시작해 schema migration을 수행하고, foreign-key 검사와 기존 행·관리 파일 hash 보존을 확인한 뒤 작업을 제출합니다.

Schema 8은 stack 테이블 추가와 기존 queue 부모 테이블 재구성을 포함합니다. 자세한 조건은 [backend의 저장·migration 계약](../backend/README.md)을 따릅니다. 예제는 새 output directory를 요구하고 기존 파일을 덮어쓰지 않습니다.

```bash
.venv/bin/python examples/stack_replay_workflow.py \
  --url http://127.0.0.1:8000 \
  --output .runtime/stack-workflow-example
```

API 인증을 사용하는 환경에서는 기존 `ADE2E_API_TOKEN`을 전달합니다. [로컬 worker 실행](execution-jobs.md), [SDK](../sdk/python/README.md).

이 workflow는 다음을 실제 HTTP API와 queue로 실행합니다.

- 독립적으로 작성한 ego 샘플 8개와 표준 Foxglove point bytes를 MCAP로 가져옵니다. 실제 센서 측정·CARLA·ray-casting·차량 dynamics를 사용하지 않는 authored fixture입니다.
- Roof mount 2.2m, source별 log offset, decoder를 rig/binding revision에 고정합니다. 같은 timestamp의 LiDAR가 두 개 있을 때 마지막 ordinal을 선택하는 것도 확인합니다.
- Stack revision 1의 headway 1.5초를 revision 2에서 3.0초로 바꾸고 동일 입력으로 실행합니다. 특정 tick의 동일한 point hash에서 `approach`와 `obstacle_stop` 명령이 달라지는 것을 확인합니다.
- 과거 LiDAR 없음·empty scan·stale scan은 두 revision 모두 `unknown_stop`입니다. 이후 ego 속도는 출력 명령과 무관한 authored 값으로 남습니다.
- 이전 stack revision의 bytes, 각 replay manifest와 전체 소비 XYZ bytes의 SHA-256을 검증합니다. `summary.json`과 제출 전 저장한 `submit-r*.json`에 근거를 남깁니다.

입력 파일만 만들려면 다음을 사용합니다. stdout의 metadata에 채널 ID·ego ordinals·offset·각 case가 포함됩니다.

```bash
.venv/bin/python examples/generate_stack_recording.py .runtime/authored-stack-input.mcap
```

이 예제 결과는 제한된 알고리즘 인과와 API/저장 계약의 검증입니다. 여러 지도·날씨·occlusion·sensor dropout·실차 braking 성능의 일반화는 남아 있습니다. 상용 목표와 전체 release gate는 [roadmap](roadmap.md)과 [현재 상태](status.md)를 따릅니다.
