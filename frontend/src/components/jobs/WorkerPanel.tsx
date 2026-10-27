import { AlertCircle, Cpu, Radio, ServerOff } from "lucide-react";
import { elapsedTime, preciseJobTime } from "./JobTime";
import type { WorkerSummary } from "./JobTypes";

export default function WorkerPanel({
  workers,
  loading,
  error,
  now,
  onSelectJob,
}: {
  workers: WorkerSummary[] | null;
  loading: boolean;
  error: string;
  now: number;
  onSelectJob: (id: string) => void;
}) {
  const alive = workers?.filter((worker) => worker.alive).length ?? 0;
  return (
    <section className="panel jobs-workers" aria-label="Execution workers">
      <div className="jobs-workers-heading">
        <Cpu size={17} />
        <h3>Workers</h3>
        <span>
          {workers
            ? `${alive} reporting alive`
            : loading
              ? "Reading heartbeats…"
              : "Status unavailable"}
        </span>
        <span className="jobs-worker-scope">Heartbeat status from the API</span>
      </div>
      {error && (
        <p className="jobs-stale-note" role="status">
          <AlertCircle size={14} />
          Worker refresh failed.{" "}
          {workers ? "Showing the last observation. " : ""}
          {error}
        </p>
      )}
      {workers && !workers.length ? (
        <p className="jobs-no-workers">
          <ServerOff size={17} />
          No worker has registered. Queued jobs wait for an execution worker.
        </p>
      ) : (
        workers && (
          <div className="jobs-worker-list">
            {workers.map((worker) => (
              <div
                key={worker.id}
                className={`jobs-worker ${worker.alive ? "alive" : "stale"}`}
              >
                <Radio size={16} />
                <div>
                  <strong className="mono" title={worker.id}>
                    {worker.id}
                  </strong>
                  <span title={preciseJobTime(worker.heartbeat_at)}>
                    {worker.alive
                      ? worker.state
                      : `${worker.state} · not reporting alive`}{" "}
                    · observed {elapsedTime(worker.heartbeat_at, now)} ago
                  </span>
                </div>
                {worker.job_id && (
                  <button
                    type="button"
                    className="job-text-button"
                    onClick={() => onSelectJob(worker.job_id!)}
                  >
                    View job
                  </button>
                )}
              </div>
            ))}
          </div>
        )
      )}
      {workers && workers.length > 0 && alive === 0 && (
        <p className="jobs-no-workers">
          <AlertCircle size={15} />
          No worker currently reports alive. A stale heartbeat does not
          establish whether a dispatched operation stopped.
        </p>
      )}
    </section>
  );
}
