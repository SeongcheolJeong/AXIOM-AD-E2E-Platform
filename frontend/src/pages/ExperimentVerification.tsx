import { useEffect, useMemo, useRef, useState } from "react";
import { api, authHeaders, useResource } from "../api";
import { Badge, PageHeading } from "../components/ui";
import {
  contextMatchesPlan,
  executionContextQuery,
  executionContextRows,
  loadExecutionContext,
  type VerificationExecution,
} from "../experiment-verification-context";
import "../expansion.css";

type Kind =
  | "object_sim"
  | "planning_replay"
  | "sensor_capture"
  | "isp_compare"
  | "driving_open_loop";
type Metric =
  | "collision"
  | "minimum_distance"
  | "minimum_ttc"
  | "lane_departure"
  | "maximum_speed"
  | "maximum_acceleration"
  | "maximum_jerk"
  | "goal_arrival"
  | "maximum_trajectory_error"
  | "native_contact"
  | "lateral_error_p95"
  | "goal_error"
  | "final_speed"
  | "distance_travelled";
type Criterion = {
  metric: Metric;
  unit: string;
  operator: "eq" | "lt" | "lte" | "gt" | "gte";
  threshold: boolean | number;
  actor_id: string;
  required_evaluated_fraction: number;
};
type Definition = {
  project_id: string;
  name: string;
  purpose: string;
  experiment_kind: Kind;
  allowed_fidelities: string[];
  criteria: Criterion[];
  change_reason: string;
};
type Observation = {
  status: string;
  value?: number | boolean | null;
  unit?: string;
  denominator: number;
  evaluated: number;
  reason?: string | string[];
  missing_reasons?: string[];
};
type CriterionResult = {
  criterion: Criterion;
  observation: Observation;
  status: string;
  reason?: string;
};
type EvidenceResult = {
  execution_id: string;
  status: string;
  execution_status?: string;
  fidelity?: string | null;
  reason?: string;
  criteria?: CriterionResult[];
  input_hash?: string;
  manifest_hash?: string | null;
  outcome_uncertain?: boolean;
};
type Evaluation = {
  id: string;
  plan_id: string;
  plan_revision: number;
  plan_manifest_hash: string;
  manifest_hash: string;
  verdict: string;
  execution_denominator: number;
  counts: Record<string, number>;
  entries: EvidenceResult[];
  distinct_input_hashes: number;
  evidence_scope: string;
  is_safety_validation: boolean;
  created_at: string;
};
type Plan = {
  id: string;
  revision: number;
  manifest_hash: string;
  request: Definition;
  evidence_scope: string;
  evaluations?: Evaluation[];
};
type Execution = {
  id: string;
  experiment_id: string;
  name: string;
  experiment_kind: Kind;
  status: string;
  fidelity: string | null;
  input_hash: string;
  manifest_hash: string | null;
  created_at: string;
};
type Principal = {
  mode: string;
  project_id: string | null;
  role: string;
  permissions: {
    read: boolean;
    execute: boolean;
    review: boolean;
    admin: boolean;
  };
};
const kinds: Record<Kind, string> = {
  object_sim: "Object simulation",
  planning_replay: "Log / planning replay",
  sensor_capture: "Sensor capture / simulation · context",
  isp_compare: "RAW / ISP comparison · context",
  driving_open_loop: "Driving policy open loop · context",
};
const metrics: Metric[] = [
  "collision",
  "minimum_distance",
  "minimum_ttc",
  "lane_departure",
  "maximum_speed",
  "maximum_acceleration",
  "maximum_jerk",
  "goal_arrival",
  "maximum_trajectory_error",
  "native_contact",
  "lateral_error_p95",
  "goal_error",
  "final_speed",
  "distance_travelled",
];
const cityMetrics: Metric[] = [
  "native_contact",
  "lateral_error_p95",
  "goal_error",
  "final_speed",
  "distance_travelled",
];
const statuses = [
  "passed",
  "sut_failure",
  "missing_input",
  "unsupported",
  "infrastructure_failure",
  "unevaluated",
  "context_only",
];
const fidelityDefaults: Record<Kind, string> = {
  object_sim: "authored_object_kinematics",
  planning_replay:
    "authored_planar_reference, recorded_objects_reference_vehicle_feedback",
  sensor_capture: "",
  isp_compare: "",
  driving_open_loop: "",
};
function metricUnit(metric: Metric, kind: Kind): string {
  if (
    metric === "collision" ||
    metric === "native_contact" ||
    metric === "lane_departure" ||
    (metric === "goal_arrival" && kind === "planning_replay")
  )
    return "boolean";
  if (metric === "maximum_speed" || metric === "final_speed") return "m/s";
  if (metric === "maximum_acceleration") return "m/s2";
  if (metric === "maximum_jerk") return "m/s3";
  if (metric === "minimum_ttc") return "s";
  return "m";
}
function defaultCriterion(
  metric: Metric = "maximum_speed",
  kind: Kind = "object_sim",
): Criterion {
  const unit = metricUnit(metric, kind);
  return {
    metric,
    unit,
    operator:
      unit === "boolean"
        ? "eq"
        : metric.startsWith("minimum") || metric === "distance_travelled"
          ? "gte"
          : "lte",
    threshold: unit === "boolean" ? metric === "goal_arrival" : 20,
    actor_id: "ego",
    required_evaluated_fraction: 1,
  };
}
function text(value: string) {
  return value.replaceAll("_", " ");
}

export default function ExperimentVerification({
  notify,
}: { notify?: (message: string) => void } = {}) {
  const principal = useResource<Principal>("/team/whoami"),
    projects = useResource<{ items: { id: string; name: string }[] }>(
      "/projects",
    );
  const [project, setProject] = useState(""),
    [selected, setSelected] = useState(""),
    [name, setName] = useState(""),
    [purpose, setPurpose] = useState(""),
    [kind, setKind] = useState<Kind>("object_sim"),
    [fidelities, setFidelities] = useState(fidelityDefaults.object_sim),
    [criteria, setCriteria] = useState<Criterion[]>([defaultCriterion()]),
    [changeReason, setChangeReason] = useState("Initial plan"),
    [evidence, setEvidence] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState(""),
    [activeEvaluation, setActiveEvaluation] = useState<Evaluation | null>(null),
    [beforeSeq, setBeforeSeq] = useState<number | null>(null);
  const [contextQuery, setContextQuery] = useState(() =>
      executionContextQuery(location.hash),
    ),
    [linkedExecution, setLinkedExecution] =
      useState<VerificationExecution | null>(null),
    [contextError, setContextError] = useState(""),
    [contextLoading, setContextLoading] = useState(Boolean(contextQuery));
  const contextApplied = useRef(false);
  useEffect(() => {
    const changed = () => {
      setContextQuery(executionContextQuery(location.hash));
      setLinkedExecution(null);
      setContextError("");
      setEvidence([]);
      setSelected("");
      setActiveEvaluation(null);
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const saveIntent = useRef<{ signature: string; key: string } | null>(null),
    evaluationIntent = useRef<{ signature: string; key: string } | null>(null);
  const plans = useResource<{ items: Plan[] }>(
    project
      ? `/experiment-verification-plans?project_id=${encodeURIComponent(project)}`
      : null,
  );
  const detail = useResource<Plan>(
    selected
      ? `/experiment-verification-plans/${encodeURIComponent(selected)}`
      : null,
  );
  const executions = useResource<{
    items: Execution[];
    next_before_seq: number | null;
  }>(
    project
      ? `/experiments/executions?project_id=${encodeURIComponent(project)}&limit=50${beforeSeq ? `&before_seq=${beforeSeq}` : ""}`
      : null,
  );
  const canWrite = principal.data?.permissions.execute ?? false,
    canReview = principal.data?.permissions.review ?? false;
  const contextOnly = kind !== "object_sim" && kind !== "planning_replay";
  useEffect(() => {
    if (!contextQuery) {
      setContextLoading(false);
      return;
    }
    if (!projects.data) return;
    let active = true;
    contextApplied.current = false;
    setContextLoading(true);
    setContextError("");
    setLinkedExecution(null);
    setEvidence([]);
    setSelected("");
    setActiveEvaluation(null);
    loadExecutionContext(contextQuery, projects.data.items, (path) => api(path))
      .then((execution) => {
        if (!active) return;
        setLinkedExecution(execution);
        setProject(execution.project_id);
      })
      .catch((reason) => {
        if (active)
          setContextError(
            reason instanceof Error
              ? reason.message
              : "실행 근거를 확인할 수 없습니다.",
          );
      })
      .finally(() => {
        if (active) setContextLoading(false);
      });
    return () => {
      active = false;
    };
  }, [contextQuery, projects.data]);
  useEffect(() => {
    if (!contextQuery && projects.data?.items.length && !project)
      setProject(
        principal.data?.project_id &&
          projects.data.items.some(
            (item) => item.id === principal.data!.project_id,
          )
          ? principal.data.project_id
          : projects.data.items[0].id,
      );
  }, [projects.data, principal.data, project, contextQuery]);
  useEffect(() => {
    setSelected("");
    setEvidence([]);
    setBeforeSeq(null);
    setActiveEvaluation(null);
    setError("");
  }, [project]);
  useEffect(() => {
    if (
      !linkedExecution ||
      linkedExecution.project_id !== project ||
      contextApplied.current
    )
      return;
    contextApplied.current = true;
    setKind(linkedExecution.experiment_kind);
    setFidelities(linkedExecution.fidelity);
    setEvidence([linkedExecution.id]);
    setName("");
    setPurpose("");
    setChangeReason("Initial plan");
    setCriteria(
      linkedExecution.fidelity === "native_carla_object_physics"
        ? [
            {
              ...defaultCriterion("native_contact"),
              actor_id: "all",
              threshold: false,
            },
            { ...defaultCriterion("lateral_error_p95"), threshold: 0.5 },
            { ...defaultCriterion("goal_error"), threshold: 2 },
            { ...defaultCriterion("final_speed"), threshold: 0.2 },
            { ...defaultCriterion("distance_travelled"), threshold: 2 },
          ]
        : [defaultCriterion("maximum_speed", linkedExecution.experiment_kind)],
    );
  }, [linkedExecution, project]);
  useEffect(() => {
    if (
      !detail.data ||
      detail.data.id !== selected ||
      detail.data.request.project_id !== project
    )
      return;
    const value = detail.data.request;
    setName(value.name);
    setPurpose(value.purpose);
    setKind(value.experiment_kind);
    setFidelities(value.allowed_fidelities.join(", "));
    setCriteria(value.criteria);
    setChangeReason("");
    setEvidence(
      contextMatchesPlan(linkedExecution, value) ? [linkedExecution!.id] : [],
    );
    setActiveEvaluation(detail.data.evaluations?.at(-1) ?? null);
  }, [detail.data, selected, project, linkedExecution]);
  const contextRows = useMemo(() => {
    try {
      return {
        items: executionContextRows<Execution>(
          executions.data?.items ?? [],
          linkedExecution,
          project,
          kind,
        ),
        error: "",
      };
    } catch (reason) {
      return {
        items: [] as Execution[],
        error:
          reason instanceof Error
            ? reason.message
            : "실행 근거 목록이 일치하지 않습니다.",
      };
    }
  }, [executions.data, linkedExecution, project, kind]);
  const relevantExecutions = contextRows.items;
  const selectedPlan =
    detail.data?.id === selected && detail.data.request.project_id === project
      ? detail.data
      : null;
  const activeLink =
    linkedExecution?.project_id === project ? linkedExecution : null;
  const observedFidelities = [
    ...new Set(
      relevantExecutions
        .map((item) => item.fidelity)
        .filter((value): value is string => Boolean(value)),
    ),
  ];
  function clearEditor() {
    setSelected("");
    setName("");
    setPurpose("");
    setKind("object_sim");
    setFidelities(fidelityDefaults.object_sim);
    setCriteria([defaultCriterion()]);
    setChangeReason("Initial plan");
    setEvidence([]);
    setActiveEvaluation(null);
    setSuccess("");
    setError("");
  }
  function changeKind(value: Kind) {
    setKind(value);
    setFidelities(fidelityDefaults[value]);
    setCriteria(
      value === "object_sim" || value === "planning_replay"
        ? [defaultCriterion("maximum_speed", value)]
        : [],
    );
    setEvidence([]);
  }
  function cityProfile() {
    setFidelities("native_carla_object_physics");
    setCriteria([
      {
        ...defaultCriterion("native_contact"),
        actor_id: "all",
        threshold: false,
      },
      { ...defaultCriterion("lateral_error_p95"), threshold: 0.5 },
      { ...defaultCriterion("goal_error"), threshold: 2 },
      { ...defaultCriterion("final_speed"), threshold: 0.2 },
      { ...defaultCriterion("distance_travelled"), threshold: 2 },
    ]);
    setEvidence([]);
  }
  function updateCriterion(index: number, value: Partial<Criterion>) {
    setCriteria((items) =>
      items.map((item, position) =>
        position === index ? { ...item, ...value } : item,
      ),
    );
  }
  async function save() {
    if (
      contextLoading ||
      contextError ||
      contextRows.error ||
      (selected && !selectedPlan) ||
      !projects.data?.items.some((item) => item.id === project)
    )
      return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const scopes = fidelities
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
      if (!scopes.length)
        throw new Error(
          "Select or enter at least one exact evidence fidelity.",
        );
      if (!name.trim() || !purpose.trim() || !changeReason.trim())
        throw new Error("Name, purpose and change reason are required.");
      const body: Definition = {
        project_id: project,
        name,
        purpose,
        experiment_kind: kind,
        allowed_fidelities: scopes,
        criteria: contextOnly ? [] : criteria,
        change_reason: changeReason,
      };
      const path = selected
          ? `/experiment-verification-plans/${encodeURIComponent(selected)}/revisions`
          : "/experiment-verification-plans",
        signature = JSON.stringify({ path, body });
      if (saveIntent.current?.signature !== signature)
        saveIntent.current = { signature, key: crypto.randomUUID() };
      const saved = await api<Plan>(path, {
        method: "POST",
        headers: { "Idempotency-Key": saveIntent.current.key },
        body: JSON.stringify(body),
      });
      saveIntent.current = null;
      setSelected(saved.id);
      plans.reload();
      detail.reload();
      setSuccess(
        `Plan revision ${saved.revision} saved. Previous revisions and assessments remain unchanged.`,
      );
      notify?.("Experiment verification plan saved");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to save plan",
      );
    } finally {
      setBusy(false);
    }
  }
  async function assess() {
    if (
      !selectedPlan ||
      !evidence.length ||
      contextLoading ||
      contextError ||
      contextRows.error
    )
      return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const path = `/experiment-verification-plans/${encodeURIComponent(selectedPlan.id)}/evaluations`,
        body = {
          plan_revision: selectedPlan.revision,
          execution_ids: [...evidence].sort(),
        },
        signature = JSON.stringify({ path, body });
      if (evaluationIntent.current?.signature !== signature)
        evaluationIntent.current = { signature, key: crypto.randomUUID() };
      const result = await api<Evaluation>(path, {
        method: "POST",
        headers: { "Idempotency-Key": evaluationIntent.current.key },
        body: JSON.stringify(body),
      });
      evaluationIntent.current = null;
      setActiveEvaluation(result);
      detail.reload();
      setSuccess(
        `Assessment saved with ${result.execution_denominator} requested executions. ${text(result.verdict)} within this plan scope.`,
      );
      notify?.("Fixed execution evidence assessed");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to assess evidence",
      );
    } finally {
      setBusy(false);
    }
  }
  async function download(result: Evaluation) {
    setError("");
    try {
      const path = `/api/v1/experiment-verification-plans/${encodeURIComponent(result.plan_id)}/evaluations/${encodeURIComponent(result.id)}/export`;
      const response = await fetch(path, { headers: authHeaders() });
      if (!response.ok)
        throw new Error(`Receipt download failed (${response.status}).`);
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 4 * 1024 * 1024)
        throw new Error("Receipt exceeds its manifest byte bound.");
      const hash = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      )
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      if (hash !== result.manifest_hash)
        throw new Error("Receipt SHA-256 differs from its pinned assessment.");
      const url = URL.createObjectURL(
        new Blob([bytes], { type: "application/json" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${result.id}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to download receipt",
      );
    }
  }
  const topError =
    contextError ||
    contextRows.error ||
    error ||
    principal.error ||
    projects.error ||
    plans.error ||
    detail.error ||
    executions.error;
  return (
    <div className="expansion-workspace">
      <PageHeading
        eyebrow="FIX THE PURPOSE · CONNECT THE EVIDENCE"
        title="Experiment verification"
        description="Define bounded object or planning criteria, retain execution gaps, and keep sensor, ISP and driving evidence within its accepted scope."
        actions={
          <button
            className="button secondary"
            onClick={() => {
              plans.reload();
              detail.reload();
              executions.reload();
              principal.reload();
            }}
          >
            Refresh evidence
          </button>
        }
      />
      <div className="expansion-note warning">
        A completed execution is separate from a criterion pass. These plans
        evaluate supported authored, reference, or native CARLA ground-truth
        metrics within their exact fidelity. Native contact and sampled
        bounding-box collision are separate observations. Sensor, ISP and
        driving receipts remain context evidence; this workspace does not grant
        physical sensor or road safety approval.
      </div>
      <div className="expansion-actions">
        <label className="expansion-field">
          Project
          <select
            aria-label="Verification project"
            disabled={contextLoading}
            value={project}
            onChange={(event) => setProject(event.target.value)}
          >
            <option value="" disabled>
              Choose an accessible project
            </option>
            {projects.data?.items.map((item) => (
              <option value={item.id} key={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <Badge
          status="context_only"
          label={`${principal.data?.role ?? "Loading access"} · project scope`}
        />
        {principal.data?.mode === "project" && (
          <span className="expansion-clock">
            Legacy resource APIs require the operator profile.
          </span>
        )}
      </div>
      {topError && (
        <p role="alert" className="expansion-error">
          {topError}
        </p>
      )}
      {success && (
        <p role="status" className="expansion-success">
          {success}
        </p>
      )}
      {contextLoading && (
        <p role="status" className="expansion-note">
          원본 실행과 접근 가능한 프로젝트를 확인하는 중…
        </p>
      )}
      {activeLink && (
        <section
          className="expansion-note"
          aria-label="Verified execution handoff"
        >
          <strong>실행 근거 연결 · {activeLink.id}</strong>
          <p>
            원본 실행에서 확인한 프로젝트 · {activeLink.project_id} /{" "}
            {kinds[activeLink.experiment_kind]} / {activeLink.fidelity}. 기존
            계획을 선택해 범위와 기준을 검토한 뒤 평가하세요. 이 화면을 여는
            것만으로 계획이나 평가가 저장되지는 않습니다.
          </p>
          {selectedPlan &&
            !contextMatchesPlan(activeLink, selectedPlan.request) && (
              <p role="status">
                선택한 계획의 종류 또는 허용 fidelity가 이 실행과 다릅니다. 연결
                실행의 자동 선택을 해제했습니다.
              </p>
            )}
          <button
            className="button secondary"
            onClick={() => {
              setContextQuery(null);
              setLinkedExecution(null);
              setContextError("");
              setContextLoading(false);
              setEvidence([]);
            }}
          >
            연결 선택 해제
          </button>
        </section>
      )}
      {contextError && (
        <button
          className="button secondary"
          onClick={() => {
            setContextQuery(null);
            setLinkedExecution(null);
            setContextError("");
            setContextLoading(false);
            setEvidence([]);
          }}
        >
          잘못된 연결을 닫고 계획 열기
        </button>
      )}
      <div className="expansion-grid">
        <section className="expansion-panel">
          <div className="expansion-actions">
            <h2>
              {activeLink ? "기존 계획 선택 · 근거 연결" : "Plan authoring"}
            </h2>
            <button className="button secondary" onClick={clearEditor}>
              New plan
            </button>
          </div>
          <div
            className="expansion-list"
            aria-label="Saved experiment verification plans"
          >
            {plans.data?.items.map((item) => (
              <button
                key={item.id}
                className={selected === item.id ? "selected" : ""}
                onClick={() => {
                  setSelected(item.id);
                  setActiveEvaluation(null);
                  setError("");
                  setSuccess("");
                }}
              >
                <strong>{item.request.name}</strong>
                <small>
                  r{item.revision} · {kinds[item.request.experiment_kind]} ·{" "}
                  {text(item.evidence_scope)}
                </small>
              </button>
            ))}
            {!plans.loading && !plans.data?.items.length && (
              <p className="expansion-note">
                No plans in this project. Define the purpose, supported fidelity
                and criteria below.
              </p>
            )}
          </div>
          <div className="expansion-fields">
            <label className="expansion-field wide">
              Plan name
              <input
                aria-label="Experiment plan name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={!canWrite || busy}
              />
            </label>
            <label className="expansion-field wide">
              Purpose
              <textarea
                aria-label="Experiment plan purpose"
                value={purpose}
                onChange={(event) => setPurpose(event.target.value)}
                disabled={!canWrite || busy}
                style={{ minHeight: 90, fontFamily: "inherit" }}
              />
            </label>
            <label className="expansion-field wide">
              Evidence type
              <select
                aria-label="Experiment plan evidence type"
                value={kind}
                onChange={(event) => changeKind(event.target.value as Kind)}
                disabled={!canWrite || busy}
              >
                {Object.entries(kinds).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="expansion-field wide">
              Allowed fidelity scopes · exact identifiers separated by commas
              <input
                aria-label="Allowed experiment fidelities"
                value={fidelities}
                onChange={(event) => setFidelities(event.target.value)}
                disabled={!canWrite || busy}
              />
            </label>
            {kind === "object_sim" && (
              <div className="expansion-field wide">
                <button
                  className="button secondary"
                  disabled={!canWrite || busy}
                  onClick={cityProfile}
                >
                  CARLA native route criteria
                </button>
                <small>
                  All-actor native contact = false · ego lateral P95 ≤ 0.5m ·
                  goal error ≤ 2m · final speed ≤ 0.2m/s · travelled distance ≥
                  2m. Review these fixed criteria before saving.
                </small>
              </div>
            )}
            {observedFidelities.length > 0 && (
              <div className="expansion-field wide">
                <span>Observed in retained results</span>
                <div className="expansion-actions">
                  {observedFidelities.map((value) => (
                    <button
                      className="button secondary"
                      key={value}
                      disabled={!canWrite || busy}
                      onClick={() =>
                        setFidelities((previous) =>
                          [
                            ...new Set([
                              ...previous
                                .split(",")
                                .map((item) => item.trim())
                                .filter(Boolean),
                              value,
                            ]),
                          ].join(", "),
                        )
                      }
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          {contextOnly ? (
            <p className="expansion-note">
              Context plan: execution and provenance can be linked. No accepted
              physical or safety metric evaluator is available for this evidence
              type, so assessment cannot pass.
            </p>
          ) : (
            <div
              className="expansion-list"
              aria-label="Typed experiment criteria"
            >
              {criteria.map((criterion, index) => (
                <div
                  className="expansion-panel"
                  style={{ padding: 12 }}
                  key={index}
                >
                  <div className="expansion-fields">
                    <label className="expansion-field">
                      Metric
                      <select
                        aria-label={`Criterion ${index + 1} metric`}
                        value={criterion.metric}
                        disabled={!canWrite || busy}
                        onChange={(event) =>
                          updateCriterion(
                            index,
                            defaultCriterion(
                              event.target.value as Metric,
                              kind,
                            ),
                          )
                        }
                      >
                        {metrics
                          .filter((metric) =>
                            kind === "object_sim"
                              ? metric !== "maximum_trajectory_error"
                              : !cityMetrics.includes(metric),
                          )
                          .map((metric) => (
                            <option value={metric} key={metric}>
                              {text(metric)}
                              {metric === "native_contact"
                                ? " · native engine"
                                : metric === "collision"
                                  ? " · sampled geometry"
                                  : ""}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label className="expansion-field">
                      Actor
                      <input
                        aria-label={`Criterion ${index + 1} actor`}
                        value={criterion.actor_id}
                        disabled={
                          !canWrite || busy || kind === "planning_replay"
                        }
                        onChange={(event) =>
                          updateCriterion(index, {
                            actor_id: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label className="expansion-field">
                      Operator
                      <select
                        aria-label={`Criterion ${index + 1} operator`}
                        value={criterion.operator}
                        disabled={
                          !canWrite || busy || criterion.unit === "boolean"
                        }
                        onChange={(event) =>
                          updateCriterion(index, {
                            operator: event.target
                              .value as Criterion["operator"],
                          })
                        }
                      >
                        {["eq", "lte", "gte", "lt", "gt"].map((operator) => (
                          <option value={operator} key={operator}>
                            {operator}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="expansion-field">
                      Threshold · {criterion.unit}
                      {criterion.unit === "boolean" ? (
                        <select
                          aria-label={`Criterion ${index + 1} threshold`}
                          value={String(criterion.threshold)}
                          disabled={!canWrite || busy}
                          onChange={(event) =>
                            updateCriterion(index, {
                              threshold: event.target.value === "true",
                            })
                          }
                        >
                          <option value="false">false</option>
                          <option value="true">true</option>
                        </select>
                      ) : (
                        <input
                          aria-label={`Criterion ${index + 1} threshold`}
                          type="number"
                          step="any"
                          value={Number(criterion.threshold)}
                          disabled={!canWrite || busy}
                          onChange={(event) =>
                            updateCriterion(index, {
                              threshold: Number(event.target.value),
                            })
                          }
                        />
                      )}
                    </label>
                    <label className="expansion-field">
                      Required evaluated fraction
                      <input
                        aria-label={`Criterion ${index + 1} evaluated fraction`}
                        type="number"
                        min="0.001"
                        max="1"
                        step="0.01"
                        value={criterion.required_evaluated_fraction}
                        disabled={!canWrite || busy}
                        onChange={(event) =>
                          updateCriterion(index, {
                            required_evaluated_fraction: Number(
                              event.target.value,
                            ),
                          })
                        }
                      />
                    </label>
                    <button
                      className="button secondary"
                      disabled={!canWrite || busy}
                      onClick={() =>
                        setCriteria((values) =>
                          values.filter((_, position) => position !== index),
                        )
                      }
                    >
                      Remove criterion
                    </button>
                  </div>
                </div>
              ))}
              <button
                className="button secondary"
                disabled={!canWrite || busy || criteria.length >= 32}
                onClick={() =>
                  setCriteria((values) => [
                    ...values,
                    defaultCriterion("collision", kind),
                  ])
                }
              >
                Add criterion
              </button>
            </div>
          )}
          <label className="expansion-field">
            Change reason
            <input
              aria-label="Experiment plan change reason"
              value={changeReason}
              onChange={(event) => setChangeReason(event.target.value)}
              disabled={!canWrite || busy}
            />
          </label>
          <button
            className="button primary"
            disabled={
              !canWrite ||
              busy ||
              !project ||
              contextLoading ||
              Boolean(contextError) ||
              Boolean(contextRows.error)
            }
            onClick={save}
          >
            {busy
              ? "Saving…"
              : selected
                ? "Save new plan revision"
                : "Save verification plan"}
          </button>
          {!canWrite && (
            <p className="expansion-note">
              Engineer or Admin access is required to write plans. Reviewer
              access can assess saved plans.
            </p>
          )}
          {selectedPlan && (
            <p className="expansion-clock">
              Fixed plan r{selectedPlan.revision} · SHA-256{" "}
              {selectedPlan.manifest_hash}
            </p>
          )}
        </section>
        <section className="expansion-panel">
          <h2>Execution evidence · original denominator</h2>
          <p className="expansion-note">
            Select fixed executions of this evidence type. Pending, failed and
            unsupported evidence keeps its own status. Full planning traces are
            evaluated rather than the bounded visualization preview.
          </p>
          <div className="expansion-actions">
            <button
              className="button secondary"
              onClick={() =>
                setEvidence(relevantExecutions.map((item) => item.id))
              }
            >
              Select visible executions
            </button>
            <button
              className="button secondary"
              onClick={() => setEvidence([])}
            >
              Clear selection
            </button>
          </div>
          <div style={{ maxHeight: 400, overflow: "auto" }}>
            <table className="expansion-table">
              <thead>
                <tr>
                  <th>Select</th>
                  <th>Experiment / execution</th>
                  <th>Execution state</th>
                </tr>
              </thead>
              <tbody>
                {relevantExecutions.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select execution ${item.name} ${item.id}`}
                        checked={evidence.includes(item.id)}
                        onChange={(event) =>
                          setEvidence((values) =>
                            event.target.checked
                              ? [...values, item.id]
                              : values.filter((value) => value !== item.id),
                          )
                        }
                      />
                    </td>
                    <td>
                      <strong>{item.name}</strong>
                      <br />
                      <small>{item.id}</small>
                      <br />
                      <small>{item.fidelity ?? "No completed fidelity"}</small>
                    </td>
                    <td>
                      <Badge status={item.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!executions.loading && !relevantExecutions.length && (
              <p className="expansion-note">
                No executions of this type on this page. Create and run an
                Experiment, then refresh evidence.
              </p>
            )}
          </div>
          <div className="expansion-actions">
            {beforeSeq && (
              <button
                className="button secondary"
                onClick={() => setBeforeSeq(null)}
              >
                Latest executions
              </button>
            )}
            {executions.data?.next_before_seq && (
              <button
                className="button secondary"
                onClick={() => setBeforeSeq(executions.data!.next_before_seq)}
              >
                Older executions
              </button>
            )}
            <span className="expansion-clock">
              {evidence.length} fixed execution IDs selected · pages contain up
              to 50 executions
            </span>
          </div>
          <button
            className="button primary"
            disabled={
              !canReview ||
              busy ||
              contextLoading ||
              Boolean(contextError) ||
              Boolean(contextRows.error) ||
              !selectedPlan ||
              !evidence.length ||
              evidence.length > 128
            }
            onClick={assess}
          >
            Evaluate selected evidence
          </button>
          {!canReview && (
            <p className="expansion-note">
              Reviewer or Admin access is required to capture an assessment.
            </p>
          )}
          {selectedPlan?.evaluations && selectedPlan.evaluations.length > 0 && (
            <label className="expansion-field">
              Saved immutable assessments
              <select
                aria-label="Saved experiment assessments"
                value={activeEvaluation?.id ?? ""}
                onChange={(event) =>
                  setActiveEvaluation(
                    selectedPlan!.evaluations!.find(
                      (item) => item.id === event.target.value,
                    ) ?? null,
                  )
                }
              >
                {selectedPlan.evaluations.map((item) => (
                  <option key={item.id} value={item.id}>
                    r{item.plan_revision} · {text(item.verdict)} ·{" "}
                    {item.execution_denominator} executions · {item.created_at}
                  </option>
                ))}
              </select>
            </label>
          )}
          {activeEvaluation && (
            <>
              <div className="expansion-actions">
                <Badge status={activeEvaluation.verdict} />
                <span>
                  Plan revision {activeEvaluation.plan_revision} ·{" "}
                  {text(activeEvaluation.evidence_scope)}
                </span>
                <button
                  className="button secondary"
                  onClick={() => download(activeEvaluation)}
                >
                  Download assessment receipt
                </button>
              </div>
              <div className="expansion-stat-grid">
                <div className="expansion-stat">
                  <strong>{activeEvaluation.execution_denominator}</strong>
                  <span>Requested executions</span>
                </div>
                {statuses.map((status) => (
                  <div className="expansion-stat" key={status}>
                    <strong>{activeEvaluation.counts[status] ?? 0}</strong>
                    <span>{text(status)}</span>
                  </div>
                ))}
              </div>
              <p className="expansion-note">
                {activeEvaluation.distinct_input_hashes} distinct frozen input
                hashes. Repeated executions and different seeds are not
                independent scene diversity. A plan pass applies to its stated
                observer criteria; no road safety approval is established.
              </p>
              <table className="expansion-table">
                <thead>
                  <tr>
                    <th>Evidence</th>
                    <th>Plan state / next gap</th>
                  </tr>
                </thead>
                <tbody>
                  {activeEvaluation.entries.map((entry) => (
                    <tr key={entry.execution_id}>
                      <td>
                        <a
                          href={`#${kind === "object_sim" ? "object-sim" : "experiments"}?project=${encodeURIComponent(project)}&execution=${encodeURIComponent(entry.execution_id)}`}
                        >
                          {entry.execution_id}
                        </a>
                        <br />
                        <small>
                          {entry.fidelity ?? "No accepted fidelity"}
                        </small>
                        {entry.outcome_uncertain && (
                          <p>External outcome remains uncertain</p>
                        )}
                      </td>
                      <td>
                        <Badge status={entry.status} />
                        {entry.reason && <p>{text(entry.reason)}</p>}
                        {entry.criteria?.map((item) => (
                          <div
                            key={`${item.criterion.metric}-${item.criterion.actor_id}`}
                            style={{ marginTop: 12 }}
                          >
                            <strong>
                              {text(item.criterion.metric)} ·{" "}
                              {item.criterion.actor_id}
                            </strong>
                            <p>
                              {item.observation.value === undefined ||
                              item.observation.value === null
                                ? "No valid metric"
                                : String(item.observation.value)}{" "}
                              {item.observation.unit ?? ""} ·{" "}
                              {item.criterion.operator}{" "}
                              {String(item.criterion.threshold)}{" "}
                              {item.criterion.unit}
                            </p>
                            <Badge status={item.status} />
                            <p>
                              Evaluated {item.observation.evaluated} /{" "}
                              {item.observation.denominator} · required fraction{" "}
                              {item.criterion.required_evaluated_fraction}
                            </p>
                            {(item.reason ||
                              item.observation.reason ||
                              item.observation.missing_reasons?.length) && (
                              <small>
                                {item.reason ??
                                  (Array.isArray(item.observation.reason)
                                    ? item.observation.reason.join(", ")
                                    : item.observation.reason) ??
                                  item.observation.missing_reasons?.join(", ")}
                              </small>
                            )}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="expansion-clock">
                Assessment SHA-256 {activeEvaluation.manifest_hash}
                <br />
                Plan SHA-256 {activeEvaluation.plan_manifest_hash}
              </p>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
