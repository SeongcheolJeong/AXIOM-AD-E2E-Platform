# CARLA engine patch candidates

## LiDAR `noise_seed` · source 1ae5356

Status: **prepared; not compiled or deployed**. The normal worker and CARLA executable are unchanged. Platform warnings about unverified sensor RNG application remain valid.

[`1ae5356-lidar-noise-seed.patch`](1ae5356-lidar-noise-seed.patch) calls `SetSeed(Description.RandomSeed)` after the new LiDAR description is assigned. The constructor seeds the RNG before the requested blueprint description is applied; the pinned source's later `Set(FLidarDescription)` does not reseed it. [Official source](https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/RayCastLidar.cpp).

The [manifest](1ae5356-lidar-noise-seed.json) pins original, patched and patch SHA-256 values. An isolated source checkout passed `git apply --check`, exact one-line-change comparison, rejection of a second application, and reverse application restoring original bytes. These are source-patch checks, not engine execution or sensor validation.

### Applying in a separate source build

Use an isolated CARLA source checkout with the original file SHA `c37adb3c64e5c6b89019d90daec00c9e61be1188e4efd6c58b963dd5c07e00d4`. Inspect local changes first; a different file hash requires a new review and manifest. From that checkout, run `git apply --check /absolute/path/to/1ae5356-lidar-noise-seed.patch`, then `git apply` with the same path. Confirm the patched file SHA is `55288639e1e437c8ada5e97d1b3c21c280d399aae29a889824bcd625d68d31dd`. Do not patch an executable or replace the production runtime with these source files.

Build prerequisites include the CARLA Unreal fork and compatible content. The [official 0.9.16 Linux build guide](https://carla.readthedocs.io/en/0.9.16/build_linux/) describes Epic-linked repository access, approximately130GB disk space and an Ubuntu20.04/22.04 build environment;24.04 is outside its tested environment. The currently inspected runtime has no CARLA/Unreal source, no conventional `~/UnrealEngine_4.26` checkout, no Clang on PATH, and an empty `workspace/CARLA` directory. A different source location or repository authorization has not been established. There is about155GiB free disk on the inspected host; this is not a verified build-capacity estimate.

### Acceptance required before deployment

1. Record exact CARLA/Unreal/content revisions, patch and executable hashes, build ID, compiler and client version. Give the new engine a distinct build identity; retain the original binary and prior results.
2. In an isolated diagnostic build, record `Description.RandomSeed` and `GetSeed()` around sensor configuration. The Python `carla.Sensor` in the installed package exposes no seed method; detached blueprint attribute readback is not RNG state readback.
3. Use a fixed sensor pose and scene with dropout enabled. Compare42/43/42, preserving native callback bytes, actor/frame identities, timing and all attributes. Require repeated42 to reproduce under declared conditions and43 to affect the intended randomized output. Include a zero-noise/dropout deterministic control and boundary seeds accepted by the blueprint.
4. Check startup, cleanup, existing ground-truth capture and live LiDAR paths with the new build. Record changed output as new evidence. Never retrofit it onto old runs or count changing a requested seed as proof of independent stochastic trials.

The recorded host audit reads the running executable and matching debug symbols without attaching a debugger or ticking/changing the world. It supports analysis of the configuration path; it does not read a live sensor's RNG state. See [seed diagnosis](../../../docs/carla-lidar-seed-diagnosis.md).

CARLA source is MIT licensed. Unreal Engine, models and content have separate terms; this patch does not grant access to them or establish distribution rights.
