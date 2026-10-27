/** Bounded retained object evidence. No generated motion or inferred native identities. */
import { actorColor as authoringActorColor } from "../../scenario-authoring.ts";
export const OBJECT_COORDINATES = "world_m_x_forward_y_left_z_up";
export type ObjectArtifact = { id: string; name: string; media_type: string; size_bytes: number; sha256: string; download_url: string };
export type ObjectExecution = { id: string; experiment_id: string; project_id: string; experiment_kind: string; status: string; adapter_id: string; adapter_version: string; input_hash: string; manifest_hash: string | null; download_url: string | null; fidelity: string | null; artifacts: ObjectArtifact[] };
export type NativeActorState = { id: string; x: number; y: number; z: number; heading_rad: number; speed_mps: number; vx_mps: number; vy_mps: number; ax_mps2: number; ay_mps2: number; bb_x: number; bb_y: number; length_m: number; width_m: number; lane_id: number; lane_offset_m: number };
export type NativeFrame = { time_s: number; actors: NativeActorState[] };
export type SceneActor = { id: string; kind: "vehicle" | "pedestrian"; role: "ego" | "other"; dimensions_m: { length_m: number; width_m: number; height_m: number }; authored_intent: { initial_pose: { x: number; y: number; heading_rad: number }; initial_speed_mps: number; route: unknown[]; actions: unknown[]; interpretation: string }; metadata?: Record<string, unknown> };
export type ScenePoint = { x: number; y: number; z: number };
export type SceneLane = { id: number; width_m: number; centerline: ScenePoint[]; left_boundary: ScenePoint[]; right_boundary: ScenePoint[] };
export type SceneMap = { kind: "authored_straight_road"; id: string; road_id: number; length_m: number; lane_width_m: number; reference_line: ScenePoint[]; lanes: SceneLane[] };
export type SceneEvent = { kind: string; actor_ids: string[]; time_s: number; end_time_s?: number; provenance: { source: string; method: string; [key: string]: unknown }; details: Record<string, unknown> };
export type TimelineEventGroup = { kind: string; time_s: number; events: SceneEvent[] };
export type ObjectScene = { source: Record<string, unknown>; map: SceneMap; actors: SceneActor[]; frames: NativeFrame[]; events: SceneEvent[]; provenance: "object-scene/1" | "legacy_verified_derivation" };
export type SceneBounds = { xmin: number; xmax: number; ymin: number; ymax: number };
export function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}: object required.`);
  return value as Record<string, unknown>;
}
function number(value: unknown, label: string, min = -10000, max = 10000): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) throw new Error(`${label}: finite bounded number required.`);
  return value;
}
function identity(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(value)) throw new Error(`${label}: invalid native actor identity.`);
  return value;
}
function rows(value: unknown, label: string, min: number, max: number): unknown[] {
  if (!Array.isArray(value) || value.length < min || value.length > max) throw new Error(`${label}: array exceeds supported bound.`);
  return value;
}
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => sameValue(item, b[index]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ak = Object.keys(a), bk = Object.keys(b);
    return ak.length === bk.length && ak.every((key) => Object.hasOwn(b, key) && sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
  }
  return false;
}
export function actorCenter(state: NativeActorState): { x: number; y: number } {
  const c = Math.cos(state.heading_rad), s = Math.sin(state.heading_rad);
  return { x: state.x + state.bb_x * c - state.bb_y * s, y: state.y + state.bb_x * s + state.bb_y * c };
}
export function actorCorners(state: NativeActorState): { x: number; y: number }[] {
  const center = actorCenter(state), c = Math.cos(state.heading_rad), s = Math.sin(state.heading_rad);
  return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([u, v]) => ({ x: center.x + u * state.length_m / 2 * c - v * state.width_m / 2 * s, y: center.y + u * state.length_m / 2 * s + v * state.width_m / 2 * c }));
}
export function nativeOverlap(a: NativeActorState, b: NativeActorState): boolean {
  const ca = actorCorners(a), cb = actorCorners(b);
  return [a.heading_rad, b.heading_rad].flatMap((h) => [[Math.cos(h), Math.sin(h)], [-Math.sin(h), Math.cos(h)]]).every(([x, y]) => {
    const pa = ca.map((p) => p.x * x + p.y * y), pb = cb.map((p) => p.x * x + p.y * y);
    return Math.max(Math.min(...pa), Math.min(...pb)) <= Math.min(Math.max(...pa), Math.max(...pb));
  });
}
export function sceneBounds(scene: ObjectScene, includeMap = false): SceneBounds {
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  const visit = (p: { x: number; y: number }) => { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y); };
  for (const frame of scene.frames) for (const actor of frame.actors) for (const point of actorCorners(actor)) visit(point);
  if (includeMap) for (const lane of scene.map.lanes) [...lane.left_boundary, ...lane.right_boundary].forEach(visit);
  return { xmin: xmin - 8, xmax: xmax + 8, ymin: ymin - 8, ymax: ymax + 8 };
}
export const SCENE_CAMERA_DIRECTION = { x: .24 / Math.sqrt(1.0384), y: .72 / Math.sqrt(1.0384), z: .68 / Math.sqrt(1.0384) };
/** Fit every corner to the perspective frustum, including nearer corners and height. */
export function perspectiveFitDistance(bounds: SceneBounds, aspect: number, fovDeg = 45, heightRange = { min: 0, max: 5 }): number {
  const direction = SCENE_CAMERA_DIRECTION, horizontal = Math.hypot(direction.x, direction.z), right = { x: direction.z / horizontal, y: 0, z: -direction.x / horizontal };
  const up = { x: direction.y * right.z, y: direction.z * right.x - direction.x * right.z, z: -direction.y * right.x };
  const tanV = Math.tan(fovDeg * Math.PI / 360), tanH = tanV * Math.max(.1, aspect);
  let distance = 10;
  for (const x of [-(bounds.xmax - bounds.xmin) / 2, (bounds.xmax - bounds.xmin) / 2]) for (const z of [-(bounds.ymax - bounds.ymin) / 2, (bounds.ymax - bounds.ymin) / 2]) for (const y of [heightRange.min, heightRange.max]) {
    const towardCamera = x * direction.x + y * direction.y + z * direction.z;
    distance = Math.max(distance, towardCamera + Math.abs(x * right.x + z * right.z) / tanH, towardCamera + Math.abs(x * up.x + y * up.y + z * up.z) / tanV);
  }
  return distance * 1.12;
}
export function actorColor(id: string, _role?: string): string { return authoringActorColor(id); }

// Only the retained AXIOM authored straight-road subset is supported. XML is data.
type XmlNode = { tag: string; attributes: Record<string, string>; children: XmlNode[] };
function xmlTree(xml: string): XmlNode {
  if (xml.length > 200000 || /<!|&/.test(xml)) throw new Error("Unsupported OpenDRIVE declaration/entity.");
  const root: XmlNode = { tag: "document", attributes: {}, children: [] }, stack = [root];
  const tokens = xml.replace(/^\s*<\?xml[^?]*\?>/, "").match(/<[^>]+>|[^<]+/g) ?? [];
  for (const token of tokens) {
    if (!token.startsWith("<")) { if (token.trim()) throw new Error("Unexpected OpenDRIVE text."); continue; }
    if (token.startsWith("</")) { if (stack.length < 2 || token !== `</${stack.pop()!.tag}>`) throw new Error("Invalid OpenDRIVE element nesting."); continue; }
    const match = /^<([A-Za-z][A-Za-z0-9]*)([\s\S]*?)(\/?)>$/.exec(token);
    if (!match) throw new Error("Unsupported OpenDRIVE token.");
    const attrs: Record<string, string> = {}; let remainder = match[2];
    while (remainder.trim()) {
      const attribute = /^\s+([A-Za-z][A-Za-z0-9]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/.exec(remainder);
      if (!attribute || Object.hasOwn(attrs, attribute[1])) throw new Error("Invalid OpenDRIVE attributes.");
      attrs[attribute[1]] = attribute[2] ?? attribute[3]; remainder = remainder.slice(attribute[0].length);
    }
    const node: XmlNode = { tag: match[1], attributes: attrs, children: [] }; stack.at(-1)!.children.push(node);
    if (!match[3]) stack.push(node);
    if (stack.length > 12) throw new Error("OpenDRIVE nesting exceeds bound.");
  }
  if (stack.length !== 1 || root.children.length !== 1 || root.children[0].tag !== "OpenDRIVE") throw new Error("Invalid OpenDRIVE root.");
  return root.children[0];
}
function children(node: XmlNode, names: string[]) {
  if (!sameValue(node.children.map((child) => child.tag), names)) throw new Error(`Unsupported OpenDRIVE ${node.tag} geometry.`);
  return node.children;
}
function attrs(node: XmlNode, expected: Record<string, string | number>) {
  if (Object.keys(node.attributes).length !== Object.keys(expected).length || Object.entries(expected).some(([key, value]) => typeof value === "number" ? Number(node.attributes[key]) !== value : node.attributes[key] !== value)) throw new Error(`OpenDRIVE ${node.tag} differs from pinned straight map.`);
}
export function parseStraightMap(value: unknown, roadXml: string): SceneMap {
  const map = record(value, "map");
  if (map.kind !== "authored_straight_road" || map.id !== "axiom-straight-road-r1") throw new Error("Unsupported map: only the pinned authored straight road is supported.");
  const length = number(map.length_m, "map length", 100, 2000), width = number(map.lane_width_m, "lane width", 2.5, 5);
  const root = xmlTree(roadXml), [header, road] = children(root, ["header", "road"]);
  if (Object.keys(root.attributes).length || header.attributes.name !== map.id || header.children.length) throw new Error("OpenDRIVE header differs from pinned map.");
  attrs(road, { name: "AXIOM authored straight road", length, id: 1, junction: -1 });
  const [link, plan, lanes] = children(road, ["link", "planView", "lanes"]); children(link, []); attrs(link, {}); attrs(plan, {}); attrs(lanes, {});
  const [geometry] = children(plan, ["geometry"]); attrs(geometry, { s: 0, x: 0, y: 0, hdg: 0, length }); const [line] = children(geometry, ["line"]); attrs(line, {}); children(line, []);
  const [section] = children(lanes, ["laneSection"]); attrs(section, { s: 0 }); const [center, right] = children(section, ["center", "right"]); attrs(center, {}); attrs(right, {});
  const [centerLane] = children(center, ["lane"]); attrs(centerLane, { id: 0, type: "none", level: "false" }); const [centerLink] = children(centerLane, ["link"]); attrs(centerLink, {}); children(centerLink, []);
  const nativeLanes = children(right, ["lane", "lane"]);
  nativeLanes.forEach((lane, index) => {
    attrs(lane, { id: -index - 1, type: "driving", level: "false" });
    const [laneLink, laneWidth, marking] = children(lane, ["link", "width", "roadMark"]); attrs(laneLink, {}); children(laneLink, []);
    attrs(laneWidth, { sOffset: 0, a: width, b: 0, c: 0, d: 0 }); children(laneWidth, []);
    attrs(marking, { sOffset: 0, type: index === 0 ? "broken" : "solid", weight: "standard", color: "standard", width: .12, laneChange: "both" }); children(marking, []);
  });
  const segment = (y: number): ScenePoint[] => [{ x: 0, y, z: 0 }, { x: length, y, z: 0 }];
  return { kind: "authored_straight_road", id: String(map.id), road_id: 1, length_m: length, lane_width_m: width, reference_line: segment(0), lanes: [-1, -2].map((id, index) => ({ id, width_m: width, centerline: segment(-(index + .5) * width), left_boundary: segment(-index * width), right_boundary: segment(-(index + 1) * width) })) };
}
export function parsePinnedActors(value: unknown): SceneActor[] {
  const actors = rows(value, "pinned actors", 1, 8).map((raw) => {
    const actor = record(raw, "actor"), dimensions = record(actor.dimensions, "authored dimensions"), pose = record(actor.pose, "initial pose");
    if (!["vehicle", "pedestrian"].includes(String(actor.kind)) || !["ego", "other"].includes(String(actor.role))) throw new Error("Unsupported pinned actor kind/role.");
    return { id: identity(actor.id, "actor ID"), kind: actor.kind as SceneActor["kind"], role: actor.role as SceneActor["role"], dimensions_m: { length_m: number(dimensions.length_m, "authored length", .001, 30), width_m: number(dimensions.width_m, "authored width", .001, 5), height_m: number(dimensions.height_m, "authored height", .001, 5) }, authored_intent: { initial_pose: { x: number(pose.x, "initial x"), y: number(pose.y, "initial y"), heading_rad: number(pose.heading_rad, "initial heading", -6.284, 6.284) }, initial_speed_mps: number(actor.speed_mps, "initial speed", 0, 60), route: rows(actor.route, "authored route", 0, 32), actions: rows(actor.actions, "authored actions", 0, 16), interpretation: "authored_intent_not_observed_completion" } };
  });
  if (new Set(actors.map((actor) => actor.id)).size !== actors.length || actors.filter((actor) => actor.role === "ego").length !== 1) throw new Error("Pinned actor identities/ego role are ambiguous.");
  return actors;
}
export function parseNativeTrace(value: unknown, actors: SceneActor[], duration: number, timestep: number): NativeFrame[] {
  const trace = record(value, "native trace");
  if (trace.schema_version !== "object-trace/1" || trace.coordinate_system !== OBJECT_COORDINATES) throw new Error("Unsupported native trace version or coordinate system.");
  const max = Math.ceil(duration / timestep) + 4;
  const frames = rows(trace.frames, "full native frames", 2, max).map((raw) => {
    const frame = record(raw, "native sample"), time = number(frame.time_s, "native timestamp", 0, duration + timestep * 3);
    const states = rows(frame.actors, "native actors", actors.length, actors.length).map((rawState) => {
      const state = record(rawState, "native state"), id = identity(state.id, "native ID");
      const required = ["x", "y", "z", "heading_rad", "speed_mps", "vx_mps", "vy_mps", "ax_mps2", "ay_mps2", "bb_x", "bb_y", "length_m", "width_m", "lane_id", "lane_offset_m"] as const;
      const parsed = { id } as NativeActorState; for (const key of required) parsed[key] = number(state[key], `native ${key}`);
      if (parsed.length_m <= 0 || parsed.length_m > 30 || parsed.width_m <= 0 || parsed.width_m > 5 || !Number.isInteger(parsed.lane_id)) throw new Error("Invalid native box/lane coordinates.");
      return parsed;
    });
    if (new Set(states.map((state) => state.id)).size !== actors.length || actors.some((actor) => !states.some((state) => state.id === actor.id))) throw new Error("Native actor identity differs from pinned actors.");
    return { time_s: time, actors: states };
  });
  if (frames.some((frame, index) => index > 0 && frame.time_s <= frames[index - 1].time_s) || frames.at(-1)!.time_s < duration - timestep) throw new Error("Native clock is unordered or the retained run is incomplete.");
  return frames;
}
export function deriveTraceEvents(frames: NativeFrame[]): SceneEvent[] {
  const events: SceneEvent[] = [], open = new Map<string, SceneEvent>();
  for (let index = 0; index < frames.length; index++) {
    const frame = frames[index], collisions = new Set<string>();
    frame.actors.forEach((actor, ai) => {
      for (const other of frame.actors.slice(ai + 1)) if (nativeOverlap(actor, other)) {
        const ids = [actor.id, other.id].sort(), key = ids.join(":"); collisions.add(key);
        if (!open.has(key)) { const event: SceneEvent = { kind: "collision", actor_ids: ids, time_s: frame.time_s, provenance: { source: "native_trace", method: "saved_sample_oriented_box_overlap" }, details: { scope: "sampled planar bounding-box overlap; not a safety verdict" } }; events.push(event); open.set(key, event); }
        open.get(key)!.end_time_s = frame.time_s;
      }
      const previous = frames[index - 1]?.actors.find((state) => state.id === actor.id);
      if (previous && previous.lane_id !== actor.lane_id) events.push({ kind: "lane_transition", actor_ids: [actor.id], time_s: frame.time_s, provenance: { source: "native_trace", method: "saved_sample_lane_id_change" }, details: { from: previous.lane_id, to: actor.lane_id } });
      if (previous && Math.abs(previous.speed_mps) > .05 && Math.abs(actor.speed_mps) <= .05) events.push({ kind: "stopped", actor_ids: [actor.id], time_s: frame.time_s, provenance: { source: "native_trace", method: "saved_sample_speed_threshold_0.05_mps" }, details: { speed_mps: actor.speed_mps } });
    });
    for (const key of open.keys()) if (!collisions.has(key)) open.delete(key);
  }
  return events.sort((a, b) => a.time_s - b.time_s);
}
export function retainedLogActionEvents(scene: ObjectScene, log: string, sha256: string): SceneEvent[] {
  const events: SceneEvent[] = [];
  log.split(/\r?\n/).forEach((line, lineIndex) => {
    const match = /^\[(\d+\.\d+)\].*?\b([A-Za-z0-9_-]+)_(\d+)_event.*?\b(runningState|completeState)\s*$/.exec(line);
    if (!match) return;
    const actor = scene.actors.find((value) => value.id === match[2]), time = Number(match[1]), actionIndex = Number(match[3]);
    if (!actor || time < scene.frames[0].time_s || time > scene.frames.at(-1)!.time_s || actionIndex >= actor.authored_intent.actions.length) return;
    events.push({ kind: "native_action", actor_ids: [actor.id], time_s: time, provenance: { source: "native_log", artifact: "engine.log", sha256, line_numbers: [lineIndex + 1], method: "esmini_event_state_log" }, details: { action_index: actionIndex, action_kind: record(actor.authored_intent.actions[actionIndex], "authored action").kind, engine_state: match[4], raw_line: line } });
  });
  return events;
}
/** One hit target per exact native timestamp/kind; keep every original event. */
export function groupTimelineEvents(events: SceneEvent[]): TimelineEventGroup[] {
  const groups = new Map<string, TimelineEventGroup>();
  for (const event of events) {
    const key = JSON.stringify([event.time_s, event.kind]);
    const existing = groups.get(key);
    if (existing) existing.events.push(event);
    else groups.set(key, { kind: event.kind, time_s: event.time_s, events: [event] });
  }
  return [...groups.values()].sort((a, b) => a.time_s - b.time_s || a.kind.localeCompare(b.kind));
}
export function parseSceneEvidence(sealed: unknown, traceValue: unknown, roadXml: string, execution: ObjectExecution, sceneValue?: unknown): ObjectScene {
  const receipt = record(sealed, "sealed receipt"), stored = record(receipt.execution, "sealed execution"), prepared = record(receipt.prepared, "sealed prepared input"), spec = record(prepared.spec, "sealed spec"), input = record(prepared.input, "sealed input");
  if (receipt.schema_version !== 1 || prepared.schema_version !== 1 || input.schema_version !== "object-sim-input/1" || execution.experiment_kind !== "object_sim" || execution.adapter_id !== "esmini-object" || execution.fidelity !== "authored_object_kinematics") throw new Error("Unsupported retained Object Sim execution contract.");
  for (const key of ["id", "experiment_id", "project_id", "status", "experiment_kind", "adapter_id", "adapter_version", "input_hash", "fidelity"] as const) if (stored[key] !== execution[key]) throw new Error(`Sealed execution ${key} differs from selected result.`);
  if (!sameValue(stored.artifacts, execution.artifacts) || spec.project_id !== execution.project_id || spec.adapter_id !== execution.adapter_id || spec.adapter_version !== execution.adapter_version || input.project_id !== execution.project_id || input.adapter_version !== execution.adapter_version || spec.seed !== input.seed) throw new Error("Sealed artifact/input provenance differs from selected result.");
  const scenario = record(input.scenario, "pinned scenario"), request = record(input.native_request, "native request");
  if (scenario.schema_version !== "scenario-v2/1" || scenario.coordinate_system !== OBJECT_COORDINATES || request.road_xml !== roadXml || request.seed !== input.seed) throw new Error("Retained map/coordinate source differs from original execution input.");
  const duration = number(scenario.duration_s, "duration", 1, 30), timestep = number(scenario.timestep_s, "timestep", .02, .2), actors = parsePinnedActors(scenario.actors), map = parseStraightMap(scenario.map, roadXml);
  if (!sameValue(request.actor_ids, actors.map((actor) => actor.id)) || request.duration_s !== duration || request.timestep_s !== timestep) throw new Error("Native request identity/timing differs from pinned scenario.");
  const frames = parseNativeTrace(traceValue, actors, duration, timestep);
  const source: Record<string, unknown> = Object.fromEntries(["scenario_id", "scenario_revision", "scenario_manifest_hash", "configuration_hash", "seed", "adapter_version"].map((key) => [key, input[key]]));
  for (const key of ["scenario_manifest_hash", "configuration_hash"]) if (typeof source[key] !== "string" || !/^[a-f0-9]{64}$/.test(source[key] as string)) throw new Error("Pinned source content hashes are missing.");
  if (!Number.isSafeInteger(source.scenario_revision) || Number(source.scenario_revision) < 1) throw new Error("Pinned scenario revision is missing.");
  if (!sceneValue) {
    const traceHash = execution.artifacts.find((artifact) => artifact.name === "object-trace.json")?.sha256;
    return { source, map, actors, frames, events: deriveTraceEvents(frames).map((event) => ({ ...event, provenance: { ...event.provenance, artifact: "object-trace.json", sha256: traceHash } })), provenance: "legacy_verified_derivation" };
  }
  const scene = record(sceneValue, "object scene"), sceneSource = record(scene.source, "scene source");
  if (scene.schema_version !== "object-scene/1" || scene.coordinate_system !== OBJECT_COORDINATES || scene.fidelity !== "authored_object_kinematics" || scene.physical_sensor_validation !== false) throw new Error("Unsupported object scene contract.");
  for (const [key, value] of Object.entries(source)) if (sceneSource[key] !== value) throw new Error(`Scene ${key} differs from sealed input.`);
  const engineReceipt = record(record(stored.summary, "sealed summary").engine_receipt, "sealed engine receipt");
  for (const [key, nativeKey] of [["engine_version", "engine_version"], ["engine_build", "engine_build"], ["engine_binary_sha256", "binary_sha256"], ["runner_sha256", "runner_sha256"]]) if (sceneSource[key] !== engineReceipt[nativeKey]) throw new Error(`Scene ${key} differs from sealed native engine receipt.`);
  for (const [key, name] of [["road_xml_sha256", "road.xodr"], ["scenario_xml_sha256", "scenario.xosc"], ["native_csv_sha256", "native-trace.csv"], ["native_log_sha256", "engine.log"], ["object_trace_sha256", "object-trace.json"]]) {
    const found = execution.artifacts.filter((artifact) => artifact.name === name); if (found.length !== 1 || sceneSource[key] !== found[0].sha256) throw new Error(`Scene ${name} source hash differs from retained artifact.`);
  }
  const sceneMap = record(scene.map, "scene map");
  for (const [key, value] of Object.entries(map)) if (!sameValue(sceneMap[key], value)) throw new Error(`Scene map ${key} differs from pinned OpenDRIVE.`);
  if (sceneMap.source_artifact !== "road.xodr" || sceneMap.source_sha256 !== sceneSource.road_xml_sha256 || sceneMap.geometry_provenance !== "validated_pinned_opendrive_line_and_lane_widths") throw new Error("Scene map source differs from retained OpenDRIVE.");
  const sceneActors = rows(scene.actors, "scene actors", actors.length, actors.length);
  for (const actor of actors) {
    const matching = sceneActors.filter((raw) => record(raw, "scene actor").id === actor.id); if (matching.length !== 1) throw new Error("Scene actor identity is ambiguous.");
    const candidate = record(matching[0], "scene actor"); for (const key of ["kind", "role", "dimensions_m", "authored_intent"] as const) if (!sameValue(candidate[key], actor[key])) throw new Error(`Scene actor ${actor.id} ${key} differs from sealed source.`);
    const metadata = record(candidate.metadata, "actor metadata");
    if (metadata.origin !== "pinned_scenario_revision" || metadata.height_source !== "authored_scenario_not_native_observation") throw new Error("Scene actor metadata loses authored height provenance.");
  }
  if (!sameValue(scene.frames, record(traceValue, "trace").frames)) throw new Error("Scene frames differ from full saved native trace.");
  const events = rows(scene.events, "scene events", 0, 20000).map((raw) => {
    const event = record(raw, "scene event"), provenance = record(event.provenance, "event provenance");
    if (!["collision", "lane_transition", "stopped", "native_action"].includes(String(event.kind)) || !["native_trace", "native_log"].includes(String(provenance.source)) || typeof provenance.method !== "string") throw new Error("Unsupported event provenance.");
    const ids = rows(event.actor_ids, "event actors", 1, actors.length).map((id) => identity(id, "event actor")); if (new Set(ids).size !== ids.length || ids.some((id) => !actors.some((actor) => actor.id === id))) throw new Error("Event actor is outside retained native identity.");
    const expectedArtifact = provenance.source === "native_trace" ? "object-trace.json" : "engine.log";
    if (provenance.artifact !== expectedArtifact || provenance.sha256 !== execution.artifacts.find((artifact) => artifact.name === expectedArtifact)?.sha256) throw new Error("Event source hash differs from retained evidence.");
    for (const fi of rows(provenance.frame_indices, "event sample indices", 0, frames.length)) if (!Number.isSafeInteger(fi) || Number(fi) < 0 || Number(fi) >= frames.length) throw new Error("Event references an invalid native sample.");
    for (const li of rows(provenance.line_numbers, "event log lines", 0, 10000)) if (!Number.isSafeInteger(li) || Number(li) < 1) throw new Error("Event references an invalid engine log line.");
    const time = number(event.time_s, "event timestamp", frames[0].time_s, frames.at(-1)!.time_s);
    return { kind: String(event.kind), actor_ids: ids, time_s: time, ...(event.end_time_s == null ? {} : { end_time_s: number(event.end_time_s, "event end timestamp", time, frames.at(-1)!.time_s) }), provenance: { ...provenance, source: String(provenance.source), method: provenance.method }, details: record(event.details, "event detail") };
  });
  for (const actor of actors) actor.metadata = record(sceneActors.find((raw) => record(raw, "scene actor").id === actor.id), "scene actor").metadata as Record<string, unknown> | undefined;
  return { source: sceneSource, map, actors, frames, events, provenance: "object-scene/1" };
}
