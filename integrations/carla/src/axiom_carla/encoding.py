"""Sensor encoding with no imaging dependency or external media assets."""
import struct
import zlib


def bgra_png(width: int, height: int, raw: bytes) -> bytes:
    if width <= 0 or height <= 0 or width * height > 2_000_000 or len(raw) != width * height * 4:
        raise ValueError("Invalid bounded BGRA frame dimensions or byte count")
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            start = (y * width + x) * 4
            rows.extend((raw[start + 2], raw[start + 1], raw[start]))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
