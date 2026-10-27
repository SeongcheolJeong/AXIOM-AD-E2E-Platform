import { ArrowUpRight, ChevronRight, GitBranch } from "lucide-react";
import { formatDate, shortId } from "../api";
import type { RunSummary } from "../types";
import { Badge, Empty } from "./ui";
import { RunEngineBadge } from "./EngineProvenance";
export default function RunTable({
  runs,
  onSelect,
  selectedId,
  compact = false,
}: {
  runs: RunSummary[];
  onSelect: (id: string) => void;
  selectedId?: string;
  compact?: boolean;
}) {
  if (!runs.length)
    return (
      <Empty
        title="No runs yet"
        description="Run a scenario to start collecting test evidence."
      />
    );
  return (
    <div className="table-scroll">
      <table className={`data-table ${compact ? "compact" : ""}`}>
        <thead>
          <tr>
            <th>Scenario / run</th>
            <th>Verdict</th>
            <th>Recorded adapter</th>
            {!compact && <th>Execution</th>}
            <th>Created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.id}
              className={selectedId === run.id ? "selected-row" : ""}
            >
              <td>
                <button className="row-title" onClick={() => onSelect(run.id)}>
                  {run.scenario_name}
                </button>
                <div className="row-meta">
                  <span className="mono">{shortId(run.id)}</span>
                  <span>
                    <GitBranch size={11} /> r{run.scenario_revision}
                  </span>
                  {run.recovery && (
                    <span className="recovery-row-label">
                      Recovered evidence
                    </span>
                  )}
                </div>
              </td>
              <td>
                <Badge status={run.verdict} />
              </td>
              <td>
                <RunEngineBadge run={run} />
                <details className="engine-fidelity-disclosure">
                  <summary>Saved fidelity</summary>
                  <p>{run.provenance.model_fidelity || "Not recorded"}</p>
                </details>
              </td>
              {!compact && (
                <td className="mono">{run.execution_ms.toFixed(0)} ms</td>
              )}
              <td className="table-date">{formatDate(run.created_at)}</td>
              <td>
                <button
                  className="icon-button"
                  aria-label={`Inspect ${run.scenario_name}`}
                  onClick={() => onSelect(run.id)}
                >
                  {compact ? (
                    <ChevronRight size={15} />
                  ) : (
                    <ArrowUpRight size={16} />
                  )}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
