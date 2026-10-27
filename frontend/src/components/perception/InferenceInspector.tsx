import { useState } from "react";
import {
  ArrowDownToLine,
  Fingerprint,
  History,
  LoaderCircle,
  Square,
} from "lucide-react";
import { api, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { CopyButton } from "../ui";
import JobStatusBadge from "../jobs/JobStatusBadge";
import { elapsedTime } from "../jobs/JobTime";
import InferenceFramePanel from "./InferenceFramePanel";
import { terminalInference, type InferenceDetail } from "./PerceptionTypes";

export default function InferenceInspector({
  job,
  onChanged,
  onCompare,
  onRepeat,
}: {
  job: InferenceDetail;
  onChanged: () => void;
  onCompare: (id: string) => void;
  onRepeat: (job: InferenceDetail) => void;
}) {
  const [index, setIndex] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const frame =
    job.frames.find((frame) => frame.selection_index === index) ??
    job.frames[0];
  async function cancel() {
    setBusy(true);
    setError("");
    try {
      await api(`/perception/jobs/${encodeURIComponent(job.id)}/cancel`, {
        method: "POST",
      });
      onChanged();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Cancellation request failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="perception-inspector" data-testid="perception-job-detail">
      <section className="panel perception-job-header">
        <div className="perception-small-heading">
          <span>EXPERIMENT #{job.submission_seq}</span>
          <JobStatusBadge status={job.status} />
        </div>
        <div className="perception-section-toolbar">
          <div>
            <h2>{job.label}</h2>
            <p>
              {job.model_name} · r{job.model_revision}{" "}
              <span className="perception-dot">/</span> {job.runtime_id} ·{" "}
              {job.runtime_version}
            </p>
          </div>
          <div className="perception-actions">
            {(job.status === "queued" || job.status === "running") && (
              <button
                className="button secondary"
                disabled={busy}
                onClick={cancel}
              >
                {busy ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Square size={13} />
                )}
                {job.status === "queued"
                  ? "Cancel queued inference"
                  : "Request inference cancellation"}
              </button>
            )}
            {terminalInference(job.status) && (
              <button
                className="button secondary"
                onClick={() => onCompare(job.id)}
              >
                Compare results
              </button>
            )}
            <button className="button secondary" onClick={() => onRepeat(job)}>
              New experiment with this model
            </button>
          </div>
        </div>
        <div className="perception-hash">
          <code data-testid="perception-job-id">{job.id}</code>
          <CopyButton text={job.id} label="Copy perception job ID" />
        </div>
        {job.outcome_uncertain && (
          <p className="inline-error" role="alert">
            Execution ownership was interrupted. The outcome is uncertain;
            committed frame results remain available. No automatic retry is
            scheduled.
          </p>
        )}
        {job.status === "cancel_requested" && (
          <p className="perception-stale" role="status">
            Cancellation requested. Waiting for the worker to record that
            execution has stopped. Completed inputs remain available.
          </p>
        )}
        {job.error && (
          <p className="inline-error" role="alert">
            {job.error}
          </p>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="perception-counts" data-testid="perception-counts">
          <div>
            <span>Completed / all inputs</span>
            <strong>
              {job.counts.completed}
              <small> / {job.counts.total}</small>
            </strong>
          </div>
          <div>
            <span>Failed inputs</span>
            <strong>{job.counts.failed}</strong>
          </div>
          <div>
            <span>Running inputs</span>
            <strong>{job.counts.running}</strong>
          </div>
          <div>
            <span>Not attempted</span>
            <strong>{job.counts.not_attempted}</strong>
          </div>
          <div>
            <span>
              {job.finished_at ? "Worker elapsed" : "Worker elapsed · observed"}
            </span>
            <strong>
              {elapsedTime(job.started_at, job.finished_at ?? job.updated_at)}
            </strong>
          </div>
        </div>
        <p className="perception-muted">
          Completion means prediction output was stored. It is not a perception
          accuracy, ground-truth, or driving validation verdict.
        </p>
      </section>
      <section className="panel perception-frame-workspace">
        <div
          className="perception-frame-list"
          aria-label="Inference input results"
        >
          {job.frames.map((item) => (
            <button
              key={item.selection_index}
              aria-pressed={frame?.selection_index === item.selection_index}
              aria-label={`Inspect inference input ${item.selection_index + 1}, ordinal ${item.input.ordinal}`}
              onClick={() => setIndex(item.selection_index)}
            >
              <span>#{item.input.ordinal}</span>
              <i className={item.status} />
              <small>
                {item.status === "completed"
                  ? `${item.detections.length} detections`
                  : item.status.replaceAll("_", " ")}
              </small>
            </button>
          ))}
        </div>
        {frame ? (
          <InferenceFramePanel frame={frame} />
        ) : (
          <p className="perception-frame-empty">
            No per-input observations have been returned yet.
          </p>
        )}
      </section>
      <section className="panel perception-job-evidence">
        <div className="perception-section-toolbar">
          <h3>
            <Fingerprint size={17} />
            Captured execution evidence
          </h3>
          {job.download_url && (
            <DownloadLink
              className="button secondary small"
              href={job.download_url}
              download
            >
              <ArrowDownToLine size={14} />
              Download inference manifest
            </DownloadLink>
          )}
        </div>
        <div className="perception-hash">
          <span>Input configuration hash</span>
          <code>{job.input_hash}</code>
        </div>
        {job.manifest_hash && (
          <div className="perception-hash">
            <span>Terminal manifest SHA-256</span>
            <code>{job.manifest_hash}</code>
          </div>
        )}
        <details className="perception-details">
          <summary>Fixed request, model & recording declarations</summary>
          <pre aria-label="Captured inference request">
            {JSON.stringify(
              {
                request: job.request,
                model: job.model_snapshot,
                recording: job.recording_snapshot,
              },
              null,
              2,
            )}
          </pre>
        </details>
        <details className="perception-details">
          <summary>
            <History size={15} />
            Recorded events · {job.events.length}
            {job.events_total > job.events.length
              ? ` of ${job.events_total}`
              : ""}
          </summary>
          <ol className="perception-events">
            {job.events.map((event, eventIndex) => (
              <li key={`${event.at}/${eventIndex}`}>
                <time>{formatDate(event.at)}</time>
                <strong>{event.type.replaceAll("_", " ")}</strong>
                <span>{event.message}</span>
              </li>
            ))}
          </ol>
          <p className="perception-muted">
            Worker {job.worker_id || "not assigned"} · Lease{" "}
            {job.lease_expires_at ? formatDate(job.lease_expires_at) : "none"}
          </p>
        </details>
      </section>
    </div>
  );
}
