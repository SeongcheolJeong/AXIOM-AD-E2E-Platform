import json
from pathlib import Path
import pytest
from axiom_hil_gateway.devices import load,probe_devices

def test_unregistered_hardware_never_becomes_ready(tmp_path):
    (tmp_path/'bridge-devices.json').write_text(json.dumps({'hardware':None}))
    with pytest.raises(ValueError,match='not configured'):load(tmp_path,'hardware')

def test_physical_profile_cannot_use_virtual_interface(monkeypatch):
    monkeypatch.setattr('socket.if_nametoindex',lambda n:1)
    original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda p:Path('/sys/devices/virtual/net/vcan0') if str(p).startswith('/sys/class/net') else original(p))
    with pytest.raises(ValueError,match='Virtual interface'):probe_devices(dict(target='hardware',can_a='vcan0',can_b='vcan1'))

def test_same_bus_does_not_allow_selective_injection(tmp_path):
    (tmp_path/'bridge-devices.json').write_text(json.dumps({'vcan':dict(target='vcan',can_a='vcan0',can_b='vcan0')}))
    with pytest.raises(ValueError,match='isolated'):load(tmp_path,'vcan')
