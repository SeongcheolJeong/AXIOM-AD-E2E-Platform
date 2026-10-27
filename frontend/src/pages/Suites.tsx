import { useEffect, useState } from "react";
import {
  Archive,
  FileLock2,
  GitBranch,
  Layers3,
  Play,
  Plus,
  RefreshCw,
} from "lucide-react";
import { ErrorState, Loading, Empty, PageHeading } from "../components/ui";
import type { Adapter, Requirement, Scenario } from "../types";
import {
  SuiteRefreshNotice,
  useSuiteResource,
} from "../components/suites/SuiteCommon";
import SuiteEditor from "../components/suites/SuiteEditor";
import SuiteDefinition from "../components/suites/SuiteDefinition";
import { CampaignLauncher } from "../components/suites/CampaignForms";
import CampaignWorkspace from "../components/suites/CampaignWorkspace";
import {
  BaselineLibrary,
  SavedComparisons,
} from "../components/suites/SavedEvidence";
import type {
  BaselineSummary,
  SuiteDetail,
  SuiteSummary,
} from "../components/suites/SuiteTypes";
import "../suites.css";

type SuiteTab = "definition" | "campaigns" | "baselines" | "comparisons";
export default function Suites({
  scenarios,
  adapters,
  onOpenJob,
  onOpenRun,
  onSettled,
  onNavigate,
  notify,
}: {
  scenarios: Scenario[];
  adapters: Adapter[];
  onOpenJob: (id: string) => void;
  onOpenRun: (id: string) => void;
  onSettled: () => void;
  onNavigate: (page: string) => void;
  notify: (message: string) => void;
}) {
  const [suiteId, setSuiteId] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [tab, setTab] = useState<SuiteTab>("definition");
  const [editing, setEditing] = useState<"new" | "revision" | null>(null);
  const [launch, setLaunch] = useState(false);
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [baselineId, setBaselineId] = useState<string | null>(null);
  const [savedComparisonId, setSavedComparisonId] = useState<string | null>(
    null,
  );
  const list = useSuiteResource<SuiteSummary[]>("/suites", 10000);
  const revisions = useSuiteResource<SuiteDetail[]>(
    suiteId ? `/suites/${encodeURIComponent(suiteId)}/revisions` : null,
    10000,
  );
  const requirements = useSuiteResource<Requirement[]>("/requirements", 15000);
  const baselines = useSuiteResource<BaselineSummary[]>(
    suiteId ? `/baselines?suite_id=${encodeURIComponent(suiteId)}` : null,
    10000,
  );
  const availableRevisions =
    revisions.data?.filter((entry) => entry.id === suiteId) ?? [];
  const suite = availableRevisions.find((entry) =>
    revision === null
      ? entry.revision ===
        Math.max(...availableRevisions.map((item) => item.revision))
      : entry.revision === revision,
  );
  const latestRevision = Math.max(
    0,
    ...availableRevisions.map((entry) => entry.revision),
    ...(list.data ?? [])
      .filter((entry) => entry.id === suiteId)
      .map((entry) => entry.revision),
  );
  useEffect(() => {
    if (!suiteId && list.data?.[0]) setSuiteId(list.data[0].id);
  }, [suiteId, list.data]);
  function chooseSuite(id: string) {
    setSuiteId(id);
    setRevision(null);
    setCampaignId(null);
    setBaselineId(null);
    setSavedComparisonId(null);
  }
  function saved(suite: SuiteDetail) {
    setEditing(null);
    setSuiteId(suite.id);
    setRevision(suite.revision);
    list.reload();
    revisions.reload();
    setTab("definition");
    notify(`Suite saved · revision ${suite.revision}`);
  }
  const canAuthor = scenarios.length > 0 && !!requirements.data?.length;
  const tabs = [
    { id: "definition", label: "Pinned cases", icon: Layers3 },
    { id: "campaigns", label: "Campaigns", icon: Play },
    { id: "baselines", label: "Baselines", icon: Archive },
    { id: "comparisons", label: "Saved comparisons", icon: FileLock2 },
  ] as const;
  return (
    <div className="test-suites-page">
      <PageHeading
        eyebrow="PIN INPUTS. COMPARE EVIDENCE."
        title="Test suites"
        description="Version test cases, run campaigns, and retain the evidence behind a comparison."
        actions={
          <>
            <button
              type="button"
              className="button secondary"
              aria-label="Refresh test suites"
              onClick={() => {
                list.reload();
                revisions.reload();
                requirements.reload();
                baselines.reload();
              }}
            >
              <RefreshCw size={15} />
              Refresh
            </button>
            <button
              type="button"
              className="button primary"
              disabled={!canAuthor}
              onClick={() => setEditing("new")}
            >
              <Plus size={16} />
              New suite
            </button>
          </>
        }
      />
      {!canAuthor && !requirements.loading && (
        <p className="suite-authoring-prerequisite">
          Suite authoring needs an existing scenario and a scoped requirement.
          <button
            type="button"
            className="suite-text-button"
            onClick={() =>
              onNavigate(scenarios.length ? "validation" : "scenarios")
            }
          >
            Open {scenarios.length ? "Validation" : "Scenario library"}
          </button>
        </p>
      )}
      <SuiteRefreshNotice
        error={requirements.error}
        hasData={!!requirements.data}
        label="Requirements"
        onRetry={requirements.reload}
      />
      <SuiteRefreshNotice
        error={list.error}
        hasData={!!list.data}
        label="Suites"
        onRetry={list.reload}
      />
      {list.loading && !list.data ? (
        <Loading label="Loading test suites…" />
      ) : !list.data?.length && !suiteId ? (
        <section className="panel suite-empty-workspace">
          <Empty
            title="Create a reproducible test set"
            description="Choose scenarios, pin their revisions and criteria, then run the same suite as a campaign. Baselines preserve what was measured."
            action={
              <button
                type="button"
                className="button primary"
                disabled={!canAuthor}
                onClick={() => setEditing("new")}
              >
                <Plus size={16} />
                Create first suite
              </button>
            }
          />
        </section>
      ) : (
        <>
          <div className="suite-selection-toolbar suite-main-selector">
            <label>
              Test suite
              <select
                aria-label="Select test suite"
                value={suiteId ?? ""}
                onChange={(event) => chooseSuite(event.target.value)}
              >
                {suiteId &&
                  !list.data?.some((entry) => entry.id === suiteId) && (
                    <option value={suiteId}>{suite?.name ?? suiteId}</option>
                  )}
                {list.data?.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Revision
              <select
                aria-label="Suite revision"
                value={suite?.revision ?? revision ?? ""}
                onChange={(event) => setRevision(Number(event.target.value))}
              >
                {!availableRevisions.length && (
                  <option value="">Loading revisions…</option>
                )}
                {availableRevisions.map((entry) => (
                  <option key={entry.revision} value={entry.revision}>
                    r{entry.revision}
                    {entry.revision === latestRevision
                      ? " · latest"
                      : " · historical"}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <button
                type="button"
                className="button secondary"
                disabled={!suite || !canAuthor}
                onClick={() => setEditing("revision")}
              >
                <GitBranch size={15} />
                Create revision
              </button>
              <button
                type="button"
                className="button primary"
                disabled={!suite}
                onClick={() => setLaunch(true)}
              >
                <Play size={15} />
                Run suite
              </button>
            </div>
          </div>
          <SuiteRefreshNotice
            error={revisions.error}
            hasData={!!suite}
            label="Suite revision"
            onRetry={revisions.reload}
          />
          {revisions.loading && !suite ? (
            <Loading label="Loading pinned suite revision…" />
          ) : suite ? (
            <div data-testid="suite-detail">
              <section className="panel suite-overview">
                <div>
                  <span className="suite-kicker">
                    SUITE REVISION {suite.revision}
                  </span>
                  <h2>{suite.name}</h2>
                  <p>{suite.description || "No description supplied."}</p>
                </div>
                <div className="suite-overview-counts">
                  <span>
                    <strong>{suite.case_count}</strong>cases
                  </span>
                  <span>
                    <strong>
                      {suite.cases.reduce(
                        (sum, entry) => sum + entry.requirements.length,
                        0,
                      )}
                    </strong>
                    pinned criteria
                  </span>
                </div>
              </section>
              <div
                className="suite-tabs"
                role="tablist"
                aria-label="Test suite views"
              >
                {tabs.map((entry) => (
                  <button
                    type="button"
                    role="tab"
                    key={entry.id}
                    id={`suite-${entry.id}-tab`}
                    aria-controls={`suite-${entry.id}-panel`}
                    aria-selected={tab === entry.id}
                    onClick={() => setTab(entry.id)}
                  >
                    <entry.icon size={15} />
                    {entry.label}
                  </button>
                ))}
              </div>
              <div
                role="tabpanel"
                id={`suite-${tab}-panel`}
                aria-labelledby={`suite-${tab}-tab`}
              >
                {tab === "definition" && <SuiteDefinition suite={suite} />}
                {tab === "campaigns" && (
                  <>
                    <SuiteRefreshNotice
                      error={baselines.error}
                      hasData={!!baselines.data}
                      label="Baselines"
                      onRetry={baselines.reload}
                    />
                    <CampaignWorkspace
                      key={suiteId}
                      suiteId={suite.id}
                      selectedId={campaignId}
                      baselineId={baselineId}
                      baselines={baselines.data ?? []}
                      onSelect={setCampaignId}
                      onBaselineSelect={setBaselineId}
                      onLaunch={() => setLaunch(true)}
                      onBaselineCaptured={(baseline) => {
                        setBaselineId(baseline.id);
                        baselines.reload();
                        setTab("baselines");
                        notify(`Baseline captured · ${baseline.verdict}`);
                      }}
                      onComparisonCaptured={(comparison) => {
                        setSavedComparisonId(comparison.id);
                        setTab("comparisons");
                        notify("Comparison evidence captured");
                      }}
                      onOpenRun={onOpenRun}
                      onOpenJob={onOpenJob}
                      onSettled={onSettled}
                    />
                  </>
                )}
                {tab === "baselines" && (
                  <>
                    <SuiteRefreshNotice
                      error={baselines.error}
                      hasData={!!baselines.data}
                      label="Baselines"
                      onRetry={baselines.reload}
                    />
                    <BaselineLibrary
                      key={suiteId}
                      suiteId={suite.id}
                      baselines={baselines.data ?? []}
                      selectedId={baselineId}
                      onSelect={setBaselineId}
                      onCompare={(id) => {
                        setBaselineId(id);
                        setTab("campaigns");
                      }}
                      onOpenRun={onOpenRun}
                      onOpenJob={onOpenJob}
                    />
                  </>
                )}
                {tab === "comparisons" && (
                  <SavedComparisons
                    key={suiteId}
                    suiteId={suite.id}
                    selectedId={savedComparisonId}
                    onSelect={setSavedComparisonId}
                    onOpenRun={onOpenRun}
                    onOpenJob={onOpenJob}
                  />
                )}
              </div>
            </div>
          ) : revisions.error ? (
            <ErrorState message={revisions.error} retry={revisions.reload} />
          ) : (
            <section className="panel">
              <Empty
                title="This suite revision is unavailable"
                description="Choose a saved revision or refresh the suite."
              />
            </section>
          )}
        </>
      )}
      {editing && (
        <SuiteEditor
          suite={editing === "revision" ? suite : undefined}
          expectedRevision={latestRevision}
          scenarios={scenarios}
          requirements={requirements.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      )}
      {launch && suite && (
        <CampaignLauncher
          suite={suite}
          adapters={adapters}
          onClose={() => setLaunch(false)}
          onSubmitted={(campaign) => {
            setLaunch(false);
            setCampaignId(campaign.id);
            setTab("campaigns");
            notify(`Campaign submitted · ${campaign.counts.total} cases`);
          }}
        />
      )}
    </div>
  );
}
