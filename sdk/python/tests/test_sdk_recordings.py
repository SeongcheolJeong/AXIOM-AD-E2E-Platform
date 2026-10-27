"""Recording transport and local-file integrity boundaries, independent of MCAP parsing."""
import hashlib
from pathlib import Path

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


def test_import_streams_original_bytes_and_explicit_source_metadata(tmp_path):
    source = tmp_path / "original.mcap"
    content = b"\x89MCAP\x00\r\n" + bytes(range(256)) * 5000
    source.write_bytes(content)
    calls = []

    def api(request):
        calls.append(request)
        assert request.read() == content
        return httpx.Response(201, json={"id": "recording-1"})

    with AxiomClient("https://api.test", token="secret", transport=httpx.MockTransport(api)) as client:
        assert client.import_recording(source, name="Synthetic / telemetry", source_kind="synthetic", source="Original demo") == {"id": "recording-1"}
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == "/api/v1/recordings"
    assert request.url.params["source_kind"] == "synthetic"
    assert request.url.params["source"] == "Original demo"
    assert request.headers["content-length"] == str(len(content))
    assert request.headers["content-type"] == "application/octet-stream"
    assert request.headers["authorization"] == "Bearer secret"
    assert source.read_bytes() == content


def test_sample_seek_transmits_exact_uint64_and_zero():
    calls = []
    with AxiomClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={"samples": []}))) as client:
        for timestamp in (0, "18446744073709551615"):
            client.recording_samples("r", 42, at_ns=timestamp, limit=10)
    assert [request.url.params["at_ns"] for request in calls] == ["0", "18446744073709551615"]
    assert all(request.url.params["channel_id"] == "42" for request in calls)


def test_chronological_iteration_includes_all_duplicate_zero_timestamps():
    calls = []

    def api(request):
        calls.append(request)
        if request.url.params.get("from_start") == "true":
            return httpx.Response(200, json={"samples": [{"ordinal": i, "log_time_ns": "0"} for i in (9, 20)], "next_after_ordinal": 20})
        assert request.url.params["after_ordinal"] == "20"
        return httpx.Response(200, json={"samples": [{"ordinal": 30, "log_time_ns": "0"}], "next_after_ordinal": None})

    with AxiomClient(transport=httpx.MockTransport(api)) as client:
        result = list(client.iter_recording_samples("r", 1, page_size=2))
    assert [sample["ordinal"] for sample in result] == [9, 20, 30]
    assert len(calls) == 2


@pytest.mark.parametrize("kwargs", [{"at_ns": 0, "from_start": True}, {"from_start": True, "after_ordinal": 0}, {"after_ordinal": -1}])
def test_invalid_page_modes_fail_without_network(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected network request"))) as client:
        with pytest.raises(ValueError):
            client.recording_samples("r", 1, **kwargs)


@pytest.mark.parametrize("timestamp", [-1, 2**64, 1.5, True, "not-a-time"])
def test_bad_timestamps_fail_before_network(timestamp):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected network request"))) as client:
        with pytest.raises(ValueError):
            client.recording_samples("r", 1, at_ns=timestamp)


def recording(content):
    return {"id": "r", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "download_url": "https://outside.test/ignore"}


def test_download_streams_exact_bytes_from_fixed_api_host(tmp_path):
    content, calls = b"test\x00\xff" * 200_000, []
    with AxiomClient("https://api.test", token="secret", transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, content=content))) as client:
        path = client.download_recording(recording(content), tmp_path / "download.mcap")
    assert path.read_bytes() == content
    assert len(calls) == 1
    assert calls[0].url.host == "api.test"
    assert calls[0].url.path == "/api/v1/recordings/r/download"


@pytest.mark.parametrize("content", [b"too short", b"wrong-content", b"too-long-content-in-response"])
def test_download_integrity_error_removes_own_partial_file(tmp_path, content):
    target = tmp_path / "download.mcap"
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(ValueError):
            client.download_recording(recording(b"right-content"), target)
    assert not target.exists()


def test_download_never_overwrites_existing_user_file(tmp_path):
    target = tmp_path / "download.mcap"
    target.write_bytes(b"user file")
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"new"))) as client:
        with pytest.raises(FileExistsError):
            client.download_recording(recording(b"new"), target)
    assert target.read_bytes() == b"user file"


def test_download_redirect_does_not_forward_token_or_create_file(tmp_path):
    target, calls = tmp_path / "download.mcap", []
    with AxiomClient("https://api.test", token="secret", transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(307, headers={"location": "https://outside.test/collect"}))) as client:
        with pytest.raises(AxiomError):
            client.download_recording(recording(b"new"), target)
    assert len(calls) == 1
    assert not target.exists()


def test_interrupted_download_removes_partial_file_and_does_not_retry(tmp_path):
    target, calls = tmp_path / "download.mcap", []

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * (1024 * 1024 + 10)
            raise httpx.ReadError("connection dropped after partial delivery")

    with AxiomClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, stream=InterruptedBody()))) as client:
        with pytest.raises(httpx.ReadError):
            client.download_recording(recording(b"a" * (2 * 1024 * 1024)), target)
    assert len(calls) == 1
    assert not target.exists()


def test_response_close_error_removes_only_created_file(tmp_path):
    target = tmp_path / "download.mcap"

    class CloseFailure(httpx.SyncByteStream):
        def __iter__(self):
            yield b"complete"

        def close(self):
            raise OSError("injected response close failure")

    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=CloseFailure()))) as client:
        with pytest.raises(OSError, match="close failure"):
            client.download_recording(recording(b"complete"), target)
    assert not target.exists()


@pytest.mark.parametrize("operation", ["recording", "artifact", "manifest"])
def test_short_local_write_never_reports_success(tmp_path, monkeypatch, operation):
    target, content = tmp_path / "download.bin", b"complete"
    original_open = Path.open

    class ShortWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            return self.stream.write(data[:-1])

    def open_file(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ShortWriter(stream) if path == target and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", open_file)
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(OSError, match="Incomplete"):
            if operation == "recording":
                client.download_recording(recording(content), target)
            elif operation == "artifact":
                client.download_artifact(recording(content), target)
            else:
                client.export_run("r", target)
    assert not target.exists()
