# Native city scenarios and YAML authoring

Updated: 2026-10-09. This document describes the additive `scenario-v2/2`
authoring and catalog contract. Native execution acceptance and deployment status
belong in `status.md` and the recorded acceptance report. A stored scenario,
catalog entry, planned route or successful contract test does not establish that
CARLA or Autoware ran.

## Workflow

The existing Object Sim authoring workspace can use a registered CARLA city,
registered actor assets and pinned controller configuration. Start, intermediate
points and goal are snapped to the city's directed native lane graph. The editor
and YAML use the same normalized API representation. Save creates an immutable
scenario revision; execution uses that revision through the existing Experiment
queue. There is no second scheduler or mutable file that overrides a queued
scenario.

1. Select the project and registered map revision. Read its native source, rights,
   coordinate system, lane geometry and spawn locations.
2. Select actor assets. Assign exactly one ego and one motion controller per actor.
3. Choose start/via/goal on the map and validate the directed route. A route cannot
   jump between disconnected roads merely because their display lines intersect.
4. Edit JSON-backed controls or YAML; validation returns canonical YAML and the
   semantic configuration hash. Original YAML comments remain in the saved source.
5. Review duration, sensors, controller availability and fixed acceptance criteria.
6. Save, preview the selected adapter's workload, then submit through Experiments.
7. Inspect the actual execution, original sensor artifacts, requested route, native
   trace and evaluation separately. Missing data or a failed controller remains
   visible; a completed job can have a failed test verdict.

## Immutable references and hashes

Each map, asset and controller reference is `{id, revision, sha256}` in the same
project. The reference SHA-256 is the **catalog revision manifest hash**, not the
map's OpenDRIVE hash. The map catalog also preserves original OpenDRIVE UTF-8 bytes
and their separate SHA-256. Editing metadata, graph geometry, rights declarations
or controller image information creates a new catalog revision; older scenarios
keep their old references.

Every scenario revision stores the normalized validated scenario and manifest
hash. YAML imports additionally store `source_yaml` and `source_yaml_sha256`.
`configuration_hash` hashes only normalized scenario data: comments, field order,
whitespace and explicitly spelling an unchanged default do not change it. Changing
the seed, route, sensor, acceptance criterion or a pinned reference does change it.
Revisions are never updated to point at a new catalog automatically.

YAML is data only. Duplicate keys, unknown fields, non-string mapping keys,
executable tags, aliases, merge keys, cyclic references, multiple documents and
excessive depth/size are rejected. JSON text is also accepted by the YAML endpoint.
Original YAML supplied with a JSON create/revise request must normalize to exactly
the submitted scenario, or the API rejects it.

The old `scenario-v2/1` straight-road/esmini contract and stored original manifests
remain readable. Its OpenSCENARIO subset import/export remains unchanged. Native
city schema 2 has no lossless OpenSCENARIO exporter yet; requesting that export
fails explicitly instead of silently losing city/controller information.

## Scenario 2 contract

| Field | Meaning |
| --- | --- |
| `schema_version` | `scenario-v2/2` |
| `name`, `description` | Authored purpose; text is not an instruction to an agent |
| `seed` | Integer 0–2147483647, matching the shared Experiment API, frozen with the scenario |
| `map` | Exact project catalog reference |
| `coordinate_system` | `carla_world_m_x_forward_y_right_z_up`; CARLA's left-handed world, metres and radians |
| `duration_s`, `timestep_s` | Schema bounds: 1–120 seconds, 0.02–0.1 seconds; native adapters may support a narrower range |
| `actors` | 1–32 unique actors and exactly one ego |
| `sensors` | Explicit RGB camera attachments, resolution, FOV, local pose and period |
| `environment` | Currently declared day/dry; arbitrary weather/optical physics are not implied |
| `observers` | Existing bounded observer declarations; unsupported native observers must be rejected by the adapter |
| `acceptance` | Criteria frozen before execution; native contacts and measured tracking/arrival stay distinct |
| `signals` | Optional schedules for pinned map OpenDRIVE traffic-light IDs; explicit initial state and ordered changes |

Actor `pose` and each route waypoint contain `x, y, z, heading_rad`. `asset` pins
the native blueprint metadata. `speed_mps` is a requested speed, not a measured
velocity. Asset dimensions can be unknown before a native spawn; native result
bounds must then be recorded rather than inferred from a generic box.

The current CARLA runner adds an explicit fixed 0.3 m to authored spawn Z and
performs 20 setup ticks before retaining the measured interval. A map's road
height is not automatically a collision-free actor origin: walkers and vehicles
have different origin/bounding-box conventions. Preview cannot establish native
spawn clearance without creating an actor. A rejected native spawn remains an
initialization failure; author a new revision instead of silently moving the
actor. The Town10 pedestrian acceptance revision raises only the authored start
Z from 0 to 1 m, preserves the XY route and criteria, and records the settled
native pose separately. The original spawn/cleanup failure remains retained.

Controller modes are distinct:

| Mode | Authored intent |
| --- | --- |
| `timed_replay` | A strictly increasing timed trajectory; not feedback route tracking |
| `route_follow` | Feedback tracking of a requested route |
| `traffic` | Route following with explicit headway and signal policy |
| `autoware` | Ego vehicle controlled by the selected pinned Autoware revision |
| `static` | Zero-speed placement with no motion path |

The controller also records `end_behavior` (`stop` by default, or explicit
`repeat`), `wait_s`, optional `stop_time_s`, `headway_s` and `obey_signals`.
Wait/stop times must be ordered and fit inside the scenario. Timed replay cannot
also own a route; a non-replay controller cannot contain a timed trajectory.
Autoware requires an ego vehicle and a pinned controller reference. These schema
options describe authoring; the chosen execution adapter must independently
reject any mode or parameter that it does not implement.

`route_mode: authored_lane_change` explicitly marks an authored S-curve for a
physical vehicle under `route_follow` or `traffic`. It is not a lane-change
planner or an assertion of legal right of way. The native adapter must check the
entire path against one same-direction adjacent-lane corridor. Optional
`required_gap_m` withholds initiation until the bounded native gap condition is
satisfied; a manoeuvre that never starts must not count as an exercised cut-in.
The default remains `native_lane_graph`. These additive default fields are
omitted during canonical serialization so existing schema-2 hashes stay valid.

Signal schedules have `{id, initial_state, changes:[{time_s,state}]}`. States are
`red`, `yellow` and `green`; change times are strictly increasing and no later
than the last command time, `duration_s - timestep_s` (1 ns numeric tolerance).
Commands are applied before each response tick; a change at `duration_s` would
never be issued and is rejected. IDs must belong to registered traffic lights in the pinned map and
must be stable OpenDRIVE IDs, not transient episode actor IDs. Native execution
must apply/freeze and observe the requested states. Merely declaring red in YAML
does not establish a red-light trial. No stop-line legality verdict is provided
without corresponding geometry and a crossing evaluator.

RGB sensor periods must be an integer multiple of the simulation timestep. The
API does not infer RAW, calibrated optics, an ISP pipeline or perception-model
input from an RGB attachment. Native archival limits and workload admission are
checked by the selected execution adapter.

Default frozen acceptance:

```yaml
acceptance:
  max_lateral_error_p95_m: 0.5
  max_goal_error_m: 2.0
  max_final_speed_mps: 0.2
  require_goal: true
  require_no_contacts: true
```

For an intentionally interrupted lead-stop or stress case, `require_goal` may be
false in its authored revision. Changing that choice after a run creates a new
scenario configuration; it cannot retroactively turn existing evidence into a
pass. Sampled bounding-box overlap is not a substitute for native contact events.

## Catalog and route APIs

All paths below have the `/api/v1` prefix. Project Viewer can read and preflight;
Engineer or Admin can author. Each endpoint appears in the generated agent tool
catalog with its current OpenAPI schema and access requirements.

| Method/path | Result |
| --- | --- |
| `GET /maps`, `/assets`, `/controllers` | Current revisions, project-scoped and paginated |
| `POST /maps`, `/assets`, `/controllers` | Append `{project_id,id,expected_revision,definition}`; 0 means create |
| `GET /maps/{id}?project_id=…&revision=…` | Full native map manifest and derived display lane chains |
| `GET /assets/{id}`, `/controllers/{id}` | Exact immutable revision |
| `POST /maps/{id}/route` | Directed lane snap/routing using the exact revision hash; creates no scenario or job |
| `POST /scenario-v2/yaml/validate` | `{project_id,yaml}` → normalized scenario, canonical YAML, configuration hash, catalog warnings |
| `POST /scenario-v2/yaml/import` | Same input plus `Idempotency-Key` → saved immutable scenario |
| `POST /scenario-v2` | JSON scenario, optionally matching `source_yaml` |
| `POST /scenario-v2/{id}/revisions` | `expected_revision`, scenario and optionally matching `source_yaml` |
| `GET /scenario-v2/{id}/export?format=yaml` | Canonical YAML and response content hash |
| `GET /scenario-v2/{id}/export?format=source_yaml` | Exact original YAML, or 404 for a JSON-only revision |
| `GET /scenario-v2/{id}/export?format=opendrive` | Original pinned native map XML |

Route requests contain `project_id`, `revision`, `sha256`, `start`, `goal`, optional
`via` and `max_snap_distance_m` (default 5, maximum 10). The response includes
snapped poses, ordered native `node_ids`, `route`, `distance_m` and `route_hash`.
Outside-tolerance, opposite-facing and unreachable selections return 422. A
changed reference hash returns 409. Native lane direction is preserved; no route
is synthesized across disconnected graph components.

Map definitions retain original OpenDRIVE, sampled native directed nodes, spawn
locations and signals. A map list omits the large `nodes` and `opendrive` fields;
load a specific revision for them. Controller definitions declare adapter version,
mode, status, capabilities and optional pinned image digest. An unavailable
controller can be authored with an explicit warning; execution must remain blocked
until the adapter's runtime preflight succeeds.

The current native reference controller requires the catalog capability
`control_math:axiom-city-pure-pursuit/2`. It steers from the measured rear axle,
while route/goal metrics retain the native actor origin used by the frozen
criterion. Old controller revisions are not silently upgraded: preview rejects
an incompatible revision, and the operator must create a new scenario revision
with an explicit new controller reference. Catalog rediscovery preserves existing
controller definitions. The engine's raw steering curve is retained; an
unverified steering gain is not fitted to make a failed trial pass.

Code, map, asset, dataset, model-weight and engine rights are separate. Catalog
`rights` is a declaration with source URL, license text identifier, redistribution
status and notes. Importing a catalog does not grant redistribution rights, and
the web viewer does not extract native proprietary meshes.

## Python SDK

The ordinary `AxiomClient` exposes `simulation_catalog`,
`simulation_catalog_revision`, `register_simulation_catalog`, `plan_city_route`,
`validate_scenario_yaml`, `import_scenario_yaml`, `create_scenario_v2`,
`revise_scenario_v2`, `scenario_v2`, `scenarios_v2` and `export_scenario_yaml`.
They use the same endpoints as the UI. YAML export verifies the response content
hash before returning text. The existing `preview_experiment`,
`create_experiment`, `submit_experiment` and `experiment_execution` methods remain
the execution path; catalog registration and YAML validation never schedule work.

## Storage and verification boundary

The existing Experiment Verification workspace has an additive
`native-city-observers/1` profile for `native_carla_object_physics` executions.
It accepts `native_contact` (boolean; actor ID or `all`), `lateral_error_p95` (m),
`goal_error` (m), `final_speed` (m/s), `maximum_speed` (m/s) and
`distance_travelled` (m). Native contacts
come from collision sensor events. The legacy `collision` observer is not aliased
to native contact; requesting it from this profile remains unevaluated.

The profile reads sealed `city-scene.json`, `city-evaluation.json` and `map.xodr`,
verifies their content hashes and joins them to frozen scenario, controller,
adapter and server versions. Aggregates use the entire retained frame sequence.
Missing originals remain unevaluated; modified source/hash/metric joins are
rejected. A completed trial that violates a criterion is a SUT failure, while an
execution failure remains an infrastructure failure. An uncertain execution
cannot pass. Existing verification-plan revisions and old evaluator receipts
remain readable; new evaluations use `typed-experiment-observers/2`.

## Bounded acceptance suite

`scripts/carla_city_acceptance.py` provides `draft`, `author` and `run` commands
through the same public API. Draft/author never submit simulation jobs. Author
creates new scenario and verification-plan IDs, preserves fixed catalog refs and
criteria in a hashed suite manifest, and saves an authoring checkpoint. Existing
user scenarios are not revised. Run checks that manifest and submits at most
three repeats serially through the existing queue. Each repeat retains exactly
the same scenario and seed; it is not a claim of independent scene diversity or
byte-identical physics.

The nine requested case categories are straight, curve, left turn, right turn,
lead stop, pedestrian crossing, obstacle response, fixed red approach/stop and
physical cut-in. Geometry is selected from each map's directed graph. Unsupported
map/capability combinations remain explicit gaps in the suite manifest. The
cut-in selects same-direction adjacent lanes and labels the path as authored.
Fixed-red cases schedule registered lights red and verify retained native states;
their scope is approach and stop under a fixed red input, not traffic-law
compliance.

Every baseline requires at least 2 m of ego movement, so a stationary episode
cannot pass a stop test. Lead-stop/obstacle/red cases waive goal arrival but keep
the fixed final-speed criterion. Crossing and cut-in also require the participant
to reach its goal and stop; a gap condition that never opens cannot masquerade
as a completed manoeuvre. The result retains the criterion verdict separately
from `exercise_status`: `not_exercised_as_planned` prevents an uncompleted
participant manoeuvre being described as an exercised scenario, even if unrelated
ego checks passed. Infrastructure/preflight failures stop further submissions and
preserve the exact input and known execution IDs for investigation. Thresholds
are never adjusted from the result.

Example preparation (no computation):

```sh
.venv/bin/python scripts/carla_city_acceptance.py author \
  --api http://127.0.0.1:8031/api/v1 --project default \
  --suite-id my-reviewed-city-suite \
  --output .runtime/my-reviewed-city-suite.json
```

After the native execution gate is satisfied, `run --suite <saved-file>
--repeats 3 --output <results-file>` submits the immutable suite serially. A
configured Autoware catalog can be selected at authoring with
`--ego-controller autoware`; configuration availability does not imply native
driving acceptance.

`run --map <catalog-map-id>` selects one map (repeat for multiple maps) while
retaining the original suite hash and recording selected/excluded case counts.
The result file must differ from the sealed source suite. Run requires an
already configured operator worker for that API workspace; it does not launch
a worker or claim unrelated queued jobs. Poll timeouts preserve the existing
execution as an unknown outcome and never automatically dispatch it again.

Native preview rejects legacy `observers` because this runner does not execute
that evaluator contract; use the fixed acceptance profile or supported
experiment verification criteria. The Autoware bridge always obeys signals,
so `obey_signals: false` is rejected before dispatch. Timed replay remains
available as diagnostic visualization, but its displacement/speed are not
physical motion qualification. Mixed replay contact criteria remain unevaluated;
physical actors can still supply their own route tracking measurements with
that context recorded.

Contact observations are the union of callbacks from native vehicle collision
sensors. `actor_id: all` means that retained callback union, not instrumentation
of every pedestrian/static-body pair. A pedestrian with no reported contact
cannot qualify a contact-absence criterion without its own sensor. Native
contact observations and path tracking do not establish physical-world safety.

Schema 19 adds `simulation_catalog_revisions` only. Existing scenarios, queued
jobs, original artifacts and sealed results are not rewritten. Migration tests
exercise concurrent initialization, byte-preserving old rows and transaction
rollback on an injected commit failure. Backend tests exercise YAML attacks,
semantic roundtrips, revision conflicts, cross-project access, directed routing,
controller bindings and generated agent contracts. SDK checks exercise shared
transport and download integrity. These are contract/implementation checks,
separate from a real simulator run, Autoware driving acceptance or physical sensor
validation.
