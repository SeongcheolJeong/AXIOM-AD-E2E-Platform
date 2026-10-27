# SIL → HIL Bridge

Status: **SIL–HIL interface and virtual bus connection acceptance complete** after explicit deployment approval. Actual Linux vCAN/UDP, independent reference ECU processes and CARLA were executed. Physical ECU validation remains incomplete.

The saved `hil-binding/1` references an immutable Network Lab, vECU fault A/B, or Software-DUT test. Source records are not rewritten. A binding freezes the source, profile, injection boundaries and timing budget. The optional `axiom-hil-gateway` adapter shares the Experiment queue, project authorization, idempotency, cancellation, sealed artifacts and export.

## Normal workflow

Virtual ECU Lab → **SIL → HIL 연결** → select a source revision → select vCAN or physical target → save binding → device readiness → preview → new normal/fault run → common-cursor inspection/export. URL preserves binding/revision, execution, comparison axis, SIL comparison and selected input step. A changed draft requires a new saved revision before execution.

Normal/fault A/B is within a target. SIL/connection comparison selects an original execution with the exact source revision/hash; each side retains its own normal/fault pair. An object-only source adds two ECU and CAN boundaries and is explicitly classified as such. Settings/package A/B and nonmatching policy profiles fail closed.

## Injection map and contextual settings

The HIL Bridge page includes a clickable injection map for transport, reference hooks and four model measurement boundaries. Select object UDP,
Restbus CAN, ADAS→Brake relay, or Brake output feedback to see the affected signal,
fault interval/value, normal behavior, recovery policy and required observations.
CAN identifiers come from the selected profile; the current example uses command
0x100, output 0x101 and Restbus 0x200/0x201. The map explicitly names the Linux gateway
and separate CAN A/B segments. CARLA images are observations, not injected RAW/CSI input.

The inspector separates three different kinds of configuration:

- **Injected fault**: source SIL revision owns UDP/Restbus/command faults. The editor link
  opens that exact source revision; after saving, select its new revision in the binding.
  Output-feedback loss belongs to the binding and its shortcut focuses the existing fields.
- **ECU monitoring**: the current reference uses object age >250ms, heartbeat/command
  silence >=100ms and three fresh items to recover. These are read-only reference rules.
  Physical ECU monitoring remains unknown until a device-specific contract is registered.
- **Test validity**: the binding's lateness budget (default 5ms) constrains the test system,
  not the intentionally injected delay and not an ECU timeout.

The support view is embedded in the same page and uses `/profiles`, avoiding a separate
static capability list. Profile version 4 includes the reference reset/control-event hooks
and optional native model output replay to the reference ADAS monitor on vCAN. Physical
reset/OS hooks and physical RAW/CSI, FPGA, power and analog connections still need
device-specific adapters. Software, reference processes and physical device roles have separate labels.
A physical connection label requires the selected profile's successful readiness result,
`physical_hardware_connected` and both device identities. A timestamp records when it was
checked; it is not continuous connection monitoring.

URL `point` restores the selected location and `info=scope` restores the support view.
Observed stage buttons target the existing video cursor only for a completed execution
matching the saved binding hash and an unchanged draft. Missing diagnostics/timestamps stay
unobserved/unknown. Draft summaries describe configured expectations, not executed results.

The earlier injection-map UI change passed 23 presentation/interpretation checks, 26 API/adapter/SDK checks and
production build. Browser access was declined, so visual/click/refresh acceptance for this
change remains **unverified**. No new native execution or physical acceptance was performed.
See [UI change receipt](validation/hil-injection-map-20261017/receipt.json); the earlier native
acceptance below belongs to the preserved implementation/results preceding this UI change.

## Execution and observation boundaries

- Independent reference ADAS and Brake subprocesses use UDP and two real Linux `vcan` interfaces. The gateway does not calculate heartbeat protection or Brake watchdog output. These run in the respective **reference processes**, not production ECU firmware.
- The same `CanBus` transport supports physical SocketCAN interfaces. Two isolated segments are required for selective ADAS→Brake command dropping. Own local echo is not physical ECU reception.
- Physical device configuration is operator-owned `bridge-devices.json`. The shipped hardware entry is null. Both identities, probe/initialization driver and physical interfaces are required; virtual interfaces cannot establish hardware readiness. No public API accepts commands or driver paths.
- AXO1 object bytes and the existing `<HHhBB` Classic CAN control payload are reused. Route/physics initialization and ego pose are explicit separate inputs. Operator-pinned codec modules must supply bounded encode/decode functions and distinct CAN ID mappings; vendor codecs and firmware are not supplied.
- The supplied reference driver uses Python reference ADAS policy and an independent Brake process. It is a distinct target from the existing SIL Kit/autoas/Trampoline execution. It does not imply BSW/OS equivalence.
- UDP delay/drop/repeat, Restbus suppression, inter-segment CAN command drop and Brake→plant feedback loss are executable boundaries. Reference reset/OS injection is now executable on vCAN. Physical reset/OS and physical RAW/CSI, FPGA, analog and power boundaries remain blocked without their adapters.
- A missing/stale Brake feedback frame stops the run. No replacement control is synthesized. Failed-run stderr includes pending gateway events and validity errors. Bounded failure artifacts are retained and the UI verifies their hashes before showing the Korean stop reason. Cleanup after cancellation and feedback loss was observed; unexpected failures also require the common resource reconciliation workflow before reuse.

## Clock and evidence contract

Input/plant period is 50ms, Restbus 10ms; reference ADAS uses its own paced 50ms task and Brake its own 10ms task. Default maximum lateness is 5ms, configurable in the binding and frozen in the receipt. Missing an entire plant period aborts instead of burst replay. Lesser violations are retained and produce `timing_invalid`, never a timing pass.

The final reference schedule uses ADAS sampling at +5ms, Brake output at +8ms and gateway readback at +12ms in each 50ms period. Fixed replay uploads frozen input once and is paced on Linux. At UDP-delay recovery, queued old packets are discarded; this 3–7s case delivered 72 delayed packets and discarded eight pending packets, without burst catch-up.

Gateway observations use Linux monotonic time; kernel CAN receive realtime timestamps remain separate. Reference processes in the same kernel share the monotonic clock. ECU timestamps without a verified mapping have no cross-clock latency. CARLA input/frame time and actual wall-clock response readback are distinct. Synchronous CARLA is not hard-real-time proof.

`hil-bridge-causality/1` contains source pointers and observed injection/detection/ADAS CAN/Brake output/CARLA response stages. Missing ECU diagnostics stay unobserved. A Brake watchdog response to CAN command loss does not imply a new ADAS braking command. Bus transmission acceptance is not remote ECU reception.

## Additional injection workflows · 2026-10-17

The binding editor accepts additional reference reset and OS-event windows in seconds
(50ms grid); its immutable API stores milliseconds. These schedules can overlap Restbus
loss. For source Network Lab resets, `reset_restart_ms` is the actual reset duration;
the source observation-window end is preserved as `source_end_ms`, not used to extend
the reset. Profile hashes changed: save a new binding revision to select the new profile.

- Reference reset clears ADAS controller, object and heartbeat state, omits commands
  during the reset, increments the command epoch and waits for new inputs. Brake confirms
  three fresh commands before recovery. The reference process remains alive to record
  its hook; this is not a CPU reboot, power cycle or firmware reset validation.
- Reference OS-event loss omits the scheduled ADAS control tick and its CAN command.
  UDP/CAN reception continues. This is a Python reference task hook, not a fault in the
  Trampoline kernel, AUTOSAR OS or an actual ECU scheduler.
- Missing diagnostic data is explicitly unobserved. Host route projection is only CARLA
  bookkeeping and never fills ECU diagnostics or generates substitute Brake control.
- Independent reference hooks can execute just before the gateway records the same
  schedule. Causality matches the scheduled interval and preserves both actual times.

The **RAW · FPGA · 전원 · 아날로그 주입시험** panel provides editable settings, immutable
normal A/fault B pairs and native execution through Engineering Lab. `modelPair` restores
the saved configuration; the resulting Engineering URL restores both executions.

| Model boundary | Implemented injection | Scope |
| --- | --- | --- |
| RAW after CSI receiver | Selected channel valid gap; channel, start µs, duration µs | Verilator, 4×1920×1080 RAW12, 30 FPS, 30 frames/channel, 100MHz; no CSI packets/PHY |
| FPGA output | Selected channel ready stall; channel, start µs, duration µs | Same full-resolution RTL, FIFO/abort/recovery |
| Power reference | 12V source sag; fault voltage and start/end ms | ngspice, 2Ω wire, 100µF bulk, 120Ω load, 10ms transient |
| Analog reference | 2.5V sensor offset; offset and start/end ms | ngspice, 1kΩ/1µF input filter, 10ms transient |

The four original model executions remain independent of CARLA and the real ECU. Their plans explicitly
record `carla_connected=false` and `physical_hardware_connected=false`. The voltage source
uses assumed 1µs ramps. No PMIC, ECU reset threshold, ADC, camera PHY or physical I/O
behavior is inferred. A separate saved model coupling can now reuse their sealed outputs
in the CARLA loop, as described below. Original full-resolution RTL pixel checking and commit/abort
visibility rules remain unchanged.

Native runs and independent capture checks are recorded in
[injection expansion acceptance](validation/hil-boundary-injections-20261017/acceptance.md).
The current browser permission denial prevents click/refresh acceptance; API/SDK execution
and build checks do not substitute for that browser workflow.

## Native model output → reference ECU → CARLA · 2026-10-18

The model connection panel selects a saved pair and its completed native A/B runs, a
fault-free CARLA Network Lab source, replay start, minimum protection duration and
normal-sample count. Save/preview/execute uses the same binding and Experiment APIs.
The binding accepts either the original `model_coupling` or two to four
`model_couplings`, one per boundary. Each member preserves its native A/B source and
recovery policy. The reference ADAS keeps braking while **any** member remains protective.
Transport/internal faults and a physical target cannot be mixed into this model-replay
workflow and are rejected before dispatch. A/B side identity, project, configuration, seed, manifest and
source artifact hashes are verified. Old sources and results are unchanged.

The gateway independently paces measurement UDP at 50ms. The ADAS process receives
measurements, detects the failure and sends its own CAN command. CARLA receives only
the independent Brake process's reported CAN output. The host does not substitute a
braking command. Missing measurements and schedule violations invalidate the test.

| Measurement | Reference ADAS rule |
| --- | --- |
| RAW / FPGA | Selected-channel abort in the interval, or last frame still aborted |
| Power | Native interval minimum v(out) below 8V |
| Analog | Native interval outside 2–3V |

These are explicit example monitor rules, not Qualcomm or production ECU specifications.
Protection stays asserted until the last bad measurement is at least the configured
500ms old and at least three subsequent normal samples have arrived (defaults shown).
Native source time is **1:1**: the 10ms SPICE waveform is not stretched to seconds.
A 50ms envelope retains brief voltage extrema and frame aborts. Monitoring is inactive
before replay; after the finite source ends the last observed state/value is explicitly
held. Therefore the plotted fault-to-detection interval includes measurement aggregation.
The fault marker is mapped from the native source schedule; UDP transmit and ECU receive
are separately retained events.

The same results panel shows A/B source measurements, ECU monitor state, protection hold,
CAN/Brake causality, vehicle speed and recorded CARLA cameras. Detection/recovery buttons
seek the common cursor. Binding/model pair/run/point/step restore through the URL; exports
retain model provenance. The browser workflow remains unverified because this chat's
browser access is blocked. API/SDK execution and original camera artifact inspection
were verified; neither substitutes for normal-browser acceptance.

This is sealed output replay into a closed-loop vehicle simulation. Pixel perception,
physical ADC/PMIC/CSI behavior and feedback from CARLA into live RTL/SPICE are not connected.
Four final native CARLA A/B pairs passed the original 5ms budget; early invalid runs remain
sealed. See [model-loop acceptance](validation/hil-model-loop-20261017/acceptance.md).

## Overlapping models and independent recovery · 2026-10-18

Use **동시 감시 모델 추가** in the same model connection panel. Two to four distinct
boundaries are supported by the API/SDK, with individual source runs, start offsets,
minimum protection durations and normal-sample counts. `model_link` becomes an immutable
`hil-model-coupling-set/1` containing each member. Single-model bindings/results remain
readable. Profile version4 freezes the `any_protect` combination; gateway adapter0.4.0
retains source and runtime identities.

The independent ADAS process evaluates every member each tick. Every member has its own
latch and recovery counter. A recovered member cannot clear another member's protection.
Missing measurements retain protection and invalidate the test. Individual recovery
events include `aggregate_protect` and `remaining_boundaries`. `model_group_recovered`
is emitted only when all model protection conditions clear. Shared CAN output is the
combined decision; it is not attributed exclusively to either individual fault.

The A/B panel shows each member, remaining causes and separate individual/all-clear
seek buttons. Source identity and settings restore from the saved binding; the normal
save path preserves multiple members. A new300ms FPGA-stall pair was actually run at
4×1080p30/100MHz and combined with the sealed power sag. The final CARLA pair passed5ms,
including a250ms interval after power protection cleared while FPGA protection retained
100% Brake output. See [compound acceptance](validation/hil-model-overlap-20261018/acceptance.md).

The initial compound run failed before fault-side DUT initialization because the CARLA
transport still used the single-DUT256KiB bound. The registered gateway already supports
8MiB. Only the gateway transport now uses that existing bounded contract; ordinary DUT
limits are unchanged. The first failure remains sealed, with separate cleanup observations
and resource reconciliation. A fresh execution passed after the fix.

## Recovery-order comparison · 2026-10-18

Voltage model authoring now exposes `stop_ms` (default10ms, up to1000ms), preserving the
native time axis. The fault must end before the model stop, and the10µs integration step
and bounded sample contract remain explicit. Old saved pairs without this optional field
stay readable; their original source configs/hashes are unchanged. A new400ms ngspice
run models a6V sag from3–303ms, without changing the reference monitor hold policy.

The result selector adds **복합 고장 · 이전 실행 A / 현재 실행 B**. Both columns are the
faulted sides of separate executions. The UI checks the same source revision, seed,
boundaries, replay starts, monitor policies, adapter sources, gateway installation and
fidelity before loading the comparison. Each camera scene/archive/member is loaded and
verified against its own execution, never relabeled as the other run. `axis=model_runs`
and `compareModel` preserve the comparison in the URL; the existing normal/fault and
SIL/connection axes remain separately selectable.

The native400ms power pair and a fresh CARLA A/B passed5ms. FPGA protection cleared first;
all six partial-recovery rows retained100% Brake output and a stopped vehicle until power
protection cleared. The previous power-first case and this FPGA-first case have identical
360-row faulted control sequences because their final protection-clear input is the same.
The selected cursor shows the changed remaining cause. See [acceptance and video](validation/hil-model-recovery-order-20261018/acceptance.md).

## Recurrent FPGA injection and protection handoff

FPGA model settings accept up to four ordered, non-overlapping `repeats` in native
microseconds on the selected channel. The last stall must leave 50ms of native recovery.
An empty schedule preserves the previous single-stall serialization. Native trace windows,
sealed measurement replay, single/compound injection maps and causality retain each occurrence.
The cross-run comparison offers separate first-detection and re-detection seek buttons.

At replay time3.8s a100µs stall was absorbed at frame start, so it is retained as a
non-triggering attempt. A new200µs native stall caused a second FIFO overflow. The fresh
CARLA A/B passed5ms; power protection cleared while recurrent FPGA protection kept the
brake applied. All162 observed ADAS/Brake CAN events during protection held100% braking;
final all-clear was4.405669s and CARLA motion resumed at model4.60s (previously4.05s).
[Acceptance, original attempt and video](validation/hil-model-recurrence-20261018/acceptance.md).
Browser workflow remains unverified under the existing denial; this is sealed native
measurement replay through reference ECUs/vCAN/CARLA, not physical power/ECU validation.

## Recurrent power sag and measurement-phase boundary

Power profiles accept ordered `repeats` (`start_ms`, `end_ms`) at the same sag voltage,
up to4s native stop time and explicit `step_us` (default10, maximum100). Requested steps
and ngspice adaptive points are each bounded at250,000 (expanded from2s/100,000 for the
late5.85s recurrence). The native64MiB file bound remains; measurement artifacts are
bounded at32MiB. Default single-sag
serialization and previous native plan hashes remain unchanged. The UI can author,
save and execute the same native/binding contract as the SDK. No model time stretching.

A new1.75s/20us native pair added a6V/300ms sag at vehicle4.35s to the sealed recurrent
FPGA case. The fresh CARLA A/B passed5ms. FPGA recovery and power re-detection shared
one ECU processing tick; all264 observed ADAS/Brake wire events during protection
retained100% brake. Global clear was5.255772s, motion at CARLA5.45s (previous4.60s).

This observed pass is not a general phase guarantee: a4350 source excursion enters
the4400 measurement envelope, and a legal earlier FPGA latch clear can create a50ms
command gap. Both phase outcomes are covered by authored monitor unit fixtures.
Aggregate release qualification against observation age and complete phase acceptance
remain open; holds were not silently extended to produce this pass. Browser workflow
remains unverified under the existing denial. [Evidence and limitation](validation/hil-power-recurrence-20261018/acceptance.md).

## Qualified aggregate protection release

New compound bindings pin `fresh-window-v2`: after all per-cause latches clear, keep
braking through two sequential complete measurement windows. One window can straddle
the candidate's processing time; two include a full50ms interval after it. The sequence
must advance continuously and input age must remain0..10ms. New faults invalidate the
candidate; missing, duplicate, future, skipped or stale input cannot authorize release.
Per-cause500ms hold, three normal samples and voltage thresholds remain unchanged.
`release_guard` records pending/candidate/required and confirmed cursors. Individual
recovery and aggregate release are separate; no fictitious active fault is added.

The sealed original code reproduced255 gaps in500 authored timing fixtures; the new
code had none. Four independent native-model/vCAN/CARLA A/B executions at4.349/4.350/
4.351/4.399s passed the original5ms budget, with no transient release among1,092 protected
ADAS/Brake observations. Every2,880 vehicle command and4,800 native measurement window
was checked. The same4.35s case released at5.305826s and moved at CARLA5.50s; its legacy
counterpart released at5.255772s and moved at5.45s. The legacy native run had no observed
gap and is never relabeled as the authored counterexample.

Cross-run comparison uses both hash-verified provenance artifacts to allow only this
monitor-file/installation upgrade, displays the policy change, and retains each camera's
own execution identity. Unrelated engine/fidelity/policy changes remain rejected.
Legacy results remain readable/exportable; a new execution needs a new qualified binding.
The qualification bounds the tested observation horizon; it does not predict faults
occurring after a confirmed window or establish physical safety. Browser workflow remains
unverified under the existing denial. [Acceptance and waveforms](validation/hil-release-qualification-20261018/acceptance.md).

## Pending release and late FPGA recurrence · 2026-10-18

FPGA profiles now expose actual input `frames` per channel (30–300, default30).
Late `at_us`/`repeats` are validated against that duration with at least50ms native
recovery. Default30-frame serialization and hashes remain unchanged. Increasing
frames expands the full-resolution native source and cycle budget; no time scaling.

An actual75-frame/channel pair at4×1080p30/100MHz added a200µs CH0 stall at native
2.25s (vehicle5.25s). Normal300commit and faulted297commit/3abort passed whole-pixel
checks with zero mismatches/protocol errors; CH1/2/3 each committed75frames.
The candidate pending at input5.25s was canceled by the5.30s FPGA measurement.
All57 ADAS command and285 Brake report events during protection retained100% braking.
Aggregate release was5.905587s, CARLA first motion6.10s, versus5.305826s/5.50s previously.

The UI offers a `해제 후보 취소 관측` seek from consecutive reference-ECU state
reports. Its timestamp is gateway readback, never invented internal cancellation time.
Native diagnosis, measurement cursor and camera/model time remain distinct. Both
normal sides have identical360controls; comparison is previous-faulted/new-faulted.
The native FPGA source ends at vehicle5.5s, power at4.75s; the existing replay contract
holds their last observed state afterward. Final release is not evidence of additional
native measurements in that tail. Browser workflow remains unverified under the
existing denial; physical ECU, hard-real-time and safety acceptance remain separate.
[CAN/ECU/CARLA A/B and receipt](validation/hil-pending-fpga-20261018/acceptance.md).

## Power recurrence in the next pending release · 2026-10-18

The sealed FPGA5.25s pair was reused unchanged. A fresh native power A/B extended
the original1.75s source to4s, keeping its maximum20µs step and6V/300ms sags while
adding native2.85–3.15s (vehicle5.85–6.15s). Normal200,008 and faulted200,080 adaptive
points were retained without resampling. Requested/actual samples remain capped at
250,000, native output64MiB and coupling artifact32MiB. No gateway/ECU code or
500ms/three-sample/fresh-window-v2 policy change was needed.

At input5.85s the reference ECU reported candidate117→119. Input5.90s contained
native minimum5.901639V<8V and canceled the candidate. All75 ADAS CAN and375 Brake
report events during protection retained100% braking. Final all-clear was6.805512s;
CARLA first motion7.00s, compared with5.905587s/6.10s previously. Controls before
the extra fault and the entire normal side match the previous execution.

The first checker assumed candidate116→118. Actual processing phase left0.149ms of
the unchanged500ms hold at input5.80s, deferring candidate creation one tick. Its
failed log is retained; verification now checks the published candidate cursor+2,
the minimum hold and continued braking, backed by a varying-phase unit case. This
was an assertion correction, with no changed DUT logic/budget or replacement run.

The cancellation timestamp remains gateway state readback, not an invented exact
internal event. Final power recovery is inside its native replay; FPGA is already
in the explicit last-observation tail. Browser workflow remains unverified under
the existing denial; this does not establish physical ECU/power or safety acceptance.
[CAN/ECU/CARLA A/B and receipt](validation/hil-pending-power-20261018/acceptance.md).

## FPGA recurrence during the next release wait · 2026-10-18

The prior power5.85s source was reused unchanged. A fresh native FPGA pair extended
the2.5s source to4.5s/135frames per channel, keeping4×1080p30/100MHz and adding a
CH0 output stall of200µs at native3.75s (vehicle6.75s). Normal540commit and faulted
536commit/4abort passed whole-pixel checks with zero mismatches/protocol errors;
the other three channels each committed135frames. Both common300-frame prefixes
match previous RAW/RGB/reference hashes, terminals, states and epochs.

At input6.75s candidate134→136 was pending. The6.80s envelope contained native CH0
frame112 FIFO overflow despite a later committed frame in the same window, canceled
the candidate, and kept braking. All87 ADAS command and435 Brake report events during
protection retained100% braking. Final all-clear was7.405788s and first CARLA motion
7.60s, versus6.805512s/7.00s previously. All normal controls and faulted controls before
the extra fault match. Per-cause500ms/three-sample and fresh-window-v2 policy, reference
ECUs, gateway installation and seed remain unchanged.

Cancellation is a reference-ECU report transition observed by gateway readback, not an
invented exact internal cancellation timestamp. Final FPGA recovery is in native replay;
power is in its explicit last-observation tail after7s. No fresh synthesis/physical ECU,
live sensor or safety acceptance is claimed. Browser workflow remains unverified under
the existing denial. [CAN/ECU/CARLA A/B and receipt](validation/hil-recurrent-fpga-6750-20261018/acceptance.md).

## API / SDK

All endpoints are under `/api/v1/hil-bridge` and project-scoped:

- `GET profiles`, `GET readiness?profile_id=…`
- `POST bindings`, `GET bindings`, `GET bindings/{id}?revision=…`
- `POST bindings/{id}/preview`, `POST bindings/{id}/executions`
- `GET executions`, `GET executions/{id}`, `POST executions/{id}/cancel`
- `GET executions/{id}/export`, `GET executions/{id}/causality`
- `GET model-injections/catalog`, `GET model-injections`, `POST model-injections`, `GET model-injections/{id}`

SDK: `hil_bridge_profiles`, `hil_bridge_readiness`, `save_hil_binding`, `hil_binding(s)`, `preview_hil_binding`, `execute_hil_binding`, `hil_bridge_execution(s)`, `hil_bridge_causality`, `cancel_hil_bridge_execution`, `export_hil_bridge_execution`.

Model SDK: `hil_model_injection_catalog`, `save_hil_model_injection`,
`hil_model_injection`, `hil_model_injections`, `execute_hil_model_injection`,
`save_hil_model_loop`, `save_hil_model_set`. Pair creation atomically saves both
Engineering tests with plan/source hashes. Execution uses the existing Engineering
preview and Experiment queue, cancel, artifacts and export APIs. A stable submission key
resumes partial A/B submission without duplicating a side.

Published artifacts are `vecu-trace.json`, `hil-bridge-provenance.json`, `hil-bridge-summary.json`, `hil-causality.json`, stderr and, for actual CARLA runs, original scene/camera/command-readback artifacts. The reader verifies sealed manifest and artifact identity. Hardware connected, physical validation, hard realtime and functional/timing verdict remain separate.

## Operator setup and verified acceptance

Install with `pip install -e integrations/hil_gateway`. The dedicated operator installation uses `AXIOM_HIL_GATEWAY_DIR`; original SIL paths and hashes remain unchanged. The approved Linux container has its own network namespace, NET_ADMIN only, and a read-only source mount. It is not a physical CAN bus. No existing CARLA or UI/API service was restarted.

Prepared scripts under `integrations/hil_gateway/scripts/` are `deploy_example.py`, `launch_local.py`, `reload_local.py`, `accept_binding.py`, `collect_acceptance.py` and `verify_native_evidence.py`. Deployment and service changes remain explicit operator operations. `accept_binding.py --execute` uses the public SDK and resumes an already submitted receipt on repeat invocation. Collection is read-only and verifies sealed artifacts and received Brake bytes independently.

[Native acceptance](validation/hil-bridge-20261017/acceptance.md) and [machine receipt](validation/hil-bridge-20261017/receipt.json) retain all results. The final installation passed two fixed-replay pairs and five CARLA fault A/B pairs at the original 5ms limit, 18 seconds and 360 samples per side. Four earlier timing-invalid results remain unchanged. Local backend/adapter/SDK/SIL regression checks: 127 passed; frontend build and normal browser save/run/video/timeline/restore/export passed.

The feedback-loss run failed with the expected missing/stale Brake output diagnostic and no replacement command. Its sealed outcome remains uncertain in the generic worker receipt. Separate observations confirmed no ECU processes, zero CAN traffic for 300ms, and released bench flock; the normal operator API then reconciled the shared resource. Subsequent source-compatibility execution passed. The UI distinguishes the original failure from later operational cleanup.

Physical ADAS+Brake acceptance requires actual device identities, mapped codec, initialization/route/pose inputs, verified output feedback, two physical CAN segments and device-specific timing. Physical validation, electrical behavior and hard realtime remain separate gates. Unconfigured hardware and unsupported reset/OS injections return 422 before dispatch.
