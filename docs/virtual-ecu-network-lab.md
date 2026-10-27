# Virtual ECU Network Lab

Existing Virtual ECU Lab now has a `네트워크 / BSW / OS` workspace at
`#hil-sim?project=…&mode=vecu&tab=network`. Saved network and test revisions use
`/api/v1/virtual-ecu-network` and the same Python SDK contract as the UI.

## Executable scope

Four distinct SIL Kit participants exchange actual Classic CAN and Ethernet
frames. Perception emits AXO1 object payloads in Ethernet/IPv4/UDP. ADAS receives
those bytes, runs the registered C++ controller after an actual Trampoline
ControlTask request, and transmits an eight-byte BrakeCommand through actual
public autoas `Com → PduR → CanIf → host CanDriver` code. Brake receives through
the reverse BSW route and applies the decoded CAN command to CARLA. Restbus emits
VehicleState and Heartbeat frames; heartbeat timeout independently overrides
ADAS output. VehicleState is monitored; controller pose and speed still come
from the CARLA host state. This is not a complete RTE/COM application mapping.

SIL Kit 5.0.8 uses its functional CAN/Ethernet service. The configured CAN rate
is not a validated arbitration/serialization/load model. No physical CAN,
Ethernet PHY, hard real-time deadline, MCU WCET or target binary is represented.

The ADAS role has one Trampoline kernel executing real OSEK
Counter/Alarm/Task/Event services with an external 1 ms clock. Its BSW request
advances the three separate ADAS/Brake/Restbus BSW processes each 10 ms; its
control request runs the ADAS application each 50 ms. This is not four complete
ECU operating-system images.
Generated `WITH_AUTOSAR=NO` is retained. AUTOSAR-only applications, timing
protection, scalability classes, MCAL, RTE generation, ARXML import, UDS,
SOME/IP, CAN FD, commercial BSW and complete AUTOSAR conformance are not included.
The OS, BSW and application are separate host processes, not one ECU image.

## Faults and causality

- `udp_delay`: 400ms wire-frame queue. Old queued frames are discarded at fault
  end and the discard boundary is recorded. Freshness uses original source time.
- `udp_drop`: no emitted UDP frame during the selected interval.
- `udp_repeat`: emits the previous serialized packet, preserving its sequence,
  timestamp and checksum; actual receiver and C++ diagnostics detect repeats.
- `can_command_drop`: actual BSW Tx completes, then the virtual driver-to-network
  injection drops delivery. Driver confirmation is not a remote acknowledgment.
- `restbus_drop`: VehicleState/Heartbeat stop. ADAS heartbeat timeout is 100 ms.
- `ecu_reset`: resets the ADAS kernel, BSW and C++ process; control requests are
  inhibited for 200 ms. Brake remains a separate ECU and uses its own watchdog.
- `os_event_drop`: real OS counter/alarms/BSW continue; ControlEvent is suppressed.
  The C++ application is not called, and the Brake command watchdog expires.

Intervals are half-open and ordered by start time; defaults are [3000,7000)ms.
An ADAS reset may start during a Restbus interruption and finish inside it or
cross communication recovery at its exact 200ms restart boundary. Other
overlapping combinations are rejected. The compound schedule preserves each
active fault and its start/end cursor events; the ADAS epoch and reset counter
remain visible alongside heartbeat protection and Brake watchdog state.
Brake watchdog is 100 ms with three new command frames required for recovery.
ADAS object freshness is 250 ms with three new packets required for recovery.
Timeout deadlines are processed before arrivals at the same tick. Same-time
received frames are ordered by transport timestamp, bus, ID and payload before
BSW/decoder processing, so repeated input produces the same logical evidence.
The v1 eight-bit command counter supports fewer than 127 consecutive missing
50ms command opportunities; longer or adjacent combined outages are rejected
at authoring. Long outage counter resynchronization remains follow-up work.
There is no live Python control fallback. Missing engines, failed processes,
changed files or unsupported profiles fail execution.

All four participants share 1 ms SIL Kit barriers. Frames transmitted at T are
consumed by the receiving ECU at T+1ms. The external input/control boundary is
50ms. Consequently initial safe braking and pipeline latency are visible even
in the normal baseline. Source input time, network delivery time, command
application and CARLA response time remain distinct. A 360-step input covers
0–17.95 s; CARLA response frames end at 18 s. The final emitted packet is not
claimed as received unless a subsequent network tick is executed.

## Evidence and validation

### Detection → CAN → applied control → vehicle observations

`GET /api/v1/virtual-ecu-network/executions/{id}/causality?project_id=…`
and Python SDK `vecu_network_causality(id, project_id=…)` return
`vecu-network-causality/1`. The same read-only report powers the Network Lab
timeline and the separate comparison of two executions' faulted B sides.
The completed execution's sealed manifest and original `vecu-trace.json` hash
are checked before deriving the report. Every stage retains a source JSON
pointer, observation time, containing input-step index and decoded evidence.
The report preserves execution/test refs, seed, adapter version, fidelity,
source hashes and its own canonical content hash. It does not modify or reseal
the original execution export and does not submit another simulation.

The first slice supports UDP delay and Restbus loss. It uses a recorded C++
controller activation with stale object age for the former, and the actual
ADAS `heartbeat_timeout` event for the latter. It then requires the associated
ADAS control activation, CAN 0x100 bytes, a matching Brake receive payload and
transport timestamp, and the corresponding applied-control/readback records.
Aggregate `degraded`, configured fault start, OS reset injection or an unrelated
positive brake frame cannot replace missing detector or delivery evidence.
Other fault kinds explicitly report unsupported causality, while retaining
their recorded injection event. Missing links remain unobserved.

Both retained single-fault cases start at 3.000 s and end at 7.000 s:

| Observation | UDP 400ms delay | Restbus loss |
|---|---:|---:|
| ECU detection | 3.250 s / +250 ms | 3.091 s / +91 ms |
| Detector evidence | object age 300 ms > limit 250 ms; source 2.950 s | last heartbeat 2.991 s; 100 ms silence |
| ADAS CAN transmit / Brake receive | 3.250 / 3.251 s | 3.100 / 3.101 s |
| Applied full brake at vehicle input | 3.300 s / +300 ms | 3.150 s / +150 ms |
| First matching CARLA control readback | 3.350 s / +350 ms | 3.200 s / +200 ms |
| Subsequent first speed drop ≥0.01 m/s in one step | 3.400 s | 3.200 s |

The initial full-brake readback in the delay case still has a small speed rise;
therefore it is not labelled as the onset of deceleration. The speed-drop
observation is not a physical brake-response measurement or proof that all
speed change was caused by this command. Network events have 1ms ticks, C++
object policy executes every 50ms, and CARLA response is input time +50ms.
Cameras are 10Hz nearest retained frames; capture time and its offset from
the selected vehicle-response time are shown separately from ECU state.

Comparison requires equal scenario/input ref, seed, network ref, runtime,
adapter version, fidelity and fault window before describing the difference
as this paired comparison. `compareDelay`, `compareRestbus`, `causeFault` and
the existing selected execution/input step are preserved in the URL. Timestamp
buttons open that execution's A/B camera/state view at the containing input
step; they do not claim sub-step video or execute the ECU again.
[API/SDK and browser readback](validation/vecu-network-causality-20261017/receipt.json).

### Reading A/B at the selected time

The Network Lab shows the fault schedule from the verified execution receipt,
not the editable form. A is the same scenario/seed without injected faults;
B applies that execution's saved schedule, including overlapping intervals.
These are the two sides of one test, not two different saved test executions.
Intervals include their start and exclude their end. Normal baseline braking
during initialization or following a lead vehicle is not an injected fault.

Immediately below the cameras, Korean summaries describe injection location,
observed ECU protection/control, and the next CARLA response. Injection ending,
controller tracking, Brake recovery, released brake and resumed motion remain
separate observations. Fixed replay explicitly has no vehicle response. Missing
observations are not interpreted as healthy operation. Detailed sequence,
OS/BSW and Brake paths remain available in an expandable section.
`수신 CAN 명령 적용 중` describes valid command application and may accompany
100% braking; it replaces the ambiguous `정상 CAN 명령` label.

The UI interpretation checks and retained actual CARLA playback checks are in
[the UI validation receipt](validation/vecu-network-explanation-20261017/receipt.json).
This presentation change does not create a new simulator execution or alter
the retained trace, fault schedule or native control policy.

Every execution freezes network/test/source revisions, seed, native installation
manifest, binary/source/tool hashes, and adapter identities. `vecu-trace.json`
contains source requests and applied controls. `network-trace.json` contains
actual transport, routing and kernel events; `network-summary.json` supplies
per-side counters. The normal execution export is a hash-sealed JSON manifest
with artifact download references, not a ZIP containing all artifacts.

New Network Lab CARLA captures use lossless ZIP DEFLATE level 1 for BGRA camera
archives. Resolution, selected frames, raw pixels, per-image hashes and response
timestamps are preserved. Archive SHA/size values identify the compressed bytes;
member SHA/size values identify the original BGRA pixels. Earlier retained
Network Lab captures remain valid with their original stored ZIP bytes. Other
Virtual ECU and city capture paths keep their existing ZIP STORED format.

An independent Python audit runs after native outputs: it checks actual OS
activation counts, the complete C++ controller result against the reference
policy, real BSW callback routes, CAN encoding/decoding, accepted command
lineage and Brake watchdog/recovery. It never supplies live control. Native
component tests, full network acceptance, API/SDK fixtures and browser checks
are separate evidence classes. CARLA camera images show the simulated vehicle,
not camera ISP/RTL output or physical ECU validation. The reference application's
input is CARLA actor ground truth; these camera pixels are not perception input.
ECU state recovery, arrival of a released-brake CAN command, and resumed vehicle
motion are reported separately. Initial baseline braking is excluded from
post-fault metrics, and driving safety remains `not_evaluated`.

No Silver compatibility or feature parity is claimed. Appropriate description:
“Functional multi-ECU virtual network simulation with public BSW and OSEK OS
services, connected to a reference ADAS controller and CARLA.”

## Build and operation

Optional adapter: `integrations/vecu_network`. Native build recipes are in
`native/net`, `native/bsw`, `native/os`, and `native/controller`. The isolated
Linux install is sealed by `scripts/seal_install.py`; readiness verifies all
registered file hashes. No user-provided command or executable path is accepted.
New UI5330/API8230 use a separate workspace. Earlier services/results remain.

The retained 18-second compressed CARLA A/B cases occupy about 480 MB each,
including traces. Capture staging and publication can temporarily require
roughly twice that space; compression ratio varies with the scene. Check local
free space before another camera run. The acceptance campaign left less than
1 GiB free on its host. Published results remain intact; failed scratch camera
copies were removed only after verifying the retained remote originals and
recording their hashes and restoration paths in the validation receipts.

The pins are SIL Kit5.0.8 (MIT), autoas/as
`cc3709675ac30f7539c0710dfec6d579a5235841` (GPLv3/commercial dual license;
this evaluation uses GPLv3), and Trampoline
`ff287023252bf73273067f00d2dbc8bbc9a20401` (GPL family, retain upstream notices).
Original source, explicit OS/controller patches and dependency notices are
preserved. Process separation does not settle distribution obligations.
CAN/UDP payload definitions are AXIOM examples, not OEM signal databases.
