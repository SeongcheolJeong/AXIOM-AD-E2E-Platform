import type { HilTest, HilTestRef } from "./beta-contract.ts";
import { hilTestRef } from "./beta-contract.ts";

export type HilBatchExecution = {
  id: string;
  status: string;
  outcome_uncertain?: boolean;
  error?: string | null;
  input_hash?: string;
  manifest_hash?: string;
};
export type HilBatchAttempt = {
  attempt: number;
  status: string;
  execution_id: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  execution: HilBatchExecution | null;
  outcome_uncertain: boolean;
  result: HilBatchResult | null;
};
export type HilBatchResultSide = {
  trace_sha256: string;
  response_count: number;
  dut_states: string[];
  first_degraded_s: number | null;
  first_recovered_s: number | null;
  peak_input_age_ms: number;
  peak_brake: number;
};
export type HilBatchResult = {
  schema_version: "hil-sim-batch-result/1";
  execution_id: string;
  manifest_hash: string;
  sides: { baseline: HilBatchResultSide; faulted: HilBatchResultSide };
  verification_status: "not_a_test_verdict";
};
export type HilBatchItem = {
  index: number;
  test_ref: HilTestRef;
  test: HilTest;
  status: string;
  attempts: HilBatchAttempt[];
};
export type HilBatch = {
  schema_version: "hil-sim-batch/1";
  id: string;
  project_id: string;
  revision: number;
  sha256: string;
  definition_sha256: string;
  name: string;
  note: string;
  status:
    "draft" | "running" | "stopping" | "stopped" | "cancelled" | "completed";
  created_at: string;
  updated_at: string;
  author: { user_label?: string; role?: string };
  error: string | null;
  can_start: boolean;
  can_cancel: boolean;
  can_resume: boolean;
  action_reason: string | null;
  items: HilBatchItem[];
  live_executions?: Record<
    string,
    {
      id: string;
      status: string;
      outcome_uncertain: boolean;
      started_at?: string | null;
      finished_at?: string | null;
      cancel_requested_at?: string | null;
    }
  >;
};
export type HilBatchPreview = {
  schema_version: "hil-sim-batch-preview/1";
  batch_ref: HilTestRef;
  preview_hash: string;
  items: {
    index: number;
    test_ref: HilTestRef;
    preview_hash: string;
    execution_fidelity: string;
    resource_id: string;
  }[];
  job_count: number;
  requires_explicit_submit: true;
};
export type HilBatchDraft = { name: string; note: string; tests: HilTest[] };
export type HilBatchMutation = {
  version: 1;
  project: string;
  principal: string;
  path: string;
  body: string;
  key: string;
  action: "save" | "start" | "cancel" | "resume";
};
export const hilBatchRefKey = (ref: HilTestRef) =>
  `${ref.id}:${ref.revision}:${ref.sha256}`;
export const hilBatchActive = (batch: HilBatch) =>
  ["running", "stopping"].includes(batch.status);
export const hilBatchLastAttempt = (item: HilBatchItem) => item.attempts.at(-1);
export const hilBatchAttemptUncertain = (attempt?: HilBatchAttempt) =>
  !!(attempt?.outcome_uncertain || attempt?.execution?.outcome_uncertain);
export function hilBatchRowState(batch: HilBatch, item: HilBatchItem) {
  const attempt = hilBatchLastAttempt(item);
  const live = attempt?.execution_id
    ? batch.live_executions?.[attempt.execution_id]
    : undefined;
  return {
    status: live?.status ?? item.status,
    uncertain: !!live?.outcome_uncertain || hilBatchAttemptUncertain(attempt),
    summaryPending: live?.status === "completed" && item.status !== "completed",
  };
}
export const hilBatchRequestRejected = (status: number) =>
  status >= 400 && status < 500 && status !== 408;
export function hilBatchActionNotice(
  action: HilBatchMutation["action"],
  status: HilBatch["status"],
) {
  if (status === "completed")
    return "모든 조건의 실행이 완료되었습니다. 각 실제 A/B 결과와 관찰을 확인하세요.";
  if (status === "cancelled")
    return "순차 실행이 취소되었습니다. 기존 실행 시도와 결과는 보존했습니다.";
  if (status === "stopped")
    return "순차 실행이 중단되었습니다. 원본 실행 기록과 이어가기 가능 여부를 확인하세요.";
  if (action === "save")
    return "실행 순서와 시험 개정을 고정했습니다. 엔진 준비를 확인한 뒤 시작하세요.";
  if (action === "cancel")
    return "중단을 요청했습니다. 현재 실행의 실제 종료 상태를 확인하는 중입니다.";
  return "순차 실행을 접수했습니다. 이 화면을 닫아도 서버의 진행 기록을 다시 열 수 있습니다.";
}
export function hilBatchDraftError(draft: HilBatchDraft) {
  if (!draft.name.trim()) return "순차 실행 이름을 입력하세요.";
  if (draft.tests.length < 3 || draft.tests.length > 5)
    return "저장된 시험 개정 3–5개를 선택하세요.";
  if (new Set(draft.tests.map(hilBatchRefKey)).size !== draft.tests.length)
    return "같은 시험의 같은 개정은 한 번만 넣을 수 있습니다.";
  if (draft.tests.some((test) => test.project_id !== draft.tests[0].project_id))
    return "같은 프로젝트의 시험만 함께 실행할 수 있습니다.";
  return "";
}
export const hilBatchSaveBody = (project: string, draft: HilBatchDraft) => ({
  project_id: project,
  name: draft.name.trim(),
  note: draft.note.trim(),
  tests: draft.tests.map(hilTestRef),
});
export function hilBatchMove(
  tests: HilTest[],
  index: number,
  direction: -1 | 1,
) {
  const next = index + direction;
  if (index < 0 || index >= tests.length || next < 0 || next >= tests.length)
    return tests;
  const result = [...tests];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}
export const hilBatchStatus = (status: string) =>
  (
    ({
      draft: "목록 저장됨",
      pending: "차례 대기",
      preparing: "실행 준비",
      queued: "접수됨",
      running: "실행 중",
      stopping: "중단 요청 중",
      stopped: "중단됨",
      cancel_requested: "취소 요청 중",
      completed: "실행 완료",
      failed: "실패",
      cancelled: "취소됨",
      interrupted: "실행 중단",
      claimed: "엔진 준비",
    }) as Record<string, string>
  )[status] ?? status;
export function hilBatchCounts(batch: HilBatch) {
  return {
    total: batch.items.length,
    completed: batch.items.filter((item) => item.status === "completed").length,
    failed: batch.items.filter((item) =>
      ["failed", "interrupted"].includes(hilBatchRowState(batch, item).status),
    ).length,
    uncertain: batch.items.filter(
      (item) => hilBatchRowState(batch, item).uncertain,
    ).length,
    attempts: batch.items.reduce((sum, item) => sum + item.attempts.length, 0),
  };
}
export function hilBatchTestDescription(test: HilTest) {
  const delay = test.preset_id === "input_delay";
  const start = test.parameters.start_s ?? (delay ? 3 : 3.5);
  const duration = test.parameters.duration_s ?? (delay ? 4 : 1);
  const fault = delay
    ? `${test.parameters.delay_ms} ms 지연`
    : `${duration} s 단절`;
  const profiles = test.dut_profiles
    ? `A ${test.dut_profiles.baseline.name} r${test.dut_profiles.baseline.revision} / B ${test.dut_profiles.faulted.name} r${test.dut_profiles.faulted.revision}`
    : "정상 A / 고장 B";
  return { fault, interval: `입력 ${start}–${start + duration} s`, profiles };
}
export const hilBatchMutationKey = (project: string, principal: string) =>
  `axiom.hil-batch.pending.v1:${encodeURIComponent(project)}:${encodeURIComponent(principal)}`;
export function restoreHilBatchMutation(
  raw: string | null,
  project: string,
  principal: string,
): HilBatchMutation | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as HilBatchMutation;
    const body = JSON.parse(value.body) as Record<string, unknown>;
    const validPath =
      value.action === "save"
        ? value.path === "/hil-sim/batches"
        : ["start", "cancel", "resume"].includes(value.action) &&
          new RegExp(`^/hil-sim/batches/[^/]+/${value.action}$`).test(
            value.path,
          );
    if (
      value.version !== 1 ||
      value.project !== project ||
      value.principal !== principal ||
      body.project_id !== project ||
      !value.key ||
      !validPath
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
export function hilBatchAcceptSnapshot(
  current: HilBatch | null,
  next: HilBatch,
  project: string,
): HilBatch {
  if (next.schema_version !== "hil-sim-batch/1" || next.project_id !== project)
    throw new Error("다른 프로젝트이거나 지원하지 않는 순차 실행 기록입니다.");
  if (
    next.items.some(
      (item, index) =>
        item.index !== index ||
        item.test.project_id !== project ||
        hilBatchRefKey(item.test) !== hilBatchRefKey(item.test_ref),
    )
  )
    throw new Error("순차 실행에 고정된 시험 개정이 일치하지 않습니다.");
  for (const item of next.items)
    for (const attempt of item.attempts) {
      if (attempt.execution && attempt.execution.id !== attempt.execution_id)
        throw new Error("순차 실행 시도의 원본 실행 식별자가 다릅니다.");
      if (
        attempt.result &&
        (attempt.result.schema_version !== "hil-sim-batch-result/1" ||
          attempt.result.execution_id !== attempt.execution_id ||
          attempt.status !== "completed" ||
          attempt.execution?.status !== "completed" ||
          !attempt.result.manifest_hash ||
          attempt.result.manifest_hash !== attempt.execution?.manifest_hash ||
          attempt.result.verification_status !== "not_a_test_verdict" ||
          hilBatchAttemptUncertain(attempt))
      )
        throw new Error(
          "순차 실행 요약의 원본 결과 또는 확정 상태가 다릅니다.",
        );
    }
  for (const [id, execution] of Object.entries(next.live_executions ?? {}))
    if (
      id !== execution.id ||
      !next.items.some((item) =>
        item.attempts.some((attempt) => attempt.execution_id === id),
      )
    )
      throw new Error("순차 실행 실시간 상태의 실행 식별자가 다릅니다.");
  return current?.id === next.id && current.revision > next.revision
    ? current
    : next;
}
export function hilBatchPreviewMatches(
  preview: HilBatchPreview,
  batch: HilBatch,
) {
  const remaining = batch.items.filter((item) => item.status !== "completed");
  return (
    preview.schema_version === "hil-sim-batch-preview/1" &&
    preview.batch_ref.id === batch.id &&
    preview.batch_ref.revision === batch.revision &&
    preview.batch_ref.sha256 === batch.sha256 &&
    !!preview.preview_hash &&
    preview.requires_explicit_submit === true &&
    preview.items.length === preview.job_count &&
    preview.items.length === remaining.length &&
    preview.items.length > 0 &&
    preview.items.every(
      (item, index) =>
        remaining[index]?.index === item.index &&
        hilBatchRefKey(remaining[index].test_ref) ===
          hilBatchRefKey(item.test_ref),
    )
  );
}
