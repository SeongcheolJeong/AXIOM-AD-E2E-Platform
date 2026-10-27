# CARLA 연결 · 실험적 adapter 0.7.0

현재 0.7.0은 기존 GT controller·kernel/1을 유지하면서, 명시적 kernel/2와 별도 near-field LiDAR profile을 제공합니다. 실제 near/kernel1 cruise·near/kernel2 cruise·near/kernel2 close-lead 3건은 각80명령/81관측을 저장하며 completed였고, 주행 판정은 모두 failed입니다. Kernel/2 두 실행은 각각80구간 모두 emergency_stop이었으며, 범퍼 기준 positive surface 거리0 때문에 S-curve plan이 생성되지 않았습니다. **S-curve의 실제 차량 추종과 주행 품질 개선은 아직 인수되지 않았습니다.** Near sample 증가도 free-space 증명이 아니며 자차 반환이라는 원인 후보는 미확인입니다. [현재 상태](../../docs/status.md) · [0.7 통합 인수 보고서](../../docs/validation/stack-v2-2026-09-13.json).

0.5.0에서 도입한 실행 ID·원격 저장소·명령/센서 journal·부분 checkpoint·완료 archive도 유지합니다. 네트워크 응답 유실이나 worker 종료 뒤 동일 실행의 근거를 회수하며, 과거 실패 Run을 수정하거나 차량 명령을 재실행하지 않습니다. 실제 0.7 reader에서 과거0.5·0.6 archive4개와 artifact33개의 원본 bytes를 확인했습니다. UI/API/SDK의 회수와 별도 engine cleanup 절차는 [복구 안내](../../docs/carla-recovery.md)를 따릅니다. 아래0.2–0.4.1 결과는 기존 연결·차량 제어·설정 인수 기록입니다.

**이전0.2.0의 첫 연결 인수(2026-09-13)에서는 원격 Linux의 실제 CARLA `1ae5356`과 연결해 4초 주행 2건, 각 RGB/LiDAR 3쌍, MCAP 저장·재생, 기록 RGB의 YOLOX 추론을 확인했습니다.** 두 주행의 감속·jerk 기준은 실패했으며 인지 정확도·센서 물리·폐루프 검증 완료가 아닙니다. [실제 실행 근거](../../docs/validation/carla-remote-2026-09-13.json) · [이 workspace의 연결 운영](../../docs/carla-connection.md). 기본 참조 실행은 CARLA 결과가 아닙니다.

후속0.3.0은 초기 정착 gate·실패 진단 파일·엄격한 versioned 응답·초기화 그래프를 추가했습니다. 실제 lane-follow4/6m/s와 emergency-brake 총3건에서23tick 뒤 정착 조건을 만족했고, 실행·각RGB/LiDAR3쌍 저장·actor 정리를 확인했습니다. 세 주행 판정은 모두 failed이며 정착 통과를 주행 성능 통과로 해석하지 않습니다. 전체Python1171개와Chrome16개를 통과했습니다. [0.3.0 실제 인수와 남은 제어 진동](../../docs/validation/carla-startup-acceptance-2026-09-13.json).

0.4.0에서 **선택적 LiDAR shared stack 제어**를 연결했고 현재0.7.0에서도 기본 GT controller를 유지합니다. 위 과거 capture·정착 인수를 새 제어 경로의 검증으로 대체하지 않습니다. Live 실행의 범위는 [CARLA live stack 안내](../../docs/carla-live-stack.md), 실제 배포 인수와 남은 문제는 [현재 상태](../../docs/status.md)를 확인합니다.

**0.4.1에서 실제 설정 유실을 수정하고 차량 응답 근거를 추가했습니다.** 먼저 생성한 차량의 tire scale이 후속 차량 생성 뒤 바뀌므로, 두 차량을 모두 만든 후 각각 physics를 적용합니다. rest gate 뒤 실제 네 wheel 값이 요청 float32와 맞는지 두 차량 모두 확인한 다음 초기 속도를 부여합니다. 불일치·읽기 실패는 부분 근거와 함께 실패하며 자동 재적용하지 않습니다. 실제 live 두 실행은 각80명령/81관측·80tick 원본 재계산이 일치했지만 r1/r2 verdict는 모두 failed입니다. 감속·jerk 기준과 모든 측정 표본은 유지했습니다. [0.4.1 실제 인수](../../docs/validation/carla-vehicle-response-2026-09-13.json) · [응답 해석](../../docs/carla-vehicle-response.md).

최종0.4.1 source-b의 기본 GT lane-follow4m/s·4초는 양쪽 설정 gate,81개 표본·RGB/LiDAR3쌍·MCAP를 확인했고 감속0.051685·jerk1.169115로 passed입니다. 이 결과는 GT 제어의 한 조건이며 live 두 개정의 failed 판정과 구분합니다.

macOS의 AXIOM API는 HTTP 어댑터를 사용하고, CARLA Python API는 지원되는 별도 worker에서만 import합니다. 따라서 UI·DB용 환경에 libcarla를 억지로 설치할 필요가 없습니다.

```text
Browser / SDK → AXIOM API + durable job DB → execution worker
                                              → HTTP CARLA adapter → CARLA worker → CARLA server
                                              ← trajectory + events + mode-specific source/trace artifacts
Run MCAP artifact → recording import → frozen origin → sensor replay / pinned rig binding
```

## 기본 GT controller의 지원 범위

- 현재 지도와 일치하는 `map_name`, 2–20초/0.05초 단위의 `lane_follow`, `emergency_brake`.
- 분기·교차로가 없는 충분한 길이의 직선 도로를 선택합니다. 지원 도로가 없으면 422로 거절합니다.
- 자차·선행차와 객체 정보를 직접 사용하는 단순 제어기, CARLA 충돌 센서, 고정 간격 동기 tick.
- 기본 480×270 RGB PNG와 16-channel ray-cast LiDAR를 시작·중간·끝에서 캡처합니다. 모든 tick에서 센서 frame ID를 맞추고, 일치하지 않으면 실패합니다.
- 개별 PNG·원래 CARLA 좌표의 LiDAR JSON과 함께 `carla-sensors.mcap`를 생성합니다. 표준 Foxglove JSON CompressedImage/PointCloud에 실제 포착한 frame bytes를 넣으며, 중간 누락 frame을 생성하거나 보간하지 않습니다.
- 센서 출력·timestamp·server version·지도·scenario revision·seed·timestep·좌표 변환·선언된 정적 mount를 `carla-capture-manifest.json` schema version 2에 기록합니다.
- 실행에서 만든 actor만 정리하고 원래 world 설정·날씨를 복구합니다. 지도를 자동 reload하거나 다른 차량을 삭제하지 않습니다.

끼어들기/보행자, OpenSCENARIO import, live 제어 내 학습형 인지 모델, ROS 2 폐루프, RAW·ISP·실측 센서 모델, 규제 인증과 분산 자원 소유권은 아직 없습니다. 기본 GT controller는 캡처한 RGB/LiDAR를 제어 입력으로 사용하지 않습니다.

## 선택적 live stack

`control.mode="stack"`은 고정 stack·rig revision과 명시적인 센서 profile을 선택합니다. 기본 `carla-lidar-ego/1`, 선택적 dense/near recipe의 지원 버전·범위는 [센서 설정 안내](../../docs/carla-sensor-profiles.md)를 따릅니다. Kernel/2와 near는 adapter0.7.0이 필요하며 기존 선택을 자동으로 교체하지 않습니다. 현재 직선 lane_follow/emergency_brake의 2–4초·20Hz를 지원하며 기본 모드와 같은 `carla` 큐 자원을 사용합니다. 실제 LiDAR와 simulator ego/map oracle을 같은 `StackSession`으로 계산하고, 동기 batch ACK와 다음 physics frame을 따로 저장합니다. 이 모드는 RGB를 생성하거나 소비하지 않습니다.

모든 N+1개 LiDAR/ego 관측은 `carla-stack-sources.mcap`에, N개 명령과 원본/소비 point hash·offset은 trace/manifest에 보존합니다. capture 비활성화, 지원하지 않는 recipe·rig·component는 실행 전에 거절합니다. 부분 실패는 진단 prefix로 남고 완료 지표를 생성하지 않습니다. [고정 입력·artifact·실패 계약](../../docs/carla-live-stack.md)을 따릅니다. [기존 실행 예제](../../examples/carla_stack_workflow.py)는 adapter0.4.0/kernel1 전용이며 현재0.7 실행용으로 표시하지 않습니다. 이 예제는 simulator에 명령을 적용하며 자동 재실행이나 resource reconcile을 하지 않습니다.

0.4.1의 live `carla-vehicle-response.json`은 마지막 rest 상태·초기 속도 요청·측정 snapshot의 원래 위치/XYZ 속도/가속도/각속도·frame과, 별도 frame bracket을 가진 control/telemetry readback을 저장합니다. 제어기 입력과 기존 metric의 3D 속도 정의를 바꾸지 않습니다. UI의 signed heading speed·수직속도·원래 3D 속도를 구분해 원인을 조사할 수 있습니다. 지원되지 않거나 nonfinite인 telemetry는 값0으로 대체하지 않습니다. 원격 force/torque나 road-friction을 실측한 자료가 아닙니다.

## Worker 준비

서버와 Python client는 같은 버전을 사용해야 하며 preflight가 일치 여부를 검사합니다. CARLA의 최신 가이드는 개발 브랜치를 반영하거나 일부 옛 버전을 가리킬 수 있으므로, 선택한 릴리스의 지원표·wheel·지도·라이선스를 함께 고정합니다. 확인 시점의 PyPI에는 **0.9.16 / CPython 3.11·3.12 / Linux x86-64·Windows x86-64** wheel이 있습니다. 다음은 **0.9.16 서버를 별도로 확보한 경우**의 worker 예시이며 호환성 검증 완료 선언이 아닙니다. [공식 설치 가이드](https://carla.readthedocs.io/en/latest/start_quickstart/), [공식 Python 패키지](https://pypi.org/project/carla/).

전용 Linux GPU 호스트에서 저장소와 서버를 준비한 뒤:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e backend -e integrations/carla
.venv/bin/python -m pip install carla==0.9.16
export ADE2E_CARLA_HOST=127.0.0.1
export ADE2E_CARLA_PORT=2000
export ADE2E_CARLA_EXCLUSIVE=1
export ADE2E_CARLA_CAPTURE=1
export ADE2E_CARLA_WORKER_TOKEN='choose-a-private-token'
export ADE2E_CARLA_EXECUTION_STORE="$HOME/.local/share/axiom/carla-executions"
.venv/bin/python -m uvicorn axiom_carla.worker:app --host 127.0.0.1 --port 8100 --workers 1
```

패키지 버전만 같아도 `get_client_version()`과 `get_server_version()`의 실제 빌드가 다를 수 있습니다. 이번 서버는 `1ae5356`이고 공개 PyPI client는 `0.9.16`을 반환해 preflight가 정확히 거절했습니다. 기존 runtime에 동봉된 `PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl`을 독립 worker venv에 설치한 뒤 두 빌드가 `1ae5356`으로 일치했습니다. 버전 검사를 생략하거나 기존 프로젝트의 venv를 변경하지 않았습니다. 서버 배포본에 맞는 wheel의 SHA와 실제 build를 함께 고정하세요.

사용 중인 별도 CARLA 서버를 이 문서 때문에 중단하지 마세요. worker는 **전용의 비어 있는 서버**에서 실험하도록 작성되었습니다. 다른 vehicle/walker/sensor가 있거나 이미 synchronous mode이면 거절합니다. `EXCLUSIVE=1`은 이 운영 전제를 명시적으로 설정하는 값이며 다른 호스트와 분산 잠금을 제공하지는 않습니다.

원격 worker를 사용하면 기존 SSH 터널이나 인증된 사설 네트워크를 사용합니다. 이 저장소는 방화벽을 열거나 GPU 인프라를 자동 구매하지 않습니다. HTTP worker의 token과 실제 서버 주소를 로그·프롬프트에 노출하지 않습니다.

## 플랫폼 연결

플랫폼 API를 시작하기 전에 worker 주소를 설정합니다. 아래 localhost는 worker가 같은 호스트에 있거나 해당 포트로 터널을 연결한 경우입니다.

```bash
export ADE2E_CARLA_WORKER_URL=http://127.0.0.1:8100
export ADE2E_CARLA_WORKER_TOKEN='the-same-private-token'
.venv/bin/python scripts/dev.py
```

현재 workspace의 launcher는 명시적으로 저장한 `.runtime/carla-connection.json`도 읽습니다. `schema_version:1`, `worker_url`, `.runtime` 안의 상대 `token_file`만 허용하며 shell 파일을 실행하지 않습니다. 환경 변수에 `ADE2E_CARLA_WORKER_URL`이 있으면 저장된 설정과 token을 모두 건너뜁니다. 따라서 다른 URL로 바꿀 때 기존 worker token을 따라 보내지 않습니다. 연결 정보·token은 Git에 포함하지 않습니다.

Integrations의 `available`은 **호출 설정됨**을 뜻합니다. 실제 readiness는 매 실행 전 `/health`로 확인하고, live stack은 고정 context의 `/v2/preflight`도 검사합니다. 미연결/시간 초과/지원하지 않는 시나리오는 실패하며 참조 엔진으로 대체하지 않습니다. adapter `0.7.0`은 이 저장소의 실행·artifact 계약 버전이며 CARLA server의 `0.9.x` 버전과 별개입니다. 플랫폼과 remote worker를 함께 업그레이드해야 합니다. worker가 정확한 `worker_adapter_version`을 보고하지 않거나 버전이 다르면 실행 POST 전에 거절합니다. 새 dispatch는 정확한0.7.0 계약을 요구하고, 과거0.5.0·0.6.0 durable 결과의 조회·회수에는 원래 request/version/store/hash를 사용합니다. 기존0.2–0.6 실행·artifact·hash를 다시 작성하거나 새 버전으로 표시하지 않습니다.

### 초기화와 실패 진단

0.3에서 도입한 `carla-startup-stability/1` 정착 gate를 현재0.7에서도 유지합니다. 같은 world snapshot에서 두 차량의 위치·선속도·수직속도·각속도·가속도·자세 변화를 관측하고, 명시된 모든 정착 조건을 연속5표본 동안 만족한 뒤에 초기 목표 속도를 부여합니다. 최소8tick, 최대100tick/5simulation seconds, wall60초 안에서 확인하며 tick RPC는남은시간과10초 중 작은값으로 제한합니다. 다른 RPC에는 client timeout이 적용됩니다.

정책의 전체 한도, 원래 spawn transform과 복제한 요청값, 모든 초기화 표본·판정 사유·단위 출처를 `carla-startup.json`에 남깁니다. 기본 GT 경로는 두 차량의 정착 후 physics readback과 일치 상태를 이 파일의 `context.physics_configuration`에 보존합니다. Live 경로는 별도 vehicle-response artifact에 같은 gate 근거와 이후 측정 원본을 보존합니다. 측정 구간의 표본을 제거하거나 기존 감속·jerk 기준을 낮추지 않습니다. 정착 통과는 초기 속도 부여 후의 wheel/속도 수렴이나 차량 성능 검증이 아닙니다. 초기화 실패는 명시적인 error Run과 진단 artifact로 남고, 생성한 actor 정리와 원래 설정 복원을 시도합니다. 정리 오류도 별도 진단에 보존합니다.

보존되는 내부 wire payload에서 기본 모드의 성공/실패는 `carla-execution-result/1` / `carla-execution-failure/1`, live context 경로는 각각 `/2` envelope를 사용합니다. 정확한 worker adapter version을 다시 검사하고 live 응답은 execution ID·input/context hash도 대조합니다. health64KiB·실행응답48MiB, identity encoding, artifact100개·개별20MiB·합32MiB로 제한하며 원본 bytes는 검증한 canonical base64로 전달합니다. 손상·누락·버전 불일치는 자동 재시도하지 않습니다. 현재 플랫폼 dispatch는 0.5.0에서 도입한 `/v3/simulate`의 실행 ID·store UUID를 사용하고, 위 payload를 포함한 `carla-execution-archive/1`을 먼저 봉인합니다. 단절 시에는 해당 ID를 inspect하고 검증된 archive만 별도의 회수 Run으로 게시합니다. 응답 유실만으로 성공·정리 완료를 추정하지 않습니다.

기본 실행 경로는 `POST /jobs`입니다. 제출 시 scenario/configuration과 adapter version을 고정하고 execution worker가 별도 process에서 실행합니다. Jobs에서 실제 terminal status와 run ID를 확인합니다. 기존 `POST /runs`도 같은 durable queue를 inline으로 기다리는 호환 경로입니다. 멱등성 키는 endpoint별로 구분하므로 응답 유실 시 **같은 endpoint와 같은 key**로 조회·재시도합니다.

CARLA adapter는 진행 중 실행의 cooperative cancellation을 제공하지 않습니다. 취소 요청이 실제 engine 정지를 뜻하지 않으며, 응답이 돌아오면 관측 결과를 보존합니다. lease 만료의 `interrupted`/uncertain 작업은 자동 재실행하지 않고 같은 adapter resource를 격리합니다. 기존 dispatcher와 외부 engine이 멈추고 world가 정리되었음을 확인한 후 reconcile해야 합니다. 단순 health 성공으로 격리를 해제하지 않습니다. 실패·취소·근거 선택의 정확한 규칙은 [execution jobs](../../docs/execution-jobs.md)를 따릅니다.

Integrations의 `Check connection`은 별도의 읽기 전용 `/api/v1/adapters/carla/health`를 호출해 확인 시각·상태·서버 version·지도 또는 실패 이유를 표시합니다. 연결 확인만으로 실행을 만들거나 센서 정확도를 검증하지 않습니다.

```python
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    scenario = api.create_scenario(
        name="CARLA first straight road capture", category="lane_follow",
        map_name="Town04",  # 실제 worker /health에 반환된 지도명으로 설정
        duration_s=4, seed=42,
        parameters={"ego_speed_mps": 8, "target_speed_mps": 8,
                    "initial_gap_m": 20, "trigger_time_s": 2,
                    "friction": 0.8, "response_delay_s": 0.4},
    )
    job = api.submit_job(scenario["id"], adapter_id="carla", idempotency_key="carla-first-001")
    job = api.wait_job(job["id"], timeout=300)
    if job["status"] != "completed" or not job["run_id"]:
        raise RuntimeError(f"Inspect job {job['id']}: {job['status']}")
    result = api.get_run(job["run_id"])
    print(result["status"], result["provenance"], result["artifacts"])

    mcap = next(item for item in result["artifacts"] if item["media_type"] == "application/mcap")
    recording = api.import_run_recording(result["id"], mcap["id"])
    print(recording["id"], api.recording_origin(recording["id"]))
```

Town04 예시는 지도 다운로드나 현재 서버 지도 변경을 수행하지 않습니다. 시나리오가 지원 직선을 찾지 못하면 시간·속도·간격을 줄이거나 별도 준비한 지도를 선택합니다.

## Capture MCAP → recording → sensor replay

기본 GT 모드에서 `ADE2E_CARLA_CAPTURE=1`의 성공 실행에는 `kind=sensor_recording`, `media_type=application/mcap`인 `carla-sensors.mcap` artifact가 있습니다. 기본 모드의 capture를 끄면 RGB/LiDAR MCAP가 생기지 않습니다. Live stack은 capture가 필수이며 LiDAR/ego의 `carla-stack-sources.mcap`를 사용합니다. 기존 run의 artifact를 가져오는 작업은 engine을 재실행하지 않습니다.

1. `POST /api/v1/runs/{run_id}/recordings`에 `{"artifact_id":"..."}`를 보냅니다. 서버는 완료 run의 입력/출력 근거, artifact 소유 관계, size/SHA-256 및 복사한 MCAP를 확인합니다. 최초 import는 201, 동일 artifact 재요청은 같은 recording을 200으로 반환합니다.
2. `GET /api/v1/recordings/{recording_id}/origin`에서 run/scenario revision/seed/adapter version/input·output hash/fidelity/artifact의 고정 snapshot을 확인합니다. Live run의 `execution_context`와 `execution_context_hash`도 응답의 `snapshot` 아래 보존합니다. recording의 `source_kind`는 자동으로 measured가 되지 않으며 현재 import 정책은 `unknown`입니다. 생성 run과의 연결은 별도의 origin으로 보존합니다.
3. Data recordings의 Sensor replay 또는 `sensor-channels`, `sensor-frame`, `sensor-bundle` API로 실제 image/point payload를 확인합니다. 기본 `foxglove-json@1` decoder를 사용하며 원본과 preview의 hash를 구분합니다.
4. manifest의 mount·intrinsics 선언을 검토한 후 rig revision과 recording binding revision을 명시적으로 만듭니다. origin import는 자동으로 rig를 생성하거나 calibration을 승인하지 않습니다. 필요한 clock offset/max age와 decoder version도 binding에 고정합니다.

기본 capture의 MCAP camera topic은 `/camera/front/image`, frame은 `camera_front_optical`입니다. LiDAR topic은 `/lidar/roof/points`, frame은 `lidar_roof`이며 **MCAP points는 `(x,-y,z)`로 변환한 right-handed local 좌표**입니다. 함께 보관된 원래 LiDAR JSON은 CARLA left-handed local 좌표를 유지하므로 두 표현을 혼동하지 않습니다. Live stack은 원본 XYZI를 별도 binary artifact에 보존하고 `/ego/state` 채널의 정확한 `axiom.EgoState.v1` 메시지를 추가합니다.

Camera/LiDAR의 보고된 episode double seconds를 각각 정수 ns로 반올림하여 MCAP timestamp에 넣습니다. 이는 ns 측정 정밀도를 새로 얻었다는 의미가 아니며 원래 double과 world frame ID는 manifest에 남습니다. run trajectory의 0부터 시작하는 simulation time과 episode clock도 구분해야 합니다. mount는 camera `[1.5,0,1.7]m`, LiDAR `[0,0,2.2]m`의 adapter 선언입니다. 90도 FOV로 계산한 camera K는 nominal pinhole 값이고 distortion은 unknown이므로 실제 calibration으로 사용하지 않습니다.

형식별 한도와 거절 조건은 [sensor format notes](../../docs/sensor-format-notes.md), binding 및 frame 선택 규칙은 [sensor API 계약](../../docs/sensors-contract.md)을 확인합니다.

## 기본 capture의 실제 서버 인수 조건

아래는 기본 GT controller와 RGB/LiDAR capture의 인수 항목입니다. 선택적 live stack의 입력→명령→다음 physics 검증은 [live 안내와 실행 예제](../../docs/carla-live-stack.md)를 따릅니다.

1. client/server 버전과 지도 hash를 고정하고 충분한 직선 구간을 확인합니다.
2. 위 시험에서 실제 궤적·충돌 이벤트·RGB/LiDAR bytes와 capture MCAP가 DB·카탈로그로 돌아오는지 확인합니다.
3. run artifact를 recording으로 import하고 origin hash를 대조합니다. PNG의 색·시야각, 원래 LiDAR/MCAP 변환, frame ID·episode timestamp와 sensor manifest를 확인합니다.
4. 속도·지연을 바꿔 실행 결과와 센서 입력이 실제로 달라지는지 측정합니다.
5. 실패·timeout·sensor 지연에서도 actor 정리와 world 복구를 확인합니다.
6. 인지 검증에는 별도의 모델 버전·calibration·정답·평가 지표를 고정합니다. 기하 stack의 실행 완료를 인지 정확도 평가로 사용하지 않습니다.

seed와 timestep을 기록해도 서로 다른 CARLA/GPU/physics 환경에서 bitwise 동일 결과를 보장하지 않습니다. 현재 replay의 도로 배경은 공통 도식이므로 실제 CARLA 지도 형상으로 해석하지 않습니다. [CARLA 동기 실행 문서](https://carla.readthedocs.io/en/0.9.15/adv_synchrony_timestep/), [센서 형식 문서](https://carla.readthedocs.io/en/0.9.15/ref_sensors/).

`collision_count`는 측정 시간 범위에 속하는 **관측된 callback의 접촉 수**입니다. sensor stop/destroy 동안 도착한 callback까지 수집한 뒤 집계하며, 측정 이전 settling 이벤트는 제외합니다. 그러나 `sensor.stop()`은 unsubscribe이며 진행 중인 callback 스트림의 완료를 보장하는 장벽이 아닙니다. 관측 접촉 0건을 전체 충돌 관측의 완전성으로 해석하면 안 됩니다. 이 한계는 결과 fidelity와 sensor manifest의 `limitations`에도 저장합니다. 임의 sleep으로 완전성을 보장하지 않으며, 실서버에서 마지막 측정 프레임의 callback 전달과 손실 여부를 별도 검증해야 합니다. [CARLA 0.9.16 Stop 구현](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/ServerSideSensor.cpp), [비동기 수신 callback 구현](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/streaming/detail/tcp/Client.cpp).

## 계약 검증

```bash
.venv/bin/python -m pytest integrations/carla/tests -q
```

이 테스트는 HTTP transport·예외·인증·큐 정렬·좌표·PNG/MCAP encoding과 fake world의 shared kernel·ACK·부분 실패 경계를 검사합니다. 원본 MCAP를 importer/decoder와 기존 open-loop replay로 연결하는 시험도 실제 CARLA GPU engine의 기능·센서 출력·물리·복구 검증을 대신하지 않습니다. 현재 실제 인수 근거는 [status](../../docs/status.md)에 기록합니다.
