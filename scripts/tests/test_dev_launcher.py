"""Launcher configuration tests; no server process is started."""
from pathlib import Path
import importlib.util
import sys

import pytest


PATH = Path(__file__).resolve().parents[1] / "dev.py"
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location("axiom_dev_launcher", PATH)
dev = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(dev)


def test_configured_port_uses_default(monkeypatch):
    monkeypatch.delenv("AXIOM_API_PORT", raising=False)
    assert dev.configured_port("AXIOM_API_PORT", 8000) == 8000


def test_configured_port_accepts_override(monkeypatch):
    monkeypatch.setenv("AXIOM_API_PORT", "8100")
    assert dev.configured_port("AXIOM_API_PORT", 8000) == 8100


@pytest.mark.parametrize("value", ["abc", "0", "65536", "-1"])
def test_configured_port_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("AXIOM_API_PORT", value)
    with pytest.raises(ValueError, match="AXIOM_API_PORT"):
        dev.configured_port("AXIOM_API_PORT", 8000)
