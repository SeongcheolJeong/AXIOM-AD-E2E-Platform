import { ArchiveRestore, ArrowUpRight } from "lucide-react";
import type { RunDetail } from "../../types";
import { CopyButton } from "../ui";
import { preciseJobTime } from "./JobTime";
import "./remote-recovery.css";

export default function RecoveredRunLineage({
  run,
  onOpenRun,
  onOpenJob,
}: {
  run: RunDetail;
  onOpenRun: (id: string) => void;
  onOpenJob: (id: string) => void;
}) {
  const origin = run.recovery;
  if (!origin) return null;
  return (
    <section
      className="recovered-run-lineage"
      aria-label="Recovered evidence lineage"
      data-testid="recovered-run-lineage"
    >
      <header className="remote-outcome-heading">
        <ArchiveRestore size={19} />
        <h3>
          {origin.archive_kind === "complete"
            ? "Recovered complete execution archive"
            : "Recovered partial diagnostics"}
        </h3>
      </header>
      <p>
        This is a supplemental local evidence record for the original remote
        execution. Original file bytes and execution identifiers are preserved;
        the source submission and its earlier result remain unchanged.
      </p>
      <dl className="remote-outcome-identities">
        <div>
          <dt>Supplemental local Run ID</dt>
          <dd>
            <code data-testid="recovered-local-run-id">{run.id}</code>{" "}
            <CopyButton text={run.id} label="Copy recovered Run ID" />
          </dd>
        </div>
        <div>
          <dt>Original remote execution ID · inside artifact bytes</dt>
          <dd>
            <code data-testid="recovered-source-execution-id">
              {origin.source_execution_id}
            </code>
          </dd>
        </div>
        <div>
          <dt>Source submission</dt>
          <dd>
            #{origin.source_submission_seq} ·{" "}
            <code>{origin.source_job_id}</code>
          </dd>
        </div>
        <div>
          <dt>Recovery ID</dt>
          <dd>
            <code>{origin.recovery_id}</code>
          </dd>
        </div>
        <div>
          <dt>Remote store ID</dt>
          <dd>
            <code>{origin.store_id}</code>
          </dd>
        </div>
        <div>
          <dt>Recovered · UTC</dt>
          <dd>{preciseJobTime(origin.recovered_at)}</dd>
        </div>
      </dl>
      <div className="remote-outcome-actions">
        {origin.source_run_id && (
          <button
            type="button"
            className="button secondary"
            onClick={() => onOpenRun(origin.source_run_id!)}
          >
            <ArrowUpRight size={15} />
            Open original result
          </button>
        )}
        <button
          type="button"
          className="button secondary"
          onClick={() => onOpenJob(origin.source_job_id)}
        >
          <ArrowUpRight size={15} />
          Open source submission
        </button>
      </div>
      {!origin.source_run_id && (
        <p>
          The source submission had no locally published Run before recovery.
        </p>
      )}
      <details className="remote-outcome-disclosure">
        <summary>Recovery provenance and checksums</summary>
        <dl className="remote-outcome-identities">
          <div>
            <dt>Original remote request SHA-256</dt>
            <dd>
              <code>{origin.request_hash}</code>
            </dd>
          </div>
          <div>
            <dt>
              Original remote archive SHA-256 · separate from Run output hash
            </dt>
            <dd>
              <code>{origin.archive_sha256}</code>
            </dd>
          </div>
          <div>
            <dt>Core verification policy</dt>
            <dd>
              <code>{origin.verification_version}</code>
            </dd>
          </div>
        </dl>
        <p>
          Retrieval is not a new experiment. It does not release resource
          quarantine or replace explicit engine cleanup confirmation.
        </p>
      </details>
    </section>
  );
}
