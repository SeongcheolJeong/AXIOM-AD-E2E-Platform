# Native network reference controller

This is the full existing AXIOM immediate C++ reference controller, copied from
`integrations/vecu/native` and pinned in `source.lock.json`. The original source
and SHA-256 helper are preserved under `upstream/`. `network-sequence.patch`
contains the complete adaptation. Building verifies original files and patch,
applies exactly those changes, and checks the generated source hash.

Two changes are made: init reports `network-traffic-cpp/1`, and control request
sequence numbers must increase strictly but may skip. `time_s` must still equal
`sequence * 0.05`. A new process after ECU reset may start at the current global
sequence. No synthetic control steps are executed for missed OS events. Repeated
or decreasing control sequences and wrong timestamps remain errors. Existing
object sequence, packet identity, freshness and frozen-profile checks remain
unchanged. Object packets and controller invocation sequences are distinct.

The request/response protocol remains `hil-dut/1`, with unchanged route,
target-speed, physics and optional frozen-profile initialization. This is the
immediate policy only; generated code cannot enable the ramp variant.

```sh
python3 integrations/vecu_network/native/controller/build.py --output-dir /tmp/network-controller
/tmp/network-controller/reference-traffic-cpp
```

Output must be a new directory outside the source tree. Builds perform no
downloads and preserve generated source, compiler log, dependency/source hashes
and executable hash. Original AXIOM source ownership is unchanged; the copied
nlohmann/json 3.11.3 dependency retains its MIT license and hash lock.

For **post-run audit only**, the independent Python reference can set
`reference.sequence = request.sequence - 1` immediately before its `step` call.
That audit must not produce the live control and must retain the object's own
sequence/fingerprint checks. A fresh oracle must be created for each ECU reset.
Reset clears controller state; it does not replay missed tasks or old outputs.
The first object after reset must be a real newly delivered packet, consistent
with the inherited `object_delivery_present` check.

This host reference controller is not a customer production application,
AUTOSAR RTE, MCU execution-time model or safety-qualified vehicle controller.
