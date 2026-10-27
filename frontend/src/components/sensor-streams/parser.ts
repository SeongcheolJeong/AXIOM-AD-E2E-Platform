import type { CarlaZipArtifact, CarlaZipMember } from "../../pages/carla-zip";
import type { CaptureEvidence, CaptureMotion, CapturePin, CaptureRecord, CaptureSensor, CaptureTick, Matrix, SensorKind, SensorStreamExecution } from "./types";

const NATIVE = "left_handed_x_forward_y_right_z_up", DECLARED = "right_handed_x_forward_y_left_z_up";
const VERSION = "carla-streaming-capture/1", FIDELITY = "actual_carla_native_sensor_model";
const DRIVING_VERSION = "carla-driving-multisensor/1", DRIVING_FIDELITY = "actual_carla_reference_driving_multisensor";
const BLUEPRINTS = { rgb: "sensor.camera.rgb", lidar: "sensor.lidar.ray_cast", radar: "sensor.other.radar" };
const HASH = /^[a-f0-9]{64}$/, KEY = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
function reject(message: string): never { throw new Error(`Sensor streams: ${message}`); }
export function object(value: unknown, label: string): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) reject(`${label} is not an object.`); return value as Record<string, unknown>; }
function array(value: unknown, label: string): unknown[] { if (!Array.isArray(value)) reject(`${label} is not an array.`); return value; }
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) reject("Invalid integer/count/identity."); return value; }
function finite(value: unknown, limit = 1e6): number { if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > limit) reject("Nonfinite/out-of-domain native value."); return value; }
function hash(value: unknown): string { if (typeof value !== "string" || !HASH.test(value)) reject("Original SHA-256 is missing."); return value; }
function text(value: unknown, limit = 200): string { if (typeof value !== "string" || !value.length || value.length > limit) reject("Invalid native string identity."); return value; }
export function nativeTimestampNs(stamp: number) {
  const scaled = stamp * 1e9, lower = Math.floor(scaled);
  // Original Python codec uses round(): ties-to-even, unlike Math.round().
  const rounded = scaled - lower === .5 ? lower % 2 === 0 ? lower : lower + 1 : Math.round(scaled);
  return BigInt(rounded).toString();
}
export function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    const ak = Object.keys(a).sort(), bk = Object.keys(b).sort(); return same(ak, bk) && ak.every(k => same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
  }
  return false;
}
export function rigidMatrix(value: unknown): Matrix {
  const rows = array(value, "pose matrix"); if (rows.length !== 4) reject("Pose is not 4 × 4.");
  const m = rows.map(row => { const r = array(row, "pose row"); if (r.length !== 4) reject("Pose row is not 4D."); return r.map(v => finite(v)); });
  if (!same(m[3], [0, 0, 0, 1])) reject("Pose lacks homogeneous bottom row.");
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) if (Math.abs(m.reduce((sum, row, k) => k < 3 ? sum + row[i] * row[j] : sum, 0) - Number(i === j)) > 2e-5) reject("Pose rotation is not orthonormal.");
  const [a, b, c] = m, determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
  if (Math.abs(determinant - 1) > 4e-5) reject("Pose rotation is not proper."); return m;
}
export function multiply(a: Matrix, b: Matrix): Matrix { return a.map(row => b[0].map((_, j) => row.reduce((sum, v, k) => sum + v * b[k][j], 0))); }
function inverse(m: Matrix): Matrix { return [0, 1, 2].map(i => [m[0][i], m[1][i], m[2][i], -[0, 1, 2].reduce((sum, k) => sum + m[k][i] * m[k][3], 0)]).concat([[0, 0, 0, 1]]); }
function reflect(m: Matrix): Matrix { const s = [1, -1, 1, 1]; return m.map((row, i) => row.map((v, j) => v * s[i] * s[j])); }
export function relativeRhMatrix(ego: Matrix, sensor: Matrix) { return reflect(multiply(inverse(ego), sensor)); }
function residual(a: Matrix, b: Matrix) { return [Math.max(...[0, 1, 2].map(i => Math.abs(a[i][3] - b[i][3]))), Math.max(...[0, 1, 2].flatMap(i => [0, 1, 2].map(j => Math.abs(a[i][j] - b[i][j]))))]; }
function mountTolerance(...matrices: Matrix[]) { return 2e-5 + 16 * 2 ** -23 * Math.max(1, ...matrices.map(m => Math.abs(m[0][3]) + Math.abs(m[1][3]) + Math.abs(m[2][3]))); }
function vector(value: unknown, limit: number) { const v = array(value, "declared vector"); if (v.length !== 3) reject("Declared vector needs three components."); return v.map(n => finite(n, limit)); }
function declaredMount(sensor: Record<string, unknown>) {
  if (sensor.rotation_order !== "pitch_yaw_roll_right_hand_rule") reject("Unsupported mount rotation order.");
  const [p, y, r] = vector(sensor.rotation_deg, 180).map(n => n * Math.PI / 180), [cp, sp, cy, sy, cr, sr] = [Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y), Math.cos(r), Math.sin(r)];
  const m = multiply(multiply([[cy, -sy, 0, 0], [sy, cy, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], [[cp, 0, sp, 0], [0, 1, 0, 0], [-sp, 0, cp, 0], [0, 0, 0, 1]]), [[1, 0, 0, 0], [0, cr, -sr, 0], [0, sr, cr, 0], [0, 0, 0, 1]]);
  vector(sensor.translation_m, 10).forEach((v, i) => m[i][3] = v); return m;
}
function pose(value: unknown) { return rigidMatrix(object(value, "native transform").matrix); }
function descriptor(value: unknown, expected?: string): CarlaZipMember {
  const d = object(value, "original member"); const name = text(d.name, 180);
  if (name.startsWith("/") || name.includes("\\") || name.includes("\0") || name.split("/").some(s => !s || s === "." || s === "..") || expected && name !== expected) reject("Original member name differs from exact source identity.");
  return { name, size_bytes: integer(d.size_bytes, 0, 768 * 1024 ** 2), sha256: hash(d.sha256) };
}
function original(artifacts: CarlaZipArtifact[], name: string, media: string) { const items = artifacts.filter(a => a.name === name); if (items.length !== 1 || items[0].media_type !== media) reject(`${name} original is missing/ambiguous.`); return items[0]; }

function validateDrivingController(value: unknown) {
  const controller = object(value, "driving controller");
  if (controller.id !== "native-map-lane-follow/1" || !same(Object.keys(controller).sort(), ["id", "lookahead_m", "route_length_m", "target_speed_mps"])) reject("Unsupported driving controller.");
  for (const [key, low, high] of [["target_speed_mps", 1, 4], ["lookahead_m", 3, 12], ["route_length_m", 30, 150]] as const) { const v = finite(controller[key]); if (v < low || v > high) reject("Driving controller value exceeds bounds."); }
  return controller;
}
function drivingEgo(value: unknown) {
  const ego = object(value, "native driving ego"), matrix = pose(ego.world_transform), velocity = vector(ego.velocity_mps, 100), speed = finite(ego.speed_mps, 100);
  if (speed < 0 || Math.abs(Math.hypot(...velocity) - speed) > 1e-7 || ["x_m", "y_m", "z_m"].some((key, index) => Math.abs(finite(ego[key]) - matrix[index][3]) > 1e-8)) reject("Native driving pose/velocity differs.");
  finite(ego.yaw_rad, 100); return { ego, matrix, speed };
}
function validateDrivingStartup(m: Record<string, unknown>, initial: Matrix, requested: Matrix) {
  const warmup = array(m.warmup, "braked warmup").map(v => object(v, "warmup frame"));
  if (m.warmup_policy !== "10_native_braked_settling_frames_all_sensor_queues_drained/1" || warmup.length !== 10) reject("Driving warmup is incomplete.");
  warmup.forEach((tick, index) => { integer(tick.frame); finite(tick.timestamp_seconds, 1e12); drivingEgo(tick.ego); if (index && (tick.frame !== Number(warmup[index - 1].frame) + 1 || Math.abs(Number(tick.timestamp_seconds) - Number(warmup[index - 1].timestamp_seconds) - .1) > 1e-5)) reject("Driving warmup clock differs."); });
  const last = warmup.at(-1)!, ego = drivingEgo(last.ego);
  if (last.frame !== m.ego_initial_world_frame || last.timestamp_seconds !== m.ego_initial_timestamp_seconds || !same(ego.matrix, initial) || ego.speed > .2 || Math.hypot(initial[0][3] - requested[0][3], initial[1][3] - requested[1][3]) > .25) reject("Driving settled startup differs from pinned spawn.");
}
function parseDrivingMotion(m: Record<string, unknown>, rig: Record<string, unknown>, probe: Record<string, unknown>, ticks: CaptureTick[], sensors: CaptureSensor[]): CaptureMotion {
  const motion = object(m.motion, "driving motion"), controller = validateDrivingController(rig.controller), route = object(motion.route, "native route"), rows = array(motion.ticks, "driving response ticks");
  if (motion.schema_version !== "carla-driving-motion-trace/1" || motion.adapter_version !== DRIVING_VERSION || motion.configuration_hash !== m.configuration_hash || motion.rig_revision !== rig.rig_revision || motion.seed !== rig.seed || motion.coordinate_convention !== NATIVE || !same(controller, motion.controller) || !same(controller, m.controller) || !same(route, probe.route) || m.camera_is_policy_input !== false || m.sensors_are_policy_input !== false || motion.camera_is_policy_input !== false || motion.sensors_are_policy_input !== false || rows.length !== ticks.length) reject("Driving motion scope or source differs.");
  if (route.schema_version !== "carla-native-map-route/1" || route.coordinate_convention !== NATIVE || route.map_opendrive_sha256 !== probe.map_opendrive_sha256 || route.spawn_index !== rig.spawn_index || route.configuration_revision !== rig.rig_revision) reject("Driving native route identity differs.");
  hash(route.sha256); const routePoints = array(route.points, "route points").map(v => object(v, "route point")); if (routePoints.length < 16 || routePoints.length > 80) reject("Driving route count exceeds bounds.");
  let routeLength = 0; routePoints.forEach((p, index) => { for (const key of ["x_m", "y_m", "z_m"]) finite(p[key]); if (index) routeLength += Math.hypot(Number(p.x_m) - Number(routePoints[index - 1].x_m), Number(p.y_m) - Number(routePoints[index - 1].y_m)); });
  if (routeLength + 1e-6 < Number(rig.duration_seconds) * Number(controller.target_speed_mps) + Number(controller.lookahead_m) + 10) reject("Driving route cannot cover the capture horizon.");
  let distance = 0, previousEgo: unknown = object(array(m.warmup, "warmup").at(-1), "last warmup").ego, previousFrame = integer(m.ego_initial_world_frame), previousTime = finite(m.ego_initial_timestamp_seconds, 1e12);
  const parsed = rows.map((value, index) => {
    const row = object(value, "driving tick"), tick = ticks[index], input = drivingEgo(row.input_ego), ego = drivingEgo(row.ego);
    if (row.index !== index || row.input_frame !== previousFrame || row.input_timestamp_seconds !== previousTime || !same(row.input_ego, previousEgo) || row.frame !== tick.frame || row.frame !== previousFrame + 1 || row.timestamp_seconds !== tick.timestampSeconds || Math.abs(tick.timestampSeconds - previousTime - .1) > 1e-5 || row.simulation_timestamp_ns !== tick.timestampNs || !same(row.sensor_keys, sensors.map(s => s.key)) || !same(ego.matrix, tick.records[0].egoWorld) || row.actuator_acknowledged !== true) reject("Driving control and multisensor response are not causally aligned.");
    const requested = object(row.requested_control, "requested control"), acknowledged = object(row.acknowledged_control, "acknowledged control"), observed = object(row.observed_control, "observed control");
    for (const values of [requested, acknowledged, observed]) { if (!same(Object.keys(values).sort(), ["brake", "steer", "throttle"])) reject("Unsupported driving control fields."); for (const key of ["throttle", "brake", "steer"]) { const number = finite(values[key], 1); if (key !== "steer" && number < 0) reject("Driving actuator value exceeds bounds."); } }
    if (["throttle", "brake", "steer"].some(key => Math.abs(Number(requested[key]) - Number(acknowledged[key])) > 1e-6 || Math.abs(Number(requested[key]) - Number(observed[key])) > 1e-6)) reject("Driving actuator acknowledgement/readback differs.");
    distance += Math.hypot(ego.matrix[0][3] - input.matrix[0][3], ego.matrix[1][3] - input.matrix[1][3]);
    previousFrame = tick.frame; previousTime = tick.timestampSeconds; previousEgo = row.ego;
    return { inputFrame: Number(row.input_frame), speedMps: ego.speed, distanceM: distance, egoWorld: ego.matrix, requestedControl: requested as Record<string, number>, observedControl: observed as Record<string, number> };
  });
  const maximumSpeed = Math.max(...parsed.map(t => t.speedMps));
  if (Math.abs(distance - finite(m.distance_travelled_m)) > 1e-6 || Math.abs(maximumSpeed - finite(m.maximum_speed_mps)) > 1e-7) reject("Driving motion aggregate differs from native ticks.");
  const interlock = object(m.engine_interlock, "driving engine interlock"), cleanup = object(m.cleanup_observation, "fresh cleanup observation");
  if (interlock.lock_acquired !== true || interlock.reservation_cleared_after_confirmed_cleanup !== true || cleanup.status !== "confirmed" || cleanup.controlled_cleanup_tick_count !== 1 || !same(cleanup.observed_dynamic_actor_ids, []) || !same(cleanup.owned_snapshot_absent_actor_ids, array(m.owned_actor_ids, "owned actors").slice().sort((a, b) => Number(a) - Number(b))) || cleanup.input_frame !== ticks.at(-1)!.frame || cleanup.frame !== ticks.at(-1)!.frame + 1) reject("Driving cleanup confirmation differs.");
  return { controller, route, distanceM: distance, maximumSpeedMps: maximumSpeed, ticks: parsed };
}

export function parseCaptureReceipt(sealed: unknown, execution: SensorStreamExecution, rigHashes: Record<string, string>): CapturePin {
  const root = object(sealed, "sealed receipt"), stored = object(root.execution, "sealed execution"), prepared = object(root.prepared, "sealed prepared input"), spec = object(prepared.spec, "frozen spec"), handler = object(prepared.handler, "frozen handler"), input = object(prepared.input, "frozen native input");
  const driving = execution.adapter_id === "carla-driving-multisensor", version = driving ? DRIVING_VERSION : VERSION, fidelity = driving ? DRIVING_FIDELITY : FIDELITY;
  if (root.schema_version !== 1 || prepared.schema_version !== 1 || execution.experiment_kind !== "sensor_capture" || execution.status !== "completed" || execution.adapter_id !== (driving ? "carla-driving-multisensor" : "carla-streaming-capture") || execution.adapter_version !== version || execution.fidelity !== fidelity || input.schema_version !== (driving ? "carla-driving-multisensor-experiment/1" : "carla-streaming-experiment/1") || handler.id !== execution.adapter_id || handler.version !== version || handler.fidelity !== fidelity || spec.kind !== "sensor_capture" || spec.adapter_id !== execution.adapter_id || spec.adapter_version !== version || spec.project_id !== execution.project_id) reject("Unsupported sealed execution scope.");
  for (const key of ["id", "experiment_id", "project_id", "experiment_kind", "status", "adapter_id", "adapter_version", "input_hash", "fidelity"] as const) if (stored[key] !== execution[key]) reject(`Selected execution ${key} differs from sealed source.`);
  const artifacts = array(stored.artifacts, "sealed artifacts").map(value => object(value, "sealed artifact") as unknown as CarlaZipArtifact);
  if (artifacts.length !== execution.artifacts.length || new Set(artifacts.map(a => a.id)).size !== artifacts.length || new Set(artifacts.map(a => a.name)).size !== artifacts.length) reject("Ambiguous sealed artifacts.");
  for (const a of artifacts) { const live = execution.artifacts.find(x => x.id === a.id); if (!live || !same(a, live)) reject("Current artifact metadata differs from sealed original."); hash(a.sha256); integer(a.size_bytes, 0, 2 * 1024 ** 3); }
  const summary = object(stored.summary, "sealed summary");
  if (!same(summary, execution.summary) || summary.schema_version !== "sensor-experiment-result/1" || summary.execution_status !== "completed" || summary.evaluation_status !== "not_evaluated" || summary.verification_verdict !== "not_evaluated" || summary.calibration_status !== "uncalibrated_engine_model") reject("Sensor evidence scope differs from its sealed summary.");
  const stages = input.comparison_kind === "single" ? ["single"] : input.comparison_kind === "same_scene_sensor_ab" ? ["rig_a", "rig_b"] : reject("Unsupported sensor experiment comparison.");
  if (summary.comparison_kind !== input.comparison_kind || spec.comparison_kind !== input.comparison_kind) reject("Comparison scope differs.");
  const rawRigs = object(input.rigs, "frozen rigs"), captures = object(summary.captures, "sealed captures");
  if (!same(Object.keys(rawRigs).sort(), [...stages].sort()) || !same(Object.keys(captures).sort(), [...stages].sort())) reject("Frozen rig denominator differs.");
  const rigs: Record<string, Record<string, unknown>> = {}, seed = integer(spec.seed, 0, 2147483647);
  for (const stage of stages) {
    const rig = object(rawRigs[stage], "frozen rig"); rigs[stage] = rig;
    if (rig.schema_version !== (driving ? "carla-driving-multisensor-spec/1" : "sensor-capture/1") || rig.coordinate_convention !== DECLARED || rig.sample_hz !== 10 || rig.seed !== seed || !hash(rigHashes[stage])) reject("Frozen rig schema, clock, coordinates or seed differ.");
    integer(rig.duration_seconds, driving ? 20 : 10, 30); if (driving) validateDrivingController(rig.controller); integer(rig.rig_revision, 1); integer(rig.spawn_index); text(rig.expected_map); text(rig.expected_server_version);
    const sensors = array(rig.sensors, "frozen sensors"); if (sensors.length < 1 || sensors.length > 4) reject("Frozen sensor count exceeds accepted rig.");
    const keys = new Set<string>(), counts = { rgb: 0, lidar: 0, radar: 0 };
    for (const value of sensors) { const s = object(value, "frozen sensor"), key = text(s.key, 32); if (!KEY.test(key) || keys.has(key) || !Object.hasOwn(BLUEPRINTS, String(s.kind))) reject("Frozen sensor identity/kind is unsupported."); keys.add(key); counts[s.kind as SensorKind]++; declaredMount(s); object(s.native_parameters, "frozen native parameters"); }
    if (counts.rgb > 2 || counts.lidar > 1 || counts.radar > 1) reject("Frozen sensor kinds exceed accepted rig.");
    const capture = object(captures[stage], "sealed capture"); if (capture.configuration_hash !== rigHashes[stage] || capture.frame_count !== Number(rig.duration_seconds) * 10 || capture.sensor_count !== sensors.length || capture.cleanup_status !== "confirmed" || capture.drop_count !== 0) reject("Frozen configuration hash/denominator/cleanup differs.");
    if (capture.archive_sha256 !== original(artifacts, `${stage}-native-capture.zip`, "application/zip").sha256) reject("Sealed native archive SHA differs.");
  }
  if (stages.length === 2) for (const field of ["duration_seconds", "sample_hz", "spawn_index", "vehicle_blueprint", "expected_map", "expected_server_version", ...(driving ? ["controller"] : [])]) if (!same(rigs.rig_a[field], rigs.rig_b[field])) reject("A/B scene definition is not fixed.");
  return { input, summary, rigs, stages, rigHashes, artifacts, seed, ...(driving ? { driving: true } : {}) };
}

export function parseCaptureManifest(value: unknown, pin: CapturePin, stage: string): CaptureEvidence {
  if (!pin.stages.includes(stage)) reject("Unexpected native capture stage.");
  const driving = pin.driving === true, version = driving ? DRIVING_VERSION : VERSION, fidelity = driving ? DRIVING_FIDELITY : FIDELITY;
  const m = object(value, "native manifest"), rig = pin.rigs[stage], capture = object(object(pin.summary.captures, "captures")[stage], "capture summary"), probe = object(m.probe, "native scene probe"), pinnedProbe = object(driving ? object(pin.input.native_capability_snapshots, "frozen per-stage probes")[stage] : pin.input.native_capability_snapshot, "frozen native scene probe");
  if (m.schema_version !== (driving ? "carla-driving-multisensor-result/1" : "carla-streaming-capture-result/1") || m.adapter_version !== version || m.fidelity !== fidelity || m.status !== "completed" || m.cleanup_status !== "confirmed" || m.world_settings_restored !== true || !same(m.cleanup_failures, []) || m.drop_count !== 0 || m.calibration_status !== "uncalibrated_engine_model" || m.configuration_hash !== pin.rigHashes[stage] || m.external_execution_id !== capture.external_execution_id || m.seed !== pin.seed || m.rig_revision !== rig.rig_revision || m.native_coordinate_convention !== NATIVE || m.declared_coordinate_convention !== DECLARED) reject("Native completion, source identity or coordinate scope differs.");
  for (const field of ["server_version", "client_version", "map", "map_opendrive_sha256", "spawn_points", "spawn_points_sha256", "weather", "sensors", "native_script_sha256", "world_settings", ...(driving ? ["route", "native_support_script_sha256", "native_feedback_script_sha256", "native_scene_script_sha256"] : [])]) if (!same(probe[field], pinnedProbe[field])) reject(`Captured ${field} differs from frozen scene.`);
  if (probe.server_version !== rig.expected_server_version || probe.client_version !== rig.expected_server_version || text(probe.map).split("/").at(-1) !== text(rig.expected_map).split("/").at(-1) || !same(probe.active_dynamic_actor_ids, []) || !same(m.world_settings_after_cleanup, probe.world_settings) || pin.input.native_script_sha256 !== probe.native_script_sha256) reject("Native build/free world/codec settings differ.");
  const initial = pose(m.ego_initial_transform), requested = pose(m.ego_requested_spawn_transform), spawns = array(probe.spawn_points, "native spawns");
  if (!same(m.ego_requested_spawn_transform, spawns[integer(rig.spawn_index, 0, spawns.length - 1)])) reject("Requested spawn differs from frozen geometry.");
  if (!driving) {
  const [spawnT, spawnR] = residual(initial, requested), spawnQ = object(m.ego_initial_spawn_qualification, "spawn qualification"), spawnTol = mountTolerance(initial, requested);
  if (spawnTol > .01 || spawnT > spawnTol || spawnR > 2e-5 || spawnQ.policy !== "carla-streaming-first-coherent-spawn/1" || spawnQ.qualified !== true || Math.abs(finite(spawnQ.translation_residual_m) - spawnT) > 1e-12 || Math.abs(finite(spawnQ.rotation_residual) - spawnR) > 1e-12 || Math.abs(finite(spawnQ.translation_tolerance_m) - spawnTol) > 1e-12 || spawnQ.rotation_tolerance !== 2e-5) reject("First coherent ego spawn qualification differs.");
  } else validateDrivingStartup(m, initial, requested);
  const frameCount = integer(m.frame_count, driving ? 200 : 100, 300); if (frameCount !== Number(rig.duration_seconds) * 10 || frameCount !== capture.frame_count) reject("Native frame denominator differs.");
  const declared = array(rig.sensors, "declared sensors").map(v => object(v, "declared sensor")), nativeSensors = array(m.sensors, "native sensors"), egoId = integer(m.ego_actor_id, 1), actorIds = new Set<number>();
  if (nativeSensors.length !== declared.length || !same(m.sensors, capture.sensors)) reject("Native sensor denominator differs from receipt.");
  const sensors: CaptureSensor[] = declared.map(s => {
    const matches = nativeSensors.map(v => object(v, "native sensor")).filter(n => n.key === s.key); if (matches.length !== 1) reject("Missing/duplicate native sensor identity."); const n = matches[0], key = String(s.key), kind = s.kind as SensorKind, actorId = integer(n.actor_id, 1), parameters = object(s.native_parameters, "requested parameters"), observed = object(n.native_readback, "native parameter readback");
    if (actorId === egoId || actorIds.has(actorId) || n.kind !== kind || n.blueprint_id !== BLUEPRINTS[kind] || !same(n.requested_native_parameters, parameters) || !same(Object.keys(observed).sort(), Object.keys(parameters).sort())) reject("Native sensor actor/kind/parameter identity differs."); actorIds.add(actorId);
    for (const [k, v] of Object.entries(parameters)) if (typeof v !== "string" || typeof observed[k] !== "string" || !(observed[k] === v || k !== "enable_postprocess_effects" && Number.isFinite(Number(v)) && Math.fround(Number(v)) === Math.fround(Number(observed[k])))) reject("Native parameter readback differs.");
    const mountRh = declaredMount(s), nativeMount = pose(n.mount_native), declaredEvidence = rigidMatrix(n.declared_rh_mount_matrix);
    if (Math.max(...residual(reflect(mountRh), nativeMount), ...residual(mountRh, declaredEvidence)) > 2e-6) reject("Native mount differs from declared RH geometry.");
    let intrinsics: Record<string, unknown> | undefined, fov: number | undefined;
    if (kind === "rgb") { fov = Number(parameters.fov); intrinsics = object(n.pinhole_intrinsics, "engine pinhole intrinsics"); const focal = 640 / (2 * Math.tan(fov * Math.PI / 360)); if (!(fov >= 10 && fov <= 120) || intrinsics.width !== 640 || intrinsics.height !== 360 || intrinsics.cx !== 320 || intrinsics.cy !== 180 || Math.abs(finite(intrinsics.fx) - focal) > 1e-8 || Math.abs(finite(intrinsics.fy) - focal) > 1e-8 || intrinsics.distortion_status !== "engine_model_not_measured_calibration") reject("Native camera intrinsics differ."); }
    return { key, kind, actorId, blueprint: BLUEPRINTS[kind], mountRh, parameters: observed, fov, intrinsics };
  });
  const expectedOwned = [egoId, ...actorIds].sort((a, b) => a - b), destroyed = array(m.actor_destroy_acknowledgements, "owned cleanup").map(v => object(v, "destroy acknowledgement"));
  if (m.ego_blueprint !== rig.vehicle_blueprint || !same(array(m.owned_actor_ids, "owned actors").map(v => integer(v, 1)).sort((a, b) => a - b), expectedOwned) || destroyed.length !== expectedOwned.length || !same(destroyed.map(v => integer(v.actor_id, 1)).sort((a, b) => a - b), expectedOwned) || destroyed.some(v => v.acknowledged !== true)) reject("Native owned actor cleanup is incomplete.");
  const drain = object(m.callback_drain, "callback closure"), started = integer(drain.started); if (drain.accepting !== false || drain.drained !== true || drain.active !== 0 || started !== drain.finished || started < frameCount * sensors.length) reject("Native callback closure is incomplete.");
  const descriptors = array(m.artifacts, "native file closure"), byName = new Map<string, Record<string, unknown>>(), members: CarlaZipMember[] = [], referenced = new Set<string>(); let total = 0;
  if (descriptors.length > 1204) reject("Native artifact closure exceeds accepted profile.");
  for (const value of descriptors) { const d = object(value, "native descriptor"), member = descriptor(d); if (byName.has(member.name)) reject("Duplicate native artifact."); byName.set(member.name, d); members.push(member); total += member.size_bytes; if (total > 768 * 1024 ** 2) reject("Native artifacts exceed complete byte budget."); }
  if (total !== m.native_bytes || total !== capture.native_bytes) reject("Native byte denominator differs.");
  const rows = array(m.records, "native records"); if (rows.length !== frameCount * sensors.length) reject("Missing/extra original sensor records.");
  const ticks: CaptureTick[] = [];
  for (let index = 0; index < frameCount; index++) {
    const records: CaptureRecord[] = sensors.map((sensor, slot) => {
      const row = object(rows[index * sensors.length + slot], "native record"), frame = integer(row.frame), stamp = finite(row.timestamp_seconds, 1e12), ns = text(row.simulation_timestamp_ns, 20);
      if (row.index !== index || row.key !== sensor.key || row.sensor_actor_id !== sensor.actorId || row.sensor_parent_actor_id !== egoId || row.sensor_blueprint !== sensor.blueprint || row.snapshot_world_frame !== frame || row.bracket_after_world_frame !== frame || row.world_timestamp_seconds !== stamp || stamp < 0 || !/^\d{1,20}$/.test(ns) || ns !== nativeTimestampNs(stamp)) reject("Native record clock/frame/actor identity differs.");
      const sensorWorld = pose(row.sensor_world_transform), egoWorld = pose(row.ego_world_transform), snap = pose(row.sensor_snapshot_world_transform), api = pose(row.sensor_api_world_transform), relativeRh = relativeRhMatrix(egoWorld, sensorWorld), q = object(row.mount_qualification, "mount qualification"), [dt, dr] = residual(relativeRh, sensor.mountRh), tol = mountTolerance(egoWorld, sensorWorld), [stationaryT, stationaryR] = residual(initial, egoWorld);
      if (tol > .01 || dt > tol || dr > 2e-5 || !driving && (stationaryT > tol || stationaryR > 2e-5) || residual(snap, sensorWorld)[0] > tol || residual(api, sensorWorld)[0] > tol || residual(snap, sensorWorld)[1] > 2e-5 || residual(api, sensorWorld)[1] > 2e-5 || q.policy !== "carla-streaming-rigid-readback/1" || q.qualified !== true || Math.abs(finite(q.translation_residual_m) - dt) > 1e-12 || Math.abs(finite(q.rotation_residual) - dr) > 1e-12 || Math.abs(finite(q.translation_tolerance_m) - tol) > 1e-12 || q.rotation_tolerance !== 2e-5 || Math.max(...residual(rigidMatrix(q.relative_rh_matrix), relativeRh)) > 1e-12) reject("Native measurement/snapshot/ego mount qualification differs.");
      const member = descriptor(row.raw_artifact, `frames/${String(index).padStart(4, "0")}-${sensor.key}.${sensor.kind === "rgb" ? "bgra8" : "f32"}`), source = object(row.raw_artifact, "raw descriptor");
      if (!same(source, byName.get(member.name)) || source.media_type !== "application/octet-stream" || referenced.has(member.name)) reject("Native record member has no unique original provenance."); referenced.add(member.name);
      let width = 0, height = 0, pointCount = 0;
      const encoding = sensor.kind === "rgb" ? "BGRA8 postprocessed engine RGB" : sensor.kind === "lidar" ? "float32_le x_y_z_intensity native_sensor_LH" : "float32_le velocity_azimuth_altitude_depth";
      if (row.encoding !== encoding) reject("Native encoding differs from supported original layout.");
      if (sensor.kind === "rgb") {
        width = integer(row.width, 640, 640); height = integer(row.height, 360, 360); if (member.size_bytes !== width * height * 4) reject("Native image is incomplete.");
        if (index === 0 || index === frameCount - 1) { const preview = object(row.preview_artifact, "original preview"), p = descriptor(preview, `previews/${String(index).padStart(4, "0")}-${sensor.key}.png`); if (!same(preview, byName.get(p.name)) || preview.media_type !== "image/png" || referenced.has(p.name)) reject("Native preview has no exact provenance."); referenced.add(p.name); }
        else if (row.preview_artifact !== undefined) reject("Unexpected authored camera preview.");
      } else { pointCount = integer(row.point_count, 0, sensor.kind === "lidar" ? 3200 : 150); if (member.size_bytes !== pointCount * 16) reject("Native point count/stride/bytes differ."); }
      return { index, key: sensor.key, kind: sensor.kind, frame, timestampNs: ns, timestampSeconds: stamp, encoding, member, width, height, pointCount, sensorWorld, egoWorld, relativeRh, cacheKey: `${stage}:${member.name}:${member.sha256}` };
    });
    const first = records[0]; if (records.some(r => r.frame !== first.frame || r.timestampNs !== first.timestampNs || r.timestampSeconds !== first.timestampSeconds || !same(r.egoWorld, first.egoWorld))) reject("Original sensor tick is not frame/time/ego aligned.");
    if (index && (first.frame !== ticks[index - 1].frame + 1 || Math.abs(first.timestampSeconds - ticks[index - 1].timestampSeconds - .1) > 1e-5)) reject("Native clock is not the fixed complete 10 Hz sequence.");
    ticks.push({ index, frame: first.frame, timestampNs: first.timestampNs, timestampSeconds: first.timestampSeconds, records });
  }
  if ((!driving && (ticks[0].frame !== m.ego_initial_world_frame || ticks[0].timestampSeconds !== m.ego_initial_timestamp_seconds || !same(ticks[0].records[0].egoWorld, initial))) || ticks[0].timestampSeconds !== capture.simulation_clock_start_s || ticks.at(-1)!.timestampSeconds !== capture.simulation_clock_end_s || referenced.size !== byName.size || [...byName.keys()].some(n => !referenced.has(n))) reject("Native initial clock or complete member closure differs.");
  const motion = driving ? parseDrivingMotion(m, rig, probe, ticks, sensors) : undefined;
  return { ...(motion ? { motion } : {}), stage, externalExecutionId: text(m.external_execution_id), configurationHash: hash(m.configuration_hash), rigRevision: integer(m.rig_revision, 1), seed: pin.seed, map: text(probe.map), ticks, sensors, members, manifest: original(pin.artifacts, `${stage}-capture-manifest.json`, "application/json"), archiveArtifact: original(pin.artifacts, `${stage}-native-capture.zip`, "application/zip") };
}
