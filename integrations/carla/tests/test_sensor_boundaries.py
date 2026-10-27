"""Unit evidence for sensor conversion, not physical sensor fidelity."""
import math
import queue
import struct
import zlib
from types import SimpleNamespace

import pytest

from ade2e.adapters import AdapterInputError, AdapterUnavailable
from axiom_carla.encoding import bgra_png
from axiom_carla.runner import CarlaRunner, _sensor_frame, _state


def test_png_decodes_to_rgb_with_correct_size_rows_and_crc():
    # Two rows test channel order, row framing, and opaque output from varied alpha.
    encoded = bgra_png(2, 2, bytes([
        0, 0, 255, 0, 0, 255, 0, 128,
        255, 0, 0, 255, 32, 64, 128, 7,
    ]))
    assert encoded[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, offset = [], 8
    while offset < len(encoded):
        size = struct.unpack(">I", encoded[offset:offset + 4])[0]
        tag = encoded[offset + 4:offset + 8]
        data = encoded[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", encoded[offset + 8 + size:offset + 12 + size])[0]
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF
        chunks.append((tag, data))
        offset += 12 + size
    assert offset == len(encoded)
    assert struct.unpack(">IIBBBBB", chunks[0][1]) == (2, 2, 8, 2, 0, 0, 0)
    scanlines = zlib.decompress(b"".join(data for tag, data in chunks if tag == b"IDAT"))
    assert scanlines == b"\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\x80\x40\x20"
    assert chunks[-1] == (b"IEND", b"")


@pytest.mark.parametrize("width,height,raw", [(0, 1, b""), (-1, 1, b""), (1, 0, b""), (1, 1, b"abc"), (2001, 1000, b"")])
def test_png_rejects_invalid_or_unbounded_sensor_frame(width, height, raw):
    with pytest.raises(ValueError):
        bgra_png(width, height, raw)


def test_actor_conversion_uses_right_handed_xy_radians_and_vector_speed():
    actor = SimpleNamespace(
        get_transform=lambda: SimpleNamespace(location=SimpleNamespace(x=10, y=7, z=2), rotation=SimpleNamespace(yaw=90)),
        get_velocity=lambda: SimpleNamespace(x=3, y=4, z=12),
    )
    state = _state(actor)
    assert state.x == 10
    assert state.y == -7
    assert state.yaw == pytest.approx(-math.pi / 2)
    assert state.speed_mps == 13


def test_sensor_alignment_discards_stale_data_but_returns_exact_frame():
    stream = queue.Queue()
    for frame in [17, 18, 20, 21]:
        stream.put(SimpleNamespace(frame=frame))
    assert _sensor_frame(stream, 20, timeout=0.01).frame == 20
    assert stream.get_nowait().frame == 21


def test_sensor_alignment_rejects_a_gap_instead_of_using_nearby_sample():
    stream = queue.Queue()
    stream.put(SimpleNamespace(frame=21))
    with pytest.raises(AdapterUnavailable, match="expected 20, received 21"):
        _sensor_frame(stream, 20, timeout=0.01)


def test_missing_sensor_frame_times_out():
    with pytest.raises(AdapterUnavailable):
        _sensor_frame(queue.Queue(), 20, timeout=0.005)


def test_preflight_requires_exclusive_server_opt_in_before_import(monkeypatch):
    monkeypatch.delenv("ADE2E_CARLA_EXCLUSIVE", raising=False)
    monkeypatch.setattr("axiom_carla.runner.importlib.import_module", lambda _: pytest.fail("CARLA imported before exclusivity check"))
    with pytest.raises(AdapterUnavailable, match="EXCLUSIVE"):
        CarlaRunner().preflight()


def test_version_mismatch_does_not_access_or_modify_world(monkeypatch):
    monkeypatch.setenv("ADE2E_CARLA_EXCLUSIVE", "1")
    client = SimpleNamespace(
        set_timeout=lambda _: None,
        get_server_version=lambda: "0.9.15",
        get_client_version=lambda: "0.9.16",
        get_world=lambda: pytest.fail("Version mismatch must not reach world"),
    )
    monkeypatch.setattr("axiom_carla.runner.importlib.import_module", lambda _: SimpleNamespace(Client=lambda *_: client))
    with pytest.raises(AdapterUnavailable, match="version mismatch"):
        CarlaRunner().preflight()


@pytest.mark.parametrize("duration", [4.01, 20.05])
def test_invalid_timestep_duration_is_rejected_before_connection(duration, carla_scenario, monkeypatch):
    monkeypatch.setattr(CarlaRunner, "_connect", lambda _: pytest.fail("Invalid duration reached engine"))
    with pytest.raises(AdapterInputError):
        CarlaRunner().execute(carla_scenario.model_copy(update={"duration_s": duration}))


def test_map_mismatch_does_not_modify_world(carla_scenario, monkeypatch):
    world = SimpleNamespace(get_map=lambda: SimpleNamespace(name="/Game/Carla/Maps/Town02"))
    monkeypatch.setattr(CarlaRunner, "_connect", lambda _: (None, None, world, "test"))
    with pytest.raises(AdapterInputError, match="Town02"):
        CarlaRunner().execute(carla_scenario)
