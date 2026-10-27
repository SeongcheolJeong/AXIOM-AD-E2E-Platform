import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, authHeaders, useResource } from "../api";
import { PageHeading, ErrorState, Loading } from "../components/ui";
import { useJobResource } from "../components/jobs/useJobResource";
import type { DatasetDetail } from "../components/datasets/DatasetTypes";
import { useDatasetImage } from "../components/datasets/useDatasetImage";
import type { InferenceImage } from "../components/perception/PerceptionTypes";
import "../verification.css";

type Resource = {
  id: string;
  name?: string;
  title?: string;
  scenario_name?: string;
  revision?: number;
  definition?: { name?: string };
};
type Model = { id: string; name: string; revision: number; labels: string[] };
type Criterion = {
  metric: string;
  minimum_score: number | null;
  maximum_drop: number | null;
  minimum_images: number | null;
  minimum_objects: number | null;
  minimum_recordings: number | null;
};
type Axis = {
  name: string;
  values: string[];
  cardinality?: "single" | "multiple";
};
type Cell = {
  id: string;
  name: string;
  conditions: Record<string, string>;
  image_ids: string[];
  labels: string[];
  review_status: "draft" | "reviewed";
  reviewer: string;
  review_note: string;
  source_scope: "image";
  source_taxonomy: string;
  source_revision: string;
  mapping_note: string;
  criterion: Criterion;
};
type Evidence = {
  kind: string;
  id: string;
  revision: number | null;
  role: string;
};
type Definition = {
  name: string;
  purpose: string;
  author: string;
  change_reason: string;
  dataset_id: string;
  dataset_revision: number;
  split: string;
  models: {
    model_id: string;
    revision: number;
    class_mapping: { model_label: string; dataset_label: string | null }[];
  }[];
  runtime_id: string;
  evaluator_id: string;
  seed: number;
  score_threshold: number;
  iou_threshold: number;
  max_detections: number;
  axes: Axis[];
  cells: Cell[];
  criterion: Criterion;
  evidence: Evidence[];
  expected_revision?: number;
};
type Plan = {
  id: string;
  revision: number;
  manifest_hash: string;
  download_url: string;
  definition: Definition;
  snapshots: {
    dataset: DatasetDetail;
    models: Model[];
    evidence: {
      id: string;
      kind: string;
      snapshot: { download_url?: string };
    }[];
  };
  limitations: string[];
};
type Counts = {
  images: number;
  reviewed_images: number;
  objects: number;
  unique_recordings: number;
};
type Preview = {
  preview_hash: string;
  workload: {
    inference_jobs: number;
    reused_inference_jobs: number;
    evaluation_jobs: number;
    images_per_model: number;
  };
  cells: { id: string; name: string; counts: Counts; preparation: string }[];
};
type ResultCell = {
  id: string;
  name: string;
  counts: Counts;
  verdict: {
    status: string;
    baseline?: number | null;
    candidate?: number | null;
    delta?: number | null;
  };
  evaluation_ids: string[];
};
type Attempt = {
  id: string;
  revision: number;
  status: string;
  stage: string;
  error: string | null;
  manifest_hash: string | null;
  download_url: string | null;
  cancel_requested: boolean;
  report: {
    status: string;
    cells: ResultCell[];
    overlapping_images: Record<string, string[]>;
    unclassified_image_ids: string[];
  } | null;
};
const emptyCriterion: Criterion = {
  metric: "ap",
  minimum_score: null,
  maximum_drop: null,
  minimum_images: null,
  minimum_objects: null,
  minimum_recordings: null,
};
const initial: Definition = {
  name: "",
  purpose: "",
  author: "",
  change_reason: "Initial verification plan",
  dataset_id: "",
  dataset_revision: 1,
  split: "test",
  models: [],
  runtime_id: "onnx-cpu",
  evaluator_id: "coco-bbox",
  seed: 42,
  score_threshold: 0.35,
  iou_threshold: 0.45,
  max_detections: 100,
  axes: [
    {
      name: "lighting",
      values: ["daylight", "night", "low_light", "twilight", "unknown"],
    },
  ],
  cells: [],
  criterion: emptyCriterion,
  evidence: [],
};
const display = (value: number | null | undefined) =>
  value == null ? "—" : value.toFixed(4);
const datasetLink = (d: Definition) =>
  `#datasets?dataset=${encodeURIComponent(d.dataset_id)}&revision=${d.dataset_revision}`;

export default function VerificationPlans() {
  const plans = useResource<Plan[]>("/verification-plans");
  const keys = useRef(new Map<string, string>());
  function actionKey(signature: string) {
    let key = keys.current.get(signature);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(signature, key);
    }
    return key;
  }
  const [selected, setSelected] = useState<Plan | null>(null),
    [editing, setEditing] = useState(false),
    [preview, setPreview] = useState<Preview | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [confirmed, setConfirmed] = useState(false),
    [attemptId, setAttemptId] = useState(""),
    [retry, setRetry] = useState<string | null>(null);
  const attempts = useJobResource<Attempt[]>(
    selected ? `/verification-plans/${selected.id}/attempts` : null,
    2000,
  );
  useEffect(() => {
    if (!selected && !editing) {
      const id = new URLSearchParams(location.hash.split("?")[1] ?? "").get(
        "plan",
      );
      const p = plans.data?.find((p) => p.id === id);
      if (p) setSelected(p);
    }
  }, [plans.data, selected, editing]);
  const chosen =
    attempts.data?.find((a) => a.id === attemptId) ?? attempts.data?.at(-1);
  useEffect(() => {
    if (selected) {
      sessionStorage.setItem("axiom-verification-context", selected.id);
      location.hash = `validation?plan=${selected.id}`;
    }
    setPreview(null);
    setConfirmed(false);
    setAttemptId("");
    setRetry(null);
    setError("");
  }, [selected?.id, selected?.revision]);
  async function prepare(retryId: string | null = null) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api<Preview>(`/verification-plans/${selected.id}/preview`, {
          method: "POST",
          body: JSON.stringify({
            revision: selected.revision,
            retry_of: retryId,
          }),
        }),
      );
      setConfirmed(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function execute() {
    if (!selected || !preview || !confirmed) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<Attempt>(
        `/verification-plans/${selected.id}/attempts`,
        {
          method: "POST",
          headers: { "Idempotency-Key": actionKey(preview.preview_hash) },
          body: JSON.stringify({
            revision: selected.revision,
            expected_preview_hash: preview.preview_hash,
            confirm_trusted_models: true,
            retry_of: retry,
          }),
        },
      );
      setAttemptId(result.id);
      setPreview(null);
      setRetry(null);
      attempts.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="verification-workspace">
      <PageHeading
        eyebrow="DEFINE • EXECUTE • INVESTIGATE"
        title="Verification plans"
        description="Pin the development decision, evidence and next actions in one revision."
        actions={
          <button
            className="button primary"
            onClick={() => {
              setSelected(null);
              setEditing(true);
            }}
          >
            Create verification plan
          </button>
        }
      />
      <div className="evidence-note">
        <p>
          Execution completion, evaluation coverage and criteria are separate.
          Human conditions are declarations; these plans do not certify road
          safety.
        </p>
      </div>
      {error && <ErrorState message={error} retry={() => setError("")} />}
      {plans.error && <ErrorState message={plans.error} retry={plans.reload} />}
      {editing ? (
        <PlanEditor
          prior={selected}
          onCancel={() => setEditing(false)}
          onSaved={(p) => {
            setSelected(p);
            setEditing(false);
            plans.reload();
          }}
        />
      ) : (
        <>
          <div className="filter-bar">
            <label>
              Open plan
              <select
                aria-label="Open verification plan"
                value={selected?.id ?? ""}
                onChange={(e) => {
                  setSelected(
                    plans.data?.find((p) => p.id === e.target.value) ?? null,
                  );
                }}
              >
                <option value="">Select a plan</option>
                {plans.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.definition.name} · r{p.revision}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {plans.loading ? (
            <Loading />
          ) : !selected ? (
            <p className="muted">
              Create a plan or select an existing plan to review evidence and
              gaps.
            </p>
          ) : (
            <>
              <section className="panel verification-panel">
                <h2>
                  {selected.definition.name} · revision {selected.revision}
                </h2>
                <p>{selected.definition.purpose}</p>
                <code>{selected.manifest_hash}</code>
                <div className="filter-bar">
                  <button
                    className="button secondary"
                    onClick={() => setEditing(true)}
                  >
                    Revise plan
                  </button>
                  <a
                    className="button secondary"
                    href={datasetLink(selected.definition)}
                  >
                    Review pinned dataset
                  </a>
                  <button
                    className="button primary"
                    onClick={() => {
                      setRetry(null);
                      prepare();
                    }}
                    disabled={busy}
                  >
                    Preview A/B workload
                  </button>
                  <a className="button secondary" href="#scene-discovery">
                    Find scene data
                  </a>
                  <a
                    className="button secondary"
                    href={`#collections?source_dataset=${selected.definition.dataset_id}&source_revision=${selected.definition.dataset_revision}`}
                  >
                    Select collection
                  </a>
                  <a className="button secondary" href="#suites">
                    Scenario tests
                  </a>
                </div>
                <p className="muted">
                  Models:{" "}
                  {selected.snapshots.models
                    .map((m) => `${m.name} r${m.revision}`)
                    .join(" → ")}
                  . Criteria: {selected.definition.criterion.metric}. Author:{" "}
                  {selected.definition.author} (declared).
                </p>
                <h3>Context evidence</h3>
                {selected.snapshots.evidence.length ? (
                  selected.snapshots.evidence.map((e, i) => (
                    <a
                      key={i}
                      className="button secondary small"
                      href={
                        e.kind === "evaluation"
                          ? `#datasets?evaluation=${e.id}`
                          : e.kind === "case"
                            ? `#cases?case=${e.id}`
                            : e.kind === "collection"
                              ? `#collections?collection=${e.id}`
                              : e.kind === "run"
                                ? `#runs?run=${e.id}`
                                : e.kind === "suite" || e.kind === "campaign"
                                  ? "#suites"
                                  : datasetLink(selected.definition)
                      }
                    >
                      {e.kind}: {e.id}
                    </a>
                  ))
                ) : (
                  <p>
                    No extra context linked. Context does not satisfy detection
                    criteria.
                  </p>
                )}
              </section>
              {preview && (
                <section
                  className="panel verification-panel"
                  data-testid="verification-preview"
                >
                  <h2>Confirm bounded execution</h2>
                  <p>
                    {preview.workload.images_per_model} images per model ·{" "}
                    {preview.workload.inference_jobs} new inference jobs ·{" "}
                    {preview.workload.reused_inference_jobs} reused ·{" "}
                    {preview.workload.evaluation_jobs} evaluation jobs.
                  </p>
                  <p>
                    Retries reuse only sealed successful inference jobs with
                    matching fixed inputs and capabilities; evaluations are
                    recomputed.
                  </p>
                  {preview.cells.map((c) => (
                    <p key={c.id}>
                      {c.name}: {c.preparation} · {c.counts.reviewed_images}/
                      {c.counts.images} reviewed · {c.counts.objects} objects
                    </p>
                  ))}
                  <label>
                    <input
                      type="checkbox"
                      checked={confirmed}
                      onChange={(e) => setConfirmed(e.target.checked)}
                    />{" "}
                    I trust both models and confirm this workload
                  </label>
                  <button
                    className="button primary"
                    disabled={!confirmed || busy}
                    onClick={execute}
                  >
                    {retry ? "Submit retry" : "Submit plan A/B"}
                  </button>
                </section>
              )}
              <section className="panel verification-panel">
                <h2>Execution attempts</h2>
                {attempts.error && (
                  <ErrorState
                    message={attempts.error}
                    retry={attempts.reload}
                  />
                )}
                <label>
                  Attempt
                  <select
                    aria-label="Verification attempt"
                    value={chosen?.id ?? ""}
                    onChange={(e) => setAttemptId(e.target.value)}
                  >
                    {!attempts.data?.length && (
                      <option value="">Not executed</option>
                    )}
                    {attempts.data?.map((a) => (
                      <option key={a.id} value={a.id}>
                        r{a.revision} · {a.status} · {a.id}
                      </option>
                    ))}
                  </select>
                </label>
                {chosen ? (
                  <>
                    <p data-testid="verification-stage">
                      Execution: {chosen.status} · stage: {chosen.stage}
                      {chosen.cancel_requested
                        ? " · cancellation requested"
                        : ""}
                    </p>
                    {chosen.error && <p role="alert">{chosen.error}</p>}
                    {chosen.status === "running" ? (
                      <button
                        className="button secondary"
                        onClick={async () => {
                          try {
                            await api(
                              `/verification-attempts/${chosen.id}/cancel`,
                              { method: "POST" },
                            );
                            attempts.reload();
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        Cancel execution
                      </button>
                    ) : (
                      <button
                        className="button secondary"
                        disabled={chosen.revision !== selected.revision}
                        onClick={() => {
                          setRetry(chosen.id);
                          prepare(chosen.id);
                        }}
                      >
                        Preview retry
                      </button>
                    )}
                    {chosen.report && (
                      <>
                        <h3 data-testid="verification-verdict">
                          Plan verdict: {chosen.report.status}
                        </h3>
                        <div className="verification-table">
                          <table>
                            <thead>
                              <tr>
                                <th>Condition</th>
                                <th>Evidence counts</th>
                                <th>Verdict</th>
                                <th>A / B / Δ</th>
                                <th>Next action</th>
                              </tr>
                            </thead>
                            <tbody>
                              {chosen.report.cells.map((c) => (
                                <tr key={c.id}>
                                  <td>{c.name}</td>
                                  <td>
                                    {c.counts.reviewed_images}/{c.counts.images}{" "}
                                    reviewed · {c.counts.objects} objects ·{" "}
                                    {c.counts.unique_recordings} recordings
                                  </td>
                                  <td data-testid={`verification-cell-${c.id}`}>
                                    {c.verdict.status}
                                  </td>
                                  <td>
                                    {display(c.verdict.baseline)} /{" "}
                                    {display(c.verdict.candidate)} /{" "}
                                    {display(c.verdict.delta)}
                                  </td>
                                  <td>
                                    {c.evaluation_ids.length === 2 ? (
                                      <a
                                        href={`#datasets?baseline=${c.evaluation_ids[0]}&candidate=${c.evaluation_ids[1]}`}
                                      >
                                        Investigate A/B images
                                      </a>
                                    ) : (
                                      <a
                                        href={datasetLink(selected.definition)}
                                      >
                                        Review data and conditions
                                      </a>
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <p>
                          {Object.keys(chosen.report.overlapping_images).length}{" "}
                          overlapping images;{" "}
                          {chosen.report.unclassified_image_ids.length}{" "}
                          unclassified images. Counts are not independent
                          events.
                        </p>
                      </>
                    )}
                    {chosen.download_url && (
                      <button
                        className="button secondary"
                        onClick={async () => {
                          try {
                            const response = await fetch(chosen.download_url!, {
                              headers: authHeaders(),
                            });
                            if (!response.ok)
                              throw new Error("Export unavailable");
                            const raw = await response.arrayBuffer();
                            const hash = [
                              ...new Uint8Array(
                                await crypto.subtle.digest("SHA-256", raw),
                              ),
                            ]
                              .map((b) => b.toString(16).padStart(2, "0"))
                              .join("");
                            if (hash !== chosen.manifest_hash)
                              throw new Error("Export hash mismatch");
                            const url = URL.createObjectURL(
                              new Blob([raw], { type: "application/json" }),
                            );
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `${chosen.id}.json`;
                            a.click();
                            URL.revokeObjectURL(url);
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        Export sealed result
                      </button>
                    )}
                  </>
                ) : (
                  <p>No execution submitted.</p>
                )}
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}

function CriterionEditor({
  value,
  onChange,
}: {
  value: Criterion;
  onChange: (c: Criterion) => void;
}) {
  return (
    <div className="verification-grid">
      <label>
        Metric
        <select
          value={value.metric}
          onChange={(e) => onChange({ ...value, metric: e.target.value })}
        >
          {[
            "ap",
            "ap50",
            "ap75",
            "ap_small",
            "ap_medium",
            "ap_large",
            "precision",
            "recall",
            "f1",
          ].map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
      </label>
      {(
        [
          "minimum_score",
          "maximum_drop",
          "minimum_images",
          "minimum_objects",
          "minimum_recordings",
        ] as const
      ).map((key) => (
        <label key={key}>
          {key.replaceAll("_", " ")}
          <input
            aria-label={key.replaceAll("_", " ")}
            type="number"
            min={0}
            max={
              key === "minimum_score" || key === "maximum_drop" ? 1 : undefined
            }
            step={
              key === "minimum_score" || key === "maximum_drop" ? ".001" : "1"
            }
            value={value[key] ?? ""}
            onChange={(e) =>
              onChange({
                ...value,
                [key]: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        </label>
      ))}
    </div>
  );
}
function ImageChoice({
  image,
  checked,
  onToggle,
  label,
}: {
  image: InferenceImage;
  checked: boolean;
  onToggle: () => void;
  label: string;
}) {
  const source = useDatasetImage(image);
  return (
    <label className="verification-image">
      <input type="checkbox" checked={checked} onChange={onToggle} />
      {source.url && <img src={source.url} alt={label} />}
      <span>{label}</span>
      {source.error && <span role="alert">{source.error}</span>}
    </label>
  );
}

function PlanEditor({
  prior,
  onSaved,
  onCancel,
}: {
  prior: Plan | null;
  onSaved: (p: Plan) => void;
  onCancel: () => void;
}) {
  const saveKeys = useRef(new Map<string, string>());
  const datasets = useResource<DatasetDetail[]>("/datasets"),
    models = useResource<Model[]>("/perception/models");
  const [value, setValue] = useState<Definition>(
      prior ? structuredClone(prior.definition) : structuredClone(initial),
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [evidenceKind, setEvidenceKind] = useState("case"),
    [evidenceId, setEvidenceId] = useState(""),
    [evidenceRevision, setEvidenceRevision] = useState(1);
  const evidenceResources = useResource<Resource[] | { items: Resource[] }>(
    evidenceKind === "dataset"
      ? "/datasets"
      : evidenceKind === "collection"
        ? "/collections?limit=100"
        : evidenceKind === "case"
          ? "/cases?limit=100"
          : evidenceKind === "evaluation"
            ? "/detection-evaluations?limit=100"
            : evidenceKind === "suite"
              ? "/suites"
              : evidenceKind === "campaign"
                ? "/campaigns?limit=100"
                : "/runs",
  );
  const evidenceOptions = Array.isArray(evidenceResources.data)
    ? evidenceResources.data
    : (evidenceResources.data?.items ?? []);
  const pinnedDataset = useResource<DatasetDetail>(
    value.dataset_id
      ? `/datasets/${encodeURIComponent(value.dataset_id)}?revision=${value.dataset_revision}`
      : null,
  );
  const datasetOptions =
    datasets.data ?? (prior ? [prior.snapshots.dataset] : []);
  const dataset =
    pinnedDataset.data ??
    (prior?.snapshots.dataset.id === value.dataset_id
      ? prior.snapshots.dataset
      : null) ??
    datasetOptions.find(
      (d) => d.id === value.dataset_id && d.revision === value.dataset_revision,
    );
  const modelOptions =
    models.data ??
    (prior
      ? [...new Map(prior.snapshots.models.map((m) => [m.id, m])).values()]
      : []);
  const update = (changes: Partial<Definition>) =>
    setValue((previous) => ({ ...previous, ...changes }));
  const setCell = (i: number, changes: Partial<Cell>) =>
    update({
      cells: value.cells.map((c, n) => (n === i ? { ...c, ...changes } : c)),
    });
  function selectModel(side: number, id: string) {
    const model = models.data?.find((m) => m.id === id);
    if (!model) return;
    const refs = [...value.models];
    refs[side] = {
      model_id: model.id,
      revision: model.revision,
      class_mapping: model.labels.map((label) => ({
        model_label: label,
        dataset_label: dataset?.labels.includes(label) ? label : null,
      })),
    };
    update({ models: refs });
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (value.models.length !== 2 || !value.models[0] || !value.models[1])
        throw new Error("Select both models");
      const body = {
        ...value,
        ...(prior ? { expected_revision: prior.revision } : {}),
      };
      const signature = JSON.stringify(body);
      let key = saveKeys.current.get(signature);
      if (!key) {
        key = crypto.randomUUID();
        saveKeys.current.set(signature, key);
      }
      const result = await api<Plan>(
        prior
          ? `/verification-plans/${prior.id}/revisions`
          : "/verification-plans",
        {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: signature,
        },
      );
      onSaved(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="panel verification-panel" onSubmit={save}>
      <h2>{prior ? "Revise" : "Create"} verification plan</h2>
      {error && <p role="alert">{error}</p>}
      {[datasets.error, models.error, pinnedDataset.error]
        .filter(Boolean)
        .map((e, i) => (
          <p role="alert" key={i}>
            {e}
          </p>
        ))}
      <div className="verification-grid">
        {(["name", "purpose", "author", "change_reason"] as const).map(
          (key) => (
            <label key={key}>
              {key.replaceAll("_", " ")}
              <input
                aria-label={`Plan ${key}`}
                required
                value={value[key]}
                onChange={(e) => update({ [key]: e.target.value })}
              />
            </label>
          ),
        )}
        <label>
          Dataset
          <select
            aria-label="Plan dataset"
            required
            value={value.dataset_id}
            onChange={(e) => {
              const d = datasets.data?.find((d) => d.id === e.target.value);
              if (d)
                update({
                  dataset_id: d.id,
                  dataset_revision: d.revision,
                  cells: [],
                  models: [],
                });
            }}
          >
            <option value="">Select dataset</option>
            {datasetOptions.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Dataset revision
          <input
            type="number"
            min={1}
            value={value.dataset_revision}
            onChange={(e) =>
              update({ dataset_revision: Number(e.target.value), cells: [] })
            }
          />
        </label>
        <label>
          Split
          <select
            value={value.split}
            onChange={(e) => update({ split: e.target.value, cells: [] })}
          >
            {["train", "validation", "test", "unspecified"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        {[0, 1].map((side) => (
          <label key={side}>
            {side ? "Candidate model" : "Baseline model"}
            <select
              aria-label={side ? "Candidate model" : "Baseline model"}
              required
              disabled={!dataset || pinnedDataset.loading}
              value={value.models[side]?.model_id ?? ""}
              onChange={(e) => selectModel(side, e.target.value)}
            >
              <option value="">Select model</option>
              {modelOptions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · r{m.revision}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label>
          Inference runtime
          <input
            value={value.runtime_id}
            onChange={(e) => update({ runtime_id: e.target.value })}
          />
        </label>
        <label>
          Evaluator
          <input
            value={value.evaluator_id}
            onChange={(e) => update({ evaluator_id: e.target.value })}
          />
        </label>
      </div>
      {value.models.map((model, side) => (
        <fieldset key={side}>
          <legend>
            {side ? "Candidate" : "Baseline"} revision and class mapping
          </legend>
          <label>
            Model revision
            <input
              type="number"
              min={1}
              value={model.revision}
              onChange={(e) =>
                update({
                  models: value.models.map((m, n) =>
                    n === side ? { ...m, revision: Number(e.target.value) } : m,
                  ),
                })
              }
            />
          </label>
          {model.class_mapping.map((mapping, i) => (
            <label key={mapping.model_label}>
              {mapping.model_label}
              <select
                value={mapping.dataset_label ?? ""}
                onChange={(e) =>
                  update({
                    models: value.models.map((m, n) =>
                      n === side
                        ? {
                            ...m,
                            class_mapping: m.class_mapping.map((c, k) =>
                              k === i
                                ? {
                                    ...c,
                                    dataset_label: e.target.value || null,
                                  }
                                : c,
                            ),
                          }
                        : m,
                    ),
                  })
                }
              >
                <option value="">Explicitly exclude</option>
                {dataset?.labels.map((l) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            </label>
          ))}
        </fieldset>
      ))}
      <h3>Inference settings</h3>
      <div className="verification-grid">
        {(
          [
            "seed",
            "score_threshold",
            "iou_threshold",
            "max_detections",
          ] as const
        ).map((key) => (
          <label key={key}>
            {key.replaceAll("_", " ")}
            <input
              aria-label={key.replaceAll("_", " ")}
              type="number"
              min={0}
              step={key.includes("threshold") ? ".01" : "1"}
              value={value[key]}
              onChange={(e) => update({ [key]: Number(e.target.value) })}
            />
          </label>
        ))}
      </div>
      <h3>Overall criterion</h3>
      <p>
        All fields are required for a verdict. Counts are minimum requirements
        you define, not a statistical confidence guarantee.
      </p>
      <CriterionEditor
        value={value.criterion}
        onChange={(criterion) => update({ criterion })}
      />
      <h3>Plan condition definitions</h3>
      <p>
        Image conditions require manual review. Keep low_light and twilight
        separate. Standard COCO bbox area metrics are available; arbitrary
        object-size boundaries are unsupported.
      </p>
      {value.axes.map((axis, i) => (
        <div className="verification-grid" key={i}>
          <label>
            Axis name
            <input
              aria-label={`Axis ${i + 1} name`}
              value={axis.name}
              onChange={(e) => {
                const name = e.target.value;
                update({
                  axes: value.axes.map((a, n) =>
                    n === i ? { ...a, name } : a,
                  ),
                  cells: value.cells.map((c) => {
                    const conditions = { ...c.conditions };
                    delete conditions[axis.name];
                    conditions[name] =
                      c.conditions[axis.name] ?? axis.values[0];
                    return { ...c, conditions, review_status: "draft" };
                  }),
                });
              }}
            />
          </label>
          <label>
            Value semantics
            <select
              value={axis.cardinality ?? "single"}
              onChange={(e) =>
                update({
                  axes: value.axes.map((a, n) =>
                    n === i
                      ? {
                          ...a,
                          cardinality: e.target.value as "single" | "multiple",
                        }
                      : a,
                  ),
                  cells: value.cells.map((c) => ({
                    ...c,
                    review_status: "draft",
                  })),
                })
              }
            >
              <option value="single">One value per image</option>
              <option value="multiple">Multiple values may coexist</option>
            </select>
          </label>
          <label>
            Values (comma separated)
            <input
              aria-label={`Axis ${i + 1} values`}
              value={axis.values.join(", ")}
              onChange={(e) =>
                update({
                  axes: value.axes.map((a, n) =>
                    n === i
                      ? {
                          ...a,
                          values: e.target.value
                            .split(",")
                            .map((s) => s.trim()),
                        }
                      : a,
                  ),
                  cells: value.cells.map((c) => ({
                    ...c,
                    review_status: "draft",
                  })),
                })
              }
            />
          </label>
        </div>
      ))}
      <button
        type="button"
        className="button secondary"
        onClick={() => {
          const name = `axis_${value.axes.length + 1}`;
          update({
            axes: [...value.axes, { name, values: ["unknown"] }],
            cells: value.cells.map((c) => ({
              ...c,
              conditions: { ...c.conditions, [name]: "unknown" },
              review_status: "draft",
            })),
          });
        }}
      >
        Add condition axis
      </button>
      <h3>Required condition cells</h3>
      {value.cells.map((cell, i) => (
        <fieldset key={cell.id}>
          <legend>{cell.name}</legend>
          <div className="verification-grid">
            <label>
              Cell name
              <input
                aria-label={`Cell ${i + 1} name`}
                value={cell.name}
                onChange={(e) => setCell(i, { name: e.target.value })}
              />
            </label>
            {value.axes.map((axis) => (
              <label key={axis.name}>
                {axis.name}
                <select
                  aria-label={`Cell ${i + 1} ${axis.name}`}
                  value={cell.conditions[axis.name] ?? ""}
                  onChange={(e) =>
                    setCell(i, {
                      conditions: {
                        ...cell.conditions,
                        [axis.name]: e.target.value,
                      },
                      review_status: "draft",
                    })
                  }
                >
                  {axis.values.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            ))}
            <label>
              Reviewer
              <input
                aria-label={`Cell ${i + 1} reviewer`}
                value={cell.reviewer}
                onChange={(e) => setCell(i, { reviewer: e.target.value })}
              />
            </label>
            <label>
              Review note
              <input
                aria-label={`Cell ${i + 1} review note`}
                value={cell.review_note}
                onChange={(e) => setCell(i, { review_note: e.target.value })}
              />
            </label>
            <label>
              Source taxonomy
              <input
                value={cell.source_taxonomy}
                onChange={(e) =>
                  setCell(i, {
                    source_taxonomy: e.target.value,
                    review_status: "draft",
                  })
                }
              />
            </label>
            <label>
              Source revision
              <input
                value={cell.source_revision}
                onChange={(e) =>
                  setCell(i, {
                    source_revision: e.target.value,
                    review_status: "draft",
                  })
                }
              />
            </label>
            <label>
              Explicit mapping note
              <input
                value={cell.mapping_note}
                onChange={(e) =>
                  setCell(i, {
                    mapping_note: e.target.value,
                    review_status: "draft",
                  })
                }
              />
            </label>
          </div>
          <p>
            Select exact images. A segment tag is not automatically an image
            tag.
          </p>
          <div className="verification-images">
            {dataset?.images
              .filter((m) => m.split === value.split)
              .map((m, n) => {
                const asset = dataset.image_assets.find(
                  (a) => a.id === m.image_id,
                )!;
                return (
                  <ImageChoice
                    key={m.image_id}
                    image={asset.image}
                    label={`Image ${n + 1} · ${m.review_status}`}
                    checked={cell.image_ids.includes(m.image_id)}
                    onToggle={() =>
                      setCell(i, {
                        image_ids: cell.image_ids.includes(m.image_id)
                          ? cell.image_ids.filter((id) => id !== m.image_id)
                          : [...cell.image_ids, m.image_id],
                        review_status: "draft",
                      })
                    }
                  />
                );
              })}
          </div>
          <label>Classes (empty means all)</label>
          {dataset?.labels.map((label) => (
            <label key={label}>
              <input
                type="checkbox"
                checked={cell.labels.includes(label)}
                onChange={() =>
                  setCell(i, {
                    labels: cell.labels.includes(label)
                      ? cell.labels.filter((l) => l !== label)
                      : [...cell.labels, label],
                    review_status: "draft",
                  })
                }
              />
              {label}
            </label>
          ))}
          <CriterionEditor
            value={cell.criterion}
            onChange={(criterion) => setCell(i, { criterion })}
          />
          <button
            type="button"
            className="button secondary small"
            onClick={() =>
              setCell(i, { criterion: structuredClone(value.criterion) })
            }
          >
            Use overall criterion
          </button>
          <label>
            <input
              aria-label={`Cell ${i + 1} reviewed`}
              type="checkbox"
              checked={cell.review_status === "reviewed"}
              onChange={(e) =>
                setCell(i, {
                  review_status: e.target.checked ? "reviewed" : "draft",
                })
              }
            />{" "}
            I reviewed these image conditions
          </label>
          <button
            type="button"
            className="button secondary small"
            onClick={() =>
              update({ cells: value.cells.filter((_, n) => n !== i) })
            }
          >
            Remove cell
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        className="button secondary"
        onClick={() =>
          update({
            cells: [
              ...value.cells,
              {
                id: crypto.randomUUID(),
                name: `Condition ${value.cells.length + 1}`,
                conditions: Object.fromEntries(
                  value.axes.map((a) => [a.name, a.values[0]]),
                ),
                image_ids: [],
                labels: [],
                review_status: "draft",
                reviewer: value.author,
                review_note: "",
                source_scope: "image",
                source_taxonomy: "plan-manual",
                source_revision: "1",
                mapping_note: "",
                criterion: structuredClone(value.criterion),
              },
            ],
          })
        }
      >
        Add required condition
      </button>
      <h3>Link existing evidence</h3>
      <p>
        Exact revisions are preserved. Links provide context and cannot
        independently pass the detection criteria.
      </p>
      <div className="verification-grid">
        <label>
          Evidence kind
          <select
            value={evidenceKind}
            onChange={(e) => {
              setEvidenceKind(e.target.value);
              setEvidenceId("");
            }}
          >
            {[
              "case",
              "collection",
              "dataset",
              "evaluation",
              "run",
              "suite",
              "campaign",
            ].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label>
          Evidence resource
          <select
            aria-label="Evidence resource"
            value={evidenceId}
            onChange={(e) => {
              setEvidenceId(e.target.value);
              const selected = evidenceOptions.find(
                (r) => r.id === e.target.value,
              );
              setEvidenceRevision(selected?.revision ?? 1);
            }}
          >
            <option value="">Select existing evidence</option>
            {evidenceOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name ??
                  r.title ??
                  r.definition?.name ??
                  r.scenario_name ??
                  r.id}
                {r.revision ? ` · r${r.revision}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Evidence revision
          <input
            type="number"
            min={1}
            value={evidenceRevision}
            onChange={(e) => setEvidenceRevision(Number(e.target.value))}
          />
        </label>
      </div>
      <button
        type="button"
        className="button secondary"
        disabled={!evidenceId}
        onClick={() => {
          update({
            evidence: [
              ...value.evidence,
              {
                kind: evidenceKind,
                id: evidenceId,
                revision: ["case", "collection", "dataset", "suite"].includes(
                  evidenceKind,
                )
                  ? evidenceRevision
                  : null,
                role: "context",
              },
            ],
          });
          setEvidenceId("");
        }}
      >
        Attach evidence
      </button>
      {value.evidence.map((e, i) => (
        <p key={i}>
          {e.kind} · {e.id} · {e.revision ?? "sealed result"}{" "}
          <button
            type="button"
            onClick={() =>
              update({ evidence: value.evidence.filter((_, n) => n !== i) })
            }
          >
            Remove
          </button>
        </p>
      ))}
      <div className="filter-bar">
        <button
          className="button primary"
          disabled={
            busy || datasets.loading || models.loading || pinnedDataset.loading
          }
        >
          Save plan revision
        </button>
        <button className="button secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
