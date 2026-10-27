import json

import httpx
import pytest

from axiom_sdk import AxiomClient
from axiom_sdk.cli import _control_file


CONTROL = {"mode": "stack", "stack_id": "stack_one", "stack_revision": 2,
           "rig_id": "rig_one", "rig_revision": 1, "lidar_sensor_key": "lidar_roof",
           "sensor_profile_id": "carla-lidar-ego/1"}


@pytest.mark.parametrize("method,path", [("run", "/api/v1/runs"), ("submit_job", "/api/v1/jobs")])
def test_control_selection_keeps_endpoint_key_and_exact_revisions(method, path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == path
        assert request.headers["Idempotency-Key"] == "fixed-selection-1"
        return httpx.Response(200, json={"id": "saved_result"})

    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        call = getattr(client, method)
        for _ in range(2):
            call("scenario_one", adapter_id="carla", idempotency_key="fixed-selection-1", control=CONTROL)
        call("scenario_one", adapter_id="carla", idempotency_key="fixed-selection-1")
    assert requests[0].content == requests[1].content
    assert json.loads(requests[0].content)["control"] == CONTROL
    assert "control" not in json.loads(requests[2].content)


def test_cli_reads_exact_control_selection_and_rejects_wrong_or_unbounded_file(tmp_path):
    source = tmp_path / "control.json"
    source.write_text(json.dumps(CONTROL))
    assert _control_file(str(source)) == CONTROL
    assert _control_file(None) is None
    source.write_text("null")
    with pytest.raises(ValueError, match="object"):
        _control_file(str(source))
    source.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        _control_file(str(source))


def test_profile_catalog_sdk_and_cli_use_same_read_only_endpoint(monkeypatch, capsys):
    from axiom_sdk import cli
    observed = []
    catalog = [{"id": "carla-lidar-ego-dense/1", "supported_adapter_versions": ["0.6.0"]}]
    def handler(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json=catalog)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.simulation_sensor_profiles() == catalog
    monkeypatch.setattr(cli, 'AxiomClient', lambda *args, **kw: AxiomClient(*args, transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr('sys.argv', ['axiom', 'simulation-sensor-profiles'])
    cli.main()
    assert json.loads(capsys.readouterr().out) == catalog
    assert observed == [('GET', '/api/v1/simulation-sensor-profiles')] * 2
