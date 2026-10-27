# Native acceptance · 2026-10-09

Three repetitions of one **normal API → immutable experiment → worker queue →
native CARLA + Autoware → retained scene/RGB → evaluated result** have passed.
This is simulator ground
truth planning/control, not camera perception, physical sensor calibration,
complete Autoware safety-stack qualification or commercial product release.

This historical three-run qualification is tied to source `5fe3e527…` below.
Subsequent rear-axle kinematics, centered bbox-cloud and current-trajectory
control-gate corrections produce source `57de03d3…`; the older straight results
do not qualify that new source. Its first Town10 native straight trial failed
during route startup, as recorded below.

## Current Town10 native blocker

Source `57de03d3aa3a1de1275d340517685b521559d6e3f10d430a6b906e39009223a0`
was executed through the normal API/queue in
`job_1247cca2ac81420e9dcd78627cf16803`. The pinned native
`mission_planner_container` crashed with exit code **−11** immediately after
logging main lanelets **5229, 3037**. Startup never produced a trajectory/control
for measured driving. The job remains failed, and its normal verification plan
records **infrastructure failure / not evaluated**.

The successful stationary probe used a different authored route and did not
cover this failure. The frozen native route and criteria were not changed;
remaining straight repetitions and all curve trials were **not submitted**.
The exact root cause of the native crash has not been established.

Read-only inspection of the retained map confirms that lanelets 5229 and 3037
share both boundary endpoint IDs (2925 and 2980), with zero geometric gap. The
rear-axle start falls in 5229 and forward vias/goal in 3037; the native log also
reports that sequence. A simple connectivity check would not reject this
failure. A future route-specific probe of the pinned mission planner, bound to
map/vehicle/checkpoint hashes, is needed alongside routing-graph validation.
That additional preflight is a recommendation, not a shipped capability. The
current public route-handler source is not proven identical to the pinned
binary, so the last log line does not identify the exact crashing function.

Native cleanup confirmed destruction of actors 24/25/26. A fresh read-only
snapshot confirmed those IDs absent, no vehicles, and restored asynchronous
world settings; the owned container, dispatcher and quarantine were absent.
The resource was released through normal operator reconciliation, preserving
the failed sealed result. Source/profile/map hashes, full frozen route, native
log excerpt and diagnostic hashes are in the
[Town10 failure report](../../docs/validation/autoware-town10-driving-20261009.json).

## Historical Town01 straight qualification

| Item | Retained result |
| --- | --- |
| API instance used | Local city integration instance, `127.0.0.1:8031` |
| Scenario | `scenario2_9bcf911bc35d4db6b21593df777a5e3d`, revision 2 |
| Experiment | `experiment_af5c97ed055e41a49f2a111781902115` |
| Execution | `job_169844a5c1814e9e9686416f2a04f054` |
| Native episode | `city_493678820085427e83eda82348c84680` |
| Engine / map / vehicle | CARLA build `1ae5356`, Town01, native Prius |
| Runtime package fingerprint | `5fe3e527fcef71b626677bc8733a765e94ae5d823e4ff199a4d4d0c6f8fe11fc` |
| Native controller | Pinned Jazzy Autoware 0.50 planners + MPC/PID; explicit legacy acceleration flag compatibility |
| Frames | 600 physics frames over 30 s; 300 original RGB frames; 0 dropped |
| Travel / maximum speed | 29.53451 m / 3.21892 m/s |
| Lateral P95 | 0.001286 m, criterion ≤0.5 m |
| Final goal error | 0.465977 m, criterion ≤2 m |
| Final speed | 0.000000401 m/s, criterion ≤0.2 m/s |
| Native contacts | 0, criterion 0 |
| Verdict / cleanup | pass / confirmed |
| Scene SHA256 | `30b1cc8c6e40a894f5523def942aef0b9d63d860e8f101321e0a1a2f66ca6fca` |
| Sealed result manifest SHA256 | `2528c81efd4d2117370bcc1e68fa9606497f3ed9e8988b0ec190756b28dd8dba` |

All three executions used the same experiment and preview hash
`458c6911bc627c6c75a524ace0e27a7c2ee0b11f5e92fe8080433da66da4cf4f`.
The table above records the first; all results and content hashes are in the
[machine-readable acceptance report](../../docs/validation/autoware-city-20261009.json).

| Repetition / execution | Goal error (m) | Lateral P95 (m) | Maximum speed (m/s) | Verdict |
| --- | ---: | ---: | ---: | --- |
| 1 · `job_169844a5c1814e9e9686416f2a04f054` | 0.465977 | 0.001286 | 3.218917 | pass |
| 2 · `job_d4c35878e5c6438c8e6c39f49fa9926e` | 0.429785 | 0.001167 | 3.235292 | pass |
| 3 · `job_2ca83ffb0b424cb9bd6e5f095139bfb7` | 0.436098 | 0.001568 | 3.238626 | pass |

Each retained 600 physics frames and 300 original RGB frames, with no camera
drops or native contacts and confirmed cleanup. Each final speed was below
0.000001 m/s. The measured values differ: these are three passes under the fixed
criteria, not byte-level determinism or three independent driving conditions.

The goal and criteria were frozen before dispatch. Desired route, native planner
trajectories, native vehicle responses, controller commands, original RGB chunks,
map, source fingerprints, evaluation and cleanup receipt are separate retained
artifacts. Maximum speed exceeded the 3 m/s target by up to 0.239 m/s; the frozen
criteria did not include a maximum-speed pass/fail check.

The first queued attempt `job_d10dec5d13f843c6955579c8c1ce2726` is retained as a
failure: startup produced a valid native trajectory, then the transport rejected
extra `reverse`/`hand_brake` keys. The converter was narrowed to its three-key
contract, source fingerprint changed, and a new experiment was previewed/submitted.
The prior resource was reconciled through the normal API only after confirming
the old worker stopped, exact owned actors were destroyed, and native quarantine
and owned container were absent. The failed evidence was not rewritten.

Town01's official Lanelet2 map had no traffic-light regulatory elements. A
separate private revision derives 36 unique lane/stop-light associations from
native stop waypoints, with maximum 0.00304 m lateral and 0.01892° heading matching
error. The actual Autoware loader accepted the derived map. This geometric
correspondence and the straight-run pass **do not yet accept red-light stopping,
pedestrian crossing, cut-in, obstacle response or curved tracking**. Those
scenarios require their own frozen native executions. The nine-scenario suite
is not complete on the strength of these straight-route repetitions.

The seven local adapter tests at that historical qualification covered contract
boundaries. The expanded current suite also checks turning-origin transforms,
bbox corners and invalid-path control rejection. Authored-input native
ROS startup probes are saved separately and are not counted as driving results.
