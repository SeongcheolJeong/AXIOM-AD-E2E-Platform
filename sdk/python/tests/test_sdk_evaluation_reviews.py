import hashlib
import httpx
import pytest

from axiom_sdk import AxiomClient


def manifest(content=b"original review"):
    return {"schema_version": "detection-evaluation-comparison/1", "status": "comparable",
        "baseline": {"id": "eval_a"}, "candidate": {"id": "eval_b"},
        "exports": {"json": {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                             "url": "https://untrusted.example/never-follow"}}}


def test_sdk_same_api_exact_ids_and_hash_checked_download(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "pilot.test"
        assert request.url.params["baseline_id"] == "eval_a" and request.url.params["candidate_id"] == "eval_b"
        if request.url.path.endswith("/export"): return httpx.Response(200, content=b"original review")
        return httpx.Response(200, json=manifest())
    with AxiomClient("https://pilot.test", transport=httpx.MockTransport(handle)) as client:
        dest = client.export_evaluation_comparison("eval_a", "eval_b", tmp_path / "review.json")
    assert dest.read_bytes() == b"original review" and len(calls) == 2


def test_bad_checksum_removes_only_new_download(tmp_path):
    def handle(request):
        return httpx.Response(200, content=b"altered review!") if request.url.path.endswith("/export") else httpx.Response(200, json=manifest())
    with AxiomClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError): client.export_evaluation_comparison("eval_a", "eval_b", tmp_path / "new.json")
    assert not (tmp_path / "new.json").exists()


def test_wrong_response_identity_is_rejected():
    body = manifest(); body["candidate"]["id"] = "eval_other"
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(ValueError, match="identity"): client.compare_detection_evaluations("eval_a", "eval_b")


@pytest.mark.parametrize("a,b", [("eval_a", "eval_a"), ("../a", "eval_b"), ("eval_a", "")])
def test_invalid_requests_do_not_reach_http(a, b):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))) as client:
        with pytest.raises(ValueError): client.compare_detection_evaluations(a, b)
