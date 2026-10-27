# Platform API contract · v0.1

Implementation agreement for the local platform. Base URL `/api/v1`.

Virtual ECU Network timing update **2026-10-17**:
`GET /virtual-ecu-network/executions/{id}/causality?project_id=...` returns
`vecu-network-causality/1` from completed, project-scoped, hash-verified evidence.
It is shared by UI and `vecu_network_causality` in the Python SDK. Derivation
does not submit or reseal an execution. [Detection/CAN/vehicle clocks and scope](virtual-ecu-network-lab.md).
MCAP, durable execution, suite/campaign evidence, sensor replay and schema 7 datasets/evaluation update: **2026-09-13**.

Object Sim authoring update **2026-10-09**: `/object-maps` stores immutable `object-map/1`
sources; `/preview` and `/import` are read-only. `/{id}/build` pins the source revision/hash,
compiler/geometry hashes and target engine build into the existing `/maps` catalog, without
launching CARLA. `/{id}/export` returns the original/imported or compiled document with
`X-Content-SHA256`. `/object-behaviors` publishes the shared action/trigger contract.
`scenario-v2/3` adds explicit behavior sequences, a replaceable reactive planner and optional
fixed destination. Existing `/1` and `/2` contracts and receipts remain unchanged.
Native execution, artifacts and verification reuse `/experiments` and
`/experiment-verification-plans`; no second scheduler or opaque agent-only API is introduced.
See [Object Sim map, behavior and evidence contract](object-sim-reactive.md).

Project-scoped Object Sim also uses the versioned experiment API and schema18 workspace.
The `esmini-object/0.2.0+esmini3.8.2` handler adds the immutable `object-scene/1` artifact through
the existing execution/export/managed artifact routes. See [scene source, geometry and event contract](object-scene-workbench.md).
UI and SDK validate the sealed input and original hashes; legacy executions retain their original receipts.
This authored kinematic scene is not physical sensor or real-world validation.

Sensor Sim uses the same project-scoped experiment routes. Optional CARLA feedback1.1.0 adds
`sensor-scene/1` and original `map.xodr`; source hashes/configuration/seed and exact f→f+1
ego/camera matrices remain bound to the sealed result. Retained stationary RGB/LiDAR/radar
use original clocks, tuple layouts and observed transforms, with sequential A/B and anomalies
explicit. [Sensor original contracts and normal UI/SDK investigation](sensor-scene-workbench.md).

Scenario/run physics use SI: XY world plane in metres, yaw in radians, and monotonic simulation time in seconds. Imported MCAP recordings have a **separate clock contract**: `log_time_ns` and `publish_time_ns` are original uint64 nanoseconds encoded as decimal strings, relative to the producer's epoch. They are not automatically UTC or simulation time, and their clocks are not aligned or calibrated by ingestion. Unknown payload units/coordinates do not inherit the scenario SI/world-frame contract. This is an application schema, **not OpenSCENARIO/OSI compliance**.

## HIL fixed scene selection · 2026-10-11

The project-scoped Software-DUT API adds fixed lead-motion overlays to the existing
preset-bound base scenario. Catalog lookup and test save work without an engine;
preview requires the configured native adapter and does not submit a job.

| Endpoint | Contract |
|---|---|
| GET `/hil-sim/scenes?project_id=default` | Read-only `{schema_version:"hil-sim-scenes/1", project_id, default_scene_id:"lead_stop", source_scope:"existing_preset_bound_base_scene", items:[SceneProfile]}`; requires project read access. |
| POST `/hil-sim/tests` | Existing test-save body plus optional `scene_profile:{id,revision,sha256}`; requires Engineer and `Idempotency-Key`. Returns the full frozen descriptor in `test.scene_profile` and `test.experiment.input.scene_profile`. |
| POST `/hil-sim/presets/{id}/preview` | Existing `{project_id,expected_binding_revision,parameters}` plus the same optional scene ref. Returns full `scene_profile` and the exact experiment/prepared preview. Requires Engineer. |
| POST `/hil-sim/tests/{id}/preview` | Existing `{project_id,test_ref:{id,revision,sha256}}`; uses the saved full input, with no scene override or latest-scene lookup. Requires Engineer. |

`SceneProfile` has exactly `schema_version:"hil-scene-profile/1"`, `id`, `revision:1`,
`title`, `description`, `clock:"simulation_input"`, `duration_s:18`,
`lead_speed_schedule:[{at_s,target_speed_mps}]`, and `sha256`. Supported recipes:

| ID | Authored target-speed schedule |
|---|---|
| `lead_deceleration` | 0s: 2m/s; 4s: 0.5m/s |
| `lead_stop_go` | 0s: 2m/s; 4s: 0m/s; 8s: 2m/s |

The hash covers the complete descriptor except `sha256`, using UTF-8 JSON with
sorted keys, compact separators, unescaped Unicode and no nonfinite numbers.
Integral numbers remain integers. Clients use the returned exact reference;
unknown IDs, revisions, hashes or extra fields fail closed. `lead_stop` names the
legacy default and is not an explicit selectable ref. Omitted/null `scene_profile`
retains that default and adds no new fields to legacy records or idempotency hashes.

The overlay changes only the lead vehicle's speed/stop schedule. The original
`source.scenario_ref` revision, `configuration_hash` and `manifest_hash`, seed and
preset binding stay pinned; they continue to identify the base scenario. The full
overlay is separately bound into the test hash and prepared experiment input hash.
The adapter remains `carla-software-dut/1.0.0`. Fault timing and optional frozen DUT
profiles are independent; scene selection does not change the comparison mode.

The frozen snapshot, top/side summaries and original HIL trace must echo the exact
selected descriptor. Execution attachment, observation and batch-summary paths
reject mismatches. Saved test revisions, batch inputs and exports retain the same
selection. Schedule times are authored simulation-input times; trace response
frames and observed lead events record what occurred after the physics tick.
Selecting a target speed is not evidence that the vehicle reached it. This remains
Software-DUT simulation, without a physical-ECU or real-time guarantee.
See the [SDK example](../sdk/python/README.md#hil-장면-선택) and
[HIL beta workflow](hil-sim-beta.md) for explicit execution and retained evidence.

## Entities

Suite/campaign/baseline APIs use the same authentication and queue ownership. See [versioned suite contract](suites-contract.md), [captured comparison contract](comparison-evidence.md), and [SDK/CLI walkthrough](test-suites.md). Suite criteria remain distinct from the dynamic current-scenario requirement view. Saved baselines and comparisons include failed/unevaluated evidence and do not approve a release.

- `Scenario`: `id`, `name`, `description`, `category` (`cut_in`, `emergency_brake`, `pedestrian_crossing`, `lane_follow`), `map_name`, `weather` (`clear`, `rain`, `fog`), `time_of_day` (`day`, `night`, `dusk`), `tags: string[]`, `parameters: {ego_speed_mps, target_speed_mps, initial_gap_m, trigger_time_s, friction, response_delay_s}`, `duration_s`, `seed`, `revision`, `created_at`, `updated_at`.
- `ScenarioCreate`: same authoring fields without server fields; reasonable defaults required.
- `JobSummary`: identity and `submission_seq`, captured scenario ID/name/revision, adapter ID/version, `input_hash`, state (`queued`, `running`, `cancel_requested`, `completed`, `failed`, `cancelled`, `interrupted`), UTC creation/update/start/dispatch/finish times, nullable `run_id`, `error`, worker/lease/cancel-request fields, `cancellation_supported`, `outcome_uncertain`.
- `JobDetail`: summary + replayable `request`, immutable effective `scenario_snapshot`, recent append-only lifecycle `events: {at,type,message}[]` (last 200), `events_total`, nullable `failure_http_status`. Job `completed` means result publication; the run's verdict may still be failed. Job timestamps are wall-clock UTC, separate from trajectory simulation time and MCAP producer clocks.
- `WorkerSummary`: `id`, `heartbeat_at`, `state` (`idle`, `running`, `stopped`), nullable `job_id`, computed `alive`.
- `ExecutionResource`: `adapter_id`, `state` (`idle`, `reserved`, `reconcile_required`), nullable `job_id`, `updated_at`, `note`. This is ownership inside one workspace, not a remote-server-wide distributed lock. Full models: [jobs contract](jobs-contract.md).
- `RunSummary`: `id`, `scenario_id`, `scenario_name`, `scenario_revision`, `adapter_id`, `status` (`completed`, `error`), `verdict` (`passed`, `failed`, `error`), `created_at`, `duration_s`, `execution_ms`, `metrics: Metric[]`, `provenance`.
- `RunDetail`: RunSummary + `scenario_snapshot: Scenario`, `trajectory: Frame[]`, `events: Event[]`, `artifacts: Artifact[]`.
- `Metric`: `name`, `value: number|null`, `unit`, `threshold: number`, `operator` (`gte`, `lte`, `eq`), `passed: boolean`. Names: `collision_count`, `min_ttc_s`, `max_deceleration_mps2`, `max_jerk_mps3`, `distance_m`. Infinite/undefined TTC represented as null. Distance is informational (threshold 0 gte).
- `Frame`: `time_s`, `ego: {x,y,yaw,speed_mps}`, `actors: {id,type,x,y,yaw,speed_mps}[]`, `ttc_s: number|null`, `acceleration_mps2`.
- `Event`: `time_s`, `type`, `message`.
- `Provenance`: `input_hash`, `output_hash`, `seed`, `adapter_version`, `model_fidelity` describing the producer's actual scope. An available adapter or decoded sensor payload does not imply sensor validation.
- `Artifact`: `id`, `run_id`, `name`, `kind`, `media_type`, `size_bytes`, `sha256`, `created_at`, `download_url`, `source`.
- `Requirement`: `id`, `name`, `description`, `metric_name`, `operator`, `threshold`, `category: string|null`, `scenario_ids: string[]`, `status` (`passed`, `failed`, `untested`), `evidence_count`, `last_run_id: string|null`.
- `Adapter`: `id`, `name`, `version`, `kind`, `status` (`available`, `not_configured`, `planned`), `description`, `capabilities: string[]`, `limitations: string[]`, `license`, `source_url`.
- `RecordingSummary`: original MCAP identity, declared source metadata, SHA-256/size, profile, actual message/channel counts, exact ns bounds or null, warnings, download URL and derived payload `index` manifest. `index.sha256` hashes concatenated payload bytes, not SQL rows; `index.source_sha256` links those bytes to the original.
- `RecordingDetail`: summary + channels keyed by MCAP channel ID, schema definitions/hashes and container metadata. Same-topic channels stay distinct; empty channels have count 0 and null time bounds.
- `RecordingSamples`: `recording_id`, `channel_id`, `requested_time_ns`, `samples`, `warnings`, `next_after_ordinal`. Samples preserve ordinal, log/publish ns, sequence, payload size/hash, `decode_status`, bounded `decoded`/`decoded_text` and opaque preview. Full field definitions: [recording contract](recordings-contract.md).
- `RigDetail`: source-declared sensor frame/transform/intrinsics definitions, immutable rig revision and exact export hash/size. `calibration_status:"source_declared"` is not physical validation.
- `BindingDetail`: a recording's immutable channel→sensor mapping, pinned rig snapshot/revision/hash, decoder versions, age limits and signed log-time offsets. Actual recording/channel and rig revision foreign keys are preserved.
- `SensorFrame`: exact recording/channel/ordinal identity and raw message metadata, separate decode status, optional `SensorDecodeResult` with PNG/XYZ preview bytes and source/output hashes. `SensorBundle` retains cursor, explicit binding identity, per-channel selection/freshness, original clocks and transform eligibility. Full models: [sensor contract](sensors-contract.md).

## Endpoints

- GET `/health`: `{status,version,database}`
- GET `/overview`: `{scenario_count,run_count,pass_rate,artifact_count,requirement_count,covered_requirement_count,recent_runs: RunSummary[],category_distribution: {category,count}[],coverage: {category,weather,total,tested,passed,failed,untested}[]}`. Aggregate real persisted rows only. `total` is the number of defined scenarios; only the latest complete current baseline counts as `tested`. Stale, variant, errored and missing runs are `untested`. `pass_rate` is 0–100 across persisted executions, not real-world safety coverage.
- GET `/scenarios?q=&category=` → `Scenario[]`
- POST `/scenarios` body ScenarioCreate → Scenario (201)
- GET `/scenarios/{id}` → Scenario
- PATCH `/scenarios/{id}` body partial authoring fields → Scenario (revision increment)
- POST `/scenarios/{id}/clone` → independent Scenario (201)
- GET `/runs?scenario_id=&verdict=` → RunSummary[] newest first
- POST `/runs` body `{scenario_id,adapter_id:"reference",seed?:int,parameter_overrides?:Partial<Scenario.parameters>}` and optional `Idempotency-Key` header → RunDetail (201). Compatibility route: submits to the same durable queue and claim/resource path, then executes inline or waits for its result. Unknown/unconfigured adapters are rejected, never silently substituted. Existing key/body returns the original result; changed body returns 409. A pending, interrupted or quarantined job can return 409 with its job ID; inspect it without creating a new request.
- GET `/runs/{id}` → RunDetail
- GET `/runs/{id}/export` → downloadable JSON evidence manifest containing snapshot, provenance, metrics, trajectory and events.
- POST `/jobs` uses the same execution body and **required** `Idempotency-Key` (1–200 characters) → JobDetail (202). Persists input snapshot/version before returning; a separate worker processes queued jobs. Same key/body returns the same job even after scenario changes, completion, failure or restart.
- GET `/jobs?status=&limit=100&before_seq=` → `{items: JobSummary[], next_before_seq}`. Newest submission first; limit 1–100. Use the returned non-null cursor for the next page.
- GET `/jobs/{id}` → JobDetail
- POST `/jobs/{id}/cancel` → JobDetail. Records a request; `cancel_requested` is not confirmation that a remote engine stopped. Terminal jobs remain terminal.
- GET `/workers` → WorkerSummary[]. Recent heartbeat indicates process connectivity, not engine validation.
- GET `/execution-resources` → ExecutionResource[]
- POST `/execution-resources/{adapter_id}/reconcile` body `{job_id,note,confirmed_stopped:true}` → ExecutionResource. Requires the exact quarantined job, a nonempty note and explicit confirmation that the former dispatcher and engine are stopped/cleaned. Records the operator declaration and permits future dispatch; it neither stops an engine itself nor resolves the previous job's uncertain outcome. Read-only health success is insufficient.
- GET `/artifacts?q=&kind=` → Artifact[]
- GET `/artifacts/{id}/download` → managed local artifact bytes only
- POST `/recordings?name=&source_kind=&source=` with binary `application/octet-stream` body → RecordingDetail (201). Identical original bytes and identical supplied metadata return the existing recording (200); different attribution never silently overwrites it. `source_kind` (`unknown`, `synthetic`, `simulation`, `measured`) is a user declaration.
- GET `/recordings?q=` → RecordingSummary[] newest first
- GET `/recordings/{id}` → RecordingDetail
- GET `/recordings/{id}/download` → exact managed original bytes after size/SHA-256 validation
- GET `/recordings/{id}/samples?channel_id=&at_ns=&limit=` → RecordingSamples. At-time query selects the latest sample at/before the exact uint64 ns cursor, with the last file ordinal winning a timestamp tie; if none precedes it, returns the first following sample with a warning. Default cursor is recording start; limit 1–100.
- The same samples endpoint accepts `from_start=true` to begin complete chronological traversal, then `after_ordinal=N` from `next_after_ordinal` to continue strictly after `(log_time_ns, ordinal)` in the same recording/channel. `at_ns`, `from_start=true`, and `after_ordinal` are mutually exclusive. Browse `requested_time_ns` is null; continuation is null at the end. This exposes all messages, including timestamp-zero ties and duplicate groups larger than one page.
- GET `/requirements` → Requirement[] (current-baseline evidence uses latest eligible job submission per linked scenario; previous runs are used only when no eligible job exists)
- POST `/requirements` body `{name,description,metric_name,operator,threshold,category?,scenario_ids:[]}` → Requirement (201)
- GET `/adapters` → Adapter[]
- GET `/adapters/{id}/health` → `{adapter_id,checked_at,status,detail,metadata?}`. Read-only runtime check; status is `ready`, `not_configured`, `unreachable`, or `unknown`. Configuration and connection are separate, and a ready connection is not model validation. No simulation or run record is created.
- GET `/agent/tools` → `{tools:[{name,description,method,path,input_schema,read_only}],usage_notes:string[]}`. OpenAPI `/openapi.json`, Swagger `/docs`, ReDoc `/redoc`, agent guide `/llms.txt`.

Input validation errors use FastAPI's structured `detail`, missing resource 404, duplicate key 409, nonavailable adapter 422. No manufactured analytics or implied sensor fidelity. Boot seeds authored example scenarios and **actually executes** a small set of reference runs; all example artifacts labelled synthetic/reference. UI uses the same API as SDK and agents. Available reference adapter must be protocol/registry replaceable; future engines never need core DB/UI edits.

## Execution failure semantics

Idempotency keys are **endpoint scoped**: `/jobs` and `/runs` have separate key namespaces. After a lost response, retry the same endpoint, key and normalized body. Moving the same key between these endpoints creates a separate execution intent. A new key also creates a new intent; neither a client timeout nor `wait_job` timeout cancels or retries the engine. UI and new SDK workflows default to job submission/polling; `/runs` remains the synchronous compatibility path.

Submission preflight or unsupported-input rejection produces 422. A worker that rejects a pinned adapter/version or input records a failed job with the failure status and no fabricated result. Engine runtime failures, uncertain remote outcomes and invalid returned evidence normally compute an `error` Run that is published with a failed job. Publication/worker failure can leave a failed job without `run_id`; lease loss leaves an interrupted job. Retrying the original endpoint/key retrieves that same intent instead of dispatching the engine again.

Queued cancellation ends without engine invocation. A cancellation confirmed by the core before adapter invocation, or acknowledged by a cooperative adapter, becomes `cancelled`. Dispatch intent is retained as an audit timestamp even if core cancellation wins immediately before the call. Unsupported external cancellation remains a request: if a complete result arrives, it is kept with a `cancel_not_honoured` event. An expired owner cannot renew its lease or publish; its outcome remains uncertain and is never automatically retried.

Claim/heartbeat/publication use short SQLite transactions; computation and per-attempt artifact staging run outside them. A live owner atomically publishes job status, Run, artifact references and any legacy run-key mapping. External adapter IDs are exclusively reserved; uncertain outcomes quarantine the resource until explicit operator reconciliation. The exact built-in reference class can run in parallel. Other workspaces sharing a remote server are outside this local ownership guarantee.

Baseline selection uses greatest `submission_seq` among jobs eligible as execution evidence, not completion order. A newer running/failed/interrupted/after-invocation-cancelled job blocks an older pass. Queued, known preflight-rejected and confirmed-before-invocation-cancelled jobs do not replace previous evidence. A completed result must still match current scenario revision, seed and parameters. Legacy Run-only scenarios retain the earlier latest-run fallback.

Schema 1/2→3 added jobs/events/workers/resources; schema 4 added versioned suites, campaign membership and sealed baseline/comparison records. Schema 5 adds reusable sensor rig revisions, recording binding revisions/channel mappings and run-recording lineage. Known older versions migrate additively with existing data retained. Ordinary failed publication cleans up its owned files. Abrupt process/host failure may leave unreferenced staging/files; automatic orphan collection, PostgreSQL queue, distributed scheduling, automatic retry and tenancy remain unimplemented. Actual CARLA engine/sensor validation remains a separate acceptance gate. See [execution workflow](execution-jobs.md) and [jobs contract](jobs-contract.md).

## Implemented recording boundary

Recording import uses `mcap==1.4.0`, `lz4==4.4.5`, `zstandard==0.25.0` and a separate bounded parser. It scans actual records, validates present CRCs and references, and publishes the original, derived payload sidecar and SQL index transactionally for ordinary local failures. Indexed/unindexed and out-of-order input use the same derived seek path. Native Summary/index semantics are not certified. Absent CRCs remain explicit warnings.

The local synchronous importer limits uploads to 128 MiB, records/decompressed chunks to 32 MiB, cumulative parsing volume to 256 MiB and messages to 100,000. JSON decode is limited to 64 KiB per sample; unknown payloads remain opaque. Limits are application policies, not whole-process memory or fleet-scale guarantees. Oversize requests return 413, wrong media type 415, malformed/unsupported input 422, and detected stored-byte integrity failures 409. Invalid or failed imports clean up their owned partial files; this is not cross-process/host crash atomicity.

JSON null may be successfully decoded. Unsafe JavaScript integers retain exact `decoded_text` while structured `decoded` is null with a warning; interpret `decode_status`, not truthiness. No ROS/CDR/protobuf, image/point-cloud decode, sensor synchronization, clock/calibration verification, or log-to-scenario conversion is implied by container acceptance.

See [recording API details](recordings-contract.md), [user/SDK walkthrough](data-recordings.md), [README](../README.md), [format and safety notes](mcap-format-notes.md) and [preserved upstream notices](../third_party/README.md).

## Sensor declarations and recorded replay

- GET `/sensor-decoders` → installed decoder metadata. GET `/recordings/{id}/sensor-channels` → `{recording_id,channels,warnings}`, with exact schema/encoding support per channel.
- POST/GET `/sensor-rigs` → create/list rigs. GET `/sensor-rigs/{id}?revision=N`, POST/GET `/sensor-rigs/{id}/revisions` → immutable definition revisions; POST requires a complete definition and `expected_revision`. GET `/sensor-rigs/{id}/export?revision=N` → exact stored manifest bytes.
- POST `/recordings/{id}/sensor-bindings` → complete mapping with explicit rig revision, decoder versions, source note and `expected_revision` (0 initially). GET the same path with optional `revision=N` for authoring; GET `/sensor-bindings/revisions` lists history and GET `/sensor-bindings/export?revision=N` returns exact bytes beneath that recording path. No implicit binding update follows a rig revision.
- GET `/recordings/{id}/sensor-frame?channel_id=N&ordinal=N&max_points=20000` → exact message inspection, including timestamp ties. GET `/recordings/{id}/messages/{ordinal}/payload` → hash-checked original message bytes.
- GET `/recordings/{id}/sensor-bundle?at_ns=N&binding_revision=R&channel_id=C` → latest observations at or before cursor after declared offsets; at most four repeated channel IDs. Omitted binding revision means unbound. Optional query `max_age_ns` applies only when unbound. Future observations are not substituted; stale previews are withheld and absent age policy stays unknown.
- POST `/runs/{run_id}/recordings` body `{artifact_id}` → import an existing MCAP artifact with run lineage, without executing the engine. GET `/recordings/{id}/origin` → verified origin metadata or null.

Rig/binding authoring uses optimistic revision conflicts, not an Idempotency-Key
contract. A saved binding pins the exact rig snapshot and decoder versions.
Exports verify canonical bytes, hashes, identity and mapping metadata. Unknown
coordinates/calibration, frame mismatch or transformed point overflow disables
rig transforms while valid native inspection remains available.

Built-in decoders support bounded subsets of Foxglove JSON and standard ROS2
CDR1 image/point-cloud messages. Syntax support and actual pixel/point decoding
are separate from clock synchronization, calibration and sensor accuracy. Core
rechecks PNG framing/CRC/decompression, finite point buffers, complete point
accounting, output bounds and source/output hashes. See [supported formats](sensor-format-notes.md),
[complete sensor API contract](sensors-contract.md), and [SDK/CLI example](../sdk/python/README.md).

## Perception model and experiment API

`/api/v1/perception` adds raw bounded model-artifact upload, immutable model
revisions/exports, available inference runtimes, typed recorded-image jobs,
exact saved input PNG downloads and immutable terminal prediction comparisons.
Model registration does not execute a graph. Jobs require pinned model/decoder
revisions, 1..64 exact original file ordinals and an endpoint-scoped stable
`Idempotency-Key`. Frame states include running, completed, failed and genuinely
unattempted; cancellation retains partial results. These jobs do not create
simulation runs or driving-validation verdicts. The UI and SDK use these same
routes. See [complete request/response contract](perception-contract.md) and
[executable workflow](perception.md).

## Dataset authoring and detection evaluation API

`/dataset-images` captures exact source camera messages independently of detector
execution. `/datasets` stores immutable images/labels/splits/review declarations
with optimistic revisions. `/detection-evaluations` submits a pinned dataset
revision, explicit split and class mapping plus terminal inference jobs to the
same durable queue (`kind=evaluation`). Each source image stays in the selected
denominator; unreviewed/missing/failed/changed input withholds aggregate metrics.
`/detection-evaluators` advertises optional evaluator identity and policy.

Terminal evaluation export pins original dataset and source-job manifests,
original inference limits, class mapping, evaluator version/policy, full report
and hashes. It does not create a driving Run or requirement verdict. Schema7
preserves earlier simulation and perception rows and their submission order.
See [typed dataset/evaluation contract](datasets-contract.md),
[walkthrough](datasets.md), and [metric provenance](detection-evaluation-notes.md).

## Native city catalog and YAML API

Schema19 adds immutable project-scoped `/maps`, `/assets`, and `/controllers`
revisions. References pin `{id, revision, sha256}`; catalog manifest SHA and native
OpenDRIVE SHA are separate. `/maps/{id}/route` is read-only directed graph routing.
`/scenario-v2/yaml/validate` is read-only; YAML import saves a revision only, with
original source and normalized configuration hashes. No YAML endpoint executes code
or submits simulator work. Existing schema1 JSON/XML and stored results remain valid.

City schema2 uses the same `/experiments` and execution queue, artifact exports and
`/experiment-verification-plans` APIs as other engines. Prepared input freezes
scenario, map/assets/controllers, runner/control/maneuver source, native build,
operator transport and optional Autoware profile. Changed sources require a fresh
preview. Physical feedback, timed position replay and native GT planning/control
remain distinct. See [full endpoint, SDK and evidence contract](scenario-v2-city.md).

## ADAS Camera Module RTL · 2026-10-17

Engineering Lab의 기존 versioned API에 `config.engine=adas_module_rtl`,
`pipeline_id=adas-camera-4ch/1`을 추가했다. UI·SDK는 같은 immutable 시험 개정을 사용한다.

- `GET /v1/hil-sim/engineering-lab/templates` / `readiness`: 네 채널 시험과 별도 빌드 준비 상태.
- `POST /v1/hil-sim/engineering-lab/tests`: 네 채널 RAW 참조·raster·CSR·fault·trace window를 저장.
- `POST /v1/hil-sim/engineering-lab/tests/{id}/preview`: project, revision, sha256을 고정한 native 실행 준비.
- 같은 경로의 `build-preview`: `ecp5-85k-cabga381-6-ooc/1`의 별도 합성·배치배선 준비.
- 반환된 experiment를 기존 Experiments 생성·제출·조회·취소·artifact/export API로 실행한다.

지원하지 않는 보존 개정은 원본을 변경하지 않는다. 목록은 `unavailable_revisions`에 원인을
반환하며, 해당 개정의 직접 조회·새 실행 준비는409로 실패한다. 다른 정상 개정 목록은 유지한다.

`frames.json`의 schema는 `adas-module-frames/1`이다. 프레임 상태는 committed, aborted,
unadmitted, missing이고 commit만 유효한 완성 영상이다. `source_pixels`와 `source_complete`가
입력 hash의 범위를 나타내며 미도달·불완전 입력에는 RAW preview를 만들지 않는다.
모든 출력 비교 차이는 유지하고 확정/폐기 영상 오류와 예상하지 못한 오류를 별도로 기록한다.

**job status completed는 검증 통과가 아니다.** `summary.verdict`,
`acceptance_1080p30_passed`와 별도 빌드의 `timing_met`을 확인해야 한다.
1080p30 인수는 고정1920×1080·30프레임/채널·74.25MHz/2200×1125 조건에서120개 commit을
요구한다. `hardware_target`은 현재 실행 불가이며 software 결과로 대체하지 않는다.

[사용·실행 경계](adas-camera-module-rtl.md) ·
[공개 SDK 재현 예제](../examples/adas_module_rtl_workflow.py)

## SIL-to-HIL bridge

The project-scoped `/api/v1/hil-bridge` routes add immutable bindings, live readiness,
preview/execution and sealed causality/export. See [HIL Bridge](hil-bridge.md).
Existing Network Lab and vECU tests/results retain their revisions.
