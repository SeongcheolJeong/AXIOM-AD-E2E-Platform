# Sensor declarations and recorded replay · API contract

This slice decodes actual MCAP message payloads into bounded image and point
previews. A successful decode is a syntax/representation result, not a sensor
validation result. Rig transforms, intrinsics, clock offsets and age policies are
source declarations; the API checks mathematical validity, not physical accuracy.

All routes below are authenticated under `/api/v1`. Public models are
`backend/src/ade2e/sensor_schemas.py` and `sensor_decoder_schemas.py`. Nanosecond
timestamps and ages are decimal strings. Cursor values are uint64; declared
offsets are signed decimal strings with magnitude at most uint64 max. Derived
aligned times/ages use Python integer arithmetic and remain exact strings even
when an offset moves them outside the unsigned timestamp range.

## Reusable sensor rigs

- `POST /sensor-rigs`: `RigCreate`, returns `RigDetail`, 201.
- `GET /sensor-rigs`: `RigDetail[]`, newest created rig first.
- `GET /sensor-rigs/{id}?revision=N`: a selected immutable revision; omission
  reads the current revision for authoring.
- `POST /sensor-rigs/{id}/revisions`: complete `RigCreate` plus
  `expected_revision>=1`; returns the newly stored `RigDetail`, 201. A concurrent
  change returns 409. Existing revisions are never edited.
- `GET /sensor-rigs/{id}/revisions`: `RigDetail[]`, newest revision first.
- `GET /sensor-rigs/{id}/export?revision=N`: exact stored canonical JSON bytes.

`RigCreate`: `name`, `description` (optional), `reference_frame`,
`coordinate_convention` (`right_handed_z_up|left_handed_z_up|unknown`),
`source_kind` (`unknown|synthetic|simulation|measured`), `source`, nonempty `note`,
and 1–32 `sensors`. Sensor keys and frame IDs are unique within the revision.
Each sensor has `key` (ASCII 1–64), `name`, `kind` (`camera|lidar`), `frame_id`,
optional `transform`, optional camera-only `intrinsics`.

`transform` is source sensor-frame → rig reference-frame, in metres, with
`translation_m:[x,y,z]` (each component ±1,000,000 m), `rotation_xyzw:[x,y,z,w]`. Quaternion squared norm must
be within 1e-6 of one. Values are finite; no silent normalization occurs. A null
transform means unknown, including for a sensor located at the presumed origin.

`intrinsics`: `width`, `height` (1–16384), positive `fx`, `fy`, finite `cx`, `cy`,
`distortion_model`, and `distortion_coefficients`. Supported declaration shapes
are `none` (0 coefficients), `plumb_bob` (5), `rational_polynomial` (8),
`equidistant` (4), `unknown` (0–16). These declarations do not cause rectification
or camera projection during decode. Camera previews preserve recorded pixels.

`RigDetail` contains all authored fields plus `schema_version:"1"`, `id`,
`revision`, `created_at`, `definition_hash`, `calibration_status:"source_declared"`,
`manifest_hash`, `manifest_size_bytes`, `download_url`. The export contains the
`RigSnapshot` fields (all detail fields except the final three export metadata
fields). Its exact bytes match `manifest_hash` and remain unchanged after edits.

## Recording bindings

- `POST /recordings/{id}/sensor-bindings`: complete `BindingCreate`, 201.
- `GET /recordings/{id}/sensor-bindings?revision=N`: selected `BindingDetail`;
  authoring-only omission returns current. No binding returns 404.
- `GET /recordings/{id}/sensor-bindings/revisions`: `BindingDetail[]`, newest
  revision first; an existing recording without bindings returns `[]`.
- `GET /recordings/{id}/sensor-bindings/export?revision=N`: exact immutable bytes.

`BindingCreate`: `expected_revision` (0 for initial creation), `rig_id`, required
`rig_revision`, nonempty `note`, and 1–32 `channels`. Each channel has
`channel_id`, `sensor_key`, `decoder_id`, `decoder_version`, `max_age_ns` (null or
uint64 string), `log_time_offset_ns` (default `"0"`). Channel IDs and sensor keys
must be unique. The selected channel must belong to this recording; its exact
schema/encoding and kind must be supported by the pinned decoder and rig sensor.
Foreign keys preserve actual recording/channel/rig revision relationships.

The server copies the selected `rig_snapshot` and `rig_manifest_hash` into the
new binding revision. Later rig changes do not update existing bindings.
`BindingDetail` also contains `recording_id`, `recording_sha256`, `revision`,
`created_at`, `definition_hash`, and the export metadata fields described above.
Export bytes are the `BindingSnapshot` without export metadata. Reads verify
stored canonical bytes, digest, identity and relational mapping metadata.

Authoring has optimistic revision guards and atomic transactions. It does not
add an Idempotency-Key contract; a lost create response should be resolved by
reading the catalog, and a repeated revision request can return 409.

## Decoder discovery and exact-message inspection

- `GET /sensor-decoders`: installed `SensorDecoderMetadata[]`.
- `GET /recordings/{id}/sensor-channels`: `{recording_id,channels,warnings}`.
  A channel reports `channel_id,topic,message_encoding,schema_id,schema_name,
  schema_sha256,status:supported|unsupported,kind,decoders,reason`. Support uses
  exact encoding and bounded complete schema content, never topic-name guessing.
- `GET /recordings/{id}/sensor-frame?channel_id=N&ordinal=N&max_points=20000`:
  inspect exactly the original ordinal, including duplicates. `max_points` is
  1–100000. An ordinal from another channel returns 404.
- `GET /recordings/{id}/messages/{ordinal}/payload`: exact hash-checked message
  payload bytes, `application/octet-stream`; does not return an MCAP container.

`SensorFrame`: `{recording_id,channel_id,status,message,decoded,reason,warnings}`.
`message` has `ordinal,log_time_ns,publish_time_ns,sequence,size_bytes,sha256,
preview_hex` (up to 64 original bytes). `status` is
`decoded|unsupported|invalid_payload|limit_exceeded|decoder_unavailable|decoder_error|integrity_error`.
`decoded` is null on failure; `decoder_error` includes invalid plugin output.
An exact-frame payload integrity failure returns 409; bundles preserve this as
a per-channel `integrity_error` and continue the remaining channels.

`SensorDecodeResult` preserves decoder id/version, source SHA/size, original
encoding, payload `frame_id`, separate payload `capture_time_ns`, kind, units,
coordinate convention, transform policy, warnings, and either `camera` or
`point_cloud`. PNG previews and XYZ float32 little-endian previews are inline
base64 with their own SHA-256 and byte count. Point stride is 12 bytes; optional
intensity is a separate float32 little-endian buffer with the same point count.
Original/selected/invalid/skipped point counts and sampling stride are explicit.
No intensity is invented. Foxglove message pose, when present, is applied into
the message frame and retained as `source_pose`; rig transforms are not applied.

The registry is replaceable through `SensorDecoderRegistry.register` and a
`SensorDecoder` protocol. Plugins are trusted installed Python code. Core checks
source and decoder identity, preview hashes/lengths, bounded PNG chunk CRCs and
full image decoding, finite XYZ/intensity buffers, exact XYZ bounds and complete
point accounting before returning a result.
Input bounds are 16 MiB, 2 million decoded pixels, 1 million source points for
built-ins. A thread timeout is not claimed as process-level CPU isolation.

## Cursor bundles

`GET /recordings/{id}/sensor-bundle?at_ns=N&binding_revision=R&channel_id=C&max_points=20000`

`at_ns` is required. **Binding revision omission always means unbound.** Replay
does not silently use the current stored binding. With a binding, requested
channels must be in that revision and all age/offset/decoder choices come from
the snapshot. A query `max_age_ns` override is rejected. Without a binding,
explicit channels are inspected natively; omission selects the first up to four
supported channels in numeric ID order. Optional unbound `max_age_ns` is a query
declaration. Repeated `channel_id` selects at most four unique channels. A bound
rig with more than four channels requires an explicit subset.

Selection uses `aligned_time = log_time + declared_offset`: choose the largest
aligned time <= cursor, then the last original ordinal at that time. No preceding
observation means `missing`, with optional next log/aligned timestamp. **A future
message is never substituted into the bundle.** Stale means age strictly greater
than max age; equality remains fresh. With no age declaration, freshness is
unknown, even at the cursor. A stale observation retains identity/timestamps but
omits its decoded preview. Explicit exact-frame inspection remains available.

`SensorBundle`: `recording_id,recording_sha256,at_ns,binding_revision,
binding_manifest_hash,rig_snapshot,alignment_basis:"log_time_plus_declared_offset",
entries,encoded_data_size_bytes,warnings`.

Each entry has `channel_id,sensor_key,selection_status:selected|missing`,
`time_relation:at_cursor|past|missing`, `freshness:fresh|stale|unknown|missing`,
`max_age_ns,age_policy_source:binding|query|unknown,log_time_offset_ns,
aligned_time_ns,age_ns,next_log_time_ns,next_aligned_time_ns,message,frame,
declared_sensor,transform_status,transform,reason`. At most 24 MiB of encoded
preview data is returned per bundle; affected channels report `limit_exceeded`
with no preview. Camera and LiDAR are decoded independently.

`transform_status` is `available|unbound|unknown_convention|unknown_transform|
frame_mismatch|kind_mismatch|not_decoded|out_of_range`. `available` means a source-declared
transform is mathematically usable and the actual payload frame/kind match the
pinned sensor; it is not physical calibration validation. Unknown convention,
unknown transform, unsupported decoding, frame mismatch or a declared transform
that overflows the selected float32 point preview disables rig view.
Native inspection remains possible. Image bytes are not camera overlays.

Log, publish and capture timestamps remain distinct; ROS capture timestamps may
be signed, while MCAP log/publish timestamps remain uint64. A common cursor and offsets
do not prove sensor acquisition synchronization. Existing message digests cover
payload bytes; the derived sidecar digest does not hash SQL index/timestamps.
Schema catalogs contain bounded previews and cannot be assumed to contain an
arbitrarily large protobuf/CDR schema. Unsupported encodings remain opaque in
the original recording browser.

## Storage and acceptance

Schema 5 adds rig heads/revisions, binding heads/revisions, relational channel
mappings and run-recording lineage. Migration accepts known schema versions
1–4, preserves existing tables and payload files, and rejects unknown versions
before schema writes. Rig and binding manifests are capped at 1 MiB each.

Tests cover source bytes and bounded decoder contracts, exact uint64 selection,
offsets, duplicates, no future rendering, stale withholding, immutable history,
optimistic conflicts/transaction rollback, unavailable decoder versions, frame
mismatch, content integrity, auth and migration. Actual simulator sensor output
and calibrated real-world accuracy retain separate acceptance gates.
