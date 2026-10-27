# Four-module expansion

Approved implementation scope: 2026-10-07. This tracks implementation, not vendor equivalence or commercial release approval.

The first enlarged release covers ADP, Object Sim, Sensor Sim and Log Sim on the existing Mac and RTX 2070 SUPER Linux host. CPU esmini and the installed CARLA build are the selected engines. Sensor work includes native CARLA camera/LiDAR/radar and an optional CameraE2E RAW/ISP connector. Log closed loop starts with recorded object tracks and planning/control, with a pinned reference bicycle model. Camera E2E closed loop requires reactive sensor generation and remains unsupported until that exists. VAD/nuScenes work remains isolated noncommercial research.

## Delivery units

| Unit | Scope | Acceptance gate | State |
| --- | --- | --- | --- |
| Common experiments | Immutable project experiments, preview, existing queue, streamed artifacts, role tokens | Project isolation, idempotency, lease/cancel/restart, schema16→17 restored-copy migration | Implemented; isolated role workflow and cold recovery accepted |
| Object simulation | Scenario v2, six actor/action templates, bounded XML interchange, esmini optional adapter, observers | Actual native engine traces; unsupported constructs rejected; UI author/run/inspect | Implemented; 36 actual native runs retained |
| Sensor simulation | Native capability/readback, 10–30 second streaming multi-sensor capture, frame identity and rig provenance | Actual installed CARLA, hashes, timing, drops, bounded resource use | Implemented; native 10/30 seconds and sequential rig A/B accepted |
| CameraE2E | Same RAW ISP comparison through the existing versioned API, complete external evidence closure | Original RAW hashes unchanged, actual processing outputs and external execution ID | Same RAW workflow accepted; lens/sensor bridge remains unsupported |
| Log simulation | Immutable sequence and ReplaySpec, original/open-loop/planning closed loop, pose/clock constraints | Control changes next ego observation; missing pose/time/dimensions refused | Reference feedback accepted; actual learned VAD completed through the queue in isolated research |
| Integrated workflow | Normal UI/API/SDK, generated agent catalog, typed plan evidence, team repeatability | Browser author/preview/submit/result/download; no cross-type safety pass | Role workflows accepted; stable built UI/API/worker entry point added |

## Constraints carried into implementation

- Preserve existing dirty work, v1 scenario APIs and result hashes. No automatic operating database migration, commit, deployment or server restart.
- Additive schema changes are accepted first on temporary restored copies; a clean operating migration is a separate action.
- Every execution pins input/configuration revision, adapter/evaluator version, seed, fidelity and content hashes. Execution success and SUT verdict are distinct.
- Engine-specific dependencies stay in optional adapter packages. Unconfigured or unsupported capabilities fail closed before dispatch.
- Team tokens use explicit roles and project scope. Legacy resources without project ownership must not be exposed to team tokens by inference.
- GPU work is serialized. Existing remote CARLA and unrelated processes are preserved. Current remote free VRAM was about 3.5 GiB when work began; acceptance must use measured resource limits.
- Sensor A/B, same RAW ISP A/B, and same input model A/B are distinct experiments. CARLA postprocessed RGB is not physical RAW.
- Authored/fixture traces, reference vehicle dynamics, actual simulator output and measured camera data retain separate provenance.
- License gates for engine code, models, datasets, maps and other assets remain separate. Vendor documentation is a scope reference; proprietary implementation/assets are not copied.

## Remaining quality gates

Functional correspondence is the first gate. Precision, measured performance, robustness and operational readiness are separate gates. Direct comparison with Applied requires executable access and matching inputs, which the current documentation access does not provide. No superiority or full commercial readiness claim follows from this implementation.

The current accepted development workspace is `http://127.0.0.1:8020`, with its
own schema17 data and private bootstrap token. The ordinary operating workspace
was not migrated or restarted. [Shared contract and launch](experiment-contract.md),
[Object Sim](object-sim-v2.md), [sensor/ISP acceptance](sensor-capture-expansion.md),
[Log Sim](planning-replays.md), and [native learned model](driving-policies.md)
describe exact usage and evidence boundaries.

[Integrated acceptance record](four-module-expansion-acceptance-20261007.md) and its
[machine-readable evidence index](validation/four-module-expansion-20261007.json)
separate native runs, authored contract checks, browser workflows and open release gates.

Remaining expansion gates include a frozen lens/sensor scene plan and pixel
correspondence, actual approved object/planning road logs, a second real learned
policy/checkpoint for A/B, reactive camera closed loop, accepted depth/segmentation,
full legacy project ownership, human team repeatability, measured correlation,
long-duration/load/fault tests and commercial data/model/asset rights.
