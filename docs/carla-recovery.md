# CARLA durable execution and evidence recovery

The deployed adapter is now 0.6.0. New dispatch requires exactly 0.6.0; inspection and retrieval accept stored 0.5.0 or 0.6.0 identities. Original complete and partial archive bytes, inner worker versions and hashes stay unchanged. Unknown versions and a historical 0.5.0 request containing the new dense recipe are rejected. Actual remote reads of three prior 0.5.0 archives matched their original bytes; this is distinct from repeating fault-injection acceptance on 0.6.0. [Profiles and acceptance](carla-sensor-profiles.md).

The optional CARLA adapter 0.5.0 reserves an execution identity for both the ground-truth controller and live LiDAR stack. An HTTP response is no longer the only copy of the outcome. The Linux worker commits the request, a bounded native journal, and coherent partial checkpoints to a private execution store. It seals an exact result archive before returning it.

This is an execution reliability feature. It does not establish sensor accuracy, actuator calibration, real-time performance, or vehicle safety. A failed metric remains failed after recovery.

## Platform and agent workflow

1. Submit a simulation through the versioned core API. The queue pins the worker store UUID and immutable attempt hash before dispatch.
2. If local execution fails or is interrupted, inspect `GET /api/v1/jobs/{job_id}/remote-execution`. This addresses the reserved execution ID and does not issue a simulator command. A missing record does not prove that no external effect happened; an unavailable status is not a completed execution.
3. Submit `POST /api/v1/execution-recoveries` with an `Idempotency-Key`, source job and a note. The worker checks the store identity pinned before dispatch and the exact terminal archive identity before publication. The normal durable queue retrieves and validates the archive. Retry a lost submission response with the same key and body.
4. Open the linked recovered Run, manifest, and original archive. The original job and failed Run are unchanged. The new Run has its own storage ID and an explicit original execution ID for immutable source artifacts.
5. Resolve engine cleanup separately. A recovered success does not clear an existing core resource quarantine. Cleanup status `confirmed` means the execution observed the cleanup API acknowledgements/returns; it is not independent proof of an empty, restored world.

The UI and `/agent/tools` use these same endpoints. A recovery job only retrieves evidence; cancelling it does not cancel, stop, or repeat the original simulator execution. Original experiment submission order is retained when selecting effective requirement evidence.

## Worker deployment

Set `ADE2E_CARLA_EXECUTION_STORE` to a private persistent directory on the Linux worker. Keep the worker bearer token, explicit exclusive-server declaration, supported native Python wheel, and a single engine owner. The store UUID must survive process/release restarts. Do not replace the store to bypass an unresolved execution.

- `POST /v3/inspect` accepts the immutable `execution-attempt/1` request. It reports the execution in this store without manipulating CARLA.
- `POST /v3/simulate?store_id=...` requires the inspected store UUID. Repeating an existing execution ID never starts another simulation. A changed request under that ID is rejected.
- `GET /v3/executions/{execution_id}/archive?store_id=...&archive_sha256=...` returns the exact sealed bytes.
- `GET /v3/executions/{execution_id}/journal` returns verified journal metadata and blob references. Raw journal blobs remain in the private SQLite store; they are diagnostic observations, not an inferred control replay.

All routes require the configured bearer token. When a durable store is enabled, legacy `/v1/simulate` and `/v2/simulate` are disabled so uncorrelated dispatch cannot bypass the store. The old wire codecs remain available for historical/direct compatibility tests outside this deployment mode.

## Persistence and uncertainty

Each attempt uses a SHA-256 directory key, a SQLite FULL-synchronous database, and an OS owner lock. The worker commits native intent before an external mutation, validated control ACKs separately, raw physics observations before optional RPCs, and raw source bytes before decoding. Snapshot checkpoints replace the previous complete checkpoint atomically. Journals retain a chained digest over ordered events and source blob hashes.

The checkpoint performance revision stores a bounded `AXIOMCHK2` / `carla-checkpoint-refs/1` descriptor and deduplicated blobs in that same atomic transaction. It checks exact public wire capacity before commit and reconstructs the original public failure payload on recovery. The new reader also supports old full-wire checkpoints. An older source D reader refuses a new-format partial checkpoint before sealing it; keep the new reader for such attempts. Completed public archives are unchanged. See [storage compatibility and performance evidence](carla-performance.md).

Store identity and engine reservations are published from fully written, fsynced private temporary files. A process killed during first initialization cannot publish an empty store UUID. Existing identities are never reset to make a failed startup pass.

A stopped owner lock permits sealing an interrupted archive from the last committed checkpoint. It does **not** certify that CARLA stopped, that an unacknowledged command was rejected, or that cleanup succeeded. Even a checkpoint containing the final N+1 observations remains partial if no terminal result seal was committed. Commands after the checkpoint are never resumed. The journal can contain later individual observations than the last coherent checkpoint; these are not silently added to the published trajectory.

The live stack's partial trace uses its existing complete denominator and source/hash contracts. The legacy controller records a separate diagnostic trajectory/control prefix with async returns clearly distinguished from ACKs, plus retained selected sensor captures. Neither failure path invents success metrics.

Limits are explicit: at most 1,000 attempts and 4 GiB recorded store usage before new dispatch is refused; at least 512 MiB free space; 12,000 journal entries and 40 MiB aggregate event/blob content per attempt; 2 MiB per raw journal blob; 48 MiB per wire archive/checkpoint. Store eviction, quotas across hosts, and archival retention administration remain future work. Host power-loss/storage durability depends on the filesystem and storage honoring fsync; process-crash tests do not establish hardware power-loss safety.

## Explicit cleanup on the engine host

`python -m axiom_carla.reconcile inspect` builds a read-only ownership plan for one quarantined attempt. `cleanup` requires the same store UUID and inspected journal digest. Both require the worker owner lock and engine lock to be free.

The command checks the original lossless decimal world episode ID, unique actor roles and blueprints, any acknowledged actor IDs, and current settings/weather against this attempt's original or requested values. Foreign actors, reused IDs, changed episodes, unavailable properties, and other world changes stop cleanup before deletion. It never reloads a map or resumes simulation controls. An exact post-cleanup readback is required before clearing this worker's quarantine; an immutable intent and outcome report remains on disk. The platform's resource reconciliation is still a separate explicit operation.

A process can die after the complete archive is sealed but before its reservation is removed. The complete archive remains retrievable, while new dispatch stays blocked. The same explicit reconciliation checks the stopped owner and world before releasing that reservation; it does not rewrite the sealed outcome. This boundary has a real Linux process-kill acceptance case.

```sh
python -m axiom_carla.reconcile inspect --store /private/executions \
  --execution-id run_EXACT_ID --expected-store-id STORE_UUID
python -m axiom_carla.reconcile cleanup --store /private/executions \
  --execution-id run_EXACT_ID --expected-store-id STORE_UUID \
  --journal-sha256 INSPECTED_JOURNAL_SHA256
```

## Release evidence

Protocol and authored native-world tests are separate from actual Linux CARLA runs. The [dated validation report](validation/carla-durable-recovery-2026-09-13.json) records source manifests, original/recovered IDs, exact hashes, cleanup reports and verification scope. The standalone Linux campaign covers normal live and ground-truth execution plus process death at startup, before control, after ACK, after physics, after raw LiDAR, after a selected legacy capture, during cleanup, and after terminal sealing. Partial archives contain no completed metrics. A complete live execution still fails its existing jerk criterion.

The subsequent checkpoint optimization reduced instrumented client wall time from 55.214 to 28.362 seconds for live control, and from 19.414 to 13.443 seconds for ground-truth control, preserving all checkpoint boundaries and physical outputs. A new actual after-ACK SIGKILL case verified exact recovery from preserved descriptor/blob bytes, old-reader refusal without changing the attempt, and separate owned-actor cleanup. Two final normal platform runs and UI replay were also checked. These individual four-second trials do not establish real-time or fleet throughput; the [performance report](validation/carla-checkpoint-performance-2026-09-13.json) keeps instrumented and normal-queue measurements separate.
