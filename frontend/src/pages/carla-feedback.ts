import type { CarlaZipMember } from "./carla-zip";

export type FeedbackPoint = { x_m: number; y_m: number; z_m: number };
export type FeedbackEgo = FeedbackPoint & { yaw_rad: number; speed_mps: number };
export type FeedbackControl = { throttle: number; brake: number; steer: number };
export type FeedbackTick = {
  index: number; input_frame: number; input_timestamp_seconds: number; input_ego: FeedbackEgo;
  planned_path: FeedbackPoint[]; requested_control: FeedbackControl; acknowledged_control: FeedbackControl;
  frame: number; timestamp_seconds: number; simulation_timestamp_ns: string; ego: FeedbackEgo;
  observed_control: FeedbackControl; rgb_keys: string[]; actuator_acknowledged: true;
};
export type FeedbackCamera = { index: number; key: string; frame: number; timestamp_seconds: number; simulation_timestamp_ns: string; width: number; height: number; member: CarlaZipMember };
export type FeedbackEvidence = { ticks: FeedbackTick[]; cameras: FeedbackCamera[]; cameraKeys: string[]; map: string; configurationHash: string; configurationRevision: number; seed: number; targetSpeed: number; traceMember: CarlaZipMember; distance: number };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => finite(value) && Number.isSafeInteger(value) && value >= min && value <= max;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
function reject(message: string): never { throw new Error(`CARLA feedback: ${message}`); }
function point(value: unknown): FeedbackPoint {
  if (!object(value) || ![value.x_m, value.y_m, value.z_m].every(finite) || [value.x_m, value.y_m, value.z_m].some(v => Math.abs(Number(v)) > 1e7)) reject("계획·자차 좌표가 유효하지 않습니다.");
  return { x_m: Number(value.x_m), y_m: Number(value.y_m), z_m: Number(value.z_m) };
}
function ego(value: unknown): FeedbackEgo {
  const p = point(value);
  if (!object(value) || !finite(value.yaw_rad) || !finite(value.speed_mps) || value.speed_mps < 0 || value.speed_mps > 1000) reject("자차 방향·관측 속도가 유효하지 않습니다.");
  return { ...p, yaw_rad: value.yaw_rad, speed_mps: value.speed_mps };
}
function control(value: unknown): FeedbackControl {
  if (!object(value) || !finite(value.throttle) || value.throttle < 0 || value.throttle > 1 || !finite(value.brake) || value.brake < 0 || value.brake > 1 || !finite(value.steer) || Math.abs(value.steer) > 1) reject("요청·확인·관측 제어값이 유효하지 않습니다.");
  return { throttle: value.throttle, brake: value.brake, steer: value.steer };
}
function keys(value: unknown): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 2 || value[0] !== "front" || value.length === 2 && value[1] !== "rear") reject("지원하는 전방·후방 카메라 순서가 아닙니다.");
  return value as string[];
}
function member(value: unknown, expected: string): CarlaZipMember {
  if (!object(value) || value.name !== expected || !integer(value.size_bytes, 1, 8 * 1024 * 1024) || !hash(value.sha256)) reject("원본 파일 이름·크기·SHA-256이 유효하지 않습니다.");
  return { name: expected, size_bytes: value.size_bytes, sha256: value.sha256 };
}

// Parse original evidence before plotting. A matching index alone never establishes causality.
export function parseFeedbackEvidence(manifest: unknown, trace: unknown): FeedbackEvidence {
  if (!object(manifest) || manifest.schema_version !== "carla-feedback-native-result/1" || manifest.status !== "completed" || manifest.cleanup_status !== "confirmed" || manifest.world_settings_restored !== true || manifest.camera_is_policy_input !== false || !integer(manifest.frame_count, 20, 60) || !Array.isArray(manifest.records) || !Array.isArray(manifest.sensors) || !hash(manifest.configuration_hash) || !integer(manifest.configuration_revision, 1) || !integer(manifest.seed, 0, 2147483647)) reject("지원하는 원본 feedback capture 계약이 아닙니다.");
  if (!object(trace) || trace.schema_version !== "carla-feedback-trace/1" || trace.coordinate_convention !== "left_handed_x_forward_y_right_z_up" || trace.camera_is_policy_input !== false || trace.configuration_hash !== manifest.configuration_hash || trace.configuration_revision !== manifest.configuration_revision || trace.seed !== manifest.seed || !Array.isArray(trace.ticks) || trace.ticks.length !== manifest.frame_count || !object(trace.controller) || trace.controller.id !== "native-map-lane-follow/1" || !finite(trace.controller.target_speed_mps)) reject("제어 trace의 원본 정체성·좌표계·입력 범위가 capture와 다릅니다.");
  const traceMember = member(manifest.trace_artifact, "feedback-trace.json");
  const cameraKeys = keys(manifest.sensors.map(sensor => object(sensor) ? sensor.key : null));
  const ticks: FeedbackTick[] = trace.ticks.map((row, index) => {
    if (!object(row) || row.index !== index || row.actuator_acknowledged !== true || !integer(row.input_frame) || !integer(row.frame, 1) || row.frame !== row.input_frame + 1 || !finite(row.input_timestamp_seconds) || !finite(row.timestamp_seconds) || Math.abs(row.timestamp_seconds - row.input_timestamp_seconds - .1) > 1e-5 || typeof row.simulation_timestamp_ns !== "string" || !/^\d{1,20}$/.test(row.simulation_timestamp_ns) || Math.abs(Number(row.simulation_timestamp_ns) / 1e9 - row.timestamp_seconds) > 1e-5 || !Array.isArray(row.planned_path) || row.planned_path.length < 2 || row.planned_path.length > 512 || JSON.stringify(keys(row.rgb_keys)) !== JSON.stringify(cameraKeys)) reject(`Tick ${index}: f → f+1 시간·계획·카메라 계약이 유효하지 않습니다.`);
    return { index, input_frame: row.input_frame, input_timestamp_seconds: row.input_timestamp_seconds, input_ego: ego(row.input_ego), planned_path: row.planned_path.map(point), requested_control: control(row.requested_control), acknowledged_control: control(row.acknowledged_control), frame: row.frame, timestamp_seconds: row.timestamp_seconds, simulation_timestamp_ns: row.simulation_timestamp_ns, ego: ego(row.ego), observed_control: control(row.observed_control), rgb_keys: cameraKeys, actuator_acknowledged: true };
  });
  for (let index = 1; index < ticks.length; index++) {
    const previous = ticks[index - 1], current = ticks[index];
    if (current.input_frame !== previous.frame || Math.abs(current.input_timestamp_seconds - previous.timestamp_seconds) > 1e-5 || Math.hypot(current.input_ego.x_m - previous.ego.x_m, current.input_ego.y_m - previous.ego.y_m, current.input_ego.z_m - previous.ego.z_m) > 1e-4 || Math.abs(current.input_ego.speed_mps - previous.ego.speed_mps) > 1e-4) reject(`Tick ${index}: 다음 제어가 이전 물리 응답과 이어지지 않습니다.`);
  }
  if (manifest.records.length !== ticks.length * cameraKeys.length) reject("연속 카메라 프레임 수가 제어 trace와 다릅니다.");
  const seen = new Set<string>();
  const cameras = manifest.records.map(row => {
    if (!object(row) || !integer(row.index, 0, ticks.length - 1) || typeof row.key !== "string" || !cameraKeys.includes(row.key) || row.sensor_blueprint !== "sensor.camera.rgb" || row.width !== 640 || row.height !== 360) reject("카메라 식별자·640 × 360 원본 격자가 유효하지 않습니다.");
    const tick = ticks[row.index], identity = `${row.index}:${row.key}`;
    if (seen.has(identity) || row.frame !== tick.frame || row.timestamp_seconds !== tick.timestamp_seconds || row.simulation_timestamp_ns !== tick.simulation_timestamp_ns) reject("카메라 프레임·시간이 실제 자차 응답 시점과 다릅니다.");
    seen.add(identity);
    const raw = member(row.raw_artifact, `frames/${String(row.index).padStart(4, "0")}-${row.key}.bgra8`);
    if (raw.size_bytes !== 640 * 360 * 4 || !object(row.raw_artifact) || row.raw_artifact.media_type !== "application/octet-stream") reject("BGRA8 원본 크기·형식이 다릅니다.");
    return { index: row.index, key: row.key, frame: tick.frame, timestamp_seconds: tick.timestamp_seconds, simulation_timestamp_ns: tick.simulation_timestamp_ns, width: 640, height: 360, member: raw };
  });
  let distance = 0;
  for (const tick of ticks) distance += Math.hypot(tick.ego.x_m - tick.input_ego.x_m, tick.ego.y_m - tick.input_ego.y_m);
  if (!finite(manifest.distance_travelled_m) || Math.abs(manifest.distance_travelled_m - distance) > 1e-6) reject("수평 이동 거리가 원본 물리 궤적과 다릅니다.");
  return { ticks, cameras, cameraKeys, map: object(manifest.probe) ? String(manifest.probe.map ?? "미확인") : "미확인", configurationHash: manifest.configuration_hash, configurationRevision: manifest.configuration_revision, seed: manifest.seed, targetSpeed: trace.controller.target_speed_mps, traceMember, distance };
}

export function feedbackInputTemplate() {
  return { schema_version: "carla-feedback-sensor-spec/1", duration_seconds: 4, sample_hz: 10, spawn_index: 0, vehicle_blueprint: "vehicle.tesla.model3", expected_map: "Town10HD_Opt", expected_server_version: "1ae5356", configuration_revision: 1, coordinate_convention: "right_handed_x_forward_y_left_z_up", controller: { id: "native-map-lane-follow/1", target_speed_mps: 4, lookahead_m: 6, route_length_m: 100 }, sensors: [{ key: "front", kind: "rgb", translation_m: [1.5, 0, 1.7], rotation_deg: [0, 0, 0], rotation_order: "pitch_yaw_roll_right_hand_rule", parameters: { fov: "90" } }] };
}
