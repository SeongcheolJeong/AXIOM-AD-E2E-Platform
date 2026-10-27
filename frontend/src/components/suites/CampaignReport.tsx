import { useState } from "react";
import {
  Archive,
  ArrowDownToLine,
  ArrowUpRight,
  Clock3,
  FileJson,
  LoaderCircle,
  Square,
} from "lucide-react";
import { api, formatDate } from "../../api";
import { metricLabels } from "../../types";
import { DownloadLink } from "../ArtifactAccess";
import JobStatusBadge from "../jobs/JobStatusBadge";
import { criterionOperator, suiteNumber } from "./SuiteCommon";
import type { CampaignDetail, CriterionCheck } from "./SuiteTypes";

import CampaignPinnedPlan, {
  CampaignCaseFingerprint,
} from "./CampaignPinnedPlan";

export function EvidenceBadge({
  status,
  label,
}: {
  status: string;
  label?: string;
}) {
  return (
    <span className={`suite-evidence-badge ${status}`}>
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}

export function CheckValue({ check }: { check: CriterionCheck }) {
  return (
    <>
      <strong className="mono">
        {check.unbounded ? "∞" : suiteNumber(check.value)}
      </strong>
      {check.unit && <span>{check.unit}</span>}
      {check.unbounded && <small>unbounded constant-velocity TTC</small>}
      {check.measurement_contract === "carla-observed-contact-count/1" && (
        <small className="suite-contact-boundary">
          Observed contact count criterion · does not prove collision-stream
          completeness or absence of collisions.
        </small>
      )}
    </>
  );
}

export default function CampaignReport({
  campaign,
  frozen = false,
  onOpenJob,
  onOpenRun,
  onCapture,
  onRefresh,
}: {
  campaign: CampaignDetail;
  frozen?: boolean;
  onOpenJob: (id: string) => void;
  onOpenRun: (id: string) => void;
  onCapture?: () => void;
  onRefresh?: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const evaluated = campaign.cases.filter(
    (entry) => entry.evaluation_complete,
  ).length;
  async function cancel() {
    setError("");
    setCancelling(true);
    try {
      await api<CampaignDetail>(
        `/campaigns/${encodeURIComponent(campaign.id)}/cancel`,
        { method: "POST" },
      );
      onRefresh?.();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Campaign cancellation failed.",
      );
    } finally {
      setCancelling(false);
    }
  }
  return (
    <div
      className="suite-campaign-report"
      data-testid={frozen ? "frozen-campaign-report" : "campaign-detail"}
    >
      <section className="panel suite-campaign-header">
        <div className="suite-campaign-title">
          <div>
            <span className="suite-kicker">
              {frozen
                ? "CAPTURED CAMPAIGN"
                : `CAMPAIGN #${campaign.submission_seq}`}
            </span>
            <h2>{campaign.label}</h2>
          </div>
          <div>
            <EvidenceBadge status={campaign.status} />
            <EvidenceBadge
              status={campaign.verdict}
              label={`Verdict: ${campaign.verdict}`}
            />
          </div>
        </div>
        <div className="suite-campaign-meta">
          <code data-testid="campaign-id">{campaign.id}</code>
          <span>Suite r{campaign.suite_revision}</span>
          <span>
            {campaign.adapter_id} · {campaign.adapter_version}
          </span>
          <time>{formatDate(campaign.created_at)}</time>
        </div>
        {!campaign.evaluation_complete && (
          <p className="suite-incomplete-note">
            <Clock3 size={15} />
            Evaluation is incomplete. A known failure can coexist with missing
            or pending checks.
          </p>
        )}
        <div className="suite-campaign-actions">
          {!frozen && (
            <DownloadLink
              href={`/api/v1/campaigns/${encodeURIComponent(campaign.id)}/export`}
              download
              className="button secondary small"
              aria-label="Download campaign report"
            >
              <ArrowDownToLine size={14} />
              Current report
            </DownloadLink>
          )}
          {!frozen && campaign.status === "finished" && onCapture && (
            <button
              type="button"
              className="button secondary small"
              onClick={onCapture}
            >
              <Archive size={14} />
              Capture baseline
            </button>
          )}
          {!frozen && campaign.status !== "finished" && (
            <button
              type="button"
              className="button secondary small"
              disabled={cancelling}
              onClick={cancel}
            >
              {cancelling ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Square size={13} />
              )}
              {cancelling ? "Requesting cancellation…" : "Cancel campaign"}
            </button>
          )}
        </div>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div
          className="suite-evaluation-counts"
          aria-label="Campaign denominators"
        >
          <div>
            <span>Jobs completed</span>
            <strong>
              {campaign.counts.completed}
              <small>/ {campaign.counts.total}</small>
            </strong>
          </div>
          <div>
            <span>Cases fully evaluated</span>
            <strong>
              {evaluated}
              <small>/ {campaign.counts.total}</small>
            </strong>
          </div>
          <div>
            <span>Cases passing</span>
            <strong>
              {campaign.counts.passed_cases}
              <small>/ {campaign.counts.total}</small>
            </strong>
          </div>
          <div>
            <span>Checks evaluated</span>
            <strong>
              {campaign.counts.checks_passed + campaign.counts.checks_failed}
              <small>/ {campaign.counts.checks_total}</small>
            </strong>
            <small>
              {campaign.counts.checks_passed} passed ·{" "}
              {campaign.counts.checks_failed} failed
            </small>
          </div>
        </div>
        <div className="suite-lifecycle-counts">
          <span>{campaign.counts.pending} pending</span>
          <span>{campaign.counts.failed} job failures</span>
          <span>{campaign.counts.cancelled} cancelled</span>
          <span>{campaign.counts.interrupted} interrupted</span>
          <span>{campaign.counts.missing} missing</span>
          <strong>
            {campaign.counts.checks_not_evaluated} /{" "}
            {campaign.counts.checks_total} checks not evaluated
          </strong>
        </div>
        <p className="suite-panel-footnote">
          {frozen
            ? "This report is an immutable observation at capture time."
            : "State and evaluation are refreshed from actual member jobs. Completed execution and passing criteria are separate."}{" "}
          Evaluator {campaign.evaluator_version} · observed{" "}
          {formatDate(campaign.evaluated_at)}.
        </p>
      </section>
      <CampaignPinnedPlan campaign={campaign} />
      <section
        className="panel suite-campaign-cases"
        aria-label="Campaign cases and criteria"
      >
        <div className="suite-panel-heading">
          <FileJson size={17} />
          <h3>Cases & actual criteria</h3>
          <span>{campaign.cases.length} cases</span>
        </div>
        {campaign.cases.map((entry) => (
          <details
            key={entry.key}
            className="suite-campaign-case"
            open={campaign.cases.length <= 3}
            data-testid={`campaign-case-report-${entry.key}`}
          >
            <summary>
              <span className="suite-case-key">{entry.key}</span>
              <span className="suite-case-summary-name">
                <strong>{entry.name}</strong>
                <small>
                  Scenario r{entry.scenario_revision} ·{" "}
                  {
                    entry.checks.filter(
                      (check) => check.status !== "not_evaluated",
                    ).length
                  }{" "}
                  / {entry.checks.length} checks evaluated
                </small>
              </span>
              {entry.job_status === "missing" ? (
                <EvidenceBadge status="missing" />
              ) : (
                <JobStatusBadge status={entry.job_status} />
              )}
              <EvidenceBadge status={entry.verdict} />
              {!entry.evaluation_complete && (
                <span className="suite-small-warning">
                  Evaluation incomplete
                </span>
              )}
            </summary>
            <div className="suite-campaign-case-body">
              {entry.reason && (
                <p className="suite-case-reason">{entry.reason}</p>
              )}
              <div className="suite-case-evidence-links">
                {entry.job_id && (
                  <button
                    type="button"
                    className="button secondary small"
                    aria-label={`Open job for ${entry.key}`}
                    onClick={() => onOpenJob(entry.job_id!)}
                  >
                    Open job
                    <ArrowUpRight size={13} />
                  </button>
                )}
                {entry.run_id && (
                  <button
                    type="button"
                    className="button secondary small"
                    aria-label={`Open result for ${entry.key}`}
                    onClick={() => onOpenRun(entry.run_id!)}
                  >
                    Open result
                    <ArrowUpRight size={13} />
                  </button>
                )}
                <span>
                  Fidelity:{" "}
                  {entry.execution_context.model_fidelity ??
                    "Unknown / no result"}
                </span>
              </div>
              <CampaignCaseFingerprint entry={entry} />
              {entry.checks.length === 0 && (
                <p className="suite-incomplete-note">
                  No pinned criteria are available for this case. Completion and
                  input identity alone do not establish a criteria-based pass.
                </p>
              )}
              <div className="suite-table-scroll">
                <table className="suite-criteria-table">
                  <thead>
                    <tr>
                      <th>PINNED REQUIREMENT</th>
                      <th>OBSERVED VALUE</th>
                      <th>THRESHOLD</th>
                      <th>EVALUATION</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entry.checks.map((check) => (
                      <tr key={check.requirement_id}>
                        <td>
                          <strong>{check.name}</strong>
                          <small>
                            {metricLabels[check.metric_name] ??
                              check.metric_name}
                          </small>
                        </td>
                        <td>
                          <div className="suite-check-value">
                            <CheckValue check={check} />
                          </div>
                        </td>
                        <td>
                          <span className="mono">
                            {criterionOperator(check.operator)}{" "}
                            {suiteNumber(check.threshold)} {check.expected_unit}
                          </span>
                        </td>
                        <td>
                          <EvidenceBadge status={check.status} />
                          {check.reason && <p>{check.reason}</p>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details className="suite-json-disclosure">
                <summary>Case identity and execution context</summary>
                <dl className="suite-hash-list">
                  <div>
                    <dt>Effective input</dt>
                    <dd>{entry.effective_input_hash}</dd>
                  </div>
                  <div>
                    <dt>Criteria</dt>
                    <dd>{entry.criteria_hash}</dd>
                  </div>
                </dl>
                <pre aria-label={`Execution context for ${entry.key}`}>
                  {JSON.stringify(entry.execution_context, null, 2)}
                </pre>
              </details>
            </div>
          </details>
        ))}
      </section>
    </div>
  );
}
