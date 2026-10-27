# Optional Neural Sim adapter

This package connects the ordinary AXIOM Experiment queue to a task-owned GPU
runtime. It reconstructs known-pose CARLA captures or operator-registered real-road
recordings with native LiDAR initialization, then renders changed camera positions
using the resulting Splatfacto checkpoint.

CARLA outputs retain **CARLA 합성 로그의 Neural 재구성**; recorded real-road outputs
use `neural_reconstruction_of_real_road`. Both are static scene reconstructions.
Dynamic actor editing, sensor-driven E2E control and physical sensor validation
remain outside this implementation. All selected source frames are used for demo
training; baseline reconstruction images are not a held-out quality measurement.
The available nuScenes source is limited to isolated noncommercial research and
has not been cleared for external publication.

## Dependencies and connection

Core remains free of Torch/CUDA imports. Local adapter dependencies are NumPy
and Pillow. The remote native runtime requires the pinned Nerfstudio 1.1.5
environment with CUDA, gsplat, compatible Torch and a working NVIDIA GPU.
Engine/library identities are retained in native results.

Upstream code notices: [Nerfstudio 1.1.5](https://github.com/nerfstudio-project/nerfstudio/blob/v1.1.5/LICENSE)
and [gsplat 1.4.0](https://github.com/nerfstudio-project/gsplat/blob/v1.4.0/LICENSE)
each publish an Apache-2.0 license. Their code licenses do not grant rights to
CARLA/Unreal maps and assets, the NVIDIA runtime, or downloaded neural weights.
The local demo retains the existing CARLA capture provenance; it does not bundle
engine assets for redistribution. Splatfacto initializes its LPIPS dependency
using a separately cached official PyTorch AlexNet weight; this demo does not
report LPIPS as a held-out evaluation. Runtime dependency identities and weight
hashes remain in the task-owned environment evidence.

Expose `integrations/neural/src` on the API and worker Python paths or install
this package. The `ade2e.experiment_handlers` entry point is
`axiom_neural:create_handler`; its ID/version are `neural-splatfacto`/`1.0.0`.
`scripts/internal_platform.py --enable-configured-engines` includes the adapter
source and forwards only explicitly configured `AXIOM_NEURAL_` variables.

Set `AXIOM_NEURAL_NATIVE_CONFIG` to an operator-owned local JSON file:

```json
{
  "schema_version": "neural-native-config/1",
  "ssh_destination": "operator@gpu-host",
  "remote_root": "/opt/axiom-neural-demo",
  "launcher": "/opt/axiom-neural-demo/run-python",
  "launcher_sha256": "<64 lowercase hex characters>",
  "environment_sha256": "<64 lowercase hex characters>",
  "training_steps": 10000,
  "timeout_seconds": 10800
}
```

This is a format example; replace both hash placeholders with actual values.
The launcher must run the supplied native Python script and request path inside
the pinned CUDA environment, with `remote_root` visible at the same absolute
path. SSH is noninteractive. API and worker use the same config and source
versions. Changing the config, launcher identity or adapter source invalidates
previous previews. The configured environment must be prepared independently;
installing this package does not alter drivers, reboot the GPU host or start
CARLA. The existing `carla` resource lease is shared because the configured demo
uses the same GPU host as CARLA; it does not create a new CARLA world.

The current presenter environment restores CARLA port2000 and keeps port2100
stopped. Native frame rendering was exercised with port2000 running. Training
was exercised with both CARLA servers stopped; reclaim that GPU capacity before
starting another scene training job. CUDA availability alone does not establish
sufficient training memory. The task-local 595.84 driver userspace must be
rechecked if the host is rebooted or its loaded kernel driver changes.

## Inputs and retained outputs

`neural_reconstruction` accepts `{source_execution_id, rig:"rig_a", camera:"front"}`.
The source must be a completed, same-project CARLA driving Experiment (20 seconds,
200 front RGB frames) or `neural_source_import` execution with intact published
artifacts. The `rig_a/front` request remains the common API selector; real-road
metadata also records its actual source rig and `CAM_FRONT` camera. The adapter
verifies source bytes and uses centered, scaled, right-handed OpenGL geometry.

`neural_render` accepts `{reconstruction_execution_id, lateral_m, height_m,
mode:"frame"|"sequence"|"camera_sweep", frame_index}`. Frame/sequence lateral
displacement is camera-relative within ±0.5 m; height stays within ±0.3 m.
`camera_sweep` freezes the selected source frame and treats `lateral_m` as a
positive sweep amplitude up to 2 m. Its CARLA `sweep` preset uses source frame 50,
amplitude 1.5 m and zero height. The first real-road scene uses frame 48 and
amplitude 0.5 m; its actual recorded anchor time is 4.050016 seconds. These are experimental authoring limits, not
a validated novel-view envelope.

The sweep renders 61 cosine-eased camera positions from left to right at 10 Hz.
Its `/2` result keeps playback time/index separate from fixed
`source_frame_index`/`source_time_s`; `/1` frame/sequence results keep their
original contract. The source scene is frozen, while the virtual camera moves.

Training publishes the checkpoint, trainer config, dataset manifest and summary.
Rendering publishes `neural-render-result.json` plus a `ZIP_STORED` archive of
original/baseline/novel RGB PNGs. File hashes, frame identity, applied poses and
the native summary are cross-checked before queue publication. A native run
directory remains under `remote_root/runs/neural_<id>` for checkpoint reuse and
diagnostics. Rendering currently requires that retained remote dataset/config/
checkpoint directory. The local published checkpoint alone is not a portable
scene restore package.

Cancellation is unsupported after native dispatch. Timeout or transport loss
preserves the external run ID and uncertain outcome; a disconnected SSH client
does not establish that the remote GPU process stopped. The queue does not
automatically retry these jobs or clear a quarantined resource.

## Real-road source preparation

`neural_source_import` accepts only `{source_id}`. Set
`AXIOM_NEURAL_REAL_ROAD_SOURCES` on both API and worker to an operator-owned JSON
catalog with schema `neural-real-road-catalog/1` and a `sources` list. Each entry
contains `source_id`, absolute `manifest_path` and `dataset_path`, and their
`manifest_sha256` and `dataset_sha256` values. Browser input cannot select paths.
The adapter verifies the catalog hashes, research scope, exact image/pose mapping,
recording anchors and ZIP members before publishing the source execution.

`scripts/prepare_nuscenes_source.py` prepares an already available nuScenes
archive; it does not acquire data. Its `extract`, `build`, `mcap`, and `seal`
commands all require `--research-only`. `extract` selects calibrated camera
records and original LiDAR within 50 ms. `build` uses recorded sensor/ego poses,
resizes 1600×900 JPEGs to 640×360 with Lanczos, scales intrinsics accordingly, and
projects original LiDAR points to obtain initialization colors. Original JPEG,
LiDAR, metadata, transformed PNG and package hashes are retained.

`mcap` writes the selected original JPEG bytes to one
`/camera/CAM_FRONT/image` channel with exact original nanosecond capture times.
Import that MCAP through the normal recording API and grant the project access.
Then `seal --anchor PATH` requires the returned `recording_id`, `channel_id` and
`recording_sha256`. Register the sealed files in the operator catalog and submit
`neural_source_import` through the normal Experiment API before training.

The first source contains 96 dense camera records across 8 seconds, nominally
12 Hz. Original timestamps are slightly irregular; per-frame `log_time_ns`
remains a decimal string and `time_s` uses the actual capture interval. A camera
sweep still has its own 61-frame/10-Hz render timeline while every frame points
to the same original recording ordinal and timestamp. Neither this package nor
its open-source code licenses grant commercial or redistribution rights to the
dataset, engine assets, model weights, or generated reconstructions.

## Presenter preparation through the normal API

`scripts/neural_demo.py` provides `train`, `render`, `render-presets`, `bind` and
`status`. It uses only `AxiomClient`, stores no token in its receipt, and records
idempotency state before submitting work. Reuse the same output file and original
arguments to inspect/resume the same jobs. A failed or uncertain job is retained;
the helper does not replace it with a new execution.

```bash
.venv/bin/python scripts/neural_demo.py \
  --url http://127.0.0.1:8040 --token-file /private/path/token \
  --project-id PROJECT --output /private/path/train.json \
  train --source-execution-id SOURCE_JOB

.venv/bin/python scripts/neural_demo.py \
  --url http://127.0.0.1:8040 --token-file /private/path/token \
  --project-id PROJECT --output /private/path/train.json status

.venv/bin/python scripts/neural_demo.py \
  --url http://127.0.0.1:8040 --token-file /private/path/token \
  --project-id PROJECT --output /private/path/views.json --wait-seconds 600 \
  render-presets --source-execution-id SOURCE_JOB \
  --reconstruction-execution-id COMPLETED_TRAIN_JOB
```

`render-presets` requests the three exact default sequence views. Only completed,
certain renders are attached as representative preset results. With the default
zero wait it submits/checks jobs and returns; repeat the same command and receipt
after completion to attach them. `bind` can attach an explicit completed scene
without starting a render; `--saved-execution-id` requires one preset ID.

To generate the camera-motion showcase directly:

```bash
.venv/bin/python scripts/neural_demo.py \
  --url http://127.0.0.1:8040 --token-file /private/path/token \
  --project-id PROJECT --output /private/path/sweep.json \
  render --reconstruction-execution-id COMPLETED_TRAIN_JOB \
  --mode camera_sweep --frame-index 50 --lateral-m 1.5 --height-m 0
```

Use `render-presets --include-sweep` to also prepare and, after completion,
explicitly bind the sweep alongside the three original sequence views. The
flag is opt-in so existing three-view preparation receipts retain their scope.

See [the product demo contract](../../docs/neural-sim-demo.md) for the presentation
flow. Backend/adapter file-contract fixtures are not actual GPU execution or
reconstruction quality validation.
