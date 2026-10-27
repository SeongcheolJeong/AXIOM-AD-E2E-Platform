/** Read-only result handoff. Query values are hints until verified by the API. */
export type ExperimentKind =
  | "object_sim"
  | "planning_replay"
  | "sensor_capture"
  | "isp_compare"
  | "driving_open_loop";
export type VerificationExecution = {
  id: string;
  project_id: string;
  experiment_id: string;
  experiment_kind: ExperimentKind;
  status: string;
  fidelity: string;
  manifest_hash: string;
  input_hash: string;
  name: string;
  created_at: string;
};
export type ExecutionContextQuery = {
  execution: string;
  project: string | null;
  kind: string | null;
  fidelity: string | null;
};
export function executionContextQuery(
  hash: string,
): ExecutionContextQuery | null {
  const params = new URLSearchParams(hash.split("?")[1] ?? "");
  const execution = params.get("execution");
  if (!execution) return null;
  return {
    execution,
    project: params.get("project"),
    kind: params.get("kind"),
    fidelity: params.get("fidelity"),
  };
}
export function executionVerificationLink(execution: {
  id: string;
  project_id: string;
  experiment_kind: string;
  fidelity: string | null;
}): string {
  return `#validation?${new URLSearchParams({ "experiment-plan": "connect", execution: execution.id, project: execution.project_id, kind: execution.experiment_kind, fidelity: execution.fidelity ?? "" })}`;
}
const kinds = new Set([
  "object_sim",
  "planning_replay",
  "sensor_capture",
  "isp_compare",
  "driving_open_loop",
]);
export async function loadExecutionContext(
  query: ExecutionContextQuery,
  projects: { id: string }[],
  read: (path: string) => Promise<unknown>,
): Promise<VerificationExecution> {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(query.execution))
    throw new Error("연결할 실행 ID가 올바르지 않습니다.");
  const value = await read(
    `/experiments/executions/${encodeURIComponent(query.execution)}`,
  );
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("실행 근거를 확인할 수 없습니다.");
  const execution = value as Record<string, unknown>;
  if (
    execution.id !== query.execution ||
    typeof execution.project_id !== "string" ||
    !projects.some((project) => project.id === execution.project_id)
  )
    throw new Error("이 실행은 접근 가능한 프로젝트의 근거가 아닙니다.");
  for (const [hint, actual] of [
    [query.project, execution.project_id],
    [query.kind, execution.experiment_kind],
    [query.fidelity, execution.fidelity],
  ]) {
    if (hint !== null && hint !== actual)
      throw new Error(
        "연결 주소의 프로젝트·종류·fidelity가 실제 실행과 다릅니다. 원래 결과에서 다시 연결하세요.",
      );
  }
  if (
    !kinds.has(String(execution.experiment_kind)) ||
    execution.status !== "completed" ||
    typeof execution.fidelity !== "string" ||
    !execution.fidelity ||
    !/^[a-f0-9]{64}$/.test(String(execution.manifest_hash)) ||
    !/^[a-f0-9]{64}$/.test(String(execution.input_hash))
  )
    throw new Error("완료되고 봉인된 실행만 검증 계획에 연결할 수 있습니다.");
  return {
    ...execution,
    name:
      typeof execution.name === "string"
        ? execution.name
        : String(execution.experiment_id ?? execution.id),
    created_at:
      typeof execution.created_at === "string" ? execution.created_at : "",
  } as VerificationExecution;
}
export function contextMatchesPlan(
  execution: VerificationExecution | null,
  plan: {
    project_id: string;
    experiment_kind: string;
    allowed_fidelities: string[];
  },
): boolean {
  return Boolean(
    execution &&
    execution.project_id === plan.project_id &&
    execution.experiment_kind === plan.experiment_kind &&
    plan.allowed_fidelities.includes(execution.fidelity),
  );
}
export function executionContextRows<
  T extends {
    id: string;
    experiment_kind: string;
    fidelity: string | null;
    input_hash: string;
    manifest_hash: string | null;
  },
>(
  rows: T[],
  context: (T & { project_id: string }) | null,
  project: string,
  kind: string,
): T[] {
  const filtered = rows.filter((row) => row.experiment_kind === kind);
  if (
    !context ||
    context.project_id !== project ||
    context.experiment_kind !== kind
  )
    return filtered;
  const listed = rows.find((row) => row.id === context.id);
  if (
    listed &&
    ["experiment_kind", "fidelity", "input_hash", "manifest_hash"].some(
      (key) => listed[key as keyof T] !== context[key as keyof T],
    )
  )
    throw new Error(
      "실행 목록과 원본 실행의 근거가 다릅니다. 새로고침 후 확인하세요.",
    );
  return [context, ...filtered.filter((row) => row.id !== context.id)];
}
