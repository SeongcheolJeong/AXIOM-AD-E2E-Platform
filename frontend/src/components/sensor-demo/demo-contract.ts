import type { LoadedCapture, SensorStreamExecution } from "../sensor-streams/types";

export type SensorDemoParameter = { key: string; type: "number"; label: string; min: number; max: number; step: number; unit: string; baseline: number; default: number; sensor_key: string };
export type SensorDemoPreset = { id: string; title: string; description: string; ready: boolean; status: string; reason: string | null; parameters: SensorDemoParameter[]; defaults: Record<string, number>; binding_revision: number | null; binding_sha256: string | null; saved_execution_id: string | null; source: { adapter_id?: string; capture_spec: Record<string, unknown>; seed: number; adapter_version: string } | null };
export type SensorDemoPreview = { schema_version: string; project_id: string; preset_id: string; binding_revision: number; binding_sha256: string; parameters: Record<string, number>; changes: { key: string; label: string; unit: string; baseline: number; value: number }[]; experiment: Record<string, unknown>; preview: { preview_hash: string; job_count: number; resource_id: string | null; execution_fidelity: string; evaluation_status: string } };
export type SensorDemoExecution = SensorStreamExecution & { created_at: string; error?: string | null; outcome_uncertain?: boolean };
export type SensorDemoAttempt = { key: string; signature: string; experiment?: { id: string; preview_hash: string }; execution?: { id: string } };
export type SensorDemoApi = <T>(path: string, init?: RequestInit) => Promise<T>;
export const sensorDemoTerminal = (status: string) => ["completed", "failed", "cancelled", "interrupted", "rejected"].includes(status);
export const sensorDemoStatus = (status: string) => ({ queued: "실행 대기", claimed: "엔진 준비", preparing: "엔진 준비", running: "센서 A/B 실행 중", completed: "새 결과 준비 완료", failed: "실행 실패", cancelled: "취소됨", interrupted: "실행 중단 · 원인 확인 필요", rejected: "실행 거절" } as Record<string, string>)[status] ?? status;
export const sensorDemoSignature = (project: string, preset: SensorDemoPreset, parameters: Record<string, number>) => JSON.stringify([project, preset.id, preset.binding_revision, preset.binding_sha256, parameters]);

export class SensorDemoPreviewChanged extends Error {}

/** The reviewed preview is the exact create body. Both rigs belong to one new native execution. */
export async function executeSensorDemo(request: SensorDemoApi, preview: SensorDemoPreview, attempt: SensorDemoAttempt, progress: (value: string) => void): Promise<{ id: string }> {
  if (attempt.execution) return attempt.execution;
  progress("실험 입력 고정 중");
  attempt.experiment ??= await request<{ id: string; preview_hash: string }>("/experiments", { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-experiment` }, body: JSON.stringify(preview.experiment) });
  if (attempt.experiment.preview_hash !== preview.preview.preview_hash) throw new SensorDemoPreviewChanged("확인한 설정과 실행 입력이 달라졌습니다. 변경 내용을 다시 확인하세요.");
  progress("A와 B를 실행 큐에 제출 중");
  attempt.execution = await request<{ id: string }>(`/experiments/${encodeURIComponent(attempt.experiment.id)}/executions`, { method: "POST", headers: { "Idempotency-Key": `${attempt.key}-execution` }, body: JSON.stringify({ preview_hash: preview.preview.preview_hash }) });
  return attempt.execution;
}

export function sensorDemoParameterError(parameter: SensorDemoParameter, value: number) {
  if (!Number.isFinite(value) || value < parameter.min || value > parameter.max) return `${parameter.min.toLocaleString()}–${parameter.max.toLocaleString()} ${parameter.unit} 범위에서 선택하세요.`;
  if (Math.abs((value - parameter.min) / parameter.step - Math.round((value - parameter.min) / parameter.step)) > 1e-7) return `${parameter.step.toLocaleString()} ${parameter.unit} 단위로 선택하세요.`;
  if (value === parameter.baseline) return "A와 다른 값을 선택하면 변화가 보입니다.";
  return "";
}


/** A result deep link must belong to the preset shown above it, including the real changed field. */
export function sensorDemoObservedChange(captures: Pick<LoadedCapture, "stage" | "sensors">[], presetId: string) {
  const a = captures.find(c => c.stage === "rig_a"), b = captures.find(c => c.stage === "rig_b");
  if (!a || !b || a.sensors.length !== b.sensors.length) throw new Error("A/B 센서 구성이 일치하지 않습니다.");
  const expected = ({ driving_camera_fov: ["front", "fov", "° FoV"], camera_fov: ["front", "fov", "° FoV"], lidar_density: ["lidar", "points_per_second", "points/s"], radar_range: ["radar", "range", "m range"] } as Record<string, string[]>)[presetId];
  const differences: { sensor: string; parameter: string; baseline: number; value: number }[] = [];
  for (const sensor of a.sensors) {
    const other = b.sensors.find(s => s.key === sensor.key);
    if (!other || sensor.kind !== other.kind || sensor.blueprint !== other.blueprint || JSON.stringify(sensor.mountRh) !== JSON.stringify(other.mountRh)) throw new Error("선택한 프리셋과 저장된 rig 구성이 다릅니다.");
    for (const key of new Set([...Object.keys(sensor.parameters), ...Object.keys(other.parameters)])) {
      const left = sensor.parameters[key], right = other.parameters[key];
      if (left === right || typeof left === "string" && typeof right === "string" && Number.isFinite(Number(left)) && Number(left) === Number(right)) continue;
      differences.push({ sensor: sensor.key, parameter: key, baseline: Number(left), value: Number(right) });
    }
  }
  const difference = differences[0];
  if (!expected || differences.length !== 1 || difference.sensor !== expected[0] || difference.parameter !== expected[1] || !Number.isFinite(difference.baseline) || !Number.isFinite(difference.value)) throw new Error("선택한 프리셋과 이 실행의 실제 변경 항목이 다릅니다. 해당 프리셋의 저장 결과를 다시 여세요.");
  return { ...difference, unit: expected[2] };
}


export const isDrivingSensorAdapter = (id: string) => id === "carla-driving-multisensor";
export const isNativeSensorAdapter = (id: string) => id === "carla-streaming-capture" || isDrivingSensorAdapter(id);
export const sensorPresetAdapter = (preset: SensorDemoPreset) => preset.source?.adapter_id ?? "carla-streaming-capture";
export function initialSensorPreset(items: SensorDemoPreset[], explicitId: string | null) {
  return items.find(item => item.id === explicitId) ?? items.find(item => item.id === "driving_camera_fov" && item.ready) ?? items.find(item => item.id === "camera_fov") ?? items[0];
}
