# Configurable CARLA rig and LiDAR recipe: design record

Historical design: written on 2026-09-14 KST before implementation. The proposal and its original acceptance gates below are preserved as a dated design record; “current”, “proposed” and “next release” in that record describe that earlier state.

Current update, 2026-09-14: CARLA adapter **0.8.0**, immutable sensor configuration registry, **schema10**, context/2, native mount/readback evidence and UI/API/SDK/CLI are implemented and deployed. Existing context-free/context/1 inputs and three roof profiles retain their historical byte/hash behavior. See the [implemented usage and limits](carla-configurable-sensors.md), [current status](status.md) and [actual evidence](validation/carla-configurable-sensors-2026-09-14.json).

| Gate | Current evidence and remaining boundary |
| --- | --- |
| 1–2: core/API/migration and native contract | Implemented; full Python 1,934 tests passed with two existing warnings. Authored/fake-world tests remain distinct from actual CARLA. Schema9→10 preserves all 3,260 original business rows across 43 tables, all their columns and duplicate multiplicities, and 685 managed files; operational tables are separately checked. Exact evidence is recorded in the linked acceptance report. |
| 3: durability | Existing historical archives remain readable. **Actual context/2 disconnect, after-ACK process death and early mount-readback failure injection are not yet accepted.** Earlier-context fault proofs are not substitutes. |
| 4: UI/SDK and actual configuration | Same versioned API, immutable revision selection and validated readback UI are implemented. Full Chrome run: 63 passed and one stale version expectation failed; corrected expectation plus related cases: 13 passed, followed by a passing TypeScript/Vite build. These are overlapping scopes. Actual stationary and combined roll/pitch/yaw trials each retained 81 qualified pose observations. |
| 5: geometry qualification | **Open:** complete near/side/thin/low external-obstacle and observer-only semantic qualification. Actor attribute/pose agreement, a bounding box and zero reported positive surfaces do not prove visibility, clearance or absence of self returns. |
| 6: driving quality | Exploratory cruise completed but failed unchanged deceleration/jerk criteria (8.800123m/s² and 173.610865m/s³); close-lead also failed (7.870616m/s² and 157.169649m/s³), with 1 cruise / 42 controlled-stop / 37 hold commands. The two stationary passed results were 80 hold commands with throttle 0. They do not close this gate, gate5 or the commercial release gates. |

All four actual trials retained 80 commands and 81 pose observations. The stationary, rotated-stationary, cruise and close-lead diagnostics were conducted while full geometry qualification remained open; they are not acceptance of the complete gate5→6 sequence. Original points, criteria and failed results remain unchanged. The original design follows.

## Decision and evidence

Use a new `simulation-control-context/2`, the existing immutable rig registry for physical pose, and a small immutable **simulation sensor configuration** registry for a reusable LiDAR recipe. Keep the three context/1 profiles and their reader/validator/hash behavior intact. Presets become optional authoring templates for new configurations; adding a fourth hardcoded mount/profile enum is not the solution.

Actual identity evidence is now available in `.runtime/carla-self-hit-v2-2026-09-13/roof/analysis-1.json`: 8/8 accepted regular/semantic pairs, 54,448 regular points, 8,853 positive-near candidates, and 8,853 exact regular-to-semantic ego-ID associations for those candidates. The same diagnostic reports 33,928 direct semantic ego hits and 18,678 exact ego associations across the complete regular set. These denominators are distinct. It establishes self-return for the matched observations in this new diagnostic; it does not retroactively label every point in older runs, qualify a new mount, or prove free space. Semantic IDs stay observer-only. This slice does not delete points, add owner-ignore ray tracing, or change kernel1/2 algorithms.

Code boundaries verified at the time of the original design:

- `backend/src/ade2e/simulation_context.py`: context/1 compares the full recipe against one of three fixed profiles; each embeds `[0,0,2.2]` and identity rotation. The chosen rig sensor must match that exact mount/frame.
- `integrations/carla/src/axiom_carla/stack_bridge.py`: `_configured_lidar` writes all attributes and checks detached blueprint typed readbacks. Physical spawn still uses `Transform(Location(z=2.2))`. Those readbacks do not independently verify attached actor state, implementation of noise_seed, or calibration.
- `native_journal.py`: spawn intent/returned records retain identity and requested transform. A returned handle is not a coherent measurement-pose readback. Current diagnostic failure established that `get_actors()` can omit newly returned sensors until a new episode snapshot.
- `service.py`: context-free prepared objects retain exactly three fields; context/1 uses `prepared-simulation/2`. `simulation_input_hash` adds `simulation-input/2` only when a context exists.
- `execution_attempt.py`, `wire.py`, `remote_durable.py`, `durable_worker.py`, `spool.py`: recovery verifies the original request/version/store/hash and reconstructs partial checkpoints using the original worker version. New dispatch and historical reads already have separate guards.
- `frontend/src/components/carla/CarlaControlFields.tsx`: compatibility currently hardcodes roof pose/frame and supported versions. `sdk/python/src/axiom_sdk/client.py` passes a control dict through; `cli.py` reads the same bounded control JSON.

Alternatives considered:

| Option | Assessment |
| --- | --- |
| Widen context/1 attributes/mount | Reject: changes the meaning of existing supported profiles and exact reader checks. |
| Context/2 with only inline configuration | Can pin one run, but forces users to copy settings and lacks reusable configuration revisions and review history. |
| **Context/2 plus immutable configuration revisions** | Recommended: separate optical/sampling settings from rig placement, reusable authored assets, one bounded native execution path and exact provenance. |
| New custom CARLA adapter for each mount | Reject: duplicates execution/recovery ownership and makes ordinary configuration appear to be a new engine. |

## Public authoring and capability contract

Keep `/sensor-rigs` and its optimistic revision API unchanged. A rig sensor remains a source-declared `frame_id` plus sensor-to-ego translation/quaternion. It does not absorb CARLA blueprint fields, a vehicle collision model, or an implicit calibration approval.

Add endpoints under the **existing local API access/authentication policy**. This does not introduce platform-wide RBAC, tenant isolation or new authorization scopes in this slice; those commercial gates remain open:

- `GET /simulation-sensor-capabilities?adapter_id=carla`: installed contract IDs/versions, supported context/kernel versions, parameter JSON Schema, explicit bounds, mount rules, resource/evidence limits and limitations. No world mutation. A separate bounded preflight observes the remote installation; a local catalog alone never means the engine is ready.
- `POST/GET /simulation-sensor-configurations`; `GET /{id}?revision=N`; `POST/GET /{id}/revisions`; `GET /{id}/export?revision=N`.
- Create body: `{name, description, note, adapter_id, capability_id, capability_version, parameters}`. Revision body adds required `expected_revision`. Detail includes the complete normalized definition, revision, canonical definition/manifest hashes and exact export size/URL. Require Idempotency-Key for create/revise; replay the identical body/key before a revision conflict, reject different bodies with that key.

First capability: `carla-raycast-lidar/1`, with one complete typed parameter schema. An unknown capability, attribute, wrong type, boolean-as-number, nonfinite number or unsupported combination fails closed. No arbitrary dictionary is forwarded to a CARLA blueprint. The generic capability/registry interfaces live in core; CARLA imports and physical property application stay in the optional adapter package. Installed plugins must provide an explicit versioned schema and validator, not just claim a compatible display name.

New control selection (illustrative IDs):

```json
{
  "mode": "stack",
  "schema_version": "simulation-control-selection/2",
  "stack_id": "stack_example", "stack_revision": 2,
  "rig_id": "rig_example", "rig_revision": 3,
  "lidar_sensor_key": "lidar_front",
  "sensor_configuration_id": "sensor_config_example",
  "sensor_configuration_revision": 1
}
```

The legacy control shape without this discriminator is context/1 exactly. Reject mixed legacy `sensor_profile_id` and configuration-revision fields. Editing the current rig/configuration head cannot affect a submitted job. Show a reviewable full effective configuration before submitting, and keep the original body/key for retries.

## Bounded recipe and physical support

These are proposed engineering resource bounds, not hardware ratings or empirically accepted ODD limits. Freeze their version before the first actual test; do not tune them to make driving verdicts pass.

| Field | Proposed initial supported range |
| --- | --- |
| channels | strict integer 1..128 |
| lower/upper FOV | finite degrees, -89..89, lower < upper |
| horizontal FOV | finite degrees, 1..360 |
| range | finite metres, 0.1..200 |
| points_per_second | strict integer, at least `20*channels`; conservative per-frame allocation bound below |
| rotation_frequency / sensor_tick | fixed 20 Hz / 0 for this 20 Hz frame-aligned slice |
| atmosphere attenuation | finite 0..1 (declared modeled parameter) |
| general/zero-intensity dropout | finite 0..1 |
| intensity limit | finite greater than 0 and at most 1 |
| noise standard deviation | finite 0..1 metre (modeled Gaussian range-noise setting; not calibrated) |
| seed policy | fixed `effective_scenario_seed`; no second independent hidden seed |
| physical pose | RH ego metres, x/y within [-5,5], z within [0,5], finite unit quaternion; no automatic normalization |

The complete effective attribute map includes all existing 14 fields, including the effective noise_seed. Canonical numeric-to-blueprint-text serialization is explicit and versioned; new numeric values never rewrite old string recipes. Before execution require `channels * ceil(points_per_second * 0.05 / channels) <= 20000`, a conservative allocation guard against per-channel rounding, and preserve actual full point/count/byte checks. The point bound alone does **not** prove an entire run fits: up to 81 observations duplicate source data across raw XYZI, consumed XYZ, MCAP/base64 and trace/readback artifacts. Add a pure core `estimate_full_execution_evidence(context, scenario)` preflight helper with a documented conservative upper bound for the complete selected duration, per-artifact bytes, total artifact bytes and canonical public wire bytes. Include all N+1 frames, worst-case retained returns (never an assumed dropout/compression saving), exact base64 length expansion, source/trace/configuration metadata bounds and fixed overhead/reserve. Reject a recipe/duration when that estimate exceeds the declared capability, even if its per-frame point count is legal; some configurations will therefore be unavailable under current limits. Preserve the independent native actual-size checks and next-frame publication reservation because estimates do not authorize overflow. Do not cut a frame or raise the limit merely because a configuration generates too much evidence. Current 20,000 points/frame, 81 observations, 20 MiB individual artifact, 32 MiB total payload and 48 MiB wire bounds stay effective; fixed pre-dispatch next-frame reservation must include new readback artifacts. This new conservative admission rule applies to context/2, without retroactively changing context/1 hashes or historical read eligibility.

The mount range only bounds execution inputs. A sensor can still sit inside a collision mesh or see its own vehicle. Preserve those returns and explicit qualification limitations. No automatic placement correction, body mask, ground-plane inference, or hidden point rotation is permitted. LiDAR ray geometry/noise/dropout are simulator settings, not measured optical calibration. Camera intrinsics/distortion are outside this capability and must not be copied from another rig sensor.

Current stack input still supports one LiDAR, one ego telemetry source, RH ego coordinates, the existing flat-road constraints, and 2–4 seconds/20 Hz. Arbitrary rig orientation is a sensor pose, not permission to rotate the ego convention or bypass kernel pitch/roll/ground checks. Multi-sensor fusion, rolling acquisition/deskew, dynamic mounting and other frame rates remain explicitly unsupported.

## Immutable context and hashing

New context/2 contains:

- exact pinned stack snapshot;
- exact pinned rig snapshot and selected sensor key/frame;
- exact sensor configuration snapshot and its manifest hash;
- pinned capability identity/version/schema hash and the normalized complete effective sensor recipe including effective seed;
- `physical_mount_policy`, coordinate/clock policies, full-frame/evidence bounds and unchanged ego source policy;
- canonical context hash excluding only itself.

The mount has one authority: the chosen rig revision. The recipe has one authority: the chosen configuration revision. Derived effective fields duplicate neither ambiguously: reconstruct and compare them during preparation/read verification. Typed core helpers may use `PreparedSimulationContextV1 | PreparedSimulationContextV2` and `validate_prepared_context(value)`; keep the old class/alias and old serializers available so historic code paths do not silently adopt new defaults.

Recommended identity separation:

| Input | Serialization/hash rule |
| --- | --- |
| No context | Exact old three-field prepared payload and original scenario/adapter input hash |
| Context/1 | Existing `prepared-simulation/2`, `simulation-input/2`, `simulation-execution-request/1`, execution-attempt/1 bytes unchanged |
| Context/2 | New `prepared-simulation/3`, `simulation-input/3`, `simulation-execution-request/2`, execution-attempt/2 |

New result/failure codecs dispatch by exact request/context version. Context/2 may use `carla-execution-result/3` and `carla-execution-failure/3`; the outer archive/1 shape can remain because it already pins request hash, original adapter version, context hash and exact payload bytes. Do not regenerate an old archive under a new writer version. Old full-wire checkpoints and private checkpoint-blob rows must both remain readable; partial recovery chooses the encoder from the stored attempt/context, never the current adapter version. Old readers must refuse new contexts before sealing anything.

Use a new adapter release for this native physical capability. Its exact version is a release decision, not assigned by this plan. New dispatch requires that installed version; historical 0.5/0.6/0.7 inspect/retrieve keeps the existing explicit allowlist and original request hashes. A caller cannot put context/2 under an old adapter version or resubmit an old execution ID to get a new configuration. Recovery creates supplemental lineage as today, preserving the source Run/job and submission ordering.

## Native application and readback

The optional adapter declares `execution_context_capabilities()` and validates the effective context before mark_dispatched. Core must require this exact capability for context/2; no fallback to `execute`, context/1 defaults or reference simulation. Keep the same `carla` resource, durable attempt ID, OS engine lock, quarantine and cleanup obligations. Revalidate after remote acceptance before mutating the world.

For context/2, use a single context-derived spawn helper rather than a branch per preset. Apply every supported blueprint attribute and save requested text plus typed detached readback. Observe the spawned actor's available attributes separately; require requested retained values for fields the capability promises to expose, and never imply attributes prove internal RNG behavior.

Pose conversion is explicit. Let `H=diag(1,-1,1,1)`. The declared RH sensor-to-ego matrix `T_R` becomes native LH relative matrix `T_L = H T_R H`. Convert that rotation matrix to the installed CARLA native rotation representation in the optional package, reconstruct its matrix, and verify equivalence before spawn. Never copy yaw/pitch/roll signs ad hoc. Retain the original declared quaternion, derived requested matrix, native quantized spawn pose/matrix and numerical conversion policy.

At a coherent WorldSnapshot retain sensor/ego actor IDs, public parent metadata, snapshot/measurement world transforms and `inverse(T_ego_L) * T_sensor_L`. Reflect this relative matrix back to RH and compare to the requested mount using an explicit versioned readback tolerance (translation metres and rotation-matrix residual, not quaternion-component subtraction). Determine/freeze tolerances from native float32 precision and independent rotations/positions, not driving outcomes. Save residuals and the source/status of each observation. A bounding box and serialized parent are observations with known limitations, not collision-mesh or UE owner proof.

Lifecycle for the new path: all owned vehicles spawn → both vehicle physics configurations applied → configured sensor spawn before the bounded startup gate → coherent rest/sensor-mount readback → startup/physics validation → initial velocity requests → listeners/measurement frames → N commands/N+1 full observations. A missing actor-list entry immediately after spawn is not permission to forget a returned handle. Preserve required coherent snapshot refresh boundaries; any extra initialization tick is explicitly unmeasured. Keep context/1 ordering unchanged. Invalid/missing mount/readback fails before a driving command, with immutable failure artifacts and owned sensor-first cleanup. Callback drain/final process-exit evidence remains distinct from stream-completeness claims.

For context/2, the selected rig sensor frame ID must also flow through `recording.py` message `frame_id`, channel metadata, native source references and `simulation_evidence.py` checks. These currently hardcode `lidar_roof`; changing only physical spawn would create a mislabeled recording. Preserve the old functions/default paths for context/1 and pin an explicit source-serialization policy for the new path. Replay binding uses that exact frame and rig revision, without an implicit latest rig.

Raw callback XYZI remains untouched. Each new MCAP message is serialized once with its pinned frame ID and retained unchanged; historical recording bytes are never rewritten. Consumed XYZ is the exact full existing reflection-then-quaternion arithmetic using the pinned rig transform, with all source index order retained. Measurement pose readback qualifies the declared transform; it does not secretly substitute another transform into consumption. No tolerance is introduced for raw/consumed hash comparison.

Add a bounded `carla-sensor-configuration/1` artifact to every context/2 complete/partial native chain, including request/context correlation, effective recipe hash, rig/configuration manifest hashes, actual attribute observations, frame-tagged mount matrices/residuals and failure phase. For early failures, explicit not_observed fields are valid; completed runs require complete promised readbacks. Hash the artifact in the normal live manifest and checkpoint it before actuator dispatch. Core validates the full recipe, matrix relationships, actual/source frame links and exact raw-to-consumed transform. A detached requested value alone cannot satisfy actual-readback fields.

## Database, API and consumer changes

A configuration registry needs only new head/revision tables with immutable canonical JSON/manifest, expected_revision guards and endpoint-scoped idempotency records, following existing asset patterns. Proposed schema9→10 is additive: no execution_jobs rebuild, no old rows/files rewritten, no old rig schema changes. Existing jobs/recoveries continue to store full snapshots in their prepared payload. List/detail compound reads use the existing explicit read transaction helper.

Likely implementation seams:

- Core: new sensor-configuration schema/models/service/capability definitions; `database.py`, `main.py`, `simulation_context.py`, `execution_attempt.py`, `service.py`, `schemas.py`; version-aware source validation in `simulation_evidence.py`, `run_recordings.py`, replay provenance readers and recovery preparation.
- Optional CARLA: `stack_bridge.py`, `recording.py` and a dedicated pure pose conversion/application helper; metadata/preflight in `runner.py`/`remote.py`; strict new/historical branches in `wire.py`, `remote_durable.py`, `durable_worker.py`, `spool.py`, `version.py`. Legacy ground-truth capture stays unchanged.
- UI: reuse rig authoring and immutable revision pickers; add configuration authoring based on the same capability JSON Schema, full effective-input review and requested/detached/actor/measurement readback inspector. Remove hardcoded pose/frame compatibility only for context/2. Retain the legacy viewer branch. Capability unavailable/readback missing is a visible reason, not an enabled run button.
- SDK/CLI: new capability/configuration create/list/detail/revise/export helpers with explicit idempotency keys; keep `run`, `submit_job` and `--control-file` forwarding the exact selected schema. Export verifies exact manifest bytes. Update OpenAPI, agent tools and llms flow together.

No new scheduler, fake Run kind, hidden latest revision, source-point filter, semantic GT control dependency, or bespoke fourth-preset route is needed.

## Acceptance gates before deployment claims

1. **Core/API/auth/migration:** exact legacy JSON/hash fixtures for context-free and context/1; context/2 create/revision conflict and duplicate-body replay; rejects mixed shapes, unknown capability, malformed units/quaternion, wrong rig frame, unsupported seed/caps and oversized recipes. Fresh schema plus additive upgrade preserves every old row/file/hash. Prepared jobs remain pinned after registry edits.
2. **Math/native contract (mocked, labeled):** nonzero x/y/z, combined roll/pitch/yaw, 180-degree/yaw boundary, equivalent quaternion sign, signed-zero and float32 readback. Physical spawn matrix, callback-relative matrix and independently computed consumed XYZ agree. Wrong actual attribute/mount/parent/frame fails. Returned handles survive stale lists; visible identity conflict, lost destroy ACK, active callback and quota failure preserve uncertainty/quarantine.
3. **Durability:** disconnect after complete seal, after-ACK worker death and early mount-readback failure; exact complete/partial archive retrieval under the original identity, no resimulation, unchanged source history. Verify old 0.5 complete/partial and 0.6/0.7 archives through the new reader, not just locally constructed fixtures.
4. **Browser/SDK with real API:** author a new rig revision and non-preset configuration, inspect exact effective full settings, submit once, wait/cancel/export, open raw/readback evidence and replay it. Changing heads while a job runs cannot alter its input. Unsupported server and stale asynchronous UI responses cannot enable wrong submission. At least one actual configured sensor trial is required in addition to mocked browser tests.
5. **Actual geometry qualification before driving:** separately instrument the declared pose; preserve all three lateral near-ground band counts and complete raw return denominator. Test close external returns ahead and beside the bumper, a thin/low obstacle and self-body observations with semantic diagnostics observer-only. A formula, bounding box or absence of self hits does not qualify free space. Candidate front/low mounts must be evaluated, not preselected because they eliminate the failing detector output. A future authored configuration such as x=2.5m, z=1.5m and horizontal FOV=180 degrees is a physical diagnostic candidate only, not a fourth built-in preset or accepted placement. The reported actor-local bounding-box maxima x=2.4251096m and z=1.47969055m provide a geometric reference, not a collision-mesh, clearance or optical-visibility guarantee.
6. **Actual driving scope:** only after geometry/readback gates, run the original cruise and close-lead conditions with unchanged kernels/criteria and declared model assumptions. Record all decisions, latches, S-curve plan presence/absence, commands/readbacks, metrics and raw evidence. Completed/failed remains failed. A successful configurable sensor setup is independently useful even if S-curve/jerk/ODD gates remain unaccepted.

For every actual trial save original map/world/23-actor identity/settings/weather, owned cleanup ACKs, final fresh-snapshot comparison, supervisor exitcode/stderr, requested-vs-observed source identities and byte hashes. No auto reconciliation, engine restart, source rewrites or pass-driven tuning is authorized by this plan.
