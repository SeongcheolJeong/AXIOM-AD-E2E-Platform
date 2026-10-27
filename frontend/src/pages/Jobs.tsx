import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  CircleDot,
  Clock3,
  ListOrdered,
  LoaderCircle,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Empty, ErrorState, Loading, PageHeading } from "../components/ui";
import JobInspector from "../components/jobs/JobInspector";
import JobList from "../components/jobs/JobList";
import WorkerPanel from "../components/jobs/WorkerPanel";
import ResourceNotices from "../components/jobs/ResourceNotices";
import {
  activeJobStatuses,
  type ExecutionResource,
  type JobDetail,
  type JobsResponse,
  type JobStatus,
  type WorkerSummary,
} from "../components/jobs/JobTypes";
import { useJobResource } from "../components/jobs/useJobResource";
import { elapsedTime, useJobClock } from "../components/jobs/JobTime";
import "../jobs.css";

export default function Jobs({
  selectedId,
  onSelect,
  onOpenRun,
  onLaunch,
  onSettled,
}: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenRun: (id: string) => void;
  onLaunch: () => void;
  onSettled: () => void;
}) {
  const [status, setStatus] = useState<JobStatus | "all">("all");
  const [query, setQuery] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const [history, setHistory] = useState<(number | null)[]>([]);
  const now = useJobClock();
  const parameters = new URLSearchParams({ limit: "100" });
  if (status !== "all") parameters.set("status", status);
  if (before !== null) parameters.set("before_seq", String(before));
  const jobs = useJobResource<JobsResponse>(`/jobs?${parameters}`);
  const detail = useJobResource<JobDetail>(
    selectedId ? `/jobs/${encodeURIComponent(selectedId)}` : null,
  );
  const workers = useJobResource<WorkerSummary[]>("/workers", 5000);
  const resources = useJobResource<ExecutionResource[]>(
    "/execution-resources",
    5000,
  );
  const observedTerminal = useRef(new Set<string>());
  useEffect(() => {
    if (!selectedId && jobs.data?.items[0]) onSelect(jobs.data.items[0].id);
  }, [selectedId, jobs.data, onSelect]);
  useEffect(() => {
    const job = detail.data;
    if (!job || activeJobStatuses.has(job.status)) return;
    const observation = `${job.id}:${job.status}`;
    if (observedTerminal.current.has(observation)) return;
    observedTerminal.current.add(observation);
    onSettled();
  }, [detail.data, onSettled]);
  const refresh = useCallback(() => {
    jobs.reload();
    detail.reload();
    workers.reload();
    resources.reload();
  }, [jobs.reload, detail.reload, workers.reload, resources.reload]);
  const rows = jobs.data?.items ?? [];
  const counts = {
    queued: rows.filter((job) => job.status === "queued").length,
    active: rows.filter(
      (job) => job.status === "running" || job.status === "cancel_requested",
    ).length,
    completed: rows.filter((job) => job.status === "completed").length,
    attention: rows.filter(
      (job) =>
        job.status === "failed" ||
        job.status === "interrupted" ||
        job.outcome_uncertain,
    ).length,
  };
  function resetPage() {
    setBefore(null);
    setHistory([]);
  }
  return (
    <div className="execution-jobs-page">
      <PageHeading
        eyebrow="EXECUTION WITH OWNERSHIP"
        title="Execution queue"
        description="Submit once. Follow worker state, lifecycle events, and recorded results."
        actions={
          <>
            <button
              type="button"
              className="button secondary"
              aria-label="Refresh execution queue"
              onClick={refresh}
            >
              <RefreshCw size={15} />
              Refresh
            </button>
            <button type="button" className="button primary" onClick={onLaunch}>
              <Plus size={16} />
              New execution
            </button>
          </>
        }
      />
      <div className="jobs-observation-line">
        <span>
          {jobs.refreshing ? (
            <LoaderCircle size={13} className="spin" />
          ) : (
            <CircleDot size={13} />
          )}
          {jobs.error
            ? "Queue refresh unavailable"
            : jobs.fetchedAt
              ? `Observed ${elapsedTime(new Date(jobs.fetchedAt).toISOString(), now)} ago · polling every 3 s`
              : "Reading durable execution state…"}
        </span>
        <span>
          Counts below describe this page of {rows.length} submissions
        </span>
      </div>
      <div className="jobs-page-counts" aria-label="Current page job counts">
        {[
          {
            label: "Queued",
            value: counts.queued,
            icon: ListOrdered,
            className: "queued",
          },
          {
            label: "In progress",
            value: counts.active,
            icon: Clock3,
            className: "running",
          },
          {
            label: "Completed",
            value: counts.completed,
            icon: CheckCircle2,
            className: "completed",
          },
          {
            label: "Needs review",
            value: counts.attention,
            icon: AlertCircle,
            className: "attention",
          },
        ].map((item) => (
          <div key={item.label} className={item.className}>
            <item.icon size={17} />
            <span>{item.label}</span>
            <strong>{jobs.data ? item.value : "—"}</strong>
          </div>
        ))}
      </div>
      <ResourceNotices
        resources={resources.data}
        error={resources.error}
        onSelectJob={onSelect}
      />
      <WorkerPanel
        workers={workers.data}
        loading={workers.loading}
        error={workers.error}
        now={now}
        onSelectJob={onSelect}
      />
      {jobs.error && (
        <div className="jobs-stale-note" role="status">
          <AlertCircle size={15} />
          <span>
            {jobs.data
              ? "Queue refresh failed. The list is the last successful observation. "
              : "Could not read the queue. "}
            {jobs.error}
          </span>
          <button
            type="button"
            className="job-text-button"
            onClick={jobs.reload}
          >
            Retry queue
          </button>
        </div>
      )}
      <div className="jobs-explorer-grid">
        <JobList
          jobs={rows}
          selectedId={selectedId}
          status={status}
          query={query}
          now={now}
          loading={jobs.loading}
          hasOlder={
            jobs.data?.next_before_seq !== null &&
            jobs.data?.next_before_seq !== undefined
          }
          hasNewer={history.length > 0}
          onStatus={(next) => {
            setStatus(next);
            resetPage();
            onSelect(null);
          }}
          onQuery={setQuery}
          onSelect={onSelect}
          onOlder={() => {
            const next = jobs.data?.next_before_seq;
            if (next === null || next === undefined) return;
            setHistory((entries) => [...entries, before]);
            setBefore(next);
          }}
          onNewer={() => {
            if (!history.length) return;
            setBefore(history.at(-1) ?? null);
            setHistory((entries) => entries.slice(0, -1));
          }}
          onNewest={resetPage}
        />
        <div className="jobs-explorer-detail">
          {detail.error && detail.data && (
            <p className="jobs-stale-note" role="status">
              <AlertCircle size={15} />
              Detail refresh failed. Showing the last successful observation for
              this job. {detail.error}
            </p>
          )}
          {detail.error && !detail.data ? (
            <section className="panel">
              <ErrorState message={detail.error} retry={detail.reload} />
            </section>
          ) : detail.loading && !detail.data ? (
            <section className="panel">
              <Loading label="Loading selected execution…" />
            </section>
          ) : detail.data ? (
            <JobInspector
              key={detail.data.id}
              job={detail.data}
              now={now}
              onOpenRun={onOpenRun}
              onChanged={refresh}
              onRecovered={onSettled}
            />
          ) : (
            <section className="panel jobs-empty-detail">
              <Empty
                title="Select an execution"
                description="Every submission records its captured configuration, worker ownership, and lifecycle events."
                action={
                  <button
                    type="button"
                    className="button primary"
                    onClick={onLaunch}
                  >
                    <Plus size={15} />
                    New execution
                  </button>
                }
              />
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
