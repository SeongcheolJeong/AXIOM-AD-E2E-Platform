"""Pinned submission, lifecycle and consumed point download boundaries."""
import copy
import hashlib
import json
import struct

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


def body(**changes):
    return {"label": "Recorded commands", "stack_id": "stack_one", "stack_revision": 2,
        "recording_id": "rec_one", "binding_revision": 3, "lidar_channel_id": 0, "ego_channel_id": 1,
        "ego_ordinals": [9, 2], "ego_decoder_id": "axiom-ego-json", "ego_decoder_version": "1",
        "ego_log_time_offset_ns": "-18446744073709551615", **changes}


def test_lost_response_preserves_exact_pins_order_clock_and_same_intent():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1: raise httpx.ReadError("Lost accepted response")
        return httpx.Response(202, json={"id": "replay_one", "status": "queued"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError): client.submit_stack_replay(**body(), idempotency_key="intent-one")
        assert len(calls) == 1
        client.submit_stack_replay(**body(), idempotency_key="intent-one")
    assert len(calls) == 2 and calls[0].content == calls[1].content
    assert all(r.method == "POST" and r.url.path == "/api/v1/stack-replays" and r.headers["idempotency-key"] == "intent-one" for r in calls)
    assert json.loads(calls[0].content) == body()


@pytest.mark.parametrize("changes", [
    {"stack_id": "../x"}, {"recording_id": "https://outside.invalid"}, {"stack_revision": True},
    {"binding_revision": 0}, {"lidar_channel_id": 1}, {"ego_channel_id": False},
    {"ego_ordinals": []}, {"ego_ordinals": [1, 1]}, {"ego_ordinals": [False]},
    {"ego_ordinals": [100000]}, {"ego_ordinals": list(range(129))}, {"ego_decoder_version": "2"},
    {"ego_log_time_offset_ns": 9007199254740993}, {"ego_log_time_offset_ns": "-0"},
    {"ego_log_time_offset_ns": "18446744073709551616"}, {"ego_log_time_offset_ns": "00"},
])
def test_invalid_pins_fail_without_http(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No request expected"))) as client:
        with pytest.raises(ValueError): client.submit_stack_replay(**body(**changes), idempotency_key="intent")


@pytest.mark.parametrize("key", [None, "", " ", "x" * 201])
def test_replay_key_is_explicit_and_nonblank(key):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No request expected"))) as client:
        with pytest.raises(ValueError): client.submit_stack_replay(**body(), idempotency_key=key)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_wait_returns_partial_terminal_outcomes_using_get_only(status):
    calls = []
    value = {"id": "replay_one", "status": status, "ticks": [{"index": 1, "status": "not_attempted", "output": None}]}
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=value)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.wait_stack_replay("replay_one", timeout=0) == value
    assert len(calls) == 1 and calls[0].method == "GET"


def test_wait_timeout_does_not_cancel_or_resubmit():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "replay_one", "status": "running"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError): client.wait_stack_replay("replay_one", timeout=0)
    assert len(calls) == 1 and calls[0].method == "GET"


def point_detail(content):
    return {"id": "replay_one", "status": "completed", "ticks": [{"index": 0,
        "points": {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "point_count": len(content) // 12, "encoding": "xyz_float32_le", "frame_id": "ego",
            "download_url": "https://outside.invalid/secret"}}]}


@pytest.mark.parametrize("content", [b"", struct.pack("<fff", 3.0, -1.0, .5)])
def test_exact_consumed_points_include_genuine_empty_cloud_and_ignore_external_url(content, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "api.test" and request.headers["authorization"] == "Bearer private"
        if request.url.path.endswith("/replay_one"): return httpx.Response(200, json=point_detail(content))
        assert request.url.path == "/api/v1/stack-replays/replay_one/ticks/0/points"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=content)
    path = tmp_path / "points.bin"
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        assert client.export_stack_replay_points("replay_one", 0, path) == path
    assert path.read_bytes() == content and len(calls) == 2


@pytest.mark.parametrize("damage", ["digest", "length", "redirect"])
def test_point_download_damage_never_leaves_partial_file(damage, tmp_path):
    content = struct.pack("<fff", 3.0, 1.0, 0.0)
    def handler(request):
        if request.url.path.endswith("/replay_one"): return httpx.Response(200, json=point_detail(content))
        if damage == "digest": return httpx.Response(200, content=b"x" * len(content))
        if damage == "length": return httpx.Response(200, content=content, headers={"Content-Length": "1000"})
        return httpx.Response(302, headers={"Location": "https://outside.invalid"})
    path = tmp_path / "points.bin"
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((ValueError, AxiomError)): client.export_stack_replay_points("replay_one", 0, path)
    assert not path.exists()


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(id="other"), lambda d: d["ticks"][0].update(index=False),
    lambda d: d["ticks"].append(copy.deepcopy(d["ticks"][0])),
    lambda d: d["ticks"][0].update(points=None),
    lambda d: d["ticks"][0]["points"].update(point_count=0),
    lambda d: d["ticks"][0]["points"].update(encoding="preview"),
])
def test_wrong_tick_identity_or_incomplete_buffer_contract_does_not_download(mutate, tmp_path):
    detail = point_detail(struct.pack("<fff", 1.0, 2.0, 3.0))
    mutate(detail)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=detail)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError): client.export_stack_replay_points("replay_one", 0, tmp_path / "points")
    assert len(calls) == 1 and list(tmp_path.iterdir()) == []


def test_stack_export_pins_returned_revision_and_manifest(tmp_path):
    content = b'{"revision":2}\n'
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/stack_one"):
            return httpx.Response(200, json={"id": "stack_one", "revision": 2, "manifest_hash": hashlib.sha256(content).hexdigest(), "manifest_size_bytes": len(content)})
        assert request.url.params["revision"] == "2"
        return httpx.Response(200, content=content)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        client.export_stack("stack_one", tmp_path / "manifest")
    assert (tmp_path / "manifest").read_bytes() == content and len(calls) == 2


def test_cli_submission_passes_explicit_request_and_stable_key(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    path = tmp_path / "request.json"
    path.write_text(json.dumps(body()))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={"id": "replay_one", "status": "queued"})
    monkeypatch.setattr(cli, "AxiomClient", lambda *args, **kwargs: AxiomClient(*args, **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("sys.argv", ["axiom", "submit-stack-replay", str(path), "--idempotency-key", "explicit"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "queued"
    assert json.loads(calls[0].content) == body() and calls[0].headers["idempotency-key"] == "explicit"
