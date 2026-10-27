# Temporal sequences and reference planning replay

Implemented in the four-module expansion on 2026-10-07. Through **Log replay →
Experiments**, a user can retain a temporal input, pin replay intent, preflight
the exact sources, explicitly submit a job, inspect ego/object/control/observer
timelines, and download the native trace and immutable execution receipt.

This is the original-replay, reference open-loop planner and reference vehicle
feedback slice. The controller is `geometric-headway/1`; dynamics are
`planar-kinematic-bicycle/1`. It does not qualify a learned driving policy,
calibrated vehicle dynamics, reconstructed camera observations or road safety.

## Temporal input and source limits

`SceneSequence` is separate from the existing bounding-box image Dataset.
`POST /api/v1/scene-sequences` retains an immutable source snapshot and its SHA.
The two explicit source variants are:

- `authored_reference`: bounded explicit ego/object frames, always marked
  `synthetic`. The UI's lead-vehicle example belongs to this variant.
- `recording`: existing MCAP reference, exact ego ordinals, supported channel
  schemas, original log/publish/capture clocks, message/content hashes,
  predecessor object identities and declared clock offsets/maximum ages.

The recorded decoder accepts **exact** JSON schema definitions published by
`GET /api/v1/replay-specs/capabilities` and
`GET /api/v1/scene-sequences/sources/{recording_id}`:

| Schema | Required observations |
| --- | --- |
| `axiom.PlanningEgo.v1` | Capture timestamp, world frame, X/Y position, yaw, speed, length/width, declared source |
| `axiom.ObjectTracks.v1` | Capture timestamp, coordinate frame, unique object IDs, X/Y/yaw/length/width, declared source and `absolute_ground_velocity` semantics; velocities optional but both components required together |

Coordinates are planar right-handed Z-up, metres and radians. `world` objects
use the explicitly selected world frame. `ego` objects use the original ego
reference origin: their positions and absolute ground velocities are expressed
in the original ego axes. Relative observations are transformed through the
**exact original world ego pose at the aligned object observation time**, before constructing
the new ego-relative replay observation. They are never attached to the updated
vehicle as if they moved with it. This slice does not interpolate or extrapolate
a missing world pose from a nearby timestamp.

Existing `axiom.EgoState.v1` has no world pose. Topic names, speed alone or a
camera sequence cannot replace these missing inputs. An unsupported schema or
missing pose/dimension is rejected; missing/stale object messages remain explicit
omissions. Future messages are never predecessor fallbacks. Additional sensor
references require the exact existing rig/binding revision, decoder, clock,
maximum age and original message identities; they remain **nonreactive references**
and are not reference-planner inputs.

Limits: 512 decision steps, 128 objects per observation, up to 16 additional
sensor references, 256 KiB per decoded JSON message, 16 MiB frozen source,
strictly increasing steps of at most one second. Source snapshot and retained
message bytes are rechecked before execution. Legacy recorded resources remain
default-workspace only; they cannot be imported into another new project without
an implemented ownership boundary.

## Replay intent and actual feedback

`POST /api/v1/replay-specs` pins the sequence ID/hash, mode, controller,
dynamics, observers, optional explicit goal and note. A new spec is a new
immutable object. `POST /api/v1/replay-specs/{id}/preflight` reports executable
scope, missing/stale inputs, original denominator, source kind and warnings.

| Mode | Observations | Effect of controller output |
| --- | --- | --- |
| `original_replay` | Original retained ego and objects | No controller execution |
| `open_loop` | Original retained ego and objects | Next ego is predicted for diagnosis; it does not replace subsequent logged ego |
| `planning_closed_loop` | Retained world objects observed from updated ego | Controller acceleration/steering updates the pinned reference bicycle, then that new ego creates the next observation |

Closed loop requires at least two input times and complete causal object
observations at every selected step. Surrounding actors follow the retained
world observations and do **not** react to the changed ego. Logged camera pixels
cannot respond to ego motion; camera E2E closed-loop substitution is rejected.
Only the supported fixed reference controller/evaluator executes here. Unknown
model, sensor and evaluator selections fail preflight.

The reference headway controller limits speed using observed object corridor
geometry, declared cruise/headway/braking parameters and bounded heading/steer
control. The bicycle uses trapezoidal speed and midpoint yaw integration. These
choices are fixed by the kernel version, not presented as measured dynamics.
Each tick preserves input identities, source ego/world objects, changed ego
observation, control, next state, metrics and a prefix hash. The seed is retained;
this deterministic kernel makes no stochastic draws.

## Observers and product evidence

- Collision: oriented rectangle overlap at each discrete observed step.
- Distance: rectangle boundary clearance, including zero for overlap.
- TTC: fixed-yaw swept-rectangle overlap under constant declared velocities.
  Missing object velocity stays unevaluated; no future trajectory is inferred.
- Speed, finite-difference acceleration/jerk and error versus retained ego.
  Initial steps without the required prefix stay unevaluated.
- Goal arrival requires an explicit world goal. Lane departure remains
  unevaluated because retained map/lane geometry is not implemented.

Empty observed tracks and missing observations are distinct. `collision_steps`
is a diagnostic count, not a generic safety verdict. The result says
`diagnostic_not_safety_validation` and preserves every observer's evaluated and
unevaluated denominator. Discrete collision cannot guarantee continuous-time
collision avoidance. Original ego is a retained reference, not an automatically
reviewed trajectory ground truth.

`reference-planning-replay/1` runs as `kind=planning_replay` in the existing
Experiment queue, with `input={"replay_spec_id":"..."}`. Preview pins versions
and hashes; authoring, explicit submit, cancellation, lease fencing and result
publication use the common queue. `planning-trace.ndjson` contains all selected
steps; `planning-manifest.json` pins the trace and spec. The UI preview is bounded
to 128 ticks and 20 objects per tick and tells the user when it is truncated.
Native downloads retain the complete trace. UI and SDK use the same versioned
API; Viewer/Reviewer can inspect, Engineer/Admin can author and execute.

## Acceptance

17 scoped backend checks and four SDK checks passed, including authored MCAP
uint64 clocks, exact identities, causal alignment, original-pose coordinate
transforms, missing/stale refusal, original/open-loop distinction, actual
reference-control feedback, OBB/TTC geometry, repeat hashes, cancellation,
retained-byte corruption, unsupported substitutions and project roles/isolation.
Two Chrome workflows passed normal authoring → preflight → queue → worker →
timeline/metrics → SHA-verified receipt download and unsupported camera-model
rejection before dispatch. The checked fixtures are authored synthetic inputs;
**no measured road object-track replay acceptance is claimed**.

The isolated browser acceptance workspace is
`.runtime/four-module-log-e2e-20261007`; the operating database/server was not
migrated or restarted. Commercial release and measured-data gates remain open.
