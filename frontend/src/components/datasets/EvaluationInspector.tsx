import { useState } from "react";
import { ArrowDownToLine, LoaderCircle, Square } from "lucide-react";
import { api, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import JobStatusBadge from "../jobs/JobStatusBadge";
import EvaluationReportView from "./EvaluationReport";
import type { EvaluationDetail } from "./EvaluationTypes";

export default function EvaluationInspector({
  job,
  onChanged,
}: {
  job: EvaluationDetail;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function cancel() {
    setBusy(true);
    setError("");
    try {
      await api(`/detection-evaluations/${encodeURIComponent(job.id)}/cancel`, {
        method: "POST",
      });
      onChanged();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Evaluation cancellation failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  const first = job.source_jobs[0];
  return (
    <div
      className="evaluation-inspector"
      data-testid="detection-evaluation-detail"
    >
      <section className="panel evaluation-job-header">
        <div className="dataset-section-toolbar">
          <div>
            <span className="dataset-eyebrow">
              EVALUATION #{job.submission_seq}
            </span>
            <h2>{job.name}</h2>
            <p>
              {job.dataset_name} r{job.dataset_revision} · {job.request.split} ·{" "}
              {job.evaluator_id}/{job.evaluator_version}
            </p>
          </div>
          <JobStatusBadge status={job.status} />
        </div>
        <code data-testid="detection-evaluation-id">{job.id}</code>
        <div className="dataset-actions">
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
                ? "Cancel queued evaluation"
                : "Request evaluation cancellation"}
            </button>
          )}
          {job.download_url && (
            <DownloadLink
              href={job.download_url}
              download
              className="button secondary"
            >
              <ArrowDownToLine size={14} />
              Download evaluation manifest
            </DownloadLink>
          )}
        </div>
        {job.status === "cancel_requested" && (
          <p className="dataset-scope">
            Cancellation requested. Waiting for the evaluation child to stop and
            the worker to record an outcome.
          </p>
        )}
        {job.outcome_uncertain && (
          <p className="inline-error" role="alert">
            Evaluation ownership was interrupted. No automatic retry is
            scheduled. Inspect the recorded execution boundary.
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
        <div className="evaluation-source-context">
          <strong>Captured prediction configuration</strong>
          <span>
            {first
              ? `${first.model_name} r${first.model_revision} · ${first.runtime_id}/${first.runtime_version}`
              : "No source job descriptor"}
          </span>
          {first && (
            <span>
              Inference score ≥ {first.request.score_threshold} · NMS IoU{" "}
              {first.request.iou_threshold} · max detections{" "}
              {first.request.max_detections} · seed {first.request.seed}
            </span>
          )}
          <span>
            Additional evaluation score ≥ {job.request.score_threshold} ·
            diagnostic IoU {job.request.diagnostic_iou.toFixed(2)}
          </span>
        </div>
      </section>
      {job.report ? (
        <EvaluationReportView job={job} />
      ) : (
        <section className="panel evaluation-no-report">
          <h3>No evaluation report has been published</h3>
          <p>
            {job.evaluation_input?.rows.length ??
              job.dataset_snapshot.images.filter(
                (image) => image.split === job.request.split,
              ).length}{" "}
            selected images remain in the captured input. Pending, cancelled,
            interrupted, or failed execution is not an aggregate metric result.
          </p>
        </section>
      )}
      <section className="panel evaluation-manifest">
        <div className="dataset-hash">
          <span>Input snapshot hash</span>
          <code>{job.input_hash}</code>
        </div>
        {job.manifest_hash && (
          <div className="dataset-hash">
            <span>Terminal manifest SHA-256</span>
            <code>{job.manifest_hash}</code>
          </div>
        )}
        <details className="dataset-details">
          <summary>Class mapping, source exclusions & evaluator policy</summary>
          <pre>
            {JSON.stringify(
              {
                request: job.request,
                source_context: job.source_context,
                evaluator: job.evaluator_snapshot,
                report_parameters: job.report?.parameters,
              },
              null,
              2,
            )}
          </pre>
        </details>
        <details className="dataset-details">
          <summary>
            Execution events · {job.events.length}
            {job.events_total > job.events.length
              ? ` of ${job.events_total}`
              : ""}
          </summary>
          <ol className="evaluation-events">
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
