"""Bounded MCAP framing/CRC validation; never trust a summary for message data.

The pinned MCAP package supplies the interchange writer/API elsewhere. Ingest
uses an explicit bounded cursor because mcap 1.4's generic reader does not cap
inner record lengths or all decompression paths and skips some container CRCs.
"""

import hashlib
import io
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import lz4.frame
import zstandard
from mcap.opcode import Opcode

MAGIC = b"\x89MCAP0\r\n"
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_MESSAGES = 100_000
MAX_RECORDS = 250_000
MAX_CHANNELS = 2048
MAX_SCHEMAS = 1024
MAX_METADATA_RECORDS = 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_SCHEMA_PREVIEW_BYTES = 64 * 1024
KNOWN_OPCODES = frozenset(Opcode)


class RecordingValidationError(ValueError):
    status_code = 422


class RecordingLimitError(RecordingValidationError):
    status_code = 413


class Cursor:
    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    @property
    def remaining(self):
        return len(self.data) - self.offset

    def read(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise RecordingValidationError("MCAP record has a truncated or inconsistent inner length")
        start, self.offset = self.offset, self.offset + size
        return self.data[start:self.offset]

    def number(self, size: int) -> int:
        return int.from_bytes(self.read(size), "little")

    def string(self, max_bytes: int = MAX_TEXT_BYTES) -> str:
        size = self.number(4)
        if size > max_bytes:
            raise RecordingLimitError(f"MCAP text field exceeds {max_bytes} bytes")
        try:
            return self.read(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RecordingValidationError("MCAP container string is not valid UTF-8") from exc

    def mapping(self) -> dict[str, str]:
        size = self.number(4)
        if size > MAX_METADATA_BYTES:
            raise RecordingLimitError("MCAP metadata map exceeds the catalog size limit")
        fields = Cursor(self.read(size))
        result = {}
        while fields.remaining:
            key, value = fields.string(), fields.string()
            if key in result:
                raise RecordingValidationError("MCAP metadata contains duplicate map keys")
            result[key] = value
        return result

    def fixed_array(self, stride: int) -> None:
        size = self.number(4)
        if size % stride:
            raise RecordingValidationError("MCAP index array has an inconsistent entry size")
        self.read(size)

    def finish(self):
        if self.remaining:
            raise RecordingValidationError("MCAP record has trailing bytes beyond its declared fields")


@dataclass
class ParsedRecording:
    profile: str = ""
    schemas: dict[int, dict] = field(default_factory=dict)
    channels: dict[int, dict] = field(default_factory=dict)
    metadata: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    payload_sha256: str = ""
    payload_size: int = 0

    def warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)


def decompress_chunk(data: bytes, compression: str, declared_size: int) -> bytes:
    if declared_size > MAX_RECORD_BYTES:
        raise RecordingLimitError(f"MCAP decompressed chunk exceeds {MAX_RECORD_BYTES} bytes")
    try:
        if compression == "":
            expanded = data
        elif compression == "zstd":
            parameters = zstandard.get_frame_parameters(data)
            # max_output_size alone is not a cap for frames with a declared
            # content size, so check frame parameters before decompression.
            if parameters.window_size > MAX_RECORD_BYTES:
                raise RecordingLimitError("Zstandard frame window exceeds the chunk memory limit")
            if parameters.content_size == zstandard.CONTENTSIZE_ERROR:
                raise RecordingValidationError("Invalid Zstandard frame content size")
            if parameters.content_size != zstandard.CONTENTSIZE_UNKNOWN and parameters.content_size > MAX_RECORD_BYTES:
                raise RecordingLimitError("Zstandard frame content exceeds the chunk size limit")
            if parameters.dict_id:
                raise RecordingValidationError("Zstandard dictionaries are not supported")
            # The pinned C extension forwards this value in bytes. The frame
            # precheck remains necessary for the known-size one-shot path.
            expanded = zstandard.ZstdDecompressor(max_window_size=MAX_RECORD_BYTES).decompress(data, max_output_size=MAX_RECORD_BYTES, allow_extra_data=False)
        elif compression == "lz4":
            frame = lz4.frame.get_frame_info(data)
            if frame["skippable"]:
                raise RecordingValidationError("Skippable LZ4 frames are not supported")
            if frame["content_size"] > MAX_RECORD_BYTES:
                raise RecordingLimitError("LZ4 frame content exceeds the chunk size limit")
            decoder = lz4.frame.LZ4FrameDecompressor()
            expanded = decoder.decompress(data, max_length=MAX_RECORD_BYTES + 1)
            if len(expanded) > MAX_RECORD_BYTES:
                raise RecordingLimitError("LZ4 output exceeds the chunk size limit")
            if not decoder.eof:
                raise RecordingValidationError("LZ4 chunk is truncated or exceeds the bounded output limit")
            if decoder.unused_data:
                raise RecordingValidationError("Concatenated or trailing LZ4 frames are not supported")
        else:
            raise RecordingValidationError(f"Unsupported MCAP chunk compression: {compression!r}")
    except RecordingValidationError:
        raise
    except (ValueError, RuntimeError, zstandard.ZstdError) as exc:
        raise RecordingValidationError(f"Invalid or oversized {compression or 'uncompressed'} MCAP chunk") from exc
    if len(expanded) > MAX_RECORD_BYTES:
        raise RecordingLimitError("Decompressed chunk exceeds the size limit")
    if len(expanded) != declared_size:
        raise RecordingValidationError("MCAP chunk's actual expanded size differs from uncompressed_size")
    return expanded


def parse_mcap(path: Path | bytes, payload_stream: BinaryIO) -> ParsedRecording:
    result = ParsedRecording()
    payload_hash = hashlib.sha256()
    record_count, expanded_bytes, metadata_bytes = 0, 0, 0
    schema_fingerprints, channel_fingerprints = {}, {}
    used_channels: set[int] = set()

    def scan_record(opcode: int, body: bytes, *, in_chunk: bool = False, in_summary: bool = False):
        nonlocal record_count, expanded_bytes, metadata_bytes
        record_count += 1
        expanded_bytes += len(body) + 9
        if record_count > MAX_RECORDS:
            raise RecordingLimitError(f"MCAP exceeds {MAX_RECORDS} records")
        if expanded_bytes > MAX_EXPANDED_BYTES:
            raise RecordingLimitError(f"MCAP expanded parsing volume exceeds {MAX_EXPANDED_BYTES} bytes")
        if opcode == 0:
            raise RecordingValidationError("MCAP opcode zero is reserved")
        if in_chunk and opcode in KNOWN_OPCODES and opcode not in (Opcode.SCHEMA, Opcode.CHANNEL, Opcode.MESSAGE):
            raise RecordingValidationError("This MCAP record type cannot appear inside a chunk")
        if in_summary and opcode in KNOWN_OPCODES and opcode not in (Opcode.SCHEMA, Opcode.CHANNEL, Opcode.STATISTICS, Opcode.CHUNK_INDEX, Opcode.ATTACHMENT_INDEX, Opcode.METADATA_INDEX, Opcode.SUMMARY_OFFSET):
            raise RecordingValidationError("MCAP data record appears after DataEnd")
        if not in_chunk and not in_summary and opcode in KNOWN_OPCODES and opcode not in (Opcode.HEADER, Opcode.SCHEMA, Opcode.CHANNEL, Opcode.MESSAGE, Opcode.CHUNK, Opcode.METADATA, Opcode.ATTACHMENT, Opcode.MESSAGE_INDEX):
            raise RecordingValidationError("MCAP summary/index record appears before DataEnd")
        if opcode in (Opcode.MESSAGE_INDEX, Opcode.CHUNK_INDEX, Opcode.ATTACHMENT_INDEX, Opcode.METADATA_INDEX, Opcode.STATISTICS, Opcode.SUMMARY_OFFSET):
            result.warn("Native index offsets/statistics are not semantically certified; sample lookup and counts use the independently scanned messages.")
        cursor = Cursor(body)
        if opcode == Opcode.HEADER:
            result.profile = cursor.string()
            cursor.string()  # writer library, retained in the original file
        elif opcode == Opcode.SCHEMA:
            schema_id, name, encoding = cursor.number(2), cursor.string(), cursor.string()
            if schema_id == 0:
                raise RecordingValidationError("MCAP Schema ID zero is reserved for no schema")
            data = cursor.read(cursor.number(4))
            fingerprint = hashlib.sha256(body).hexdigest()
            if schema_id in schema_fingerprints and schema_fingerprints[schema_id] != fingerprint:
                raise RecordingValidationError(f"MCAP Schema {schema_id} is redefined with different content")
            if schema_id not in schema_fingerprints:
                if len(schema_fingerprints) >= MAX_SCHEMAS:
                    raise RecordingLimitError(f"MCAP exceeds {MAX_SCHEMAS} schemas")
                preview = data[:MAX_SCHEMA_PREVIEW_BYTES]
                try:
                    definition = preview.decode("utf-8") if len(data) <= MAX_SCHEMA_PREVIEW_BYTES else preview.decode("utf-8", errors="ignore")
                except UnicodeDecodeError:
                    definition = None
                result.schemas[schema_id] = {"id": schema_id, "name": name, "encoding": encoding, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "definition": definition, "definition_truncated": len(data) > MAX_SCHEMA_PREVIEW_BYTES}
                schema_fingerprints[schema_id] = fingerprint
        elif opcode == Opcode.CHANNEL:
            channel_id, schema_id = cursor.number(2), cursor.number(2)
            if channel_id not in channel_fingerprints and metadata_bytes + len(body) > MAX_METADATA_BYTES:
                raise RecordingLimitError(f"MCAP channel/metadata catalog exceeds {MAX_METADATA_BYTES} bytes")
            if schema_id and schema_id not in result.schemas:
                raise RecordingValidationError(f"MCAP Channel {channel_id} refers to Schema {schema_id} before its definition")
            topic, encoding, metadata = cursor.string(), cursor.string(), cursor.mapping()
            fingerprint = hashlib.sha256(body).hexdigest()
            if channel_id in channel_fingerprints and channel_fingerprints[channel_id] != fingerprint:
                raise RecordingValidationError(f"MCAP Channel {channel_id} is redefined with different content")
            if channel_id not in channel_fingerprints:
                if len(channel_fingerprints) >= MAX_CHANNELS:
                    raise RecordingLimitError(f"MCAP exceeds {MAX_CHANNELS} channels")
                metadata_bytes += len(body)
                result.channels[channel_id] = {"id": channel_id, "schema_id": schema_id, "topic": topic, "message_encoding": encoding, "metadata": metadata, "decoder": "json" if encoding == "json" else "opaque", "message_count": 0, "start_time_ns": None, "end_time_ns": None}
                channel_fingerprints[channel_id] = fingerprint
        elif opcode == Opcode.MESSAGE:
            if len(result.messages) >= MAX_MESSAGES:
                raise RecordingLimitError(f"MCAP exceeds {MAX_MESSAGES} messages")
            channel_id, sequence = cursor.number(2), cursor.number(4)
            if channel_id not in result.channels:
                raise RecordingValidationError(f"MCAP Message refers to Channel {channel_id} before its definition")
            log_time, publish_time = cursor.number(8), cursor.number(8)
            data = cursor.read(cursor.remaining)
            offset = result.payload_size
            if payload_stream.write(data) != len(data):
                raise OSError("MCAP payload sidecar write was incomplete")
            payload_hash.update(data)
            result.payload_size += len(data)
            result.messages.append({"ordinal": len(result.messages), "channel_id": channel_id, "log_time_key": f"{log_time:020d}", "publish_time_key": f"{publish_time:020d}", "sequence": sequence, "payload_offset": offset, "payload_length": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            used_channels.add(channel_id)
        elif opcode == Opcode.CHUNK:
            cursor.number(8), cursor.number(8)  # actual bounds come from Messages
            declared, crc = cursor.number(8), cursor.number(4)
            compression = cursor.string(max_bytes=32)
            compressed = cursor.read(cursor.number(8))
            # MCAP permits unknown extension fields at the end of Chunk.
            if declared > MAX_EXPANDED_BYTES - expanded_bytes:
                raise RecordingLimitError("MCAP chunk exceeds the remaining expanded-byte budget")
            expanded = decompress_chunk(compressed, compression, declared)
            if crc and zlib.crc32(expanded) != crc:
                raise RecordingValidationError("MCAP chunk CRC mismatch")
            if not crc:
                result.warn("One or more chunk CRCs are absent; checksums were validated only where present.")
            for inner_opcode, inner_body in bounded_records(io.BytesIO(expanded), len(expanded)):
                scan_record(inner_opcode, inner_body, in_chunk=True)
        elif opcode == Opcode.METADATA:
            if len(result.metadata) >= MAX_METADATA_RECORDS:
                raise RecordingLimitError(f"MCAP exceeds {MAX_METADATA_RECORDS} metadata records")
            if metadata_bytes + len(body) > MAX_METADATA_BYTES:
                raise RecordingLimitError(f"MCAP channel/metadata catalog exceeds {MAX_METADATA_BYTES} bytes")
            result.metadata.append({"name": cursor.string(), "metadata": cursor.mapping()})
            metadata_bytes += len(body)
        elif opcode == Opcode.ATTACHMENT:
            cursor.number(8), cursor.number(8)
            cursor.string(), cursor.string()
            cursor.read(cursor.number(8))
            crc_end = cursor.offset
            crc = cursor.number(4)
            if crc and zlib.crc32(body[:crc_end]) != crc:
                raise RecordingValidationError("MCAP attachment CRC mismatch")
            if not crc:
                result.warn("One or more attachment CRCs are absent.")
            result.warn("Attachments are retained in the original file but are not extracted or decoded.")
        elif opcode == Opcode.MESSAGE_INDEX:
            cursor.number(2)
            cursor.fixed_array(16)
        elif opcode == Opcode.CHUNK_INDEX:
            cursor.read(32)
            cursor.fixed_array(10)
            cursor.number(8)
            cursor.string()
            cursor.read(16)
        elif opcode == Opcode.ATTACHMENT_INDEX:
            cursor.read(40)
            cursor.string(), cursor.string()
        elif opcode == Opcode.METADATA_INDEX:
            cursor.read(16)
            cursor.string()
        elif opcode == Opcode.STATISTICS:
            cursor.read(42)
            cursor.fixed_array(10)
        elif opcode == Opcode.SUMMARY_OFFSET:
            cursor.read(17)
        else:
            cursor.read(cursor.remaining)
            result.warn("Unknown MCAP record types are retained in the original file and were not interpreted.")
        if cursor.remaining:
            # Message consumes the remainder as payload; DataEnd/Footer are
            # handled separately with exact lengths. Other records may grow.
            cursor.read(cursor.remaining)
            result.warn("Unknown MCAP record extension fields are retained in the original file and were not interpreted.")
        if metadata_bytes > MAX_METADATA_BYTES:
            raise RecordingLimitError(f"MCAP channel/metadata catalog exceeds {MAX_METADATA_BYTES} bytes")

    with (io.BytesIO(path) if isinstance(path, bytes) else path.open("rb")) as source:
        size = len(path) if isinstance(path, bytes) else path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise RecordingLimitError(f"MCAP upload exceeds {MAX_UPLOAD_BYTES} bytes")
        if source.read(8) != MAGIC:
            raise RecordingValidationError("MCAP opening magic is missing or invalid")
        data_crc, summary_crc = zlib.crc32(MAGIC), 0
        header_seen, data_end_seen, footer_seen = False, False, False
        summary_start_actual, first_summary_offset = None, None
        summary_record_count = 0
        summary_groups: set[int] = set()
        last_summary_opcode = None
        for opcode, body in bounded_records(source, size - 8, stop_at_footer=True):
            record_start = source.tell() - len(body) - 9
            prefix = struct.pack("<BQ", opcode, len(body))
            if not header_seen:
                if opcode != Opcode.HEADER:
                    raise RecordingValidationError("MCAP Header must be the first record")
                header_seen = True
            elif opcode == Opcode.HEADER:
                raise RecordingValidationError("MCAP has multiple Header records")
            if opcode == Opcode.DATA_END:
                if data_end_seen or len(body) != 4:
                    raise RecordingValidationError("MCAP DataEnd is duplicated or malformed")
                expected = int.from_bytes(body, "little")
                if expected and expected != data_crc:
                    raise RecordingValidationError("MCAP data section CRC mismatch")
                if not expected:
                    result.warn("MCAP data section CRC is absent.")
                data_end_seen = True
                summary_start_actual = source.tell()
                continue
            if opcode == Opcode.FOOTER:
                if not data_end_seen or len(body) != 20:
                    raise RecordingValidationError("MCAP Footer requires a preceding DataEnd and exactly 20 bytes")
                summary_start, offsets_start, expected_crc = struct.unpack("<QQI", body)
                if summary_start != (summary_start_actual if summary_record_count else 0):
                    raise RecordingValidationError("MCAP Footer summary_start does not match actual records")
                if offsets_start != (first_summary_offset or 0):
                    raise RecordingValidationError("MCAP Footer summary_offset_start does not match actual records")
                actual_crc = zlib.crc32(prefix + body[:16], summary_crc)
                if expected_crc and expected_crc != actual_crc:
                    raise RecordingValidationError("MCAP summary/footer CRC mismatch")
                if not expected_crc:
                    result.warn("MCAP summary CRC is absent.")
                footer_seen = True
                break
            if data_end_seen:
                summary_record_count += 1
                if opcode == Opcode.SUMMARY_OFFSET and first_summary_offset is None:
                    first_summary_offset = record_start
                elif first_summary_offset is not None and opcode != Opcode.SUMMARY_OFFSET:
                    raise RecordingValidationError("MCAP non-offset record appears in the Summary Offset section")
                if opcode != Opcode.SUMMARY_OFFSET and opcode != last_summary_opcode:
                    if opcode in summary_groups:
                        raise RecordingValidationError("MCAP Summary records are not grouped by opcode")
                    summary_groups.add(opcode)
                    last_summary_opcode = opcode
                summary_crc = zlib.crc32(prefix + body, summary_crc)
            else:
                data_crc = zlib.crc32(prefix + body, data_crc)
            scan_record(opcode, body, in_summary=data_end_seen)
        if not footer_seen or source.read(8) != MAGIC or source.read(1):
            raise RecordingValidationError("MCAP Footer/trailing magic is missing, truncated or followed by extra bytes")
    missing_channels = used_channels - result.channels.keys()
    if missing_channels:
        raise RecordingValidationError(f"MCAP messages refer to undefined channel IDs: {sorted(missing_channels)[:8]}")
    for channel in result.channels.values():
        if channel["schema_id"] and channel["schema_id"] not in result.schemas:
            raise RecordingValidationError(f"MCAP channel {channel['id']} refers to undefined schema {channel['schema_id']}")
    for message in result.messages:
        channel = result.channels[message["channel_id"]]
        timestamp = int(message["log_time_key"])
        channel["message_count"] += 1
        channel["start_time_ns"] = str(min(timestamp, int(channel["start_time_ns"]))) if channel["start_time_ns"] is not None else str(timestamp)
        channel["end_time_ns"] = str(max(timestamp, int(channel["end_time_ns"]))) if channel["end_time_ns"] is not None else str(timestamp)
    result.payload_sha256 = payload_hash.hexdigest()
    result.warn("Source attribution is user-declared; container acceptance is not sensor/perception validation. Calibration and coordinate transforms are unknown unless recorded.")
    return result


def bounded_records(source: BinaryIO, end_offset: int, *, stop_at_footer: bool = False):
    while source.tell() < end_offset:
        header = source.read(9)
        if len(header) != 9:
            raise RecordingValidationError("MCAP record header is truncated")
        opcode, length = struct.unpack("<BQ", header)
        if length > MAX_RECORD_BYTES:
            raise RecordingLimitError(f"MCAP record exceeds {MAX_RECORD_BYTES} bytes")
        if length > end_offset - source.tell():
            raise RecordingValidationError("MCAP record body extends beyond the file or chunk")
        body = source.read(length)
        if len(body) != length:
            raise RecordingValidationError("MCAP record body is truncated")
        yield opcode, body
        if stop_at_footer and opcode == Opcode.FOOTER:
            return
