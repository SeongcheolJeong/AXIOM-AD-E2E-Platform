import { formatBytes } from "../../api";
import { CopyButton } from "../ui";
import { preciseJobTime } from "./JobTime";
import type { RemoteExecutionStatus } from "./RemoteExecutionTypes";

const explanations: Record<RemoteExecutionStatus["state"], string> = {
  not_found:
    "The queried store has no record for this execution ID. This does not prove that an earlier execution never ran.",
  accepted:
    "The remote worker recorded acceptance of this execution. No terminal archive is available yet.",
  running:
    "The remote store reports this same execution as running. The workspace does not submit it again.",
  completed:
    "The remote worker sealed a complete execution archive. Its contents must be retrieved and verified before local evidence is published.",
  failed:
    "The remote worker recorded a failure. Available diagnostic evidence is separate from a completed measurement result.",
  interrupted:
    "The remote execution was interrupted. A sealed partial archive, when available, preserves only observations actually recorded.",
};

export default function RemoteStatusDetails({
  status,
}: {
  status: RemoteExecutionStatus;
}) {
  return (
    <div className="remote-outcome-state" data-testid="remote-execution-status">
      <div className="remote-outcome-status">
        <strong>Remote · {status.state.replaceAll("_", " ")}</strong>
        <time dateTime={status.observed_at}>
          Observed {preciseJobTime(status.observed_at)}
        </time>
      </div>
      <p>{explanations[status.state]}</p>
      {status.error && <p className="inline-error">{status.error}</p>}
      <dl className="remote-outcome-identities">
        <div>
          <dt>Original execution ID</dt>
          <dd>
            <code>{status.execution_id}</code>
          </dd>
        </div>
        <div>
          <dt>Remote store ID</dt>
          <dd>
            <code>{status.store_id}</code>
          </dd>
        </div>
        <div>
          <dt>Remote adapter</dt>
          <dd>
            {status.adapter_id} · {status.adapter_version}
          </dd>
        </div>
        <div>
          <dt>Remote cleanup report</dt>
          <dd>{status.cleanup_status.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>External mutation recorded</dt>
          <dd>{status.mutations_started ? "Yes" : "Not recorded"}</dd>
        </div>
        <div>
          <dt>Sealed archive</dt>
          <dd>
            {status.archive_kind && status.archive_size_bytes !== null
              ? `${status.archive_kind} · ${formatBytes(status.archive_size_bytes)}`
              : "Unavailable"}
          </dd>
        </div>
        {status.archive_sha256 && (
          <div>
            <dt>Remote archive SHA-256</dt>
            <dd>
              <code>{status.archive_sha256}</code>{" "}
              <CopyButton
                text={status.archive_sha256}
                label="Copy remote archive hash"
              />
            </dd>
          </div>
        )}
      </dl>
      <details className="remote-outcome-disclosure">
        <summary>Correlated request and context hashes</summary>
        <dl className="remote-outcome-identities">
          <div>
            <dt>Request SHA-256</dt>
            <dd>
              <code>{status.request_hash}</code>
            </dd>
          </div>
          <div>
            <dt>Input SHA-256</dt>
            <dd>
              <code>{status.input_hash}</code>
            </dd>
          </div>
          <div>
            <dt>Context SHA-256</dt>
            <dd>
              <code>{status.context_hash ?? "No stack context"}</code>
            </dd>
          </div>
        </dl>
      </details>
    </div>
  );
}
