import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Beaker,
  Cpu,
  GitCompareArrows,
  Plus,
  RefreshCw,
} from "lucide-react";
import { formatDate } from "../api";
import { Empty, ErrorState, Loading, PageHeading } from "../components/ui";
import { useSensorResource } from "../components/sensors/SensorData";
import { useJobResource } from "../components/jobs/useJobResource";
import JobStatusBadge from "../components/jobs/JobStatusBadge";
import ModelRegistry from "../components/perception/ModelRegistry";
import InferenceLauncher from "../components/perception/InferenceLauncher";
import InferenceInspector from "../components/perception/InferenceInspector";
import InferenceComparison from "../components/perception/InferenceComparison";
import type {
  InferenceDetail,
  InferenceJobs,
  InferenceSource,
  ModelDetail,
  RuntimeMetadata,
} from "../components/perception/PerceptionTypes";
import "../perception.css";

export default function Perception({
  source,
  onSourceConsumed,
}: {
  source: InferenceSource | null;
  onSourceConsumed: () => void;
}) {
  const [tab, setTab] = useState<"experiments" | "models" | "compare">(
    "experiments",
  );
  const [pendingSource, setPendingSource] = useState<InferenceSource | null>(
    source,
  );
  const [launch, setLaunch] = useState(Boolean(source));
  const [launchModel, setLaunchModel] = useState<ModelDetail | null>(null);
  const [status, setStatus] = useState(""),
    [before, setBefore] = useState<number | null>(null);
  const [id, setId] = useState(""),
    [compareJob, setCompareJob] = useState<string | null>(null);
  const models = useSensorResource<ModelDetail[]>("/perception/models");
  const runtimes = useSensorResource<RuntimeMetadata[]>("/perception/runtimes");
  const query = new URLSearchParams({ limit: "100" });
  if (status) query.set("status", status);
  if (before !== null) query.set("before_seq", String(before));
  const jobs = useJobResource<InferenceJobs>(`/perception/jobs?${query}`, 3000);
  const detail = useJobResource<InferenceDetail>(
    id ? `/perception/jobs/${encodeURIComponent(id)}` : null,
    2500,
  );
  const job = detail.data?.id === id ? detail.data : null;
  useEffect(() => {
    if (source) onSourceConsumed();
  }, [source, onSourceConsumed]);
  useEffect(() => {
    if (!id && jobs.data?.items[0]) setId(jobs.data.items[0].id);
  }, [id, jobs.data]);
  function refresh() {
    models.reload();
    runtimes.reload();
    jobs.reload();
    detail.reload();
  }
  function submitted(job: InferenceDetail) {
    setLaunch(false);
    setPendingSource(null);
    setId(job.id);
    setStatus("");
    setBefore(null);
    setTab("experiments");
    jobs.reload();
    detail.reload();
  }
  return (
    <div className="perception-page">
      <PageHeading
        eyebrow="OBSERVATIONS → MODEL PREDICTIONS"
        title="Perception experiments"
        description="Run fixed model revisions on recorded camera images and preserve the predictions with their source evidence."
      />
      <div className="perception-topbar">
        <div
          role="tablist"
          aria-label="Perception workspace views"
          className="perception-tabs"
        >
          {(
            [
              ["experiments", "Experiments", Beaker],
              ["models", "Models", Cpu],
              ["compare", "Compare", GitCompareArrows],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        <div className="perception-actions">
          <button
            className="button secondary"
            aria-label="Refresh perception workspace"
            onClick={refresh}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            className="button primary"
            disabled={
              !models.data?.length ||
              !runtimes.data?.some((runtime) => runtime.status === "available")
            }
            onClick={() => {
              setLaunchModel(null);
              setLaunch(true);
            }}
          >
            <Plus size={15} />
            New experiment
          </button>
        </div>
      </div>
      {pendingSource && !models.loading && !models.data?.length && (
        <div className="perception-stale" role="status">
          Camera channel #{pendingSource.channel_id}, ordinal #
          {pendingSource.ordinal} is selected. Register a model to run this
          saved image.
          <button
            className="button secondary small"
            onClick={() => setTab("models")}
          >
            Open model registry
          </button>
        </div>
      )}
      {models.error || runtimes.error ? (
        <ErrorState message={models.error || runtimes.error} retry={refresh} />
      ) : tab === "models" ? (
        models.loading || runtimes.loading ? (
          <Loading label="Loading model registry…" />
        ) : (
          <ModelRegistry
            models={models.data ?? []}
            runtimes={runtimes.data ?? []}
            onRefresh={models.reload}
            onLaunch={(model) => {
              setLaunchModel(model);
              setLaunch(true);
            }}
          />
        )
      ) : tab === "compare" ? (
        <InferenceComparison
          key={compareJob ?? "comparisons"}
          initialJob={compareJob}
        />
      ) : (
        <>
          <div className="perception-experiment-toolbar">
            <label>
              Execution status
              <select
                aria-label="Filter inference status"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setBefore(null);
                  setId("");
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
            <p>
              {jobs.data
                ? `${jobs.data.items.length} submissions in this window`
                : "Loading submissions"}{" "}
              · Shared durable worker queue
            </p>
          </div>
          {jobs.error && (
            <div className="perception-stale" role="alert">
              Experiment list refresh failed.{" "}
              {jobs.data
                ? "Showing its last successful observation."
                : "No list observation is available."}{" "}
              {jobs.error}
              <button className="button secondary small" onClick={jobs.reload}>
                Retry experiment list
              </button>
            </div>
          )}
          <div className="perception-experiment-grid">
            <aside
              className="panel perception-job-list"
              aria-label="Perception experiment library"
            >
              {jobs.loading && !jobs.data ? (
                <Loading label="Loading experiments…" />
              ) : !jobs.data?.items.length ? (
                <div className="perception-list-empty">
                  <Beaker size={25} />
                  <strong>No experiments in this window</strong>
                  <p>
                    {models.data?.length
                      ? "Create an experiment from original camera messages."
                      : "Register a model to start working with stored images."}
                  </p>
                  <button
                    className="button secondary small"
                    onClick={() => setTab("models")}
                  >
                    Model registry
                  </button>
                </div>
              ) : (
                jobs.data.items.map((item) => (
                  <button
                    key={item.id}
                    className="perception-job-card"
                    aria-label={`Open inference experiment ${item.label}`}
                    aria-pressed={id === item.id}
                    onClick={() => setId(item.id)}
                  >
                    <div>
                      <small>#{item.submission_seq}</small>
                      <JobStatusBadge status={item.status} />
                    </div>
                    <strong>{item.label}</strong>
                    <span>
                      {item.model_name} · r{item.model_revision}
                    </span>
                    <small>
                      {item.counts.completed} / {item.counts.total} completed ·{" "}
                      {item.counts.failed} failed
                    </small>
                    <time>{formatDate(item.created_at)}</time>
                  </button>
                ))
              )}
              <div className="perception-job-pagination">
                <button
                  className="button secondary small"
                  disabled={before === null}
                  onClick={() => {
                    setBefore(null);
                    setId("");
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
                    setId("");
                  }}
                >
                  Older
                  <ArrowRight size={12} />
                </button>
              </div>
            </aside>
            <div className="perception-experiment-main">
              {detail.error && (
                <div className="perception-stale" role="alert">
                  {job
                    ? `State refresh failed. Showing the observation from ${detail.fetchedAt ? new Date(detail.fetchedAt).toLocaleTimeString() : "the last successful request"}.`
                    : "This experiment could not be loaded."}{" "}
                  {detail.error}
                  <button
                    className="button secondary small"
                    onClick={detail.reload}
                  >
                    Retry selected experiment
                  </button>
                </div>
              )}
              {id && detail.loading && !job ? (
                <section className="panel">
                  <Loading label="Loading fixed inputs and prediction evidence…" />
                </section>
              ) : job ? (
                <InferenceInspector
                  key={job.id}
                  job={job}
                  onChanged={() => {
                    detail.reload();
                    jobs.reload();
                  }}
                  onCompare={(jobId) => {
                    setCompareJob(jobId);
                    setTab("compare");
                  }}
                  onRepeat={(selected) => {
                    setLaunchModel(selected.model_snapshot);
                    const input = selected.inputs[0];
                    setPendingSource(
                      input
                        ? {
                            recording_id: input.recording_id,
                            channel_id: input.channel_id,
                            ordinal: input.ordinal,
                            decoder_id: input.decoder_id,
                            decoder_version: input.decoder_version,
                          }
                        : null,
                    );
                    setLaunch(true);
                  }}
                />
              ) : !id ? (
                <section className="panel">
                  <Empty
                    title="Select a perception experiment"
                    description="Inspect original inputs, per-frame inference outcomes, saved predictions, and exact runtime evidence."
                  />
                </section>
              ) : null}
            </div>
          </div>
        </>
      )}
      {launch && Boolean(models.data?.length) && runtimes.data && (
        <InferenceLauncher
          models={models.data!}
          runtimes={runtimes.data}
          model={launchModel}
          source={pendingSource}
          onClose={() => setLaunch(false)}
          onSubmitted={submitted}
        />
      )}
    </div>
  );
}
