# CARLA stunt clock investigation

The isolated CARLA build reporting `1ae5356` advanced extra frames after a single
tick request when its fixed interval was the Python double `0.01`. The runner
correctly rejected those runs. This was reproduced with an empty camera setup;
rendering, file writes and controller commands were not required to trigger it.

## Cause and implementation

At commit `1ae5356`, `call_tick` creates a synchronization target from elapsed game
time plus the requested double interval. `IsNextGameTickAllowed` permits another
frame while that target is strictly greater than elapsed time. See
[CarlaServer.cpp lines 3461–3476 and 3532–3538](https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Server/CarlaServer.cpp#L3461)
and [ServerSynchronization.h lines 129–151](https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Server/ServerSynchronization.h#L129).
The engine advances episode timers using the float `DeltaSeconds` argument in
[CarlaEngine.cpp lines 258–298](https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Game/CarlaEngine.cpp#L258).

The source explains the observed failure: float32 `0.01` is
`0.009999999776482582`, leaving a positive remainder after one frame against a
double target of `0.01`. One tick can therefore authorize a second frame. Source
identity is evidence for this explanation, not a proof that an installed binary
is identical; the independent runtime probes below establish the observed effect.

The adapter sets the engine interval to the exact float32 value promoted back to
double. Nominal `0.005` similarly becomes `0.004999999888241291`. The saved design
keeps the nominal interval. Trace `clock_contract` records both, and impulse budget
accounting uses the slightly larger nominal interval conservatively. The validator
requires this precise representation, consecutive frame IDs and the observed
interval. It does not accept arbitrary time drift or skip frames.

`tick_snapshot` issues one tick, then only reads the snapshot until that requested
frame arrives. Snapshot lag never causes a retry tick. The next loop consumes
that exact returned snapshot. The read-only probe rejects worlds already in
synchronous mode, and execution compares initial settings with the frozen probe.
Commissioning checks both supported intervals with one-tick/idle-dwell tests.

## Runtime evidence, 2026-10-23

Each diagnostic held the dedicated stunt resource lease, used the same server on
port 2400 and restored original settings after destroying its owned actors.
After a warm-up, each ran 20 ticks in each of five stages: idle without cameras,
force/torque/control RPCs without cameras, idle with cameras, RPCs with cameras,
and RPCs with image writes. A 30 ms wall-clock idle interval followed each
observed operation. Each diagnostic contains 100 commanded ticks.

| Engine interval | Tick gaps | Extra frames during idle dwell | Cleanup |
| --- | ---: | ---: | --- |
| double `0.01` | 4 | 94 | confirmed |
| float32 `0.01` promoted to double | 0 | 0 | confirmed |
| float32 `0.005` promoted to double | 0 | 0 | confirmed |

Preserved local records are under `.runtime/stunt-debug/`; matching remote
records are under `/home/axiom-user/workspace/axiom-stunt-20261023/`.

| Record | SHA-256 |
| --- | --- |
| `clock-diagnostic.json` | `401b7871b0d52fbb70da9165f1d6b5ff5741ff23bcdce5f630605aae9447403a` |
| `clock-diagnostic-float32.json` | `de34eff7c85eda3f631eb654895b8399ab233486c2629d2e1d7070bc08e7b40d` |
| `clock-diagnostic-float32-5ms.json` | `8f1c3445000eccaaf1069ba1f1f213625b7569278ab2c5a98bce360c6a2ee30f` |

These are engine integration checks. They do not establish realistic vehicle,
gas thruster, tire, sensor or Tesla Roadster behavior. The stunt results retain
`physical_validation: not_established`.

## Camera initialization

The first full run after the clock correction completed its physical trajectory
but failed the strict camera interval check. Both 200-frame camera streams had
one six-physics-frame interval immediately after their first image; all 198
subsequent intervals were five frames. This failed run was retained as
`stunt_bd2f5b8e4dd445aab1e960ad4f7e5224`. Its physical summary passing did not make
the whole execution pass.

The runner now initializes cameras for 0.3 simulated seconds while the vehicle
is braked, before assigning initial velocity and choosing the recorded start
frame. It drains initialization callbacks and filters any delayed callbacks with
pretrial source frame IDs. The trace retains initialization counts and every
recorded image's actual source time and frame. The strict interval check remains.

A separate one-second native diagnostic confirmed 20 frames per camera at each
physics interval: all 19 intervals were five frames at 10 ms, and ten frames at
5 ms. Both trials restored settings and removed their actors. Evidence:
`.runtime/stunt-debug/camera-warmup-diagnostic.json`, SHA-256
`87725f5bba3d91df1aa6bb6554b0ac447144f3dc9084ce7a8e47e8bc955d49eb`.

Camera validation or encoding failures produce a structured failed receipt and
update the trace. They preserve the already established cleanup status and error
text instead of turning a confirmed cleanup into an uncertain process exit.
