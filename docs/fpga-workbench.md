# FPGA Workbench

The separate **HIL → FPGA Lab → Workbench** target authors a bounded executable graph and
connects virtual instruments to named nodes and edges. Source → one to four serial FIFOs → Sink
executes as generated SystemVerilog in Verilator and as an independent SimPy model. A Ready
controller drives the sink's receiver schedule. This is single-clock reference RTL verification,
not a general analog circuit solver, Qualcomm internals, physical FPGA timing or hardware HIL.
Existing RTL A/B, Apollo, SA8650 and FPGA timing targets remain distinct.

## Author, execute and inspect

Start with one FIFO, two FIFOs, stall/recovery or reset. Place and connect supported blocks,
edit FIFO depth and source length, configure receiver stalls/reset cycles, and attach instruments
to nodes or edges. Save the graph and instrument layout as an immutable revision, validate,
execute, select the saved result and inspect one clock across block state, wire values and probes.

Drafts can be saved while wiring is incomplete. Validation returns errors with node, edge and
probe IDs, so missing wiring, fan-out/fan-in, loops, unsupported signals or orphan probes can be
located in the editor. Execution requires a valid complete graph; an invalid graph is never
silently reinterpreted as a different supported graph. Malformed identities, parameter bounds
and schema errors are rejected even at save time.

Two comparisons are separate: SimPy versus Verilator for the same saved graph, and one saved
RTL scenario versus another. Instrument panels offer digital logic, data values, buffer state
and transfer counters. The selected cycle maps to actual saved node/edge trace values. VCD,
generated RTL, input stimulus, transfers and comparison evidence remain downloadable.

## Graph, scheduling and validation contract

- Fixed 32-bit words and 100 MHz virtual clock. Exactly one `source`, `sink` and `ready`, plus
  1–4 `fifo` nodes in one serial stream. Branches, merges, feedback, multi-clock/CDC, arbitrary
  RTL upload and analog components are outside this adapter.
- Source parameters: `words`, default 4,096, range 1–16,384. FIFO parameters: `depth`, default
  64, range 2–1,024 independently per FIFO. Ready parameters: `stalls`, sorted non-overlapping
  half-open windows `[start_cycle, end_cycle)` where the sink's ready signal is zero.
- Shared settings: `max_cycles` defaults to 20,000 (16–100,000); `resets` defaults to `[0, 1]`,
  sorted/unique, includes cycle 0 and lies inside the cycle budget. Seed defaults to 42.
- A graph node has `id`, `type`, `label`, `position: {x, y}` and typed `params`. Edges have `id`,
  `source`, `source_port: out`, `target` and `target_port: in | ready`. Only a Ready output can
  connect to `sink.ready`; all other supported edges carry the valid/ready/data stream.
- A probe has `id`, `target_type: node | edge`, `target_id`, `signals` and
  `instrument: logic | data | buffer | counter`. `panel_order` holds probe IDs. Unsupported
  targets/signals and stale panel IDs are validation errors, while the unfinished draft remains
  saveable. The templates response publishes `signal_registry` for each node/edge type.
  Buffer instruments require a FIFO `occupancy` or `occupancy_after` signal, optionally
  `full`/`empty`. Counters count asserted boolean cycles, or accepted transactions for
  `accepted*` signals; data and occupancy values cannot be used as count inputs.
- FIFOs use synchronous active-high reset, reset priority, valid/ready rising-edge transfers,
  no empty fall-through, and simultaneous read/write at full capacity. Every FIFO observes
  the same pre-edge state; payload does not bypass an empty FIFO on that edge.
- Normalized signal values are pre-edge at `cycle * 10 + 5 ns`. `occupancy_after` is post-edge;
  reset flushes accepted-but-buffered words from all FIFOs and does not rewind the source.
  Source/edge/sink transfer observations bind directly to the graph's stable IDs.
- Execution stops when all source words were accepted and the pipeline drained, or its cycle
  budget expires. A returned `timeout` verdict preserves partial traces, `pending_words` and
  `unsubmitted_words`. Infrastructure `completed` means the calculation ended, not necessarily
  that validation passed. Compile/process failures, wall-time timeout and cancellation are
  reported separately. Host wall time is distinct from virtual clock time.

## Immutable presentation and executable identity

Each saved `fpga-workbench/1` revision has a `sha256` covering the full graph, labels, positions,
probes, panel order and revision metadata. Every experiment freezes that exact revision.

A separate `execution_hash` covers a canonical ordering of node IDs/types/parameters, edge
IDs/endpoints/ports, shared settings and seed. It excludes the name, labels, positions, probes,
panel order and graph array ordering. Moving/renaming a block or rearranging/adding instruments
therefore creates a new saved revision without changing executable identity. A depth, wiring,
seed or scheduling edit changes the execution hash and requires a new compatible execution.
Changing an ID also changes it because traces and instruments bind to those IDs.

Retained results keep their original revision provenance. A compatible current layout may
inspect them only when its execution hash matches; presentation edits do not relabel old results
as a newly executed revision. Invalid or incompatible edits are visible and cannot masquerade
as fresh execution. The full authoring hash and execution hash answer different questions.

## API, SDK and installation

The project-scoped prefix is `/api/v1/hil-sim/fpga-workbench`:

| API | Behavior |
|---|---|
| `GET /templates`, `GET /readiness` | Presets, schema, signal registry and local tool availability |
| `POST /validate` | Unsaved authoring definition → `valid`, `errors`, `warnings`, `compiled`, `execution_hash` |
| `POST /benches` | Save a revision, including incomplete structural drafts; requires `Idempotency-Key` |
| `GET /benches`, `GET /benches/{id}?revision=…` | Read project revisions with verified integrity |
| `POST /benches/{id}/preview` or `/preflight` | Validate an immutable `{project_id, revision, sha256}` reference |
| `GET /executions` | Project workbench execution history |

Save bodies contain `name`, `graph`, `settings`, `seed`, `probes`, `panel_order`, `project_id` and
optional `id`/`expected_revision`. Optimistic revision checks prevent overwriting another edit.
Validation errors carry `code`, `message`, `node_ids`, `edge_ids` and `probe_ids`. A valid
compilation gives ordered FIFO IDs/depths, source/sink/ready IDs, ordered stream edge IDs and
one ready edge ID, with a normalized executable config and its execution hash.

Preview returns the generic `experiment` and `preview_hash`; creation, submission, polling,
cancellation and artifact/export reads use the existing `/api/v1/experiments` API. Kind is
`fpga_workbench`, adapter is `verilator-workbench-ab`, fidelity is `rtl_reference_graph`.

Python SDK: `fpga_workbench_templates`, `fpga_workbench_readiness`, `validate_fpga_workbench`,
`save_fpga_workbench`, `fpga_workbench`, `fpga_workbench_benches`, `preview_fpga_workbench`,
`execute_fpga_workbench`, `fpga_workbench_executions`, `export_fpga_workbench_execution`.
Generic experiment cancellation and SHA-verified artifact downloads apply unchanged.

Install Verilator, a C++ compiler and optional adapters in API and worker environments:

```sh
brew install verilator
.venv/bin/python -m pip install -e integrations/rtl_lab -e integrations/fpga_workbench
```

Readiness reports executable/version availability, not a live worker's proven readiness.
The worker checks pinned source/tool identity, compiles the generated design and executes it;
missing tools fail closed. Cached binaries are content-addressed and integrity checked.

## Retained native workflow and evidence

`examples/fpga_workbench_workflow.py` defaults to API 8130 and UI 5230 and starts no services:

```sh
.venv/bin/python examples/fpga_workbench_workflow.py
```

It saves/executes all four presets through the public SDK, records concrete test/experiment/job
receipts and retry keys before polling, verifies every artifact's SHA-256/size and each graph's
execution hash, and checks the same accepted input-word sequence across scenarios. The workflow defaults to
`docs/validation/fpga-workbench-20261014.json`; the final verified campaign is preserved separately
in [final native receipts](validation/fpga-workbench-final-20261014.json), with full traces and cycle-384 links;
original artifacts are kept beside it. Existing evidence is not overwritten by a new campaign.
Use `--resume` to reuse the exact saved campaign, verify existing files without replacing them,
and continue missing work. Corrupt files stop recovery rather than being silently replaced.

Execution artifacts include both normalized traces, comparison/summary/build JSON, VCD,
source stimulus and native cycle CSV, delivered/reset-discarded/pending transfer CSV, generated
top-level RTL and harness, provided FIFO RTL, compiler/runtime logs and sealed execution export.
Source/configuration/tool/version/seed/artifact hashes retain provenance; they do not establish
physical correctness or verification beyond the exercised supplied models.

Next adapters can connect user RTL or selected external circuit engines. Multi-clock/CDC,
analog/SPICE components, FPGA-board measurement and Apollo shared execution need their own
contracts and verification; the current graph editor does not imply those capabilities.


## Final native campaign · 2026-10-14

The final adapter, including mismatch localization, was executed through the API and worker
with Verilator 5.052 and SimPy 4.1.1. All four engine comparisons passed. The campaign verified
all 56 native artifact hashes/sizes and sealed exports, the saved graph execution hashes, and
the identical accepted sequence of 4,096 source words. Each run drained with zero pending and
unsubmitted words. All four executions pin the same current adapter/tool identity.

| Graph/scenario | Output words | Reset discarded | Peak buffered words (all FIFOs) | Input-stalled cycles | Total cycles | Max delivered latency (cycles) |
|---|---:|---:|---:|---:|---:|---:|
| One FIFO | 4,096 | 0 | 1 | 0 | 4,099 | 1 |
| Two FIFOs | 4,096 | 0 | 2 | 0 | 4,100 | 2 |
| Two FIFOs + stall/recovery | 4,096 | 0 | 128 | 130 | 4,356 | 258 |
| Two FIFOs + stall + reset | 4,062 | 34 | 128 | 95 | 4,322 | 223 |

The reset's 34 discarded words are accepted payload flushed by the specified reset, not
unexplained loss. Stall/recovery delivers every source word after backpressure clears. The
one-versus-two-FIFO latency changes by one clock, demonstrating executed topology semantics.
First one/two-FIFO builds compiled natively; stall/reset reused the verified two-FIFO binary.
These are reference RTL/model results, not measured hardware performance.

- [One FIFO engine comparison](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_f7181c817f7d44ffa22b17a3904b622c&revision=1&execution=job_024423071b9f4f838231d2c0801f1610&cycle=384&window=368&span=64&view=engine)
- [Two FIFO engine comparison](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_e31acc147bd641afac19ded28bfd9d1c&revision=1&execution=job_00b8e95412fd4e468d33e36963589b56&cycle=384&window=368&span=64&view=engine)
- [Stall/recovery engine comparison](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_5aed353c58834d29b8cf6c992f4f66cd&revision=1&execution=job_69b995f0b4814e1b9d31f23a82e07d31&cycle=384&window=368&span=64&view=engine)
- [Reset engine comparison](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_ecb5cb6e1cd841ac8218fab435787b9f&revision=1&execution=job_43889f0ffb804ec49b78849e33d33b90&cycle=384&window=368&span=64&view=engine)
- [Stall/recovery versus normal two FIFOs](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_5aed353c58834d29b8cf6c992f4f66cd&revision=1&execution=job_69b995f0b4814e1b9d31f23a82e07d31&cycle=384&window=368&span=64&view=scenario&compare=job_00b8e95412fd4e468d33e36963589b56)
- [Reset versus normal two FIFOs](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_ecb5cb6e1cd841ac8218fab435787b9f&revision=1&execution=job_43889f0ffb804ec49b78849e33d33b90&cycle=384&window=368&span=64&view=scenario&compare=job_00b8e95412fd4e468d33e36963589b56)

## Browser delivery · 2026-10-14

Related backend/adapter/SDK regression: **152 passed**. Two actual-worker browser workflows
passed, including pointer wiring and node/connection Undo/Redo, a newly added FIFO's native
execution, incomplete-draft saving, probe edits without rerun, exact resume/downloads, timeout
failure, and the full-FIFO simultaneous read/write recovery boundary. Production build passed;
the existing frontend chunk-size warning remains.

The [prepared two-FIFO comparison](http://127.0.0.1:5230/#hil-sim?project=default&mode=fpga&target=fpga-workbench&bench=fpwb_5aed353c58834d29b8cf6c992f4f66cd&revision=3&execution=job_69b995f0b4814e1b9d31f23a82e07d31&cycle=384&window=368&span=64&view=scenario&compare=job_00b8e95412fd4e468d33e36963589b56)
adds a FIFO 2 buffer probe and reorders panels in presentation revision 3. It retains the
original revision-1 native execution and matching execution hash; no rerun is implied.
At cycle 300 both FIFOs are full and stalled. At cycle 384 both are still full but accept
simultaneous reads/writes as ready recovers; full capacity alone does not mean backpressure.
[UI receipt, source hashes and verification logs](validation/fpga-workbench-ui-20261014.json)
and [captured screen](validation/fpga-workbench-20261014.png) preserve the checked state.
