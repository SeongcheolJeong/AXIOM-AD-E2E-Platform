# Open ISP RTL Lab

`HIL → FPGA Lab → Engineering Lab → OpenISP RTL` connects immutable RAW inputs and
saved configurations to native Verilator execution, an independent integer pixel
reference, image/stream evidence and a separate ECP5 OOC build.

## Implemented contract

The bundled pipeline is RAW12 → BLC → Bayer gain → completed-frame RAM → MHC
demosaic → RGB tap. One input pixel per clock, one 100 MHz simulated clock, one
channel, 1–2 frames; default 128×96, even dimensions from 8×8 through 320×240.
Black level is a common offset. Gains are `[R, Gr, Gb, B]` unsigned Q7, unity128;
each arithmetic stage clips to0–4095. Bayer order is explicit. The 5×5 MHC stage
crops2pixels on every border, so output is `(width−4)×(height−4)` with origin `(2,2)`.
Outputs retain RGB12 integers; PNG images use fixed right-shift4 for display.

The frame buffer is synthesized inferred on-chip RAM. It waits for a whole
preprocessed frame before replaying it and holds the next input frame until RGB
output drains. It is **not an external DDR controller or a DDR timing model**.
Reset aborts the current frame and restarts that source frame at SOF; aborts and
retries remain visible in the native trace. A cycle-budget timeout is not a pass.

The source is AMD mini-isp, pinned to
`67e752d6bf668179ec45446089a709bbc1ef9736`; six unmodified SV blocks, MIT license,
REUSE metadata and file hashes are retained in the optional adapter. The wrapper,
frame RAM, stimulus, integer oracle and result integration are AXIOM code.
The upstream full-pipeline test has an unfinished reference comparison; this
adapter therefore uses its own integer convolution oracle and native checks.

The source architecture documents are Qualcomm80-PN984-66 RevAC(p6–9) and
80-64995-82 RevAA(p10,63), retained in the user's Qualcomm ISP study folder.
These describe **SM8750 Apollo**, not SA8650 internals. BLC/WB roughly occupy a
TFE role and demosaic reaches an OFE-role RGB tap. This does not implement full
TFE/OFE/IPE, Qualcomm algorithms, CHI/HWL binaries, ISP registers, HDR, automatic
3A, temporal NR, EIS, CSI/D-PHY or physical ECU behavior. Documents are not copied
into the product.

## UI / API / SDK workflow

1. Select a synthetic RAW12 pattern or upload an immutable RAW12 asset through
   the existing Apollo asset API. Packed/unpacked decoding, metadata and content
   hashes are reused. Dimension/CFA/frame-count mismatches fail before execution.
2. Edit black level, four gains, frame count, sink stalls or reset; save a revision.
3. Run **RTL 픽셀 실행**. Inspect reference/RTL/difference images, mismatch coordinate,
   stream trace, full top-port VCD, and original integer outputs. A/B is only
   presented as the same-input experiment when input hashes and dimensions match.
4. Run **ECP5 OOC 빌드** separately. A synthesis result is never a pixel pass.
5. Reopen the saved revision/execution URL or export the sealed evidence manifest
   and each native source, output, report and hash.

The existing Engineering Lab save/preview routes accept `engine: isp_rtl` and
`pipeline_id: mini-isp-blc-wb-mhc/1`. Pixel execution stays `engineering_lab`.
`POST /api/v1/hil-sim/engineering-lab/tests/{id}/build-preview` accepts
`project_id`, `revision`, `sha256`, `build_profile_id`; its ordinary Experiment
preview creates a separate `fpga_build` using `open-isp-ecp5-build`.
Both jobs retain the same generated `design_hash` when the synthesized design
is identical. RAW/stimulus/seed and tool/adapter identities remain separate.
Existing polling, cancellation, history, artifact download and export APIs apply.
The SDK and `examples/open_isp_rtl_workflow.py` exercise this same public path.

## FPGA profiles and tools

The executable profile `ecp5-85k-cabga381-6-ooc/1` uses LFE5U-85F, CABGA381,
speed6, target100MHz, nextpnr out-of-context mode and an explicit seed. These
package/speed settings are an evaluation assumption, not a discovered board.
The build retains Yosys netlist/statistics, nextpnr routed JSON and timing report,
logs, commands and identities. Synthesis, routing and timing have separate
verdicts. nextpnr is allowed to finish routing/reporting a timing miss, but the
adapter independently requires achieved frequency≥100MHz and the100MHz
constraint; the final verdict remains failed on a miss or absent timing proof.
No board pins, IO timing or bitstream are generated.

Default external tool directory:
`~/.cache/axiom/tools/oss-cad-suite-20261016/oss-cad-suite`.
`AXIOM_OSS_CAD_SUITE` can select another installed distribution; actual binary,
plugin, source and tool-version hashes are recorded. Tools remain outside the
repository. The reference macOS ARM64 archive is:

- <https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-10-16>
- `oss-cad-suite-darwin-arm64-20261016.tgz`
- SHA256 `ac8765e94c5122c3e3b48b18f55cea306d08aa37ffc12cd9a938f3c485322326`

Install the optional Python adapter with `.venv/bin/pip install -e integrations/isp_rtl`.
The existing Engineering, RTL and Apollo optional adapters must also be installed.
No existing service or global Python/PATH configuration needs replacement.

GW2A/Gowin and CycloneV/Quartus profiles export source-project Tcl templates with
an explicit missing-exact-device error. They are `not_executed`, not supported
native builds. Complete device/package, tool/license and worker checks precede
their actual implementation. Gowin has an official macOS ARM64 distribution;
macOS26.3 execution is not established here. Quartus uses a Windows/Linux worker.

## Validation boundary

Native pixel tests cover all four CFA orders, asymmetric gains, saturation,
backpressure, frame boundaries, reset during capture/output, timeout, RAW upload
and deterministic A/B. The integer arrays, not PNG appearance, are the oracle.
FPGA OOC implementation tests are independent of pixel and physical validation.
See `docs/validation/open-isp-rtl-20261017.json` for retained public API executions
and artifact/export readback, and the companion checks receipt for test results.

Common commercial/platform release gates remain open. Board-level bring-up,
external memory/PHY,1080p multichannel throughput and Qualcomm runtime correlation
remain separate work.
