import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  FileLock2,
  GitCompareArrows,
} from "lucide-react";
import { Empty, ErrorState, Loading } from "../ui";
import { SuiteRefreshNotice, useSuiteResource } from "./SuiteCommon";
import CampaignReport from "./CampaignReport";
import ComparisonReport from "./ComparisonReport";
import { BaselineCapture } from "./CampaignForms";
import ComparisonCapture from "./ComparisonCapture";
import type {
  BaselineDetail,
  BaselineSummary,
  CampaignComparison,
  CampaignDetail,
  CampaignPage,
  ComparisonDetail,
} from "./SuiteTypes";

export default function CampaignWorkspace({
  suiteId,
  selectedId,
  baselineId,
  baselines,
  onSelect,
  onBaselineSelect,
  onLaunch,
  onBaselineCaptured,
  onComparisonCaptured,
  onOpenRun,
  onOpenJob,
  onSettled,
}: {
  suiteId: string;
  selectedId: string | null;
  baselineId: string | null;
  baselines: BaselineSummary[];
  onSelect: (id: string) => void;
  onBaselineSelect: (id: string | null) => void;
  onLaunch: () => void;
  onBaselineCaptured: (baseline: BaselineDetail) => void;
  onComparisonCaptured: (capture: ComparisonDetail) => void;
  onOpenRun: (id: string) => void;
  onOpenJob: (id: string) => void;
  onSettled: () => void;
}) {
  const [before, setBefore] = useState<number | null>(null);
  const [history, setHistory] = useState<(number | null)[]>([]);
  const [captureBaseline, setCaptureBaseline] = useState(false);
  const [captureComparison, setCaptureComparison] = useState(false);
  const queries = new URLSearchParams({ suite_id: suiteId, limit: "100" });
  if (before !== null) queries.set("before_seq", String(before));
  const list = useSuiteResource<CampaignPage>(`/campaigns?${queries}`, 5000);
  const detail = useSuiteResource<CampaignDetail>(
    selectedId ? `/campaigns/${encodeURIComponent(selectedId)}` : null,
  );
  const selectedBaseline = baselines.find(
    (entry) => entry.id === baselineId && entry.suite_id === suiteId,
  );
  const comparison = useSuiteResource<CampaignComparison>(
    selectedId && selectedBaseline
      ? `/campaigns/${encodeURIComponent(selectedId)}/compare?baseline_id=${encodeURIComponent(selectedBaseline.id)}`
      : null,
    5000,
  );
  const campaign = detail.data?.suite_id === suiteId ? detail.data : null;
  const entries =
    list.data?.items.filter((entry) => entry.suite_id === suiteId) ?? [];
  const settled = useRef(new Set<string>());
  useEffect(() => {
    if (!selectedId && entries[0]) onSelect(entries[0].id);
  }, [selectedId, entries, onSelect]);
  useEffect(() => {
    if (campaign?.status === "finished" && !settled.current.has(campaign.id)) {
      settled.current.add(campaign.id);
      onSettled();
    }
  }, [campaign, onSettled]);
  function refresh() {
    detail.reload();
    list.reload();
    comparison.reload();
  }
  return (
    <div className="suite-campaign-workspace">
      <SuiteRefreshNotice
        error={list.error}
        hasData={!!list.data}
        label="Campaign list"
        onRetry={list.reload}
      />
      <div className="suite-selection-toolbar">
        <label>
          Campaign
          <select
            aria-label="Select campaign"
            value={selectedId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
          >
            {!entries.length && !selectedId && (
              <option value="">No campaign selected</option>
            )}
            {selectedId &&
              !entries.some((entry) => entry.id === selectedId) && (
                <option value={selectedId}>
                  {campaign?.label ?? selectedId}
                </option>
              )}
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label} · r{entry.suite_revision} · {entry.status}
              </option>
            ))}
          </select>
        </label>
        <div className="suite-pagination">
          <button
            type="button"
            className="button secondary small"
            disabled={!history.length || list.loading}
            onClick={() => {
              setBefore(history.at(-1) ?? null);
              setHistory((previous) => previous.slice(0, -1));
            }}
          >
            <ArrowLeft size={13} />
            Newer
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={
              list.loading ||
              list.data?.next_before_seq === null ||
              list.data?.next_before_seq === undefined
            }
            onClick={() => {
              const next = list.data?.next_before_seq;
              if (next === null || next === undefined) return;
              setHistory((previous) => [...previous, before]);
              setBefore(next);
            }}
          >
            Older
            <ArrowRight size={13} />
          </button>
        </div>
        <span>{entries.length} campaigns on this page</span>
      </div>
      <SuiteRefreshNotice
        error={detail.error}
        hasData={!!campaign}
        label="Campaign"
        onRetry={detail.reload}
      />
      {detail.loading && !campaign ? (
        <Loading label="Loading campaign evidence…" />
      ) : detail.error && !campaign ? (
        <ErrorState message={detail.error} retry={detail.reload} />
      ) : campaign ? (
        <>
          <div className="suite-compare-controls">
            <div>
              <GitCompareArrows size={19} />
              <span>Compare this campaign</span>
            </div>
            <label>
              Baseline
              <select
                aria-label="Comparison baseline"
                value={selectedBaseline?.id ?? ""}
                onChange={(event) =>
                  onBaselineSelect(event.target.value || null)
                }
              >
                <option value="">Choose a captured baseline</option>
                {baselines
                  .filter((entry) => entry.suite_id === suiteId)
                  .map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.name} · r{entry.suite_revision} · {entry.verdict}
                    </option>
                  ))}
              </select>
            </label>
            {selectedBaseline && (
              <button
                type="button"
                className="button secondary small"
                disabled={
                  campaign.status !== "finished" ||
                  !comparison.data ||
                  !!comparison.error
                }
                onClick={() => setCaptureComparison(true)}
              >
                <FileLock2 size={14} />
                Capture comparison
              </button>
            )}
          </div>
          {selectedBaseline && campaign.status !== "finished" && (
            <p className="suite-form-help">
              Live comparison is available while jobs run. Capture becomes
              available after the candidate campaign finishes.
            </p>
          )}
          {selectedBaseline && (
            <>
              <SuiteRefreshNotice
                error={comparison.error}
                hasData={!!comparison.data}
                label="Live comparison"
                onRetry={comparison.reload}
              />
              {comparison.loading && !comparison.data ? (
                <Loading label="Comparing pinned evidence…" />
              ) : comparison.data &&
                comparison.data.campaign_id === campaign.id &&
                comparison.data.baseline_id === selectedBaseline.id ? (
                <ComparisonReport
                  key={`${campaign.id}:${selectedBaseline.id}`}
                  report={comparison.data}
                  baselineLabel={selectedBaseline.name}
                  candidateLabel={campaign.label}
                  baselineRevision={selectedBaseline.suite_revision}
                  candidateRevision={campaign.suite_revision}
                  onOpenRun={onOpenRun}
                  onOpenJob={onOpenJob}
                />
              ) : null}
            </>
          )}
          <CampaignReport
            key={campaign.id}
            campaign={campaign}
            onOpenJob={onOpenJob}
            onOpenRun={onOpenRun}
            onCapture={() => setCaptureBaseline(true)}
            onRefresh={refresh}
          />
          {captureBaseline && (
            <BaselineCapture
              campaign={campaign}
              onClose={() => setCaptureBaseline(false)}
              onCaptured={(baseline) => {
                setCaptureBaseline(false);
                onBaselineCaptured(baseline);
              }}
            />
          )}
          {captureComparison && selectedBaseline && (
            <ComparisonCapture
              campaign={campaign}
              baseline={selectedBaseline}
              onClose={() => setCaptureComparison(false)}
              onCaptured={(capture) => {
                setCaptureComparison(false);
                onComparisonCaptured(capture);
              }}
            />
          )}
        </>
      ) : !list.loading ? (
        <section className="panel">
          <Empty
            title="No campaign selected"
            description="Run the selected suite revision to create a campaign with one durable job per case."
            action={
              <button
                type="button"
                className="button primary"
                onClick={onLaunch}
              >
                Run suite
              </button>
            }
          />
        </section>
      ) : (
        <Loading label="Loading campaigns…" />
      )}
    </div>
  );
}
