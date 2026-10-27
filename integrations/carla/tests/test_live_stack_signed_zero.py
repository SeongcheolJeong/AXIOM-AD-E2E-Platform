"""Exact float32 source/transform contract; fake-world runs, not CARLA physics."""
import hashlib
import json
import struct

import pytest

from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla.runner import CarlaRunner
from axiom_carla.stack_bridge import _consumed_xyz
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import live_request, install_live_world


def test_native_identity_quaternion_arithmetic_changes_signed_zero_bits(carla_scenario):
    request = live_request(carla_scenario)
    raw = struct.pack('<ffff', -0.0, +0.0, -0.0, .5)
    native = _consumed_xyz(raw, request.context.sensor_profile.mount)
    x, y, z, _ = struct.unpack('<ffff', raw)
    simplified = struct.pack('<fff', x, -y, z+2.2)
    assert struct.unpack('<III', native)[:2] == (0, 0)
    assert struct.unpack('<III', simplified)[:2] == (0x80000000, 0x80000000)
    assert native != simplified and hashlib.sha256(native).digest() != hashlib.sha256(simplified).digest()


@pytest.mark.parametrize('coordinates', [
    [(x,y,z) for x in (+0.0,-0.0) for y in (+0.0,-0.0) for z in (+0.0,-0.0)],
    [(4.123456, -1.234567, -2.2), (-8.74531, 2.0**-120, -3.625), (79.99999,-0.0,12.0625),
     (struct.unpack('<f',struct.pack('<I',1))[0], -struct.unpack('<f',struct.pack('<I',1))[0], -2.2000001),
     (-0.0, 0.0, -2.2)],
])
def test_full_native_fake_world_evidence_accepts_exact_signed_zero_and_real_float_points(monkeypatch,carla_scenario,coordinates):
    request = live_request(carla_scenario)
    raw = b''.join(struct.pack('<ffff',x,y,z,.5) for x,y,z in coordinates)
    world = install_live_world(monkeypatch,raw=raw)
    result = CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request,result.artifact_payloads,completed=True,
        trajectory=result.trajectory,adapter_version=ADAPTER_VERSION)
    payloads = {item.kind:item for item in result.artifact_payloads}
    trace = json.loads(payloads['live_stack_trace'].content)
    for tick in trace['ticks']:
        ref = tick['points']
        content = payloads['live_stack_points'].content[ref['offset_bytes']:ref['offset_bytes']+ref['size_bytes']]
        assert content == _consumed_xyz(raw,request.context.sensor_profile.mount)
        assert hashlib.sha256(content).hexdigest() == ref['sha256'] == tick['packet']['lidar']['points_sha256']
    assert all(actor.destroy_called for actor in world.vehicles) and world.lidar.destroyed
    assert world.settings.synchronous_mode is False
