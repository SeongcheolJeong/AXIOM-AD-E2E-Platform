# AXIOM workspace frontend

The workspace uses React, TypeScript, Vite, Three.js, and Recharts. The normal launcher starts the API, a separate durable-queue worker, and Vite together. From the repository root, after setup:

```sh
.venv/bin/python scripts/dev.py
```

For frontend development alone, run these commands from `frontend/`:

```sh
npm ci
npm run dev
npm run build
```

The default API proxy targets `http://127.0.0.1:8000`. `AXIOM_API_TARGET` can point the Vite server at another API instance. Starting only the API and UI leaves newly submitted asynchronous jobs queued; start the worker in another terminal from the repository root:

```sh
.venv/bin/python -m ade2e.worker
```

API and worker must use the same `ADE2E_DATA_DIR` and adapter configuration. The platform queue worker is distinct from an optional CARLA HTTP worker. See the [repository README](../README.md) for full setup and the [queue contract](../docs/jobs-contract.md) for process and ownership boundaries.

## Execution queue and result workflow

`RunLauncher` submits `POST /api/v1/jobs` and opens **Execution queue** (`#jobs`). It keeps an `Idempotency-Key` for each unchanged request body during the launch dialog's lifetime, including a lost-response retry. A successful submission closes the dialog; a later intentional launch receives a new key. The UI does not resubmit work automatically.

The queue page polls job summaries and the selected detail, supports status filters and submission-order pagination, and shows worker heartbeats, lifecycle events, effective scenario snapshots, input hashes, and recorded ownership times. Counts describe the current loaded page; text search applies to that page. Poll failures keep only the selected job's last successful observation and mark it stale. No invented progress percentage is shown.

**Open result** opens the linked Run explorer replay, metric verdicts, and artifacts. `completed` means a result was published; it does not mean the test verdict passed. The existing `POST /runs` and Python SDK `run()` remain compatibility calls that wait for a result through the same durable execution path.

Queued jobs can be cancelled before dispatch. `cancel_requested` records intent, not a confirmed stop. The reference adapter supports cooperative cancellation; an adapter without that capability, including the current CARLA remote adapter, may return a result after cancellation was requested. `interrupted` jobs are not automatically retried. `outcome_uncertain` and resource cleanup notices remain visible; operator reconciliation is an explicit API action after independent cleanup confirmation, not a UI health-check shortcut.

## Test suites, campaigns, and saved comparisons

**Test suites** (`#suites`) is separate from the existing Validation requirement workspace. Authoring requires existing scenarios and scoped requirements. Each suite case has a stable key, a selected scenario revision, optional seed/parameter overrides, and 1–32 applicable requirement IDs. Cases can share a scenario while varying their seeds or parameters. The editor can select historical scenario revisions, and the saved definition exposes effective input snapshots, pinned requirement definitions/units, and hashes.

**Create revision** saves a complete new suite definition with an optimistic `expected_revision` guard captured when the editor opens. Background polling does not advance that guard. The suite revision selector keeps older definitions available; editing a definition does not rewrite old campaign evidence.

**Run suite** submits the pinned suite revision and adapter as a campaign. Unchanged submission retries retain the same idempotency key for the dialog's lifetime. The campaign page shows actual member job states, case/check evaluation, recorded values and expected units, input/criteria hashes, and links to job details or run replay. Completed jobs, fully evaluated cases, passing cases, and evaluated checks are separate counts with their full denominators. A campaign can contain known failure and incomplete evaluation simultaneously. **Cancel campaign** requests cancellation for its members and preserves terminal results; the remote cancellation limits above still apply.

After a campaign finishes, **Capture baseline** preserves its report with a required name and note. Failed and incomplete reports may be retained, and their verdicts remain visible. **Comparison baseline** compares a candidate from the same suite with that captured report. The live matrix and table show improvements, regressions, unchanged verdicts, changed conditions, unevaluated cases, and added/removed case keys. Reported deltas are candidate minus baseline for finite values in equal units; unbounded TTC has no numeric delta. Matching names or units alone do not establish comparability, and adapter-version changes remain explicit.

**Capture comparison** is available for a finished candidate. It saves the baseline, candidate, comparison report, policy, name, and review note. **Saved comparisons** displays only this frozen manifest and provides **Download comparison** for the server's exact stored bytes, with its SHA-256 shown alongside. The live comparison is labelled separately and is never described as immutable. Capturing a baseline or comparison is evidence retention, not release approval or sensor validation.

Campaigns and baselines are listed within the selected suite; campaign pagination follows submission order. Saved comparisons show the latest bounded capture list for that suite. Poll errors may retain only the same selection's last successful observation, with a stale-state notice. Evidence downloads use the same authentication path as execution artifacts. The [suite contract](../docs/suites-contract.md) defines the API and comparability rules; authoritative fields are also in OpenAPI.

## Recordings, decoded sensors, and rig declarations

Data catalog retains both **Execution artifacts** and **Recordings**. The MCAP explorer preserves channel identity and exact nanosecond timestamps, supports cursor-based inspection and complete chronological sample windows, and shows bounded JSON or opaque bytes with declared provenance. Imported container data is not automatically calibrated or validated sensor data.

Within a recording, **Raw messages** retains the original explorer. **Sensor replay** queries decoder capabilities from exact encoding/schema matches and uses one exact log-time cursor for camera and LiDAR. Supported camera payloads become PNG previews; point clouds become explicit XYZ float32 little-endian buffers, with optional separately recorded intensity. The browser checks preview sizes and SHA-256, renders actual decoded data, releases image object URLs and Three.js geometry, and cancels superseded requests. A response for an older recording, channel selection, binding revision or cursor never becomes the new observation.

Every panel shows sample age, freshness policy, source frame, original encoding and decoder version. Future samples are not substituted; stale frames are withheld. An absent age threshold remains unknown freshness. The original log, publish and capture clocks remain separate. **Frame steps** advances to actual next recorded times at up to four cursor changes per second; it is not real-time video playback. Point counts distinguish source, rendered, invalid and sampled-out points. Color scales describe only the displayed sample. **Inspect original sample** addresses an exact file ordinal, including equal-timestamp duplicates, independently of the cursor's freshness policy. Its download returns the original message payload, not a complete MCAP file.

**Create sensor rig** registers source-declared sensor keys, frame IDs, coordinate convention, optional static sensor-to-rig transforms and camera intrinsics. Unknown transforms stay null; quaternion values are not silently normalized. **Create rig revision** uses an optimistic guard and preserves older immutable revisions. **Bind recording** pins one rig revision, exact channel IDs, decoder IDs/versions, optional age thresholds, and declared log-time offsets. Saved bindings do not move when the rig changes; the replay binding selector can load an older revision or explicitly choose unbound native decoding. Rig and binding downloads preserve the server's exact stored manifest bytes and hashes. Authoring does not use an idempotency-key contract: resolve a lost create response by refreshing the registry before repeating it.

**Declared rig frame** applies the recorded static transform only when the server reports matching sensor kind/frame, known convention, known transform and finite rendering range. Native coordinates remain inspectable independently. The coordinate readout and point geometry use the same declared rotation and translation. Rig registration checks representation and mathematics, not physical calibration accuracy. Camera projection, rectification, ego-motion compensation and physical acquisition synchronization are not implemented by this viewer.

An `application/mcap` execution artifact provides **Open as recording**, which registers the artifact through the API and opens that recording in the catalog. Its execution-origin panel preserves the producing scenario revision, seed, adapter version, fidelity and hashes, with a link back to the run. Origin linkage does not turn the payload into verified sensor data. See the [sensor API contract](../docs/sensors-contract.md) for decoder, timing, binding and manifest limits.

The backend's additive schema 5 migration retains existing scenarios, runs, recordings, jobs, suites, campaigns and captured evidence while adding sensor rigs, bindings and run-recording lineage. This remains a local development platform: the queue does not establish distributed execution guarantees, actual CARLA acceptance, sensor fidelity, or commercial release readiness. Current evidence and remaining gates are maintained in [status](../docs/status.md) and [roadmap](../docs/roadmap.md).

## Locally bundled fonts

The application bundles these exact Fontsource packages. Font requests are served from the same origin as the application; there are no runtime Google Fonts or Google font-file requests.

| Font family   | npm package                 | Version | Upstream font project                   | License                   |
| ------------- | --------------------------- | ------- | --------------------------------------- | ------------------------- |
| DM Sans       | `@fontsource/dm-sans`       | 5.3.0   | https://github.com/googlefonts/dm-fonts | SIL Open Font License 1.1 |
| Manrope       | `@fontsource/manrope`       | 5.3.0   | https://github.com/sharanda/manrope     | SIL Open Font License 1.1 |
| IBM Plex Mono | `@fontsource/ibm-plex-mono` | 5.3.0   | https://github.com/IBM/plex             | SIL Open Font License 1.1 |

Fontsource distribution source: https://github.com/fontsource/font-files. Package metadata and lockfile pin the distribution versions. Original copyright notices and complete license texts are preserved in `public/licenses/`; Vite includes those files in production builds.

The Latin subsets are bundled for the workspace interface. Other scripts, including Korean in user-authored scenario names, use the operating system's sans-serif fallback. Font family names remain `DM Sans`, `Manrope`, and `IBM Plex Mono`.

## Typography and responsive layout

Essential body text and controls use at least 12 CSS pixels. Supporting labels use at least 11 pixels. Breakpoints change grid structure, wrap controls, and permit scrolling in data tables without reducing text size. Form inputs use 16 pixels on small screens to avoid browser zoom during editing.
