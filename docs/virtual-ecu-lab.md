# Virtual ECU Lab · 2026-10-17

Virtual ECU 영상 후속: 새 CARLA 실행 `job_603ab17dbeb04c549722a7ed33342e00`에서
정상/400 ms 지연·회복을 완료했다. 두 카메라 원본의 SHA 검증·동기 재생·전방/추적 전환을
ECU 상태·제동·다음 속도와 연결했다. 요청 시각과 제어 적용 후 촬영 시각은 50 ms 차이를 명시한다.
[실행·화면 검사](validation/vecu-carla-demo-20261017/checks.json).

AXIOM의 host software 실행 계층이다. 운영자가 등록한 reference ADAS를 별도 프로세스로
실행하고, 불변 객체 입력·시험 개정·실행 근거를 기존 Experiment API에 연결한다.
Silver의 제품 구현이나 호환 어댑터가 아니다. 양산 애플리케이션/OS/Qualcomm target binary는 없다.

## 사용 흐름

HIL → Virtual ECU Lab에서 소프트웨어 패키지와 18초 합성 입력을 등록한다.
고장 또는 설정 A/B를 저장하고 실행 준비 확인 → 새 실행을 선택한다.
결과의 공통 커서로 입력 age·제동·ECU 상태·실제 요청/응답을 비교하고 trace/export를 받는다.
보존 실행은 재실행하지 않고 읽는다. URL은 project/test/revision/execution/step을 보존한다.
복합 고장 JSON은 단일 고장 편집을 대체하며 겹치는 구간을 거부한다.

- 고정 재생: 20 Hz, 50 ms, 20–1,200 steps. 제어 출력은 다음 ego 입력을 바꾸지 않는다.
- CARLA: 기존 HIL 시험의 정확한 개정·시나리오·고장·DUT 설정을 동결해 native 실행한다.
  실제 제어 적용 이후 A/B의 차량 상태와 후속 입력이 달라진다. 차량 안전성 판정은 미평가다.
- fault_ab: 동일 설정에서 A 정상/B 고장. settings_ab: 같은 고장 입력에서 설정만 변경한다.
- 중단과 반복은 마지막 패킷을 유지한다. timestamp를 새것처럼 갱신하지 않는다.
- 기본 freshness 한계는 250 ms inclusive, 정상 신규 패킷 3회 후 회복한다.
- 독립 oracle은 age/상태/회복/제어 범위와 degraded 제동을 검사한다. ADAS 안전성 oracle이 아니다.

## 계약 및 운영

`/api/v1/virtual-ecu`의 catalog, packages, inputs, tests, records, executions API와
Python SDK `vecu_*`, `save_vecu_record`, `execute_vecu_test`가 동일 계약을 사용한다.
생성 요청은 Idempotency-Key, 개정 변경은 expected_revision, 실행은 정확한 revision/hash가 필요하다.
실행·취소·artifact·sealed export는 기존 Experiment API를 재사용한다.
소프트웨어/interpreter/adapter/input/test hashes, seed, fidelity와 논리 결과 hash를 보존한다.
논리 hash에서 IPC wall latency를 제외한다. wall latency는 실시간 보장 지표가 아니다.

선택 어댑터 설치: `.venv/bin/pip install --no-deps -e integrations/vecu`.
기존 CARLA adapter가 필요하다. `AXIOM_VECU_REGISTRY`는 운영자 관리 JSON registry 경로다.
각 runtime은 id/name/absolute command/source_files/dut_version/classification 필드를 가진다.
classification은 reference_application이다. API에서는 임의 실행 명령을 받지 않는다.
이 프로세스 격리는 보안 sandbox가 아니다. 신뢰하는 로컬 코드만 등록한다.
프로토콜은 기존 `hil-dut/1`의 init → step → close이며 sequence·version·profile·PID를 검증한다.
응답 상한 256 KiB, wall timeout 2초, 취소 시 process group을 정리한다.
소스 drift, 잘못된 응답, 종료, timeout, 미연결 CARLA는 실패하며 합성 결과로 대체하지 않는다.

## 실행 근거

[공개 SDK 기록](validation/vecu-20261017.json)에 정상/지연/중단/반복/설정 A/B,
지연 반복 2회, 실제 CARLA 폐루프 등 8개 실행을 보존했다. 모두 완료했고 다운로드한
artifact와 sealed manifest를 재해시했다. 지연 3회 논리 hash는 동일하다.
각 side는 18초/360 steps다. 설정 A/B의 입력 hash는 같으며 최초 degraded step은
A=85(4.25초), B=82(4.10초)로 다르다. CARLA는 실제 request/reply/next_ego와 원래 영상을 보존한다.

92개 관련 backend/실제 subprocess/기존 HIL 회귀 검사가 통과했다.
`examples/vecu_workflow.py`는 API/SDK 실행·poll·artifact hash·export 검증을 재현한다.
UI 실행·개정·커서 복원 근거는 [검사 기록](validation/vecu-checks-20261017.json)에 있다.

새 UI 5300/API 8200/worker는 별도 데이터 디렉터리에서 실행한다.
기존 HIL 저장소를 일관된 SQLite backup과 copy-on-write artifact 복사로 보존했다.
기존 5280 ADAS 서비스와 다른 서비스는 재시작하지 않았다.

## 후속 경계

이 초기 패키지 경로에는 실제 ECU 소프트웨어, RTOS/MCAL, target binary, AUTOSAR,
Silver/FMI adapter, RAW/RGB→객체 인지, 실제 보드와 물리 HIL을 연결하지 않았다.
별도 [Network Lab](virtual-ecu-network-lab.md)은 SIL Kit CAN/Ethernet 프레임,
공개 Com/PduR/CanIf와 Trampoline OSEK 서비스를 실행한다. 전체 AUTOSAR 또는
양산 ECU 통합은 아니다. 카메라 RTL 결과는 별도 근거다.
현재 reference application 결과를 양산 ECU vECU 등급 인증이나 물리 센서 검증으로 표현하지 않는다.

## CARLA packet repeat · 2026-10-17

`input_repeat` preset은 마지막 수락 패킷의 sequence/time_s/objects를 그대로 반복한다.
`object_delivery_present`는 실제 어댑터→DUT 전달 여부다. drop은 false인 상태에서
기존 수락 패킷을 유지하고, repeat은 true로 같은 패킷을 전달한다. 실제 Ethernet/CSI 패킷은 아니다.
`reference-traffic-dut/2`는 `duplicate_packet_detected`, `duplicate_packet_count`,
`missing_packet_count`를 반환한다. 같은 sequence의 payload 변경은 실패한다.
중복은 freshness timestamp를 갱신하지 않고 recovery의 신규 패킷 수에도 포함하지 않는다.
기존 250 ms 감시와 신규 패킷 3회 회복 정책, 제어 알고리즘은 유지했다.

검출 수는 DUT 경계의 수신 관측이다. 지연 모델도 초기 지연을 채우는 8 step 동안
마지막 수락 패킷을 DUT에 재노출하므로 400 ms 지연에서는 중복 8회가 기록된다.
이를 네트워크에서 주입한 80회 repeat과 같은 고장으로 해석하지 않는다.

새 UI 5310/API 8210은 이전 workspace의 일관된 DB backup과 copy-on-write artifact 복사를
사용한다. 기존 5300/8200 서비스를 재시작하지 않았다. 원격 native 코드는
`axiom-hil-repeat-20261017`의 별도 디렉터리로 배치하고 source hash를 검증한다.
기존 package는 v1 소스 hash를 보존한다. v2 실행에는 새 등록 package를 사용한다.

[새 native 실행 기록](validation/vecu-repeat-20261017/receipt.json),
[회복 영상](validation/vecu-repeat-20261017/recovery.png).

새 repeat/drop/delay 세 실행과 기존 결과의 제어·속도 비교는 [comparison.json](validation/vecu-repeat-20261017/comparison.json)에 보존했다. 관련 테스트 216개 및 UI build 통과.

## C/C++ ECU package A/B · 2026-10-17

HIL → Virtual ECU Lab now connects registered C/C++ source → actual Linux build →
immutable package A/B → fixed replay or native CARLA → synchronized video/signals,
retained-event seek, exact saved URL and sealed export. The build panel exposes the
compiler, target platform, source/binary hashes and compiler log. An empty compiler
log is valid and is preserved as an empty artifact. Existing Python packages and
retained results remain readable.

The first target is the operator-configured Linux x86_64 CARLA worker. Source IDs
are `cpp-immediate` and `cpp-ramp`; public requests never supply commands or paths.
Each native executable performs the full reference controller in C++17. It does
not call Python for its control output. Python remains an independent post-run
policy oracle. A reference C++ program is still reference application software,
not a production Level-1 ECU or a Silver-compatible runtime.

- A preserves the Python reference's 250 ms freshness threshold and immediate
  full-brake behavior. Three **new** fresh packets release the recovery gate.
- B immediately suppresses throttle on stale input and increases brake from the
  previous applied value to 1 over 300 ms. After the same recovery gate, it blends
  brake/throttle toward normal control for 500 ms; throttle remains zero whenever
  blended brake exceeds 1e-9. The diagnostic state remains `recovering` during this
  blend. A new fault restarts braking from the applied brake, without a brake drop.
- Both policies use identical normal driving code and steering. B is a comparison
  policy, not a qualified improvement in safety or stopping distance.
- Package A/B uses the same scene/seed/fault schedule on both sides. `fault_enabled`
  false executes both without injection. CARLA vehicle state and subsequent
  observations may diverge after different control commands; fixed replay does not
  feed commands back into the vehicle input.
- Replay and CARLA distinguish no delivery (`dropout`/`drop`) from an identical
  delivered packet (`repeat`). A duplicate never refreshes its timestamp or counts
  as a new recovery packet. The initial retained exposures of a delay queue are
  separately interpretable from intentionally repeated packets.

API additions under `/api/v1/virtual-ecu`: `POST /builds`, `GET /builds`,
`GET /builds/{execution_id}`. Build requests accept project/name/source_id and use
normal Experiment execution, cancellation, artifact and export APIs. Package save
accepts either legacy `runtime_id` or `build_execution_id`. `package_ab` tests add
`variant_package_ref` and `fault_enabled`; A remains `package_ref`. Build results
pin source files, compiler/flags, binary, dependency licenses and target platform.
The runtime resolves a server-controlled immutable build key and checks the binary
before and after execution. Failed/missing builds and unsupported platforms fail
closed; no Python result substitutes for a native build.

The new local preview is UI5320/API8220 with an isolated data clone under
`e2e/.runtime/vecu-cpp-20261017`. Existing services were preserved. The test bench
controls are retained playback and new-execution controls, not live debugging,
interactive calibration or hard-real-time scheduling. This package path does not
run FMU, production AUTOSAR/OS, Qualcomm binaries or physical HIL. The separate
[Network Lab](virtual-ecu-network-lab.md) adds functional CAN/Ethernet frames,
public BSW routing and actual OSEK service execution through a registered adapter.

Reproduce through the public SDK:

```sh
.venv/bin/python examples/vecu_cpp_workflow.py \
  --url http://127.0.0.1:8220 --ui http://127.0.0.1:5320 \
  --project project_9f48a965d4194be0b18859e334e007bb \
  --hil-refs docs/validation/vecu-repeat-20261017/receipt.json \
  --output /absolute/path/new-receipt.json --fixed-replay
```

This builds both policies and runs six fixed replays (including three identical
logical repeats) plus four 18 s CARLA A/B pairs: normal, delay400ms, dropout and
repeat at [3,7) seconds with seed42. JSON/log/source artifacts and export hashes are
verified. `--download-media` additionally retrieves all media; otherwise original
media references remain on the execution. `--resume` preserves existing run IDs.

The acceptance campaign completed both Linux builds, all six fixed replays and
all four CARLA A/B pairs (eight vehicle executions). The three repeated fixed
replays have equal per-policy logical hashes. Normal CARLA A/B commands and speed
are identical. Under 400 ms delay, degraded-to-stop sampled ego travel is
0.563855 m for A and 0.817079 m for B; tracking resumes at input time 7.10 s and
7.60 s respectively. Drop and repeat retain the same vehicle behavior within each
policy, but expose different duplicate/missing counters. These are measured
simulator outputs, not a driving-safety verdict.

The campaign retained and rehashed 12 execution exports and 140 artifacts,
including 40 original camera ZIP artifacts checked directly in server storage.
This does not imply decoding every image frame. The initial failed execution's
source-identity mismatch was rejected before result publication and preserved in
`attempt-1.json`; its cleanup was confirmed before a new frozen-source campaign.
Backend/native/adapter/SDK checks: 206 passed. See the
[receipt](validation/vecu-cpp-20261017/receipt.json),
[comparison and metric definitions](validation/vecu-cpp-20261017/comparison.md), and
[test report](validation/vecu-cpp-20261017/tests.xml).

Build repeatability boundary: a later UI build of the same ramp source/compiler
has a different binary hash. Read-only ELF comparison found identical executable
`.text`; the differences are the build-directory path embedded in the JSON
library's assertion strings and the derived GNU build ID. Each package pins and
verifies its own binary; this implementation does not claim bit-for-bit identical
rebuilds. Stable path mapping remains a build-system follow-up and does not change
the identity of the already verified campaign binaries.

Final browser checks passed for source build/registration, a new native fixed
replay, and retained CARLA synchronized front/chase playback, measured geometry,
event seek, exact resume and hashed export. Production UI build passed; its
existing bundle-size warning remains. See the [browser receipt](validation/vecu-cpp-20261017/browser/receipt.json)
and [aggregate checks](validation/vecu-cpp-20261017/checks.json).
