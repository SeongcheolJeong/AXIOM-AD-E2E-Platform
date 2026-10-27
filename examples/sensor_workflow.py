"""Import real standard message bytes, pin a rig and inspect exact sensor frames.

The authored source is an analytic fixture, not a real sensor or CARLA run.
Requires the backend[test] environment for the independent ROS2 fixture writer.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
from copy import deepcopy
from pathlib import Path

from PIL import Image
from axiom_sdk import AxiomClient

from generate_sensor_recording import BASE_NS, FOCAL, HEIGHT, STEP_NS, WIDTH, generate


def rig_definition():
    return {
        'name': 'Synthetic vehicle · front camera and roof LiDAR', 'description': 'Analytic replay fixture with declared static transforms',
        'reference_frame': 'ego', 'coordinate_convention': 'right_handed_z_up', 'source_kind': 'synthetic',
        'source': 'examples/generate_sensor_recording.py; authored scene, pixels and sampled surfaces',
        'note': 'Known by fixture construction only. No measured calibration or validated sensor physics.',
        'sensors': [
            {'key': 'camera_front', 'name': 'Front camera', 'kind': 'camera', 'frame_id': 'camera_front_optical',
             'transform': {'translation_m': [1.5, 0, 1.7], 'rotation_xyzw': [-.5, .5, -.5, .5]},
             'intrinsics': {'width': WIDTH, 'height': HEIGHT, 'fx': FOCAL, 'fy': FOCAL, 'cx': WIDTH / 2, 'cy': HEIGHT / 2,
                            'distortion_model': 'none', 'distortion_coefficients': []}},
            {'key': 'lidar_roof', 'name': 'Roof LiDAR', 'kind': 'lidar', 'frame_id': 'lidar_roof',
             'transform': {'translation_m': [0, 0, 2.2], 'rotation_xyzw': [0, 0, 0, 1]}},
        ],
    }


def run(api: AxiomClient, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    generated = generate(output / 'synthetic-sensors.mcap')
    recording = api.import_recording(output / 'synthetic-sensors.mcap', name='Synthetic driving · RGB and LiDAR replay',
                                     source_kind='synthetic', source='Original analytic scene; no measured sensors or CARLA engine. examples/generate_sensor_recording.py')
    rid, channels = recording['id'], generated['channels']
    supported = api.sensor_channels(rid)['channels']
    assert all(channel['status'] == 'supported' for channel in supported)
    # A PNG channel starts later: before any observations, no future image can
    # enter a current-time bundle. Exact ordinal inspection remains independent.
    before = api.sensor_bundle(rid, at_ns=BASE_NS, channel_ids=[channels['camera'], channels['lidar'], channels['compressed']])
    assert all(entry['selection_status'] == 'missing' and entry['frame'] is None for entry in before['entries'])
    definition = rig_definition()
    rig = api.create_sensor_rig(**definition)
    binding_body = {'rig_id': rig['id'], 'rig_revision': rig['revision'],
                    'note': 'Fixture capture clock: subtract known injected log delay. This is a source declaration, not clock synchronization measurement.',
                    'channels': [{'channel_id': channels['camera'], 'sensor_key': 'camera_front', 'decoder_id': 'ros2-cdr', 'decoder_version': '1',
                                  'max_age_ns': '150000000', 'log_time_offset_ns': '-3000000'},
                                 {'channel_id': channels['lidar'], 'sensor_key': 'lidar_roof', 'decoder_id': 'ros2-cdr', 'decoder_version': '1',
                                  'max_age_ns': '150000000', 'log_time_offset_ns': '-5000000'}]}
    binding = api.bind_recording_sensors(rid, expected_revision=0, **binding_body)
    cursor = BASE_NS + 6 * STEP_NS
    bundle = api.sensor_bundle(rid, at_ns=cursor, binding_revision=binding['revision'])
    assert all(entry['age_ns'] == '0' and entry['freshness'] == 'fresh' and entry['message']['sequence'] == 6 for entry in bundle['entries'])
    assert all(entry['transform_status'] == 'available' and entry['frame']['decoded']['capture_time_ns'] == str(cursor) for entry in bundle['entries'])
    entries = {entry['channel_id']: entry for entry in bundle['entries']}
    camera_ordinal, lidar_ordinal = entries[channels['camera']]['message']['ordinal'], entries[channels['lidar']]['message']['ordinal']
    camera_path = api.export_sensor_preview(rid, channels['camera'], camera_ordinal, output / 'camera-frame-06.png')
    points_path = api.export_sensor_preview(rid, channels['lidar'], lidar_ordinal, output / 'lidar-frame-06.xyz.f32', component='points')
    with Image.open(camera_path) as camera:
        assert camera.size == (WIDTH, HEIGHT)
    points = list(struct.iter_unpack('<fff', points_path.read_bytes()))
    assert len(points) == generated['point_count']
    assert abs(points[0][2] + 2.2) < 1e-5  # Native sensor z; ego transform has not silently changed bytes.
    compact = api.sensor_frame(rid, channels['lidar'], lidar_ordinal, max_points=5000)['decoded']['point_cloud']
    assert compact['original_point_count'] == generated['point_count'] and compact['skipped_points'] > 0 and compact['point_count'] <= 5000
    rig_before = api.export_sensor_rig(rig['id'], output / 'rig-r1.json').read_bytes()
    binding_before = api.export_sensor_binding(rid, output / 'binding-r1.json').read_bytes()
    changed = deepcopy(definition)
    changed['sensors'][1]['transform']['translation_m'][0] = 2
    changed['note'] = 'A deliberately changed declaration; earlier replay must retain the original mount.'
    rig2 = api.revise_sensor_rig(rig['id'], expected_revision=rig['revision'], **changed)
    preserved = api.sensor_bundle(rid, at_ns=cursor, binding_revision=binding['revision'])
    assert preserved == bundle
    binding2 = api.bind_recording_sensors(rid, expected_revision=binding['revision'], **{**binding_body, 'rig_revision': rig2['revision']})
    changed_bundle = api.sensor_bundle(rid, at_ns=cursor, binding_revision=binding2['revision'])
    lidar_changed = next(entry for entry in changed_bundle['entries'] if entry['channel_id'] == channels['lidar'])
    assert lidar_changed['transform']['translation_m'] == [2, 0, 2.2]
    assert lidar_changed['frame']['decoded'] == entries[channels['lidar']]['frame']['decoded']
    assert api.export_sensor_rig(rig['id'], output / 'rig-r1-after.json', revision=1).read_bytes() == rig_before
    assert api.export_sensor_binding(rid, output / 'binding-r1-after.json', revision=1).read_bytes() == binding_before
    stale = api.sensor_bundle(rid, at_ns=BASE_NS + 5_000_000_000, binding_revision=1)
    assert all(entry['freshness'] == 'stale' and entry['frame'] is None for entry in stale['entries'])
    downloaded = api.download_recording(recording, output / 'original-download.mcap')
    assert downloaded.read_bytes() == (output / 'synthetic-sensors.mcap').read_bytes()
    summary = {'recording_id': rid, 'rig_id': rig['id'], 'binding_revisions': [binding['revision'], binding2['revision']],
               'message_count': recording['message_count'], 'channels': channels, 'selected_capture_ns': str(cursor),
               'camera_sample_ordinal': camera_ordinal, 'lidar_sample_ordinal': lidar_ordinal,
               'full_point_count': len(points), 'bounded_point_count': compact['point_count'], 'sampling_stride': compact['sampling_stride'],
               'rig_r1_sha256': hashlib.sha256(rig_before).hexdigest(), 'binding_r1_sha256': hashlib.sha256(binding_before).hexdigest(),
               'source_sha256': recording['sha256'], 'declared_offsets_align_selected_capture_times': True,
               'future_samples_withheld': True, 'stale_samples_withheld': True, 'old_rig_and_binding_bytes_unchanged': True,
               'fidelity': 'Authored analytic fixture; no CARLA server, measured sensors, perception or calibrated physical model'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.getenv('ADE2E_API_TOKEN')) as client:
        print(json.dumps(run(client, args.output), indent=2))
