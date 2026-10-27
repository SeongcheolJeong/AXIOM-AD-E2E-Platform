import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Database,
  ListChecks,
  Plus,
  RefreshCw,
} from "lucide-react";
import { formatDate } from "../api";
import { Empty, ErrorState, Loading, PageHeading } from "../components/ui";
import { useSensorResource } from "../components/sensors/SensorData";
import { useJobResource } from "../components/jobs/useJobResource";
import JobStatusBadge from "../components/jobs/JobStatusBadge";
import type { InferenceSource } from "../components/perception/PerceptionTypes";
import DatasetEditor from "../components/datasets/DatasetEditor";
import DatasetDetails from "../components/datasets/DatasetDetails";
import EvaluationLauncher from "../components/datasets/EvaluationLauncher";
import EvaluationInspector from "../components/datasets/EvaluationInspector";
import EvaluationComparison from "../components/datasets/EvaluationComparison";
import type { DatasetDetail } from "../components/datasets/DatasetTypes";
import type {
  EvaluationDetail,
  EvaluationPage,
  EvaluatorMetadata,
} from "../components/datasets/EvaluationTypes";
import "../datasets.css";

export default function Datasets({
  source,
  onSourceConsumed,
}: {
  source?: InferenceSource | null;
  onSourceConsumed?: () => void;
}) {
  const targetEvaluation = new URLSearchParams(location.hash.split("?")[1] ?? "").get("evaluation");
  const comparisonTarget = new URLSearchParams(location.hash.split("?")[1] ?? "").has("baseline");
  const [tab, setTab] = useState<"datasets" | "evaluations" | "comparison">(targetEvaluation ? "evaluations" : comparisonTarget ? "comparison" : "datasets");
  const dataTarget = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const [id, setId] = useState(dataTarget.get("dataset") ?? ""),
    [revision, setRevision] = useState(Number(dataTarget.get("revision") ?? 1));
  const [editor, setEditor] = useState<DatasetDetail | "new" | null>(
    source ? "new" : null,
  );
  const [pendingSource, setPendingSource] = useState(source ?? null);
  const [launch, setLaunch] = useState<DatasetDetail | "new" | null>(null);
  const [jobId, setJobId] = useState(targetEvaluation ?? ""),
    [status, setStatus] = useState(""),
    [before, setBefore] = useState<number | null>(null);
  const datasets = useSensorResource<DatasetDetail[]>("/datasets");
  const history = useSensorResource<DatasetDetail[]>(
    id ? `/datasets/${encodeURIComponent(id)}/revisions` : null,
  );
  const evaluators = useSensorResource<EvaluatorMetadata[]>(
    "/detection-evaluators",
  );
  const query = new URLSearchParams({ limit: "100" });
  if (status) query.set("status", status);
  if (before !== null) query.set("before_seq", String(before));
  const jobs = useJobResource<EvaluationPage>(
    `/detection-evaluations?${query}`,
    3000,
  );
  const detail = useJobResource<EvaluationDetail>(
    jobId ? `/detection-evaluations/${encodeURIComponent(jobId)}` : null,
    2500,
  );
  useEffect(() => {
    if (source) onSourceConsumed?.();
  }, [source, onSourceConsumed]);
  useEffect(() => {
    if (!id && datasets.data?.[0]) {
      setId(datasets.data[0].id);
      setRevision(datasets.data[0].revision);
    }
  }, [id, datasets.data]);
  useEffect(() => {
    if (!jobId && jobs.data?.items[0]) setJobId(jobs.data.items[0].id);
  }, [jobId, jobs.data]);
  const dataset =
    history.data?.find(
      (item) => item.id === id && item.revision === revision,
    ) ?? null;
  const job = detail.data?.id === jobId ? detail.data : null;
  function refresh() {
    datasets.reload();
    history.reload();
    evaluators.reload();
    jobs.reload();
    detail.reload();
  }
  function saved(dataset: DatasetDetail) {
    setEditor(null);
    setPendingSource(null);
    setId(dataset.id);
    setRevision(dataset.revision);
    datasets.reload();
    history.reload();
  }
  function submitted(job: EvaluationDetail) {
    setLaunch(null);
    setTab("evaluations");
    setStatus("");
    setBefore(null);
    setJobId(job.id);
    jobs.reload();
    detail.reload();
  }
  return (
    <div className="datasets-page">
      <PageHeading
        eyebrow="SOURCE IMAGES → REVIEWED LABELS → EVIDENCE"
        title="Detection datasets"
        description="Author immutable image labels, declare review context, and evaluate fixed model predictions against a complete dataset split."
      />
      <div className="dataset-workspace-toolbar">
        <div
          className="dataset-tabs"
          role="tablist"
          aria-label="Dataset workspace views"
        >
          <button
            role="tab"
            aria-selected={tab === "datasets"}
            onClick={() => setTab("datasets")}
          >
            <Database size={15} />
            Datasets
          </button>
          <button
            role="tab"
            aria-selected={tab === "evaluations"}
            onClick={() => setTab("evaluations")}
          >
            <ListChecks size={15} />
            Evaluations
          </button>
          <button role="tab" aria-selected={tab === "comparison"} onClick={() => setTab("comparison")}>
            Compare evaluations
          </button>
        </div>
        <div className="dataset-actions">
          <button
            className="button secondary"
            aria-label="Refresh datasets workspace"
            onClick={refresh}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          {tab === "datasets" ? (
            <button
              className="button primary"
              onClick={() => {
                setPendingSource(null);
                setEditor("new");
              }}
            >
              <Plus size={15} />
              New dataset
            </button>
          ) : (
            <button
              className="button primary"
              disabled={!datasets.data?.length}
              onClick={() => setLaunch("new")}
            >
              <Plus size={15} />
              New evaluation
            </button>
          )}
        </div>
      </div>
      {tab === "comparison" ? (
        <EvaluationComparison onOpenEvaluation={(id) => { setJobId(id); setTab("evaluations"); }} />
      ) : datasets.error ? (
        <ErrorState message={datasets.error} retry={datasets.reload} />
      ) : tab === "datasets" ? (
        datasets.loading && !datasets.data ? (
          <Loading label="Loading immutable datasets…" />
        ) : !datasets.data?.length ? (
          <section className="panel">
            <Empty
              title="Start with original images"
              description="Capture camera messages directly, define classes, and author boxes. Inference jobs are not required to create a dataset."
              action={
                <button
                  className="button primary"
                  onClick={() => setEditor("new")}
                >
                  Create first dataset
                </button>
              }
            />
          </section>
        ) : (
          <>
            <div className="dataset-library-controls">
              <label>
                Dataset
                <select
                  aria-label="Select detection dataset"
                  value={id}
                  onChange={(e) => {
                    const selected = datasets.data!.find(
                      (item) => item.id === e.target.value,
                    )!;
                    setId(selected.id);
                    setRevision(selected.revision);
                  }}
                >
                  {datasets.data.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Immutable revision
                <select
                  aria-label="Dataset revision"
                  value={revision}
                  onChange={(e) => setRevision(Number(e.target.value))}
                >
                  {history.data?.map((item) => (
                    <option key={item.revision} value={item.revision}>
                      r{item.revision} · {formatDate(item.created_at)}
                    </option>
                  )) ?? <option value={revision}>r{revision}</option>}
                </select>
              </label>
            </div>
            {history.error ? (
              <ErrorState message={history.error} retry={history.reload} />
            ) : !dataset ? (
              <Loading label="Loading the selected dataset revision…" />
            ) : (
              <DatasetDetails
                key={`${dataset.id}/${dataset.revision}`}
                dataset={dataset}
                onEdit={() => setEditor(dataset)}
                onEvaluate={() => setLaunch(dataset)}
              />
            )}
          </>
        )
      ) : (
        <>
          <div className="evaluation-list-toolbar">
            <label>
              Evaluation status
              <select
                aria-label="Filter detection evaluations"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setBefore(null);
                  setJobId("");
                }}
              >
                <option value="">All states</option>
                {[
                  "queued",
                  "running",
                  "cancel_requested",
                  "completed",
                  "failed",
                  "cancelled",
                  "interrupted",
                ].map((value) => (
                  <option key={value} value={value}>
                    {value.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <span>
              {jobs.data?.items.length ?? 0} evaluations in this page · shared
              durable queue
            </span>
          </div>
          {jobs.error && (
            <p className="dataset-stale" role="alert">
              Evaluation list refresh failed.{" "}
              {jobs.data
                ? "Showing its last successful observation."
                : "No list observation available."}{" "}
              {jobs.error}
              <button onClick={jobs.reload}>Retry evaluation list</button>
            </p>
          )}
          <div className="evaluation-workspace-grid">
            <aside
              className="panel evaluation-job-list"
              aria-label="Detection evaluation library"
            >
              {jobs.loading && !jobs.data ? (
                <Loading label="Loading evaluations…" />
              ) : jobs.data?.items.length ? (
                jobs.data.items.map((item) => (
                  <button
                    className="evaluation-job-card"
                    key={item.id}
                    aria-label={`Open detection evaluation ${item.name}`}
                    aria-pressed={jobId === item.id}
                    onClick={() => setJobId(item.id)}
                  >
                    <span>
                      #{item.submission_seq}
                      <JobStatusBadge status={item.status} />
                    </span>
                    <strong>{item.name}</strong>
                    <small>
                      {item.dataset_name} r{item.dataset_revision}
                    </small>
                    <time>{formatDate(item.created_at)}</time>
                  </button>
                ))
              ) : (
                <div className="dataset-list-empty">
                  <ListChecks size={24} />
                  <strong>No evaluations in this page</strong>
                  <p>Pin a dataset split and source predictions to begin.</p>
                </div>
              )}
              <div className="dataset-actions">
                <button
                  className="button secondary small"
                  disabled={before === null}
                  onClick={() => {
                    setBefore(null);
                    setJobId("");
                  }}
                >
                  <ArrowLeft size={12} />
                  Newest
                </button>
                <button
                  className="button secondary small"
                  disabled={jobs.data?.next_before_seq == null}
                  onClick={() => {
                    setBefore(jobs.data?.next_before_seq ?? null);
                    setJobId("");
                  }}
                >
                  Older
                  <ArrowRight size={12} />
                </button>
              </div>
            </aside>
            <div className="evaluation-main">
              {detail.error && (
                <p className="dataset-stale" role="alert">
                  {job
                    ? "Evaluation refresh failed. Showing the last observation of this same job."
                    : "The selected evaluation could not be loaded."}{" "}
                  {detail.error}
                  <button onClick={detail.reload}>
                    Retry selected evaluation
                  </button>
                </p>
              )}
              {jobId && detail.loading && !job ? (
                <Loading label="Loading captured evaluation evidence…" />
              ) : job ? (
                <EvaluationInspector
                  key={job.id}
                  job={job}
                  onChanged={() => {
                    jobs.reload();
                    detail.reload();
                  }}
                />
              ) : !jobId ? (
                <section className="panel">
                  <Empty
                    title="Select an evaluation"
                    description="Inspect source compatibility, full image coverage, evaluator metrics, and image-level diagnostic matches."
                  />
                </section>
              ) : null}
            </div>
          </div>
        </>
      )}
      {editor && (
        <DatasetEditor
          dataset={editor === "new" ? undefined : editor}
          expectedRevision={
            datasets.data?.find(
              (item) => item.id === (editor === "new" ? "" : editor.id),
            )?.revision
          }
          source={pendingSource}
          onClose={() => setEditor(null)}
          onSaved={saved}
        />
      )}
      {launch && datasets.data && (
        <EvaluationLauncher
          datasets={datasets.data}
          initialDataset={launch === "new" ? null : launch}
          evaluators={evaluators.data ?? []}
          onClose={() => setLaunch(null)}
          onSubmitted={submitted}
        />
      )}
      {evaluators.error && tab === "evaluations" && (
        <p className="dataset-stale" role="alert">
          Evaluator capability lookup failed: {evaluators.error}
          <button onClick={evaluators.reload}>Retry evaluator catalog</button>
        </p>
      )}
    </div>
  );
}
