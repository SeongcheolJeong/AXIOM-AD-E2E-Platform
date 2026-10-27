# 실제 CARLA 연결 · 2026-09-13

이 workspace는 Mac의 API/DB/worker에서 SSH 터널을 통해 사용자의 Linux GPU PC에 연결합니다. Linux에서는 기존 CARLA 서버와 별도 AXIOM HTTP worker가 실행됩니다. 서버/client build는 **1ae5356**, Python package는 **0.9.16**, 지도는 **Town10HD_Opt**입니다. RTX 2070 SUPER 8GB에서 `-RenderOffScreen -quality-level=Low`로 시험했습니다. Off-screen은 GPU 영상 생성이 유지되는 모드이며 no-rendering과 다릅니다. [공식 0.9.16 렌더링 문서](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_rendering_options.md).

## 현재 선택형 센서 source · adapter 0.6.0

일반 worker는 `sensor-profiles-0.6.0-20260913`이며 기존 엔진과 영속 store를 유지합니다. 82개 source 파일의 archive SHA는 `3145ddf743846e909187e32900be3fffae238373e097d820e02a89b1561260a7`입니다. UI·API·SDK에서 baseline/dense LiDAR recipe를 선택할 수 있고, 기존0.5 완료·부분 archive도 원래 bytes 그대로 읽습니다. 실제8건의 비교에서 cruise는 dense로 jerk 기준을 통과했지만 가까운 정지 선행차는 두 설정 모두 실패했습니다. 서로 다른 요청 seed의 점군이 같아서 RNG 적용은 미확인입니다. [설정·결과·한계](carla-sensor-profiles.md) · [인수 근거](validation/carla-sensor-profiles-2026-09-13.json).

## 앞선 checkpoint 성능 source · 공개 adapter 0.5.0

새 source `checkpoint-performance-0.5.0-20260913`을 일반 Linux worker에 적용했습니다. 같은4초 조건의 계측 wall time은 live55.214→28.362초, GT19.414→13.443초이며 전체 궤적·지표·live 점군과 제어가 baseline과 정확히 같습니다. 일반 큐에서도 GT `fd7f5f7e`는13.180초/passed, live `3afd9887`은28.254초/failed로 완료되어 실제 화면에서 재생·센서/명령 근거를 확인했습니다. Live jerk11.362139 실패를 유지하며 실시간·센서 정확도 인수로 확대하지 않습니다.

새 private checkpoint 형식은 실제 after-ACK SIGKILL 후 원본 CAS bytes와 정확히 같은 공개 부분 결과로 회수됐습니다. 구 source D reader는503을 반환하고 기존 DB를 바꾸지 않았습니다. 완료 archive와 공개 adapter 계약은0.5.0이며, 진행 중인 새 형식의 checkpoint에는 새 reader가 필요합니다. 기존 CARLA 서버·store UUID를 유지했고 원래23개 actor/settings/weather와 소유 actor0을 확인했습니다. 현재56runs·63jobs·189artifacts/schema9이며 이전54runs·61jobs·170artifacts 및 관리 파일567개를 보존했습니다. [성능·호환성 안내](carla-performance.md) · [8건의 실제 인수 기록](validation/carla-checkpoint-performance-2026-09-13.json).

## 앞선 adapter 0.5.0 결과 회수 인수

원격 실행은 같은 영속 store에 요청 identity, journal, 원본 센서 bytes, 일관된 부분 checkpoint, 완료 archive를 보존합니다. Mac이 응답을 받지 못해도 **Execution queue → Inspect remote execution**에서 원래 실행을 조회하고 별도 recovery job으로 결과를 회수할 수 있습니다. 원래 실패 Run과 이력은 유지하며 회수 결과는 출처가 연결된 새 Run에 저장합니다. 회수는 CARLA를 다시 실행하거나 중단된 제어를 재개하지 않습니다. [운영·API 안내](carla-recovery.md).

이번 인수는 독립 native 실행10건과 플랫폼 실행3건으로 구성됩니다. 실제 worker 종료, 명령 응답 전후, physics·센서 저장, cleanup·완료 봉인 경계의 부분 결과를 확인했습니다. 플랫폼에서는 전체 응답 유실 후 완료 결과 회수와 실제 worker SIGKILL 후 부분 진단 회수를 각각 API·DB·브라우저에서 확인했습니다. 부분 회수에는 미실행 구간과 누락 관측을 남기며 통과 지표를 만들지 않습니다.

현재 정규 queue worker로 실행한 GT lane-follow4m/s·gap10m·4초는81관측, RGB3장·LiDAR3개·MCAP를 저장했고 passed입니다. 같은 인수의 완료 live stack 주행은 jerk11.362139m/s³로 failed입니다. 실제 시간은4초 시뮬레이션에 GT 약19초, live 약55초였으며 실시간 성능을 충족했다고 주장하지 않습니다.

DB는 schema9이며 이전48runs/55jobs/139artifacts의 모든 업무 행·열과 관리 파일534개의 SHA를 보존했습니다. 최종 확인 시53runs/60jobs/169artifacts, 활성 작업0, CARLA 자원 idle입니다. 원래23개 actor의 identity·world settings·weather를 보존했고 이 작업이 만든 actor는0개입니다. 현재 source와 각 인수 source의 차이, 결과 및 한계는 [0.5 인수 기록](validation/carla-durable-recovery-2026-09-13.json)에 고정했습니다. 아래 이전 버전 절의 수치·미완료 항목은 해당 버전 당시 기록입니다.

## 이전 adapter 0.4.1에서 확인한 결과

요청 tire friction scale0.8이 먼저 생성한 ego에서3.5로 바뀌는 문제를 실제 readback으로 확인했습니다. 이제 기본 GT와 live stack 모두 차량 두 대를 만든 뒤 physics를 설정하고, rest gate 뒤 두 차량의 네 wheel 값을 검사합니다. 값이 요청 float32와 다르거나 읽지 못하면 초기 속도 부여 전에 실패합니다. scale을 노면의 실측 마찰계수로 해석하지 않습니다.

실제 live stack 두 실행은 각각80명령/81관측과 MCAP80tick 재계산이 일치했습니다. 두 차량의 readback은 모두0.800000011920929입니다. headway1.5초 r1은7.724732m·감속0.606410m/s²·jerk11.362139m/s³, headway8초 r2는0.564826m·8.633477m/s²·170.319628m/s³입니다. **두 verdict는 기존 기준 그대로 failed입니다.** r1의 관측 부족 정지는 명령63(3.15초)이며 응답 관측64(3.20초)에서 peak jerk가 발생합니다. 이전0.4 r1의 마지막 명령79와 혼동하지 않습니다. [현재 실제 run·source·재생 근거](validation/carla-vehicle-response-2026-09-13.json).

최종 source-b의 별도 기본 GT lane-follow(4m/s·gap10m·seed42·4초)는81개 표본·RGB3장·LiDAR3개·MCAP를 보존했고, 양쪽 설정 gate가 matched였습니다. 감속0.051685m/s²·jerk1.169115m/s³·거리15.519266m로 이 실행은 passed입니다. 기본 GT 제어이며 센서 기반 제어 성능이나 위 live 실행의 통과를 뜻하지 않습니다.

Live Run explorer는 `carla-vehicle-response.json`의 원래 XYZ 속도·가속도·마지막 rest·초기 속도 요청·명령/다음 frame을 연결합니다. 요청값·readback·signed heading speed·vz·3D speed를 구분하고 선택적 telemetry의 unavailable/nonfinite를 보존합니다. 기본 GT 실행의 설정 검사는 startup context에 남습니다. [차량 응답 해석](carla-vehicle-response.md).

플랫폼과 별도로 원래0.4.0 환경에서 수행한5조건 진단은 baseline/rest40/native_physics/reapply_native/configure_after_spawn입니다. 추가 rest나 stock 재적용만으로 진동이 사라지지 않았고, 모든 차량 생성 뒤 적용한 조건에서는 두 차량의 scale0.8이 유지됐습니다. 진단은 별도 source·script hash와 원본을 갖는 단일 조건 실험이며 플랫폼 판정이나 통계적 품질 입증을 대신하지 않습니다.

배포 검증은 기존43runs/48jobs의 모든 업무 행·열과 관리 파일331개의 SHA 보존, schema8/foreign_key_check 정상, 배포 source72개 SHA 및 실행 후 actor0·비동기 world·fixed delta None을 확인했습니다. 전체/후속 관련 회귀시험의 정확한 범위는 [현재 상태](status.md)와 [인수 JSON](validation/carla-vehicle-response-2026-09-13.json)에 기록합니다.

## 이전 adapter 0.4.0에서 확인한 결과

선택적 live stack의 실제4초 실행 두 건을 완료했습니다. 각80개 LiDAR 기반 명령·동기 응답·다음 physics frame, 81개 LiDAR/ego 원본을 저장하고 각 MCAP의80tick 재계산이 일치했습니다. headway1.5초 설정은7.762m 주행했고8초 설정은 관측 구간보다 긴18m 정지거리를 요구해 계속 정지했습니다. **두 판정은 감속/jerk 기준 때문에 failed입니다.** 고정 평지/rig 선언과 simulator ego/map oracle을 사용하는 제한된 pilot입니다. [사용법](carla-live-stack.md) · [실제 run·hash·재생·복구 기록](validation/carla-live-stack-2026-09-13.json).

최초 실제 시도의 검증기 signed-zero 오류는 error Run으로 보존했습니다. 원래 원격 응답은 채택·저장되지 않았습니다. byte 검사를 유지한 수정 뒤 이전 dispatcher/worker 종료와 별도 world 정리를 확인하고 reconcile했습니다. 정상 두 시험 후에도 actor0·원래 world 설정·지도 SHA를 확인했습니다.

## 이전 adapter 0.3.0에서 확인한 결과

고정8tick 대기를 두 차량의 실제 snapshot을 검사하는 bounded rest gate로 바꿨습니다. 세 시험에서23tick·연속5표본의 조건을 만족한 뒤 초기 목표 속도를 부여했습니다. 모든 초기화 표본과 정책·spawn pose·판정 사유를 `carla-startup.json`에 저장하며 Run explorer에서 SHA를 확인한 수직속도·선속도·연속 안정 표본 그래프를 표시합니다. 이는 초기 속도 부여 후의 wheel/속도 수렴을 증명하지 않습니다.

| 실제 4초 시험 · seed42 | 최대 감속 m/s² | 최대 jerk m/s³ | 이동 거리 m | 실행 / 판정 |
| --- | ---: | ---: | ---: | --- |
| Lane follow · 4m/s · gap10m | 0.660 | 25.726 | 15.512 | completed / failed |
| Lane follow · 6m/s · gap10m | 26.963 | 580.344 | 16.684 | completed / failed |
| Emergency brake · 4m/s · gap10m · trigger1.5s | 26.273 | 446.259 | 12.390 | completed / failed |

각각81개 측정 표본과 실제 RGB3장·LiDAR3개·MCAP·초기화/캡처/evidence manifest를 저장하고 모든 다운로드의 SHA/크기를 검사했습니다. 시험 후 생성 actor0·비동기 world·fixed delta None과 같은 지도 hash를 확인했습니다. 기존37개 실행·40개 작업을 포함한 이전 데이터의 모든 원래 열·행, 관리 파일120개의 SHA도 보존됐습니다. 작업 sequence·worker heartbeat·현재 엔진 소유권은 운영 상태로 별도 취급합니다.

전체 Python1171개·Chrome workflow16개·TypeScript/Vite build가 통과했습니다. 실제 서버의 위 실행과 mock의 startup timeout·실패 진단 보존·정리 검증은 구분합니다. 실서버의 강제 timeout/worker 종료 복구는 아직 검증하지 않았습니다. [현재 버전·실행·해시·잔여 문제](validation/carla-startup-acceptance-2026-09-13.json).

## 이전 adapter 0.2.0의 연결 인수 기록

- 같은 seed42·4초 조건에서 요청 속도 4m/s와 6m/s를 각각 실행했습니다. 저장 궤적은 각 81표본, 이동 거리는 약 12.711m/19.382m입니다. 실행은 completed이고 감속·jerk 기준 때문에 판정은 failed입니다.
- 각각 실제 RGB PNG3장·LiDAR JSON3개·MCAP·capture manifest가 플랫폼 artifact로 반환됐습니다. 모든 다운로드 SHA/size를 검사했고 첫 MCAP의 PNG3장과 LiDAR832/783/789점을 실제 decoder로 읽었습니다. MCU/실측 자료가 아니라 CARLA가 생성한 자료입니다.
- MCAP를 기존 run artifact에서 recording으로 등록해 source run/revision/seed/adapter/fidelity/hash 관계를 보존했습니다. start/middle/end만 보존하므로 2초 사이를 연속 센서 관측으로 처리하면 안 됩니다. 첫 기록에는 명시적 rig/binding r1과 50ms 최대 age를 설정했습니다. mount와 K는 adapter 선언이며 실측 calibration이 아닙니다.
- 첫 기록의 ordinal0/2/4 RGB를 기존 OpenCV Zoo YOLOX-S r1에 실제 CPU ONNX 실행했습니다. 검출 수는 3/4/3이며 브라우저에서 원본 영상과 bbox를 확인했습니다. 정답 라벨과 정확도 평가, 제어 명령 적용은 수행하지 않았습니다.
- 시험 후 vehicle/walker/sensor는 0개이며 원래 비동기 설정·fixed_delta_seconds=None이 복구됐고 지도 OpenDRIVE SHA도 같습니다. 실패/timeout 정리와 충돌 callback의 완전성은 별도 시험이 필요합니다.

[버전·hash·run ID·수치·한계](validation/carla-remote-2026-09-13.json)에 기계 판독 가능한 근거가 있습니다. 대용량 원본은 workspace DB·artifact와 `.runtime/carla-acceptance/`에 보존합니다.

## 다시 연결하기

이 Mac에 생성한 `.runtime/connect-carla.command`를 터미널에서 실행하면 저장된 대상에 SSH master와 **127.0.0.1:18100 → Linux 127.0.0.1:8100** 터널을 만듭니다. 비밀번호는 SSH가 요구할 때 터미널에 직접 입력합니다. 이 workspace는 SSH/sudo 비밀번호를 저장하지 않았습니다. 인증한 연결은 `.runtime/carla-ssh.sock`을 사용합니다.

```bash
bash .runtime/connect-carla.command
```

`Start AXIOM.command` 또는 `.venv/bin/python scripts/dev.py`는 `.runtime/carla-connection.json`에서 worker URL과 로컬 token 파일 위치를 읽습니다. 모든 외부 연결을 자동으로 발견하거나 방화벽을 열지 않습니다. 다른 endpoint를 환경 변수로 지정하면 저장된 token은 사용하지 않습니다.

현재 Linux worker는 `~/workspace/axiom-carla-worker/releases/checkpoint-performance-0.5.0-20260913/`의 `source-manifest.json`으로82개 source hash를 검사합니다. 의존성 interpreter는 이전 `durable-execution-0.5.0-20260913/.venv/bin/python`을 유지하고, 명시적 `PYTHONPATH`와 import 경로 검사로 새 release의 backend/native source를 사용합니다. 정상 worker에는 계측·오류 주입 wrapper가 없습니다. 이전 release·시작 스크립트·worker 상태와 기존 `myproject/.venv`는 보존했습니다. 서버에 동봉된 cp312 wheel을 사용합니다. worker는 loopback8100에만 bind하고 bearer token을 요구합니다. `~/workspace/axiom-carla-worker/runtime/`에는 `worker.json`, `carla-server.json`, 각 log, `carla-client-source.json`, 현재 release를 가리키는 `start-worker.py`가 있습니다. `runtime/executions/`는 `ADE2E_CARLA_EXECUTION_STORE`가 가리키는 영속 실행 store이며 worker 재시작·업그레이드 시 그대로 유지해야 합니다. SSH 주소와 token은 로컬 운영 파일로만 보관합니다.

플랫폼과 remote worker의 adapter version은 모두0.5.0이어야 합니다. `/health`와 실행 응답의 version을 각각 검사하며 구버전·손상 응답을 자동 수용하지 않습니다. 영속 store를 사용하는 worker는 v3 실행·조회·archive 계약을 사용합니다. 응답을 받지 못했다면 먼저 원격 execution identity와 store를 조회하고, 검증된 archive의 별도 회수가 완료된 뒤에만 로컬에 근거가 있다고 표시합니다. 중단된 엔진의 다음 실행은 소유 actor와 원래 world 상태를 확인하는 명시적 reconcile 이후에만 허용합니다.

서버와 worker는 이 연결 작업에서 시작한 사용자 프로세스입니다. OS 부팅 서비스나 상시 자동 재시작은 설치하지 않았습니다. Linux 재부팅 후에는 기록된 서버 명령으로 서버를 먼저 실행하고 해당 `start-worker.py`를 실행합니다. 기존 port나 차량·센서·동기 실행 소유자가 있으면 먼저 사용 상태를 확인해야 합니다. 종료할 때도 JSON의 PID와 실제 프로세스 명령이 이 workspace 소유인지 확인하며 다른 CARLA를 전역 종료하지 않습니다.

## 남은 문제

0.2.0의 최초 두 주행에서는 첫0.05초에 약25m/s² 감속이 있었습니다. 0.3.0의4m/s 주행에서는 큰 초기 감속이 줄었지만, 매2tick 반복되는 속도 변화 때문에 jerk 기준은 계속 실패합니다. 6m/s·gap10m 시험은 저장 trajectory와 제어 코드 재구성상0.45~0.65초에 최대 제동을 요청하는 조건이며 큰 감속 구간과 겹칩니다. 실제 적용 명령은 기록되지 않았고 제동 전에도 jerk가 높아 단일 원인으로 단정할 수 없습니다. 측정 표본을 버리거나 기준을 낮추지 않았습니다. 0.4.1은 요청/ACK·snapshot XYZ·wheel/physics readback을 추가해 설정 유실을 수정했습니다. 초기 속도 부여 뒤 잔여 진동·neutral로 보고되는 gear와 wheel 응답·차량 모델의 정밀 상관성은 계속 조사해야 합니다. 과거 표본의 원인을 새 근거로 일괄 확정하거나 과거 판정을 바꾸지 않습니다.

기본 주행 모드는 주변 GT 제어와 간헐적 RGB/LiDAR capture를 유지합니다. 선택적 live stack은 매 frame LiDAR를 소비하고 실제 명령·응답·다음 physics를 기록합니다. 기록 영상 추론과 기록 스택 재생은 각각 open-loop입니다. 0.5의 실제 오류/프로세스 중단 후 결과 회수는 위 범위에서 인수했습니다. 자연 발생 startup timeout, 저장장치·전원 장애, 정밀 센서/차량 상관성, 실행 속도, 팀 운영과 상용 release gate는 남아 있습니다.

[CARLA shared-stack 설계 기록](carla-stack-design.md) 이후 구현한 계약은 [live stack 사용법](carla-live-stack.md)과 [결과 회수 안내](carla-recovery.md)를 따릅니다. 현재 원격 source manifest label은 `checkpoint-performance-0.5.0-20260913`이며82개 파일의 archive SHA는 `50ff8cf20e631a926795c5efed0ac0b05435d22b15d42b08a552f57368fbbc03`입니다. 앞선 source D의 SHA `9aac07ea6df600bd3eb7aea46f45893c82935332453102ef019513a2dd504008`은 비교 기준과 이전 완료 archive의 출처로 보존합니다. 같은0.5.0의 인수용 source A/B/C와 이전0.4.1 source는 별도 archive와 manifest로 보존합니다. 업그레이드 시 진행 중 작업을 종료·확인하고 DB와 관리 파일을 backup한 뒤 같은 source 버전의 API·dispatcher·remote worker를 시작합니다. 원격 실행 store와 원래 실행·artifact를 새 버전으로 덮어쓰지 않습니다.
