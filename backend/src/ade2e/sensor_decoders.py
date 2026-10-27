"""Bounded standard sensor payload decoders, independent of MCAP storage and rigs.

ROS2 input is limited to the exact Image/CompressedImage/PointCloud2 ros2msg
layouts and CDR1. No code is generated from an uploaded schema. Foxglove JSON
uses the public schemas at https://github.com/foxglove/schemas. Binary previews
are derived observations, with no inference of calibration or clock domain.
"""

import base64
import binascii
import hashlib
import io
import json
import math
import re
import struct
import warnings
import zlib
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from .sensor_decoder_schemas import CameraPreview, PointCloudPreview, SensorDecodeResult, SensorDecoderMetadata, SensorPreviewBytes

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 2_000_000
MAX_SOURCE_POINTS = 1_000_000
MAX_SCHEMA_BYTES = 64 * 1024
MAX_FIELDS = 64
DECODER_VERSION = "1"


class SensorDecodeError(ValueError):
    def __init__(self, message: str, code: str = "invalid_payload"):
        super().__init__(message)
        self.code = code


def _check(test, message, code="invalid_payload"):
    if not test:
        raise SensorDecodeError(message, code)


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _integer(value, name, low=0, high=2**32 - 1):
    _check(type(value) is int and low <= value <= high, f"Invalid {name}")
    return value


def _text(value, name, max_length=1024):
    _check(isinstance(value, str) and len(value.encode("utf-8")) <= max_length and "\0" not in value, f"Invalid {name}")
    return value


def _no_constant(value):
    raise SensorDecodeError(f"Non-finite JSON value: {value}")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(data):
    # Bound nesting before json.loads: a byte cap alone permits huge Python trees.
    depth, tokens, in_string, escaped = 0, 0, False, False
    for char in data:
        if in_string:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                in_string = False
        elif char == 34:
            in_string = True
        elif char in (91, 123):
            depth += 1
            _check(depth <= 16, "JSON nesting exceeds 16", "limit_exceeded")
        elif char in (93, 125):
            depth -= 1
        if not in_string and char in (44, 58, 91, 123):
            tokens += 1
            _check(tokens <= 4096, "JSON structure exceeds 4096 tokens", "limit_exceeded")
    value = json.loads(data, parse_constant=_no_constant, object_pairs_hook=_pairs)
    _check(isinstance(value, dict), "Expected a JSON object")
    return value


def _base64(value):
    _check(isinstance(value, str), "Binary data must be base64 text")
    _check(len(value) <= ((MAX_INPUT_BYTES + 2) // 3) * 4, "Base64 data exceeds limit", "limit_exceeded")
    raw = base64.b64decode(value, validate=True)
    _check(len(raw) <= MAX_INPUT_BYTES, "Binary data exceeds limit", "limit_exceeded")
    return raw


def _binary(data):
    return dict(data_base64=base64.b64encode(data).decode("ascii"), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def _timestamp(value, *, ros=False):
    _check(isinstance(value, dict), "Missing sensor timestamp")
    sec = _integer(value.get("sec"), "timestamp seconds", -(2**31) if ros else 0, 2**31 - 1 if ros else 2**32 - 1)
    nsec = _integer(value.get("nanosec" if ros else "nsec"), "timestamp nanoseconds", 0, 999_999_999)
    return str(sec * 1_000_000_000 + nsec)


def _dimensions(width, height):
    width, height = _integer(width, "width", 1, MAX_PIXELS), _integer(height, "height", 1, MAX_PIXELS)
    _check(width * height <= MAX_PIXELS, "Image exceeds 2 million pixels", "limit_exceeded")
    return width, height


def _png(image):
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return CameraPreview(**_binary(output.getvalue()), width=image.width, height=image.height)


def _compressed(data, format_name, *, validate_dimensions=None):
    _check(format_name in ("png", "jpeg"), "Only PNG and JPEG compressed images are supported", "unsupported_schema")
    if format_name == "png":
        # Drop metadata before Pillow can inflate zTXt/iCCP; check all source CRCs.
        _check(data.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG signature")
        sanitized, offset, ended, chunk_count = bytearray(data[:8]), 8, False, 0
        while offset < len(data):
            _check(len(data)-offset >= 12, "Truncated PNG chunk")
            size = struct.unpack_from(">I",data,offset)[0]
            _check(size <= len(data)-offset-12, "Truncated PNG chunk body")
            kind, body = data[offset+4:offset+8], data[offset+8:offset+8+size]
            _check(zlib.crc32(kind+body) & 0xffffffff == struct.unpack_from(">I",data,offset+8+size)[0], "PNG chunk CRC mismatch")
            _check(kind not in (b"acTL",b"fcTL",b"fdAT"), "Animated PNG is unsupported", "unsupported_schema")
            _check(chunk_count > 0 or kind == b"IHDR", "PNG must begin with IHDR")
            _check(kind in (b"IHDR",b"PLTE",b"IDAT",b"IEND") or kind[0] & 32, "Unknown critical PNG chunk")
            if kind in (b"IHDR",b"PLTE",b"IDAT",b"IEND",b"tRNS"):
                sanitized.extend(data[offset:offset+12+size])
            offset += size+12
            chunk_count += 1
            _check(chunk_count <= 4096, "PNG has too many chunks", "limit_exceeded")
            if kind == b"IEND":
                _check(size == 0 and offset == len(data), "Unexpected PNG IEND or trailing data")
                ended = True
                break
        _check(ended, "PNG has no IEND")
        data = bytes(sanitized)
    # Pillow remains lazy until load; inspect dimensions before decompression.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as probe:
            _check(probe.format == format_name.upper(), "Declared image format does not match bytes")
            _dimensions(probe.width, probe.height)
            _check(getattr(probe, "n_frames", 1) == 1, "Animated images are not supported", "unsupported_schema")
            _check(probe.mode in ("RGB", "RGBA", "L", "LA", "P"), "Unsupported compressed pixel mode", "unsupported_schema")
            if validate_dimensions is not None:
                _check(probe.mode == "RGB" and probe.size == validate_dimensions, "Preview must be RGB8 PNG with the declared dimensions")
            probe.verify()
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            image.load()
            return None if validate_dimensions is not None else _png(image)


def validate_png_preview(data: bytes, width: int, height: int) -> None:
    """Verify plugin PNG framing/CRCs, RGB8 dimensions and actual pixel decoding.

    This reads the image with the same bounds as the built-in input decoder but
    does not re-encode it or replace the plugin's original preview bytes/hash.
    """
    try:
        _check(isinstance(data, bytes) and len(data) <= MAX_INPUT_BYTES, "PNG preview exceeds its byte bound", "limit_exceeded")
        dimensions = _dimensions(width, height)
        _compressed(data, "png", validate_dimensions=dimensions)
    except SensorDecodeError:
        raise
    except (ValueError, TypeError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, struct.error) as exc:
        raise SensorDecodeError(f"Invalid PNG preview: {exc}") from exc


def _raw_image(message, data):
    width, height = _dimensions(message.get("width"), message.get("height"))
    encoding = message.get("encoding")
    modes = {"rgb8": ("RGB", "RGB", 3), "bgr8": ("RGB", "BGR", 3), "rgba8": ("RGBA", "RGBA", 4), "bgra8": ("RGBA", "BGRA", 4), "mono8": ("L", "L", 1), "8UC1": ("L", "L", 1), "8UC3": ("RGB", "BGR", 3)}
    _check(encoding in modes, "Raw image encoding is unsupported; no Bayer/depth/YUV conversion", "unsupported_schema")
    mode, raw_mode, channels = modes[encoding]
    step = _integer(message.get("step"), "image row step", width * channels, MAX_INPUT_BYTES)
    _check(step * height == len(data), "Image bytes do not equal step * height")
    return _png(Image.frombytes(mode, (width, height), data, "raw", raw_mode, step, 1))


ROS_LAYOUTS = {
    "sensor_msgs/Image": ["std_msgs/Header header", "uint32 height", "uint32 width", "string encoding", "uint8 is_bigendian", "uint32 step", "uint8[] data"],
    "sensor_msgs/CompressedImage": ["std_msgs/Header header", "string format", "uint8[] data"],
    "sensor_msgs/PointCloud2": ["std_msgs/Header header", "uint32 height", "uint32 width", "sensor_msgs/PointField[] fields", "bool is_bigendian", "uint32 point_step", "uint32 row_step", "uint8[] data", "bool is_dense"],
    "std_msgs/Header": ["builtin_interfaces/Time stamp", "string frame_id"],
    "builtin_interfaces/Time": ["int32 sec", "uint32 nanosec"],
    "sensor_msgs/PointField": ["string name", "uint32 offset", "uint8 datatype", "uint32 count"],
}
_POINT_CONSTANTS = {f"uint8 {name}={number}" for number, name in enumerate(("INT8", "UINT8", "INT16", "UINT16", "INT32", "UINT32", "FLOAT32", "FLOAT64"), 1)}


def _ros_name(name):
    return name.replace("/msg/", "/")


def _ros_schema(name, definition):
    name = _ros_name(name)
    expected_names = {name, "std_msgs/Header", "builtin_interfaces/Time"}
    if name == "sensor_msgs/PointCloud2":
        expected_names.add("sensor_msgs/PointField")
    blocks, current = {name: []}, name
    for line in definition.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or re.fullmatch(r"=+", line):
            continue
        if line.startswith("MSG:"):
            current = _ros_name(line[4:].strip())
            _check(current not in blocks, "Duplicate ROS message definition", "unsupported_schema")
            blocks[current] = []
            continue
        line = " ".join(line.split())
        parts = line.split(" ", 1)
        _check(len(parts) == 2, "Invalid ROS field declaration", "unsupported_schema")
        field_type, field_name = parts
        field_type = _ros_name(field_type)
        if field_type == "Header":
            field_type = "std_msgs/Header"
        elif field_type == "PointField[]":
            field_type = "sensor_msgs/PointField[]"
        line = field_type + " " + re.sub(r"\s*=\s*", "=", field_name)
        if "=" in line:
            _check(current == "sensor_msgs/PointField" and line in _POINT_CONSTANTS, "Unexpected ROS constant", "unsupported_schema")
        else:
            blocks[current].append(line)
    _check(set(blocks) == expected_names, "ROS schema must include exactly the supported standard dependencies", "unsupported_schema")
    for block, fields in blocks.items():
        _check(fields == ROS_LAYOUTS[block], f"Nonstandard ROS layout for {block}", "unsupported_schema")


class _CDR:
    """Fixed CDR1 cursor: validate all lengths before slicing or looping."""
    def __init__(self, data):
        _check(len(data) >= 4 and data[:2] in (b"\0\0", b"\0\1") and data[2:4] == b"\0\0", "Only plain CDR1 BE/LE with zero options is supported", "unsupported_schema")
        self.data, self.offset, self.endian = data, 4, "<" if data[1] else ">"

    def read(self, count):
        _check(count <= len(self.data) - self.offset, "Truncated CDR payload")
        result = self.data[self.offset:self.offset + count]
        self.offset += count
        return result

    def number(self, fmt):
        size = struct.calcsize(fmt)
        self.read((-(self.offset - 4)) % size)
        return struct.unpack(self.endian + fmt, self.read(size))[0]

    def string(self):
        count = self.number("I")
        _check(1 <= count <= 1025, "CDR string length exceeds limit", "limit_exceeded")
        raw = self.read(count)
        _check(raw[-1] == 0 and b"\0" not in raw[:-1], "Invalid CDR string termination")
        return raw[:-1].decode("utf-8")

    def blob(self):
        count = self.number("I")
        _check(count <= MAX_INPUT_BYTES, "CDR byte sequence exceeds limit", "limit_exceeded")
        return self.read(count)

    def boolean(self):
        value = self.number("B")
        _check(value in (0, 1), "Invalid CDR boolean")
        return bool(value)

    def finish(self):
        # DDS writers may append up to three zero padding bytes.
        tail = self.data[self.offset:]
        _check(len(tail) <= 3 and not any(tail), "Unexpected trailing CDR payload")


def _read_cdr(data, name):
    cursor = _CDR(data)
    timestamp = {"sec": cursor.number("i"), "nanosec": cursor.number("I")}
    result = {"frame_id": cursor.string(), "timestamp": timestamp}
    if name.endswith("/CompressedImage"):
        result.update(format=cursor.string(), data=cursor.blob())
    elif name.endswith("/Image"):
        result.update(height=cursor.number("I"), width=cursor.number("I"), encoding=cursor.string(), is_bigendian=cursor.number("B"), step=cursor.number("I"), data=cursor.blob())
        _check(result["is_bigendian"] in (0, 1), "Invalid image is_bigendian flag")
    else:
        result.update(height=cursor.number("I"), width=cursor.number("I"))
        count = cursor.number("I")
        _check(count <= MAX_FIELDS, "Too many PointCloud2 fields", "limit_exceeded")
        result["fields"] = [dict(name=cursor.string(), offset=cursor.number("I"), datatype=cursor.number("B"), count=cursor.number("I")) for _ in range(count)]
        result.update(is_bigendian=cursor.boolean(), point_step=cursor.number("I"), row_step=cursor.number("I"), data=cursor.blob(), is_dense=cursor.boolean())
    cursor.finish()
    return result


def _pose(value):
    _check(isinstance(value, dict) and set(value) == {"position", "orientation"}, "PointCloud pose is required")
    for field, components in (("position", "xyz"), ("orientation", "xyzw")):
        _check(isinstance(value[field], dict) and set(value[field]) == set(components), "Invalid point cloud pose")
        _check(all(type(value[field][key]) in (int, float) and math.isfinite(value[field][key]) for key in components), "Non-finite point cloud pose")
    q = tuple(value["orientation"][key] for key in "xyzw")
    _check(math.isclose(sum(v*v for v in q), 1.0, abs_tol=1e-6), "PointCloud pose quaternion must have unit length")
    return value


def _apply_pose(point, pose):
    if pose is None:
        return point
    x, y, z = point
    qx, qy, qz, qw = (pose["orientation"][key] for key in "xyzw")
    tx, ty, tz = 2*(qy*z-qz*y), 2*(qz*x-qx*z), 2*(qx*y-qy*x)
    return (x+qw*tx+qy*tz-qz*ty+pose["position"]["x"], y+qw*ty+qz*tx-qx*tz+pose["position"]["y"], z+qw*tz+qx*ty-qy*tx+pose["position"]["z"])


def _point_cloud(message, data, max_points, *, ros):
    stride = _integer(message.get("point_step" if ros else "point_stride"), "point stride", 1, MAX_INPUT_BYTES)
    if ros:
        width, height = _integer(message.get("width"), "cloud width", 0, MAX_SOURCE_POINTS), _integer(message.get("height"), "cloud height", 0, MAX_SOURCE_POINTS)
        row_step = _integer(message.get("row_step"), "cloud row step", width * stride, MAX_INPUT_BYTES)
        _check(row_step * height == len(data), "PointCloud2 data does not equal row_step * height")
        endian = ">" if message["is_bigendian"] else "<"
        pose = None
    else:
        _check(len(data) % stride == 0, "Point data is not divisible by point_stride")
        width, height, row_step, endian = len(data) // stride, 1, len(data), "<"
        pose = _pose(message.get("pose"))
    count = width * height
    _check(count <= MAX_SOURCE_POINTS, "Point cloud exceeds 1 million points", "limit_exceeded")
    fields = message.get("fields")
    _check(isinstance(fields, list) and 1 <= len(fields) <= MAX_FIELDS, "Invalid point fields")
    types = {1:"b", 2:"B", 3:"h", 4:"H", 5:"i", 6:"I", 7:"f", 8:"d"} if ros else {1:"B", 2:"b", 3:"H", 4:"h", 5:"I", 6:"i", 7:"f", 8:"d"}
    readers = {}
    for field in fields:
        _check(isinstance(field, dict), "Invalid point field")
        name = _text(field.get("name"), "point field name")
        _check(name not in readers, "Duplicate point field name")
        datatype = _integer(field.get("datatype" if ros else "type"), "point datatype", 1, 8)
        offset = _integer(field.get("offset"), "point offset", 0, stride)
        field_count = _integer(field.get("count", 1), "point field count", 1, MAX_INPUT_BYTES)
        size = struct.calcsize(types[datatype])
        _check(offset + size * field_count <= stride, "Point field extends past its stride")
        readers[name] = (struct.Struct(endian + types[datatype]), offset, field_count)
    _check(all(axis in readers and readers[axis][2] == 1 and readers[axis][0].format[-1] in "fd" for axis in "xyz"), "XYZ scalar FLOAT32/FLOAT64 fields are required", "unsupported_schema")
    if "intensity" in readers:
        _check(readers["intensity"][2] == 1, "Intensity must be scalar", "unsupported_schema")
    sampling_stride = max(1, math.ceil(count / max_points))
    packed, intensities, invalid, skipped, intensity_invalid = bytearray(), bytearray(), 0, 0, 0
    bounds_min, bounds_max = None, None
    for index in range(count):
        base = (index // width) * row_step + (index % width) * stride
        point = _apply_pose(tuple(readers[axis][0].unpack_from(data, base + readers[axis][1])[0] for axis in "xyz"), pose)
        if not all(math.isfinite(value) and abs(value) <= 3.4028234663852886e38 for value in point):
            invalid += 1
            continue
        if index % sampling_stride:
            skipped += 1
            continue
        point_bytes = struct.pack("<fff", *point)
        point = struct.unpack("<fff", point_bytes)
        packed.extend(point_bytes)
        bounds_min = list(point) if bounds_min is None else [min(a,b) for a,b in zip(bounds_min,point)]
        bounds_max = list(point) if bounds_max is None else [max(a,b) for a,b in zip(bounds_max,point)]
        if "intensity" in readers:
            reader, offset, _ = readers["intensity"]
            value = reader.unpack_from(data, base + offset)[0]
            if math.isfinite(value) and abs(value) <= 3.4028234663852886e38:
                intensities.extend(struct.pack("<f", value))
            else:
                intensity_invalid += 1
    intensity = SensorPreviewBytes(**_binary(bytes(intensities))) if "intensity" in readers and not intensity_invalid else None
    preview = PointCloudPreview(**_binary(bytes(packed)), point_count=len(packed)//12, original_point_count=count, invalid_points=invalid, skipped_points=skipped, sampling_stride=sampling_stride, bounds_min=bounds_min, bounds_max=bounds_max, intensity=intensity, intensity_encoding="float32_le" if intensity is not None else None, intensity_invalid_points=intensity_invalid)
    notes = ["Coordinates retain the source frame convention; rig/clock/calibration are not inferred."]
    if intensity_invalid:
        notes.append("Intensity preview omitted because selected points include non-finite or float32-overflow intensity.")
    if pose is not None:
        notes.append("PointCloud.pose applied to XYZ to express points in message frame_id; rig transform is not applied.")
    return preview, pose, notes


class StandardSensorDecoder:
    def __init__(self, decoder_id):
        self.id = decoder_id
        self.ros = decoder_id == "ros2-cdr"

    def metadata(self):
        names = ["sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage", "sensor_msgs/msg/PointCloud2"] if self.ros else ["foxglove.RawImage", "foxglove.CompressedImage", "foxglove.PointCloud"]
        return SensorDecoderMetadata(id=self.id, version=DECODER_VERSION, name="Bounded ROS2 CDR1 sensor decoder" if self.ros else "Foxglove JSON sensor decoder", message_encoding="cdr" if self.ros else "json", schema_names=names, kinds=["camera", "lidar"])

    def _schema(self, channel, schema):
        metadata = self.metadata()
        _check(schema is not None and _get(channel,"message_encoding") == metadata.message_encoding and _get(channel,"schema_id") == _get(schema,"id"), "Channel/schema encoding mismatch", "unsupported_schema")
        name = _get(schema,"name")
        _check(name in metadata.schema_names, "Unsupported sensor schema name", "unsupported_schema")
        definition = _get(schema,"definition")
        _check(isinstance(definition,str) and not _get(schema,"definition_truncated") and _get(schema,"size_bytes",MAX_SCHEMA_BYTES+1) <= MAX_SCHEMA_BYTES, "A complete sensor schema up to 64KiB is required", "unsupported_schema")
        raw = definition.encode("utf-8")
        _check(len(raw) == _get(schema,"size_bytes") and hashlib.sha256(raw).hexdigest() == _get(schema,"sha256"), "Sensor schema integrity mismatch", "unsupported_schema")
        if self.ros:
            _check(_get(schema,"encoding") == "ros2msg", "Only ROS2 ros2msg schemas are supported", "unsupported_schema")
            _ros_schema(name, definition)
        else:
            _check(_get(schema,"encoding") == "jsonschema", "Foxglove JSON requires jsonschema", "unsupported_schema")
            document = _json(raw)
            _check(document.get("type") == "object" and document.get("title", name) == name, "Foxglove schema title/type mismatch", "unsupported_schema")
            properties = document.get("properties")
            expected = {"timestamp": "object", "frame_id": "string", "data": "string"}
            expected.update({"pose": "object", "point_stride": "integer", "fields": "array"} if name.endswith("PointCloud") else {"format": "string"} if name.endswith("CompressedImage") else {"width": "integer", "height": "integer", "encoding": "string", "step": "integer"})
            _check(isinstance(properties, dict) and all(isinstance(properties.get(key), dict) and properties[key].get("type") == kind for key, kind in expected.items()), "Foxglove schema fields do not match the supported message", "unsupported_schema")
            _check(properties["data"].get("contentEncoding") == "base64", "Foxglove binary fields must declare base64", "unsupported_schema")
        return name

    def accepts(self, channel, schema):
        try:
            self._schema(channel, schema)
            return True
        except (SensorDecodeError, ValueError, TypeError, KeyError, RecursionError):
            return False

    def kind_for(self, channel, schema):
        return ("lidar" if "PointCloud" in _get(schema,"name","") else "camera") if self.accepts(channel,schema) else None

    def decode(self, payload, channel, schema, max_points=20_000):
        try:
            _check(isinstance(payload,bytes), "Sensor payload must be bytes")
            _check(len(payload) <= MAX_INPUT_BYTES, "Sensor payload exceeds 16MiB", "limit_exceeded")
            _integer(max_points, "max_points", 1, 100_000)
            name = self._schema(channel,schema)
            message = _read_cdr(payload,name) if self.ros else _json(payload)
            frame_id = _text(message.get("frame_id"), "frame_id") or None
            capture = _timestamp(message.get("timestamp"), ros=self.ros)
            data = message["data"] if self.ros else _base64(message.get("data"))
            common = dict(decoder_id=self.id,decoder_version=DECODER_VERSION,frame_id=frame_id,capture_time_ns=capture,source_sha256=hashlib.sha256(payload).hexdigest(),source_size_bytes=len(payload))
            if "PointCloud" in name:
                preview,pose,notes = _point_cloud(message,data,max_points,ros=self.ros)
                return SensorDecodeResult(**common,kind="lidar",original_encoding="PointCloud2" if self.ros else "foxglove.PointCloud",coordinate_convention="source_declared",units="m",point_cloud=preview,source_pose=pose,transform_policy="message_pose_to_frame" if pose is not None else "preserve_native_coordinates",warnings=notes)
            if "CompressedImage" in name:
                encoding = _text(message.get("format"), "compressed format")
                if self.ros:
                    # ROS compressed_image_transport standard: ORIG; CODEC compressed [PIXEL].
                    match = re.fullmatch(r"(?:[A-Za-z0-9_]+;\s*)?(png|jpeg)(?: compressed(?: [A-Za-z0-9_]+)?)?",encoding)
                    _check(match is not None, "Unsupported ROS compressed image format", "unsupported_schema")
                    format_name = match.group(1)
                else:
                    format_name = encoding
                preview = _compressed(data,format_name)
            else:
                encoding = _text(message.get("encoding"), "raw encoding")
                _check(not self.ros or encoding != "8UC3", "ROS 8UC3 does not declare RGB/BGR color order; use named rgb8 or bgr8", "unsupported_schema")
                preview = _raw_image(message,data)
            return SensorDecodeResult(**common,kind="camera",original_encoding=encoding,coordinate_convention="camera_optical",units="pixels",camera=preview,warnings=["RGB preview drops alpha and preserves raster orientation; no EXIF rotation, color calibration, or camera model is applied."])
        except SensorDecodeError:
            raise
        except (ValueError,TypeError,KeyError,OverflowError,UnicodeError,struct.error,binascii.Error,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning,RecursionError) as exc:
            raise SensorDecodeError(f"Invalid sensor payload: {exc}") from exc


class SensorDecoder(Protocol):
    def metadata(self) -> SensorDecoderMetadata: ...
    def accepts(self, channel, schema) -> bool: ...
    def kind_for(self, channel, schema) -> str | None: ...
    def decode(self, payload: bytes, channel, schema, max_points: int = 20_000) -> SensorDecodeResult: ...


class SensorDecoderRegistry:
    def __init__(self):
        self._adapters = {key:StandardSensorDecoder(key) for key in ("foxglove-json", "ros2-cdr")}

    def list(self):
        return [adapter.metadata() for adapter in self._adapters.values()]

    def register(self, adapter: SensorDecoder, *, replace=False):
        metadata = SensorDecoderMetadata.model_validate(adapter.metadata())
        if metadata.id in self._adapters and not replace:
            raise ValueError(f"Sensor decoder {metadata.id} is already registered")
        self._adapters[metadata.id] = adapter

    def get(self, decoder_id, version=None):
        adapter = self._adapters[decoder_id]
        if version not in (None, adapter.metadata().version):
            raise KeyError(f"Sensor decoder {decoder_id}@{version} is not installed")
        return adapter
