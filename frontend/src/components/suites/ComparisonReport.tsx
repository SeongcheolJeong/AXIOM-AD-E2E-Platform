import { useState } from "react";
import { ArrowUpRight, GitCompareArrows } from "lucide-react";
import { metricLabels } from "../../types";
import { suiteNumber } from "./SuiteCommon";
import { EvidenceBadge } from "./CampaignReport";
import {
  comparisonLabels,
  type CampaignComparison,
  type ComparisonStatus,
} from "./SuiteTypes";

function comparisonMetricValue(name: string, value: number | null) {
  // Comparison entries include only present metrics. A present null TTC
  // represents an unbounded value; other null metrics remain unavailable.
  return name === "min_ttc_s" && value === null
    ? "Unbounded"
    : suiteNumber(value);
}

export default function ComparisonReport({
  report,
  baselineLabel,
  candidateLabel,
  baselineRevision,
  candidateRevision,
  frozen = false,
  onOpenRun,
  onOpenJob,
}: {
  report: CampaignComparison;
  baselineLabel: string;
  candidateLabel: string;
  baselineRevision: number;
  candidateRevision: number;
  frozen?: boolean;
  onOpenRun: (id: string) => void;
  onOpenJob: (id: string) => void;
}) {
  const [status, setStatus] = useState<ComparisonStatus | "all">("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const rows = report.rows.filter(
    (entry) =>
      (status === "all" || entry.status === status) &&
      (!selectedKey || entry.key === selectedKey),
  );
  return (
    <section
      className="panel suite-comparison-report"
      aria-label={frozen ? "Saved comparison report" : "Live comparison report"}
      data-testid={frozen ? "saved-comparison-report" : "comparison-report"}
    >
      <div className="suite-panel-heading">
        <GitCompareArrows size={18} />
        <h3>{frozen ? "Captured comparison" : "Live comparison"}</h3>
        <span>{report.counts.total} case keys in the union</span>
      </div>
      <div className="suite-comparison-sides">
        <div>
          <span>BASELINE</span>
          <strong>{baselineLabel}</strong>
          <small>
            Suite r{baselineRevision} · {report.baseline_id}
          </small>
        </div>
        <GitCompareArrows size={21} />
        <div>
          <span>CANDIDATE</span>
          <strong>{candidateLabel}</strong>
          <small>
            Suite r{candidateRevision} · {report.campaign_id}
          </small>
        </div>
      </div>
      <p className="suite-comparison-boundary">
        {frozen
          ? "This is the stored comparison observation."
          : "This report is evaluated from the selected campaign now; it is not an immutable capture."}{" "}
        Conditions must match before improvement or regression is assigned.
        Missing evidence stays unevaluated.
      </p>
      <div className="suite-comparison-counts">
        {Object.entries(comparisonLabels).map(([key, label]) => (
          <button
            type="button"
            key={key}
            className={`suite-comparison-count ${key} ${status === key ? "active" : ""}`}
            aria-pressed={status === key}
            onClick={() => {
              setStatus(status === key ? "all" : (key as ComparisonStatus));
              setSelectedKey(null);
            }}
          >
            <strong>{report.counts[key as ComparisonStatus]}</strong>
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div
        className="suite-comparison-matrix"
        aria-label="Case comparison matrix"
      >
        {report.rows.map((entry) => (
          <button
            type="button"
            key={entry.key}
            className={`${entry.status} ${selectedKey === entry.key ? "active" : ""}`}
            aria-label={`${entry.key}: ${comparisonLabels[entry.status]}`}
            aria-pressed={selectedKey === entry.key}
            title={`${entry.name} · ${comparisonLabels[entry.status]}`}
            onClick={() => {
              setSelectedKey(selectedKey === entry.key ? null : entry.key);
              setStatus("all");
            }}
          >
            {entry.key}
          </button>
        ))}
      </div>
      <div className="suite-comparison-filter">
        <label>
          Comparison status
          <select
            aria-label="Filter comparison status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as ComparisonStatus | "all");
              setSelectedKey(null);
            }}
          >
            <option value="all">All classifications</option>
            {Object.entries(comparisonLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <span>
          {rows.length} / {report.counts.total} rows shown
        </span>
        {selectedKey && (
          <button
            type="button"
            className="suite-text-button"
            onClick={() => setSelectedKey(null)}
          >
            Clear case selection
          </button>
        )}
      </div>
      <div className="suite-table-scroll">
        <table className="suite-comparison-table">
          <thead>
            <tr>
              <th>CASE</th>
              <th>BASELINE</th>
              <th>CANDIDATE</th>
              <th>CLASSIFICATION / CONDITIONS</th>
              <th>RECORDED METRICS · Δ CANDIDATE − BASELINE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <tr key={entry.key} data-testid={`comparison-row-${entry.key}`}>
                <td>
                  <code>{entry.key}</code>
                  <strong>{entry.name}</strong>
                </td>
                <td>
                  <EvidenceBadge
                    status={entry.baseline_verdict ?? "missing"}
                    label={
                      entry.baseline_verdict?.replaceAll("_", " ") ?? "No case"
                    }
                  />
                  <small>
                    Adapter {entry.baseline_adapter_version ?? "unknown"}
                  </small>
                  <div className="suite-comparison-links">
                    {entry.baseline_run_id && (
                      <button
                        type="button"
                        className="suite-text-button"
                        aria-label={`Open baseline result for ${entry.key}`}
                        onClick={() => onOpenRun(entry.baseline_run_id!)}
                      >
                        Result
                        <ArrowUpRight size={12} />
                      </button>
                    )}
                    {entry.baseline_job_id && (
                      <button
                        type="button"
                        className="suite-text-button"
                        aria-label={`Open baseline job for ${entry.key}`}
                        onClick={() => onOpenJob(entry.baseline_job_id!)}
                      >
                        Job
                      </button>
                    )}
                  </div>
                </td>
                <td>
                  <EvidenceBadge
                    status={entry.current_verdict ?? "missing"}
                    label={
                      entry.current_verdict?.replaceAll("_", " ") ?? "No case"
                    }
                  />
                  <small>
                    Adapter {entry.current_adapter_version ?? "unknown"}
                  </small>
                  <div className="suite-comparison-links">
                    {entry.current_run_id && (
                      <button
                        type="button"
                        className="suite-text-button"
                        aria-label={`Open candidate result for ${entry.key}`}
                        onClick={() => onOpenRun(entry.current_run_id!)}
                      >
                        Result
                        <ArrowUpRight size={12} />
                      </button>
                    )}
                    {entry.current_job_id && (
                      <button
                        type="button"
                        className="suite-text-button"
                        aria-label={`Open candidate job for ${entry.key}`}
                        onClick={() => onOpenJob(entry.current_job_id!)}
                      >
                        Job
                      </button>
                    )}
                  </div>
                </td>
                <td>
                  <EvidenceBadge
                    status={entry.status}
                    label={comparisonLabels[entry.status]}
                  />
                  {entry.adapter_version_changed && (
                    <p>Adapter version changed.</p>
                  )}
                  {entry.reasons.map((reason, index) => (
                    <p key={index}>{reason}</p>
                  ))}
                </td>
                <td>
                  {entry.metrics.length ? (
                    <div className="suite-metric-deltas">
                      {entry.metrics.map((metric) => (
                        <div key={metric.metric_name}>
                          <strong>
                            {metricLabels[metric.metric_name] ??
                              metric.metric_name}
                          </strong>
                          <span>
                            {comparisonMetricValue(
                              metric.metric_name,
                              metric.baseline_value,
                            )}{" "}
                            →{" "}
                            {comparisonMetricValue(
                              metric.metric_name,
                              metric.current_value,
                            )}{" "}
                            {metric.unit ?? ""}
                          </span>
                          <code>
                            Δ{" "}
                            {metric.delta === null
                              ? "—"
                              : `${metric.delta > 0 ? "+" : ""}${suiteNumber(metric.delta)}`}{" "}
                            {metric.delta === null ? "" : (metric.unit ?? "")}
                          </code>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <span className="suite-form-help">
                      No comparable finite metric values.
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <p className="suite-inline-empty">
          No comparison rows match this filter.
        </p>
      )}
      <p className="suite-panel-footnote">
        Numeric deltas require finite values in equal units. An unbounded TTC
        has no numeric delta. Baseline manifest SHA-256:{" "}
        <code>{report.baseline_manifest_hash}</code>
      </p>
    </section>
  );
}
