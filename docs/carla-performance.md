# CARLA checkpoint performance

The checkpoint optimization reduces repeated serialization, copying and SQLite blob writes while retaining every existing checkpoint and journal boundary. It does not change simulation time steps, inputs, control policies, observations, metrics or verdict thresholds. The public adapter and wire contract remain **0.5.0**; the immutable source archive identifies this implementation revision.

## Actual Linux comparison

These are individual, sequential, instrumented native runs on the same RTX 2070 SUPER host, CARLA build `1ae5356`, `Town10HD_Opt`, seed 42 and four seconds of simulation. They are not a fleet throughput benchmark or a real-time guarantee.

| Mode | Previous source D | Optimized source | Reduction | Checkpoints / observations |
| --- | ---: | ---: | ---: | --- |
| Live LiDAR stack | 55.214 s | 28.362 s | 49% | 430 / 81, unchanged |
| Ground-truth controller | 19.414 s | 13.443 s | 31% | 351 / 81, unchanged |

The intermediate copy/cache-only live run took 45.727 s. The final candidate also deduplicates private checkpoint artifact storage. All 81 trajectory rows and every metric exactly match their corresponding baseline. Live raw XYZI and consumed XYZ buffers, every tick's observation/decision/control, 80 control acknowledgements and 80 next-frame observations also match. Each run restores the original 23 actor identities, settings and weather.

This preserves the live run's **failed** verdict: maximum jerk remains 11.362139 m/s³ against the unchanged limit of 10. The ground-truth run passes its existing criteria and does not use captured RGB/LiDAR as controller inputs. Different executions retain different execution identities, native frames/times, archives and provenance; their entire archives are not asserted to be interchangeable.

The optimized source is installed in the normal Linux worker with profiling and fault injection disabled. Two subsequent normal platform jobs also completed: GT Run `fd7f5f7e` passed with an execution duration of 13.180 s; live Run `3afd9887` completed with its failed driving verdict in 28.254 s. Their physical results exactly match the corresponding baseline, all native artifact bytes match the sealed archives, and both were inspected in the actual UI. Normal-queue execution duration and instrumented client wall time have different measurement boundaries.

The [validation report](validation/carla-checkpoint-performance-2026-09-13.json) records all eight actual invocations: five profiles, one after-ACK process-kill trial and two normal platform runs. It also records the 446 affected Python tests, independent private-checkpoint reconstruction, old-reader HTTP 503 with unchanged database bytes, original data preservation and world cleanup. A local collection-folder omission and an acceptance controller's early process-identity observation were corrected using the same execution evidence; neither caused resimulation. No browser file-save acceptance is claimed.

## Storage and reader compatibility

Live checkpoint projection copies only the running tick rows it modifies. Serialization and pure validators finish synchronously before subsequent observations can mutate history. A single bounded MCAP cache compares every source-message value and complete metadata, with the same source and artifact budget checks on reuse.

Private SQLite checkpoints use an `AXIOMCHK2` binary prefix followed by a canonical `carla-checkpoint-refs/1` descriptor. Artifact names, types, order, sizes and content hashes remain distinct from the deduplicated bytes. A FULL-synchronous transaction writes new blobs, replaces the descriptor, removes unreferenced blobs and commits the checkpoint cursor together. It does not batch or skip checkpoints. The exact bounded public failure envelope is materialized when recovering an abandoned execution.

The new reader accepts both the previous full-wire checkpoint and the new descriptor format. An older source D worker cannot read the new private format: it fails before sealing an archive. Keep the newer reader when an attempt has a new-format checkpoint; do not downgrade or replace its store to make an inspection succeed. Completed public archives retain their existing format. Compatibility with an arbitrary future adapter version is not established by keeping this public version unchanged.

## Reproducing the profiling method

The exact acceptance wrapper is preserved as [profile_carla_worker.py](../scripts/profile_carla_worker.py). It uses the installed native adapter and existing worker authentication, persistent store and engine ownership locks. On the Linux engine host, configure the same ordinary `ADE2E_CARLA_*` worker environment, choose an unused loopback port and a private output directory, then run:

```sh
AXIOM_CARLA_PROFILE_DIR=/private/carla-profiles \
AXIOM_CARLA_PROFILE_PORT=8101 python scripts/profile_carla_worker.py
```

Submit an explicitly new execution identity through `RemoteCarlaAdapter` to that worker and preserve its request, status, sealed archive and profile together. The script does not submit platform jobs or replace another worker. A lost observation or request timeout is not permission to repeat the simulation; inspect the same execution identity using the [recovery contract](carla-recovery.md).

Function profiles measure the runner thread's wall and CPU times. Inclusive parent/child columns overlap and must not be summed. Process CPU can include other threads. The wrapper adds overhead; final sealing, response encoding and transport are outside its runner span but are included in the caller's measured wall time. A killed process may leave no profile JSON, so this diagnostic file does not replace the durable journal.

For a local authored-world check of instrumentation invariance, install the development test dependencies and run:

```sh
.venv/bin/python scripts/profile_carla_worker.py --self-test
```

This compares results, artifact bytes and journal data with and without instrumentation using temporary SQLite stores. It neither connects to CARLA nor demonstrates physical simulator performance.
