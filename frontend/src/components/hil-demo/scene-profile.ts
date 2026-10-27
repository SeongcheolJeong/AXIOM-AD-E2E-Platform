import { record, sameValue } from "../object-scene/object-scene.ts";

export type HilSceneRef = { id: string; revision: number; sha256: string };
export type HilSceneProfile = HilSceneRef & {
  schema_version: "hil-scene-profile/1";
  id: "lead_deceleration" | "lead_stop_go";
  title: string;
  description: string;
  clock: "simulation_input";
  duration_s: 18;
  lead_speed_schedule: { at_s: number; target_speed_mps: number }[];
};
export type HilSceneCatalog = {
  schema_version: "hil-sim-scenes/1";
  project_id: string;
  default_scene_id: "lead_stop";
  source_scope: "existing_preset_bound_base_scene";
  items: HilSceneProfile[];
};
export const hilSceneRef = (scene: HilSceneProfile): HilSceneRef => ({
  id: scene.id,
  revision: scene.revision,
  sha256: scene.sha256,
});
export const hilSceneTitle = (scene?: HilSceneProfile) =>
  scene?.title ?? "앞차 정지 · 기존 장면";
export const hilSceneScheduleLabel = (scene: HilSceneProfile) =>
  scene.lead_speed_schedule
    .map((step) => `${step.at_s} s → ${step.target_speed_mps} m/s`)
    .join(" · ");
export function parseHilSceneProfile(raw: unknown): HilSceneProfile {
  const scene = record(raw, "HIL scene profile");
  const schedule =
    scene.id === "lead_deceleration"
      ? [
          { at_s: 0, target_speed_mps: 2 },
          { at_s: 4, target_speed_mps: 0.5 },
        ]
      : scene.id === "lead_stop_go"
        ? [
            { at_s: 0, target_speed_mps: 2 },
            { at_s: 4, target_speed_mps: 0 },
            { at_s: 8, target_speed_mps: 2 },
          ]
        : null;
  if (
    !schedule ||
    scene.schema_version !== "hil-scene-profile/1" ||
    scene.revision !== 1 ||
    scene.clock !== "simulation_input" ||
    scene.duration_s !== 18 ||
    typeof scene.title !== "string" ||
    !scene.title.trim() ||
    typeof scene.description !== "string" ||
    typeof scene.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(scene.sha256) ||
    !sameValue(scene.lead_speed_schedule, schedule)
  )
    throw new Error(
      "보존된 HIL 장면의 개정·시간 기준·앞차 목표 일정이 유효하지 않습니다.",
    );
  return scene as unknown as HilSceneProfile;
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`,
      )
      .join(",")}}`;
  return JSON.stringify(value);
}
export async function verifyHilSceneHash(scene: HilSceneProfile) {
  const { sha256, ...content } = scene;
  const actual = [
    ...new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(canonical(content)),
      ),
    ),
  ]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== sha256)
    throw new Error("보존된 장면 내용과 SHA-256이 다릅니다.");
}
export function hilSceneContext(
  summary: Record<string, unknown>,
  source: Record<string, unknown>,
  spec?: Record<string, unknown>,
): HilSceneProfile | undefined {
  const sides =
    summary.sides === undefined ? {} : record(summary.sides, "HIL sides");
  const specScene =
    spec?.input === undefined
      ? undefined
      : record(spec.input, "HIL spec input").scene_profile;
  const value = source.scene_profile;
  if (value === undefined) {
    if (
      summary.scene_profile !== undefined ||
      specScene !== undefined ||
      ["baseline", "faulted"].some(
        (side) =>
          sides[side] !== undefined &&
          record(sides[side], "HIL side").scene_profile !== undefined,
      )
    )
      throw new Error(
        "기존 HIL 장면에 실행 입력과 다른 장면 프로필이 포함되어 있습니다.",
      );
    return undefined;
  }
  const scene = parseHilSceneProfile(value);
  if (
    !sameValue(summary.scene_profile, scene) ||
    !sameValue(specScene, scene) ||
    ["baseline", "faulted"].some(
      (side) =>
        !sameValue(record(sides[side], "HIL side").scene_profile, scene),
    )
  )
    throw new Error("실행 입력·결과의 고정 장면 프로필이 다릅니다.");
  return scene;
}
