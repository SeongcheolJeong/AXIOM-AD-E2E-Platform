import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, LoaderCircle, Square } from "lucide-react";
import { api, formatBytes } from "../../api";
import { CopyButton } from "../ui";
import type { RecoveryDetail } from "./RemoteExecutionTypes";
import { preciseJobTime } from "./JobTime";
import JobStatusBadge from "./JobStatusBadge";
import RecoveryDownload from "./RecoveryDownload";
import RemoteStatusDetails from "./RemoteStatusDetails";

export default function RecoveryOperation({
  recovery,
  onOpenRun,
  onChanged,
}: {
  recovery: RecoveryDetail;
  onOpenRun: (id: string) => void;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), [recovery.id]);
  const cancellable =
    recovery.status === "queued" || recovery.status === "running";
  async function cancel() {
    const request = new AbortController();
    controller.current = request;
    setPending(true);
    setError("");
    try {
      const result = await api<RecoveryDetail>(
        `/execution-recoveries/${encodeURIComponent(recovery.id)}/cancel`,
        {
          method: "POST",
          signal: request.signal,
        },
      );
      if (
        result.id !== recovery.id ||
        result.source_job_id !== recovery.source_job_id
      )
        throw new Error("Cancellation response refers to another recovery.");
      if (!request.signal.aborted) onChanged();
    } catch (reason) {
      if (!request.signal.aborted)
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not request cancellation.",
        );
    } finally {
      if (!request.signal.aborted) setPending(false);
    }
  }
  return (
    <article className="recovery-entry" data-testid="recovery-detail">
      <div className="recovery-entry-heading">
        <strong>Recovery #{recovery.submission_seq}</strong>
        <JobStatusBadge status={recovery.status} />
      </div>
      <p>
        <code>{recovery.id}</code>{" "}
        <CopyButton text={recovery.id} label="Copy recovery ID" />
      </p>
      <p>{recovery.note}</p>
      <p data-testid="recovery-outcome">
        <strong>
          Evidence outcome · {recovery.outcome.replaceAll("_", " ")}
        </strong>
      </p>
      {recovery.outcome === "diagnostics_only" && (
        <p>
          Partial or failed execution diagnostics. No completed measurement
          result is claimed.
        </p>
      )}
      {recovery.outcome === "not_available" && (
        <p>
          No retrievable sealed evidence was published. The original execution
          outcome is not resolved by this result.
        </p>
      )}
      {recovery.error && (
        <p className="inline-error" role="alert">
          {recovery.error}
        </p>
      )}
      <dl className="remote-outcome-identities">
        <div>
          <dt>Submitted · UTC</dt>
          <dd>{preciseJobTime(recovery.created_at)}</dd>
        </div>
        <div>
          <dt>Finished · UTC</dt>
          <dd>{preciseJobTime(recovery.finished_at)}</dd>
        </div>
        <div>
          <dt>Recovery worker</dt>
          <dd>
            <code>{recovery.worker_id ?? "Not assigned"}</code>
          </dd>
        </div>
        <div>
          <dt>Original execution ID</dt>
          <dd>
            <code>{recovery.source_execution_id}</code>
          </dd>
        </div>
      </dl>
      <div className="remote-outcome-actions">
        {recovery.run_id && (
          <button
            type="button"
            className="button primary"
            onClick={() => onOpenRun(recovery.run_id!)}
          >
            <ArrowUpRight size={15} />
            Open recovered evidence
          </button>
        )}
        {cancellable && recovery.cancellation_supported && (
          <button
            type="button"
            className="button secondary"
            disabled={pending}
            onClick={cancel}
          >
            {pending ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <Square size={14} />
            )}
            {pending ? "Requesting cancellation…" : "Cancel retrieval"}
          </button>
        )}
      </div>
      {(cancellable || recovery.status === "cancel_requested") && (
        <p className="remote-outcome-scope">
          Cancellation affects this evidence retrieval. It does not cancel the
          original remote execution or confirm engine cleanup.
        </p>
      )}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {(recovery.download_url || recovery.archive_download_url) && (
        <details className="remote-outcome-disclosure">
          <summary>Sealed files and checksums</summary>
          <dl className="remote-outcome-identities">
            {recovery.manifest_hash && (
              <div>
                <dt>Recovery manifest SHA-256</dt>
                <dd>
                  <code>{recovery.manifest_hash}</code>
                </dd>
              </div>
            )}
            {recovery.archive_hash && (
              <div>
                <dt>Original remote archive SHA-256</dt>
                <dd>
                  <code>{recovery.archive_hash}</code>
                </dd>
              </div>
            )}
          </dl>
          <p>
            Archive SHA-256 identifies the original wire bytes. It is separate
            from the Run output hash.
          </p>
          <div className="remote-outcome-actions">
            {recovery.download_url &&
              recovery.manifest_hash &&
              recovery.manifest_size_bytes !== null && (
                <RecoveryDownload
                  key={`${recovery.id}:export:${recovery.manifest_hash}`}
                  id={recovery.id}
                  kind="export"
                  url={recovery.download_url}
                  sha256={recovery.manifest_hash}
                  size={recovery.manifest_size_bytes}
                />
              )}
            {recovery.archive_download_url &&
              recovery.archive_hash &&
              recovery.archive_size_bytes !== null && (
                <RecoveryDownload
                  key={`${recovery.id}:archive:${recovery.archive_hash}`}
                  id={recovery.id}
                  kind="archive"
                  url={recovery.archive_download_url}
                  sha256={recovery.archive_hash}
                  size={recovery.archive_size_bytes}
                />
              )}
          </div>
          {recovery.archive_size_bytes !== null && (
            <p>
              {formatBytes(recovery.archive_size_bytes)} original remote archive
            </p>
          )}
        </details>
      )}
      {recovery.remote_status && (
        <details className="remote-outcome-disclosure">
          <summary>Remote status captured by this recovery</summary>
          <RemoteStatusDetails status={recovery.remote_status} />
        </details>
      )}
      <details className="remote-outcome-disclosure">
        <summary>
          Recovery event timeline · {recovery.events.length} of{" "}
          {recovery.events_total}
        </summary>
        <ol className="job-events">
          {recovery.events.map((event, index) => (
            <li key={`${index}:${event.at}`}>
              <span className={`job-event-dot ${event.type}`} />
              <div>
                <div className="job-event-title">
                  <strong>{event.type.replaceAll("_", " ")}</strong>
                  <time dateTime={event.at}>{preciseJobTime(event.at)}</time>
                </div>
                <p>{event.message}</p>
              </div>
            </li>
          ))}
        </ol>
      </details>
    </article>
  );
}
