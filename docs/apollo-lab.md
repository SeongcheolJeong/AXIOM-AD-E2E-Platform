# Apollo SM8750 Camera Lab

Implemented 2026-10-14 as a separate target in **HIL → FPGA Lab → Apollo SM8750**.
This is an executable architecture/timing model and full-resolution CPU image reference,
not Qualcomm firmware, bit-exact ISP, SA8650 internals, RTL, PHY or physical validation.

## Connected workflow

Choose a preset, edit channels/RAW/profile/capture requests, save an immutable test revision,
then execute through the normal experiment worker. Select saved A/B runs and a frame to inspect
RAW/TFE/OFE/IPE images, per-stage wait/service times, queue peaks, DDR bytes and Preview/Snapshot
outcomes. Event stepping/playback and URL resume preserve execution, comparison, job, stage and
cursor. Export JSON/CSV/image manifests and completed full-resolution Snapshot PNGs.

The versioned API is `/api/v1/hil-sim/apollo-lab`; Python SDK methods are in
`sdk/python/src/axiom_sdk/apollo_lab.py`. A repeatable five-case SDK workflow is
`examples/apollo_lab_workflow.py` (defaults: API 8110, UI 5210). It refuses to overwrite its
receipt file. Install the optional `integrations/apollo_lab` package in the worker environment.
Existing FPGA and SA8650 targets remain distinct.

## Model and inputs

- Default: four 1920×1080 RAW12 channels, 30 FPS, 30 frames/channel, seed 42.
- Three parallel TFE resources, one shared OFE and one shared IPE; shared DDR uses 64 KiB
  FIFO bursts. Service times and bandwidths are editable assumptions, not vendor benchmarks.
- TFE: black subtraction, manual white balance, radial shading and statistics.
  OFE: Bayer demosaic, exposure/gamma and full-range BT.709 YUV444 reference conversion.
  IPE: spatial noise reduction and sharpening. No automatic 3A, HDR, EIS, EVA or GME.
- RAW input: deterministic synthetic scenes or uploaded uint16 little-endian/MIPI packed
  RAW10/12 with explicit dimensions, Bayer order, stride, black/white levels and FPS.
  Maximum 128 MiB per file, 30 source frames and 120 requested frames across channels.
  Repetition is explicit; uploaded originals and metadata retain separate hashes.
- ZSL keeps the latest four completed TFE buffers per channel by default. Capture requests
  pin the latest available frame at the request time. Empty rings and exhausted capacity
  fail visibly. Snapshot and Preview share non-preemptive FIFO OFE/IPE resources.
- Memory capacity counts retained RAW and in-flight YUV buffers. Queue/capacity overflow
  drops newest work; deadline misses and drops are separate outcomes. All buffers release
  at run completion. DDR timeline spans include interleaved bursts, not continuous ownership.
- CPU reference pixels are calculated independently of virtual scheduling. Hashes for a
  dropped frame are reference calculations, not proof it reached a stage; the viewer masks
  unreached stages. Host CPU wall time is separate from simulated latency.

The local Apollo Quick Start and SM8750 overview supplied in the Qualcomm ISP folder informed
resource naming/topology. Source identifiers and SHA-256 values are retained in the profile;
proprietary PDF content and diagrams are not bundled. The modeled timing, arbitration, formats
and simplified algorithms need measured calibration before hardware predictions are claimed.

## Preserved 1080p executions

[Machine-readable receipts and full traces](validation/apollo-lab-20261014.json) preserve exact
profiles, sender revisions, test hashes, seed, adapter version, image hashes and artifact identity.
Every case was newly executed through the worker. Same-frame RAW and reference IPE hashes match
across timing-only variants.

| Case | Changed parameter | Preview on time / late / dropped | Bottleneck |
|---|---|---|---|
| Normal | Defaults | 120 / 0 / 0 | No deadline failure |
| CSI | Lane rate 1000 → 200 Mbps | 0 / 108 / 12 | Shared CSI / ingress buffering |
| OFE | Service 4 → 20 ms | 2 / 55 / 63 | OFE queue reaches 8 waiting jobs |
| DDR | Bandwidth 8000 → 500 MB/s | 0 / 21 / 99 | DDR contention and memory capacity |
| ZSL burst | CH0 at 400/410/420 ms | 120 / 0 / 0 | Shared work raises maximum Preview latency 71.946 → 74.467 ms |

ZSL completed all three Snapshots within the 100 ms deadline (maximum 23.491 ms).
DDR wait metrics sum all requests and can exceed total elapsed simulation time.

Validation: 73 focused backend/adapter/experiment tests; two browser workflows using the real
worker cover four-channel image A/B, ZSL, resume/export and user RAW upload/repeat validation.
Production frontend build passes; pre-existing bundle size warnings remain. Native full-resolution
runs took approximately 24–27 seconds each on this host; these are not real-time guarantees.

Remaining work: measured target calibration, Qualcomm algorithm/IP or firmware adapters,
advanced ISP stages, long streaming runs, hardware control and physical correlation.
