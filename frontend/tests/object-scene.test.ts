import test from "node:test";
import assert from "node:assert/strict";
import { actorCenter, actorColor, actorCorners, groupTimelineEvents, nativeOverlap, parseNativeTrace, parsePinnedActors, parseSceneEvidence, parseStraightMap, sceneBounds, deriveTraceEvents, retainedLogActionEvents, perspectiveFitDistance, SCENE_CAMERA_DIRECTION, type NativeActorState, type ObjectExecution, type SceneEvent } from "../src/components/object-scene/object-scene.ts";
import { actorColor as authoringActorColor } from "../src/scenario-authoring.ts";

// Authored rejection fixtures exercise the viewer contract, never simulator acceptance.
const hash = "a".repeat(64), coordinates = "world_m_x_forward_y_left_z_up";
function road(length = 500, width = 3.5) {
  const lane = (id: number) => `<lane id="${id}" type="driving" level="false"><link /><width sOffset="0" a="${width}" b="0" c="0" d="0" /><roadMark sOffset="0" type="${id === -1 ? "broken" : "solid"}" weight="standard" color="standard" width="0.12" laneChange="both" /></lane>`;
  return `<?xml version='1.0' encoding='utf-8'?><OpenDRIVE><header name="axiom-straight-road-r1" /><road name="AXIOM authored straight road" length="${length}" id="1" junction="-1"><link /><planView><geometry s="0" x="0" y="0" hdg="0" length="${length}"><line /></geometry></planView><lanes><laneSection s="0"><center><lane id="0" type="none" level="false"><link /></lane></center><right>${lane(-1)}${lane(-2)}</right></laneSection></lanes></road></OpenDRIVE>`;
}
function state(id: string, x = 0, y = -1.75): NativeActorState { return { id, x, y, z: 0, heading_rad: 0, speed_mps: 2, vx_mps: 2, vy_mps: 0, ax_mps2: 0, ay_mps2: 0, bb_x: 1.4, bb_y: 0, length_m: 4.5, width_m: 1.8, lane_id: -1, lane_offset_m: 0 }; }
function fixture() {
  const map = { kind: "authored_straight_road", id: "axiom-straight-road-r1", length_m: 500, lane_width_m: 3.5 };
  const actors = ["ego", "lead", "walker"].map((id) => ({ id, kind: id === "walker" ? "pedestrian" : "vehicle", role: id === "ego" ? "ego" : "other", dimensions: { length_m: id === "walker" ? .6 : 4.5, width_m: id === "walker" ? .6 : 1.8, height_m: id === "walker" ? 1.7 : 1.5 }, pose: { x: 0, y: -1.75, heading_rad: 0 }, speed_mps: id === "walker" ? 0 : 2, route: [], actions: [] }));
  const trace = { schema_version: "object-trace/1", coordinate_system: coordinates, frames: Array.from({ length: 6 }, (_, i) => ({ time_s: i / 5, actors: [state("ego", i * 2), state("lead", 8), { ...state("walker", 25, -5), length_m: .6, width_m: .6, bb_x: 0 }] })) };
  const artifacts = ["road.xodr", "object-trace.json", "scenario.xosc", "native-trace.csv", "engine.log"].map((name, index) => ({ id: `a${index}`, name, media_type: name.endsWith("json") ? "application/json" : "application/xml", size_bytes: 1000, sha256: hash, download_url: `/api/v1/experiments/executions/job-fixed/artifacts/a${index}` }));
  const execution: ObjectExecution = { id: "job-fixed", experiment_id: "experiment-fixed", project_id: "default", experiment_kind: "object_sim", status: "completed", adapter_id: "esmini-object", adapter_version: "0.2.0+esmini3.8.2", input_hash: hash, manifest_hash: hash, download_url: "/api/v1/experiments/executions/job-fixed/export", fidelity: "authored_object_kinematics", artifacts };
  const input = { schema_version: "object-sim-input/1", project_id: "default", scenario_id: "scenario-fixed", scenario_revision: 3, scenario_manifest_hash: hash, configuration_hash: hash, seed: 42, adapter_version: execution.adapter_version, scenario: { schema_version: "scenario-v2/1", coordinate_system: coordinates, map, duration_s: 1, timestep_s: .2, actors }, native_request: { road_xml: road(), actor_ids: actors.map((actor) => actor.id), duration_s: 1, timestep_s: .2, seed: 42 } };
  const sealed = { schema_version: 1, execution: { ...execution, summary: { engine_receipt: { engine_version: "v3.8.2", engine_build: "6400", binary_sha256: hash, runner_sha256: hash } } }, prepared: { schema_version: 1, spec: { project_id: "default", adapter_id: "esmini-object", adapter_version: execution.adapter_version, seed: 42 }, input } };
  return { sealed, execution, input, trace, map, actors, road: road() };
}
function nativeScene(v = fixture()) {
  const legacy = parseSceneEvidence(v.sealed, v.trace, v.road, v.execution);
  return { schema_version: "object-scene/1", coordinate_system: coordinates, fidelity: "authored_object_kinematics", physical_sensor_validation: false, source: { ...legacy.source, engine_version: "v3.8.2", engine_build: "6400", engine_binary_sha256: hash, runner_sha256: hash, scenario_xml_sha256: hash, road_xml_sha256: hash, native_csv_sha256: hash, native_log_sha256: hash, object_trace_sha256: hash }, map: { ...legacy.map, source_artifact: "road.xodr", source_sha256: hash, geometry_provenance: "validated_pinned_opendrive_line_and_lane_widths" }, actors: legacy.actors.map((actor) => ({ ...actor, metadata: { origin: "pinned_scenario_revision", height_source: "authored_scenario_not_native_observation" } })), frames: v.trace.frames, events: legacy.events.map((event) => ({ ...event, end_time_s: event.end_time_s ?? null, provenance: { ...event.provenance, artifact: "object-trace.json", sha256: hash, frame_indices: [] as number[], line_numbers: [] as number[] } })) };
}
test("legacy scene preserves all native samples, stable actor kind/role and exact authored map", () => {
  const v = fixture(), parsed = parseSceneEvidence(v.sealed, v.trace, v.road, v.execution);
  assert.equal(parsed.provenance, "legacy_verified_derivation"); assert.equal(parsed.frames.length, 6); assert.equal(parsed.actors.length, 3);
  assert.equal(parsed.actors[2].kind, "pedestrian"); assert.equal(parsed.actors[0].role, "ego"); assert.deepEqual(parsed.map.lanes.map((lane) => lane.centerline[0].y), [-1.75, -5.25]);
  assert.equal(parsed.map.reference_line[1].x, 500); assert.equal(parsed.frames[5].actors[0].bb_x, 1.4); assert.equal(parsed.source.scenario_revision, 3);
  assert.ok(parsed.events.some((event) => event.kind === "collision")); assert.ok(sceneBounds(parsed, true).xmax > 500);
});
test("native bounding box offset rotates with heading, preserving engine origin", () => {
  const value = { ...state("ego", 3, 4), heading_rad: Math.PI / 2, bb_x: 1.4, bb_y: .2 };
  const center = actorCenter(value); assert.ok(Math.abs(center.x - 2.8) < 1e-12); assert.ok(Math.abs(center.y - 5.4) < 1e-12);
  assert.equal(value.x, 3); assert.equal(value.y, 4); assert.equal(actorCorners(value).length, 4);
  assert.equal(nativeOverlap(value, { ...value, x: value.x + 20 }), false);
  assert.equal(nativeOverlap(value, value), true);
});
test("stable actor colors match authored and replayed scenes exactly", () => {
  const ids = ["ego", "lead", "trailing", "lane_changer", "pedestrian"];
  for (const id of ids) assert.equal(actorColor(id, id === "ego" ? "ego" : "other"), authoringActorColor(id));
  assert.equal(new Set(ids.map((id) => actorColor(id))).size, ids.length);
});
test("3D perspective fit contains all near/far corners for square, road and tall bounds across viewport aspects", () => {
  const direction = SCENE_CAMERA_DIRECTION, h = Math.hypot(direction.x, direction.z), right = { x: direction.z / h, z: -direction.x / h }, up = { x: direction.y * right.z, y: direction.z * right.x - direction.x * right.z, z: -direction.y * right.x };
  for (const [width, depth] of [[100, 100], [516, 23], [100, 250]]) for (const aspect of [.5, 1, 640 / 425, 3]) {
    const distance = perspectiveFitDistance({ xmin: 0, xmax: width, ymin: 0, ymax: depth }, aspect, 45, { min: 0, max: 5 });
    for (const x of [-width / 2, width / 2]) for (const z of [-depth / 2, depth / 2]) for (const y of [0, 5]) {
      const viewDepth = distance - x * direction.x - y * direction.y - z * direction.z, tan = Math.tan(Math.PI / 8);
      const ndcX = (x * right.x + z * right.z) / (viewDepth * tan * aspect), ndcY = (x * up.x + y * up.y + z * up.z) / (viewDepth * tan);
      assert.ok(Math.abs(ndcX) < .95, `x clipped ${width}×${depth} aspect ${aspect}`); assert.ok(Math.abs(ndcY) < .95, `y clipped ${width}×${depth} aspect ${aspect}`);
    }
  }
});
test("scene contract matches full trace, map and source; nullable event interval is supported", () => {
  const v = fixture(), scene = nativeScene(v), parsed = parseSceneEvidence(v.sealed, v.trace, v.road, v.execution, scene);
  assert.equal(parsed.provenance, "object-scene/1"); assert.deepEqual(parsed.frames, v.trace.frames); assert.equal(parsed.events[0].provenance.sha256, hash);
});
test("lane and stopped markers use actual neighboring samples, without synthesizing authored intent", () => {
  const frames = [{ time_s: 0, actors: [state("ego")] }, { time_s: .2, actors: [{ ...state("ego"), lane_id: -2, speed_mps: 0 }] }];
  const events = deriveTraceEvents(frames); assert.deepEqual(events.map((event) => [event.kind, event.time_s]), [["lane_transition", .2], ["stopped", .2]]);
});
test("legacy engine action markers distinguish terminal state and preserve exact log line identity", () => {
  const v = fixture(); v.actors[1].actions.push({ kind: "lane_change" } as never);
  const scene = parseSceneEvidence(v.sealed, v.trace, v.road, v.execution);
  const log = '[0.400] [info] lead_0_event standbyState -> startTransition -> runningState\n[0.400] [info] lead_0_event runningState -> endTransition -> completeState\n[0.600] [info] stranger_0_event runningState -> endTransition -> completeState';
  const events = retainedLogActionEvents(scene, log, hash);
  assert.equal(events.length, 2); assert.deepEqual(events.map((event) => event.details.engine_state), ["runningState", "completeState"]);
  assert.deepEqual(events[1].provenance.line_numbers, [2]); assert.equal(events[1].provenance.sha256, hash); assert.equal(events[0].time_s, .4);
  assert.ok(!scene.events.some((event) => event.kind === "lane_transition"));
});
test("timeline coalesces exact time/kind hit targets while retaining independent native sources and action states", () => {
  const marker = (kind: string, time: number, source: string, details: Record<string, unknown> = {}): SceneEvent => ({ kind, time_s: time, actor_ids: ["ego"], provenance: { source, method: source === "native_trace" ? "sampled_obb_overlap" : "esmini_event_state_log" }, details });
  const trace = marker("collision", 6.6, "native_trace"), nativeLog = marker("collision", 6.6, "native_log"), begin = marker("native_action", 2.1, "native_log", { engine_state: "runningState" }), end = marker("native_action", 2.1, "native_log", { engine_state: "completeState" }), stop = marker("stopped", 2.1, "native_trace"), later = marker("collision", 6.7, "native_trace");
  const originals = [nativeLog, begin, trace, end, stop, later], groups = groupTimelineEvents(originals);
  assert.equal(groups.length, 4); assert.equal(groups.flatMap((group) => group.events).length, originals.length);
  assert.deepEqual(groups.find((group) => group.kind === "collision" && group.time_s === 6.6)?.events, [nativeLog, trace]);
  assert.deepEqual(groups.find((group) => group.kind === "native_action")?.events.map((event) => event.details.engine_state), ["runningState", "completeState"]);
  assert.equal(groups.filter((group) => group.time_s === 2.1).length, 2); assert.equal(originals[0], nativeLog);
});
for (const [label, corrupt] of [
  ["foreign sealed execution identity", (v: ReturnType<typeof fixture>) => { v.sealed.execution.id = "job-other"; }],
  ["different sealed input version", (v: ReturnType<typeof fixture>) => { v.sealed.prepared.schema_version = 2; }],
  ["different artifact digest", (v: ReturnType<typeof fixture>) => { v.sealed.execution.artifacts = structuredClone(v.execution.artifacts); v.sealed.execution.artifacts[0].sha256 = "b".repeat(64); }],
  ["different original road bytes", (v: ReturnType<typeof fixture>) => { v.road = v.road.replace('length="500"', 'length="501"'); }],
  ["different native coordinate frame", (v: ReturnType<typeof fixture>) => { v.trace.coordinate_system = "world_y_right"; }],
  ["missing native actor", (v: ReturnType<typeof fixture>) => { v.trace.frames[2].actors.pop(); }],
  ["duplicate native actor", (v: ReturnType<typeof fixture>) => { v.trace.frames[2].actors[2].id = "ego"; }],
  ["unbounded native coordinate", (v: ReturnType<typeof fixture>) => { v.trace.frames[2].actors[0].x = 10001; }],
  ["missing native bbox offset", (v: ReturnType<typeof fixture>) => { delete (v.trace.frames[2].actors[0] as Partial<NativeActorState>).bb_x; }],
  ["nonfinite saved sample", (v: ReturnType<typeof fixture>) => { v.trace.frames[2].actors[0].heading_rad = NaN; }],
  ["unordered native timestamps", (v: ReturnType<typeof fixture>) => { v.trace.frames[2].time_s = .2; }],
  ["incomplete retained observation window", (v: ReturnType<typeof fixture>) => { v.trace.frames = v.trace.frames.slice(0, 3); }],
] as const) test(`fails closed for ${label}`, () => { const v = fixture(); corrupt(v); assert.throws(() => parseSceneEvidence(v.sealed, v.trace, v.road, v.execution)); });
for (const [label, corrupt] of [
  ["changed road source hash", (s: ReturnType<typeof nativeScene>) => { s.source.road_xml_sha256 = "b".repeat(64); }],
  ["changed scenario revision", (s: ReturnType<typeof nativeScene>) => { s.source.scenario_revision = 4; }],
  ["changed full frame", (s: ReturnType<typeof nativeScene>) => { s.frames[0].actors[0].x = 2; }],
  ["changed authored actor role", (s: ReturnType<typeof nativeScene>) => { s.actors[1].role = "ego"; }],
  ["unestablished physical claim", (s: ReturnType<typeof nativeScene>) => { s.physical_sensor_validation = true; }],
  ["foreign event source hash", (s: ReturnType<typeof nativeScene>) => { s.events[0].provenance.sha256 = "b".repeat(64); }],
  ["foreign event sample", (s: ReturnType<typeof nativeScene>) => { s.events[0].provenance.frame_indices = [999]; }],
] as const) test(`scene contract rejects ${label}`, () => { const v = fixture(), scene = structuredClone(nativeScene(v)); corrupt(scene); assert.throws(() => parseSceneEvidence(v.sealed, v.trace, v.road, v.execution, scene)); });
test("unsupported OpenDRIVE curves, lanes, widths and entity declarations fail closed", () => {
  const v = fixture();
  for (const invalid of [v.road.replace("<line />", '<arc curvature="0.1" />'), v.road.replace('id="-2"', 'id="-3"'), v.road.replace('a="3.5"', 'a="4.0"'), '<!DOCTYPE OpenDRIVE [<!ENTITY x "x">]>' + v.road]) assert.throws(() => parseStraightMap(v.map, invalid));
  assert.throws(() => parseStraightMap({ ...v.map, kind: "external_map" }, v.road));
});
test("native trace frame count bound rejects silent truncation and unexpected extra samples", () => {
  const v = fixture(), actors = parsePinnedActors(v.actors);
  assert.throws(() => parseNativeTrace({ ...v.trace, frames: [...v.trace.frames, ...v.trace.frames] }, actors, 1, .2));
});
