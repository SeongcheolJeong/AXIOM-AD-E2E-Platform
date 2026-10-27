import type { RunSummary } from "../../types";

const identity = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0 && value.length <= 180;
const sha = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

/** Local storage uses run.id; original immutable bytes retain this execution ID. */
export function runExecutionId(run: RunSummary): string {
  const origin = run.recovery;
  if (origin == null) return run.id;
  if (
    origin.verification_version !== "remote-recovery/1" ||
    !identity(origin.recovery_id) ||
    !identity(origin.source_job_id) ||
    !identity(origin.source_execution_id) ||
    origin.source_execution_id === run.id ||
    !(origin.source_run_id === null || identity(origin.source_run_id)) ||
    origin.source_run_id === run.id ||
    !identity(origin.store_id) ||
    !sha(origin.request_hash) ||
    !sha(origin.archive_sha256) ||
    !Number.isSafeInteger(origin.source_submission_seq) ||
    origin.source_submission_seq < 1 ||
    !["complete", "partial"].includes(origin.archive_kind) ||
    typeof origin.recovered_at !== "string" ||
    !Number.isFinite(Date.parse(origin.recovered_at))
  )
    throw new Error(
      "Recovered evidence lineage is invalid for this local Run.",
    );
  return origin.source_execution_id;
}
