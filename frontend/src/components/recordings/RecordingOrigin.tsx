import { ArrowUpRight, GitBranch } from "lucide-react";
import { CopyButton } from "../ui";
import { useSensorResource } from "../sensors/SensorData";

interface Origin {
  recording_id: string;
  run_id: string;
  artifact_id: string;
  created_at: string;
  definition_hash: string;
  snapshot: {
    scenario_id: string;
    scenario_revision: number;
    seed: number;
    adapter_id: string;
    adapter_version: string;
    input_hash: string;
    output_hash: string;
    model_fidelity: string;
    artifact: { name?: string; sha256?: string };
  };
}
export default function RecordingOrigin({
  recordingId,
  onOpenRun,
}: {
  recordingId: string;
  onOpenRun?: (id: string) => void;
}) {
  const origin = useSensorResource<Origin | null>(
    `/recordings/${encodeURIComponent(recordingId)}/origin`,
  );
  if (origin.loading || (!origin.data && !origin.error)) return null;
  if (origin.error)
    return (
      <details className="sensor-origin panel">
        <summary>Execution origin unavailable</summary>
        <p className="inline-error" role="alert">
          {origin.error}
        </p>
        <button
          type="button"
          className="button secondary small"
          onClick={origin.reload}
        >
          Retry origin lookup
        </button>
      </details>
    );
  const data = origin.data;
  if (!data || data.recording_id !== recordingId) return null;
  return (
    <details
      className="sensor-origin panel"
      data-testid="recording-execution-origin"
    >
      <summary>
        <GitBranch size={15} />
        Registered from execution evidence
        <span>
          {data.snapshot.adapter_id} · {data.snapshot.model_fidelity}
        </span>
      </summary>
      <div>
        <dl className="sensor-evidence-grid">
          <div>
            <dt>Original run</dt>
            <dd>
              <code>{data.run_id}</code>
              {onOpenRun && (
                <button
                  type="button"
                  className="sensor-text-button"
                  onClick={() => onOpenRun(data.run_id)}
                >
                  Open original run
                  <ArrowUpRight size={13} />
                </button>
              )}
            </dd>
          </div>
          <div>
            <dt>Original artifact</dt>
            <dd>{data.snapshot.artifact.name ?? data.artifact_id}</dd>
          </div>
          <div>
            <dt>Scenario / revision / seed</dt>
            <dd>
              {data.snapshot.scenario_id} · r{data.snapshot.scenario_revision} ·
              seed {data.snapshot.seed}
            </dd>
          </div>
          <div>
            <dt>Adapter / fidelity</dt>
            <dd>
              {data.snapshot.adapter_id} / {data.snapshot.adapter_version} ·{" "}
              {data.snapshot.model_fidelity}
            </dd>
          </div>
        </dl>
        <p className="sensor-form-help">
          This link preserves the producing run's evidence. A decoded recording
          does not establish sensor model accuracy or real-world validation.
        </p>
        {[
          ["Input SHA-256", data.snapshot.input_hash],
          ["Output SHA-256", data.snapshot.output_hash],
          ["Origin definition SHA-256", data.definition_hash],
        ].map(([label, hash]) => (
          <div className="sensor-hash" key={label}>
            <span>{label}</span>
            <code>{hash}</code>
            <CopyButton text={hash} />
          </div>
        ))}
      </div>
    </details>
  );
}
