import { AlertTriangle, ArrowUpRight } from "lucide-react";
import type { ExecutionResource } from "./JobTypes";

export default function ResourceNotices({
  resources,
  error,
  onSelectJob,
}: {
  resources: ExecutionResource[] | null;
  error: string;
  onSelectJob: (id: string) => void;
}) {
  const blocked =
    resources?.filter((resource) => resource.state === "reconcile_required") ??
    [];
  return (
    <>
      {error && (
        <p className="jobs-stale-note" role="status">
          <AlertTriangle size={15} />
          Execution-resource refresh failed.{" "}
          {resources
            ? "Showing the last observation. "
            : "Resource ownership is unknown. "}
          {error}
        </p>
      )}
      {blocked.map((resource) => (
        <section
          key={resource.adapter_id}
          className="jobs-resource-notice"
          aria-label={`Cleanup required for ${resource.adapter_id}`}
        >
          <AlertTriangle size={19} />
          <div>
            <strong>
              {resource.adapter_id} · cleanup confirmation required
            </strong>
            <p>
              Queued work for this adapter is waiting. An operator must confirm
              the former dispatcher stopped and the external engine was cleaned
              up before explicitly reconciling the resource.
            </p>
            {resource.note && (
              <p className="jobs-resource-note">{resource.note}</p>
            )}
            <div>
              {resource.job_id && (
                <button
                  type="button"
                  className="job-text-button"
                  onClick={() => onSelectJob(resource.job_id!)}
                >
                  Inspect interrupted job
                </button>
              )}
              <a href="/docs" target="_blank" rel="noreferrer">
                API documentation
                <ArrowUpRight size={13} />
              </a>
            </div>
          </div>
        </section>
      ))}
    </>
  );
}
