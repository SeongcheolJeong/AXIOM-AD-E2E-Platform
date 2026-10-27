# Object Sim · Scenario v2

The Behavior scenarios workspace adds authored actor geometry and behavior without changing legacy Scenario v1 rows, revisions or run hashes. A user can select one of six templates, edit actor poses/dimensions/routes and actions, position a selected actor in the 3D schematic, save an immutable revision, preview a fixed speed/seed sweep, prepare a selected experiment, explicitly submit it to the existing worker queue, then inspect its observed trace and criteria in Experiments. UI and agent clients use the same `/api/v1` routes.

The current native engine is optional **esmini v3.8.2, build 6400, official Linux CPU binary**. Configuration requires an explicit operator command. Missing engine configuration, changed adapter content or unsupported model/sensor controllers are rejected; there is no reference-engine fallback. CARLA Scenario v2 execution remains unsupported. The running CARLA server and its installed build were not changed by this work.

## Authored contract and limits

`scenario-v2/1` uses metres, seconds, radians and world x-forward/y-left/z-up. It supports an authored straight OpenDRIVE road with two driving lanes, up to eight actors, 1–30 seconds and a 0.02–0.2 second timestep. Exactly one actor is ego. Vehicle actors support speed, stop, lane change and timed path actions; pedestrians require explicit timed paths. Triggers support simulation time, actor distance and actor speed. The six templates are lane follow, cut in, emergency brake, pedestrian crossing, merge and lead stop. They are authored tests, not reconstructed road scenes or learned driving controllers.

Supported sweep fields are actor speed and initial x/y position, up to three axes, 64 configurations and 128 previewed jobs. Preview does not submit jobs. The current UI submits a selected run; all previewed runs can also be submitted individually through the Experiment API with idempotency keys. Changing the seed in these deterministic templates does **not** establish independent stochastic scene diversity.

OpenSCENARIO export is a bounded XML 1.2 subset. OpenDRIVE export contains our authored straight road. Import supports only lossless AXIOM-exported documents: it validates the pinned manifest and every XML node/attribute against the generated supported representation. Unknown elements, changed behavior, arbitrary external XML, external roads and DTD/entity declarations are rejected. This is not a claim of standards-wide import support or conformance.

## Native execution and evidence

An immutable experiment pins project, scenario ID/revision/hash, selected configuration hash, seed, adapter/evaluator versions, native source XML hashes, command identity and native runner content hash. The accepted Linux binary is pinned to SHA256 `7f31d9a52a1da6c9d062a89f15b714f004308eb0d3c007107f09cce85afbf619`; its official release archive SHA256 is `bf9bfd637a76910910a1d12b7e2584e3637342d89ef6b6f4bcc041af505f8711`.

The optional runner executes headless esmini and preserves original native CSV, engine log, generated OpenSCENARIO/OpenDRIVE, structured observed trace and the native response as downloadable files. Engine trace timestamps and actual actor geometry are used. The stop trigger can yield one observation after the requested duration; the original observation is retained and observed duration is reported explicitly.

Observers cover oriented-box collision, minimum clearance, constant-heading/constant-velocity projected box TTC, lane departure, maximum speed, acceleration and jerk, and goal arrival. Missing required fields and absent relative actors are unevaluated. A TTC with no finite projected intersection is unevaluated rather than represented as infinity. Criteria-free numerical observations remain `observed`; only defined criteria become `passed` or `failed`. Execution completion, evaluation completeness and criterion verdict are distinct. A successful native run can retain failed collision/jerk criteria.

This is **authored object kinematics**. The schematic is not photorealistic rendering, TTC is a diagnostic estimate, and these results do not validate camera/LiDAR/radar physics, road accuracy or driving safety.

## Operator setup

The optional adapter boundary is `integrations/esmini/src/axiom_esmini/runner.py`. It uses the separately installed engine and Python standard library. The core imports no esmini native library. Configure `AXIOM_ESMINI_COMMAND` as a JSON argv list, for example a local Python runner plus `--binary` and the native executable path, or an explicitly configured SSH Python runner. `AXIOM_ESMINI_RUNNER_SOURCE` can point to the identical local runner source for an installed distribution. The remote and local source hashes must match. No shell expression from a scenario is executed.

CPU runs need no GPU. The current handler does not claim acknowledged cancellation after native dispatch; queued cancellation and failures are handled by the existing queue. The native process has a bounded timeout, and replay after ambiguous failure is an explicit new attempt. No unknown processes are killed to acquire the engine.

Code, engine binaries, authored roads and standards are separate licensing categories. AXIOM adapter code follows the repository license; esmini code is MPL 2.0. These templates and road geometry were authored here. No competitor code, UI or assets were copied. Engine/standard publication rights and full commercial release gates remain separate.

## API

| Route | Purpose |
| --- | --- |
| `GET /scenario-v2/capabilities`, `/templates` | Supported authoring contract and authored templates |
| `POST /scenario-v2`, `GET /scenario-v2` | Create immutable revision 1 / list project-scoped scenarios |
| `GET /scenario-v2/{id}?revision=N` | Inspect exact revision |
| `POST /scenario-v2/{id}/revisions` | Optimistic new revision; retain old content |
| `GET /scenario-v2/{id}/export?format=openscenario\|opendrive\|manifest` | Exact supported source export |
| `POST /scenario-v2/import` | Strict lossless supported XML import |
| `POST /scenario-v2/{id}/sweep-preview` | Fixed configurations/seeds and workload; no submission |
| `POST /experiments/preview`, `/experiments` | Freeze selected `kind=object_sim`, `adapter_id=esmini-object` |
| `POST /experiments/{id}/executions` | Explicit idempotent queue submission |
| `GET /experiments/executions/{job_id}` | Separate execution/evaluation/observer results and artifact downloads |

Persisted scenarios and experiments enforce the new project boundary. Viewer/Reviewer can read; Engineer/Admin can author and execute. Existing legacy resources are an operator domain and are not silently declared project-isolated.

## Acceptance · 2026-10-07

The actual native acceptance used these route handlers, the existing durable queue and worker, an isolated schema-17 database, and the Linux CPU engine over SSH. **Six templates × two authored speeds × three seeds = 36 actual runs completed.** The observer verdicts were 27 failed, 6 not evaluated and 3 passed; failed outcomes were preserved. Each run's native files and immutable receipt were downloaded through the API and verified against their content hashes. Speed changes affected actual traces; different seeds produced identical deterministic traces within each configuration.

Five focused tests passed for strict XML roundtrip/rejection, bounded sweep/reference validation, oriented-box observer geometry/missing inputs, immutable/idempotent revision APIs and role/project enforcement. The frontend build passed. These scoped tests and native execution do not substitute for full commercial release acceptance.

The built internal preview at `127.0.0.1:8020` also passed a normal browser workflow
in a temporary standard Chrome development context: Workspace settings connection,
six-template selection, a 3D road click updating the actor pose form, action/pose
editing, immutable save and XML export, six-run workload preview without dispatch,
explicit selection/submission, actual esmini completion, native timeline scrubbing,
sealed receipt and CSV downloads with byte size/SHA-256 verification. This created
the retained execution `job_a22a6f84c21c423b961bf4a071217f06`; it is a separate
browser-authored run, not an additional independent stochastic trial. The same
workspace showed the existing actual 30-second CARLA receipt's last rear-camera
frame as 640×360 native pixels and verified its image/receipt download hashes.
That inspection dispatched no new GPU capture. The user Chrome extension connection
was blocked by `ERR_BLOCKED_BY_CLIENT`; its screenshot was retained and user
browser policies were not changed. Test-coordinate initialization and a strict
locator assertion were corrected in the test harness; the original attempt
evidence and successful native execution remain retained.

Browser evidence/script/screenshots are under
`.runtime/internal-platform/ui-acceptance/object-sensor-browser.json`,
`object-sensor-browser.mjs`, `object-author.png`, `object-native-evidence.png`
and `sensor30-gallery.png`. The script uses the existing `e2e` dependency and
owned 8020 preview; it starts no app server and reads the private bootstrap token
only into memory. `--resume` inspects the retained execution without a second
simulator submission.

Evidence: [native acceptance manifest](validation/object-sim-native-acceptance-20261007.json). The isolated complete database and original native files are retained under the evidence directory recorded there. Reproduce with `integrations/esmini/acceptance.py --directory <new empty directory> --command-json '<configured native argv>'`, `PYTHONPATH=backend/src`, and the project's Python environment. The script refuses an existing acceptance database and has no mock fallback.

Primary engine source: [esmini v3.8.2 release](https://github.com/esmini/esmini/releases/tag/v3.8.2), [esmini repository and license](https://github.com/esmini/esmini).
