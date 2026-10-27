# Engineering Lab

Open ISP RTL now adds pinned Bayer pixel processing and an independent ECP5 OOC
build as a fourth tab. See [Open ISP RTL](open-isp-rtl.md) for its RAW/stream
contract, actual execution evidence and FPGA/Qualcomm fidelity boundaries.

Engineering Lab is an isolated native-engine workflow under **HIL → FPGA Lab → Engineering Lab**.
It executes supplied, bounded Stream32 SystemVerilog in Verilator or a self-contained SPICE
netlist in ngspice. Qualcomm targets are connection profiles whose missing runtime is reported
explicitly. Existing FPGA, RTL FIFO, Workbench, SA8650 and Apollo models remain separate.

## Connected capabilities and boundaries

- **RTL:** edit one to eight Verilog/SystemVerilog source files, save an immutable revision,
  choose an identity/XOR transfer expectation or observation-only mode, and run a native compile
  plus execution. Inspect actual digital signals, transferred words and first mismatch. The
  supplied passthrough, correct XOR and intentionally incorrect XOR demonstrate code behavior,
  not a precomputed animation or inferred full RTL equivalence.
- **SPICE:** edit a bounded R/C/L/V/I/D circuit and time/probe settings; run native transient
  analysis and inspect actual adaptive waveform samples in seconds, volts and amperes. RC step,
  doubled-resistance RC and RLC examples produce native results. Different runs retain their
  own time points; a displayed nearest-sample readout is not an identical-time measurement.
- **Qualcomm:** select SA8650, SM8750 or SM8850 to inspect the connection requirements. An actual
  supported host, target-matched runtime/package entitlement and official execution interface
  are required before such a profile can run. PDFs, architecture names and Software Center do
  not establish an installed ISP simulator. Missing runtime fails preflight without creating a
  fake execution or falling back to Apollo/CPU reference processing.

Save → preflight → execute/cancel → inspect → select a retained A/B result → resume by URL →
download source/results uses the common API and experiment worker. Preserved source content,
configuration hash, seed, adapter/tool identity and artifact hashes accompany every execution.
Simulation scope remains explicit: two-state Stream32 RTL or analog device-model transient,
not physical board measurement, FPGA timing closure or validated Qualcomm ISP output.

## RTL contract and execution boundary

The fixed top is `user_top`; ports are `clk`, synchronous active-high `reset`, `in_valid`,
`in_ready`, 32-bit `in_data`, `out_valid`, `out_ready`, 32-bit `out_data`. Stimulus uses a
100 MHz virtual clock; accepted transfers occur on rising edges when valid and ready are
asserted and reset is inactive. Sources must maintain valid data while stalled. Reset flushes
pending contract data without rewinding the source sequence. Combinational passthrough can
accept input and output on the same edge, so zero-cycle latency is a valid result.
CSV/normalized trace rows carry the rising-edge time but show signals sampled immediately
before that edge. VCD records both phases and its value at the same edge time is after
evaluation; compare the VCD state immediately before the edge for transfer-check readback.

The default stimulus has 256 words, seed 42, 2,048 maximum cycles and resets `[0, 1]`; words
are bounded to 16,384 and cycles to 100,000. Stalls use sorted, disjoint half-open windows.
The checker evaluates the selected contract: `identity`, XOR with a specified unsigned 32-bit
mask, or `observe`. Observation-only does not establish a functional expected-output pass.
A returned failed or timeout verdict is distinct from infrastructure job failure. In particular,
the intentionally wrong XOR can finish execution successfully while failing the identity check.

Files must have plain `.v`/`.sv` basenames, ASCII text, no control characters other than ordinary
line/tab whitespace, at most eight files and 256 KiB combined. External includes, preprocessing
macros, DPI/foreign code, Verilator metacomments and system tasks are rejected. Only `$clog2`,
`$signed`, `$unsigned`, `$bits`, `timescale 1ns/1ns` and constrained default-nettype directives
are allowed. Other timescales and timeunit/timeprecision declarations are rejected so VCD and
normalized trace share the fixed nanosecond clock interpretation.
Unrecognized syntax/top-port contracts may still fail actual compilation; a saved source is not
proof it compiles. Source and compile/runtime errors remain visible.

Trusted supplied RTL execution requires `ADE2E_ENABLE_LOCAL_RTL=1` and a non-team host. Both
parsed `Settings.team_mode` and normalized team environment settings are checked, including
through the generic experiment API and direct worker dispatch. Compiler/runtime processes have
bounded execution and sanitized environments, but **this is not a verified OS sandbox for
untrusted public uploads**. Arbitrary user RTL is disabled in shared/team mode.

## SPICE contract

A netlist starts with an explicit `*` title/comment. It contains 1–128 self-contained resistor,
capacitor, inductor, independent voltage/current source or diode elements. Inline `.model name
D(...)` diode parameters and an optional final `.end` are supported. The adapter generates the
save/transient directives and performs fixed batch execution; user `.control`, `.include`,
`.lib`, `.param`, arbitrary analyses, filesystem paths and external model files are rejected.
This first version uses literal values and source waveforms such as `PULSE`.

`step_s` is the requested maximum native step, from 1e-12 to 1 second; `stop_s` is positive,
at most 10 seconds and no earlier than the step. The requested stop/step ratio cannot exceed
250,000. Adaptive solving can produce additional points; actual sample/file/process bounds
are checked independently and fail visibly if exceeded. Results retain native time points
without resampling. Up to eight unique `v(node)` or `i(Vsource)` probes are supported.

SPICE's `observed` verdict means a native waveform was obtained. It is not a circuit-design
acceptance verdict or physical correlation result. Component values/model parameters, source
conditions, simulation settings and actual solver version define the result's scope.

## Public API and SDK

Prefix: `/api/v1/hil-sim/engineering-lab`.

| Route | Purpose |
|---|---|
| `GET /templates`, `GET /readiness` | Config presets/schema and per-engine tool/runtime availability |
| `POST /tests`, `GET /tests`, `GET /tests/{id}?revision=…` | Project-scoped immutable source/configuration revisions |
| `POST /tests/{id}/preview` or `/preflight` | Validate the exact project/revision/SHA reference and runtime capability |
| `GET /executions` | Saved Engineering Lab execution history |

Save bodies use `name`, `project_id`, `seed`, `config`, optional `id`/`expected_revision` and an
`Idempotency-Key`. `config.engine` discriminates `rtl`, `spice` and `qualcomm`. Revisions use
`engineering-test/1`; full revision SHA, canonical `config_hash`, RTL `source_hashes` or SPICE
`netlist_sha256` preserve exact content. Seed is separately pinned in the test/input manifest.

Preflight actions contain `project_id`, `revision` and `sha256`; the result supplies the frozen
`experiment` and `preview_hash`. Submission/polling/cancellation/export/artifact downloads use
the common `/api/v1/experiments` API. The kind is `engineering_lab`, adapter is
`native-engineering-lab`, fidelity is `native_engineering_reference`. Local version/readiness
checks remain distinct from a verified new worker execution.

Python SDK methods are `engineering_lab_templates`, `engineering_lab_readiness`,
`save_engineering_lab_test`, `engineering_lab_test`, `preview_engineering_lab_test`,
`execute_engineering_lab_test`, `engineering_lab_executions`, `export_engineering_lab_execution`.
Generic `download_experiment_artifact` verifies size and SHA, including valid empty logs.

## Installation, repeatable workflow and artifacts

Install the optional native adapter in the API/worker environments and provide required tools:

```sh
brew install verilator ngspice
.venv/bin/python -m pip install -e integrations/rtl_lab -e integrations/engineering_lab
```

For trusted RTL, explicitly enable local RTL on the non-team API/worker process. The provided
workflow starts no services and defaults to API 8140/UI 5240:

```sh
.venv/bin/python examples/engineering_lab_workflow.py
```

It saves and executes six native examples, then records the unavailable Qualcomm preflight
without submitting an execution. The deliberately buggy RTL must fail its output contract;
SPICE cases are observed waveforms. Every source/config identity and artifact SHA/size is
verified, sealed exports are kept, and all three RTL examples must receive the same accepted
input-word sequence. Receipts retain retry keys before operations and exact execution URLs.
Use a fresh `--output` to preserve another campaign or `--resume` to continue the existing one
without replacing runs or existing verified files. Existing corrupt evidence causes a stop.

The compact default receipt is `docs/validation/engineering-lab-20261014.json` with source identity,
artifact hashes/paths, summaries, a few native sample excerpts and URLs. Full native waveforms
remain in the adjacent artifact directory rather than being duplicated in receipts.
The common `trace.json` records an explicit axis/unit,
signals, native samples, summary and provenance; other artifacts include source/netlists,
generated harness/control deck, VCD or native raw waveform/CSV, transfer reports, compiler/runtime
logs, immutable test and provenance JSON, and sealed execution export as appropriate per engine.
No receipt or UI preview alone is counted as a newly executed engine run.

## Native evidence — 2026-10-14

The saved campaign in [the compact receipt](validation/engineering-lab-20261014.json) completed
six queued native executions with Verilator 5.052 and ngspice. All 69 artifact files were
downloaded and independently checked against their SHA-256 and byte count; six sealed exports
matched their manifest hashes. All three RTL cases consumed the same 256-word input sequence.

| Case | Actual native result | Retained execution / A/B |
|---|---|---|
| Stream32 passthrough | 256 in / 256 out, 0 mismatches, passed | [Run](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_d2c5249b4642427db4f7c5439ea5405e&revision=1&tab=rtl&sample=2&window=0&span=64&execution=job_7ae3a60d1a1940ae93ba8c0749de3f92) |
| Correct XOR | 256 in / 256 out, 0 mismatches, passed | [A/B](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_df9183d96e4c4acbb7dc422e7fc1b235&revision=1&tab=rtl&sample=2&window=0&span=64&execution=job_2224a65998ae48f3a841f1067c7ac434&compare=job_7ae3a60d1a1940ae93ba8c0749de3f92) |
| XOR checked as identity | 256 mismatches; first cycle 2 at 25 ns, failed as intended | [A/B](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_7603053127b84a039658dbd39b7b24ba&revision=1&tab=rtl&sample=2&window=0&span=64&execution=job_f37fa6502cbf4251a2ecf277d5364564&compare=job_7ae3a60d1a1940ae93ba8c0749de3f92) |
| RC, R = 1 kΩ | 5,022 samples; 0.632139 V near 1 ms; 0.993262 V at 5 ms | [Run](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_4132635c85f24ad59ce327e5695508be&revision=1&tab=spice&sample=1000&window=500&span=1000&execution=job_635df597dd71482bba12f6b0dfe21bc9) |
| RC, R = 2 kΩ | 5,022 samples; 0.393484 V near 1 ms; 0.917915 V at 5 ms | [A/B](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_5f4b5ec224a042c6888f70ecd4efd902&revision=1&tab=spice&sample=1000&window=500&span=1000&execution=job_8669e8e96b2b47858027d4ab3c4860dc&compare=job_635df597dd71482bba12f6b0dfe21bc9) |
| RLC | 5,027 samples; peak 1.854467 V; 0.923623 V at 5 ms | [A/B](http://127.0.0.1:5240/#hil-sim?project=default&mode=fpga&target=engineering-lab&test=engt_44e8303de0c044589a665e00a8ff0e24&revision=1&tab=spice&sample=1000&window=500&span=1000&execution=job_24e24a5cbb024b59acdf669e4918129d&compare=job_635df597dd71482bba12f6b0dfe21bc9) |

The intentional RTL mismatch is a completed calculation with a failed contract verdict. The
three SPICE results have `observed` verdicts, without design acceptance criteria. Native RC
samples nearest 1 ms occurred at 1.000049512 ms; RLC uses its own adaptive sample times.

The saved SA8650 connection profile returned preflight HTTP 422 (`not_configured`) and created
no execution. These results exercise supplied digital logic and analog reference circuits;
they do not validate a physical ECU, FPGA board, or Qualcomm proprietary ISP runtime.
