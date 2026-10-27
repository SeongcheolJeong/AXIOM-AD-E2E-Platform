# CARLA vehicle-response evidence

The live LiDAR bridge records an additional `carla-vehicle-response.json` artifact in adapter 0.4.1. It diagnoses vehicle initialization and control response; it does not establish calibrated vehicle dynamics, sensor accuracy or road safety. Historical 0.4.0 results retain their original bytes, metrics and verdicts.

The existing stack input `speed_mps` is the magnitude of world XYZ velocity. Existing acceleration and jerk metrics are finite differences of that magnitude, with the historical first acceleration set to zero. A small alternating speed difference can therefore produce a large jerk at 20 Hz. The response view keeps signed horizontal-heading speed, signed vertical velocity and the original 3D magnitude separate. It does not substitute a smoother metric or remove failing samples.

The artifact connects the final rest-stability snapshot, initial target-velocity requests and every measured physics snapshot. Snapshot vectors use CARLA's native left-handed world coordinates, metres, seconds and native rotation degrees. Vehicle RPC readings have frame brackets because they are not part of the `WorldSnapshot`. Initial target-velocity calls have no synchronous acknowledgement; their request records must not be presented as proof of the resulting velocity.

After rest stabilization and before initial speed injection, both vehicles' wheel `tire_friction` configuration is read back and compared with the requested float32 value. A mismatch ends the execution with partial evidence. This scalar is the native tire configuration scale, not a measured road coefficient. Effective contact friction in wheel telemetry is a separate quantity.

All vehicles are now spawned before physics is applied to any of them. In the actual original-order diagnostic, the ego retained scale 3.5 while the lead retained the requested float32 representation of 0.8. Applying both configurations after all spawns retained 0.8 for both vehicles. The legacy object-truth controller uses the same ordering and gate; its readback is preserved inside `carla-startup.json`. A successful startup trace is saved before any additional readback can fail.

Telemetry is optional. Missing getters, exceptions and nonfinite native values remain explicitly unavailable or classified as nonfinite. Only direct wheel omega/slip/friction fields and engine speed/gear are used for diagnosis; HUD debug force/load/torque fields are not treated as reliable headless measurements. RPC telemetry cannot establish contact completeness or a root cause by itself.

## Source boundaries

The official CARLA 0.9.16 sources explain the API semantics; the actual installed server/client build and trial source hashes are recorded separately.

- [`Client.cpp`](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Client.cpp): asynchronous target-velocity and physics-control calls, synchronous query/batch boundaries.
- [`CarlaWheeledVehicle.cpp`](https://github.com/carla-simulator/carla/blob/0.9.16/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Vehicle/CarlaWheeledVehicle.cpp): physics state recreation and wheel telemetry mapping. Direct linear velocity injection does not initialize wheel rotation with the same road speed.
- [`Actor.cpp`](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/Actor.cpp) and [`Control.cpp`](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/Control.cpp): Python getters and telemetry fields.
- [`CarlaHUD.cpp`](https://github.com/carla-simulator/carla/blob/0.9.16/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Game/CarlaHUD.cpp): debug display is a separate path; enabling a HUD is not a validation of headless force channels.

## Standalone diagnosis

`scripts/diagnose_carla_response.py` is an explicitly separate simulator experiment, not a platform job or acceptance verdict. It requires an empty dedicated world and exclusive ownership. Stop the platform dispatcher and CARLA worker before using it. Each invocation runs one declared condition, retains the full bounded trace and restores only the world state and actors it owns. Never retry an uncertain execution without inspecting its retained output and simulator state.

The initial matrix compares the existing stability gate, additional rest until 40 ticks, untouched native physics and reapplication of native physics. These are diagnostic interventions with recorded configuration readbacks. A difference between single trials is a hypothesis for follow-up, not statistical proof or permission to relabel earlier results.

`scripts/diagnose_carla_response_configured.py` preserves a separate version of the experiment that changes only configuration ordering. The original four-condition script and its captured source hash remain available for reproduction.

## Actual acceptance, 2026-09-13

The [acceptance record](validation/carla-vehicle-response-2026-09-13.json) links all five standalone diagnostics, two actual live platform runs and their 80-tick same-source replays, the legacy capture trials, source archives and preservation checks.

| Condition | Ego / lead tire scale readback | Full ego maximum jerk | Scope |
| --- | --- | --- | --- |
| Original ordering, 23 rest ticks | 3.5 / 0.8 | 58.743 m/s³ | Standalone diagnostic; original live result reproduced |
| Original ordering, 40 rest ticks | 3.5 / 0.8 | 58.746 m/s³ | Additional rest alone did not resolve the issue |
| Configure after all spawns | 0.8 / 0.8 | 11.362 m/s³ | Standalone diagnostic and actual live r1 agree; threshold 10 still fails |
| Actual live r2, conservative horizon | 0.8 / 0.8 | 170.320 m/s³ | 80 unknown-stop commands; deceleration and jerk fail |
| Legacy 4 m/s object-truth control | 0.8 / 0.8 | 1.169 m/s³ | Illustrative run thresholds pass; not perception validation |

The full metric change is not entirely a tire-dynamics comparison. Original ordering produced its one unknown-stop command at sequence 79; corrected live r1 produced it at sequence 63 (3.15 s), when ground coverage became insufficient. The maximum response jerk followed at observation 64 (3.20 s). Before each run's first brake command, maximum jerk was 36.203 versus 1.286 m/s³. These separately bounded diagnostic intervals do not replace the full 81-observation acceptance metrics.

Raw horizontal-heading speed and the 3D norm differ by less than 0.000074 m/s in the two diagnostic traces. Vertical motion is therefore insufficient to explain the observed oscillation. Wheel/body initialization, the reported neutral gear, input-coverage transitions and braking comfort remain explicit follow-up questions; no sensor, drivetrain or road calibration is claimed.
