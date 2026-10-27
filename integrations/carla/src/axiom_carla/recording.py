"""Portable MCAP for the frames actually captured by the CARLA adapter.

Foxglove JSON message layouts are documented at docs.foxglove.dev/docs/sdk/schemas.
This writer does not infer missing frames, sensor calibration or physical fidelity.
"""
from __future__ import annotations

import base64
import io
import json
import math
import struct
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from mcap.writer import CompressionType, Writer


def _json(value) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties)}


_NUMBER = {"type": "number"}
_VECTOR = _object({axis: _NUMBER for axis in ("x", "y", "z")})
_ROTATION = _object({axis: _NUMBER for axis in ("x", "y", "z", "w")})
_STAMP = _object({"sec": {"type": "integer", "minimum": 0}, "nsec": {"type": "integer", "minimum": 0, "maximum": 999999999}})
_BYTES = {"type": "string", "contentEncoding": "base64"}

# Original descriptions of the standard layouts, not third-party source copies.
IMAGE_SCHEMA = _object({"timestamp": _STAMP, "frame_id": {"type": "string"}, "data": _BYTES, "format": {"type": "string"}})
POINT_SCHEMA = _object({
    "timestamp": _STAMP, "frame_id": {"type": "string"},
    "pose": _object({"position": _VECTOR, "orientation": _ROTATION}),
    "point_stride": {"type": "integer", "minimum": 1},
    "fields": {"type": "array", "items": _object({"name": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "type": {"type": "integer", "enum": list(range(9))}})},
    "data": _BYTES,
})


@dataclass(frozen=True)
class CapturedFrame:
    world_frame: int
    camera_timestamp_s: float
    lidar_timestamp_s: float
    png: bytes
    lidar_xyzi: bytes


def simulation_timestamp_ns(seconds: float) -> int:
    """Round the reported double seconds; this does not recover ns precision."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Sensor timestamp must be finite nonnegative simulation seconds")
    value = int((Decimal(str(seconds)) * 1_000_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
    if value > 2**64 - 1:
        raise ValueError("Sensor timestamp exceeds the MCAP uint64 clock")
    return value


def _stamp(nanoseconds: int) -> dict:
    seconds, nanos = divmod(nanoseconds, 1_000_000_000)
    return {"sec": seconds, "nsec": nanos}


def right_handed_xyzi(raw: bytes) -> bytes:
    """CARLA local x-forward/y-right/z-up -> x-forward/y-left/z-up."""
    if len(raw) % 16 or len(raw) > 16 * 1_000_000:
        raise ValueError("LiDAR bytes must contain bounded float32 XYZI points")
    output = bytearray(len(raw))
    for offset, point in enumerate(struct.iter_unpack("<ffff", raw)):
        x, y, z, intensity = point
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Captured CARLA LiDAR contains a nonfinite value")
        struct.pack_into("<ffff", output, offset * 16, x, -y, z, intensity)
    return bytes(output)


def capture_recording(frames: list[CapturedFrame], *, metadata: dict) -> bytes:
    """Write only supplied captures, preserving their separate source clocks."""
    if not 1 <= len(frames) <= 100:
        raise ValueError("A capture recording needs 1..100 captured frame pairs")
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD)
    writer.start(profile="json", library="axiom-carla-captures/1")
    image_schema = writer.register_schema(name="foxglove.CompressedImage", encoding="jsonschema", data=_json(IMAGE_SCHEMA))
    cloud_schema = writer.register_schema(name="foxglove.PointCloud", encoding="jsonschema", data=_json(POINT_SCHEMA))
    camera = writer.register_channel(topic="/camera/front/image", message_encoding="json", schema_id=image_schema,
                                     metadata={"frame_id": "camera_front_optical", "timestamp_domain": "carla_simulation_episode"})
    lidar = writer.register_channel(topic="/lidar/roof/points", message_encoding="json", schema_id=cloud_schema,
                                    metadata={"frame_id": "lidar_roof", "axes": "x-forward,y-left,z-up; metres", "source_transform": "CARLA x,y,z -> x,-y,z", "timestamp_domain": "carla_simulation_episode"})
    writer.add_metadata("axiom.capture.provenance", {
        "capture_manifest": _json(metadata).decode(),
        "clock_conversion": "Reported CARLA double seconds rounded to integer nanoseconds; no additional timestamp precision is implied. Original doubles remain in the capture manifest.",
        "sampling": "Only captured frames are present. Gaps are not interpolated.",
        "coordinate_conversion": "LiDAR y was negated into a right-handed local frame. Camera frame_id is optical x-right/y-down/z-forward. Static sensor-to-vehicle declarations remain in the capture manifest.",
    })
    total = 0
    for frame in frames:
        if isinstance(frame.world_frame, bool) or not 0 <= frame.world_frame <= 2**32 - 1:
            raise ValueError("CARLA frame must fit the MCAP sequence field")
        if not frame.png.startswith(b"\x89PNG\r\n\x1a\n") or len(frame.png) > 8 * 1024 * 1024:
            raise ValueError("Captured camera must be a bounded PNG")
        camera_ns, lidar_ns = simulation_timestamp_ns(frame.camera_timestamp_s), simulation_timestamp_ns(frame.lidar_timestamp_s)
        image = _json({"timestamp": _stamp(camera_ns), "frame_id": "camera_front_optical", "format": "png", "data": base64.b64encode(frame.png).decode()})
        cloud = _json({"timestamp": _stamp(lidar_ns), "frame_id": "lidar_roof",
                       "pose": {"position": {"x": 0, "y": 0, "z": 0}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
                       "point_stride": 16, "fields": [{"name": name, "offset": index * 4, "type": 7} for index, name in enumerate(("x", "y", "z", "intensity"))],
                       "data": base64.b64encode(right_handed_xyzi(frame.lidar_xyzi)).decode()})
        total += len(image) + len(cloud)
        if total > 32 * 1024 * 1024:
            raise ValueError("Captured sensor messages exceed the 32 MiB recording bound")
        writer.add_message(channel_id=camera, log_time=camera_ns, publish_time=camera_ns, sequence=frame.world_frame, data=image)
        writer.add_message(channel_id=lidar, log_time=lidar_ns, publish_time=lidar_ns, sequence=frame.world_frame, data=cloud)
    writer.finish()
    return output.getvalue()


# Exact application-owned contract used by stack_replay_sources.supports_ego.
# Kept independent of database/HTTP modules in this optional native package.
EGO_SCHEMA = {"type": "object", "properties": {
    "timestamp_ns": {"type": "string"}, "frame_id": {"type": "string"},
    "speed_mps": {"type": "number"}, "route_heading_error_rad": {"type": "number"},
    "pitch_rad": {"type": "number"}, "roll_rad": {"type": "number"}, "source": {"type": "string"}},
    "required": ["timestamp_ns", "frame_id", "speed_mps", "route_heading_error_rad", "pitch_rad", "roll_rad", "source"],
    "additionalProperties": False}


@dataclass(frozen=True)
class LiveMessage:
    channel_id: int
    world_frame: int
    timestamp_ns: int
    content: bytes

    def reference(self, ordinal: int) -> dict:
        return {"channel_id": self.channel_id, "ordinal": ordinal,
                "message_sha256": hashlib.sha256(self.content).hexdigest(),
                "message_size_bytes": len(self.content), "world_frame": self.world_frame,
                "log_time_ns": str(self.timestamp_ns), "publish_time_ns": str(self.timestamp_ns)}


def live_source_messages(world_frame: int, lidar_timestamp_s: float, raw_xyzi: bytes, ego: dict, *, source_frame_id: str | None = None) -> tuple[LiveMessage, LiveMessage]:
    """Encode once; these exact bytes are both hashed and written to MCAP.

    Nonfinite sensor values remain in the binary message for diagnosis. The
    native bridge withholds them from the kernel; no points are silently dropped.
    """
    if type(world_frame) is not int or not 0 <= world_frame < 2**32:
        raise ValueError("CARLA world frame must fit the MCAP sequence field")
    if len(raw_xyzi) % 16 or len(raw_xyzi) > 20_000 * 16:
        raise ValueError("Full source LiDAR frame exceeds the bounded XYZI layout")
    # Flip only the float32 sign bit: preserve all other raw bits, including
    # NaN payloads and signed zero, in the separately declared RH representation.
    reflected = bytearray(raw_xyzi)
    for offset in range(7, len(reflected), 16):
        reflected[offset] ^= 0x80
    at = simulation_timestamp_ns(lidar_timestamp_s)
    frame_id = _live_frame_id(source_frame_id)
    cloud = _json({"timestamp": _stamp(at), "frame_id": frame_id,
        "pose": {"position": {"x": 0, "y": 0, "z": 0}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
        "point_stride": 16,
        "fields": [{"name": name, "offset": index * 4, "type": 7} for index, name in enumerate(("x", "y", "z", "intensity"))],
        "data": base64.b64encode(reflected).decode()})
    return LiveMessage(1, world_frame, at, cloud), LiveMessage(2, world_frame, int(ego["timestamp_ns"]), _json(ego))


def validate_live_source_prefix(messages: list[LiveMessage]) -> None:
    """Validate the exact message prefix even when its encoded bytes are reused."""
    if len(messages) > 162 or sum(len(message.content) for message in messages) > 18 * 1024 * 1024:
        raise ValueError("Live MCAP source prefix exceeds its preservation budget")
    if any(message.channel_id not in (1, 2) for message in messages):
        raise ValueError("Unregistered live source channel")


def _live_frame_id(source_frame_id):
    if source_frame_id is None:
        return 'lidar_roof'
    if type(source_frame_id) is not str or not source_frame_id.strip() or len(source_frame_id.encode()) > 1024:
        raise ValueError('Configured LiDAR frame ID is invalid or exceeds its bound')
    return source_frame_id


def live_stack_recording(messages: list[LiveMessage], *, metadata: dict, source_frame_id: str | None = None) -> bytes:
    """Finish a valid recording, including an empty or partial source prefix."""
    validate_live_source_prefix(messages)
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD)
    writer.start(profile="json", library="axiom-carla-live-stack/1")
    cloud_schema = writer.register_schema(name="foxglove.PointCloud", encoding="jsonschema", data=_json(POINT_SCHEMA))
    ego_schema = writer.register_schema(name="axiom.EgoState.v1", encoding="jsonschema", data=_json(EGO_SCHEMA))
    frame_id = _live_frame_id(source_frame_id)
    channels = [writer.register_channel(topic="/lidar/roof/points" if source_frame_id is None else '/lidar/configured/points', message_encoding="json", schema_id=cloud_schema,
        metadata={"frame_id": frame_id, "axes": "x-forward,y-left,z-up", "timestamp_domain": "carla_simulation_episode"}),
        writer.register_channel(topic="/ego/state", message_encoding="json", schema_id=ego_schema,
        metadata={"frame_id": "ego", "source": "carla-ego-state-and-map-route/1", "timestamp_domain": "carla_simulation_episode"})]
    if channels != [1, 2]:
        raise ValueError("MCAP channel allocation differs from recorded source references")
    writer.add_metadata("axiom.live-stack.provenance", {
        "context": _json(metadata).decode(),
        "clock_conversion": "Reported CARLA double seconds rounded half-even to integer ns; no additional timestamp precision.",
        "sampling": "Every retained observation is original; a partial prefix has no invented or interpolated messages.",
        "coordinates": "LiDAR y sign reflected into RH sensor frame; static sensor-to-ego transform remains explicitly declared."})
    for message in messages:
        writer.add_message(channel_id=message.channel_id, log_time=message.timestamp_ns,
            publish_time=message.timestamp_ns, sequence=message.world_frame, data=message.content)
    writer.finish()
    return output.getvalue()
