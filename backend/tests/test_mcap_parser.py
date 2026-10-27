"""Independent byte fixtures for container boundaries, not payload semantics."""

import hashlib
import io
import struct
import zlib

import lz4.frame
import pytest
import zstandard
from mcap.writer import CompressionType, IndexType, Writer

from ade2e import mcap_ingest as parser


def record(opcode, body):
    return struct.pack("<BQ", opcode, len(body)) + body


def text(value):
    data = value.encode()
    return struct.pack("<I", len(data)) + data


def schema(identifier=1):
    return record(3, struct.pack("<H", identifier) + text("test") + text("jsonschema") + struct.pack("<I", 2) + b"{}")


def channel(identifier=1, schema_id=0):
    return record(4, struct.pack("<HH", identifier, schema_id) + text("/same") + text("json") + struct.pack("<I", 0))


def message(time=0, channel_id=1, payload=b"{}"):
    return record(5, struct.pack("<HIQQ", channel_id, 7, time, time) + payload)


def chunk(inner, compression="", declared=None, crc=None):
    if compression == "zstd":
        packed = zstandard.ZstdCompressor().compress(inner)
    elif compression == "lz4":
        packed = lz4.frame.compress(inner)
    else:
        packed = inner
    body = struct.pack("<QQQI", 0, 0, len(inner) if declared is None else declared, zlib.crc32(inner) if crc is None else crc)
    return record(6, body + text(compression) + struct.pack("<Q", len(packed)) + packed)


def container(data=(), summary=(), *, data_crc=True, summary_crc=True, footer_offsets=0):
    original = parser.MAGIC + record(1, text("") + text("independent-test")) + b"".join(data)
    original += record(15, struct.pack("<I", zlib.crc32(original) if data_crc else 0))
    summary_start = len(original) if summary else 0
    summary_bytes = b"".join(summary)
    footer_fields = struct.pack("<QQ", summary_start, footer_offsets)
    crc_prefix = struct.pack("<BQ", 2, 20) + footer_fields
    crc = zlib.crc32(summary_bytes + crc_prefix) if summary_crc else 0
    return original + summary_bytes + record(2, footer_fields + struct.pack("<I", crc)) + parser.MAGIC


def parse(tmp_path, data, stream=None):
    path = tmp_path / "input.mcap"
    path.write_bytes(data)
    output = stream if stream is not None else io.BytesIO()
    return parser.parse_mcap(path, output), output


@pytest.mark.parametrize("compression", [CompressionType.NONE, CompressionType.LZ4, CompressionType.ZSTD])
@pytest.mark.parametrize("indexed", [False, True])
def test_official_writer_compatibility_and_exact_sidecar(tmp_path, compression, indexed):
    output = io.BytesIO()
    writer = Writer(output, compression=compression, index_types=IndexType.ALL if indexed else IndexType.NONE, enable_data_crcs=True)
    writer.start()
    schema_id = writer.register_schema("foo", "jsonschema", b"{}")
    used = writer.register_channel("/same", "json", schema_id)
    empty = writer.register_channel("/same", "opaque", 0)
    times = [2**64 - 1, 0, 2**53 + 1, 2**53 + 1]
    payloads = [b"null", b"{}", b"[1]", b"false"]
    for time, payload in zip(times, payloads):
        writer.add_message(used, time, payload, time, sequence=7)
    writer.finish()
    result, sidecar = parse(tmp_path, output.getvalue())
    assert result.channels[empty]["message_count"] == 0
    assert result.channels[empty]["start_time_ns"] is None
    assert result.channels[used]["start_time_ns"] == "0"
    assert result.channels[used]["end_time_ns"] == str(2**64 - 1)
    assert [int(item["log_time_key"]) for item in result.messages] == times
    assert [item["ordinal"] for item in result.messages] == list(range(4))
    assert sidecar.getvalue() == b"".join(payloads)
    assert result.payload_sha256 == hashlib.sha256(sidecar.getvalue()).hexdigest()
    for entry, payload in zip(result.messages, payloads):
        assert sidecar.getvalue()[entry["payload_offset"]:entry["payload_offset"] + entry["payload_length"]] == payload
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("summary_only", [False, True])
def test_empty_channels_and_schema_are_preserved(tmp_path, summary_only):
    definitions = [schema(), channel(schema_id=1)]
    result, output = parse(tmp_path, container(summary=definitions) if summary_only else container(definitions))
    assert result.messages == []
    assert result.channels[1]["message_count"] == 0
    assert result.channels[1]["start_time_ns"] is None
    assert output.getvalue() == b""


@pytest.mark.parametrize("data,summary", [([message(), channel()], []), ([message()], [channel()]), ([channel(schema_id=1), schema()], [])])
def test_later_definition_does_not_validate_forward_reference(tmp_path, data, summary):
    output = io.BytesIO()
    with pytest.raises(parser.RecordingValidationError, match="before its definition"):
        parse(tmp_path, container(data, summary), output)
    assert output.getvalue() == b""


def test_chunk_reference_must_already_exist(tmp_path):
    with pytest.raises(parser.RecordingValidationError, match="before its definition"):
        parse(tmp_path, container([chunk(message() + channel())]))


@pytest.mark.parametrize("definitions", [[schema(), schema()[:-1] + b"X"], [channel(), channel()[:-1] + b"X"]])
def test_conflicting_definitions_are_rejected(tmp_path, definitions):
    with pytest.raises(parser.RecordingValidationError):
        parse(tmp_path, container(definitions))


@pytest.mark.parametrize("compression", ["", "zstd", "lz4"])
def test_chunk_crc_and_declared_size_are_independent_checks(tmp_path, compression):
    inner = channel() + message()
    with pytest.raises(parser.RecordingValidationError, match="CRC"):
        parse(tmp_path, container([chunk(inner, compression, crc=zlib.crc32(inner) ^ 1)]))
    with pytest.raises(parser.RecordingValidationError, match="actual expanded size"):
        parse(tmp_path, container([chunk(inner, compression, declared=len(inner) + 1)]))


@pytest.mark.parametrize("target", ["data", "summary", "attachment"])
def test_each_container_crc_is_checked(tmp_path, target):
    if target == "attachment":
        body = struct.pack("<QQ", 1, 1) + text("file") + text("text/plain") + struct.pack("<Q", 3) + b"abc"
        data = container([record(9, body + struct.pack("<I", zlib.crc32(body) ^ 1))])
    else:
        data = bytearray(container([channel(), message()], [channel()]))
        offset = data.index(struct.pack("<BQ", 15, 4)) if target == "data" else len(data) - 8 - 29
        data[offset + 9 + (0 if target == "data" else 16)] ^= 1
    with pytest.raises(parser.RecordingValidationError, match="CRC"):
        parse(tmp_path, data)


def test_missing_crc_is_visible_and_not_called_verified(tmp_path):
    result, _ = parse(tmp_path, container([chunk(channel() + message(), crc=0)], data_crc=False, summary_crc=False))
    assert any("chunk CRCs are absent" in warning for warning in result.warnings)
    assert any("data section CRC is absent" in warning for warning in result.warnings)
    assert any("summary CRC is absent" in warning for warning in result.warnings)


@pytest.mark.parametrize("bad", [b"", b"not mcap", container()[:-1], container() + b"extra", container()[:10]])
def test_truncated_or_extra_file_bytes_rejected(tmp_path, bad):
    with pytest.raises(parser.RecordingValidationError):
        parse(tmp_path, bad)


def test_declared_outer_and_inner_length_mismatch_rejected(tmp_path):
    bad = bytearray(container())
    struct.pack_into("<Q", bad, 9, 1)
    with pytest.raises(parser.RecordingValidationError):
        parse(tmp_path, bad)
    oversized_name = record(4, struct.pack("<HHI", 1, 0, 1024))
    with pytest.raises(parser.RecordingValidationError, match="inner length"):
        parse(tmp_path, container([oversized_name]))


def test_record_size_limit_applies_to_inner_chunk_records(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "MAX_RECORD_BYTES", 1024)
    # Only nine bytes expand, but the inner header advertises a giant body.
    with pytest.raises(parser.RecordingLimitError):
        parse(tmp_path, container([chunk(struct.pack("<BQ", 5, 2048), "zstd")]))


def test_expanded_budget_checked_before_codec_call(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "MAX_EXPANDED_BYTES", 1000)
    called = False

    def unexpected(*args):
        nonlocal called
        called = True
        pytest.fail("decompressor must not run after the remaining budget is exceeded")

    monkeypatch.setattr(parser, "decompress_chunk", unexpected)
    with pytest.raises(parser.RecordingLimitError, match="remaining expanded-byte budget"):
        parse(tmp_path, container([chunk(b"X", declared=1000)]))
    assert not called


@pytest.mark.parametrize("compression", ["zstd", "lz4"])
def test_codec_header_oversize_is_rejected_before_allocation(monkeypatch, compression):
    raw = b"x" * 65536
    packed = zstandard.ZstdCompressor().compress(raw) if compression == "zstd" else lz4.frame.compress(raw)
    monkeypatch.setattr(parser, "MAX_RECORD_BYTES", 1024)
    with pytest.raises(parser.RecordingLimitError):
        parser.decompress_chunk(packed, compression, 8)


@pytest.mark.parametrize("compression", ["zstd", "lz4"])
def test_unknown_content_size_remains_bounded(monkeypatch, compression):
    raw = b"x" * 65536
    packed = zstandard.ZstdCompressor(write_content_size=False).compress(raw) if compression == "zstd" else lz4.frame.compress(raw, store_size=False)
    monkeypatch.setattr(parser, "MAX_RECORD_BYTES", 1024)
    with pytest.raises(parser.RecordingValidationError):
        parser.decompress_chunk(packed, compression, 8)


@pytest.mark.parametrize("compression", ["zstd", "lz4"])
@pytest.mark.parametrize("suffix", ["truncated", "extra", "concatenated"])
def test_codec_frame_consumption_is_exact(compression, suffix):
    raw = b"payload"
    packed = zstandard.ZstdCompressor().compress(raw) if compression == "zstd" else lz4.frame.compress(raw)
    bad = packed[:-1] if suffix == "truncated" else packed + (b"extra" if suffix == "extra" else packed)
    with pytest.raises(parser.RecordingValidationError):
        parser.decompress_chunk(bad, compression, len(raw))


def test_unknown_codec_never_falls_back_to_uncompressed():
    with pytest.raises(parser.RecordingValidationError, match="Unsupported"):
        parser.decompress_chunk(b"plain", "brotli", 5)


def test_partial_sidecar_write_is_an_io_failure(tmp_path):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            super().write(data[:-1])
            return len(data) - 1

    with pytest.raises(OSError, match="incomplete"):
        parse(tmp_path, container([channel(), message(payload=b"hello")]), ShortWriter())


def test_metadata_limit_precedes_mapping_allocation(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "MAX_METADATA_BYTES", 32)
    with pytest.raises(parser.RecordingLimitError, match="catalog"):
        parse(tmp_path, container([record(12, text("large") + struct.pack("<I", 1000) + b"X" * 1000)]))


@pytest.mark.parametrize("opcode", [0, 1, 2, 6, 9, 15])
def test_illegal_nested_records_rejected(tmp_path, opcode):
    with pytest.raises(parser.RecordingValidationError):
        parse(tmp_path, container([chunk(record(opcode, b""))]))


def test_private_chunk_and_extended_records_remain_bounded_and_visible(tmp_path):
    extended_channel = channel() + b"extension"
    body = extended_channel[9:]
    result, _ = parse(tmp_path, container([chunk(record(4, body) + record(0x80, b"private") + message())]))
    assert len(result.messages) == 1
    assert any("extension fields" in item for item in result.warnings)
    assert any("Unknown MCAP record types" in item for item in result.warnings)


def test_attachment_extension_does_not_change_crc_coverage(tmp_path):
    body = struct.pack("<QQ", 0, 0) + text("file") + text("text/plain") + struct.pack("<Q", 3) + b"abc"
    parse(tmp_path, container([record(9, body + struct.pack("<I", zlib.crc32(body)) + b"future")]))


def test_summary_must_be_grouped(tmp_path):
    with pytest.raises(parser.RecordingValidationError, match="grouped"):
        parse(tmp_path, container(summary=[schema(), channel(), schema(2)]))


def test_summary_offset_presence_matches_footer(tmp_path):
    with pytest.raises(parser.RecordingValidationError, match="summary_offset_start"):
        parse(tmp_path, container(summary=[channel(), record(14, struct.pack("<BQQ", 4, 0, 0))]))


def test_summary_record_not_accepted_in_data_section(tmp_path):
    with pytest.raises(parser.RecordingValidationError, match="before DataEnd"):
        parse(tmp_path, container([record(11, b"\x00" * 46)]))
