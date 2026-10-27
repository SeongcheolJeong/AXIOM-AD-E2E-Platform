export type DemoCaseLink = { label: string; href: string };
export type DemoCaseExecution = {
  role: string; id: string; project_id: string; input_hash: string; manifest_hash: string;
  configuration_hash: string | null; adapter_id: string; adapter_version: string; fidelity: string;
};
export type DemoCase = {
  id: string; module: string; title: string; question: string;
  change: { label: string; before: string | number; after: string | number; unit: string | null };
  observation: string; observation_source?: "curator_note_for_pinned_results" | "investigation_guidance"; next_action: string; limits: string[];
  ready: boolean; status: "ready" | "unavailable" | "invalid"; reason: string | null;
  source_family: { id: string; label: string; kind: "synthetic_carla" } | null;
  executions: DemoCaseExecution[]; primary_link: DemoCaseLink | null; links: DemoCaseLink[];
  key_moments: (DemoCaseLink & { time_s: number; clock: "simulation" })[];
  binding: { kind: string; id: string; revision: number; sha256: string } | null;
};
export type DemoCaseCatalog = {
  schema_version: "demo-cases/1"; project_id: string; collection: "carla_external";
  items: DemoCase[];
  counts: { templates: number; ready: number; unavailable: number; unique_executions: number; source_families: number };
  source_families: { id: string; label: string; kind: string; ready_case_count: number }[];
  limits: string[];
};
export type DemoStory = "all" | "investigate" | "observe" | "respond" | "viewpoint";
export const demoStories: { id: Exclude<DemoStory, "all">; number: string; title: string; description: string }[] = [
  { id: "viewpoint", number: "OPENING", title: "기록한 순간의 시점을 바꾼다", description: "Neural Sim · 원본 시간을 고정하고 실제 렌더된 카메라 시점을 탐색합니다." },
  { id: "investigate", number: "STORY 01", title: "왜 기다렸는지 조사하고 다시 실행한다", description: "Object → Log → Case · 조건 변경이 차량의 움직임에 어떤 차이를 만드는지 비교합니다." },
  { id: "observe", number: "STORY 02", title: "센서 설정이 관측을 어떻게 바꿀까?", description: "Sensor Sim · 시야, 포인트 밀도, 관측 거리의 차이를 같은 시간축에서 살펴봅니다." },
  { id: "respond", number: "STORY 03", title: "입력에 문제가 생기면 어떻게 반응할까?", description: "Software-DUT HIL · 입력 지연과 단절, 차량 반응, 복구를 영상과 신호로 연결합니다." },
];
const storyForCase: Record<string, Exclude<DemoStory, "all">> = {
  object_lead_wait: "investigate", log_lead_wait: "investigate", object_gap_release: "investigate",
  sensor_driving_fov: "observe", sensor_lidar_density: "observe", sensor_radar_range: "observe",
  hil_delay_200: "respond", hil_delay_400: "respond", hil_dropout_1: "respond", neural_frozen_sweep: "viewpoint",
};
export function demoCaseStory(item: DemoCase): Exclude<DemoStory, "all"> | null {
  return storyForCase[item.id] ?? null;
}

// A retained link must stay in this project and use an existing product viewer.
export function scopedDemoLink(href: string, project: string): string | null {
  const match = /^#([a-z-]+)\?([^#]*)$/.exec(href);
  if (!match || !["object-sim", "log-sim", "sensor-sim", "hil-sim", "neural-sim", "cases", "viewers", "experiments"].includes(match[1])) return null;
  const params = new URLSearchParams(match[2]);
  return params.getAll("project").length === 1 && params.get("project") === project ? href : null;
}

export function demoCatalogError(catalog: DemoCaseCatalog, project: string): string | null {
  if (catalog.schema_version !== "demo-cases/1" || catalog.collection !== "carla_external") return "지원하지 않는 사례 목록 버전입니다. 결과를 새로고침해 주세요.";
  if (catalog.project_id !== project) return "선택한 작업공간과 사례 목록의 작업공간이 다릅니다. 결과를 표시하지 않았습니다.";
  if (!Array.isArray(catalog.items) || catalog.items.some(item => !Array.isArray(item.executions) || item.executions.some(result => result.project_id !== project))) return "사례의 실행 작업공간이 일치하지 않습니다. 결과를 표시하지 않았습니다.";
  return null;
}

export function demoCaseReady(item: DemoCase, project: string): boolean {
  return item.ready && item.status === "ready" && item.executions.length > 0
    && item.executions.every(result => result.project_id === project && !!result.manifest_hash)
    && !!item.primary_link && scopedDemoLink(item.primary_link.href, project) !== null;
}

export function filterDemoCases(items: DemoCase[], project: string, story: DemoStory, readyOnly: boolean): DemoCase[] {
  return items.filter(item => (story === "all" || demoCaseStory(item) === story) && (!readyOnly || demoCaseReady(item, project)));
}

export function demoCaseCounts(items: DemoCase[], project: string) {
  const ready = items.filter(item => demoCaseReady(item, project));
  return {
    ready: ready.length,
    executions: new Set(ready.flatMap(item => item.executions.map(result => result.id))).size,
    sourceFamilies: new Set(ready.flatMap(item => item.source_family ? [item.source_family.id] : [])).size,
  };
}
