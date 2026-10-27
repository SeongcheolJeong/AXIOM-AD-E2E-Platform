import { Cpu, Layers3 } from "lucide-react";
import type { RunDetail, RunSummary } from "../types";
import "./engine-provenance.css";

export const engineName = (id: string) =>
  id === "reference" ? "Reference" : id === "carla" ? "CARLA" : id;

export function runOptionLabel(run: RunSummary) {
  return `${engineName(run.adapter_id)} ${run.provenance.adapter_version} · ${run.scenario_name} · ${run.id.replace(/^run[_-]/, "").slice(0, 8)} · ${run.verdict}${run.recovery ? " · recovered evidence" : ""}`;
}

/** Adapter identity comes from the saved Run, never its scenario's display name. */
export function RunEngineBadge({ run }: { run: RunSummary }) {
  return (
    <span
      className="recorded-engine-badge"
      data-testid="recorded-engine-badge"
      aria-label={`Recorded adapter: ${engineName(run.adapter_id)}, version ${run.provenance.adapter_version}`}
    >
      <Cpu size={13} aria-hidden="true" />
      <strong>{engineName(run.adapter_id)}</strong>
      <span>{run.provenance.adapter_version}</span>
    </span>
  );
}

export function RunEngineSummary({ run }: { run: RunDetail }) {
  return (
    <section
      className="recorded-engine-summary"
      aria-label="Selected run engine provenance"
      data-testid="selected-run-engine"
    >
      <div>
        <Layers3 size={17} aria-hidden="true" />
        <strong>Recorded execution adapter</strong>
        <RunEngineBadge run={run} />
      </div>
      <p>
        <span>Saved fidelity</span>{" "}
        {run.provenance.model_fidelity || "Not recorded"}
      </p>
      {run.execution_context?.mode === "stack" && (
        <p>
          <span>Saved controller</span> Shared stack ·{" "}
          {run.execution_context.stack_snapshot.name} r
          {run.execution_context.stack_snapshot.revision}.
          {run.adapter_id === "carla" &&
            " Live LiDAR with ego/map oracles; no RGB input or capture."}
        </p>
      )}
      {run.status === "error" && (
        <p className="recorded-engine-error">
          This record ended in an execution error. Its selected adapter does not
          imply that a complete simulator result was produced.
        </p>
      )}
    </section>
  );
}
