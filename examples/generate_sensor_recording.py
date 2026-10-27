"""Original analytic RGB/point-cloud fixtures in standard MCAP messages.

No external media, simulator, sensor measurements or model predictions are used.
The same synthetic scene drives the camera drawing and sampled LiDAR surfaces.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import struct
from pathlib import Path

from mcap.writer import CompressionType, Writer
from mcap_ros2._dynamic import serialize_dynamic
from PIL import Image, ImageDraw

BASE_NS = 1_700_000_000_000_000_123
STEP_NS = 100_000_000
WIDTH, HEIGHT, FOCAL = 640, 360, 320.0

HEADER_SCHEMA = """
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""
IMAGE_SCHEMA = """std_msgs/Header header
uint32 height
uint32 width
string encoding
uint8 is_bigendian
uint32 step
uint8[] data
""" + HEADER_SCHEMA
POINT_SCHEMA = """std_msgs/Header header
uint32 height
uint32 width
sensor_msgs/PointField[] fields
bool is_bigendian
uint32 point_step
uint32 row_step
uint8[] data
bool is_dense
""" + HEADER_SCHEMA + """
================================================================================
MSG: sensor_msgs/PointField
uint8 INT8=1
uint8 UINT8=2
uint8 INT16=3
uint8 UINT16=4
uint8 INT32=5
uint8 UINT32=6
uint8 FLOAT32=7
uint8 FLOAT64=8
string name
uint32 offset
uint8 datatype
uint32 count
"""


def scene(index: int) -> tuple[bytes, bytes]:
    # Ego frame: x forward, y left, z up. The analytic box moves across the road.
    box_x, box_y = 21 - index * .25, 2.2 * math.sin(index * .13)
    image = Image.new('RGB', (WIDTH, HEIGHT), '#a0bfd2')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, HEIGHT // 2, WIDTH, HEIGHT), fill='#263a48')
    points = []

    def project(x, y, z):
        distance = x - 1.5
        return (WIDTH / 2 - FOCAL * y / distance, HEIGHT / 2 - FOCAL * (z - 1.7) / distance) if distance > .2 else None

    def point(x, y, z, intensity):
        # Point data are in the roof sensor's own frame, not ego coordinates.
        points.append((x, y, z - 2.2, intensity))

    for x_index in range(3, 151):
        x = x_index * .4
        for y_index in range(-24, 25):
            y = y_index * .3
            point(x, y, 0, .85 if abs(abs(y) - 1.8) < .1 else .2)
    for side in (-1, 1):
        for x_index in range(20, 151):
            x = x_index * .4
            for z_index in range(1, 27):
                point(x, side * 8.5, z_index * .3, .5)
        polygon = [project(x, side * 8.5, z) for x, z in [(60, 0), (60, 8), (8, 8), (8, 0)]]
        draw.polygon(polygon, fill='#456879' if side == -1 else '#527987')
    for y in (-1.8, 1.8):
        for x in range(4, 60, 5):
            draw.line([project(x, y, .01), project(x + 2, y, .01)], fill='#eee4aa', width=2)
    for face_x in (box_x - 1.7, box_x + 1.7):
        for yi in range(31):
            for zi in range(25):
                point(face_x, box_y - .9 + yi * .06, zi * .07, .9)
    for side_y in (box_y - .9, box_y + .9):
        for xi in range(51):
            for zi in range(25):
                point(box_x - 1.7 + xi * .068, side_y, zi * .07, .7)
    front = [project(box_x - 1.7, box_y + y, z) for y, z in [(-.9, 0), (.9, 0), (.9, 1.7), (-.9, 1.7)]]
    draw.polygon(front, fill='#ef9148', outline='#ffce84', width=2)
    draw.rectangle((12, 12, 336, 51), fill='#122b38')
    draw.text((22, 20), 'SYNTHETIC SENSOR REPLAY', fill='#f5f1dd')
    draw.text((22, 34), f'Analytic fixture / frame {index:02d}', fill='#8eddb7')
    cloud = b''.join(struct.pack('<ffff', *point) for point in points)
    return image.tobytes(), cloud


def generate(destination: Path, frames: int = 31) -> dict:
    if not 1 <= frames <= 61:
        raise ValueError('Use 1..61 frames for this bounded example')
    image_encoder = serialize_dynamic('sensor_msgs/msg/Image', IMAGE_SCHEMA)['sensor_msgs/msg/Image']
    cloud_encoder = serialize_dynamic('sensor_msgs/msg/PointCloud2', POINT_SCHEMA)['sensor_msgs/msg/PointCloud2']
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        writer = Writer(stream, compression=CompressionType.ZSTD, enable_data_crcs=True)
        writer.start(profile='', library='axiom-original-sensor-fixture/1')
        image_schema = writer.register_schema('sensor_msgs/msg/Image', 'ros2msg', IMAGE_SCHEMA.encode())
        cloud_schema = writer.register_schema('sensor_msgs/msg/PointCloud2', 'ros2msg', POINT_SCHEMA.encode())
        png_schema = writer.register_schema('foxglove.CompressedImage', 'jsonschema', b'{"type":"object","properties":{"timestamp":{"type":"object"},"frame_id":{"type":"string"},"data":{"type":"string","contentEncoding":"base64"},"format":{"type":"string"}}}')
        camera = writer.register_channel('/camera/front/image_raw', 'cdr', image_schema, {'clock': 'synthetic capture clock + 3 ms log delay'})
        lidar = writer.register_channel('/lidar/roof/points', 'cdr', cloud_schema, {'axes': 'x-forward,y-left,z-up', 'clock': 'synthetic capture clock + 5 ms log delay'})
        png_channel = writer.register_channel('/camera/front/compressed', 'json', png_schema)
        writer.add_metadata('axiom.synthetic.fixture', {
            'source_kind': 'synthetic', 'source': 'Original analytic scene and authored sensor bytes; no CARLA or measured sensor data',
            'image_model': 'Pinhole drawing, no noise/optics/rolling shutter simulation',
            'lidar_model': 'Sampled analytic surfaces; no occlusion/ray-casting/intensity physics',
            'clock_basis': 'header capture time; publish +1 ms; MCAP log camera +3 ms, lidar +5 ms, compressed +3 ms',
        })
        point_count = 0
        for index in range(frames):
            time_ns = BASE_NS + index * STEP_NS
            seconds, nanos = divmod(time_ns, 1_000_000_000)
            rgb, raw_cloud = scene(index)
            point_count = len(raw_cloud) // 16
            camera_payload = image_encoder({'header': {'stamp': {'sec': seconds, 'nanosec': nanos}, 'frame_id': 'camera_front_optical'},
                                             'height': HEIGHT, 'width': WIDTH, 'encoding': 'rgb8', 'is_bigendian': 0,
                                             'step': WIDTH * 3, 'data': rgb})
            cloud_payload = cloud_encoder({'header': {'stamp': {'sec': seconds, 'nanosec': nanos}, 'frame_id': 'lidar_roof'},
                                            'height': 1, 'width': point_count,
                                            'fields': [{'name': name, 'offset': offset * 4, 'datatype': 7, 'count': 1} for offset, name in enumerate(('x', 'y', 'z', 'intensity'))],
                                            'is_bigendian': False, 'point_step': 16, 'row_step': len(raw_cloud), 'data': raw_cloud, 'is_dense': True})
            writer.add_message(camera, time_ns + 3_000_000, camera_payload, time_ns + 1_000_000, index)
            writer.add_message(lidar, time_ns + 5_000_000, cloud_payload, time_ns + 1_000_000, index)
            # An alternate standard encoding, deliberately starting later. A
            # cursor before frame 3 must not display a future image as current.
            if index >= 3:
                image = Image.frombytes('RGB', (WIDTH, HEIGHT), rgb)
                png = io.BytesIO()
                image.save(png, format='PNG')
                payload = json.dumps({'timestamp': {'sec': seconds, 'nsec': nanos}, 'frame_id': 'camera_front_optical', 'format': 'png',
                                      'data': base64.b64encode(png.getvalue()).decode()}, separators=(',', ':')).encode()
                writer.add_message(png_channel, time_ns + 3_000_000, payload, time_ns + 1_000_000, index)
        writer.finish()
    return {'path': str(destination.resolve()), 'base_ns': str(BASE_NS), 'frames': frames, 'channels': {'camera': camera, 'lidar': lidar, 'compressed': png_channel},
            'point_count': point_count, 'width': WIDTH, 'height': HEIGHT,
            'source_kind': 'synthetic', 'clock_note': 'Capture clock maps from camera log -3 ms and LiDAR log -5 ms by construction only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--frames', type=int, default=31)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.destination, arguments.frames), indent=2))
