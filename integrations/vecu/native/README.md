# Native reference ECU applications

These C++17 programs execute the entire bounded route tracking, headway,
packet freshness, duplicate detection, and fault recovery algorithm in a native
host process. They do not invoke Python. They implement the existing `hil-dut/1`
JSON-lines protocol (`init`, sequential 20 Hz `step`, `close`) and retain its
256 KiB message limit. They are reference algorithms, not production ECU
software, AUTOSAR/OS virtualization, or a safety validated vehicle controller.

## Build

```sh
python3 integrations/vecu/native/build.py --policy immediate --output-dir /tmp/ecu-a
python3 integrations/vecu/native/build.py --policy ramp --output-dir /tmp/ecu-b
```

The build requires an already installed C++17 compiler (`c++`, or the operator's
`--compiler` argument / `CXX` environment setting). It performs no downloads or
package installation. The selected compiler is executed without a shell. The
output directory contains an executable, `build.log`, and `manifest.json`;
stdout contains only the same manifest JSON. Existing executable/manifest
outputs are never overwritten. Public platform APIs must expose registered
source identities, not arbitrary commands or paths.

- `cpp-immediate` / policy `immediate` creates `reference-traffic-cpp`, version
  `reference-traffic-cpp/1`. It ports the Python `reference-traffic-dut/2` and
  `axiom-city-pure-pursuit/2` behavior. Full reply parity is tested, including
  route projection, rear axle geometry, packet counters and immutable profiles.
- `cpp-ramp` / policy `ramp` creates `ramp-traffic-cpp`, version
  `ramp-traffic-cpp/1`. It shares the normal controller and 250 ms freshness /
  three-new-packet recovery defaults. At stale entry throttle immediately
  becomes zero; brake linearly increases from the preceding applied brake to
  one over 300 ms. After the fresh packet gate completes, brake/throttle move
  from the preceding applied command toward the current normal target over
  500 ms. A new stale episode restarts braking from the applied brake. Steering
  continues to use normal route feedback. `state` stays `recovering` during the
  release ramp. `policy_phase` and `phase_elapsed_s` explain the transition.
  Throttle is held at zero while blended brake exceeds `1e-9`, preserving the
  normal controller's brake/throttle exclusivity; acceleration resumes after
  brake release rather than commanding brake and throttle simultaneously.

Times are simulated seconds. A ramp starts at alpha zero on the triggering
sample and reaches its endpoint on the sample at the specified duration.
Repeated packets do not refresh age or advance the recovery gate. Input
absence is distinct from repeated delivery. A packet sequence with changed
contents is rejected. The two policies are alternatives for simulation
comparison, not a claim that the ramp policy is safer.

## Provenance and dependencies

The manifest records source and binary SHA-256, compiler identity and flags,
target OS/architecture, dependency source/license identities, and build log
hash. `source_sha256` hashes compact sorted JSON mapping the fixed `SOURCES`
relative paths in `build.py` to their SHA-256 hashes. Source identity is checked
before and after compilation. The platform must separately record the build
execution identity, runtime identity and immutable target binary location.

The only vendored dependency is unmodified **nlohmann/json 3.11.3**, under the
MIT license. The original license and pinned upstream file hashes are kept in
`vendor/`; `build.py` rejects modified vendored files. A local SHA-256
implementation validates the existing frozen profile identity and is tested
against Python hashlib for UTF-8 profiles. Code licenses do not grant rights
to unrelated simulator assets, datasets, ECU code or third-party tools.
