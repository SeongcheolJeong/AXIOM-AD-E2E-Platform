# AD E2E backend

Working local implementation of versioned scenarios → durable execution jobs → adapter execution → trajectories and metrics → immutable run evidence → current requirement coverage. The browser, Python SDK and agents use the same REST API. Schema9 durable remote recovery update: **2026-09-13**.

## Run

From the repository root, with Python 3.11 or later (verified here with Python 3.12):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e 'backend[test]'
.venv/bin/python -m uvicorn ade2e.main:app --host 127.0.0.1 --port 8000
```

Start a separate execution worker in another terminal, from the same repository root and with the same environment:

```sh
.venv/bin/python -m ade2e.worker
```

The project launcher starts API, worker and UI together. `POST /jobs` saves a queued job and returns 202; it does not wait for engine computation. Workers share the same host, SQLite DB, managed files and installed adapter settings. `--once` processes at most one job; `--poll-interval` and `--lease-seconds` configure polling and ownership. Shutdown signals stop new claims and allow current work to finish; they do not prove that a remote engine stopped. Multiple local workers use atomic claims and adapter reservations; this is not a distributed scheduler.

On an empty scenario catalog, API startup creates eight original examples and actually runs the reference adapter eight times. The example set currently produces five passing and three failing runs; these are computed results, not dashboard fixtures. Startup preserves existing catalogs and never re-seeds them. The worker never seeds examples. Set `ADE2E_SEED_EXAMPLES=0` to begin empty.

API: `http://127.0.0.1:8000/api/v1`; Swagger `/docs`; ReDoc `/redoc`; OpenAPI `/openapi.json`; agent guide `/llms.txt`; tool discovery `/api/v1/agent/tools`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADE2E_DATA_DIR` | `.data`, relative to process working directory | Shared API/worker SQLite DB and managed artifact/recording root |
| `ADE2E_DATABASE_URL` | `sqlite:///<data-dir>/platform.db` | SQLAlchemy database URL; durable jobs currently require SQLite |
| `ADE2E_API_TOKEN` | unset | Optional bearer token for every data API under `/api/v1` except health |
| `ADE2E_SEED_EXAMPLES` | `1` | Set `0`, `false` or `no` to disable initial examples |
| `ADE2E_CORS_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | Comma-separated allowed browser origins |

Public health endpoints, API schemas and documentation do not reveal workspace data. If a token is configured, clients send `Authorization: Bearer <token>` for scenario, job, worker, execution-resource, run, recording, artifact, adapter, requirement and discovery endpoints. Keep the server on loopback unless a deployment supplies transport security and an appropriate access boundary. This is a shared local workspace, not production tenancy, SSO or RBAC.

`ade2e.database.Settings` and `ade2e.main.create_app(settings, registry, sensor_registry=None, runtime_registry=None, evaluator_registry=None, stack_registry=None)` provide programmatic configuration. SQLite uses foreign keys, WAL and a 30 s connection timeout. Tables store scenario revisions, snapshots, relational run/artifact/requirement keys, explicit requirement links and unique idempotency keys. Schema 9 preserves existing data and managed files, and explicitly rebuilds the SQLite queue parent to support `kind=simulation|perception|evaluation|stack_replay|recovery` and nullable simulation-only subject columns. This migration is not purely additive: old job IDs, submission sequences, child foreign keys and sequence high-water marks are retained; DDL, data and schema-version publication commit together, with a foreign-key integrity check before commit. Rig revisions, pinned recording bindings with channel foreign keys and run-recording lineage join existing suite, campaign, baseline, comparison and queue tables. SQLite initialization takes the writer lock before schema inspection, with bounded retries for concurrent initial WAL setup. Unknown and unversioned nonempty databases are rejected. Back up the database and all managed artifact/recording directories together before upgrading.

## Durable execution

The primary execution routes are `POST /api/v1/jobs`, `GET /jobs`, `GET /jobs/{id}` and `POST /jobs/{id}/cancel`. Worker connectivity is visible at `GET /workers`; external engine reservation/quarantine is visible at `GET /execution-resources`. Explicit `POST /execution-resources/{adapter_id}/reconcile` records the exact prior job and an operator's confirmed cleanup. All these route suffixes use `/api/v1`. See [the user workflow](../docs/execution-jobs.md) and [full API/state contract](../docs/jobs-contract.md).

Submission freezes scenario revision, effective seed/parameters and adapter version. Later edits and restarts do not change those inputs. Claim and dispatch intent commit before the engine call, which runs without an open DB transaction. Artifact bytes are prepared in attempt-owned staging outside the writer transaction; publication requires a matching owner token and unexpired lease. Job result, Run, artifact references and legacy run-key mapping commit together. Expired owners cannot renew or publish. API and worker lease reapers mark expired work `interrupted` with an uncertain outcome; queued jobs remain available after restart, and interrupted work is never automatically re-executed.

Queued cancellation never invokes the engine. The core can also acknowledge cancellation before adapter invocation; the dispatch-intent audit timestamp may already exist in that case. The reference adapter checks cooperative cancellation during computation. `cancel_requested` is only a request for plugins without a cancellation hook. If their complete result arrives, it remains accessible with `cancel_not_honoured`; the platform does not claim that the remote engine stopped.

Within simulation jobs, only the exact built-in reference class can execute without an exclusive resource reservation. Other simulation adapters are gated by adapter ID inside the workspace. Uncertain external outcomes remain `reconcile_required`: stop the former dispatcher and independently confirm engine cleanup before submitting a reconciliation note and `confirmed_stopped: true`. Health `ready` does not establish that cleanup. Reconciliation permits later dispatch but preserves the prior job's uncertainty. Separate workspaces sharing one remote server require a future engine-side/distributed ownership contract.

`POST /runs` remains a compatibility endpoint. It uses the same durable submission, claim and resource rules, then executes inline or waits for the existing worker to produce a Run. New UI and SDK workflows submit jobs and poll their IDs. A client timeout does not cancel, retry or establish an engine outcome.

## Versioned suites and regression evidence

`register_suite_routes` and `register_comparison_routes` expose authenticated `/suites`, `/campaigns`, `/baselines` and `/comparisons`. Suite revisions pin effective scenario revisions, stable case keys, requirement definitions, expected units and evaluator hashes. Campaign submission and every member job commit atomically through the existing queue insertion helper; workers and leases remain the only execution path. Campaign cancellation updates all members in one transaction and preserves terminal evidence. Suite definitions are bounded to 8 MiB.

Campaign reports evaluate actual run metrics with pinned criteria. They retain every case/check in their denominators, separate lifecycle from verdict, and distinguish failed-but-incomplete evidence. Completed-run content is checked against recorded input/output hashes before evaluation; separate artifact bytes remain verified by downloads. Baselines seal exact canonical report bytes in the database (16 MiB limit), including failed and incomplete terminal campaigns. Comparison captures seal the source baseline, candidate report and comparison policy (32 MiB limit). Hash/size, full display metadata and source lineage are checked on reads. These captures are not approvals.

Live comparison uses the union of stable case keys. Changed inputs, criteria, evaluator, adapter ID/version, units, fidelity or result schema prevent an automatic regression/improvement classification. Null TTC never becomes an invented numeric delta. Use [the test suite workflow](../docs/test-suites.md), [suite API contract](../docs/suites-contract.md) and [immutable comparison contract](../docs/comparison-evidence.md). Creation keys are scoped separately to campaigns, baselines and comparisons; retry the same endpoint/key/body after a lost response.

## Imported recordings

Authenticated recording endpoints register original MCAP bytes separately from run artifacts. See [the API contract](../docs/recordings-contract.md) and [usage guide](../docs/data-recordings.md). MCAP 1.4.0 and the pinned LZ4/Zstandard codecs are reused behind a bounded framing/CRC validator; native upstream seek/summary statistics do not determine which messages exist.

Managed originals and concatenated payload objects live under `.data/recordings/`, with SQL indices for channel, exact uint64 time and stable file ordinal. Upload staging uses `.data/recording-staging/` and is cleaned after each request. `index.sha256` hashes the derived payload bytes, not SQL rows. Per-message payload hashes are verified on reads; the manifest links the object to the original SHA and parser/index version.

Limits: 128 MiB upload, 32 MiB individual records/expanded chunks, 256 MiB total parsing volume (outer plus expanded records), 100,000 messages, 250,000 records, 2,048 channels, 1,024 schemas, and 1,024 metadata records / 2 MiB metadata. JSON/schema previews are bounded to 64 KiB, opaque prefixes to 64 bytes. These bounds do not imply process RSS remains below the same numbers. NONE, single-frame LZ4 and single-frame Zstandard chunks are supported; external dictionaries and concatenated compressed frames are rejected. Source attribution remains user-declared and decoded JSON is not schema/sensor validation.

## Sensor declarations and replay

`sensor_rigs` and immutable rig revisions preserve explicit frame names, source attribution, optional camera intrinsics and source-declared sensor→rig transforms. A recording binding pins a rig revision, decoder versions, channel identities, age limits and clock offsets. Later rig edits never change that binding. Authoring uses expected-revision guards; manifests have exact canonical exports with verified SHA/size, capped at 1 MiB each. Sensor declaration rows do not assert physically validated calibration.

`/recordings/{id}/sensor-frame` reads an exact original message ordinal and verifies managed source bytes. Built-in Foxglove JSON and ROS2 CDR1 decoders support specific image/point schemas; unsupported payloads remain opaque in the ordinary recording browser. The replaceable `SensorDecoder` protocol and `SensorDecoderRegistry.register` use trusted installed Python code. Core revalidates decoder/source identity, preview byte digests, bounded PNG CRC/decompression, point accounting/bounds, finite XYZ and optional intensity. Built-ins cap input at 16 MiB, images at 2 million pixels and source point clouds at 1 million points; preview sampling defaults to 20,000 points, maximum 100,000.

`sensor-bundle` selects latest log-time observations at or before one exact cursor after declared offsets. Binding revision is explicit; omission means unbound. Missing/future observations have no substituted preview. Stale observations keep their metadata but withhold previews, and absent age policy stays unknown. At most four channels and 24 MiB encoded preview data are returned. Calibration/frame mismatch or transformed float32 overflow disables rig view while preserving valid native inspection. All previews distinguish log/publish/capture clocks; none establishes acquisition synchronization or sensor accuracy. Source-declared rig translation is bounded to ±1,000,000 metres per axis.

`POST /runs/{run_id}/recordings` imports a hash-checked MCAP run artifact; `/recordings/{id}/origin` supplies verified run/artifact lineage only after that relationship exists. Imported source classification remains explicit. See the [sensor API contract](../docs/sensors-contract.md) for exact models and failure boundaries. Sensor/decoder tests exercise actual authored MCAP bytes and plugin contract failures; actual CARLA sensor acceptance remains a separate GPU-server gate.

The optional `backend[postgres]` driver is only a future integration dependency. Durable queue mutations explicitly reject non-SQLite databases; changing the URL does not provide a working PostgreSQL queue. PostgreSQL, cross-host scheduling, automatic retry, tenancy, and in-flight-aware orphan collection remain unimplemented and require migration/recovery and integration acceptance tests.

## Reference model and evidence scope

- Original, deterministic 2D straight-road object model. Actors are rectangles; continuous swept-box collision checks detect ego-target intersections between samples. Collision count records contact with the primary target once; there is no rigid-body impact response.
- Four scenario behaviors: 1.5 s cut-in, friction-limited lead braking, constant-speed pedestrian crossing, constant-speed lane following.
- 50 ms integration with trapezoidal longitudinal displacement; controller uses object truth, a delayed brake command, friction limit `min(8, friction × 9.81)` m/s² and an 8 m/s³ command ramp. The physical stop-at-zero clamp can produce a larger measured jerk, which is retained in results.
- `initial_gap_m` is the initial longitudinal bumper/box clearance. For pedestrians, `target_speed_mps` is crossing speed. The seed introduces ±0.15 m initial-gap variation and ±0.05 s trigger variation using a local PRNG. Weather, time of day and map name are descriptive metadata; **only explicit friction** changes road dynamics. No camera, LiDAR, radar, rendering, perception model, steering or calibrated vehicle dynamics is implemented by this adapter.
- TTC is the first future intersection of ego/target axis-aligned boxes under instantaneous constant relative velocity. It includes lateral crossing velocity. `null` means there is no predicted intersection; it behaves as positive infinity only for the `min_ttc_s` predicate.
- Five computed metrics: collision count; minimum TTC; peak deceleration; peak jerk; distance. Default predicates (0 collisions, TTC ≥1 s, deceleration ≤6 m/s², jerk ≤15 m/s³) are **illustrative** and do not constitute a safety or certification standard. Distance ≥0 is informational.

A successful adapter result must start at time zero, have strictly increasing finite timestamps, reach the requested duration, include all five metrics, and report predicates consistent with their values. Incomplete or contradictory results are execution errors, never passes. Submission preflight rejects unsupported input with 422; a worker-side rejection is retained as a failed job. Neither substitutes another engine. Once adapter execution is attempted, runtime failures, remote timeouts/HTTP failures and malformed result evidence normally produce an error Run published with the failed job; server logs carry exception details. Publication failure can leave a failed job without a Run, and lease expiry retains an interrupted job. Such execution attempts invalidate older baseline evidence. Retrying the same endpoint/key retrieves the saved intent instead of resubmitting a possibly completed simulator job. `AdapterExecutionError` expresses this boundary; it is distinct from `AdapterInputError`/`AdapterUnavailable`.

## Hashes, revisions and artifacts

Scenario edits increment revision and preserve prior documents in `scenario_revisions`; empty patches leave the revision unchanged. Clones receive independent IDs and revision 1. Runs embed the effective immutable scenario snapshot, including seed and execution-only overrides; overrides never edit the scenario.

Canonical JSON uses sorted keys, compact separators, UTF-8 and rejects nonfinite numbers. `input_hash` is SHA-256 of `{scenario, adapter_id, adapter_version}`. `output_hash` is SHA-256 of `{trajectory, events, metrics, model_fidelity, artifact_hashes}` where each adapter byte artifact contributes its SHA-256 in returned order. Wall-clock timestamps, runtime, run IDs and managed artifact paths are excluded from the output digest. Thus repeated execution of an unchanged reference snapshot produces identical output hashes. Revision/name metadata changes the input hash even if trajectories remain identical.

Each run creates a managed JSON evidence artifact; plugins can supply additional bytes. Paths are generated from random server IDs, never names supplied by a client/plugin. Artifacts record size, SHA-256, MIME type, source and download URL. Downloads verify the path remains in the managed directory and the bytes match their recorded digest. A downloaded stored evidence artifact omits its own metadata to avoid a circular self-hash; `/runs/{id}/export` returns the complete current run manifest including all artifact metadata. Stored bytes and exports are downloadable through authenticated API requests.

An attempted failure can raise `AdapterExecutionError(message, artifact_payloads=[...])` to preserve diagnostic bytes. `AdapterFailureEvidence` permits at most100 artifacts, each20MiB and32MiB in total. Core clones and revalidates these models; invalid diagnostics are withheld while the failed attempt remains recorded. Valid diagnostics use the same managed artifact publication, SHA-256 and rollback path as successful output, and their hashes enter the error Run's output hash. The Run remains `error`, with empty metrics and trajectory; diagnostics never become a complete validation result. Abrupt worker death or loss of the remote response can still prevent diagnostic bytes from reaching core.

Artifact writes are local filesystem writes plus a DB transaction, not a distributed atomic object-store transaction. Job staging lives in unique attempt directories under `artifacts/.staging/`. The fenced publication transaction promotes staged files without overwriting existing paths, and tracks final files for rollback. Partial-write failures, flush/commit failures, rollback and session close remove files owned by the failed operation; successful commits retain final bytes. A released savepoint transfers file ownership to its parent transaction. Existing committed artifacts are never included in rollback cleanup. An abrupt process/host failure around commit can still leave unreferenced staging/files; collection that safely accounts for in-flight attempts and production retention policies are future work.

## Idempotency and coverage

`POST /api/v1/jobs` requires `Idempotency-Key` (1–200 characters); `/runs` keeps the optional header for compatibility. An identical normalized request returns the original intent, even if the scenario has since been edited. Reusing a key with a changed body returns 409. **Keys are endpoint scoped:** retry the same endpoint, key and body after a lost response. Sending the same key to `/jobs` and `/runs` creates separate execution intents. A new intentional execution needs a new key. Atomic SQLite claims arbitrate local workers, but do not guarantee exactly-once remote side effects after process/network failure. Unknown outcomes are retained and quarantined instead of automatically retried.

Requirements use explicit scenario IDs, or a dynamic category selector when IDs are empty. An entirely unlinked requirement remains untested. For each linked scenario, the eligible execution job with greatest `submission_seq` determines current evidence, regardless of completion order. A newer running, failed, interrupted or cancelled-after-invocation job blocks an older pass. Queued jobs, known preflight rejection and cancellation confirmed before adapter invocation do not claim new evidence. Scenarios without eligible jobs retain the latest legacy Run fallback. A selected Run must be completed, match current scenario revision, parameters and seed, and include the requested metric. The service never falls back after a revision change or parameter variant. Any valid failing predicate produces `failed`; all linked current baselines passing produces `passed`; absent or incomplete evidence produces `untested`. A failed requirement can still have incomplete scope, so inspect `evidence_count` and `linked_scenario_count`. `covered_requirement_count` counts fully evidenced scopes, including failures.

Overview pass rate is 0–100 and includes all persisted runs; errors are not passes. Each category/weather cell distinguishes `total` registered scenarios from `tested` scenarios with a completed latest current-baseline run. `passed` and `failed` split those tested scenarios; `untested` includes missing runs, execution errors, stale revisions and latest parameter/seed variants. The counts satisfy `total = tested + untested` and `tested = passed + failed`, without falling back to older runs. Coverage and requirements share the same current-baseline predicate. These cells describe catalog dimensions, not validated weather/sensor performance.

## Recorded-image perception

`/api/v1/perception` exposes model artifact upload, immutable model declarations/revisions, installed runtime discovery, typed inference jobs and saved comparisons. The existing `/jobs` endpoints retain the simulation view of one shared queue. Perception jobs have no placeholder Scenario or driving Run and never enter driving requirement coverage.

Submission requires an endpoint-scoped `Idempotency-Key` and explicit model revision, runtime, camera decoder version, recording/channel and unique file ordinals. The worker claims only installed available runtime versions. Inference runs outside database transactions through `ade2e.inference_runtime.InferenceRuntime`; ONNX/graph imports live in the optional adapter package. The registry uses the `ade2e.inference_runtimes` entry-point group or explicit `RuntimeRegistry.register`.

Each frame persists `running` and `started_at` before decoding/inference. Validated predictions and the exact decoded input PNG commit independently with lease checks before and after publication. Counts retain `total = running + completed + failed + not_attempted`. Cancellation or worker loss preserves completed frames and turns an in-flight frame without a published prediction into an explicit failure; genuinely unstarted inputs remain `not_attempted`. A successful empty detection list is distinct from any failure. PNG copies are bounded to 128 MiB per job; managed model and perception image directories must be included in workspace backups.

All terminal outcomes support a canonical immutable export. Saved comparisons embed both terminal source manifests, disclose model/threshold/runtime changes and compare prediction content only when original identity and decoded PNG bytes match. They do not calculate ground-truth accuracy, calibrated probability or driving safety. Saved PNG downloads verify size and SHA-256. Prediction-content hashes exclude durations/timestamps; separate payload and terminal hashes protect those metadata. The native runtime is trusted local code and is not a multi-tenant security sandbox.

See [the perception API contract](../docs/perception-contract.md) for exact fields and extension limits. Commercial deployment and actual CARLA GPU-server acceptance remain open.

## Replace or add an engine

Implement `ade2e.adapters.SimulationAdapter`:

```python
from ade2e.schemas import Adapter, Scenario, SimulationResult, ArtifactPayload
from ade2e.adapters import Registry, AdapterInputError, AdapterUnavailable

class MyAdapter:
    def metadata(self) -> Adapter:
        ...  # id, version, capabilities, availability, limits and source/license

    def execute(self, scenario: Scenario) -> SimulationResult:
        ...  # all trajectory frames, computed metrics, events, fidelity, bytes

registry = Registry()
registry.register(MyAdapter())
# create_app(Settings(...), registry)
```

`SimulationResult.artifact_payloads` accepts `ArtifactPayload(name, kind, media_type, content=bytes)` with at most 100 artifacts and 20 MiB per artifact. Core owns storage and hashing. Avoid unbounded sensor dumps; create compact representative captures and external dataset integration separately.

For auto-discovery, package a no-argument factory/class with this entry point:

```toml
[project.entry-points."ade2e.adapters"]
my_engine = "my_package:MyAdapter"
```

Plugins are trusted installed Python code, not sandboxed uploads. The registry checks metadata and permits explicit replacement with `registry.register(adapter, replace=True)`. Unavailable placeholders can be replaced without the flag. Entry points replace matching placeholders; choose unique IDs to avoid collisions. The separate `integrations/carla` package connects through this boundary; core does not import CARLA.

Plugins may add `execute_cancellable(scenario, should_cancel)`, preserve ownership-loss exceptions, and acknowledge actual cooperative stop with the adapter cancellation signal. Existing `execute` implementations remain valid. The CARLA remote bridge currently has no cooperative cancellation hook; actual engine/sensor behavior and cleanup still require acceptance on a configured dedicated GPU server. Local queue and protocol tests do not establish CARLA engine validation or calibrated sensor fidelity.

## Verify

```sh
.venv/bin/python -m pytest backend/tests -q
```

Tests cover real seed computation, deterministic digests, parameter/friction/delay sensitivity, metadata scope, malformed input, missing references, revision persistence, idempotency conflicts/concurrency, unavailable adapters, plug-in bytes, incomplete output rejection, artifact integrity/path boundaries, bearer authentication and latest-baseline requirement coverage. Queue tests exercise immutable submission, lease/cancellation fences, publication rollback, schema migration, and separate-process claim/restart/worker-loss behavior with test adapters. Suite/capture tests additionally cover historical input and criterion pinning, atomic partial-batch rollback, concurrent idempotent retries, optimistic revisions, whole-campaign cancellation, killed member workers, complete denominator accounting, same-context regression policy, immutable bytes, tampered metadata/content, and schema 3→4 preservation. TestClient uses isolated temporary databases and storage. Current execution results and remaining acceptance gates are recorded in the repository status documentation.

For the sensor slice, run `.venv/bin/python -m pytest backend/tests/test_sensors.py backend/tests/test_sensor_decoders.py backend/tests/test_recordings.py backend/tests/test_recording_traversal.py -q`. Tests include schema 4→5 preservation of all existing table content and managed payload files. Run the root browser/SDK workflows before claiming the UI integration works.

Perception tests distinguish explicit contract fakes from actual optional-runtime inference. Core checks include pinned source/model identities, plugin output validation, real process kill with partial evidence, interrupted-frame accounting, atomic file rollback, immutable comparisons, authentication and schema 5→6 parent-table migration.


Schema7 adds independent immutable dataset PNGs, reviewed annotation/taxonomy
revisions and `kind=evaluation` on the existing execution queue. Image hashes,
terminal inference manifests, full selected split membership, explicit class
mapping and evaluator policy/package versions are pinned. Missing, failed,
changed or unreviewed images withhold aggregate metrics. The optional
`integrations/coco` package runs official pycocotools in a bounded cancellable
child; core never imports that engine. See [the dataset/evaluation contract](../docs/datasets-contract.md).

Targeted checks: `.venv/bin/python -m pytest backend/tests/test_detection_evaluations.py backend/tests/test_queue_migration_v6.py backend/tests/test_queue_migration_v7.py -q`.
These distinguish explicit fake contract reports from actual optional native
COCO arithmetic and exercise atomic submission/publication, full denominators,
PNG/source/manifest integrity, cancellation/ownership and schema6→7 preservation.
Native arithmetic on authored fixtures does not establish real-world accuracy.


## Recorded sensor stack replay

`GET /stack-components` exposes replaceable perception, policy and controller components. `/stacks` stores immutable component versions, parameters and kernel metadata with optimistic revisions and canonical export hashes. Native components register through `ade2e.stack_components` entry points or `StackRegistry.register`; engine-specific SDKs are absent from this kernel.

`/stack-replay-sources/{recording_id}` discovers exact supported LiDAR and `axiom.EgoState.v1` JSON channels. `/stack-replays` uses the existing durable queue with its own typed subject and endpoint-scoped idempotency key. Explicit ego ordinals, stack revision and sensor binding revision pin original message hashes and latest preceding aligned LiDAR selections. Original log, publish and capture clocks remain distinct. Commands are recorded open-loop and never modify later observations or drive a vehicle.

Every tick records its started/completed/failed/not-attempted state. The worker decodes at most 20,000 complete valid points, applies only the explicitly declared RH sensor-to-rig transform, and saves the exact consumed XYZ float32 LE buffer. Empty bytes remain distinct from unavailable input. Missing, stale, incomplete or wrong-frame inputs, and absent transform declarations, force explicit unknown-stop evidence; observed geometry does not prove free space. The reference stack is an experimental flat-road geometric pilot, with declared coverage/ground assumptions and no object tracking or learned perception.

Point files are staged outside publication transactions, bounded to 128 MiB per job, and published under the same lease fence and rollback ownership rules. Terminal exports preserve all selected ticks, including partial cancellation/interruption, within 32 MiB. `/stack-replays/{id}/ticks/{index}/points` verifies saved bytes against size and SHA-256, including genuine zero-length buffers. Include `stack-replay/` in backups; abandoned staging cleanup after abrupt process failure remains an operator/retention concern.

Schema8 rebuilds the queue parent atomically to admit the separate `stack_replay` subject; simulation, perception and evaluation rows and their evidence stay unchanged. Tests cover real authored MCAP geometry, exact selectors, output sensitivity, full denominators, revision pinning, authentication, failed publication cleanup, process kill, and schema7-to-8 rollback/preservation. See [the stack contract](../docs/stacks-contract.md) and [stack workflow](../docs/stacks.md). These replay checks do not establish closed-loop CARLA stack execution or vehicle safety.


## Durable remote execution recovery

An optional adapter `execute_attempt(request, expected_store_id=...)` receives the reserved execution ID for both scenario-only and stack-controlled execution. `ExecutionAttemptRequest` does not change historical scenario/input hashes or the old three-field `PreparedRun` envelope. Adapters with `inspect_execution` and `retrieve_execution` expose `RemoteExecutionStatus` and `RemoteExecutionArchive` from `ade2e.execution_attempt`; these are read-only hooks and must never call the simulator again. The queue pins the remote store and exact request hash before dispatch. Engine packages supply the trusted codec; core independently validates correlation, bounded raw archive SHA/size, decoded metrics and any live artifact chain.

`GET /jobs/{id}/remote-execution` reports current verified remote status and whether retrieval is available. `POST /execution-recoveries` accepts `{source_job_id,note}` with a required endpoint-scoped `Idempotency-Key`; a common-queue `recovery` job retrieves outside DB transactions and publishes under its own lease. List/detail/cancel routes, terminal `/export`, and exact remote `/archive` share the normal API authentication. Both manifests and downloaded archive bytes are checked against stored hashes. Requests that find no sealed terminal archive report `not_available`; a partial/failure archive yields `diagnostics_only` and an error run without validation metrics.

The source job, original run, submission order and idempotent legacy response remain unchanged. A supplemental run has `recovery.source_execution_id` for immutable artifact correlation, while its artifact storage references use the new local run ID. Current requirement and campaign evidence resolve recovery within the original job's submission order; recovering an older attempt cannot displace a newer dispatch. Previously sealed baseline/comparison exports remain unchanged. Source recording imports retain this recovery lineage.

The `remote_execution_attempts` and `execution_recoveries` tables join the existing queue. Schema 8→9 rebuilds only the parent queue constraints transactionally; previous run bytes, child rows, foreign keys, IDs and sequence high-water marks are preserved. Backups must include `remote-recovery/` alongside existing managed data. Process-kill staging leftovers are not automatically swept; ordinary rollback removes only the current attempt's files. Recovery does not release an engine quarantine. `cleanup_status=confirmed` means acknowledged cleanup operations, not proof of an empty/restored world. Old executions without a durable binding are explicitly unsupported.

Targeted checks: `.venv/bin/pytest backend/tests/test_recoveries.py backend/tests/test_recovery_api.py backend/tests/test_queue_migration_v9.py -q`. These use an authored journal adapter, including source worker loss, no-resimulation recovery, original live artifact IDs, concurrent retrieval, lost leases, cancellation, tamper/rollback and schema preservation. Actual CARLA fault recovery is a separate acceptance gate.
