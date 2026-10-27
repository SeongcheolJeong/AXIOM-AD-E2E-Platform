"""Actual kernel2 calculations over authored points/speed; no vehicle or CARLA.

The browser wraps these records in an explicitly authored native-envelope fixture.
This checks rendering/reader contracts, not algorithm or vehicle calibration.
"""
import base64
import hashlib
import json
import struct
import sys

from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.stack_common_schemas import StackPacket


def generate(case):
    registry = StackRegistry(load_plugins=False)
    metadata = [item.model_dump(mode='json') for item in registry.list()
                if item.implementation.get('kernel') == 'stack-kernel/2']
    definition = {'schema_version': '2', 'kernel_version': 'stack-kernel/2', 'reference_frame': 'ego'}
    for role in ('perception', 'policy', 'controller'):
        items = [item for item in metadata if item['role'] == role and item['version'] == '2']
        assert len(items) == 1
        item = items[0]
        definition[role] = {'id': item['id'], 'version': item['version'], 'parameters': dict(item['default_parameters'])}
    definition['perception']['parameters'].update(ground_reference_z_m=0., ground_reference_source='Authored fixture flat plane, not measured')
    if case != 'unconfigured-brake':
        definition['controller']['parameters'].update(assumed_full_brake_deceleration_mps2=8., brake_response_source='Authored browser model assumption, not measured')
    definition['policy']['parameters']['allow_no_obstacle_observed_motion'] = True
    if case == 'gap':
        points = [(5., 0., .7), (12., .6, 0.), (12.125, .7, .01)]
    else:
        points = []
        for row in range(13):
            low = 2.5 + row
            for y in (-.8, 0., .8):
                points.extend([(low + .125, y, 0.), (low + .25, y + .05, .01)])
        points.append((7.5, 0., .7))
    raw = b''.join(struct.pack('<fff', *point) for point in points)
    points = list(struct.iter_unpack('<fff', raw))
    xyzi = b''.join(struct.pack('<ffff', x, -y, z - 2.2, 1.) for x, y, z in points)
    digest = hashlib.sha256(raw).hexdigest()
    session = StackSession(definition, registry)
    ticks = []
    for sequence in range(40):
        at = str(5_000_000_000 + sequence * 50_000_000)
        # Authored external speed samples; these are not a simulated plant response.
        t = max(0., sequence * .05 - .1)
        speed = 2. if case == 'gap' else max(0., 2. - t*t if t < 1 else (2-t)**2)
        packet = StackPacket.model_validate({'sequence': sequence, 'at_ns': at, 'unavailable_reasons': [],
            'ego': {'timestamp_ns': at, 'capture_time_ns': at, 'frame_id': 'ego', 'speed_mps': speed,
                    'route_heading_error_rad': 0., 'pitch_rad': 0., 'roll_rad': 0., 'source': 'Authored browser speed samples, not vehicle physics'},
            'lidar': {'timestamp_ns': at, 'capture_time_ns': at, 'frame_id': 'ego', 'point_count': len(points),
                      'points_sha256': digest, 'size_bytes': len(raw), 'source_message_sha256': 'b'*64,
                      'complete_frame': True, 'coordinate_convention': 'x_forward_y_left_z_up', 'encoding': 'xyz_float32_le'}})
        output = session.step(packet, raw)
        ticks.append({'packet': packet.model_dump(mode='json'), 'output': output.model_dump(mode='json')})
    return {'definition': definition, 'metadata': metadata, 'ticks': ticks,
            'points_base64': base64.b64encode(raw).decode(), 'raw_xyzi_base64': base64.b64encode(xyzi).decode(),
            'scope': 'Actual pure kernel calculation on authored points and scripted speed; no CARLA or vehicle acceptance'}


if __name__ == '__main__':
    print(json.dumps(generate(sys.argv[1]), allow_nan=False))
