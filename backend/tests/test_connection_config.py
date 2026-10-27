"""Saved worker credentials must not follow an explicit endpoint override."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("connection_config", Path(__file__).resolve().parents[2] / "scripts/connection_config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def saved(root, **changes):
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "worker-token").write_text("a" * 48)
    config = {"schema_version": 1, "worker_url": "http://127.0.0.1:18100", "token_file": "worker-token", **changes}
    (runtime / "carla-connection.json").write_text(json.dumps(config))


def test_default_environment_and_explicit_override_never_load_saved_credentials(tmp_path):
    assert module.carla_environment(tmp_path, {"PATH": "path"}) == {"PATH": "path"}
    saved(tmp_path)
    original = {"ADE2E_CARLA_WORKER_URL": "https://other.test"}
    assert module.carla_environment(tmp_path, original) == original
    assert original == {"ADE2E_CARLA_WORKER_URL": "https://other.test"}


def test_saved_connection_applies_only_its_endpoint_and_local_token(tmp_path):
    saved(tmp_path)
    result = module.carla_environment(tmp_path, {"PATH": "existing"})
    assert result == {"PATH": "existing", "ADE2E_CARLA_WORKER_URL": "http://127.0.0.1:18100", "ADE2E_CARLA_WORKER_TOKEN": "a" * 48}


@pytest.mark.parametrize("changes", [
    {"worker_url": "http://user:private-secret@worker.test"}, {"worker_url": "http://[invalid"},
    {"worker_url": "file:///private"}, {"worker_url": "http://host:99999"}, {"worker_url": "http://host?token=private-secret"},
    {"worker_url": "http://host/extra"}, {"worker_url": "http://host\n"}, {"worker_url": 7},
    {"schema_version": True}, {"token_file": "../private-secret"}, {"token_file": "/tmp/private-secret"},
    {"unknown": "private-secret"},
])
def test_invalid_configuration_rejects_without_echoing_secret(tmp_path, changes):
    saved(tmp_path, **changes)
    with pytest.raises(ValueError) as error: module.carla_environment(tmp_path, {})
    assert "private-secret" not in str(error.value)


def test_token_symlink_cannot_escape_local_runtime(tmp_path):
    saved(tmp_path)
    outside = tmp_path / "outside-token"
    outside.write_text("x" * 48)
    token = tmp_path / ".runtime/worker-token"
    token.unlink()
    token.symlink_to(outside)
    with pytest.raises(ValueError): module.carla_environment(tmp_path, {})
