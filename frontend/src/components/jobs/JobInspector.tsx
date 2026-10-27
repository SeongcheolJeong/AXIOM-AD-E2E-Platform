import { useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Fingerprint,
  GitBranch,
  History,
  LoaderCircle,
  OctagonAlert,
  ShieldQuestion,
  Square,
  Terminal,
} from "lucide-react";
import { api } from "../../api";
import { CopyButton } from "../ui";
import { categoryLabels } from "../../types";
import JobStatusBadge from "./JobStatusBadge";
import { elapsedTime, preciseJobTime } from "./JobTime";
import type { JobDetail } from "./JobTypes";
import CarlaSavedContext from "../carla/CarlaSavedContext";
import RemoteExecutionPanel from "./RemoteExecutionPanel";

function statusExplanation(job: JobDetail) {
  switch (job.status) {
    case "queued":
      return "Waiting for a worker to claim this submission. Its captured inputs are available below.";
    case "running":
      return job.dispatched_at
        ? "The adapter dispatch intent has been recorded. Waiting for a recorded execution outcome."
        : "A worker has claimed this job. Waiting for adapter dispatch or an execution outcome.";
    case "cancel_requested":
      return job.cancellation_supported
        ? "Cancellation was requested. The worker must acknowledge it before this job can be considered stopped."
        : "This adapter does not support cooperative cancellation. The request is recorded, and a completed result may still arrive.";
    case "completed":
      return "Execution finished. Open the recorded result to inspect replay, evidence, and metric verdicts.";
    case "failed":
      return "Execution failed. Inspect the recorded error and events for the failure boundary.";
    case "cancelled":
      return "Cancellation is recorded. This job will not be automatically resubmitted.";
    case "interrupted":
      return "The worker was interrupted. This job will not be automatically retried.";
  }
}

export default function JobInspector({
  job,
  now,
  onOpenRun,
  onChanged,
  onRecovered,
}: {
  job: JobDetail;
  now: number;
  onOpenRun: (id: string) => void;
  onChanged: () => void;
  onRecovered: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const canCancel = job.status === "queued" || job.status === "running";
  async function cancel() {
    setCancelling(true);
    setError("");
    try {
      await api<JobDetail>(`/jobs/${encodeURIComponent(job.id)}/cancel`, {
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
      setCancelling(false);
    }
  }
  return (
    <div className="job-inspector" data-testid="job-detail">
      <section className="panel job-detail-header">
        <div className="job-detail-kicker">
          <span>SUBMISSION #{job.submission_seq}</span>
          <JobStatusBadge status={job.status} />
        </div>
        <h2>{job.scenario_name}</h2>
        <div className="job-detail-identity">
          <code data-testid="job-id">{job.id}</code>
          <CopyButton text={job.id} label="Copy job ID" />
        </div>
        <div className="job-execution-context">
          <span>
            <GitBranch size={14} />
            Scenario revision {job.scenario_revision}
          </span>
          <span>
            <Terminal size={14} />
            {job.adapter_id} · {job.adapter_version}
          </span>
        </div>
        <p
          className="job-state-explanation"
          data-testid="job-state-explanation"
        >
          {statusExplanation(job)}
        </p>
        {job.execution_context && (
          <CarlaSavedContext context={job.execution_context} />
        )}
        {job.outcome_uncertain && (
          <div className="job-uncertain" role="alert">
            <OctagonAlert size={18} />
            <div>
              <strong>Execution outcome is uncertain</strong>
              <p>
                A dispatched operation may have continued beyond the worker
                lease. Check the external system before creating another
                execution. No automatic retry is scheduled.
              </p>
            </div>
          </div>
        )}
        {job.error && (
          <div className="job-execution-error" role="alert">
            <AlertCircle size={16} />
            <p>{job.error}</p>
          </div>
        )}
        <div className="job-detail-actions">
          {job.run_id && (
            <button
              type="button"
              className="button primary"
              onClick={() => onOpenRun(job.run_id!)}
            >
              <ArrowUpRight size={16} />
              Open result
            </button>
          )}
          {canCancel && (
            <button
              type="button"
              className="button secondary"
              disabled={cancelling}
              onClick={cancel}
            >
              {cancelling ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Square size={14} />
              )}
              {cancelling
                ? "Sending cancellation…"
                : job.status === "queued"
                  ? "Cancel queued job"
                  : "Request cancellation"}
            </button>
          )}
          {job.status === "cancel_requested" && (
            <span className="job-cancel-pending">
              <Clock3 size={14} />
              Cancellation is awaiting an outcome
            </span>
          )}
          {!job.run_id && !canCancel && job.status !== "cancel_requested" && (
            <span className="job-no-result">
              No result is linked to this job.
            </span>
          )}
        </div>
        {canCancel &&
          !job.cancellation_supported &&
          job.status === "running" && (
            <p className="job-cancel-help">
              This adapter cannot stop cooperatively. A cancellation request
              records your intent; a result may still arrive.
            </p>
          )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="job-time-cards">
          <div>
            <span>
              Queue wait{!job.started_at && !job.finished_at ? " · live" : ""}
            </span>
            <strong>
              {elapsedTime(
                job.created_at,
                job.started_at ?? job.finished_at ?? now,
              )}
            </strong>
          </div>
          <div>
            <span>
              Worker elapsed
              {job.started_at && !job.finished_at ? " · live" : ""}
            </span>
            <strong>
              {elapsedTime(job.started_at, job.finished_at ?? now)}
            </strong>
          </div>
          <div>
            <span>Cancellation</span>
            <strong className="job-capability-value">
              {job.cancellation_supported ? "Cooperative" : "Not supported"}
            </strong>
          </div>
        </div>
      </section>
      {job.dispatched_at && (
        <RemoteExecutionPanel
          key={job.id}
          job={job}
          onOpenRun={onOpenRun}
          onPublished={onRecovered}
        />
      )}
      <section
        className="panel job-event-panel"
        aria-label="Job event timeline"
      >
        <div className="job-panel-heading">
          <History size={17} />
          <h3>Event timeline</h3>
          <span>
            {job.events_total && job.events_total > job.events.length
              ? `Latest ${job.events.length} of ${job.events_total} events`
              : `${job.events.length} recorded events`}
          </span>
        </div>
        {job.events.length ? (
          <ol className="job-events">
            {job.events.map((event, index) => (
              <li key={`${index}-${event.at}-${event.type}`}>
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
        ) : (
          <p className="job-empty-events">
            No lifecycle events have been recorded.
          </p>
        )}
      </section>
      <section
        className="panel job-input-panel"
        aria-label="Job input provenance"
      >
        <div className="job-panel-heading">
          <Fingerprint size={17} />
          <h3>Captured inputs & ownership</h3>
        </div>
        <div className="job-input-summary">
          <div>
            <span>Scenario</span>
            <strong>
              {categoryLabels[job.scenario_snapshot.category] ??
                job.scenario_snapshot.category}{" "}
              · {job.scenario_snapshot.map_name}
            </strong>
          </div>
          <div>
            <span>Seed</span>
            <strong className="mono">
              {job.request.seed ?? job.scenario_snapshot.seed}
            </strong>
          </div>
          <div>
            <span>Worker</span>
            <strong className="mono">{job.worker_id ?? "Not assigned"}</strong>
          </div>
        </div>
        <div className="job-input-hash">
          <span>INPUT SHA-256</span>
          <code data-testid="job-input-hash">{job.input_hash}</code>
          <CopyButton text={job.input_hash} label="Copy job input hash" />
        </div>
        <details className="job-disclosure">
          <summary>
            <Clock3 size={15} />
            Lifecycle timestamps · UTC
          </summary>
          <dl className="job-timestamps">
            {(
              [
                ["Submitted", job.created_at],
                ["Worker claimed", job.started_at],
                ["Dispatch intent", job.dispatched_at],
                ["Cancel requested", job.cancel_requested_at],
                ["Finished", job.finished_at],
                ["Last updated", job.updated_at],
                ["Lease expires", job.lease_expires_at],
              ] as const
            ).map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{preciseJobTime(value)}</dd>
              </div>
            ))}
          </dl>
          <p>
            Live elapsed labels compare recorded times with this browser's
            clock. Lease expiry alone does not prove that an external operation
            stopped.
          </p>
        </details>
        <details className="job-disclosure">
          <summary>
            <GitBranch size={15} />
            Captured scenario snapshot{" "}
            <span>revision {job.scenario_revision}</span>
          </summary>
          <div className="job-json-toolbar">
            <span>Captured configuration for this submission</span>
            <CopyButton
              text={JSON.stringify(job.scenario_snapshot, null, 2)}
              label="Copy job scenario snapshot"
            />
          </div>
          <pre aria-label="Captured job scenario JSON">
            {JSON.stringify(job.scenario_snapshot, null, 2)}
          </pre>
        </details>
        <details className="job-disclosure">
          <summary>
            <Terminal size={15} />
            Execution request
          </summary>
          <pre aria-label="Job execution request JSON">
            {JSON.stringify(job.request, null, 2)}
          </pre>
        </details>
        <p className="job-provenance-note">
          <ShieldQuestion size={15} />A durable execution record preserves
          lifecycle evidence. Adapter fidelity and sensor-validation limits
          remain properties of the recorded result.
        </p>
      </section>
      {job.status === "completed" && job.run_id && (
        <div className="job-completion-note">
          <CheckCircle2 size={16} />
          Result linked as <code>{job.run_id}</code>
        </div>
      )}
    </div>
  );
}
