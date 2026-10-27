# SIL Kit native functional network bundle

The `participant.cpp` adapter executes four registered roles against **SIL Kit
5.0.8**, using coordinated lifecycle and asynchronous 1ms simulation steps.
`build.py` compiles that adapter and seals participant, registry, system controller
and shared-library hashes in one run manifest. The build performs no downloads.

The official Ubuntu 24.04 x86_64 SDK release ZIP is pinned to SHA256
`b1dc751c9a44b9dd4a8822b085162a050f3293ff571fbdc9f132b7807ddebbe8`.
Every linked runtime and installed public header is compared with the verified
archive before compilation. Original SIL Kit binaries and headers are unmodified.

```sh
python3 build.py \
  --sdk-root /opt/SilKit-5.0.8-ubuntu-24.04-x86_64-gcc/SilKit \
  --sdk-archive /opt/SilKit-5.0.8-ubuntu-24.04-x86_64-gcc.zip \
  --output-dir /tmp/axiom-net-build
```

The output directory must be new. The participant finds its sealed library via
`$ORIGIN/lib`, independently of an unrelated host SIL Kit installation. Source,
compiler, SDK headers, runtime binaries and licenses are hashed. Source changes
during a build fail instead of publishing a successful manifest. The companion
nlohmann/json 3.11.3 MIT header is copied from the prior pinned native ECU source;
its original hash lock and MIT license are retained.

SIL Kit uses the MIT license. The output bundle retains the original release
MIT license and `ThirdParty/LICENSES.rst`; transitive dependencies have their own
terms. No SIL Kit code or license is merged into the GPL autoas BSW executable.

Sources: [official release](https://github.com/vectorgrp/sil-kit/releases/tag/v5.0.8),
[SIL Kit simulation documentation](https://vectorgrp.github.io/sil-kit-docs/simulation/simulation.html).

This is the **functional** SIL Kit CAN/Ethernet network, not a detailed network
simulator. CAN configuration is Classic CAN, 11-bit ID and 8-byte payload. Setting
the nominal bit rate does not make this a bit-level model: arbitration, stuffing,
error frames, electrical signals and accurate bus-load timing are unverified.
Ethernet exchanges actual frame bytes; this does not model a physical switch,
link faults, TSN, signal integrity or hard real-time execution. Frame delivery
receipts and externally scheduled faults are the supported evidence boundary.
