import type { BetaRef, BetaEvidence, BetaTask } from "./beta-contract.ts";

export const sensorGuides = [
  { id: "camera_direction", title: "카메라 방향 바꾸기", description: "전방·후방 카메라의 방향을 바꾼 배치를 저장하고 A/B 영상을 비교합니다." },
  { id: "lidar_height", title: "LiDAR 높이 바꾸기", description: "LiDAR 장착 높이를 바꾼 배치를 저장하고 반환점의 차이를 조사합니다." },
  { id: "radar_direction", title: "Radar 방향 바꾸기", description: "Radar 방향을 바꾼 배치를 저장하고 관측 방향과 반환점을 비교합니다." },
] as const;
export type SensorGuide = typeof sensorGuides[number]["id"];
export type BetaSensorKey = "front" | "rear" | "lidar" | "radar";
export const betaSensorNames: Record<BetaSensorKey, string> = { front: "전방 카메라", rear: "후방 카메라", lidar: "LiDAR", radar: "Radar" };

/** Hashes are part of a saved revision identity, not just its displayed number. */
export function sameBetaRef(left: BetaRef | null | undefined, right: BetaRef | null | undefined): boolean {
  if (!left || !right || left.id !== right.id || left.revision !== right.revision) return false;
  return ["sha256", "configuration_hash", "manifest_hash"].every(key => left[key] === right[key]);
}

/** A saved Sensor observation must keep its exact layout and execution together. */
export function matchingBetaObservation(item: BetaEvidence, source: BetaRef | null | undefined, executionId: string) {
  return item.kind === "observation" && !!executionId && item.execution_id === executionId && sameBetaRef(item.source_ref, source);
}

export function betaObservationRequest(task: BetaTask, observation: {
  title: string; note: string; kind: string; time_s: number; side: string; sensor_key: BetaSensorKey;
}) {
  if (!task.source_ref || !task.execution_id) throw new Error("관찰을 저장할 개정과 실행을 먼저 연결해 주세요.");
  if (!Number.isFinite(observation.time_s) || observation.time_s < 0 || task.duration_s != null && observation.time_s > task.duration_s)
    throw new Error("관찰 시점은 원본 실행의 재생 범위 안에 있어야 합니다.");
  const common = { project_id: task.project_id, execution_id: task.execution_id,
    title: observation.title.trim(), note: observation.note.trim(), kind: observation.kind, time_s: observation.time_s };
  if (task.module === "sensor") {
    if (!["rig_a", "rig_b", "both"].includes(observation.side)) throw new Error("센서 관찰의 비교 대상을 선택해 주세요.");
    return { path: "/sensor-sim/layout-observations", body: { ...common, layout_ref: task.source_ref, sensor_key: observation.sensor_key, side: observation.side } };
  }
  if (task.module === "hil") return { path: "/hil-sim/observations", body: { ...common, test_ref: task.source_ref, side: observation.side } };
  if (task.module === "object") return { path: "/object-sim/observations", body: { ...common, scenario_ref: task.source_ref } };
  throw new Error("이 모듈의 안내 과제에서 관찰을 저장해 주세요.");
}
