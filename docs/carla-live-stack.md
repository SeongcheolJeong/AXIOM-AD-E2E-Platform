# 실제 CARLA 센서에서 교체형 스택으로

현재0.6.0은 baseline/dense LiDAR recipe를 명시적으로 선택하는 기능과 기존0.5 원본 회수를 제공합니다. 새 실제 비교의 cruise 통과, 가까운 선행차 실패, seed 적용 한계는 [설정 선택·인수 결과](carla-sensor-profiles.md)를 확인하세요. 아래0.4.1 결과는 당시의 기록입니다.

Adapter0.4.1의 제한된 실제 연결 인수를 완료했습니다. 차량 생성/physics 설정 순서의 유실을 수정하고 원래 차량 응답을 추가했습니다. 새 두4초 실행은 각80명령/81관측과 원본 MCAP 재계산80tick이 일치했고 두 주행의 comfort 판정은 failed입니다. 상세 범위는 [현재 상태](status.md)와 [0.4.1 실제 근거](validation/carla-vehicle-response-2026-09-13.json)를 따릅니다. [이전0.4.0 인수](validation/carla-live-stack-2026-09-13.json)의 원본·지표·판정은 보존합니다. 기존 CARLA 기본 제어, 실제 센서 기반 제어, 기록 재생은 서로 다른 실행 범위입니다.

| 실행 선택 | 입력과 출력 |
| --- | --- |
| 기본 CARLA 제어 · `control` 생략 | 주변 actor 상태를 읽는 기존 제어기. 시작·중간·끝의 RGB/LiDAR capture는 제어 입력이 아님 |
| CARLA live stack · `control.mode=stack` | 실제 ray-cast LiDAR와 simulator ego/map oracle → 저장한 관측·정책·제어 모듈 → 차량 명령 → 다음 physics frame. 이 모드는 RGB를 생성하거나 소비하지 않음 |
| Component stacks 기록 재생 | 저장된 MCAP ego/LiDAR → 같은 모듈 계산. 명령은 `not_applied_open_loop`이며 차량이나 이후 입력을 바꾸지 않음 |

Live stack의 주변 차량 정보는 선행차 시나리오 동작과 TTC·궤적 observer에만 사용합니다. 스택이 받는 자차 속도·자세는 simulator snapshot이고 경로 방향은 지도 oracle입니다. 위치추정이나 차선 인지 알고리즘이 계산한 결과로 표시하지 않습니다. 최초 지원 모듈은 점군의 제한된 평지 기하 관측이며 semantic detection·추적·빈 공간 판단을 입증하지 않습니다.

## 고정할 입력

시나리오는 현재 CARLA 지도에서 가능한 직선 `lane_follow` 또는 `emergency_brake`, 2–4초, 0.05초 간격을 지원합니다. 기본 CARLA 모드의 2–20초 범위는 그대로입니다. 같은 `carla` 큐 자원을 공유하므로 두 제어 방식을 동시에 같은 world에 dispatch하지 않습니다.

저장한 stack ID·revision, rig ID·revision, LiDAR sensor key와 `carla-lidar-ego/1` profile을 선택합니다. 작업은 제출 시 읽은 전체 snapshot·정규화된 파라미터·component metadata·hash를 보존합니다. 대기 중 최신 revision이 바뀌어도 선택을 바꾸지 않습니다. 설치된 component의 version·metadata·해석 결과가 달라지면 실행 전에 거절합니다.

첫 profile은 `sensor.lidar.ray_cast`, 16채널, 32,000points/s, 20Hz, range80m, sensor_tick0입니다. FOV·dropoff·attenuation·noise 등 전체 속성과 scenario에서 고정한 noise seed를 profile에 넣고 실제 blueprint에 설정합니다. 타입별 readback은 정수 또는 float32 표현을 대조하며, 속성이 없거나 값이 다르면 거절합니다. nominal mount는 sensor→ego translation `[0,0,2.2]`, rotation `[0,0,0,1]`입니다. 선택 rig는 `ego` 기준의 `right_handed_z_up`, sensor frame은 `lidar_roof`여야 하며 이 mount와 정확히 일치해야 합니다.

Rig는 출처가 선언된 장착 정보입니다. 높이2.2m에서 지면 높이를 추정하지 않습니다. 기본 geometric component의 `ground_reference_z_m=null`은 지원되는 unknown 조건이며 정지를 출력합니다. 지면을 지정하는 pilot은 평지 가정과 출처를 명시해야 합니다. 필요한 정지거리보다 관측된 ground support가 짧거나 관측이 부족하면 정지를 유지합니다.

## API·SDK·CLI

UI와 SDK는 같은 `POST /api/v1/jobs`를 사용합니다. 다음 JSON은 별도 `control.json` 파일로 저장할 수 있는 선택 형식입니다. ID는 실제 저장한 값으로 지정합니다.

```json
{
  "mode": "stack",
  "stack_id": "stack_saved",
  "stack_revision": 1,
  "rig_id": "rig_saved",
  "rig_revision": 1,
  "lidar_sensor_key": "roof",
  "sensor_profile_id": "carla-lidar-ego/1"
}
```

```python
import json
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    control = json.load(open("control.json"))
    job = api.submit_job(
        "scn_saved", adapter_id="carla", control=control,
        idempotency_key="my-live-stack-trial-001",
    )
    job = api.wait_job(job["id"], timeout=240)
    print(job["status"], job.get("run_id"))
```

```bash
axiom submit-job scn_saved --adapter carla --control control.json --idempotency-key my-live-stack-trial-001
axiom wait-job job_returned --timeout 240
```

`run(..., control=...)`와 CLI `run --control`도 같은 선택을 받을 수 있습니다. 제출 응답이 유실되면 **같은 endpoint·key·본문**으로 복구합니다. 스택이나 rig revision을 바꾼 요청은 다른 실행 의도이므로 새 키를 사용합니다. `wait` timeout은 관찰 중단이며 취소·실행 재시도를 뜻하지 않습니다.

연결한 전용 CARLA에서 두 스택 개정의 실제 실행과 동일 원본 재계산을 수행하는 예제입니다. 새 rig·stack·scenario·jobs를 생성하고 simulator에 명령을 적용합니다.

```bash
.venv/bin/python examples/carla_stack_workflow.py --output .runtime/carla-live-example
```

예제는 실제 제출 전에 요청을 파일에 저장하며, 실패한 작업이나 부분 artifact도 남깁니다. 자동 재실행이나 resource reconcile은 하지 않습니다. 2m/s·20m gap·4초, 명시적인 평지와15m 관측 구간에서 headway1.5/8초를 비교합니다. 판정 통과를 강제하지 않습니다. 각 run의 원본 MCAP를 recording으로 등록하고 같은 rig·stack으로 재생하여, 계산 소요 시간을 제외한 출력과 실제 소비한 전체 점군 bytes가 일치하는지 검사합니다. 재계산 자체는 차량을 움직이지 않습니다.

## 입력에서 다음 프레임까지의 근거

초기화는0.3의 bounded rest gate를 유지합니다. 0.4.1은 모든 차량을 만든 뒤 두 차량의 physics를 적용하고, 정착 후 네 wheel의 실제 tire scale이 요청 float32와 같은지 검사합니다. 후속 spawn으로 앞선 설정이 유실되는 문제를 막기 위한 순서와 gate이며 불일치·읽기 실패 시 초기 속도를 부여하지 않습니다. 정착 판정은 초기 목표 속도를 부여하기 전의 상태이며, 부여 이후의 wheel/속도 수렴은 별도입니다. 측정 구간에는 N개의 제어 전이와 N+1개의 physics 관측이 있습니다.

1. Frame f의 coherent ego snapshot과 같은 frame의 LiDAR callback을 확보합니다. 원래 double seconds와 변환한 integer ns, frame ID를 함께 보존합니다.
2. 원본 XYZI, RH sensor message, rig를 적용한 전체 ego XYZ를 별도로 해시합니다. 지원 한도를 넘는 source를 조용히 줄이지 않습니다.
3. 기존 `StackSession`에 packet과 exact XYZ bytes를 넣고 관측·판단·명령을 기록합니다. `steer`는 positive-left에서 CARLA 부호로 변환합니다.
4. 동기식 batch의 단일 actor 응답을 확인합니다. 일반 `apply_control()`은 비동기 송신이므로 반환만으로 ACK를 표시하지 않습니다. Batch는 tick을 자동 실행하지 않습니다. [CARLA 0.9.16의 비동기 제어](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Client.cpp#L470)와 [동기 batch 구현](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Client.cpp#L687)을 기준으로 구분합니다.
5. 정확히 한 번 tick하고 frame f+1의 physics와 control readback을 기록합니다. 수신 응답과 실제 움직임은 다른 근거이며, 목표 actuator 성능 달성은 별도 평가입니다.

Run explorer는 execution context와 trace/manifest/점군의 SHA를 확인하고 이 연결을 표시합니다. 원본 자료는 다음 artifact로도 다운로드할 수 있습니다.

| Artifact | 내용 |
| --- | --- |
| `carla-startup.json` | 측정 전 정착 표본·정책·spawn·판정 |
| `carla-vehicle-response.json` · 0.4.1 | 정착 후 설정 readback·비동기 초기 속도 요청·frame별 원래 XYZ 속도/가속도·별도 bracket의 control/telemetry |
| `carla-stack-trace.json` | 전체 제어 전이 분모, 원본 참조, packet/output, 요청·응답·다음 frame |
| `carla-stack-sources.mcap` | 실제 보존한 모든 LiDAR/ego message, 별도 clock와 ordinal |
| `carla-stack-raw-xyzi.bin` | CARLA 원본 XYZI bytes, trace의 offset/size/SHA로 조회 |
| `carla-stack-points.bin` | 스택에 소비한 전체 ego XYZ, 별도 offset/size/SHA |
| `carla-stack-physics.json` | coherent simulator 관측과 trajectory, 주변 GT는 observer 전용 |
| `carla-stack-manifest.json` | 고정 context·실제 엔진·센서 readback·전체 분모·실패/정리·artifact hashes |

Run explorer의 차량 응답 view는 artifact 크기·SHA·실행 문맥과 frame 연결을 검사한 뒤 원래 3D 속도, signed horizontal-heading speed, vz와 가속도를 나눠 표시합니다. 측정 시작과 초기 속도 요청을 구분하고 원래 `get_acceleration()` 값과 기존 속도 차분 metric을 바꾸어 쓰지 않습니다. NaN/Inf·누락은 명시적인 상태와 gap으로 유지합니다. 초기 속도 setter는 비동기 요청이며, control readback·동기 ACK도 실제 force나 목표 성능 달성의 증명은 아닙니다. [정확한 응답 의미](carla-vehicle-response.md).

실제0.4.1 r1은 headway1.5초에서7.724732m·최대 감속0.606410·jerk11.362139로 **failed**입니다. 관측 부족 정지는 명령63(3.15초)이며 응답64(3.20초)에서 peak jerk가 발생합니다. r2(headway8초)는 unknown_stop80회·0.564826m·감속8.633477·jerk170.319628로 **failed**입니다. 각각 같은 원본 재계산80tick은 일치하지만 두 실행 전체의 입력은 같지 않습니다. 임계값이나 측정 표본을 바꾸어 통과시키지 않았습니다.

## 실패와 운영 범위

명령 이전에 source와 다음 관측을 보존할 예산을 검사합니다. 개별 artifact20MiB·합32MiB·HTTP 응답48MiB를 넘기지 않습니다. 부분 실패는 확보한 원본 prefix와 `not_sent`, `send_outcome_unknown`, `send_rejected`, `acknowledged_without_next_observation`, `next_frame_observed`를 구분합니다. N개 예정 전이에서 미시도 항목을 없애지 않습니다. error Run에 완전한 성능 지표를 만들어 넣지 않습니다.

플랫폼과 worker는 adapter0.4.1이어야 합니다. 이전0.4.0 run에 vehicle-response가 없는 것은 당시 형식이며 새 artifact를 만들어 덧붙이지 않습니다. 원격 preflight와 실행 응답은 version·input/context hash를 확인하고, 실제 실행 응답에는 예약한 run ID도 대조합니다. 다른 실행의 성공·실패 artifact는 채택하지 않습니다. Native wheel과 서버 build 일치도 계속 검사합니다.

현재 remote 취소는 요청 기록이며 실제 중단 확인을 제공하지 않습니다. 연결 단절·실행 오류·lease 상실로 외부 결과가 불명하면 같은 자원을 격리하고, 이전 dispatcher와 world 정리를 확인한 뒤 운영자가 reconcile해야 합니다. 원격 프로세스 사망 후 영속 결과 회수, 여러 API DB의 분산 소유권, 임의 rig/지도, 정밀 차량·센서 상관성과 상용 운영 gate는 후속입니다. [연결 운영](carla-connection.md) · [큐의 정확한 상태](jobs-contract.md).
