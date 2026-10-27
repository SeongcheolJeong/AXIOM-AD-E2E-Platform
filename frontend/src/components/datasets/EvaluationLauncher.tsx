import { useEffect, useRef, useState, type FormEvent } from "react";
import { LoaderCircle, Play } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import {
  terminalInference,
  type InferenceDetail,
  type InferenceJobs,
} from "../perception/PerceptionTypes";
import type { DatasetDetail, DatasetSplit } from "./DatasetTypes";
import type {
  EvaluationCreate,
  EvaluationDetail,
  EvaluatorMetadata,
} from "./EvaluationTypes";

function configuration(job: InferenceDetail) {
  return JSON.stringify({
    model: job.model_snapshot.manifest_hash,
    runtime: job.runtime_id,
    version: job.runtime_version,
    seed: job.request.seed,
    score: job.request.score_threshold,
    iou: job.request.iou_threshold,
    max: job.request.max_detections,
  });
}

export default function EvaluationLauncher({
  datasets,
  initialDataset,
  evaluators,
  onClose,
  onSubmitted,
}: {
  datasets: DatasetDetail[];
  initialDataset?: DatasetDetail | null;
  evaluators: EvaluatorMetadata[];
  onClose: () => void;
  onSubmitted: (job: EvaluationDetail) => void;
}) {
  const [datasetId, setDatasetId] = useState(
    initialDataset?.id ?? datasets[0]?.id ?? "",
  );
  const [revision, setRevision] = useState(
    initialDataset?.revision ?? datasets[0]?.revision ?? 1,
  );
  const [split, setSplit] = useState<DatasetSplit>("test");
  const [name, setName] = useState("Recorded detection evaluation"),
    [note, setNote] = useState("");
  const [evaluator, setEvaluator] = useState(
    evaluators.find((item) => item.status === "available")?.id ?? "",
  );
  const [jobIds, setJobIds] = useState<string[]>([]),
    [mapping, setMapping] = useState<Record<string, string>>({});
  const [score, setScore] = useState(0),
    [iou, setIou] = useState(0.5);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [sources, setSources] = useState<{
    key: string;
    data: InferenceDetail[];
    error: string;
    loading: boolean;
  }>({ key: "", data: [], error: "", loading: false });
  const keys = useRef(new Map<string, string>());
  const [sourceRefresh, setSourceRefresh] = useState(0);
  const history = useSensorResource<DatasetDetail[]>(
    datasetId ? `/datasets/${encodeURIComponent(datasetId)}/revisions` : null,
  );
  const jobs = useSensorResource<InferenceJobs>("/perception/jobs?limit=100");
  const dataset = history.data?.find(
    (item) => item.id === datasetId && item.revision === revision,
  );
  const selectedKey = jobIds.join("|");
  useEffect(() => {
    const controller = new AbortController();
    setSources({
      key: selectedKey,
      data: [],
      error: "",
      loading: jobIds.length > 0,
    });
    if (!jobIds.length) return () => controller.abort();
    void Promise.all(
      jobIds.map((id) =>
        api<InferenceDetail>(`/perception/jobs/${encodeURIComponent(id)}`, {
          signal: controller.signal,
        }),
      ),
    )
      .then((data) => {
        if (!controller.signal.aborted)
          setSources({ key: selectedKey, data, error: "", loading: false });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setSources({
            key: selectedKey,
            data: [],
            error:
              reason instanceof Error
                ? reason.message
                : "Source jobs unavailable.",
            loading: false,
          });
      });
    return () => controller.abort();
  }, [selectedKey, sourceRefresh]);
  const selectedSources = sources.key === selectedKey ? sources.data : [];
  const first = selectedSources[0];
  const incompatible =
    first &&
    selectedSources.some((job) => configuration(job) !== configuration(first));
  const labels = first?.model_snapshot.labels ?? [];
  const members =
    dataset?.images.filter((member) => member.split === split) ?? [];
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!dataset || !members.length) {
      setError(
        "Select a fixed dataset revision and a split containing images.",
      );
      return;
    }
    if (!first || selectedSources.length !== jobIds.length || incompatible) {
      setError(
        "Choose 1–16 terminal inference jobs with the same captured model and inference configuration.",
      );
      return;
    }
    if (
      labels.some(
        (label) =>
          !Object.hasOwn(mapping, label) ||
          (mapping[label] !== "__exclude__" &&
            !dataset.labels.includes(mapping[label].slice(6))),
      )
    ) {
      setError(
        "Explicitly map or exclude every source model label. Matching names are not mapped automatically.",
      );
      return;
    }
    const body: EvaluationCreate = {
      name,
      note,
      dataset_id: datasetId,
      dataset_revision: revision,
      split,
      inference_job_ids: jobIds,
      evaluator_id: evaluator,
      class_mapping: labels.map((label) => ({
        model_label: label,
        dataset_label:
          mapping[label] === "__exclude__" ? null : mapping[label].slice(6),
      })),
      score_threshold: score,
      diagnostic_iou: iou,
    };
    const encoded = JSON.stringify(body);
    let key = keys.current.get(encoded);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(encoded, key);
    }
    setBusy(true);
    try {
      onSubmitted(
        await api<EvaluationDetail>("/detection-evaluations", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: encoded,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Evaluation submission failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Evaluate recorded detections"
      subtitle="Pin reviewed dataset labels, source prediction manifests, and an explicit class mapping."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="dataset-evaluation-modal"
    >
      <form className="dataset-evaluation-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <label>
            Evaluation name
            <input
              aria-label="Evaluation name"
              required
              maxLength={180}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="dataset-form-grid three">
            <label>
              Dataset
              <select
                aria-label="Evaluation dataset"
                required
                value={datasetId}
                onChange={(e) => {
                  const selected = datasets.find(
                    (dataset) => dataset.id === e.target.value,
                  )!;
                  setDatasetId(selected.id);
                  setRevision(selected.revision);
                  setMapping({});
                }}
              >
                {datasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Dataset revision
              <select
                aria-label="Evaluation dataset revision"
                value={revision}
                onChange={(e) => {
                  setRevision(Number(e.target.value));
                  setMapping({});
                }}
              >
                {history.data?.map((dataset) => (
                  <option key={dataset.revision} value={dataset.revision}>
                    r{dataset.revision}
                  </option>
                )) ?? <option value={revision}>r{revision}</option>}
              </select>
            </label>
            <label>
              Split
              <select
                aria-label="Evaluation dataset split"
                value={split}
                onChange={(e) => setSplit(e.target.value as DatasetSplit)}
              >
                {["test", "validation", "train", "unspecified"].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {history.error && (
            <p role="alert" className="inline-error">
              {history.error}
              <button type="button" onClick={history.reload}>
                Retry dataset revision
              </button>
            </p>
          )}
          <p className="dataset-scope">
            {members.length} images in this split ·{" "}
            {
              members.filter((member) => member.review_status === "reviewed")
                .length
            }{" "}
            reviewed. Every selected image remains in the evaluation
            denominator; aggregate metrics are withheld when evidence is
            incomplete.
          </p>
          <label>
            Evaluator
            <select
              aria-label="Detection evaluator"
              required
              value={evaluator}
              onChange={(e) => setEvaluator(e.target.value)}
            >
              <option value="">Choose an available evaluator</option>
              {evaluators.map((item) => (
                <option
                  value={item.id}
                  key={item.id}
                  disabled={item.status !== "available"}
                >
                  {item.name} · {item.version} ·{" "}
                  {item.status.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <div className="dataset-section-toolbar">
            <strong>Source inference jobs · {jobIds.length} / 16</strong>
            <button
              type="button"
              className="button secondary small"
              onClick={jobs.reload}
            >
              Refresh source jobs
            </button>
          </div>
          <div className="evaluation-source-picker">
            {jobs.loading ? (
              <p role="status">Loading terminal inference jobs…</p>
            ) : jobs.error ? (
              <p role="alert" className="inline-error">
                {jobs.error}
              </p>
            ) : (
              jobs.data?.items
                .filter((job) => terminalInference(job.status))
                .map((job) => (
                  <label key={job.id} className="evaluation-source-option">
                    <input
                      type="checkbox"
                      aria-label={`Use inference job ${job.label}`}
                      checked={jobIds.includes(job.id)}
                      disabled={!jobIds.includes(job.id) && jobIds.length >= 16}
                      onChange={(e) => {
                        setJobIds((current) =>
                          e.target.checked
                            ? [...current, job.id]
                            : current.filter((id) => id !== job.id),
                        );
                        setMapping({});
                      }}
                    />
                    <span>
                      <strong>{job.label}</strong>
                      <small>
                        {job.model_name} · r{job.model_revision} · {job.status}{" "}
                        · {job.counts.completed}/{job.counts.total} completed
                        inputs
                      </small>
                    </span>
                  </label>
                ))
            )}
          </div>
          <p className="dataset-muted">
            Up to 100 recent submissions are listed. Sources outside this split
            are disclosed. Repeated input selections across source jobs are
            rejected.
          </p>
          {sources.loading && (
            <p role="status">Checking captured source definitions…</p>
          )}
          {sources.error && (
            <p role="alert" className="inline-error">
              {sources.error}
              <button
                type="button"
                onClick={() => setSourceRefresh((value) => value + 1)}
              >
                Retry selected source jobs
              </button>
            </p>
          )}
          {incompatible && (
            <p role="alert" className="inline-error">
              Selected jobs have different model manifests or inference
              settings. Choose one consistent prediction configuration.
            </p>
          )}
          {first && !incompatible && (
            <>
              <div className="evaluation-source-context">
                <strong>Original prediction settings</strong>
                <span>
                  {first.model_name} r{first.model_revision} ·{" "}
                  {first.runtime_id}/{first.runtime_version}
                </span>
                <span>
                  Stored score ≥ {first.request.score_threshold} · NMS IoU{" "}
                  {first.request.iou_threshold} · cap{" "}
                  {first.request.max_detections} · seed {first.request.seed}
                </span>
                <small>
                  Evaluation cannot recover detections removed during inference.
                </small>
              </div>
              <div className="evaluation-class-mapping">
                <h3>Complete model → dataset class mapping</h3>
                {labels.map((label, index) => (
                  <label key={label}>
                    <span>
                      <b>{index}</b>
                      {label}
                    </span>
                    <select
                      aria-label={`Map model class ${label}`}
                      value={mapping[label] ?? ""}
                      required
                      onChange={(e) =>
                        setMapping((current) => ({
                          ...current,
                          [label]: e.target.value,
                        }))
                      }
                    >
                      <option value="">
                        Choose a dataset class or exclude
                      </option>
                      {dataset?.labels.map((target) => (
                        <option key={target} value={`label:${target}`}>
                          {target}
                        </option>
                      ))}
                      <option value="__exclude__">
                        Exclude this model class explicitly
                      </option>
                    </select>
                  </label>
                ))}
                <p>
                  Dataset classes without mapped predictions remain in the
                  denominator. Exclusion is explicit; labels are case-sensitive.
                </p>
              </div>
            </>
          )}
          <div className="dataset-form-grid two">
            <label>
              Evaluation score threshold
              <input
                aria-label="Evaluation score threshold"
                type="number"
                required
                min={0}
                max={1}
                step={0.01}
                value={score}
                onChange={(e) => setScore(Number(e.target.value))}
              />
            </label>
            <label>
              Diagnostic IoU
              <select
                aria-label="Diagnostic IoU"
                value={iou}
                onChange={(e) => setIou(Number(e.target.value))}
              >
                {Array.from({ length: 10 }, (_, index) => 0.5 + index * 0.05)
                  .map((value) => Number(value.toFixed(2)))
                  .map((value) => (
                    <option key={value} value={value}>
                      {value.toFixed(2)}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <label>
            Evaluation note
            <textarea
              aria-label="Evaluation note"
              required
              maxLength={4000}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        </fieldset>
        {error && (
          <p role="alert" className="inline-error">
            {error} Retrying identical inputs keeps the same submission key.
          </p>
        )}
        <footer className="dataset-editor-footer">
          <p>
            COCO-style metrics on stored predictions and declared labels, not an
            official benchmark submission or road performance measurement.
          </p>
          <div className="dataset-actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={
                busy ||
                !members.length ||
                !first ||
                Boolean(incompatible) ||
                sources.loading ||
                !evaluator
              }
            >
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Play size={14} />
              )}
              {busy
                ? "Submitting evaluation…"
                : error
                  ? "Retry evaluation submission"
                  : "Submit evaluation"}
            </button>
          </div>
        </footer>
      </form>
    </Modal>
  );
}
