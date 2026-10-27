"""HTTP/source identity and owned-file boundaries for dataset/evaluation APIs."""
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


def image_asset(content=b"exact saved PNG", **changes):
    return {"id": "image_one", "created_at": "2026-09-13T00:00:00Z",
        "input": {"recording_id": "rec_one", "channel_id": 0, "ordinal": 0, "decoder_id": "ros2-cdr",
                  "decoder_version": "1", "recording_sha256": "a" * 64, "message_sha256": "b" * 64,
                  "message_size_bytes": 31, "log_time_ns": str(2**64 - 1), "publish_time_ns": "0"},
        "image": {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content), "width": 10, "height": 10,
                  "download_url": "https://outside.invalid/leak", "frame_id": "camera", "capture_time_ns": "0"},
        "recording_snapshot": {"id": "rec_one", "sha256": "a" * 64, "channel": {"id": 0}}, **changes}


def definition():
    return {"name": "Reviewed set", "labels": ["person", "car"], "source": "unknown", "license": "unknown",
            "note": "Annotation review is explicit", "images": [{"image_id": "image_one", "split": "test",
                "review_status": "unreviewed", "annotation_source": "unknown", "annotations": []}]}


def evaluation_body(**changes):
    return {"name": "Stored prediction review", "note": "Not a road accuracy claim", "dataset_id": "dataset_one",
            "dataset_revision": 3, "split": "test", "inference_job_ids": ["job_z", "job_a"],
            "class_mapping": [{"model_label": "person", "dataset_label": "person"}, {"model_label": "ignored", "dataset_label": None}],
            "evaluator_id": "coco-bbox", "score_threshold": 0, "diagnostic_iou": .5, **changes}


def capture(client):
    return client.capture_dataset_image("rec_one", 0, 0, decoder_id="ros2-cdr", decoder_version="1")


def test_capture_posts_exact_source_pins_without_inference_or_automatic_review():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=image_asset())
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        result = capture(client)
    assert len(calls) == 1 and calls[0].method == "POST" and calls[0].url.path == "/api/v1/dataset-images"
    assert calls[0].headers["authorization"] == "Bearer private"
    assert json.loads(calls[0].content) == {"recording_id": "rec_one", "channel_id": 0, "ordinal": 0, "decoder_id": "ros2-cdr", "decoder_version": "1"}
    assert result["input"]["log_time_ns"] == str(2**64 - 1)


@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(id="https://outside.invalid/id"),
    lambda asset: asset["input"].update(recording_id="other"),
    lambda asset: asset["input"].update(channel_id=1),
    lambda asset: asset["input"].update(ordinal=1),
    lambda asset: asset["input"].update(decoder_version="2"),
    lambda asset: asset["input"].update(message_sha256="broken"),
    lambda asset: asset["input"].update(log_time_ns=2**63),
    lambda asset: asset["input"].update(log_time_ns="00"),
    lambda asset: asset["input"].update(publish_time_ns=str(2**64)),
    lambda asset: asset["recording_snapshot"].update(sha256="c" * 64),
    lambda asset: asset["recording_snapshot"]["channel"].update(id=False),
    lambda asset: asset["image"].update(width=True),
    lambda asset: asset["image"].update(width=2_000_000, height=2),
    lambda asset: asset["image"].update(size_bytes=8 * 1024 * 1024 + 1),
])
def test_capture_rejects_wrong_or_malformed_source_image_identity(mutate):
    asset = image_asset()
    mutate(asset)
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(201, json=asset))) as client:
        with pytest.raises(ValueError): capture(client)


@pytest.mark.parametrize("identifier", ["../other", "x/y", "?query", "https://outside.invalid/", "", None])
def test_image_ids_are_not_paths_and_invalid_source_pins_never_reach_http(identifier):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        with pytest.raises(ValueError): client.get_dataset_image(identifier)
        with pytest.raises(ValueError): client.capture_dataset_image(identifier, 0, 0, decoder_id="ros2-cdr", decoder_version="1")


def test_dataset_create_preserves_explicit_unreviewed_empty_annotation():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "dataset_one", "revision": 1})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        client.create_dataset(**definition())
        client.revise_dataset("dataset_one", expected_revision=1, **definition())
    assert json.loads(calls[0].content) == definition()
    assert json.loads(calls[1].content) == {**definition(), "expected_revision": 1}
    assert calls[1].url.path == "/api/v1/datasets/dataset_one/revisions"


def test_stale_revision_preserves_409_and_does_not_retry_or_fetch_latest():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(409, json={"detail": "Revision changed"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AxiomError) as error:
            client.revise_dataset("dataset_one", expected_revision=1, **definition())
    assert error.value.status_code == 409 and len(calls) == 1


def test_evaluation_lost_response_only_retries_when_user_repeats_same_intent():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, request.headers["Idempotency-Key"], request.content))
        if len(calls) == 1:
            raise httpx.ReadError("Reply lost after acceptance")
        return httpx.Response(202, json={"id": "evaluation_one", "status": "queued"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            client.submit_detection_evaluation(**evaluation_body(), idempotency_key="fixed-intent")
        assert len(calls) == 1
        result = client.submit_detection_evaluation(**evaluation_body(), idempotency_key="fixed-intent")
    assert result["status"] == "queued" and calls[0] == calls[1]
    assert calls[0][1] == "/api/v1/detection-evaluations"
    assert json.loads(calls[0][3])["inference_job_ids"] == ["job_z", "job_a"]
    assert json.loads(calls[0][3])["class_mapping"][1]["dataset_label"] is None


@pytest.mark.parametrize("changes", [
    {"dataset_revision": True}, {"dataset_revision": 0}, {"dataset_revision": 1.0},
    {"dataset_id": "../other"}, {"split": "all"}, {"inference_job_ids": []},
    {"inference_job_ids": ["one", "one"]}, {"inference_job_ids": ["https://outside.invalid"]},
    {"inference_job_ids": [f"job_{i}" for i in range(17)]},
    {"class_mapping": []}, {"class_mapping": [{"model_label": "person"}]},
    {"class_mapping": [{"model_label": "person", "dataset_label": ""}]},
    {"class_mapping": [{"model_label": " person", "dataset_label": None}, {"model_label": "person", "dataset_label": "person"}]},
    {"score_threshold": float("nan")}, {"score_threshold": True}, {"diagnostic_iou": .51},
])
def test_invalid_evaluation_pins_mapping_and_limits_fail_before_network(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        with pytest.raises(ValueError):
            client.submit_detection_evaluation(**evaluation_body(**changes), idempotency_key="explicit")


@pytest.mark.parametrize("key", [None, "", " ", "x" * 201])
def test_evaluation_key_is_required_and_nonblank(key):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        with pytest.raises(ValueError): client.submit_detection_evaluation(**evaluation_body(), idempotency_key=key)


def test_listing_uses_explicit_page_and_status_without_mutation():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "next_before_seq": None})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.detection_evaluations(status="interrupted", limit=4, before_seq=9007199254740993)["items"] == []
    assert calls[0].method == "GET" and dict(calls[0].url.params) == {"status": "interrupted", "limit": "4", "before_seq": "9007199254740993"}


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_wait_returns_exact_terminal_outcome_without_fabricated_accuracy(status):
    result = {"id": "evaluation_one", "status": status, "report": None}
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=result)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.wait_detection_evaluation("evaluation_one", timeout=0) == result
    assert len(calls) == 1 and calls[0].method == "GET"


def test_wait_timeout_and_identity_mismatch_never_cancel_or_resubmit():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "evaluation_one", "status": "queued"})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError): client.wait_detection_evaluation("evaluation_one", timeout=0)
        with pytest.raises(ValueError): client.get_detection_evaluation("other")
    assert len(calls) == 2 and all(request.method == "GET" for request in calls)


def download_setup(kind, content):
    digest = hashlib.sha256(content).hexdigest()
    if kind == "image":
        return image_asset(content), "/dataset-images/image_one", "/dataset-images/image_one/image", lambda client, path: client.export_dataset_image("image_one", path)
    if kind == "dataset":
        return {"id": "dataset_one", "revision": 4, "manifest_hash": digest, "manifest_size_bytes": len(content), "download_url": "https://outside.invalid/leak"}, "/datasets/dataset_one", "/datasets/dataset_one/export", lambda client, path: client.export_dataset("dataset_one", path)
    return {"id": "evaluation_one", "status": "failed", "manifest_hash": digest, "manifest_size_bytes": len(content), "download_url": "https://outside.invalid/leak"}, "/detection-evaluations/evaluation_one", "/detection-evaluations/evaluation_one/export", lambda client, path: client.export_detection_evaluation("evaluation_one", path)


@pytest.mark.parametrize("kind", ["image", "dataset", "evaluation"])
def test_evidence_download_pins_same_origin_hash_length_and_dataset_revision(kind, tmp_path):
    content = b"canonical saved evidence\n"
    metadata, metadata_path, download_path, operation = download_setup(kind, content)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "api.test" and request.headers["authorization"] == "Bearer private"
        if request.url.path == "/api/v1" + metadata_path:
            return httpx.Response(200, json=metadata)
        assert request.url.path == "/api/v1" + download_path
        assert request.headers["accept-encoding"] == "identity"
        if kind == "dataset": assert request.url.params["revision"] == "4"
        return httpx.Response(200, content=content)
    destination = tmp_path / "evidence"
    with AxiomClient("https://api.test", token="private", transport=httpx.MockTransport(handler)) as client:
        assert operation(client, destination) == destination
    assert destination.read_bytes() == content and len(calls) == 2


@pytest.mark.parametrize("kind", ["image", "dataset", "evaluation"])
@pytest.mark.parametrize("damage", ["digest", "length", "redirect"])
def test_download_failures_never_leave_credible_partial_evidence(kind, damage, tmp_path):
    content = b"original"
    metadata, metadata_path, _, operation = download_setup(kind, content)
    def handler(request):
        if request.url.path == "/api/v1" + metadata_path:
            return httpx.Response(200, json=metadata)
        if damage == "redirect": return httpx.Response(302, headers={"Location": "https://outside.invalid/leak"})
        if damage == "length": return httpx.Response(200, content=content, headers={"Content-Length": "999"})
        return httpx.Response(200, content=b"modified")
    destination = tmp_path / "evidence"
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((ValueError, AxiomError)): operation(client, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["image", "dataset", "evaluation"])
def test_export_identity_mismatch_does_not_download_or_touch_destination(kind, tmp_path):
    metadata, _, _, operation = download_setup(kind, b"x")
    metadata["id"] = "other"
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=metadata)
    destination = tmp_path / "evidence"
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError): operation(client, destination)
    assert len(calls) == 1 and not destination.exists()


@pytest.mark.parametrize("kind", ["image", "dataset", "evaluation"])
def test_exports_keep_existing_destination_and_detect_real_short_write(kind, tmp_path, monkeypatch):
    content = b"original"
    metadata, metadata_path, _, operation = download_setup(kind, content)
    def handler(request):
        return httpx.Response(200, json=metadata) if request.url.path == "/api/v1" + metadata_path else httpx.Response(200, content=content)
    destination = tmp_path / "existing"
    destination.write_bytes(b"user's existing file")
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FileExistsError): operation(client, destination)
    assert destination.read_bytes() == b"user's existing file"
    original_open = Path.open
    class ShortWriter:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def write(self, data): return self.stream.write(data[:len(data)//2])
    def patched_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ShortWriter(stream) if mode == "xb" else stream
    monkeypatch.setattr(Path, "open", patched_open)
    partial = tmp_path / "partial"
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OSError, match="Incomplete"): operation(client, partial)
    assert not partial.exists()


def test_requested_revision_mismatch_and_unsealed_evaluation_are_rejected(tmp_path):
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "dataset_one", "revision": 5}))) as client:
        with pytest.raises(ValueError): client.export_dataset("dataset_one", tmp_path / "dataset", revision=4)
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "evaluation_one", "status": "running"}))) as client:
        with pytest.raises(ValueError): client.export_detection_evaluation("evaluation_one", tmp_path / "evaluation")
    assert list(tmp_path.iterdir()) == []


def test_cli_submission_uses_explicit_key_revision_and_json_output(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(evaluation_body()))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={"id": "evaluation_one", "status": "queued"})
    monkeypatch.setattr(cli, "AxiomClient", lambda *args, **kwargs: AxiomClient(*args, **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("sys.argv", ["axiom", "submit-detection-evaluation", str(path), "--idempotency-key", "saved-intent"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "queued"
    assert calls[0].headers["idempotency-key"] == "saved-intent"
    assert json.loads(calls[0].content)["dataset_revision"] == 3


def test_cli_missing_pin_is_structured_error_without_network(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    body = evaluation_body()
    body.pop("dataset_revision")
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(body))
    monkeypatch.setattr(cli, "AxiomClient", lambda *args, **kwargs: AxiomClient(*args, **kwargs, transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))))
    monkeypatch.setattr("sys.argv", ["axiom", "submit-detection-evaluation", str(path), "--idempotency-key", "saved-intent"])
    with pytest.raises(SystemExit) as error: cli.main()
    assert error.value.code == 1 and "revision" in json.loads(capsys.readouterr().err)["error"]
