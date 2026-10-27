# Trampoline OS native participant

This participant compiles and executes the **actual Trampoline POSIX kernel**,
pinned at `ff287023252bf73273067f00d2dbc8bbc9a20401`. GOIL generates the OS configuration
from `network_os.oil`. There is no Python scheduler or wall-clock timer.
The generated configuration uses the OSEK Task/Alarm/Event services shared with
the AUTOSAR OS API subset. `WITH_AUTOSAR=NO`; AUTOSAR-only extensions, timing
protection, applications and scalability classes are not enabled or validated.

The host adapter is separate from the autoas BSW process. It is not a complete
AUTOSAR implementation, MCU image, target execution-time model, or physical ECU
validation. The C++ control application executes through explicit external IPC
after the OS task's `control_request`; its instructions do not run inside this OS.

## Build

On Linux x86_64 with GCC, Python 3, patch, and C++ tools:

```sh
python3 integrations/vecu_network/native/os/build.py --output /absolute/new/os-build
```

An existing pinned archive can be passed with `--archive`; an already compiled
GOIL executable from the same pin can be passed with `--goil`. Archive, original
patched file, patch, GOIL, compiler, generated configuration, and binary hashes
are recorded in `build-manifest.json`. Output directories are immutable. The
complete extracted upstream source, original source archive when downloaded,
generated sources, and build log remain with the build.

The upstream POSIX port uses alternate-stack `setjmp`/`longjmp` contexts. Its
host build explicitly disables libc's `_FORTIFY_SOURCE` longjmp check, which
otherwise aborts this port on Ubuntu 24.04. It uses debug `-O0`; this is not a
production-hardening profile. Task stack sizes remain below the upstream
16-bit stack-size limit (49,152 bytes). Neither change substitutes scheduling.

`patches/external-tick.patch` changes only POSIX machine initialization: the
keyboard-fork helper and ViPER timer/process are not started. The real kernel's
counter interrupt, priority scheduling, alarms, and event services remain.

## Line JSON protocol: `vecu-network-os/1`

Requests and replies are newline-delimited, with a maximum 1,023-byte request.
Malformed JSON, unknown keys, wrong scalar types, duplicate keys, skipped or
repeated ticks, and unsupported operations produce `ok:false` and exit 64.

```json
{"op":"init","protocol":"vecu-network-os/1"}
{"op":"tick","tick_ms":1}
{"op":"tick","tick_ms":2,"suppress_control_event":true}
{"op":"reset"}
{"op":"tick","tick_ms":3}
{"op":"close"}
```

`init` starts at global tick 0 and epoch 0. Each subsequent `tick_ms` must be
the previous value + 1. The maximum supported global tick is 2,147,483,647 ms.
No wall-clock waiting advances the counter.

Each accepted tick triggers the real kernel's `SystemCounter` interrupt.
`BswAlarm` activates `BswTask` every 10 ms. `ControlAlarm` activates a dispatch
task every 50 ms; it calls `SetEvent(ControlTask, ControlEvent)`, and the extended
`ControlTask` wakes from `WaitEvent`, clears the event, and records a control
request. The low-priority Driver replies only after all ready higher-priority
tasks finish. At a common boundary BSW runs before control dispatch, then the
higher-priority ControlTask preempts dispatch. A reply includes:

```json
{
  "protocol":"vecu-network-os/1","op":"tick","ok":true,
  "engine":"trampoline-posix","upstream_commit":"ff287023252bf73273067f00d2dbc8bbc9a20401",
  "tick_ms":50,"epoch":0,"pid":1234,"bsw_due":true,"control_due":true,
  "bsw_count":5,"control_count":1,"suppressed_count":0,
  "events":[
    {"name":"BswAlarm.activate","tick_ms":50,"epoch":0},
    {"name":"BswTask.run","tick_ms":50,"epoch":0},
    {"name":"ControlAlarm.activate","tick_ms":50,"epoch":0},
    {"name":"ControlEvent.set","tick_ms":50,"epoch":0},
    {"name":"ControlTask.run","tick_ms":50,"epoch":0},
    {"name":"ControlTask.control_request","tick_ms":50,"epoch":0}
  ]
}
```

`suppress_control_event:true` omits **only** that tick's `SetEvent`; it does not
stop OS ticks, BSW tasks, alarms, or process progress. There is no deferred event
backlog. Normal event delivery resumes at the next absolute 50 ms boundary.

`reset` uses `execv(/proc/self/exe)` to restart the actual kernel in the same
process. It increments epoch, clears OS events and counters, preserves the global
tick, and rearms the next **absolute** 10/50 ms boundary. Its reply comes from the
restarted Driver task. Unbuffered stdin prevents queued requests being lost
across exec. A 200 ms reset recovery interval is represented by continuing every
1 ms tick with `suppress_control_event:true`; the coordinator separately resets
the external C++ application and invalidates prior-epoch network queues.

`close` acknowledges then calls `ShutdownOS`. EOF also shuts down. OS service
errors call the actual ErrorHook, report an error, and exit 72. Unexpected process
exit or missing acknowledgment must fail the parent run; no reference fallback.

## Validation

```sh
AXIOM_NETWORK_OS_EXE=/absolute/os-build/axiom-network-os \
  python3 integrations/vecu_network/native/os/tests/test_native_os.py
```

The real native tests cover 18,000 ticks, exact 10/50 ms task counts and priority
trace, suppression across 3–7 seconds, reset at a fractional period, repeated
resets, three-run logical equality, wall-clock independence, and malformed
protocol rejection. PID is excluded from logical equality; epoch and every
task/event remain included. Tests skip if no actual Linux binary is supplied.

## Licensing

`LICENSE-GPLv2.txt` is the unmodified upstream root license. Upstream files retain
their individual GPL/LGPL notices in the downloaded source. The new application
is GPL-2.0-or-later. The port patch remains under the affected upstream file's
terms. autoas is not linked here and its license is separate. Separate processes
do not by themselves establish customer distribution rights.
