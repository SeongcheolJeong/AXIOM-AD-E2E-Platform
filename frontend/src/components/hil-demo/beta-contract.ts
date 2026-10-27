import { executeHilDemo } from "./demo-contract.ts";
import { hilProfileRef } from "./dut-profile.ts";
import { hilSceneRef } from "./scene-profile.ts";
import type { HilSceneProfile, HilSceneRef } from "./scene-profile.ts";
import type { HilDutProfiles, HilProfileSelections } from "./dut-profile.ts";
import type {
  HilApi,
  HilAttempt,
  HilExecution,
  HilPreset,
  HilPreview,
} from "./demo-contract.ts";

export type HilTestRef = { id: string; revision: number; sha256: string };
export type HilTest = HilTestRef & {
  schema_version: "hil-sim-test/1";
  project_id: string;
  name: string;
  note: string;
  preset_id: string;
  binding_ref: HilTestRef;
  parameters: Record<string, number>;
  dut_profiles?: HilDutProfiles;
  scene_profile?: HilSceneProfile;
  source: {
    scenario_ref: {
      id: string;
      revision: number;
      configuration_hash: string;
      manifest_hash: string;
    };
    adapter_id: string;
    adapter_version: string;
    seed: number;
  };
  experiment: Record<string, unknown>;
  copied_from: HilTestRef | null;
  created_at: string;
  author: { user_label?: string; role?: string };
};
export type HilTestPreview = HilPreview & { test_ref: HilTestRef };
export type HilTestExecution = {
  test_ref: HilTestRef;
  execution: HilExecution;
};
export type HilObservation = {
  id: string;
  title: string;
  note: string;
  time_s: number;
  side: "baseline" | "faulted" | "both";
  kind: "observation" | "issue";
  created_at: string;
  samples: Record<
    string,
    {
      status: string;
      reason?: string;
      time_s?: number;
      response_frame?: number;
    }
  >;
  execution_id: string;
};
export type HilTestDraft = {
  name: string;
  note: string;
  preset_id: string;
  parameters: Record<string, number>;
  dut_profiles?: HilProfileSelections;
  scene_profile?: HilSceneRef;
  binding_ref: HilTestRef;
  copied_from?: HilTestRef;
};
export type HilPending = {
  version: 1;
  project: string;
  principal: string;
  test_ref: HilTestRef;
  preview: HilTestPreview;
  attempt: HilAttempt;
  attachment_key: string;
};
export const hilTestRef = (test: HilTest): HilTestRef => ({
  id: test.id,
  revision: test.revision,
  sha256: test.sha256,
});
export const hilTestDraft = (test: HilTest): HilTestDraft => ({
  name: test.name,
  note: test.note,
  preset_id: test.preset_id,
  parameters: { ...test.parameters },
  ...(test.scene_profile
    ? { scene_profile: hilSceneRef(test.scene_profile) }
    : {}),
  ...(test.dut_profiles
    ? {
        dut_profiles: {
          baseline: hilProfileRef(test.dut_profiles.baseline),
          faulted: hilProfileRef(test.dut_profiles.faulted),
        },
      }
    : {}),
  binding_ref: { ...test.binding_ref },
});
export const hilDuplicateDraft = (
  saved: HilTest,
  draft: HilTestDraft,
): HilTestDraft => ({
  ...draft,
  name: `${draft.name} · 복사`,
  parameters: { ...draft.parameters },
  binding_ref: { ...draft.binding_ref },
  copied_from: hilTestRef(saved),
});
export function hilDraftFromPreset(preset: HilPreset): HilTestDraft {
  if (!preset.binding_revision || !preset.binding_sha256)
    throw new Error("먼저 이 프로젝트의 고정 시나리오를 연결해야 합니다.");
  return {
    name: `${preset.title} · 내 시험`,
    note: "",
    preset_id: preset.id,
    parameters: { ...preset.defaults },
    binding_ref: {
      id: preset.id,
      revision: preset.binding_revision,
      sha256: preset.binding_sha256,
    },
  };
}
export const hilDraftSignature = (draft: HilTestDraft) =>
  JSON.stringify([
    draft.name.trim(),
    draft.note.trim(),
    draft.preset_id,
    draft.binding_ref,
    draft.parameters,
    ...(draft.dut_profiles ? [draft.dut_profiles] : []),
    ...(draft.scene_profile ? [draft.scene_profile] : []),
  ]);
export function hilTestSaveBody(
  project: string,
  draft: HilTestDraft,
  saved: HilTest | null,
) {
  return {
    project_id: project,
    ...(saved ? { id: saved.id } : {}),
    expected_revision: saved?.revision ?? 0,
    name: draft.name.trim(),
    note: draft.note.trim(),
    preset_id: draft.preset_id,
    binding_revision: draft.binding_ref.revision,
    binding_sha256: draft.binding_ref.sha256,
    parameters: draft.parameters,
    ...(draft.dut_profiles ? { dut_profiles: draft.dut_profiles } : {}),
    ...(draft.scene_profile ? { scene_profile: draft.scene_profile } : {}),
    ...(draft.copied_from ? { copied_from: draft.copied_from } : {}),
  };
}
export function hilTestPreviewMatches(preview: HilTestPreview, test: HilTest) {
  const ref = hilTestRef(test);
  return (
    preview.test_ref?.id === ref.id &&
    preview.test_ref.revision === ref.revision &&
    preview.test_ref.sha256 === ref.sha256
  );
}
export function hilObservationsPath(
  project: string,
  executionId: string | null,
  test: HilTest | null,
): string | null {
  if (!executionId || !test) return null;
  return `/hil-sim/observations?${new URLSearchParams({ project_id: project, execution_id: executionId, test_id: test.id, revision: String(test.revision) })}`;
}
export const hilResultQuery = (
  executionId: string,
  timeS: number | null = null,
): Record<string, string | null> => ({
  result: executionId,
  time_s: timeS === null ? null : String(timeS),
  moment: null,
});
export const hilTrackedResultQuery = (
  executionId: string,
  timeS: number | null = null,
): Record<string, string | null> => ({
  ...hilResultQuery(executionId, timeS),
  run: executionId,
});
export async function executeHilTest(
  request: HilApi,
  pending: HilPending,
  progress: (text: string) => void,
  checkpoint: (pending: HilPending) => void,
): Promise<{ id: string }> {
  if (
    pending.preview.test_ref.id !== pending.test_ref.id ||
    pending.preview.test_ref.revision !== pending.test_ref.revision ||
    pending.preview.test_ref.sha256 !== pending.test_ref.sha256
  )
    throw new Error("실행 확인 결과가 저장 시험 개정과 다릅니다.");
  const execution = await executeHilDemo(
    request,
    pending.preview,
    pending.attempt,
    progress,
  );
  checkpoint(pending);
  progress("저장 시험에 실행 연결 중");
  await request(
    `/hil-sim/tests/${encodeURIComponent(pending.test_ref.id)}/executions`,
    {
      method: "POST",
      headers: { "Idempotency-Key": pending.attachment_key },
      body: JSON.stringify({
        project_id: pending.project,
        test_ref: pending.test_ref,
        execution_id: execution.id,
      }),
    },
  );
  return execution;
}
export const hilPendingKey = (project: string, principal: string) =>
  `axiom.hil-beta.pending.v1:${encodeURIComponent(project)}:${encodeURIComponent(principal)}`;
export function restoreHilPending(
  raw: string | null,
  project: string,
  principal: string,
): HilPending | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as HilPending;
    if (
      value.version !== 1 ||
      value.project !== project ||
      value.principal !== principal ||
      !value.test_ref?.id ||
      !value.test_ref.sha256 ||
      !value.preview?.preview?.preview_hash ||
      !value.attempt?.key ||
      !value.attachment_key ||
      value.preview.test_ref?.id !== value.test_ref.id ||
      value.preview.test_ref?.revision !== value.test_ref.revision ||
      value.preview.test_ref?.sha256 !== value.test_ref.sha256
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
