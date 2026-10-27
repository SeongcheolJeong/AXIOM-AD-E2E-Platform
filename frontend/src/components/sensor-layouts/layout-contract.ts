import type { CaptureEvidence, Matrix, SensorStreamExecution } from "../sensor-streams/types.ts";

export const sensorKeys = ["front", "rear", "lidar", "radar"] as const;
export type LayoutSensorKey = typeof sensorKeys[number];
export const sensorLabels: Record<LayoutSensorKey, string> = { front: "전방 RGB", rear: "후방 RGB", lidar: "LiDAR", radar: "Radar" };
export type Triple = [number, number, number];
export type LayoutSensor = { key: LayoutSensorKey; kind: "rgb" | "lidar" | "radar"; translation_m: Triple; rotation_deg: Triple; rotation_order: "pitch_yaw_roll_right_hand_rule"; parameters: Record<string, string> };
export type LayoutRig = { sensors: LayoutSensor[]; [key: string]: unknown };
export type LayoutRef = { id: string; revision: number; sha256: string };
export type SensorLayout = LayoutRef & { schema_version: string; project_id: string; name: string; note: string; rig_a: LayoutRig; rig_b: LayoutRig; seed: number; adapter_id: string; adapter_version: string; template_ref: LayoutRef; copied_from: LayoutRef | null; created_at: string };
export type LayoutTemplates = { template: { id: string; revision: number; sha256: string; rig_a: LayoutRig; rig_b: LayoutRig; seed: number; adapter_id: string; adapter_version: string }; bounds: { translation_m: { min: number; max: number; step: number }; rotation_deg: { min: number; max: number; step: number } }; tasks: { id: string; title: string; description: string; sensor_key: LayoutSensorKey; translation_m?: Triple; rotation_deg?: Triple }[]; limitations: string[] };
export type LayoutPreview = { layout_ref: LayoutRef; project_id: string; experiment: Record<string, unknown>; preview: { preview_hash: string; job_count: number; execution_fidelity: string; [key: string]: unknown }; changes: { sensor_key: string; field: string; baseline: unknown; value: unknown }[] };
export type LayoutExecution = { id: string; layout_ref: LayoutRef; execution_id: string; input_hash: string; execution: SensorStreamExecution & { error?: string | null; outcome_uncertain?: boolean }; created_at: string };
export type LayoutMoment = { index: number; time_s: number; sensor_key: LayoutSensorKey; execution_id: string };
export type LayoutObservation = { id: string; revision: number; execution_id: string; layout_ref: LayoutRef; sensor_key: LayoutSensorKey; side: "rig_a" | "rig_b" | "both"; time_s: number; title: string; note: string; kind: string; created_at: string; samples: Record<string, { index: number; frame: number; time_s: number }> };
export const layoutRef = (layout: LayoutRef): LayoutRef => ({ id: layout.id, revision: layout.revision, sha256: layout.sha256 });
export const sameLayoutRef = (left: LayoutRef, right: LayoutRef) => left.id === right.id && left.revision === right.revision && left.sha256 === right.sha256;
export const layoutTerminal = (status: string) => ["completed", "failed", "cancelled", "interrupted"].includes(status);
export function layoutSensor(value: string | null, guide: string | null = null): LayoutSensorKey {
  if (value === null && guide === "lidar_height") return "lidar";
  if (value === null && guide === "radar_direction") return "radar";
  return sensorKeys.includes(value as LayoutSensorKey) ? value as LayoutSensorKey : "front";
}
export function layoutMountError(rig: LayoutRig): string | null {
  if (rig.sensors.length !== 4 || sensorKeys.some(key => rig.sensors.filter(sensor => sensor.key === key).length !== 1)) return "고정된 전방·후방·LiDAR·radar 4개 센서가 필요합니다.";
  for (const sensor of rig.sensors) {
    for (const [field, limit, step] of [["translation_m", 10, .05], ["rotation_deg", 180, 1]] as const) {
      const values = sensor[field];
      if (values.length !== 3 || values.some(value => !Number.isFinite(value) || Math.abs(value) > limit || Math.abs(value / step - Math.round(value / step)) > 1e-7)) return `${sensorLabels[sensor.key]}: ${field === "translation_m" ? "XYZ는 −10~10 m, 0.05 m 단위" : "각도는 −180~180°, 1° 단위"}로 입력하세요.`;
    }
  }
  return null;
}
export function mountMatrix(sensor: Pick<LayoutSensor, "translation_m" | "rotation_deg">): Matrix {
  const [p, y, r] = sensor.rotation_deg.map(value => value * Math.PI / 180);
  const [cp, sp, cy, sy, cr, sr] = [Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y), Math.cos(r), Math.sin(r)];
  return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, sensor.translation_m[0]], [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, sensor.translation_m[1]], [-sp, cp * sr, cp * cr, sensor.translation_m[2]], [0, 0, 0, 1]];
}
export function mountPoint(sensor: Pick<LayoutSensor, "translation_m" | "rotation_deg">, point: Triple): Triple {
  const matrix = mountMatrix(sensor); return [0, 1, 2].map(i => point.reduce((sum, value, j) => sum + matrix[i][j] * value, matrix[i][3])) as Triple;
}
export function mountChanges(a: LayoutRig, b: LayoutRig) {
  return a.sensors.flatMap(sensor => { const candidate = b.sensors.find(item => item.key === sensor.key); if (!candidate) return []; return (["translation_m", "rotation_deg"] as const).flatMap(field => sensor[field].map((baseline, index) => ({ sensor_key: sensor.key, field, axis: index, baseline, value: candidate[field][index] })).filter(change => change.baseline !== change.value)); });
}
export function layoutResultMatches(captures: CaptureEvidence[], layout: SensorLayout, link: LayoutExecution): void {
  if (!sameLayoutRef(layout, link.layout_ref) || link.execution.input_hash !== link.input_hash || link.execution.id !== link.execution_id || link.execution.project_id !== layout.project_id || link.execution.adapter_id !== layout.adapter_id || link.execution.status !== "completed") throw new Error("저장 배치와 실행 출처가 일치하지 않습니다.");
  if (captures.length !== 2 || !captures.some(c => c.stage === "rig_a") || !captures.some(c => c.stage === "rig_b")) throw new Error("배치 비교에는 동일 실행의 A/B 원본이 필요합니다.");
  for (const capture of captures) {
    const rig = capture.stage === "rig_a" ? layout.rig_a : layout.rig_b;
    if (!capture.motion || capture.seed !== layout.seed || capture.sensors.length !== 4) throw new Error("고정 주행 배치의 원본 센서 구성이 다릅니다.");
    for (const sensor of rig.sensors) {
      const observed = capture.sensors.find(item => item.key === sensor.key), expected = mountMatrix(sensor);
      if (!observed || observed.kind !== sensor.kind || observed.mountRh.some((row, i) => row.some((value, j) => Math.abs(value - expected[i][j]) > 2e-6))) throw new Error(`${sensorLabels[sensor.key]} 원본 장착 위치가 저장 배치와 다릅니다.`);
    }
  }
  const [a, b] = [captures.find(c => c.stage === "rig_a")!, captures.find(c => c.stage === "rig_b")!];
  for (const sensor of a.sensors) {
    const other = b.sensors.find(item => item.key === sensor.key)!;
    const keys = new Set([...Object.keys(sensor.parameters), ...Object.keys(other.parameters)]);
    for (const key of keys) if (String(sensor.parameters[key]) !== String(other.parameters[key]) && !(Number.isFinite(Number(sensor.parameters[key])) && Number(sensor.parameters[key]) === Number(other.parameters[key]))) throw new Error("배치 비교의 A/B 센서 파라미터가 다릅니다.");
  }
}
export function layoutQuery(hash: string, values: Record<string, string | null>): string {
  const query = new URLSearchParams(hash.split("?")[1] ?? ""); query.set("mode", "layout");
  for (const [key, value] of Object.entries(values)) if (value === null) query.delete(key); else query.set(key, value);
  return `#sensor-sim?${query}`;
}
export function setLayoutQuery(values: Record<string, string | null>) { history.replaceState(null, "", `${location.pathname}${location.search}${layoutQuery(location.hash, values)}`); }
