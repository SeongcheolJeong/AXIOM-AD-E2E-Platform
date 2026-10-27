# Durable execution jobs · implementation contract

This module moves engine execution outside the API request and DB transaction.
It implements a local SQLite-backed queue with separate worker processes. It is
not a distributed cluster, automatic remote-engine recovery, or exactly-once
external execution guarantee. The original commercial roadmap remains open.

## Public API

All routes share `/api/v1` and existing bearer authentication.

- `POST /jobs`: existing `RunCreate` body; required `Idempotency-Key` (1–200
  characters); returns `JobDetail` with 202. Identical key and body return the
  original job even after scenario edits, adapter updates, failure or restart.
  A changed body under that key returns 409. Snapshot and adapter version are
  resolved only for the first accepted submission.
  Keys are scoped to the endpoint: `/jobs` and compatibility `/runs` have separate
  namespaces. Retry on the same endpoint; switching endpoints with the same key
  is a new execution, not recovery of the old one. Prefer reading the original ID.
- `GET /jobs?status=&limit=100&before_seq=` returns `{items, next_before_seq}`.
  Newest submissions first, limit 1–100; continuation is the last sequence when
  more rows exist. Sequence identity is independent of completion order.
- `GET /jobs/{id}` returns `JobDetail`.
- `POST /jobs/{id}/cancel` records an explicit cancellation request and returns
  the latest detail. A terminal job remains terminal.
- `GET /workers` returns worker identity, `heartbeat_at`, `state`
  (`idle|running|stopped`), `job_id` and computed `alive`. A recent heartbeat is
  process connectivity, not engine fidelity or completed work.
- `GET /execution-resources` shows external adapter ownership/quarantine.
- `POST /execution-resources/{adapter_id}/reconcile` requires the interrupted
  `job_id`, a nonempty operator `note`, and `confirmed_stopped: true`. Use only
  after stopping the former dispatcher and independently confirming remote
  engine cleanup. This unlocks future work; it does not turn unknown history
  into a completed or cancelled result. Health-check success is insufficient.

The compatibility `POST /runs` uses the same submission/claim/resource path,
then waits for a terminal result. New automation should submit jobs and poll
their IDs; a client timeout does not mean the engine stopped.

## Shapes and states

JobSummary: `id`, `submission_seq`, `scenario_id`, `scenario_name`,
`scenario_revision`, `adapter_id`, `adapter_version`, `status`, `created_at`,
`updated_at`, nullable `started_at`, `dispatched_at`, `finished_at`, `run_id`,
`error`, `worker_id`, `lease_expires_at`, `cancel_requested_at`, plus
`input_hash`, `cancellation_supported`, `outcome_uncertain`.

JobDetail adds the effective immutable `scenario_snapshot`, original `request`,
and append-only `events: [{at, type, message}]`. Responses include the latest 200
events and `events_total`; older events remain in the relational audit table.
`failure_http_status` retains known preflight/worker failure classification for
compatibility responses. Times are UTC ISO strings.
`created_at` is submission time; start and dispatch are separate observations.
Run provenance hashes the effective input and pinned adapter identity/version.

| State | Meaning |
| --- | --- |
| queued | Persisted; no worker has claimed execution |
| running | Worker owns a live lease; dispatch intent is separately recorded |
| cancel_requested | Request received, actual interruption not yet acknowledged |
| completed | Complete result published; test verdict may still be failed |
| failed | Execution/preflight/publication failure; inspect error and run evidence |
| cancelled | Never dispatched, or a cooperative adapter acknowledged stopping |
| interrupted | Worker ownership expired or outcome could not be established |

The reference adapter checks cancellation during computation. Existing plugins
keep their `execute(scenario)` contract and may explicitly implement
`execute_cancellable(scenario, should_cancel)`. A remote adapter without that
hook may finish after cancellation was requested; keep its result and record
`cancel_not_honoured`. Do not discard evidence or falsely report a remote stop.

## Ownership and evidence

Claim, dispatch intent, heartbeat, cancellation and publication use short DB
transactions. Engine calls run without a DB session. Heartbeats and publication
must match the owner token and an unexpired lease; an expired lease cannot be
revived. Job result, Run, artifact rows and legacy idempotency mapping commit
together. Late workers cannot publish into a newer ownership state.

Queued jobs survive API/worker restarts. Claimed jobs are not automatically
requeued after lease loss: the engine may already have executed. External adapter
resources remain quarantined until explicit reconciliation. The current resource
key is the adapter ID within one workspace/endpoint configuration; shared remote
engines across different workspaces need a future engine-side ownership contract.

Current baseline evidence uses submission order among jobs that entered dispatch.
A late older result cannot override a newer attempt. A newest running, interrupted,
failed or cancelled-after-dispatch attempt cannot fall back to an older pass.
Queued/preflight-rejected/cancelled-before-dispatch jobs do not claim new evidence.
Fixed snapshot revision, seed and parameters must still match the current scenario.

SQLite permits one simultaneous writer; keep claims/publication bounded and
measure contention before scaling. [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)
and [SQLAlchemy SQLite driver notes](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver)
inform the transaction design; process and failure tests establish actual behavior.

## Acceptance

Verify real separate processes: API returns before a slow adapter completes;
queued snapshot survives edit/restart; competing workers execute one job once;
killed worker expires without replay; stale publication is rejected; live
heartbeat keeps a slow job alive; queued/cooperative/uncooperative cancellation
states are distinct; newer dispatch blocks older baseline evidence; original
run/recording data survives schema migration. UI/SDK must show the same job and
open its actual run evidence. No fabricated progress percentages.
