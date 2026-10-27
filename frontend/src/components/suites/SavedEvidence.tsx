import { useEffect } from "react";
import { Archive, ArrowDownToLine, ArrowRight, FileLock2 } from "lucide-react";
import { formatBytes, formatDate } from "../../api";
import { CopyButton, Empty, ErrorState, Loading } from "../ui";
import { DownloadLink } from "../ArtifactAccess";
import { SuiteRefreshNotice, useSuiteResource } from "./SuiteCommon";
import CampaignReport, { EvidenceBadge } from "./CampaignReport";
import ComparisonReport from "./ComparisonReport";
import type {
  BaselineDetail,
  BaselineSummary,
  ComparisonDetail,
  ComparisonSummary,
} from "./SuiteTypes";

export function BaselineLibrary({
  suiteId,
  baselines,
  selectedId,
  onSelect,
  onCompare,
  onOpenRun,
  onOpenJob,
}: {
  suiteId: string;
  baselines: BaselineSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCompare: (id: string) => void;
  onOpenRun: (id: string) => void;
  onOpenJob: (id: string) => void;
}) {
  const selected = baselines.find((entry) => entry.id === selectedId);
  const detail = useSuiteResource<BaselineDetail>(
    selectedId ? `/baselines/${encodeURIComponent(selectedId)}` : null,
    15000,
  );
  useEffect(() => {
    if (!selectedId && baselines[0]) onSelect(baselines[0].id);
  }, [baselines, selectedId, onSelect]);
  const snapshot = detail.data?.suite_id === suiteId ? detail.data : null;
  if (!baselines.length && !selectedId)
    return (
      <section className="panel">
        <Empty
          title="No baseline has been captured"
          description="Run a suite, wait for its campaign to finish, then capture its immutable evidence. Failed and incomplete captures retain their verdicts."
        />
      </section>
    );
  return (
    <div className="suite-saved-evidence">
      <div className="suite-selection-toolbar">
        <label>
          Baseline
          <select
            aria-label="Select baseline"
            value={selectedId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
          >
            {!selected && selectedId && (
              <option value={selectedId}>{snapshot?.name ?? selectedId}</option>
            )}
            {baselines.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.name} · r{entry.suite_revision} · {entry.verdict}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SuiteRefreshNotice
        error={detail.error}
        hasData={!!snapshot}
        label="Baseline"
        onRetry={detail.reload}
      />
      {detail.loading && !snapshot ? (
        <Loading label="Verifying captured baseline…" />
      ) : snapshot ? (
        <>
          <section className="panel suite-saved-header">
            <div className="suite-panel-heading">
              <Archive size={19} />
              <h3>{snapshot.name}</h3>
              <EvidenceBadge status={snapshot.verdict} />
            </div>
            <p>{snapshot.note}</p>
            <div className="suite-saved-meta">
              <span>Captured {formatDate(snapshot.created_at)}</span>
              <span>{snapshot.total_cases} cases</span>
              <span>{formatBytes(snapshot.manifest_size_bytes)}</span>
              <span>Suite r{snapshot.suite_revision}</span>
            </div>
            <div className="suite-saved-actions">
              <DownloadLink
                href={snapshot.download_url}
                download
                className="button secondary small"
                aria-label="Download baseline manifest"
              >
                <ArrowDownToLine size={14} />
                Baseline manifest
              </DownloadLink>
              <button
                type="button"
                className="button secondary small"
                onClick={() => onCompare(snapshot.id)}
              >
                Use for comparison
                <ArrowRight size={14} />
              </button>
            </div>
            <div className="suite-manifest-hash">
              <span>IMMUTABLE MANIFEST SHA-256</span>
              <code>{snapshot.manifest_hash}</code>
              <CopyButton
                text={snapshot.manifest_hash}
                label="Copy baseline hash"
              />
            </div>
            <p className="suite-capture-boundary">
              This baseline preserves the captured report and verdict. It is not
              release approval.
            </p>
          </section>
          <CampaignReport
            campaign={snapshot.manifest.report}
            frozen
            onOpenJob={onOpenJob}
            onOpenRun={onOpenRun}
          />
        </>
      ) : detail.error ? (
        <ErrorState message={detail.error} retry={detail.reload} />
      ) : null}
    </div>
  );
}

export function SavedComparisons({
  suiteId,
  selectedId,
  onSelect,
  onOpenRun,
  onOpenJob,
}: {
  suiteId: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenRun: (id: string) => void;
  onOpenJob: (id: string) => void;
}) {
  const list = useSuiteResource<ComparisonSummary[]>(
    `/comparisons?suite_id=${encodeURIComponent(suiteId)}&limit=100`,
    10000,
  );
  const detail = useSuiteResource<ComparisonDetail>(
    selectedId ? `/comparisons/${encodeURIComponent(selectedId)}` : null,
    15000,
  );
  const entries =
    list.data?.filter((entry) => entry.suite_id === suiteId) ?? [];
  useEffect(() => {
    if (!selectedId && entries[0]) onSelect(entries[0].id);
  }, [selectedId, entries, onSelect]);
  const saved = detail.data?.suite_id === suiteId ? detail.data : null;
  return (
    <div
      className="suite-saved-evidence"
      aria-label="Saved comparison evidence"
    >
      <SuiteRefreshNotice
        error={list.error}
        hasData={!!list.data}
        label="Saved comparisons"
        onRetry={list.reload}
      />
      {list.loading && !list.data ? (
        <Loading label="Loading comparison captures…" />
      ) : !entries.length && !selectedId ? (
        <section className="panel">
          <Empty
            title="No comparison has been captured"
            description="Compare a finished campaign with a baseline, then capture the report with a review note. Stored captures preserve the original evidence and policy."
          />
        </section>
      ) : (
        <>
          <div className="suite-selection-toolbar">
            <label>
              Saved comparison
              <select
                aria-label="Select saved comparison"
                value={selectedId ?? ""}
                onChange={(event) => onSelect(event.target.value)}
              >
                {selectedId &&
                  !entries.some((entry) => entry.id === selectedId) && (
                    <option value={selectedId}>
                      {saved?.name ?? selectedId}
                    </option>
                  )}
                {entries.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} · {formatDate(entry.created_at)}
                  </option>
                ))}
              </select>
            </label>
            <span>Latest {entries.length} captures for this suite</span>
          </div>
          <SuiteRefreshNotice
            error={detail.error}
            hasData={!!saved}
            label="Captured comparison"
            onRetry={detail.reload}
          />
          {detail.loading && !saved ? (
            <Loading label="Verifying comparison manifest…" />
          ) : saved ? (
            <>
              <section
                className="panel suite-saved-header"
                data-testid="saved-comparison-detail"
              >
                <div className="suite-panel-heading">
                  <FileLock2 size={19} />
                  <h3>{saved.name}</h3>
                  <EvidenceBadge
                    status={saved.candidate_verdict}
                    label={`Candidate: ${saved.candidate_verdict}`}
                  />
                </div>
                <p>{saved.note}</p>
                <div className="suite-saved-meta">
                  <span>Captured {formatDate(saved.created_at)}</span>
                  <span>Policy {saved.policy_version}</span>
                  <span>{formatBytes(saved.manifest_size_bytes)}</span>
                  <span>
                    {saved.candidate_evaluation_complete
                      ? "Candidate fully evaluated"
                      : "Candidate evaluation incomplete"}
                  </span>
                </div>
                <div className="suite-saved-actions">
                  <DownloadLink
                    href={saved.download_url}
                    download
                    className="button secondary small"
                    aria-label="Download comparison"
                  >
                    <ArrowDownToLine size={14} />
                    Download comparison
                  </DownloadLink>
                </div>
                <div className="suite-manifest-hash">
                  <span>IMMUTABLE COMPARISON SHA-256</span>
                  <code data-testid="comparison-manifest-hash">
                    {saved.manifest_hash}
                  </code>
                  <CopyButton
                    text={saved.manifest_hash}
                    label="Copy comparison hash"
                  />
                </div>
                <p className="suite-capture-boundary">
                  The stored baseline, candidate, and comparison below come from
                  this manifest. They are not recomputed from live definitions.
                </p>
              </section>
              <ComparisonReport
                key={saved.id}
                report={saved.manifest.report}
                baselineLabel={saved.manifest.baseline.name}
                candidateLabel={saved.manifest.candidate.label}
                baselineRevision={saved.manifest.baseline.report.suite_revision}
                candidateRevision={saved.manifest.candidate.suite_revision}
                frozen
                onOpenRun={onOpenRun}
                onOpenJob={onOpenJob}
              />
            </>
          ) : detail.error ? (
            <ErrorState message={detail.error} retry={detail.reload} />
          ) : null}
        </>
      )}
    </div>
  );
}
