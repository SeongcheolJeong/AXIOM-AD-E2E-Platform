# Optional native autoas BSW runner

This executable compiles **unmodified upstream** `Com.c`, `PduR.c`,
`PduR_Com.c`, `PduR_CanIf.c`, `CanIf.c`, and `std_bit.c` from
[`autoas/as` commit cc3709675ac30f7539c0710dfec6d579a5235841](https://github.com/autoas/as/tree/cc3709675ac30f7539c0710dfec6d579a5235841).
The vendored source subset and every file hash are recorded in
`vendor/autoas.lock.json`; builds refuse modified upstream files. No upstream
patches or ASKAR OS sources are used. The AXIOM-specific configuration, JSONL
boundary and virtual driver are separate files.

The executable is an optional internal evaluation component, licensed under the
GPLv3 option of upstream's GPLv3/commercial dual license. The upstream LICENSE
is retained verbatim. Its abbreviated GPL text is supplemented by the complete
GNU text in `vendor/GPL-3.0.txt`. nlohmann/json 3.11.3 retains its MIT license.
Process separation does not by itself settle distribution/license obligations.

## Build

```sh
python3 integrations/vecu_network/native/bsw/build.py --output-dir /tmp/axiom-bsw-build
/tmp/axiom-bsw-build/axiom-autoas-bsw
```

The output directory must be new and outside the source tree. A C11/C++17 host
compiler is required. Build performs no downloads. `manifest.json` identifies
the compiler, platform, command lines, source hashes and binary hash. This is a
host communication-stack executable, not a production ECU or physical CAN driver.

## Protocol: `axiom-bsw/1`

One JSON object per input/output line. Initialize before any other operation.
`id` is optional and echoed. A reply has `ok`, `op`, `protocol`, `frames`,
`signals`, `trace`, `time_ms`, and `epoch`. An invalid request returns `ok:false`
with `error` and emits no CAN frame. A driver/BSW DET failure also fails the reply.

| Request | Behavior |
|---|---|
| `{"op":"init"}` | Calls actual CanIf, PduR, Com initialization, brings controller/PDU online and starts Com IPDU group. Returns upstream identity. |
| `{"op":"tx","can_id":256,"data":[0,1,2,3,4,5,6,7]}` | `Com_SendSignal → Com_TriggerIPDUSend → PduR_ComTransmit → PduR_Transmit → CanIf_Transmit → Can_Write`. `frames` contains driver output, followed by actual TxConfirmation callbacks. |
| `{"op":"rx","can_id":256,"data":[0,1,2,3,4,5,6,7]}` | `CanIf_RxIndication → PduR_CanIfRxIndication → PduR_RxIndication → Com_RxIndication → Com_ReceiveSignal`. `signals` contains the returned Com buffer. |
| `{"op":"tick","elapsed_ms":10}` | Advances external time; calls actual CanIf/Com main functions each 10ms. Default advance is 1ms; range 1–10000ms. |
| `{"op":"read","can_id":256}` | Reads stored Rx signal via Com; does not pretend it is newly received. |
| `{"op":"reset"}` | Increments epoch, reinitializes actual stack and zeroes signal buffers; virtual time remains monotonic. |
| `{"op":"close"}` | Acknowledges then exits. |

Registered Classic CAN IDs are BrakeCommand `0x100`, BrakeStatus `0x101`,
VehicleState `0x200`, Heartbeat `0x201`. Every payload is exactly eight bytes.
Each ID has independent Tx/Rx IPDUs with opaque byte-array Com signals. Signal
packing and units belong to the network DBC contract; this stack preserves bytes.

Transmit is explicitly triggered by the OS/application schedule; Com cyclic
auto-transmit is disabled (CycleTime=0). `tick` still runs the upstream main
functions. Receive callbacks process synchronously; the network orchestrator
controls which tick supplies a queued frame. Driver confirmation here means
**accepted by the host driver**, not acknowledged by a remote ECU or a physical
bus. Network delivery receipts are separate evidence.

The runner is single-threaded; critical-section functions are no-ops because no
ISR or concurrent entry exists. Timing is externally supplied and not WCET.
Task/Event/Alarm handling must come from the separate Trampoline process.

Trace hooks are at real call sites and configured routing callbacks; no module
implementation is replaced by trace-only code. Each trace records its virtual
time and reset epoch. Protocol validation happens before invoking BSW.
