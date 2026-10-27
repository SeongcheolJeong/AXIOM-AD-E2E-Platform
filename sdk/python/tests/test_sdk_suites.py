import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_lost_campaign_response_keeps_identity_and_polling_never_resubmits():
    posts, gets = [], []

    def transport(request):
        assert request.headers["Authorization"] == "Bearer fixture-token"
        if request.method == "POST":
            posts.append((request.headers["Idempotency-Key"], json.loads(request.content)))
            if len(posts) == 1:
                raise httpx.ReadError("response lost after server saved intent")
            return httpx.Response(202, json={"id": "campaign_saved", "status": "queued"})
        gets.append(request.url.path)
        return httpx.Response(200, json={"id": "campaign_saved", "status": "running" if len(gets) == 1 else "finished",
                                         "verdict": "incomplete", "counts": {"total": 3, "cancelled": 1}})

    with AxiomClient(token="fixture-token", transport=httpx.MockTransport(transport)) as api:
        body = dict(suite_revision=2, label="Wet-road regression", idempotency_key="reusable-campaign")
        with pytest.raises(httpx.ReadError):
            api.submit_campaign("suite_one", **body)
        submitted = api.submit_campaign("suite_one", **body)
        report = api.wait_campaign(submitted["id"], poll_interval=0.001)
    assert posts[0] == posts[1]
    assert posts[1][1] == {"suite_id": "suite_one", "suite_revision": 2, "adapter_id": "reference", "label": "Wet-road regression"}
    assert gets == ["/api/v1/campaigns/campaign_saved"] * 2
    assert report["verdict"] == "incomplete" and report["counts"]["cancelled"] == 1


@pytest.mark.parametrize("verdict", ["passed", "failed", "incomplete"])
def test_settled_campaign_preserves_verdict(verdict):
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "finished", "verdict": verdict,
    }))) as api:
        assert api.wait_campaign("campaign_one", timeout=0)["verdict"] == verdict


def test_campaign_wait_timeout_cannot_cancel_or_retry():
    calls = []

    def transport(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "running"})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        with pytest.raises(TimeoutError, match="not cancelled or resubmitted"):
            api.wait_campaign("campaign_one", timeout=0)
    assert calls == [("GET", "/api/v1/campaigns/campaign_one")]


@pytest.mark.parametrize("kwargs", [{"timeout": -1}, {"timeout": float("nan")}, {"poll_interval": 0}, {"poll_interval": float("inf")}])
def test_campaign_wait_rejects_invalid_limits(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not poll"))) as api:
        with pytest.raises(ValueError):
            api.wait_campaign("campaign_one", **kwargs)


@pytest.mark.parametrize("key", [None, "", "x" * 201])
def test_execution_and_baseline_require_explicit_keys(key):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not submit"))) as api:
        with pytest.raises(ValueError):
            api.submit_campaign("suite_one", suite_revision=1, label="Check", idempotency_key=key)
        with pytest.raises(ValueError):
            api.create_baseline("campaign_one", name="Reference", note="Review", idempotency_key=key)


def test_baseline_export_checks_stored_digest_and_never_overwrites(tmp_path):
    content = b'{"cases":[{"verdict":"not_evaluated"}],"total":1}'
    digest = hashlib.sha256(content).hexdigest()
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/export"):
            return httpx.Response(200, content=content)
        # Never follow a URL to an unrelated host carrying the auth token.
        return httpx.Response(200, json={"manifest_hash": digest, "download_url": "https://invalid.example/collect"})

    destination = tmp_path / "evidence.json"
    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        assert api.export_baseline("baseline_one", destination) == destination
        with pytest.raises(FileExistsError):
            api.export_baseline("baseline_one", destination)
    assert destination.read_bytes() == content
    assert calls == ["/api/v1/baselines/baseline_one", "/api/v1/baselines/baseline_one/export"] * 2


def test_corrupted_baseline_export_creates_no_file(tmp_path):
    def transport(request):
        return httpx.Response(200, content=b"corrupt") if request.url.path.endswith("/export") else httpx.Response(
            200, json={"manifest_hash": "0" * 64})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        with pytest.raises(ValueError, match="Baseline SHA-256 mismatch"):
            api.export_baseline("baseline_one", tmp_path / "evidence.json")
    assert list(tmp_path.iterdir()) == []


def test_cli_reads_suite_definition_without_replacing_revision_guard(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"name": "Updated suite", "cases": []}))
    received = []

    def revise(self, suite_id, **body):
        received.append((suite_id, body))
        return {"id": suite_id, "revision": 3}

    monkeypatch.setattr(AxiomClient, "revise_suite", revise)
    monkeypatch.setattr("sys.argv", ["axiom", "revise-suite", "suite_one", str(path), "--expected-revision", "2"])
    cli.main()
    assert received == [("suite_one", {"expected_revision": 2, "name": "Updated suite", "cases": []})]
    assert json.loads(capsys.readouterr().out)["revision"] == 3


def test_capture_retry_preserves_original_comparison_and_downloads_exact_manifest(tmp_path):
    payload = b'{"policy_version":"suite-comparison/1","report":{"counts":{"not_comparable":1}}}'
    sha = hashlib.sha256(payload).hexdigest()
    requests = []

    def transport(request):
        requests.append(request)
        if request.method == "POST":
            if len(requests) == 1:
                raise httpx.ReadError("response lost after capture")
            return httpx.Response(201, json={"id": "cmp_original", "manifest_hash": sha})
        if request.url.path.endswith("/export"):
            return httpx.Response(200, content=payload)
        return httpx.Response(200, json={"id": "cmp_original", "manifest_hash": sha})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        body = dict(baseline_id="baseline_one", name="Scope review", note="Changed input", idempotency_key="capture-once")
        with pytest.raises(httpx.ReadError):
            api.capture_comparison("campaign_one", **body)
        saved = api.capture_comparison("campaign_one", **body)
        api.export_comparison(saved["id"], tmp_path / "comparison.json")
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"] == "capture-once"
    assert (tmp_path / "comparison.json").read_bytes() == payload
    assert [request.method for request in requests] == ["POST", "POST", "GET", "GET"]


def test_corrupted_comparison_export_preserves_existing_file(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_bytes(b"previous evidence")

    def transport(request):
        return httpx.Response(200, content=b"corrupt") if request.url.path.endswith("/export") else httpx.Response(
            200, json={"manifest_hash": "0" * 64})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        with pytest.raises(ValueError, match="Comparison SHA-256 mismatch"):
            api.export_comparison("cmp_one", path)
    assert path.read_bytes() == b"previous evidence"


@pytest.mark.parametrize('version', [1, 2])
def test_campaign_control_retry_keeps_exact_selection_and_key(version):
    control = {'mode': 'stack', 'stack_id': 'stack_pinned', 'stack_revision': 2,
        'rig_id': 'rig_pinned', 'rig_revision': 3, 'lidar_sensor_key': 'front'}
    if version == 1:
        control['sensor_profile_id'] = 'carla-lidar-ego/1'
    else:
        control.update(schema_version='simulation-control-selection/2',
            sensor_configuration_id='sensor_recipe', sensor_configuration_revision=4)
    calls = []
    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError('response lost after atomic campaign commit')
        return httpx.Response(202, json={'id': 'same-campaign', 'execution_plan': {'control': control}})
    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        kwargs = dict(suite_revision=8, label='Pinned control cases', adapter_id='carla',
            control=control, idempotency_key='same-control-campaign')
        with pytest.raises(httpx.ReadError): api.submit_campaign('suite_one', **kwargs)
        result = api.submit_campaign('suite_one', **kwargs)
    assert calls[0].content == calls[1].content
    assert calls[0].headers['Idempotency-Key'] == calls[1].headers['Idempotency-Key'] == 'same-control-campaign'
    assert json.loads(calls[1].content)['control'] == control == result['execution_plan']['control']


def test_campaign_explicit_null_control_preserves_old_wire_body():
    calls = []
    def transport(request):
        calls.append(request.content)
        return httpx.Response(202, json={'id': 'legacy'})
    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        api.submit_campaign('suite_one', suite_revision=1, label='Old body', idempotency_key='old')
        api.submit_campaign('suite_one', suite_revision=1, label='Old body', idempotency_key='old', control=None)
    assert calls[0] == calls[1] and 'control' not in json.loads(calls[1])


def test_cli_campaign_loads_bounded_control_without_changing_identity(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    selection = {'schema_version': 'simulation-control-selection/2', 'mode': 'stack',
        'stack_id': 'stack_one', 'stack_revision': 1, 'rig_id': 'rig_one', 'rig_revision': 2,
        'lidar_sensor_key': 'front', 'sensor_configuration_id': 'config_one', 'sensor_configuration_revision': 3}
    path = tmp_path/'selection.json'; path.write_text(json.dumps(selection))
    calls = []
    def submit(self, suite_id, **kwargs):
        calls.append((suite_id, kwargs)); return {'id': 'campaign_fixed'}
    monkeypatch.setattr(AxiomClient, 'submit_campaign', submit)
    monkeypatch.setattr('sys.argv', ['axiom','submit-campaign','suite_one','--suite-revision','4','--label','Review',
        '--adapter','carla','--control',str(path),'--idempotency-key','fixed-key'])
    cli.main()
    assert calls == [('suite_one', dict(suite_revision=4,label='Review',adapter_id='carla',control=selection,idempotency_key='fixed-key'))]
    assert json.loads(capsys.readouterr().out) == {'id': 'campaign_fixed'}


@pytest.mark.parametrize('content', [b'x'*65537, b'[]', b'{malformed'])
def test_cli_campaign_rejects_bad_control_before_any_submission(tmp_path, monkeypatch, capsys, content):
    from axiom_sdk import cli
    path=tmp_path/'selection.json';path.write_bytes(content)
    monkeypatch.setattr(AxiomClient, 'submit_campaign', lambda *args, **kwargs: pytest.fail('must not submit'))
    monkeypatch.setattr('sys.argv', ['axiom','submit-campaign','suite_one','--suite-revision','1','--label','Bad',
        '--control',str(path),'--idempotency-key','bad'])
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code == 1 and 'error' in json.loads(capsys.readouterr().err)
