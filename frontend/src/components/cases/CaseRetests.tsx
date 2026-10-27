import { useRef, useState } from "react";
import { api } from "../../api";
import { ErrorState, Loading } from "../ui";
import { DownloadLink } from "../ArtifactAccess";
import { useSensorResource } from "../sensors/SensorData";
import { useJobResource } from "../jobs/useJobResource";
import { caseHref, type CaseDetail } from "./CaseTypes";

interface Check {
  name: string;
  metric_name: string;
  operator: string;
  threshold: number;
  value: number | null;
  unit: string | null;
  status: string;
  reason: string | null;
}
interface CampaignCase {
  key: string;
  name: string;
  job_id: string;
  run_id: string | null;
  job_status: string;
  verdict: string;
  checks: Check[];
}
interface Attempt {
  campaign_id: string;
  evidence_mode: string;
  capture: { id: string; manifest_hash: string } | null;
  report: {
    status: string;
    verdict: string;
    counts: Record<string, number>;
    cases: CampaignCase[];
  };
}
interface Plan {
  id: string;
  definition_hash: string;
  definition: {
    request: { name: string; note: string; template_id: string };
    source: { case_revision: number };
    source_conditions: unknown;
    suite: { id: string; revision: number; case_count: number };
    scope: string;
  };
  attempts: Attempt[];
}
interface RetestReport {
  case_id: string;
  case_revision: number;
  case_state: string;
  scope: string;
  plans: Plan[];
  manifest_hash: string;
  download_url: string;
}
interface Criterion {
  name: string;
  metric_name: string;
  operator: string;
  threshold: number;
}
const metrics = [
  "collision_count",
  "min_ttc_s",
  "max_deceleration_mps2",
  "max_jerk_mps3",
  "distance_m",
];
const parameterLabels: Record<string, string> = {
  ego_speed_mps: "Ego speed (m/s)",
  target_speed_mps: "Target speed (m/s)",
  initial_gap_m: "Base gap (m)",
  trigger_time_s: "Trigger time (s)",
  friction: "Friction coefficient",
  response_delay_s: "Response delay (s)",
};

function useExplicitWrite() {
  const pending = useRef({ signature: "", key: "" });
  return async (path: string, value: object) => {
    const body = JSON.stringify(value),
      signature = path + body;
    if (pending.current.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    return api<CaseDetail>(path, {
      method: "POST",
      body,
      headers: { "Idempotency-Key": pending.current.key },
    });
  };
}

export function CaseRetests({ value }: { value: CaseDetail }) {
  const [creating, setCreating] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const report = useJobResource<RetestReport>(
    `/cases/${encodeURIComponent(value.id)}/retests?revision=${value.revision}`,
    2000,
  );
  const write = useExplicitWrite();
  async function act(plan: Plan, attempt?: Attempt) {
    setBusy(true);
    setError("");
    try {
      const base = `/cases/${encodeURIComponent(value.id)}/retests/${encodeURIComponent(plan.id)}/campaigns`;
      const result = await write(
        attempt
          ? `${base}/${encodeURIComponent(attempt.campaign_id)}/capture`
          : base,
        { expected_revision: value.revision, plan_hash: plan.definition_hash },
      );
      location.hash = caseHref(result.id, result.revision);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retest action failed");
    } finally {
      setBusy(false);
    }
  }
  async function cancel(attempt: Attempt) {
    setBusy(true);
    setError("");
    try {
      await api(
        `/campaigns/${encodeURIComponent(attempt.campaign_id)}/cancel`,
        { method: "POST" },
      );
      report.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancellation request failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel case-section" aria-label="Case retests">
      <div className="case-toolbar">
        <h2>Derived retests</h2>
        <button
          className="button secondary"
          onClick={() => setCreating(!creating)}
        >
          Define derived retest
        </button>
        <button className="button secondary" onClick={report.reload}>
          Refresh retest outcomes
        </button>
      </div>
      <p className="retest-scope">
        Object-reference templates only. You author speeds, gaps and criteria;
        the source log is a provenance anchor, not a reconstructed scene. No
        camera, learned model, lighting or image-size validation is performed.
      </p>
      {creating && <RetestEditor value={value} />}
      {error && (
        <p role="alert">
          {error}{" "}
          <a href={caseHref(value.id)}>
            Reload latest case before changing the request
          </a>
        </p>
      )}
      {report.loading && (
        <Loading label="Reading retest definitions and queue outcomes…" />
      )}
      {report.error && (
        <ErrorState
          message={`Retest refresh failed. ${report.data ? "Last observation shown; actions disabled. " : ""}${report.error}`}
          retry={report.reload}
        />
      )}
      {report.data && (
        <div data-testid="case-retest-report">
          {!report.data.plans.length && (
            <p>
              No retest defined. Authoring creates a Scenario and Suite, but
              does not start computation.
            </p>
          )}
          {report.data.plans.map((p) => (
            <article
              key={p.id}
              className="retest-plan"
              data-testid="retest-plan"
            >
              <h3>{p.definition.request.name}</h3>
              <p>{p.definition.request.note}</p>
              <p>
                Template {p.definition.request.template_id} · source case r
                {p.definition.source.case_revision} ·{" "}
                {p.definition.suite.case_count} planned variants ·{" "}
                {p.attempts.length} attempts
              </p>
              <code>{p.definition_hash}</code>
              <details>
                <summary>
                  Review all fixed inputs, source conditions and criteria
                </summary>
                <pre>{JSON.stringify(p.definition, null, 2)}</pre>
              </details>
              <p>
                Submitting uses the ordinary queue and consumes local compute.
                Source conditions are provenance, not conditions reproduced by
                this engine.
              </p>
              <button
                className="button primary"
                disabled={busy || !!report.error || p.attempts.length >= 8}
                onClick={() => act(p)}
              >
                Submit {p.definition.suite.case_count} variants to queue
              </button>
              {!p.attempts.length && (
                <p data-testid="retest-not-submitted">
                  Not submitted · no execution or verdict
                </p>
              )}
              {p.attempts.map((a, i) => (
                <section
                  key={a.campaign_id}
                  className="retest-attempt"
                  data-testid="retest-attempt"
                >
                  <h4>
                    Attempt {i + 1} · <code>{a.campaign_id}</code>
                  </h4>
                  <p>
                    Execution:{" "}
                    <strong data-testid="retest-execution-state">
                      {a.report.status}
                    </strong>{" "}
                    · Test verdict:{" "}
                    <strong data-testid="retest-verdict">
                      {a.report.verdict}
                    </strong>{" "}
                    · Evidence:{" "}
                    <strong data-testid="retest-evidence-mode">
                      {a.evidence_mode}
                    </strong>
                  </p>
                  <p>
                    Variants: {a.report.counts.total} · completed{" "}
                    {a.report.counts.completed} · pending{" "}
                    {a.report.counts.pending} · passed{" "}
                    {a.report.counts.passed_cases} · failed{" "}
                    {a.report.counts.failed_cases} · not evaluated{" "}
                    {a.report.counts.not_evaluated_cases}
                  </p>
                  <div className="case-table-scroll">
                    <table data-testid="retest-variants">
                      <thead>
                        <tr>
                          <th>Variant</th>
                          <th>Job state</th>
                          <th>Test verdict</th>
                          <th>Actual / required</th>
                          <th>Evidence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {a.report.cases.map((c) => (
                          <tr key={c.key}>
                            <td>{c.name}</td>
                            <td>{c.job_status}</td>
                            <td>{c.verdict}</td>
                            <td>
                              {c.checks.map((k, j) => (
                                <div key={j}>
                                  {k.name}: {k.value ?? "unavailable"}{" "}
                                  {k.unit ?? ""} / {k.operator} {k.threshold} ·{" "}
                                  {k.status}
                                  {k.reason && <small> — {k.reason}</small>}
                                </div>
                              ))}
                            </td>
                            <td>
                              {c.run_id ? (
                                <a
                                  href={`#runs?${new URLSearchParams({ run: c.run_id })}`}
                                >
                                  Inspect result {c.key}
                                </a>
                              ) : (
                                "No result yet"
                              )}
                              <br />
                              <a
                                href={`#jobs?${new URLSearchParams({ job: c.job_id })}`}
                              >
                                Inspect queue job {c.key}
                              </a>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="case-toolbar">
                    {a.report.status !== "finished" && (
                      <button
                        disabled={busy || !!report.error}
                        onClick={() => cancel(a)}
                      >
                        Request campaign cancellation
                      </button>
                    )}
                    {a.report.status === "finished" && !a.capture && (
                      <button
                        className="button secondary"
                        disabled={busy || !!report.error}
                        onClick={() => act(p, a)}
                      >
                        Capture finished outcome
                      </button>
                    )}
                    {a.capture && (
                      <DownloadLink
                        href={`/api/v1/baselines/${encodeURIComponent(a.capture.id)}/export`}
                        download
                      >
                        Download captured campaign
                      </DownloadLink>
                    )}
                  </div>
                </section>
              ))}
            </article>
          ))}
          <div className="case-toolbar">
            <DownloadLink href={report.data.download_url} download>
              Download retest report JSON
            </DownloadLink>
          </div>
          <p>
            Live exports reject changed outcomes. Capture finished evidence for
            a persistent snapshot; failures and cancellations stay visible. No
            action resolves the case or changes original perception results.
          </p>
        </div>
      )}
    </section>
  );
}

function RetestEditor({ value }: { value: CaseDetail }) {
  const catalog = useSensorResource<{
    templates: { id: string; name: string }[];
  }>("/case-retest-templates");
  const [name, setName] = useState("Derived investigation test"),
    [note, setNote] = useState("");
  const [template, setTemplate] = useState("reference-follow/1"),
    [duration, setDuration] = useState(4);
  const [parameters, setParameters] = useState<Record<string, number>>({
    ego_speed_mps: 2,
    target_speed_mps: 0,
    initial_gap_m: 20,
    trigger_time_s: 1,
    friction: 0.8,
    response_delay_s: 0.4,
  });
  const [variants, setVariants] = useState([
    { key: "base", name: "Base gap", gap: 20, seed: 42 },
    { key: "near", name: "Near gap", gap: 10, seed: 42 },
  ]);
  const [criteria, setCriteria] = useState<Criterion[]>([
    {
      name: "No contact in reference model",
      metric_name: "collision_count",
      operator: "eq",
      threshold: 0,
    },
  ]);
  const [ack, setAck] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const write = useExplicitWrite();
  const editCriterion = (i: number, change: Partial<Criterion>) => {
    setCriteria(criteria.map((c, j) => (i === j ? { ...c, ...change } : c)));
    setAck(false);
  };
  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await write(
        `/cases/${encodeURIComponent(value.id)}/retests`,
        {
          expected_revision: value.revision,
          name,
          note,
          template_id: template,
          duration_s: duration,
          parameters,
          variants: variants.map((v) => ({
            key: v.key,
            name: v.name,
            seed: v.seed,
            parameter_overrides: { initial_gap_m: v.gap },
          })),
          criteria,
          acknowledge_reference_only: ack,
        },
      );
      location.hash = caseHref(result.id, result.revision);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Definition failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="case-form retest-editor"
      role="group"
      aria-label="Derived retest definition"
    >
      {catalog.loading && <Loading />}
      {catalog.error && (
        <ErrorState message={catalog.error} retry={catalog.reload} />
      )}
      <label>
        Retest name
        <input
          aria-label="Retest name"
          value={name}
          maxLength={180}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        Hypothesis and assumptions
        <textarea
          aria-label="Retest assumptions"
          value={note}
          maxLength={2000}
          onChange={(e) => {
            setNote(e.target.value);
            setAck(false);
          }}
          placeholder="Why is this manually authored test relevant? State assumptions, not reconstructed facts."
        />
      </label>
      <label>
        Reference template
        <select
          aria-label="Retest template"
          value={template}
          onChange={(e) => {
            setTemplate(e.target.value);
            setAck(false);
          }}
        >
          {catalog.data?.templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <div className="retest-parameters">
        <label>
          Duration (s)
          <input
            aria-label="Retest duration"
            type="number"
            min={2}
            max={30}
            step="any"
            value={duration}
            onChange={(e) => {
              setDuration(Number(e.target.value));
              setAck(false);
            }}
          />
        </label>
        {Object.entries(parameterLabels).map(([k, label]) => (
          <label key={k}>
            {label}
            <input
              aria-label={`Retest ${k}`}
              type="number"
              step="any"
              value={parameters[k]}
              onChange={(e) => {
                setParameters({ ...parameters, [k]: Number(e.target.value) });
                setAck(false);
              }}
            />
          </label>
        ))}
      </div>
      <h3>Explicit gap variants</h3>
      <p>
        Each gap below overrides the base gap. Other parameters remain fixed; no
        hidden search or sweep.
      </p>
      {variants.map((v, i) => (
        <div className="retest-parameters" key={v.key}>
          <label>
            Variant {i + 1} name
            <input
              aria-label={`Retest variant ${i + 1} name`}
              value={v.name}
              onChange={(e) => {
                setVariants(
                  variants.map((x, j) =>
                    j === i ? { ...x, name: e.target.value } : x,
                  ),
                );
                setAck(false);
              }}
            />
          </label>
          <label>
            Gap (m)
            <input
              aria-label={`Retest variant ${i + 1} gap`}
              type="number"
              value={v.gap}
              min={0}
              max={500}
              step="any"
              onChange={(e) => {
                setVariants(
                  variants.map((x, j) =>
                    j === i ? { ...x, gap: Number(e.target.value) } : x,
                  ),
                );
                setAck(false);
              }}
            />
          </label>
          <label>
            Seed
            <input
              aria-label={`Retest variant ${i + 1} seed`}
              type="number"
              value={v.seed}
              min={0}
              onChange={(e) => {
                setVariants(
                  variants.map((x, j) =>
                    j === i ? { ...x, seed: Number(e.target.value) } : x,
                  ),
                );
                setAck(false);
              }}
            />
          </label>
          <button
            disabled={variants.length === 1}
            onClick={() => {
              setVariants(variants.filter((_, j) => j !== i));
              setAck(false);
            }}
          >
            Remove variant {i + 1}
          </button>
        </div>
      ))}
      <button
        disabled={variants.length >= 8}
        onClick={() => {
          setVariants([
            ...variants,
            {
              key: crypto.randomUUID(),
              name: `Variant ${variants.length + 1}`,
              gap: parameters.initial_gap_m,
              seed: 42,
            },
          ]);
          setAck(false);
        }}
      >
        Add variant
      </button>
      <h3>Explicit acceptance criteria</h3>
      <p>
        User-selected reference-test thresholds, not certification criteria.
        Every variant uses every criterion.
      </p>
      {criteria.map((c, i) => (
        <div className="retest-parameters" key={i}>
          <label>
            Criterion name
            <input
              aria-label={`Retest criterion ${i + 1} name`}
              value={c.name}
              onChange={(e) => editCriterion(i, { name: e.target.value })}
            />
          </label>
          <label>
            Metric
            <select
              aria-label={`Retest criterion ${i + 1} metric`}
              value={c.metric_name}
              onChange={(e) =>
                editCriterion(i, { metric_name: e.target.value })
              }
            >
              {metrics.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Operator
            <select
              aria-label={`Retest criterion ${i + 1} operator`}
              value={c.operator}
              onChange={(e) => editCriterion(i, { operator: e.target.value })}
            >
              {["lte", "gte", "eq"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label>
            Threshold
            <input
              aria-label={`Retest criterion ${i + 1} threshold`}
              type="number"
              min={0}
              max={1000000}
              step="any"
              value={c.threshold}
              onChange={(e) =>
                editCriterion(i, { threshold: Number(e.target.value) })
              }
            />
          </label>
          <button
            disabled={criteria.length === 1}
            onClick={() => {
              setCriteria(criteria.filter((_, j) => j !== i));
              setAck(false);
            }}
          >
            Remove criterion {i + 1}
          </button>
        </div>
      ))}
      <button
        disabled={criteria.length >= 8}
        onClick={() => {
          setCriteria([
            ...criteria,
            {
              name: `Criterion ${criteria.length + 1}`,
              metric_name: "max_jerk_mps3",
              operator: "lte",
              threshold: 10,
            },
          ]);
          setAck(false);
        }}
      >
        Add criterion
      </button>
      <label className="condition-check">
        <input
          aria-label="Acknowledge reference-only retest"
          type="checkbox"
          checked={ack}
          onChange={(e) => setAck(e.target.checked)}
        />
        I reviewed these assumptions. This is not reconstructed sensor data, E2E
        model testing or verification of source scene conditions.
      </label>
      {error && <p role="alert">{error}</p>}
      <button
        className="button primary"
        disabled={busy || !ack || !name.trim() || !note.trim() || !catalog.data}
        onClick={save}
      >
        {busy
          ? "Publishing…"
          : error
            ? "Retry retest definition"
            : "Publish retest definition"}
      </button>
    </div>
  );
}

interface Activity {
  denominator: Record<string, number>;
  rows: {
    case_id: string;
    case_revision: number;
    plan_id: string;
    name: string;
    source_case_revision: number;
    source_conditions: {
      values: Record<string, string>;
      review_status: string;
    } | null;
    planned_variants: number;
    attempts: {
      campaign_id: string;
      status: string;
      verdict: string;
      evidence_mode: string;
    }[];
    job_states: Record<string, number>;
    test_verdicts: Record<string, number>;
  }[];
  download_url: string;
  conditions_meaning: string;
}
export function CaseRetestActivity({ recording }: { recording: string }) {
  const query = new URLSearchParams(
    recording.trim() ? { recording_id: recording.trim() } : {},
  );
  const result = useSensorResource<Activity>(
    `/case-retest-activity?${query}`,
    180,
  );
  return (
    <section className="panel case-section" aria-label="Retest activity">
      <div className="case-toolbar">
        <h2>Retest activity</h2>
        <button onClick={result.reload}>Refresh retest activity</button>
      </div>
      <p>
        All plans in the recording scope, including never-submitted and repeated
        attempts. Source conditions are provenance; these tests do not fill
        sensor or ODD coverage cells.
      </p>
      {result.loading && <Loading />}
      {result.error && (
        <ErrorState message={result.error} retry={result.reload} />
      )}
      {result.data && (
        <div data-testid="retest-activity">
          <div className="condition-stats">
            {Object.entries(result.data.denominator).map(([k, v]) => (
              <div key={k}>
                <strong data-testid={`retest-activity-${k}`}>{v}</strong>
                <span>{k.replaceAll("_", " ")}</span>
              </div>
            ))}
          </div>
          <div className="case-table-scroll">
            <table data-testid="retest-activity-table">
              <thead>
                <tr>
                  <th>Case / plan</th>
                  <th>Pinned source declarations</th>
                  <th>Planned variants</th>
                  <th>Attempts</th>
                  <th>Job states</th>
                  <th>Test verdicts</th>
                </tr>
              </thead>
              <tbody>
                {result.data.rows.map((r) => (
                  <tr key={`${r.case_id}/${r.plan_id}`}>
                    <td>
                      <a href={caseHref(r.case_id, r.case_revision)}>
                        {r.name}
                      </a>
                    </td>
                    <td>
                      Source case r{r.source_case_revision} ·{" "}
                      {r.source_conditions
                        ? `${r.source_conditions.review_status}: ${Object.values(r.source_conditions.values).join(" / ")}`
                        : "unclassified"}
                    </td>
                    <td>{r.planned_variants}</td>
                    <td>
                      {r.attempts.length
                        ? r.attempts.map((a) => (
                            <div key={a.campaign_id}>
                              {a.status} / {a.verdict} / {a.evidence_mode}
                            </div>
                          ))
                        : "Not submitted"}
                    </td>
                    <td>
                      {Object.entries(r.job_states)
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ") || "No jobs"}
                    </td>
                    <td>
                      {Object.entries(r.test_verdicts)
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ") || "No verdicts"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>{result.data.conditions_meaning}</p>
          <DownloadLink href={result.data.download_url} download>
            Download retest activity JSON
          </DownloadLink>
        </div>
      )}
    </section>
  );
}
