import { useState } from "react";
import { ArrowDownToLine, LoaderCircle, Square } from "lucide-react";
import { api, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import JobStatusBadge from "../jobs/JobStatusBadge";
import { elapsedTime } from "../jobs/JobTime";
import StackTraceExplorer from "./StackTraceExplorer";
import type { StackReplayDetail } from "./StackReplayTypes";
export default function StackReplayInspector({
  job,
  onChanged,
}: {
  job: StackReplayDetail;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function cancel() {
    setBusy(true);
    setError("");
    try {
      await api(`/stack-replays/${encodeURIComponent(job.id)}/cancel`, {
        method: "POST",
      });
      onChanged();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Replay cancellation failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="stack-replay-inspector" data-testid="stack-replay-detail">
      <section className="panel stack-replay-heading">
        <div className="stack-section-heading">
          <div>
            <span className="stack-eyebrow">
              RECORDED REPLAY #{job.submission_seq}
            </span>
            <h2>{job.label}</h2>
            <p>
              {job.stack_name} r{job.stack_revision} ·{" "}
              {job.recording_snapshot.name} · binding r{job.binding_revision}
            </p>
          </div>
          <JobStatusBadge status={job.status} />
        </div>
        <code className="stack-replay-id" data-testid="stack-replay-id">
          {job.id}
        </code>
        <div className="stack-mode-notice">
          <strong>RECORDED OPEN-LOOP · NOT APPLIED</strong>
          <span>
            Control requests are diagnostic outputs. Recorded ego motion and
            subsequent observations are never changed by them.
          </span>
        </div>
        <div className="stack-replay-counts">
          <div>
            <span>Completed / selected ticks</span>
            <strong>
              {job.counts.completed}
              <small> / {job.counts.total}</small>
            </strong>
          </div>
          <div>
            <span>Running</span>
            <strong>{job.counts.running}</strong>
          </div>
          <div>
            <span>Failed</span>
            <strong>{job.counts.failed}</strong>
          </div>
          <div>
            <span>Not attempted</span>
            <strong>{job.counts.not_attempted}</strong>
          </div>
          <div>
            <span>Worker elapsed · observed</span>
            <strong className="stack-elapsed">
              {elapsedTime(job.started_at, job.finished_at ?? job.updated_at)}
            </strong>
          </div>
        </div>
        <div className="stack-actions">
          {(job.status === "queued" || job.status === "running") && (
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={cancel}
            >
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Square size={13} />
              )}{" "}
              {job.status === "queued"
                ? "Cancel queued stack replay"
                : "Request stack replay cancellation"}
            </button>
          )}
          {job.download_url && (
            <DownloadLink
              href={job.download_url}
              download
              className="button secondary"
            >
              <ArrowDownToLine size={14} />
              Download stack replay manifest
            </DownloadLink>
          )}
        </div>
        {job.status === "cancel_requested" && (
          <p className="stack-notice">
            Cancellation requested. Waiting for the worker to seal the outcome;
            previously published ticks remain available.
          </p>
        )}
        {job.outcome_uncertain && (
          <p className="inline-error" role="alert">
            Worker ownership was interrupted. No automatic replay is scheduled.
            Inspect retained tick evidence and execution events.
          </p>
        )}
        {(error || job.error) && (
          <p className="inline-error" role="alert">
            {error || job.error}
          </p>
        )}
      </section>
      <StackTraceExplorer key={job.id} job={job} />
      <section className="panel stack-replay-evidence">
        <div className="stack-hash">
          <span>Input snapshot SHA-256</span>
          <code>{job.input_hash}</code>
        </div>
        {job.manifest_hash && (
          <div className="stack-hash">
            <span>Terminal manifest SHA-256</span>
            <code>{job.manifest_hash}</code>
          </div>
        )}
        <details className="stack-disclosure">
          <summary>
            Captured stack, recording & sensor-binding revisions
          </summary>
          <pre>
            {JSON.stringify(
              {
                request: job.request,
                stack: job.stack_snapshot,
                recording: job.recording_snapshot,
                binding: job.binding_snapshot,
              },
              null,
              2,
            )}
          </pre>
        </details>
        <details className="stack-disclosure">
          <summary>
            Execution events · {job.events.length}
            {job.events_total > job.events.length
              ? ` of ${job.events_total}`
              : ""}
          </summary>
          <ol className="stack-events">
            {job.events.map((event, index) => (
              <li key={`${event.at}/${index}`}>
                <time>{formatDate(event.at)}</time>
                <strong>{event.type.replaceAll("_", " ")}</strong>
                <p>{event.message}</p>
              </li>
            ))}
          </ol>
          <p>
            Worker {job.worker_id ?? "not assigned"} · lease{" "}
            {job.lease_expires_at ?? "none"}
          </p>
        </details>
      </section>
    </div>
  );
}
