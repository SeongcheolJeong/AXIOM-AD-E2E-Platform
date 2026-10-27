# ADAS pre-board validation

Engineering Lab → ADAS Camera Module → **검증 캠페인** connects saved revisions,
deterministic schedules, native worker execution, frame evidence and export.
The UI, API and Python SDK use the existing Engineering Lab execution contract.
This page describes implemented checks; the dated validation receipt records which
campaigns actually ran and their results.

## Retained execution: 2026-10-17

The [public SDK receipt](validation/adas-preboard-20261017.json) records 19/19
passed cases, including a real four-channel 1080p30 ten-second run with 1,200
committed frames. Across the campaign, 1,477 frames committed and 35 deliberately
faulted frames aborted. Committed pixel errors, protocol errors and aborted frames
made visible by the consumer were all zero. Seeds 42, 7 and 2026 produced three
different RAW input signatures; each zero/random initialization pair matched.
Eight resets per seed and simultaneous faults on three channels recovered while
the unaffected channel continued normally.

| Stall at the saved input phase | Peak FIFO pixels | Overflow | Subsequent frame recovery |
|---|---:|---|---|
| 58 µs | 3741 | No | Passed |
| 60 µs | 3889 | No | Passed |
| 62 µs | 4038 | No | Passed |
| 64 µs | 4096 | Yes | Passed |
| 66 µs | 4096 | Yes | Passed |

These thresholds depend on the saved raster phase and pipeline state; they are
not universal sensor or board timing limits. All eight separate pre-board checks
passed. The same full-HD RTL hash `2998314794b4fe5b345019a2cc69219651fafdf4ff120d92b1e67ec5bf927a18`
passed the public Yosys/nextpnr OOC build at **103.295 MHz** with seed 42 and recorded
ADAS placer timing weight 30. The receipt verifies all 885 campaign and 31 build
artifact hashes and both sealed exports. Physical board readiness remains false.

The [six-case full-HD regression and A/B report](validation/adas-preboard-regression-20261017-comparison.md)
also passed: all 720 RAW hashes, 717 committed RGB hashes, three abort dispositions
and partial hashes matched the previous timing-closure run. Output and terminal
timing changes were zero. The before/after OOC result is 101.368→103.295 MHz,
LUT 9735→9906, FF 9648→10012, BRAM 48 and DSP 4 unchanged. This comparison includes
both RTL retiming and the recorded placer timing-weight change; it does not isolate
their separate contributions. All 3,300 before/after artifacts and 14 sealed exports
were rehashed and verified against the shared execution store.

The legacy module field `acceptance_1080p30` identifies exactly the original
one-second/30-frame test. The campaign uses its distinct `full1080p_continuous`
gate for the actual ten-second run; a false legacy field does not imply reduced
resolution. The UI distinguishes these cases.

## Continuous and combined faults

`adas_validation_campaign` defaults to four independent 1920×1080 RAW12 streams,
30 fps, 100 MHz logic, 4096-pixel input FIFOs and a 300-frame/channel continuous
case. This is a ten-second source window, followed by final pipeline drainage.
It is not an hours-long soak test. The configurable maximum is 1800 frames/channel
(60 seconds); counters and cycle budgets use 64-bit values. The camera timebase
remains independent of output readiness.

Each saved plan contains:

- A continuous run, with all committed output pixels checked against the independent integer reference.
- Zero and randomized two-state initialization for every seed; post-reset frame hashes, epochs and configuration versions must agree.
- Repeated active-frame resets rotating across channels, followed by recovery.
- Simultaneous output stalls, channel reset and missing-input faults; unaffected channels must remain valid.
- A fixed-phase, increasing-duration FIFO sweep that must observe both absorption and overflow, followed by recovery.
- Separate native four-state and clock-boundary checks when enabled.

Seeds change image data and the deterministic reset schedule. A sweep that misses
the overflow transition is incomplete. Disabling pre-board checks or using one seed
does not establish the complete pre-board campaign. The explicit `extended_smoke`
mode uses 128×96 accelerated functional timing and cannot satisfy the full-HD gate.

The downstream software consumer accepts actual RGB handshakes, keeps frame/epoch/
configuration metadata and publishes only after commit. An abort discards the
whole partial frame, including late held beats. Its manifest and published hashes
are checked against the independent frame results. This verifies the consumer
contract in simulation; a future hardware receiver must implement the same policy.

Long runs retain bounded selected previews, per-frame input/output hashes, terminal
events, counters and selected trace windows. They do not emit full-resolution pixel
CSV or unbounded VCD. Uploaded RAW repetition requires `raw_repeat=true`; the
runner never silently invents additional recorded frames.

## Initialization, reset and clock boundaries

Verilator randomized initialization remains a **two-state** check. The separate
`adas_preboard_checks` engine runs installed Icarus Verilog and Verilator lint:

- Actual generated full-HD core control state before and after reset.
- Actual BLC valid data with four-state semantics, plus an original-source negative control.
- Actual four-channel core valid pixels at 16×16, including four commit events.
- Independent-clock 32-bit Gray-pointer FIFO reference, reset release on each clock,
  mid-transfer lock loss, discarded pending words and recovery in a new epoch.
- Deliberately broken reset-release and publish-before-commit controls that must be detected.

The small four-state pixel probe is not full-HD X-propagation coverage. The reference
FIFO is not integrated into the camera core and is not physical CDC/RDC signoff.
Digital simulation cannot prove metastability MTBF or synchronizer placement/skew.
Generated-core lint reports and explicit unused-lane/padding waivers are retained.

The pinned upstream BLC leaves high bits of a 17-bit working wire undriven for
RAW12/PPC1. Actual four-state simulation exposed valid `X/Z` output, previously
hidden by zero-based two-state initialization. The ADAS generator now emits an
explicitly zero-extended derivative; original MIT sources and the reproducing
negative control are preserved. Its new design hash requires new pixel and OOC
evidence; previous timing results cannot be applied to it.

## Authorized board example: ULX3S 85F

The example uses the official [ULX3S repository](https://github.com/emard/ulx3s/tree/6a92cec6b177191c5b0f80e260013a1f8ec147dd)
and its pinned [reference constraints](https://github.com/emard/ulx3s/blob/6a92cec6b177191c5b0f80e260013a1f8ec147dd/doc/constraints/ulx3s_v20.lpf).
The packaged profile records source hashes, LFE5U-85F-6BG381C, a 25 MHz oscillator,
control pin subset and generated `ecppll` 25→100 MHz parameters. The PLL file is
unintegrated; the LPF is a documented control-only subset, not a complete ADAS
board constraint file.

The documented FT231XS UART limit is 3 Mbit/s. Four RAW12 1080p30 channels require
2.986 Gbit/s of active pixels alone. Therefore this UART is for control/status and
slow offline transfer; it cannot carry the full-rate input. A camera receiver and
high-speed output transport are still unselected. No actual board is connected.

The boundary manifest specifies post-CSI RAW/SOF/EOL/frame IDs, single-clock
AXI4-Lite control and transactional RGB commit/abort. Actual CSI lane/VC/ECC/CRC,
receiver CDC, host bridge, output transport, power sequence, board top, full pins,
PLL integration and I/O timing remain explicit `not_configured` gates. The supplied
board-top skeleton is a contract placeholder, not a synthesizable completed board.
Hardware execution continues to fail closed rather than substituting software.

## API and reproducible execution

`POST /hil-sim/engineering-lab/campaign-plan` freezes and hashes schedules without
dispatch. Save, preview, execute, cancel, artifacts and export reuse the existing
Engineering Lab/Experiment routes. The project-scoped `campaign-progress` endpoint
exposes bounded provisional counters; only the sealed terminal manifest carries
the final verdict. URL parameters restore revision, execution, case and frame.

```bash
.venv/bin/python examples/adas_preboard_workflow.py \
  --url http://127.0.0.1:8180 --ui http://127.0.0.1:5280
# Resume the same immutable jobs and retained evidence after interruption:
.venv/bin/python examples/adas_preboard_workflow.py --resume
```

The workflow starts no service, downloads and rehashes every artifact, verifies
sealed exports and compares the full-HD campaign design hash with the independent
100 MHz Yosys/nextpnr OOC build. A successful native campaign is not physical HIL,
electrical validation, Qualcomm internal RTL equivalence or commercial release.
