# FPGA RTL Lab

The separate **HIL → FPGA Lab → RTL A/B** target saves and executes a provided single-clock
SystemVerilog FIFO in Verilator and compares it against an independent SimPy queue model.
This is reference RTL verification, not Qualcomm internal RTL, CSI PHY, FPGA board timing,
electrical simulation or a hard real-time HIL guarantee. SA8650 and Apollo remain separate.

## Use the connected workflow

Choose Normal, Stall/recovery, Repeated stalls or Reset; edit depth, input length, seed and
receiver/reset schedules; save a revision and execute. The normal experiment worker compiles
and runs the bundled RTL, publishes artifacts and preserves the exact test reference.

- **Engine comparison:** selected execution's Verilator and SimPy signals on one clock axis.
- **Scenario comparison:** two saved RTL executions, such as normal versus stall/recovery.
- The Source → FIFO → Sink diagram uses the selected saved cycle. Digital waveforms support
  moving, zooming, selecting a cycle and jumping to the first mismatch. Summary metrics include
  accepted inputs/outputs, reset losses, peak occupancy, stalled cycles and word latency.
- Deep links preserve the test revision, execution, comparison, view, selected cycle and visible
  waveform window. Retained results are distinguishable from a newly submitted execution.

Install the optional adapter in API and worker environments:

```sh
brew install verilator
.venv/bin/python -m pip install -e integrations/rtl_lab
```

Verilator and a C++ compiler must be present. Readiness reports local executable/version
availability; successful compilation and worker execution are verified separately. Missing
tools fail closed and do not fall back to a purported RTL result from SimPy. Only bundled
trusted RTL/harness code is compiled; arbitrary code upload is outside this first version.

## Public API and SDK

The project-scoped prefix is `/api/v1/hil-sim/rtl-lab`:

| Route | Function |
|---|---|
| `GET /templates`, `GET /readiness` | Presets, constants, constraints and local tool availability |
| `POST /tests`, `GET /tests`, `GET /tests/{id}?revision=…` | Immutable, hash-addressed test revisions |
| `POST /tests/{id}/preview` or `/preflight` | Validate a pinned test and prepare the experiment |
| `GET /executions` | Saved project execution history |

Writes require `Idempotency-Key`. Tests contain `name`, `project_id`, `config`, `seed` and
optimistic `id`/`expected_revision` for edits. Preview actions use `project_id`, `revision` and
`sha256`. Preview returns `experiment` and `preview_hash`; creation, submission, cancellation,
polling and artifact/export reads use the existing `/api/v1/experiments` API. The experiment
kind is `fpga_rtl`, adapter is `verilator-fifo-ab`, fidelity is `rtl_reference_fifo`.

Python SDK methods include `rtl_lab_templates`, `rtl_lab_readiness`, `save_rtl_lab_test`,
`rtl_lab_test`, `preview_rtl_lab_test`, `execute_rtl_lab_test`, `rtl_lab_executions` and
`export_rtl_lab_execution`. Generic `cancel_experiment`, `download_experiment_artifact` and
`export_experiment_execution` also apply. Keep the same idempotency key when retrying an
uncertain request; do not create a second run merely because a response was lost.

The repeatable native workflow starts no services and defaults to API 8120/UI 5220:

```sh
.venv/bin/python examples/rtl_lab_workflow.py --output docs/validation/rtl-lab-20261014.json
```

It saves all four presets, records write/submission retry keys and concrete receipts before
polling, downloads and checks every artifact's SHA-256 and size, and preserves both full traces,
comparison and saved URLs at cycle 384. It refuses to overwrite an existing receipt or artifact
directory. On interruption, use the same command with `--resume`: it keeps the original keys,
test, experiment and job, checks existing downloads without replacing them, and submits only
cases that have no existing job receipt. A corrupt existing file stops recovery rather than
silently overwriting evidence. API, UI and project arguments must match the saved campaign.

## FIFO and time semantics

- Fixed 32-bit data and 100 MHz virtual clock. Defaults: depth 64 words, 4,096 source words,
  seed 42, maximum 20,000 cycles. Allowed depth 2–1,024, words 1–16,384, cycle budget 16–100,000.
- A rising-edge transfer occurs only with `valid && ready`, unless synchronous active-high
  reset is asserted. Reset takes priority and discards already accepted, still queued words.
  Source sequence numbering does not restart after reset; unaccepted input remains available.
- There is no empty fall-through. A full FIFO accepts a word when a simultaneous read frees
  a slot. Valid data remains stable during backpressure until accepted or reset.
- Receiver stalls are sorted, non-overlapping half-open windows `[start_cycle, end_cycle)`.
  Reset cycles are sorted and unique, must include cycle 0 and lie within the cycle budget.
  Default reset is `[0, 1]`; the reset preset also resets cycle 160 during the 128–383 stall.
- Signal fields are sampled before the rising edge at `cycle * 10 + 5 ns`. `occupancy` is
  pre-edge; `occupancy_after` is post-edge. They can differ on a transfer or reset. VCD retains
  the actual clock and signal transitions; normalized traces make engine comparison explicit.
- The independently implemented SimPy queue creates the deterministic source/sink stimulus;
  the same frozen stimulus is then replayed through Verilator. A scoreboard separately checks
  order, handshakes, occupancy, full/empty rules and stability. This is independent execution
  and comparison, not a per-clock RPC co-simulation with Apollo or a hardware device.
- After all input is accepted and the FIFO drains, execution ends. A configured virtual cycle
  limit yields `summary.verdict = timeout` with partial traces. The job may be `completed`
  because the calculation finished; this is **not a validation pass**. `pending_words` means
  accepted but still queued; `unsubmitted_words` means source words not yet accepted. Reset
  losses remain separate, so `input = output + reset_discarded + pending_words`.
- Compile failures, process crashes, wall-time timeouts and cancellation are infrastructure
  failures/cancellations, distinct from a returned comparison mismatch or virtual timeout.

## Evidence and remaining work

Each execution pins revision/input identity, seed, adapter and tool versions, RTL/harness and
reference source hashes, compile flags, binary identity and artifact hashes. Compilation uses
a content-addressed cache; reuse is reported and the cached binary hash is checked.

Artifacts: `rtl-trace.json`, `simpy-trace.json`, `comparison.json`, `summary.json`, `build.json`,
`fifo.vcd`, `stimulus.csv`, `rtl-cycles.csv`, `transfers.csv`, `compile.log` and `run.log`, plus
the sealed generic execution export. `transfers.csv` separates delivered, reset-discarded and
pending records; latency applies only to delivered data. Host CPU wall time is distinct from
virtual cycles/time. A passed comparison covers the supplied design and exercised inputs.

Follow-up work is user RTL adapters, multi-clock/CDC, deeper formal verification, a general
block-wiring editor, Apollo co-simulation and ngspice electrical circuits. Those are not implied
by this bounded FIFO slice. Verilator, SimPy and compiler licensing remain distinct from future
vendor IP/model licenses and board or physical validation.

## Verified native campaign · 2026-10-14

[Receipts and both full traces](validation/rtl-lab-20261014.json) retain four actual 4,096-word
executions, source/tool/binary identity, every artifact SHA-256/size and resume URLs.

| Preset | Delivered | Reset discarded | Input stall cycles | Peak occupancy | Cycles |
|---|---:|---:|---:|---:|---:|
| Normal | 4096 | 0 | 0 | 1 | 4099 |
| Stall/recovery | 4096 | 0 | 193 | 64 | 4355 |
| Repeated stalls | 4096 | 0 | 842 | 64 | 5004 |
| Reset during stall | 4063 | 33 | 159 | 64 | 4322 |

Every engine comparison and independent scoreboard passed. At 100 MHz, normal word latency is
10 ns; the stalled example reaches a maximum 2,570 ns. These are reference circuit logical
latencies without routing or electrical delay. The first build/run took 2.74 s on this host;
cached runs took about 0.15 s, distinct from virtual time.

The workflow recovered its original completed normal job after an interrupted download, without
submitting a duplicate. Zero-byte logs are valid hashed artifacts. Compilation uses a private
whitespace-free temporary directory because Verilator's GNU Make rejects spaces in build paths;
only the verified binary/receipt/log are atomically published to the project cache.

Validation: 111 focused backend/adapter/SDK tests and two actual-worker browser workflows
passed. Production frontend build passed with the existing bundle-size warning. Manual browser
inspection confirmed cycle250 occupancy64 versus1 and simultaneous input/output at recovery
cycle384. The screenshot is `validation/rtl-lab-20261014.jpg`.
