# Original Stunt Ramp: asset build boundary

The preserved OBJ bundle is `assets/stunt-ramp/source-v1`. The Blender bundle
is an interchange asset, not a CARLA map. Unreal editor import, Linux cooking,
runtime surface contact and the Ramp controller are independent acceptance
steps. None establishes physical vehicle validation.

## Toolchain identity

The installed CARLA revision resolves to
`1ae535667d2c75ef92ba44d0e7506419b2e577cb` (2026-02-26). Its
[project descriptor](https://github.com/carla-simulator/carla/blob/1ae535667d2c75ef92ba44d0e7506419b2e577cb/Unreal/CarlaUE4/CarlaUE4.uproject)
selects UE4.26. The matching
[Linux build instructions](https://github.com/carla-simulator/carla/blob/1ae535667d2c75ef92ba44d0e7506419b2e577cb/Docs/build_linux.md)
require the modified `CarlaUnreal/UnrealEngine` fork's `carla` branch and an
Epic-linked GitHub account. They estimate 130 GB free disk for a source build.
The public CARLA commit does not identify the private engine's exact git SHA or
patch version. Record those from the authorized checkout's `Build.version`,
git revision and editor binary before use; stock UE4.26 and UE5 are not substitutes.

The pinned
[content catalog](https://github.com/carla-simulator/carla/blob/1ae535667d2c75ef92ba44d0e7506419b2e577cb/Util/ContentVersions.txt)
selects `20250912_2171890`. Source code, original Ramp geometry, CARLA content,
Blender and Unreal retain separate rights/provenance records.

## FBX export

Use Blender 4.2.23 LTS, build `d0cbe84903e8`, as recorded in the export manifest.
The current preserved bundle is `assets/stunt-ramp/fbx-v1-blender-4.2.23-r2`.
Two actual Linux exports produced identical FBX and manifest bytes; see the
[measured repeat evidence](validation/stunt-ramp-toolchain-20261023/transfer-reproducibility.json).
The older bundle remains preserved with its original exporter identity.
Run with a new output directory:

```sh
env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONHASHSEED=0 \
  /path/to/blender --python-use-system-env --background --factory-startup \
  --python-exit-code 1 --python scripts/export_stunt_ramp_fbx.py -- \
  --source assets/stunt-ramp/source-v1 --output /new/fbx-bundle
```

Blender must actually honor its Python environment, not merely expose the
`PYTHONHASHSEED` variable. Its
[FBX UUID generation](https://github.com/blender/blender-addons/blob/blender-v3.6-release/io_scene_fbx/fbx_utils.py)
uses Python hashes. The exporter checks the interpreter flags and normalizes
serialization timestamps; actual outputs retain their own SHA-256 values.

One FBX contains `StuntRamp` and its 55 UCX strips; the other two each contain
one slab and one UCX hull. Epic's
[UE4 FBX pipeline documentation](https://dev.epicgames.com/documentation/en-us/unreal-engine/fbx-static-mesh-pipeline?application_version=4.27)
warns that a multi-mesh FBX may import custom collision only for the first mesh.
Names must match `UCX_<visible-mesh>_<index>`.

The exporter converts meters to raw centimeter vertex coordinates once,
retains identity object transforms, and sets the Blender scene unit to 0.01 m.
Its separate import of each generated FBX verifies every object's name,
vertices, triangle connectivity and outward winding. This is DCC round-trip
evidence; it does not prove that Unreal uses the supplied hulls.

## Unreal import and cook

The following helpers are prepared and covered by file/contract tests. They
have not been executed in Unreal until a corresponding real editor receipt
and cook manifest exist. The available Epic Python reference is UE4.27; the
UE4.26 entry point checks required APIs and fails if they are unavailable.

```sh
python scripts/stunt_ramp_native_assets.py plan \
  --bundle /path/to/fbx-bundle \
  --ue-root /path/to/authorized/CarlaUnreal-4.26 \
  --carla-root /path/to/clean/carla-1ae535667d2c75ef92ba44d0e7506419b2e577cb \
  --output /new/ramp-import-plan

python scripts/stunt_ramp_native_assets.py import \
  --plan /new/ramp-import-plan/import-plan.json

python scripts/stunt_ramp_native_assets.py cook \
  --plan /new/ramp-import-plan/import-plan.json --output /new/ramp-cooked-output
```

Planning verifies the exact CARLA source, clean tracked sources, engine fork,
engine version, binary hashes and all three asset-builder script hashes. Import repeats that check and calls the
separate editor script, refusing to overwrite an existing package. The script
creates a bounded empty CARLA map with the three static meshes, native light
actors, CARLA game mode and a flat-approach-only OpenDRIVE road. The wall and
overhang are collision surfaces, not invented driving-network lanes.

Import disables automatic collision and mesh combination. It requires 55/1/1
convex hulls, matching simple-shape counts, centimeter bounds, static actors
at the common origin and `BlockAll`. Actor/component transforms, mobility,
profile and query/physics enablement are read back after assignment. The active collision policy is
`CTF_USE_SIMPLE_AND_COMPLEX`.
[Epic import settings](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FbxStaticMeshImportData?application_version=4.27)
and [collision inspection APIs](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorStaticMeshLibrary?application_version=4.27)
define these controls.

Do not substitute ordinary `make import` for this check. The pinned
[CARLA preparation commandlet](https://github.com/carla-simulator/carla/blob/1ae535667d2c75ef92ba44d0e7506419b2e577cb/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Commandlet/PrepareAssetsForCookingCommandlet.cpp#L196)
changes static meshes to `CTF_UseComplexAsSimple`. That represents a different
collision model even if the source FBX includes UCX hulls.

Cooking uses the pinned editor's `-run=cook -targetplatform=LinuxNoEditor`
command with an explicit map and a fresh output, following the commandlet
shape in [CARLA Package.sh](https://github.com/carla-simulator/carla/blob/1ae535667d2c75ef92ba44d0e7506419b2e577cb/Util/BuildTools/Package.sh#L394).
It requires new cooked map and mesh files, stages sealed OpenDRIVE/package
metadata, and hashes the resulting files. It never installs assets into the
running server. A successful process exit alone does not satisfy the cook gate.

## Runtime gates still required

- Preserve the installed server/client build, source revision, seed, design
  revision and asset manifest hash together with each new result.
- Verify loaded map identity and source/cooked artifact lineage on the isolated
  server before exposing Ramp as available.
- Measure native ray hit positions, seam/tire behavior, wall/overhang contact
  and dropped/rotated-vehicle collision against the actual cooked geometry.
  CARLA 1ae5356 ray results do not expose a native hit normal. A prospective
  local-plane fit must use at least three noncollinear returned hit points and
  record query coordinates, returned points, fit residual and orientation
  convention. Its bounded neighborhood, curvature error and normal estimate
  require commissioning; an inferred plane normal is not a returned PhysX
  contact normal.
- Implement and test bounded suction/contact force, release/landing control,
  traces, real camera playback and cleanup/world restoration.
- Keep `physical_validation: not_established`; no real Tesla capability or
  safe real-world landing follows from any of these simulations.

`native_map_available` and `native_collision_verified` remain false in the
export, editor and cook receipts. Only a separate native commissioning result
can establish the execution capability.
