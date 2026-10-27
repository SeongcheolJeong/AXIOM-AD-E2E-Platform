import { DownloadLink } from "../components/ArtifactAccess";
import { useState } from "react";
import {
  ArrowDownToLine,
  Check,
  ChevronDown,
  FileJson,
  GitBranch,
  Play,
  ShieldCheck,
  X,
} from "lucide-react";
import { formatBytes, formatDate, shortId } from "../api";
import { metricLabels, type RunDetail, type RunSummary } from "../types";
import Replay from "../components/Replay";
import CarlaStartupInspector from "../components/CarlaStartupInspector";
import CarlaSensorConfigurationInspector from "../components/carla/CarlaSensorConfigurationInspector";
import CarlaLiveInspector from "../components/carla/CarlaLiveInspector";
import CarlaVehicleResponseInspector from "../components/carla/CarlaVehicleResponseInspector";
import RecoveredRunLineage from "../components/jobs/RecoveredRunLineage";
import { runExecutionId } from "../components/jobs/RunEvidenceIdentity";
import RunTable from "../components/RunTable";
import {
  runOptionLabel,
  RunEngineSummary,
} from "../components/EngineProvenance";
import {
  Badge,
  CopyButton,
  Empty,
  PageHeading,
  PanelHeading,
  SearchField,
} from "../components/ui";
export default function Runs({
  runs,
  run: loadedRun,
  selectedId,
  loading,
  loadError,
  onSelect,
  onRun,
  onOpenJob,
}: {
  runs: RunSummary[];
  run: RunDetail | null;
  selectedId: string | null;
  loading: boolean;
  loadError: string;
  onSelect: (id: string) => void;
  onRun: () => void;
  onOpenJob: (id: string) => void;
}) {
  // The requested identity changes before the asynchronous resource effect runs.
  // Never render the previous run's details under a newly selected run ID.
  const candidate =
    loadedRun?.id === selectedId && !loading && !loadError ? loadedRun : null;
  let identityError = "";
  if (candidate) {
    try {
      runExecutionId(candidate);
    } catch (reason) {
      identityError =
        reason instanceof Error
          ? reason.message
          : "Evidence identity is invalid.";
    }
  }
  const run = identityError ? null : candidate;
  const [query, setQuery] = useState(""),
    [verdict, setVerdict] = useState("all"),
    [showList, setShowList] = useState(false);
  const filtered = runs.filter(
    (r) =>
      (verdict === "all" || r.verdict === verdict) &&
      `${r.scenario_name} ${r.id} ${r.adapter_id} ${r.provenance.adapter_version}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="REPLAY. INSPECT. UNDERSTAND."
        title="Run explorer"
        description="Every execution carries its inputs, signals, and evidence."
        actions={
          <button className="button primary" onClick={onRun}>
            <Play size={15} />
            Run simulation
          </button>
        }
      />
      <div className="run-selection">
        <label>
          <span className="eyebrow">SELECT EXECUTION</span>
          <select
            aria-label="Select execution"
            value={selectedId ?? ""}
            onChange={(e) => onSelect(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {runOptionLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary"
          onClick={() => setShowList((v) => !v)}
        >
          Browse {runs.length} runs
          <ChevronDown size={15} />
        </button>
        {run && (
          <DownloadLink
            className="button secondary"
            href={`/api/v1/runs/${run.id}/export`}
            download
          >
            <ArrowDownToLine size={15} />
            Export evidence
          </DownloadLink>
        )}
      </div>
      {showList && (
        <section className="panel run-browser">
          <div className="filter-bar">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search runs…"
            />
            <select
              aria-label="Filter verdict"
              value={verdict}
              onChange={(e) => setVerdict(e.target.value)}
            >
              <option value="all">All verdicts</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed</option>
              <option value="error">Error</option>
            </select>
          </div>
          <RunTable
            runs={filtered}
            onSelect={(id) => {
              onSelect(id);
              setShowList(false);
            }}
            selectedId={run?.id}
          />
        </section>
      )}
      {!run ? (
        <section className="panel">
          {identityError && (
            <p className="inline-error" role="alert">
              {identityError}
            </p>
          )}
          <Empty
            title={
              loading
                ? "Loading execution…"
                : loadError || identityError
                  ? "Execution unavailable"
                  : "Nothing to replay yet"
            }
            description={
              loadError
                ? "Select another execution or reload the workspace to retry."
                : loading
                  ? "Retrieving the selected execution and its evidence."
                  : "Run a scenario to record trajectories and collect execution evidence."
            }
          />
        </section>
      ) : (
        <>
          <div className="run-summary-strip">
            <div>
              <Badge status={run.verdict} />
              <span className="mono">{shortId(run.id)}</span>
              <span>
                <GitBranch size={13} />
                revision {run.scenario_revision}
              </span>
              <span>
                {run.adapter_id} · {run.provenance.adapter_version}
              </span>
            </div>
            <span>{formatDate(run.created_at)}</span>
          </div>
          <RunEngineSummary run={run} />
          {run.recovery && (
            <RecoveredRunLineage
              run={run}
              onOpenRun={onSelect}
              onOpenJob={onOpenJob}
            />
          )}
          <Replay run={run} large />
          {run.id === selectedId &&
            !loading &&
            !loadError &&
            run.adapter_id === "carla" && (
              <CarlaVehicleResponseInspector
                key={`response:${run.id}`}
                run={run}
              />
            )}
          {run.id === selectedId &&
            !loading &&
            !loadError &&
            run.execution_context && (
              <CarlaLiveInspector key={`live:${run.id}`} run={run} />
            )}
          {run.execution_context?.schema_version ===
            "simulation-control-context/2" && (
            <CarlaSensorConfigurationInspector
              key={`sensor-configuration:${run.id}`}
              run={run}
            />
          )}
          {run.id === selectedId &&
            !loading &&
            !loadError &&
            run.artifacts.some(
              (artifact) =>
                artifact.kind === "startup_manifest" &&
                artifact.name === "carla-startup.json",
            ) && <CarlaStartupInspector key={`startup:${run.id}`} run={run} />}
          <div className="metric-grid">
            {run.metrics.map((metric) => (
              <section className="metric-card" key={metric.name}>
                <div>
                  <span>{metricLabels[metric.name] ?? metric.name}</span>
                  {metric.passed ? (
                    <Check size={15} className="text-green" />
                  ) : (
                    <X size={15} className="text-red" />
                  )}
                </div>
                <strong>
                  {metric.value == null
                    ? "—"
                    : Number.isInteger(metric.value)
                      ? metric.value
                      : metric.value.toFixed(2)}{" "}
                  <small>{metric.unit}</small>
                </strong>
                <p>
                  {metric.name === "distance_m"
                    ? "Informational metric"
                    : `Threshold ${metric.operator === "gte" ? "≥" : metric.operator === "lte" ? "≤" : "="} ${metric.threshold} ${metric.unit}`}
                </p>
              </section>
            ))}
          </div>
          <div className="detail-grid">
            <section className="panel">
              <PanelHeading
                title="Event timeline"
                subtitle={`${run.events.length} recorded events`}
              />
              <div className="event-list">
                {run.events.length ? (
                  run.events.map((event, i) => (
                    <div key={i} className="event-item">
                      <span className="event-time mono">
                        {event.time_s.toFixed(1)}s
                      </span>
                      <span
                        className={`event-node ${event.type.includes("collision") ? "danger" : ""}`}
                      />
                      <div>
                        <strong>{event.type.replaceAll("_", " ")}</strong>
                        <p>{event.message}</p>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="empty-inline">
                    No discrete events recorded for this execution.
                  </p>
                )}
              </div>
            </section>
            <section className="panel provenance-panel">
              <PanelHeading
                title="Execution provenance"
                subtitle="Reproduce the test. Verify its lineage."
                action={<ShieldCheck size={20} className="text-green" />}
              />
              <dl>
                <div>
                  <dt>Model fidelity</dt>
                  <dd>{run.provenance.model_fidelity}</dd>
                </div>
                <div>
                  <dt>Random seed</dt>
                  <dd className="mono">{run.provenance.seed}</dd>
                </div>
                <div>
                  <dt>Input SHA-256</dt>
                  <dd className="hash">
                    <code>{run.provenance.input_hash}</code>
                    <CopyButton text={run.provenance.input_hash} />
                  </dd>
                </div>
                <div>
                  <dt>Output SHA-256</dt>
                  <dd className="hash">
                    <code>{run.provenance.output_hash}</code>
                    <CopyButton text={run.provenance.output_hash} />
                  </dd>
                </div>
              </dl>
            </section>
          </div>
          <section className="panel">
            <PanelHeading
              title="Run artifacts"
              subtitle="Managed files linked directly to this execution."
            />
            <div className="artifact-rows">
              {run.artifacts.map((artifact) => (
                <DownloadLink
                  key={artifact.id}
                  className="artifact-row"
                  href={artifact.download_url}
                  download
                >
                  <span className="file-icon">
                    <FileJson size={19} />
                  </span>
                  <div>
                    <strong>{artifact.name}</strong>
                    <span>
                      {artifact.kind} · {artifact.source}
                    </span>
                  </div>
                  <span>{formatBytes(artifact.size_bytes)}</span>
                  <ArrowDownToLine size={16} />
                </DownloadLink>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
