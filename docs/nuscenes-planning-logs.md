# Original nuScenes annotation planning logs

This optional converter connects the already approved **isolated noncommercial
nuScenes research input** to the ordinary Recording → project source grant →
SceneSequence preview/freeze → ReplaySpec → Experiment queue. It does not make
nuScenes assets commercially available, create a sensor perception output, or
verify a learned policy or physical vehicle.

The converter lives in `integrations/nuscenes`, separate from the pinned VAD
runner. Reading original metadata requires only Python stdlib. MCAP writing uses
the platform's exact `axiom.PlanningEgo.v1` / `axiom.ObjectTracks.v1` schemas and
`mcap==1.4.0`; it imports no Torch, MMCV or nuScenes devkit.

## Original sources and conversion

The operator supplies an existing `v1.0-mini` data root, original mini archive,
the archive's acquired SHA-256, and an already selected `driving-input/1` JSON.
The converter rechecks the whole archive, six existing metadata tables and causal
CAN file. It reads annotation/taxonomy archive members into bounded memory;
it never extracts files into or changes the VAD data root.

Original sample/scene/LiDAR/ego-pose identities and timestamps must agree exactly.
Selected frames remain contiguous and their original timestamps become decimal
uint64 nanoseconds; no added frames or pose interpolation fill gaps. Original
publish/transport timestamps are unavailable: the derived MCAP log/publish clocks
explicitly copy sample capture time and are not original transport measurements.

Object centers/orientations are in the original global frame. Width/length are
mapped from the original `[width,length,height]`; yaw is the planar projection of
the original unit `[w,x,y,z]` quaternion. Full original Z/height/quaternion,
category/visibility and source rows remain in the lineage. These conventions are
defined by the [official data schema](https://github.com/nutonomy/nuscenes-devkit/blob/master/docs/schema_nuscenes.md).

Object velocity uses only the same instance's original previous/current world
centers and their actual time difference, bounded to 1.5 seconds. It is labelled
**derived annotation motion**, with source-row hashes and interval, rather than a
measured velocity. Missing history stays `vx_mps=null, vy_mps=null`; it never
becomes zero or uses a future annotation. The upstream
[`box_velocity()`](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/nuscenes.py)
can use centered/next differences, so this converter does not call it. Original
annotations are offline ground truth; this geometry controller is not a causal
sensor perception pipeline.

Ego pose comes from the exact original ego-pose record. Speed uses the latest
original CAN pose at or before that timestamp, at most 100 ms old, and preserves
the measurement time/age separately. The
[official CAN contract](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/can_bus/README.md)
defines `vel` in the ego vehicle frame. Reverse ego motion is unsupported here.

Original measured ego dimensions and footprint-center offset are unavailable.
The CLI therefore requires explicit reference dimensions and an explicit zero
offset declaration. The accepted example uses **authored 4.5 × 1.9 m** at the
original ego-pose origin. These values are a reference shape and must never be
described as the dataset vehicle's measured dimensions.

## Operator conversion and ordinary product ingress

From a host with the existing original data available:

```sh
PYTHONPATH=backend/src:integrations/nuscenes/src .venv/bin/python -m axiom_nuscenes_planning export \
  --data-root /absolute/existing/nuscenes \
  --archive /absolute/existing/v1.0-mini.tgz \
  --archive-sha256 ORIGINAL_ACQUIRED_ARCHIVE_SHA256 \
  --driving-input /absolute/approved-driving-input.json \
  --ego-length-m 4.5 --ego-width-m 1.9 \
  --declare-zero-offset-reference-footprint \
  --output /absolute/NEW_RESEARCH_CONVERSION_DIRECTORY
```

The output directory must be new. `planning-log.mcap`, retained source bundle,
conversion lineage and `import-proposal.json` have explicit bytes/hashes. Source
collection can also be streamed over authenticated SSH without GPU/model jobs;
`convert-source --source-bundle ... --output NEW_DIRECTORY` checks the collecting
converter's source hash against local code before writing the MCAP.

1. The operator uploads `planning-log.mcap` to the existing `POST /recordings`
   using the explicit `recording` metadata in the proposal.
2. The operator grants its exact ID/SHA to the intended project using
   `POST /project-recordings/grants` and a stable `Idempotency-Key`. Project tokens
   cannot grant themselves arbitrary legacy recordings. A grant gives access and
   preserves source restrictions; it does not clear commercial rights.
3. An Engineer opens **Log replay**, selects the granted recording, supported
   ego/object channels and exact ego ordinals. Set the original world frame and
   clock interpretation from the proposal, inspect missing inputs/rights, then
   freeze the preview's unchanged hash.
4. Pin a ReplaySpec and submit the ordinary `reference-planning-replay` experiment.
   Original replay, open-loop planning and reference bicycle feedback are distinct.
   Surrounding objects stay logged and nonreactive; camera closed loop is unsupported.
5. Inspect the reference timeline and preserve/download the full native trace and
   sealed receipt. Missing velocities leave TTC unevaluated, and discrete collision
   checks do not establish continuous collision avoidance or driving safety.

## Retained original provenance

`axiom.source_provenance` MCAP metadata uses string fields:
`schema_version=axiom.source-provenance/1`,
`use_scope=isolated_noncommercial_research`, `commercial_use=false`,
`source_dataset=nuScenes mini`, `converter_version=axiom-nuscenes-planning/1` and
`lineage_json` (canonical compact JSON ≤128 KiB). The summary pins the archive,
input/tables/CAN/converter hashes, geometry declaration and original denominator.

It references `original_lineage_metadata={name:"axiom.original_lineage",
field:"lineage_json",sha256:...}`. That second original metadata record retains
the canonical full original rows and conversion receipts (≤1.5 MiB). The digest
is SHA-256 of its exact UTF-8 `lineage_json` value. Both records live inside the
original MCAP and are preserved by the immutable source grant and `scene-sequence/2`;
there is no unexported external sidecar dependency. Full original MCAP SHA and
selected payload hashes remain independently checked.

## Actual retained-input acceptance · 2026-10-07

The original scene-0061 frames selected before VAD inference were converted from
the retained archive, whose SHA-256 was reverified. No data was redownloaded and
no GPU or VAD runner was started or changed.

| Original frame | World boxes | Causal derived velocities | Unknown velocities |
| --- | ---: | ---: | ---: |
| 1 | 78 | 67 | 11 |
| 2 | 86 | 77 | 9 |
| 3 | 94 | 85 | 9 |

Normal API import, explicit project grant, project Engineer preview/freeze,
ReplaySpec and shared worker produced `job_c5f928964f6544c29c791e14fa61a45e`.
Before the grant, the project could neither see nor read/preview this source.
The full result retained all boxes; control propagated to the next reference ego
state. All three TTC observations stayed unevaluated because of unknown object
velocities. The verdict remains `diagnostic_not_safety_validation`.

The same-origin ordinary browser workflow also completed source selection,
original MCAP SHA-verified download, exact sample inspection and freezing,
ReplaySpec authoring, shared queue execution and sealed result download. Its
separate job is `job_e202b09f98b248d5beda5096404ba180`. The final built UI was then
checked against that retained receipt without another execution or API write.
All three timestamps and bounded displayed/retained counts (20/78, 20/86, 20/94)
remained visible. Object labels begin hidden to avoid obscuring dense geometry;
optional short labels and hover titles preserve the full original IDs. The full
original object list remains in the downloadable native trace.

- MCAP SHA-256: `ee6c31754eee9adb7b16befdc7f826a97d4256f68c4f41f20c99bd3311a02deb`
- Sealed result SHA-256: `6710cb657cac0f7335b92755f4f4d05040f122f329255263cf6e8ef739ef8996`
- Isolated source/product evidence: `.runtime/nuscenes-planning-research-20261007`.
- [Compact original-source evidence](validation/nuscenes-planning-product-20261007.json)
  records source/table/converter pins, original denominators, scoped ingress and
  execution/artifact hashes. The ordinary browser path is recorded separately.
- Sixty related converter, exact-clock preview/freeze, source-grant, planning-replay
  and SDK checks passed. Thirteen of these are converter contract fixtures;
  the original-source acceptance above is separate. Operating data/services are
  not migrated or deployed.

Commercial road logs, measured ego geometry, sensor-derived tracks, learned-policy
A/B, reactive actors/cameras and safety/physical correlation remain separate gates.

## Log Sim presenter clip · 2026-10-09

`scripts/prepare_log_sim_road.py` reads the existing approved input, retained mini
archive, metadata and JPEGs locally or over an already authenticated SSH connection.
It creates a new directory with `road-log.mcap`, the selected driving manifest,
original planning conversion and an import proposal. It does not download data,
run a GPU model, alter the VAD input or modify the original dataset.

```sh
.venv/bin/python scripts/prepare_log_sim_road.py \
  --ssh EXISTING_AUTHENTICATED_DESTINATION \
  --data-root /absolute/existing/nuscenes \
  --archive /absolute/existing/v1.0-mini.tgz \
  --archive-sha256 ORIGINAL_ACQUIRED_ARCHIVE_SHA256 \
  --approved-input /absolute/existing/approved-driving-input.json \
  --frame-count 10 --output /absolute/NEW_ROAD_DEMO_DIRECTORY
```

The actual prepared clip retains scene-0061 original indices **1–10**, with all
78/86/94/103/103/110/118/120/124/127 annotation boxes and ten original CAM_FRONT
JPEGs. The ego timeline lasts **4.499437 seconds**. Index 11 has 129 objects,
exceeding the current 128-object frame contract, so this clip stops before it;
objects were not filtered to extend the clip. Images retain their own capture
timestamps, about 35 ms before each ego anchor, rather than being retimed.
The MCAP is 2,156,897 bytes, SHA-256
`767d066abcd7b1d758785cc1d110052973541602324dd79979e945da33667fcf`.
Original image bytes are `foxglove.CompressedImage` JSON/base64 messages, usable
by the existing decoder. The ordinary recording import and explicit project
grant retain isolated noncommercial research scope.

The preparer opts into **compact lineage**; the converter's default output is
unchanged. `--compact-lineage` is also available on its `export` command. Original
rows and motion receipts are losslessly retained in a canonical
`axiom.compact-original-lineage/1` envelope using `zlib-base64/1`, with the exact
expanded canonical JSON's SHA-256 and byte count. The envelope fits the existing
1 MiB MCAP text bound; expanded original lineage is bounded to 16 MiB and the
existing 16-frame/128-object bounds remain. `expand_lineage()` rejects oversized,
truncated, trailing or hash-mismatched compressed content. The recognized source
summary pins the envelope and declares its encoding. The ordinary source grant
verifies the retained metadata digest; bounded decompression and row recovery are
converter operations rather than a claim that the grant evaluates annotations.

Actual local output and import/grant/catalog receipts are in
`.runtime/log-sim-demo/road`. Camera and object replay remain nonreactive, and
boxes are original annotations, not newly inferred predictions.

## Ten-scene Log Sim library · 2026-10-09

`scripts/prepare_log_sim_library.py` uses the same `--data-root`, `--archive`,
`--archive-sha256`, `--approved-input`, optional `--ssh`, and new `--output`
arguments. The batch reads/hashes the existing archive and original metadata once.
It retains each original scene's description, location, record digest and all
sample eligibility decisions. No new data, GT filtering, image interpolation or
GPU inference is involved.

The dedicated **`nuscenes-log-selection/1`** contract supports 2–64 original
contiguous frames. VAD's `driving-input/1` remains at 3–16 frames. Each supported
segment uses the same exact planning/object/image schemas. Contiguous runs that
exceed the existing compact-lineage byte bound are explicitly split into
consecutive nonoverlapping segments; every original frame is still accounted
for. Unsupported isolated frames and conversion failures retain their reasons.
No parser byte or 128-object bound was widened.

The actual retained mini batch contains **10 original scenes / 15 segments**:
404 original keyframes, 381 retained keyframes and 23 explicitly excluded frames.
Fourteen scene-0061 frames have more than 128 original objects, and nine scenes'
first frames lack a causal CAN measurement. Five scenes are whole 19–20 second
clips; five have two intervals because of exclusions or lineage byte bounds.
There are zero unaccounted or unsupported conversion frames. The fifteen MCAPs
total 80,715,723 bytes and retain 381 original front images, ego states and full
GT object sets.

The original scene count is independent of segment count. Each segment's source
summary pins the original scene token/name/description/record digest, original
frame denominator, inclusive sample indices and scene-relative interval times.
The preparer checks these against original scene/sample rows. The product's
library registration requires at least one retained provenance-bearing segment
per scene; a fully unsupported scene needs a separate original provenance anchor
before it can be registered, rather than an invented all-excluded row.

The complete manifest, per-frame reasons, segment import proposals and recovered
original-row/source-metadata check are retained under
`.runtime/log-sim-library-20261009/`. `library-manifest.json` and
`source-check.json` keep actual source preparation separate from later controller
execution, browser workflow and evaluation results.
