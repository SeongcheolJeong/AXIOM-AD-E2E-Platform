# Recording data slice · implementation contract

This extends the platform with imported MCAP recordings. Container acceptance,
payload decoding, synchronized observation, and sensor/perception validation are
separate claims. Unknown encoding and absent calibration remain explicit.

## API

All endpoints use the existing `/api/v1` authentication and error conventions.
Nanosecond timestamps are **decimal strings**, including query parameters, to
preserve uint64 precision in browsers. Bounds are inclusive unless stated otherwise.

- `POST /recordings?name=...&source_kind=...&source=...`: binary MCAP request body
  (`application/octet-stream`), not JSON or multipart. `source_kind` is
  `unknown|synthetic|simulation|measured`, default `unknown`; this is user-declared
  provenance, not a platform verification. `name` max 180, `source` max 1000.
  Returns a RecordingDetail (201), or the existing detail (200) for identical
  file bytes **and identical supplied metadata**. The same bytes with different
  metadata must not silently overwrite the original attribution.
- `GET /recordings?q=...`: RecordingSummary array, newest first.
- `GET /recordings/{id}`: RecordingDetail.
- `GET /recordings/{id}/download`: exact original file, checksum checked, safe filename.
- `GET /recordings/{id}/samples?channel_id=N&at_ns=N&limit=N`: nearest sample at
  or before `at_ns`, then following samples, ordered by `(log_time_ns, ordinal)`.
  An exact duplicate timestamp anchors on the last file-order ordinal at that
  timestamp. Use `from_start=true` for complete chronological traversal, then
  `after_ordinal=N` with the previous response's `next_after_ordinal`. These modes
  are mutually exclusive with `at_ns` and each other. Continuation resolves the
  anchor in the same recording/channel and resumes after its `(time, ordinal)`;
  file ordinals alone are not chronological. This exposes every duplicate, even
  at timestamp zero or in groups exceeding one page.
  Default `at_ns` is recording start, `limit` default 1 / maximum 100. If there
  is no preceding sample, return the first following sample and expose that fact
  through timestamps, never label it current. Channel identity is MCAP channel ID,
  not topic name (duplicate topics with different schemas are valid).

## Response shapes

RecordingSummary: `id`, `name`, `source_kind`, `source`, `sha256`, `size_bytes`,
`created_at`, `format` (`mcap`), `profile`, `message_count`, `channel_count`,
`start_time_ns` / `end_time_ns` (string or null), `duration_s` (number),
`download_url`, `warnings` (string array), `index` (`version`, `sha256`,
`size_bytes`, `source_sha256`) for the validated derived payload file.
Its `sha256` hashes concatenated payload bytes, not the SQL index rows.

RecordingDetail extends summary with `channels`, `schemas`, `metadata`.
Channels: `id` (number), `topic`, `message_encoding`, `schema_id` (number),
`message_count`, `start_time_ns` / `end_time_ns` (string or null),
`metadata` (string map), `decoder` (`json|opaque`).
Schemas: `id`, `name`, `encoding`, `sha256`, `size_bytes`, `definition`
(UTF-8 text or null; only bounded text), `definition_truncated` (boolean).
Recording `metadata`: array of `{name, metadata}` from MCAP Metadata records.

Samples response: `{recording_id, channel_id, requested_time_ns, samples, warnings,
next_after_ordinal}`. Browse-mode `requested_time_ns` is null; continuation is null
when there is no further page. At-time mode still returns the selected cursor.
Each sample: `ordinal` (stable file-order index), `log_time_ns`, `publish_time_ns`,
`sequence`, `size_bytes`, `sha256`, `decoded` (JSON value or null),
`decoded_text` (bounded original UTF-8 JSON text or null),
`decode_status` (`decoded|opaque|invalid_json|too_large`), `preview_hex`
(bounded prefix), `truncated` (boolean). JSON null can be decoded successfully;
use decode_status, not decoded truthiness. Unsafe JavaScript integers suppress
structured `decoded` with a warning while `decoded_text` preserves the original;
NaN/Infinity/nonfinite overflow are invalid JSON. Preserve sequence and publish time;
do not sort channels by publish time or silently change clocks.

## Storage and correctness

- Original file bytes live in managed object storage, independent of run artifacts.
  Recording, channels and schemas have real relational IDs/foreign keys.
- Record validation scans actual messages, rather than trusting Summary statistics.
  Counts, bounds, hashes and channel/schema references come from parsed content.
- Import and DB publication are atomic from the client perspective; rejected
  inputs and failed writes/commits leave no catalog row or owned partial files.
  Existing files must survive failed duplicate imports. Record schema migration.
- Preserve unknown payloads; only the explicit JSON decoder interprets data.
  No ROS/CDR/protobuf/image/point-cloud claim merely because an MCAP opens.
- Bound upload size, record/decompressed chunk size, counts and preview payloads;
  reject malformed/truncated/oversized data with actionable errors. Unsupported
  or unsafe compression must be rejected explicitly, not silently skipped.
- Seek must work for indexed and unindexed files, out-of-order timestamps,
  duplicate timestamps, empty channels and timestamps beyond JS safe integers.
  An index is an implementation detail; its bytes must trace to the original SHA.
- Imported log metadata must not be treated as simulator truth or certification.
  Calibration and coordinate transforms are unknown unless explicitly recorded.

## UI

Add a Recordings tab to Data catalog beside existing Execution artifacts.
Import form records name, source kind and source label before uploading a file.
Show a selectable recording list, source attribution/hash, channel/schema panel,
and time cursor in relative seconds with the exact nanosecond timestamp visible.
Selecting a channel and moving the cursor fetches actual samples. Preserve exact
timestamps and show age/future status, decoded JSON or bounded opaque preview.
Unknown encodings remain browseable without pretending to render sensor data.

## Acceptance

Use an original generated MCAP demo (synthetic telemetry and actor messages, with
precise large timestamps and unknown payload example) plus independent fixtures.
Verify original download bytes, CRC/truncation and compression boundaries,
duplicate imports, DB migration with old runs preserved, seek/clock semantics,
authentication, write rollback, SDK use, and browser upload → channel → time seek.
Document limits and measured import/seek behavior before claiming scale.
