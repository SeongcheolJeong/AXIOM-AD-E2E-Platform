import {
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  Filter,
  ListOrdered,
} from "lucide-react";
import { SearchField } from "../ui";
import { formatDate } from "../../api";
import JobStatusBadge from "./JobStatusBadge";
import { elapsedTime } from "./JobTime";
import { jobStatusLabels, type JobStatus, type JobSummary } from "./JobTypes";

export default function JobList({
  jobs,
  selectedId,
  status,
  query,
  now,
  loading,
  hasOlder,
  hasNewer,
  onStatus,
  onQuery,
  onSelect,
  onOlder,
  onNewer,
  onNewest,
}: {
  jobs: JobSummary[];
  selectedId: string | null;
  status: JobStatus | "all";
  query: string;
  now: number;
  loading: boolean;
  hasOlder: boolean;
  hasNewer: boolean;
  onStatus: (status: JobStatus | "all") => void;
  onQuery: (query: string) => void;
  onSelect: (id: string) => void;
  onOlder: () => void;
  onNewer: () => void;
  onNewest: () => void;
}) {
  const visible = jobs.filter((job) =>
    `${job.id} ${job.scenario_name} ${job.adapter_id}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section
      className="panel jobs-list-panel"
      aria-label="Execution submissions"
    >
      <div className="job-panel-heading">
        <ListOrdered size={17} />
        <h3>Submissions</h3>
        <span>{jobs.length} on this page</span>
      </div>
      <div className="jobs-list-filters">
        <label>
          <Filter size={14} />
          <span>Status</span>
          <select
            aria-label="Filter jobs by status"
            value={status}
            onChange={(event) =>
              onStatus(event.target.value as JobStatus | "all")
            }
          >
            <option value="all">All statuses</option>
            {Object.entries(jobStatusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <SearchField
          value={query}
          onChange={onQuery}
          placeholder="Search jobs on this page…"
        />
      </div>
      {hasNewer && (
        <div className="jobs-older-notice">
          <span>Browsing older submissions</span>
          <button type="button" className="job-text-button" onClick={onNewest}>
            Newest page
          </button>
        </div>
      )}
      <div className="jobs-submission-list">
        {visible.map((job) => (
          <button
            type="button"
            key={job.id}
            className={`job-list-item ${selectedId === job.id ? "active" : ""}`}
            aria-label={`Inspect job ${job.id}`}
            aria-pressed={selectedId === job.id}
            onClick={() => onSelect(job.id)}
          >
            <div className="job-list-meta">
              <span className="mono">#{job.submission_seq}</span>
              <JobStatusBadge status={job.status} />
            </div>
            <div className="job-list-title">
              <strong>{job.scenario_name}</strong>
              <ChevronRight size={15} />
            </div>
            <p>
              {job.adapter_id} · r{job.scenario_revision}
            </p>
            <div className="job-list-footer">
              <time dateTime={job.created_at}>
                {formatDate(job.created_at)}
              </time>
              <span>
                {job.finished_at
                  ? `${elapsedTime(job.created_at, job.finished_at)} total`
                  : `${elapsedTime(job.created_at, now)} since submit`}
              </span>
            </div>
            {job.outcome_uncertain && (
              <span className="job-list-uncertain">Outcome uncertain</span>
            )}
          </button>
        ))}
        {!visible.length && !loading && (
          <div className="jobs-list-empty">
            <ListOrdered size={24} />
            <strong>
              {query
                ? "No matching jobs on this page"
                : "No submissions in this view"}
            </strong>
            <p>
              {query
                ? "Adjust the search or browse another page."
                : "Submit a scenario or choose another status filter."}
            </p>
          </div>
        )}
        {loading && !jobs.length && (
          <p className="jobs-list-loading" role="status">
            Loading submissions…
          </p>
        )}
      </div>
      <div className="jobs-list-pagination">
        <button
          type="button"
          className="button secondary small"
          disabled={!hasNewer || loading}
          onClick={onNewer}
        >
          <ArrowLeft size={14} />
          Newer jobs
        </button>
        <button
          type="button"
          className="button secondary small"
          disabled={!hasOlder || loading}
          onClick={onOlder}
        >
          Older jobs
          <ArrowRight size={14} />
        </button>
      </div>
    </section>
  );
}
