import { record, sameValue } from "../object-scene/object-scene.ts";
import type { HilSide } from "./demo-contract.ts";

export type HilDutProfileRef = { id: string; revision: number; sha256: string };
export type HilDutParameters = {
  max_object_age_ms: number;
  recovery_fresh_steps: number;
};
export type HilDutProfile = HilDutProfileRef & {
  schema_version: "hil-dut-profile/1";
  project_id: string;
  name: string;
  note: string;
  parameters: HilDutParameters;
  created_at: string;
  author: { user_label?: string; role?: string };
};
export type HilDutProfiles = Record<HilSide, HilDutProfile>;
export type HilProfileSelections = Record<HilSide, HilDutProfileRef | null>;
export const hilUpdateProfileSelection = (
  current: HilProfileSelections | undefined,
  side: HilSide,
  ref: HilDutProfileRef,
) => (current ? { ...current, [side]: ref } : undefined);
export type HilComparisonKind = "same_scene_fault_ab" | "same_fault_dut_ab";
export const hilProfileRef = (profile: HilDutProfile): HilDutProfileRef => ({
  id: profile.id,
  revision: profile.revision,
  sha256: profile.sha256,
});
export function hilProfileError(parameters: HilDutParameters) {
  if (
    !Number.isInteger(parameters.max_object_age_ms) ||
    parameters.max_object_age_ms < 50 ||
    parameters.max_object_age_ms > 1000 ||
    parameters.max_object_age_ms % 50 !== 0
  )
    return "입력 age 임계값은 50–1000 ms, 50 ms 단위로 입력하세요.";
  if (
    !Number.isInteger(parameters.recovery_fresh_steps) ||
    parameters.recovery_fresh_steps < 1 ||
    parameters.recovery_fresh_steps > 10
  )
    return "회복 조건은 연속 정상 입력 1–10개로 입력하세요.";
  return "";
}
export function hilProfileSelectionError(
  profiles: HilProfileSelections | undefined,
) {
  return profiles && (!profiles.baseline || !profiles.faulted)
    ? "A와 B의 저장된 DUT 프로필 개정을 선택하세요."
    : "";
}
export function parseHilDutProfile(
  value: unknown,
  project?: string,
): HilDutProfile {
  const profile = record(value, "DUT profile"),
    parameters = record(profile.parameters, "DUT profile parameters");
  if (
    profile.schema_version !== "hil-dut-profile/1" ||
    typeof profile.id !== "string" ||
    !profile.id ||
    !Number.isInteger(profile.revision) ||
    Number(profile.revision) < 1 ||
    typeof profile.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(profile.sha256) ||
    typeof profile.project_id !== "string" ||
    (project !== undefined && profile.project_id !== project) ||
    typeof profile.name !== "string" ||
    !profile.name.trim() ||
    typeof profile.note !== "string" ||
    typeof profile.created_at !== "string" ||
    hilProfileError(parameters as HilDutParameters)
  )
    throw new Error(
      "보존된 DUT 프로필의 개정·정책·프로젝트가 유효하지 않습니다.",
    );
  record(profile.author, "DUT profile author");
  return profile as unknown as HilDutProfile;
}
function sortedJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${sortedJson((value as Record<string, unknown>)[key])}`,
      )
      .join(",")}}`;
  return JSON.stringify(value);
}
export async function verifyHilProfileHash(profile: HilDutProfile) {
  const { sha256, ...content } = profile;
  const bytes = new TextEncoder().encode(sortedJson(content));
  const actual = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  )
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== sha256)
    throw new Error("보존된 DUT 프로필 내용과 SHA-256이 다릅니다.");
}
export function hilProfileComparison(
  summary: Record<string, unknown>,
  source: Record<string, unknown>,
  project: string,
  spec?: Record<string, unknown>,
): { comparisonKind: HilComparisonKind; profiles?: HilDutProfiles } {
  const comparisonKind = summary.comparison_kind ?? "same_scene_fault_ab";
  if (
    !["same_scene_fault_ab", "same_fault_dut_ab"].includes(
      String(comparisonKind),
    ) ||
    (source.comparison_kind ?? "same_scene_fault_ab") !== comparisonKind ||
    (spec &&
      spec.comparison_kind !== undefined &&
      spec.comparison_kind !== comparisonKind)
  )
    throw new Error("보존된 HIL 비교 모드가 실행 입력과 다릅니다.");
  if (comparisonKind === "same_scene_fault_ab") {
    if (
      summary.dut_profiles !== undefined ||
      source.dut_profiles !== undefined ||
      (spec?.input !== undefined &&
        record(spec.input, "HIL spec input").dut_profiles !== undefined)
    )
      throw new Error(
        "정상/고장 비교에 지원하지 않는 DUT 프로필이 포함되어 있습니다.",
      );
    return { comparisonKind };
  }
  const raw = record(summary.dut_profiles, "HIL DUT profiles"),
    sides = record(summary.sides, "HIL sides");
  const profiles = {
    baseline: parseHilDutProfile(raw.baseline, project),
    faulted: parseHilDutProfile(raw.faulted, project),
  };
  if (
    !sameValue(raw, profiles) ||
    !sameValue(source.dut_profiles, profiles) ||
    !spec ||
    spec.comparison_kind !== "same_fault_dut_ab" ||
    !sameValue(record(spec.input, "HIL spec input").dut_profiles, profiles) ||
    ["baseline", "faulted"].some(
      (side) =>
        !sameValue(
          record(sides[side], "HIL side").dut_profile,
          profiles[side as HilSide],
        ),
    )
  )
    throw new Error("실행 입력·결과의 고정 DUT 프로필이 다릅니다.");
  return { comparisonKind: "same_fault_dut_ab", profiles };
}
