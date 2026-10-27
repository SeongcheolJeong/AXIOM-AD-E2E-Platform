"""Original CPU fixtures: portability, real replay semantics and refusal checks."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("github_demo_builder", ROOT / "scripts/build_github_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory):
    path = tmp_path_factory.mktemp("github-demo") / "fresh-build"
    manifest = demo.build(path)
    return path, manifest


def test_packaged_fixtures_relocate_and_keep_original_bytes():
    source = ROOT / "examples/github-demo"
    before = demo.file_inventory(source)
    report = demo.verify(source)
    assert report["status"] == "pass"
    assert [item["runs"] + item["executions"] for item in report["checks"]] == [2, 2, 2]
    assert demo.file_inventory(source) == before


def test_fresh_build_repeats_authored_inputs_and_object_numerics(rebuilt):
    path, actual = rebuilt
    original = demo.check_inventory(ROOT / "examples/github-demo")
    assert demo.verify(path)["status"] == "pass"
    for sample in demo.SAMPLES:
        first, second = original["samples"][sample], actual["samples"][sample]
        assert [run["output_hash"] for run in first["runs"]] == [run["output_hash"] for run in second["runs"]]
        if sample != "object-reference":
            assert first["recording_sha256"] == second["recording_sha256"]
    assert [run["verdict"] for run in actual["samples"]["object-reference"]["runs"]] == ["passed", "failed"]


def _trace(client, execution):
    artifact = next(item for item in execution["artifacts"] if item["name"] == "planning-trace.ndjson")
    response = client.get(artifact["download_url"])
    assert response.status_code == 200
    return response.content, [json.loads(line) for line in response.content.splitlines()]


@pytest.mark.parametrize("sample", ["log-open-loop", "log-feedback"])
def test_normal_queue_replay_is_repeatable_and_preserves_mode_boundaries(rebuilt, tmp_path, sample):
    from ade2e.worker import run_once
    source, manifest = rebuilt
    target = tmp_path / "different-location"
    demo.load(source, sample, target)
    final_positions = []
    with demo.reference_client(target) as client:
        for row in manifest["samples"][sample]["executions"]:
            saved = demo.request(client, "GET", f"/experiments/executions/{row['id']}")
            original_bytes, ticks = _trace(client, saved)
            final_positions.append(ticks[-1]["ego"]["x_m"])
            assert len(ticks) == 41
            assert all(tick["metrics"]["lane_departure"]["status"] == "unevaluated" for tick in ticks)
            assert all(tick["metrics"]["goal_arrival"]["status"] == "unevaluated" for tick in ticks)
            if row["mode"] in {"original_replay", "open_loop"}:
                assert all(tick["ego"] == tick["logged_ego"] for tick in ticks)
                assert (ticks[0]["control"] is None) == (row["mode"] == "original_replay")
            else:
                assert ticks[1]["ego"] == ticks[0]["next_ego"]
                assert any(tick["ego"] != tick["logged_ego"] for tick in ticks)
            experiment = demo.request(client, "GET", f"/experiments/{row['experiment_id']}")
            job = demo.request(client, "POST", f"/experiments/{row['experiment_id']}/executions", 202,
                               json={"preview_hash": experiment["preview_hash"]},
                               headers={"Idempotency-Key": "repeat-" + row["id"]})
            assert run_once(client.app.state.db, client.app.state.registry, "github-demo-test", job_id=job["id"],
                            experiment_registry=client.app.state.experiment_registry)
            repeat = demo.request(client, "GET", f"/experiments/executions/{job['id']}")
            assert repeat["status"] == "completed"
            assert _trace(client, repeat)[0] == original_bytes
    if sample == "log-feedback":
        assert abs(final_positions[0] - final_positions[1]) > 1.0


def test_loader_rejects_changed_artifacts_and_existing_targets(rebuilt, tmp_path):
    source, _ = rebuilt
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "original.txt"
    sentinel.write_text("preserve me")
    with pytest.raises(ValueError, match="must not exist"):
        demo.load(source, "object-reference", existing)
    assert sentinel.read_text() == "preserve me"
    with pytest.raises(ValueError, match="already exists"):
        demo.build(existing)
    copy = tmp_path / "corrupted-copy"
    shutil.copytree(source, copy)
    artifact = next((copy / "object-reference/artifacts").iterdir())
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="SHA-256 differs"):
        demo.load(copy, "object-reference", tmp_path / "should-not-exist")
    assert not (tmp_path / "should-not-exist").exists()


def test_loader_rejects_linked_payload_without_reading_it(rebuilt, tmp_path):
    source, _ = rebuilt
    copy = tmp_path / "linked-copy"
    shutil.copytree(source, copy)
    artifact = next((copy / "object-reference/artifacts").iterdir())
    artifact.unlink()
    artifact.symlink_to(tmp_path / "nonexistent-external")
    with pytest.raises(ValueError, match="Symlinks"):
        demo.check_inventory(copy)
