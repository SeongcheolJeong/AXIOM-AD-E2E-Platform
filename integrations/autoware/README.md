# Optional Autoware planning/control

This package runs **native Autoware planning and MPC/PID with simulator ground
truth under an AXIOM simulation supervisor**. It does not run perception, NDT,
Autoware's complete ADAPI operation-mode/safety stack, or an E2E learned policy.
The root CARLA runner remains the only world-tick and vehicle-control owner.
Native startup/route probes are not driving acceptance.

In the platform, select the registered `city-autoware` controller for a Prius ego
in a pinned city scenario, then use the ordinary Scenario → Experiment preview →
queue workflow. The worker starts this optional process; launching the CLI by
itself does not submit a product execution. The native result keeps requested
route, Autoware trajectory, applied control, vehicle response and original RGB
separately. See [city authoring and API](../../docs/scenario-v2-city.md) and
[recorded native acceptance](ACCEPTANCE.md).

Current qualification is limited: an earlier source passed three Town01 straight
goal-stop trials. The current source's first Town10 route crashed in the native
mission planner before measured driving, so repeats and curve trials stopped.
The [retained failure](../../docs/validation/autoware-town10-driving-20261009.json)
remains an infrastructure failure, not a driving or sensor pass.

## Runtime contract

The runtime uses the installed Jazzy image by immutable digest:
`ghcr.io/autowarefoundation/autoware@sha256:00aae4988d137f042de42e744d24616054c9d1917c6b64562eabae3eb6a6fe17`.
It runs without GPU access in a private network namespace, domain 78 and explicit
CycloneDDS loopback discovery. Existing AWSIM/domain 42 profiles remain untouched.

```sh
PYTHONPATH=integrations/autoware/src python3 -m axiom_autoware.launcher \
  --map-dir /private/runtime/maps/Town01 \
  --output-dir /private/runtime/episode/autoware \
  --episode-id job_example
```

Standard input/output are JSON-lines. Logs and the runtime receipt are saved
under `--output-dir`. `op:init` must be followed by monotonically increasing
`op:step` observations and then `op:close`. Close terminates only that episode's
native process group/container. ROS/CARLA dependencies never enter the core API.

`axiom-autoware-init/1` fixes episode ID, map ID, Lanelet2 SHA256, goal/waypoints,
timestep (0.02–0.1s), speed cap (≤8m/s) and measured Prius geometry. Vehicle fields
are `blueprint`, `rear_axle_offset_m`, `max_steer_angle_rad`, `wheel_base`,
`wheel_tread`, `wheel_radius`, `front_overhang`, `rear_overhang`, `left_overhang`,
`right_overhang`, `vehicle_height`. The initial profile accepts only
`vehicle.toyota.prius`; vehicle substitutions require separate acceptance.

Each step supplies native `frame`, `sim_time_s`, `ego` pose/velocity/acceleration,
yaw rate and measured steering angle, other vehicle/pedestrian/static poses, dimensions
and velocities, and mapped Lanelet2 traffic-light states. Poses and vectors use
CARLA's left-handed world coordinates in meters and **radians**, with actor-center
origin for ego and bbox-center poses for other actors. The bridge changes
handedness and moves ego origin to the rear axle;
trajectory output reverses that transform. Planar velocity and acceleration move
to that same origin using yaw-rate, angular-acceleration and centripetal terms.
Angular acceleration is derived from consecutive native observations; the first
sample explicitly assumes zero derivative. Raw command steering is inverted.
Vehicle/pedestrian predicted paths use three-second constant-velocity
extrapolation, explicitly not actual future simulator ground truth.

The native route must be UNSET/ready before route submission. At least three
finite current trajectory points, a fresh trajectory and a successful route
response are needed before AXIOM simulation engagement and every subsequent
control decision. Invalid/short paths clear engagement and reject control;
pinned MPC can otherwise retain its earlier reference for paths shorter than
three points. A zero-speed trajectory is retained as stopped output,
never proof of successful driving. Future/stale commands, missing acceleration,
stale trajectory, reverse operation, clock gaps and duplicate frames fail closed.
The caller must retain and inspect rejected replies and apply a safe stop.

The pinned PID 0.50 binary writes acceleration while leaving the new
`is_defined_acceleration` flag unset. An exact-binary compatibility rule preserves
the original false flag and records its interpretation, PID binary hash and
upstream source hash. A different binary with an unset flag is rejected.

The low-level converter inverts the **installed upstream CARLA Prius** accel and
brake CSVs, caps throttle 0.4/brake 0.8, and scales steering using measured native
wheel limits. Its CSV hashes are in the receipt. This is simulator calibration,
not physical vehicle calibration.

## Maps and rights

`scripts/fetch_map.py --town Town01 --output /private/runtime/maps/Town01`
downloads the official CARLA Autoware Lanelet2 file at upstream commit
`062f94b5322e679ee0430e2daa6995dca9f7f0ea`, preserving source URL/hash/size.
Town10HD is available through the same script for Town10HD_Opt correspondence
testing. The upstream repository has no license file at its root: **map rights
remain unconfirmed**, independent of Autoware code or the CARLA engine license.
Maps stay in private runtime storage and are not bundled in this package.
No synthetic pointcloud map is passed off as a LiDAR map; this profile loads
Lanelet2 only. Native map/route/traffic-light correspondence requires acceptance.

`capture_signal_geometry.py` reads native stop waypoints without ticking or
loading worlds. `derive_signal_map.py` creates a separate private Lanelet2 revision
only for uniquely matched same-direction native stop waypoints, retaining all
unmatched/ambiguous records. Town01 produced 36 unique correspondences with
maximum 0.00304 m lateral/0.01892° heading error; all 36 regulatory elements loaded in
the actual pinned runtime. This map correspondence is separate from actual
traffic-light stopping acceptance. Required signal capabilities fail closed when
the map lacks regulations or native observations cannot map uniquely.

Town10HD_Opt has two native lane stops for each of 15 lights. The map derivation
script therefore emits one shared regulatory element for each physical light,
attached to its uniquely matched adjacent lanes. Native stop endpoints remain
unchanged. Opposite headings, missing lanes, medians and excessive longitudinal
offsets reject the group. The separate r2 map has 30 lane associations and 15
light regulations, accepted by the actual native ROS loader and a stationary
route probe; [its receipt](../../docs/validation/autoware-town10-map-20261009.json)
does not yet establish native driving or signal stopping. The original map and
earlier derived revision remain preserved. The subsequent native driving attempt
failed in mission planning on a different frozen route; see the failure report
above. Stationary map loading alone is not a general route qualification.

The planner's occupancy input is generated from Lanelet2 road polygons and GT
actor footprints; cells outside mapped roads remain unknown. Its obstacle cloud
contains GT bounding-box corners, not LiDAR samples or static environment
perception. Static scenario props use UNKNOWN classification and must have zero
velocity. Supplied actor poses are bbox centers, so cloud corners span positive
and negative half-height. The receipt preserves these input limitations and all runtime source
hashes. Only the optional broken RViz goal-visualizer include is removed in the
disposable container; original/derived launch hashes record that adaptation.

Official references:

- [Autoware CARLA tutorial](https://docs.autoware.org/main/demos/digital-twin-demos/carla-tutorial/)
- [Autoware CARLA interface](https://github.com/autowarefoundation/autoware_universe/blob/main/simulator/autoware_carla_interface/README.md)
- [Pinned MPC 0.50 trajectory handling](https://raw.githubusercontent.com/autowarefoundation/autoware_universe/0.50.0/control/autoware_mpc_lateral_controller/src/mpc_lateral_controller.cpp)
- [CARLA Autoware map repository](https://bitbucket.org/carla-simulator/autoware-contents/src/master/)

## Verification

```sh
PYTHONPATH=integrations/autoware/src .venv/bin/python -m unittest discover \
  -s integrations/autoware/tests -v
```

Tests cover coordinate/axle conversion, command freshness, clock ownership,
bounded actuator conversion and runtime isolation. `scripts/probe_runtime.py`
additionally probes the actual ROS image with stationary **authored input**; it
does not touch CARLA and cannot demonstrate native motion, stopping, obstacle
response or sensor validation. Native acceptance remains in the platform run
receipts, separate from these adapter checks.
