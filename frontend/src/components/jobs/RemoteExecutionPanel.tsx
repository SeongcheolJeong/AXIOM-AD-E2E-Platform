import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArchiveRestore,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";
import { api } from "../../api";
import { Loading } from "../ui";
import { activeJobStatuses, type JobDetail } from "./JobTypes";
import type {
  RecoveryDetail,
  RecoveryPage,
  RemoteExecutionInspection,
} from "./RemoteExecutionTypes";
import { useJobResource } from "./useJobResource";
import { preciseJobTime } from "./JobTime";
import RemoteStatusDetails from "./RemoteStatusDetails";
import RecoveryOperation from "./RecoveryOperation";
import "./remote-recovery.css";

function validateInspection(
  value: Pick<RemoteExecutionInspection, "source_job_id" | "source_run_id" | "source_execution_id" | "binding" | "status">,
  job: JobDetail,
) {
  if (
    value.source_job_id !== job.id ||
    (value.source_run_id !== null &&
      job.run_id !== null &&
      value.source_run_id !== job.run_id)
  )
    throw new Error("Remote inspection refers to another source job or run.");
  const binding = value.binding,
    status = value.status;
  if (binding) {
    const attempt = binding.attempt;
    if (
      attempt.schema_version !== "execution-attempt/1" ||
      attempt.execution_id !== value.source_execution_id ||
      attempt.input_hash !== job.input_hash ||
      attempt.adapter_id !== job.adapter_id ||
      attempt.adapter_version !== job.adapter_version ||
      attempt.context?.context_hash !== job.execution_context?.context_hash
    )
      throw new Error(
        "Remote attempt differs from this job's captured inputs.",
      );
  }
  if (
    status &&
    (!binding ||
      status.schema_version !== "remote-execution-status/1" ||
      status.execution_id !== value.source_execution_id ||
      status.store_id !== binding.store_id ||
      status.request_hash !== binding.request_hash ||
      status.input_hash !== job.input_hash ||
      status.adapter_id !== job.adapter_id ||
      status.adapter_version !== job.adapter_version ||
      status.context_hash !== (job.execution_context?.context_hash ?? null))
  )
    throw new Error(
      "Remote status identity differs from the pinned execution.",
    );
  return value;
}

/** Persist only the random key under a digest; notes are not browser storage. */
async function recoveryKey(body: string) {
  const hash = Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body)),
    ),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  const storageKey = `axiom.recovery-key:${hash}`;
  let key = sessionStorage.getItem(storageKey);
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem(storageKey, key);
  }
  return key;
}

export default function RemoteExecutionPanel({
  job,
  onOpenRun,
  onPublished,
}: {
  job: JobDetail;
  onOpenRun: (id: string) => void;
  onPublished: () => void;
}) {
  const [inspection, setInspection] =
      useState<RemoteExecutionInspection | null>(null),
    [inspectError, setInspectError] = useState(""),
    [inspecting, setInspecting] = useState(false),
    [note, setNote] = useState(""),
    [submitting, setSubmitting] = useState(false),
    [submitError, setSubmitError] = useState(""),
    [selected, setSelected] = useState<string | null>(null),
    [before, setBefore] = useState<number | null>(null),
    [pages, setPages] = useState<(number | null)[]>([]);
  const requests = useRef(new Set<AbortController>());
  const seenPublications = useRef(new Set<string>());
  const listPath = `/execution-recoveries?source_job_id=${encodeURIComponent(job.id)}&limit=20${before === null ? "" : `&before_seq=${before}`}`;
  const history = useJobResource<RecoveryPage>(listPath, 5000);
  const operation = useJobResource<RecoveryDetail>(
    selected ? `/execution-recoveries/${encodeURIComponent(selected)}` : null,
  );
  const candidate =
    operation.data?.id === selected && operation.data.source_job_id === job.id
      ? operation.data
      : null;
  let detailIdentityError = "";
  if (candidate) {
    try { validateInspection({ ...candidate, status: candidate.remote_status }, job); }
    catch (reason) { detailIdentityError = reason instanceof Error ? reason.message : "Recovery identity is invalid."; }
  }
  const detail = detailIdentityError ? null : candidate;
  const rows =
    history.data?.items.filter((item) => item.source_job_id === job.id) ?? [];
  const historyWrong = !!history.data?.items.some(
    (item) => item.source_job_id !== job.id,
  );
  const canRetrieve =
    inspection?.supported && inspection.can_retrieve && inspection.binding && inspection.source_execution_id &&
    !inspectError && !inspecting && !submitting;
  useEffect(
    () => () => {
      requests.current.forEach((request) => request.abort());
    },
    [job.id],
  );
  useEffect(() => {
    if (!selected && rows[0]) setSelected(rows[0].id);
  }, [selected, rows]);
  useEffect(() => {
    const published = [...rows, ...(detail ? [detail] : [])].filter(
      (item) => item.run_id && !activeJobStatuses.has(item.status),
    );
    for (const item of published) {
      const key = `${item.id}:${item.run_id}`;
      if (!seenPublications.current.has(key)) {
        seenPublications.current.add(key);
        onPublished();
      }
    }
  }, [rows, detail, onPublished]);
  async function inspect() {
    const request = new AbortController();
    requests.current.add(request);
    setInspecting(true);
    setInspectError("");
    try {
      const result = await api<RemoteExecutionInspection>(
        `/jobs/${encodeURIComponent(job.id)}/remote-execution`,
        { signal: request.signal, cache: "no-store" },
      );
      validateInspection(result, job);
      if (!request.signal.aborted) setInspection(result);
    } catch (reason) {
      if (!request.signal.aborted)
        setInspectError(
          reason instanceof Error
            ? reason.message
            : "Remote status could not be inspected.",
        );
    } finally {
      requests.current.delete(request);
      if (!request.signal.aborted) setInspecting(false);
    }
  }
  async function retrieve(event: FormEvent) {
    event.preventDefault();
    if (!canRetrieve || !note.trim()) return;
    const request = new AbortController();
    requests.current.add(request);
    setSubmitting(true);
    setSubmitError("");
    const body = JSON.stringify({ source_job_id: job.id, note: note.trim() });
    try {
      const key = await recoveryKey(body);
      if (request.signal.aborted) return;
      const result = await api<RecoveryDetail>("/execution-recoveries", {
        method: "POST",
        body,
        signal: request.signal,
        headers: { "Idempotency-Key": key },
      });
      validateInspection({ ...result, status: result.remote_status }, job);
      if (
        result.source_job_id !== job.id ||
        result.source_execution_id !== inspection?.source_execution_id
      )
        throw new Error(
          "Recovery response refers to another source execution.",
        );
      if (!request.signal.aborted) {
        setSelected(result.id);
        setBefore(null);
        setPages([]);
        history.reload();
        operation.reload();
      }
    } catch (reason) {
      if (!request.signal.aborted)
        setSubmitError(
          reason instanceof Error
            ? reason.message
            : "Recovery submission failed.",
        );
    } finally {
      requests.current.delete(request);
      if (!request.signal.aborted) setSubmitting(false);
    }
  }
  return (
    <section
      className="remote-outcome-panel"
      aria-label="Remote execution outcome"
      data-testid="remote-outcome-panel"
    >
      <header className="remote-outcome-heading">
        <ArchiveRestore size={19} />
        <h3>Remote outcome & evidence recovery</h3>
      </header>
      <p className="remote-outcome-scope">
        Inspect the original execution ID and retrieve sealed evidence. This
        does not resubmit the simulation or replace its original local history.
      </p>
      <div className="remote-outcome-actions">
        <button
          type="button"
          className="button secondary"
          disabled={inspecting || submitting}
          onClick={inspect}
        >
          {inspecting ? (
            <LoaderCircle size={15} className="spin" />
          ) : inspection ? (
            <RefreshCw size={15} />
          ) : (
            <Search size={15} />
          )}
          {inspecting
            ? "Inspecting remote status…"
            : inspection
              ? "Refresh remote status"
              : "Inspect remote execution"}
        </button>
      </div>
      {!inspection && !inspectError && (
        <p className="remote-outcome-scope">
          No remote status has been checked in this view. Availability is
          checked against this submission's saved adapter and remote binding.
        </p>
      )}
      {inspectError && (
        <p className="remote-outcome-notice" role="alert">
          {inspection
            ? "Refresh failed. The last successful remote observation below is stale; retrieve is disabled. "
            : "Remote inspection unavailable. "}
          {inspectError}
        </p>
      )}
      {inspection && (
        <div data-testid="remote-inspection" aria-busy={inspecting}>
          {inspection.reason && (
            <p className="remote-outcome-notice">{inspection.reason}</p>
          )}
          <p className="remote-outcome-scope">
            Recovery capability ·{" "}
            {inspection.supported
              ? "Supported by the configured adapter"
              : "Unavailable for this submission"}
          </p>
          {inspection.status ? (
            <RemoteStatusDetails status={inspection.status} />
          ) : (
            <p>No correlated remote execution status is available.</p>
          )}
          {inspection.recovered_run_id && (
            <button
              type="button"
              className="button secondary"
              onClick={() => onOpenRun(inspection.recovered_run_id!)}
            >
              Open previously recovered evidence
            </button>
          )}
          {inspection.can_retrieve && (
            <form className="remote-recovery-form" onSubmit={retrieve}>
              <label htmlFor={`recovery-note-${job.id}`}>Recovery note</label>
              <textarea
                id={`recovery-note-${job.id}`}
                aria-label="Recovery note"
                maxLength={1000}
                required
                rows={2}
                value={note}
                disabled={submitting}
                onChange={(event) => {
                  setNote(event.target.value);
                  setSubmitError("");
                }}
                placeholder="Why this execution's original evidence is being retrieved"
              />
              <div className="remote-outcome-actions">
                <button
                  type="submit"
                  className="button primary"
                  disabled={!canRetrieve || !note.trim()}
                >
                  {submitting ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <ArchiveRestore size={15} />
                  )}
                  {submitting
                    ? "Submitting retrieval…"
                    : submitError
                      ? "Retry evidence retrieval"
                      : "Retrieve evidence"}
                </button>
              </div>
              {submitError && (
                <p className="inline-error" role="alert">
                  {submitError} Retry with the same note reuses the original
                  request key.
                </p>
              )}
            </form>
          )}
        </div>
      )}
      <p className="remote-outcome-notice">
        Evidence recovery does not release an execution-resource quarantine.
        Engine health and a remote cleanup report do not replace the workspace's
        explicit cleanup confirmation.
      </p>
      <section
        className="remote-recovery-history"
        aria-label="Evidence recovery history"
      >
        <h4>Recovery history</h4>
        {history.error && (
          <p className="inline-error" role="alert">
            Recovery history refresh failed.{" "}
            {history.data ? "Showing the last successful local history. " : ""}
            {history.error}
          </p>
        )}
        {historyWrong && (
          <p className="inline-error" role="alert">
            Recovery history contains another source job and was rejected.
          </p>
        )}
        {!historyWrong && rows.length > 0 && (
          <ol>
            {rows.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="job-text-button"
                  aria-label={`Inspect recovery ${item.id}`}
                  aria-pressed={selected === item.id}
                  onClick={() => setSelected(item.id)}
                >
                  #{item.submission_seq} · {item.status.replaceAll("_", " ")} ·{" "}
                  {item.outcome.replaceAll("_", " ")} ·{" "}
                  {preciseJobTime(item.created_at)}
                </button>
              </li>
            ))}
          </ol>
        )}
        {!history.loading &&
          !history.error &&
          !historyWrong &&
          !rows.length && <p>No recovery requests on this page.</p>}
        {(pages.length > 0 ||
          (history.data?.next_before_seq !== null &&
            history.data?.next_before_seq !== undefined)) && (
          <div className="remote-outcome-actions">
            <button
              type="button"
              className="button secondary small"
              disabled={!pages.length}
              onClick={() => {
                setBefore(pages.at(-1) ?? null);
                setPages((value) => value.slice(0, -1));
              }}
            >
              <ArrowLeft size={14} />
              Newer recoveries
            </button>
            <button
              type="button"
              className="button secondary small"
              disabled={history.data?.next_before_seq == null}
              onClick={() => {
                setPages((value) => [...value, before]);
                setBefore(history.data?.next_before_seq ?? null);
              }}
            >
              <ArrowRight size={14} />
              Older recoveries
            </button>
          </div>
        )}
        {operation.error && (
          <p className="inline-error" role="alert">
            Recovery detail refresh failed.{" "}
            {detail ? "Showing the last observation for this recovery. " : ""}
            {operation.error}
          </p>
        )}
        {operation.data && !detail && (
          <p className="inline-error" role="alert">
            Recovery detail identity does not match this selection. {detailIdentityError}
          </p>
        )}
        {operation.loading && !detail && (
          <Loading label="Loading selected recovery…" />
        )}
        {detail && (
          <RecoveryOperation
            key={detail.id}
            recovery={detail}
            onOpenRun={onOpenRun}
            onChanged={() => {
              operation.reload();
              history.reload();
            }}
          />
        )}
      </section>
    </section>
  );
}
