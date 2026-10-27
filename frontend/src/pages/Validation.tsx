import VerificationPlans from "./VerificationPlans";
import ExperimentVerification from "./ExperimentVerification";
import { useEffect, useState, type FormEvent } from "react";
import {
  ArrowUpRight,
  CheckCheck,
  ClipboardCheck,
  LoaderCircle,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { api, useResource } from "../api";
import {
  categories,
  categoryLabels,
  metricLabels,
  type Requirement,
  type Scenario,
} from "../types";
import {
  Badge,
  Empty,
  ErrorState,
  Loading,
  Modal,
  PageHeading,
  SearchField,
} from "../components/ui";
function Requirements({
  scenarios,
  onSelectRun,
  notify,
}: {
  scenarios: Scenario[];
  onSelectRun: (id: string) => void;
  notify: (message: string) => void;
}) {
  const { data, loading, error, reload } =
      useResource<Requirement[]>("/requirements"),
    [query, setQuery] = useState(""),
    [status, setStatus] = useState("all"),
    [create, setCreate] = useState(false);
  const rows = (data ?? []).filter(
    (r) =>
      (status === "all" || r.status === status) &&
      `${r.name} ${r.description}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="MAKE EVERY CLAIM TRACEABLE"
        title="Validation workspace"
        description="Connect requirements to scenarios, measurable outcomes, and evidence."
        actions={
          <button className="button primary" onClick={() => setCreate(true)}>
            <Plus size={16} />
            Add requirement
          </button>
        }
      />
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorState message={error} retry={reload} />
      ) : (
        <>
          <div className="validation-stats">
            {[
              {
                label: "Requirements",
                value: data?.length ?? 0,
                icon: ClipboardCheck,
              },
              {
                label: "Passing",
                value: data?.filter((r) => r.status === "passed").length ?? 0,
                icon: ShieldCheck,
              },
              {
                label: "Needs attention",
                value: data?.filter((r) => r.status === "failed").length ?? 0,
                icon: CheckCheck,
              },
              {
                label: "Not yet tested",
                value: data?.filter((r) => r.status === "untested").length ?? 0,
                icon: ClipboardCheck,
              },
            ].map((s) => (
              <div key={s.label}>
                <s.icon size={18} />
                <strong>{s.value}</strong>
                <span>{s.label}</span>
              </div>
            ))}
          </div>
          <div className="evidence-note">
            <ShieldCheck size={18} />
            <p>
              Verdicts use the{" "}
              <strong>latest run for every linked scenario</strong>. Previous
              passing runs never hide a newer failure.
            </p>
          </div>
          <div className="filter-bar">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search requirements…"
            />
            <select
              aria-label="Filter requirement status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed</option>
              <option value="untested">Untested</option>
            </select>
          </div>
          <section className="panel">
            {!rows.length ? (
              <Empty
                title="No matching requirements"
                description="Define a measurable requirement and link it to your scenario set."
              />
            ) : (
              <div className="requirement-list">
                {rows.map((r) => (
                  <article key={r.id} className="requirement-row">
                    <div className={`requirement-icon ${r.status}`}>
                      <ShieldCheck size={20} />
                    </div>
                    <div className="requirement-main">
                      <div className="inline-title">
                        <h3>{r.name}</h3>
                        <Badge status={r.status} />
                      </div>
                      <p>{r.description}</p>
                      <div className="requirement-meta">
                        <span>
                          {metricLabels[r.metric_name] ?? r.metric_name}{" "}
                          {r.operator === "gte"
                            ? "≥"
                            : r.operator === "lte"
                              ? "≤"
                              : "="}{" "}
                          {r.threshold}
                        </span>
                        <span>
                          {r.category
                            ? (categoryLabels[r.category] ?? r.category)
                            : r.scenario_ids.length
                              ? `${r.scenario_ids.length} linked scenarios`
                              : "No scenario scope"}
                        </span>
                        <span>
                          {r.evidence_count} evidence run
                          {r.evidence_count !== 1 ? "s" : ""}
                        </span>
                      </div>
                    </div>
                    {r.last_run_id ? (
                      <button
                        className="button secondary small"
                        onClick={() => onSelectRun(r.last_run_id!)}
                      >
                        View evidence
                        <ArrowUpRight size={14} />
                      </button>
                    ) : (
                      <span className="muted small-text">
                        Awaiting execution
                      </span>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}
      {create && (
        <RequirementForm
          scenarios={scenarios}
          onClose={() => setCreate(false)}
          onSaved={() => {
            setCreate(false);
            reload();
            notify("Requirement added and coverage recalculated");
          }}
        />
      )}
    </>
  );
}
function RequirementForm({
  scenarios,
  onClose,
  onSaved,
}: {
  scenarios: Scenario[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [metric, setMetric] = useState("collision_count"),
    [operator, setOperator] = useState("lte"),
    [threshold, setThreshold] = useState(0),
    [category, setCategory] = useState(""),
    [ids, setIds] = useState<string[]>([]),
    [pending, setPending] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!category && !ids.length) {
      setError("Choose a category or link at least one scenario.");
      return;
    }
    setPending(true);
    try {
      await api("/requirements", {
        method: "POST",
        body: JSON.stringify({
          name,
          description,
          metric_name: metric,
          operator,
          threshold,
          category: category || null,
          scenario_ids: ids,
        }),
      });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      title="Add a validation requirement"
      subtitle="Define the measurable condition that your test evidence should satisfy."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <label>
            Requirement name
            <input
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. No collision in highway cut-ins"
            />
          </label>
          <label>
            Description
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <label>
            Metric
            <select value={metric} onChange={(e) => setMetric(e.target.value)}>
              {Object.entries(metricLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="form-grid">
            <label>
              Comparison
              <select
                value={operator}
                onChange={(e) => setOperator(e.target.value)}
              >
                <option value="lte">Less than or equal to (≤)</option>
                <option value="gte">Greater than or equal to (≥)</option>
                <option value="eq">Equal to (=)</option>
              </select>
            </label>
            <label>
              Threshold
              <input
                type="number"
                required
                step="any"
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            Category scope
            <select
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setIds([]);
              }}
            >
              <option value="">Choose specific scenarios below</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {categoryLabels[c]}
                </option>
              ))}
            </select>
          </label>
          <div className="form-section-title">
            Link specific scenarios <span>optional</span>
          </div>
          <p className="form-help">
            Select a category to include its scenarios, or link specific
            scenarios below. A requirement needs a scope before it can be
            evaluated.
          </p>
          <div className="scenario-checklist">
            {scenarios
              .filter((s) => !category || s.category === category)
              .map((s) => (
                <label key={s.id}>
                  <input
                    type="checkbox"
                    checked={ids.includes(s.id)}
                    onChange={(e) =>
                      setIds((previous) =>
                        e.target.checked
                          ? [...previous, s.id]
                          : previous.filter((id) => id !== s.id),
                      )
                    }
                  />
                  <span>{s.name}</span>
                </label>
              ))}
          </div>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer>
          <button type="button" className="button secondary" onClick={onClose}>
            Cancel
          </button>
          <button disabled={pending} className="button primary">
            {pending ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Plus size={15} />
            )}
            Add requirement
          </button>
        </footer>
      </form>
    </Modal>
  );
}

export default function Validation(props: {
  scenarios: Scenario[];
  onSelectRun: (id: string) => void;
  notify: (message: string) => void;
  legacyAccess?: boolean;
}) {
  const [tab, setTab] = useState<"plans" | "requirements" | "experiments">(
    props.legacyAccess === false || new URLSearchParams(location.hash.split("?")[1] ?? "").has("experiment-plan")
      ? "experiments" : new URLSearchParams(location.hash.split("?")[1] ?? "").has("plan")
      ? "plans"
      : "requirements",
  );
  useEffect(()=>{if(props.legacyAccess===false)setTab('experiments');},[props.legacyAccess]);
  return (
    <>
      <div className="filter-bar">
        <button className={`button ${tab === 'experiments' ? 'primary' : 'secondary'}`} onClick={()=>setTab('experiments')}>Experiment plans</button>
        {props.legacyAccess!==false && <>
        <button
          className={`button ${tab === "plans" ? "primary" : "secondary"}`}
          onClick={() => setTab("plans")}
        >
          Plans
        </button>
        <button
          className={`button ${tab === "requirements" ? "primary" : "secondary"}`}
          onClick={() => setTab("requirements")}
        >
          Requirements
        </button>
        </>}
      </div>
      {tab === 'experiments' ? <ExperimentVerification notify={props.notify} /> : tab === "plans" ? <VerificationPlans /> : <Requirements {...props} />}
    </>
  );
}
