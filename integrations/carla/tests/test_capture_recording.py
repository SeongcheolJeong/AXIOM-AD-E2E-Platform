"""Actual MCAP/PNG/float payload checks; no CARLA engine or physics are run."""
import base64
import io
import json
import math
import struct
from types import SimpleNamespace

import pytest
from mcap.reader import make_reader
from PIL import Image

from axiom_carla.encoding import bgra_png
from axiom_carla.recording import CapturedFrame, capture_recording, right_handed_xyzi, simulation_timestamp_ns
from axiom_carla.runner import CarlaRunner


def test_recording_retains_actual_pixels_points_clocks_and_no_interpolated_frames():
    raw = struct.pack("<ffffffff", 12, 3, 2, .25, 9, -4, -1, .75)
    png = bgra_png(2, 1, bytes([0, 0, 255, 255, 255, 0, 0, 255]))
    frames = [CapturedFrame(42, .100000001, .100000003, png, raw), CapturedFrame(82, 2.1, 2.100000003, png, raw)]
    content = capture_recording(frames, metadata={"source": "unit-test synthetic pixels", "captures": [42, 82]})
    reader = make_reader(io.BytesIO(content))
    messages = list(reader.iter_messages())
    assert len(messages) == 4
    assert {message.sequence for _, _, message in messages} == {42, 82}
    schema, channel, message = messages[0]
    assert schema.name == "foxglove.CompressedImage" and channel.message_encoding == "json"
    assert message.log_time == 100000001 == message.publish_time
    image = json.loads(message.data)
    assert image['timestamp'] == {'sec': 0, 'nsec': 100000001}
    with Image.open(io.BytesIO(base64.b64decode(image['data']))) as decoded:
        assert decoded.size == (2, 1)
        assert decoded.tobytes() == bytes([255, 0, 0, 0, 0, 255])
    cloud = json.loads(messages[1][2].data)
    assert messages[1][2].log_time == 100000003
    assert list(struct.iter_unpack('<ffff', base64.b64decode(cloud['data']))) == [(12, -3, 2, .25), (9, 4, -1, .75)]
    assert cloud['frame_id'] == 'lidar_roof' and cloud['point_stride'] == 16
    assert cloud['pose']['orientation']['w'] == 1
    metadata = list(reader.iter_metadata())[0].metadata
    assert 'no additional timestamp precision' in metadata['clock_conversion']
    assert 'not interpolated' in metadata['sampling']


@pytest.mark.parametrize('value', [-1, math.nan, math.inf, True, 2**64])
def test_capture_timestamp_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        simulation_timestamp_ns(value)


def test_sensor_bytes_reject_invalid_layout_and_nonfinite_coordinates():
    with pytest.raises(ValueError):
        right_handed_xyzi(b'wrong-size')
    with pytest.raises(ValueError):
        right_handed_xyzi(struct.pack('<ffff', 1, math.nan, 2, 3))
    assert simulation_timestamp_ns(.0000000015) == 2
    assert simulation_timestamp_ns(.0000000025) == 2
    with pytest.raises(ValueError):
        capture_recording([], metadata={})


def install_capture_world(monkeypatch):
    from test_runner_evidence import FakeCollisionSensor, FakeWorld, transform, native_transform

    class CaptureWorld(FakeWorld):
        def __init__(self):
            super().__init__()
            self.camera = FakeCollisionSensor()
            self.lidar = FakeCollisionSensor()
            self.raw_cloud = struct.pack('<ffff', 10, 4, 1, .5)

        def get_blueprint_library(self):
            return SimpleNamespace(find=lambda name: SimpleNamespace(name=name, set_attribute=lambda *_: None, has_attribute=lambda *_: True))

        def spawn_actor(self, blueprint, *args, **kwargs):
            return {'sensor.other.collision': self.collision, 'sensor.camera.rgb': self.camera, 'sensor.lidar.ray_cast': self.lidar}[blueprint.name]

        def tick(self, seconds=10):
            frame = super().tick(seconds=seconds)
            if self.camera.callback:
                self.camera.callback(SimpleNamespace(frame=frame, timestamp=frame * .05, width=480, height=270,
                                                     raw_data=bytes([0, 0, 255, 255]) * 480 * 270))
                self.lidar.callback(SimpleNamespace(frame=frame, timestamp=frame * .05,
                                                    raw_data=self.raw_cloud))
            return frame

    world = CaptureWorld()
    monkeypatch.setenv('ADE2E_CARLA_CAPTURE', '1')
    carla = SimpleNamespace(WeatherParameters=lambda **kwargs: SimpleNamespace(**kwargs),
                            Transform=native_transform,
                            Rotation=lambda **kwargs: SimpleNamespace(**kwargs),
                            Location=lambda **kwargs: SimpleNamespace(**kwargs),
                            Vector3D=lambda x, y, z: SimpleNamespace(x=x, y=y, z=z))
    monkeypatch.setattr(CarlaRunner, '_connect', lambda _: (carla, None, world, 'test-only-server'))
    monkeypatch.setattr(CarlaRunner, '_straight_spawn', lambda *_: (transform(), transform(x=45)))
    monkeypatch.setattr(CarlaRunner, '_control', lambda *_: None)
    return world


def test_runner_publishes_mcap_from_real_capture_branch_with_declared_mounts(monkeypatch, carla_scenario):
    world = install_capture_world(monkeypatch)
    result = CarlaRunner().execute(carla_scenario)
    recording = next(artifact for artifact in result.artifact_payloads if artifact.kind == 'sensor_recording')
    assert recording.media_type == 'application/mcap'
    messages = list(make_reader(io.BytesIO(recording.content)).iter_messages())
    assert len(messages) == 6  # The explicit three sampled instants, not every engine tick.
    assert sum(artifact.kind == 'camera_rgb' for artifact in result.artifact_payloads) == 3
    manifest = json.loads(next(artifact.content for artifact in result.artifact_payloads if artifact.kind == 'sensor_manifest'))
    assert manifest['scenario_revision'] == carla_scenario.revision
    assert manifest['sensor_rig_declaration']['sensors'][0]['intrinsics']['distortion_model'] == 'unknown'
    assert [entry[2].sequence for entry in messages[::2]] == [entry['world_frame'] for entry in manifest['captures']]
    assert world.camera.stopped and world.camera.destroyed and world.lidar.destroyed
