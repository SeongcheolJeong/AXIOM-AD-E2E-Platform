from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from ade2e.database import Settings
from ade2e.main import create_app
from queue_worker_fixture import registry


def start_worker(data, marker, *, delay=0.4, lease=3):
    return subprocess.Popen([sys.executable, str(Path(__file__).with_name("queue_worker_fixture.py")),
                             str(data), str(marker), str(delay), str(lease)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(process):
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stdout + stderr
    return stdout


def wait_until(check, *, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Observable process state did not arrive")


def submit(client, *, key="one", scenario=None):
    if scenario is None:
        response = client.post("/api/v1/scenarios", json={"name": "Queue process fixture", "category": "lane_follow", "duration_s": 2,
                                                        "parameters": {"trigger_time_s": 1}})
        assert response.status_code == 201, response.text
        scenario = response.json()
    response = client.post("/api/v1/jobs", json={"scenario_id": scenario["id"], "adapter_id": "observable-test"},
                           headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json(), scenario


def test_persisted_snapshot_survives_api_restart_and_two_workers_claim_once(tmp_path):
    data, marker = tmp_path / "db", tmp_path / "calls"
    settings = Settings(data_dir=data, seed_examples=False)
    with TestClient(create_app(settings, registry(marker, 0.4))) as client:
        job, scenario = submit(client)
        client.patch("/api/v1/scenarios/" + scenario["id"], json={"parameters": {"ego_speed_mps": 5}}).raise_for_status()
    processes = [start_worker(data, marker), start_worker(data, marker)]
    try:
        for process in processes:
            finish(process)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait()
    assert marker.read_bytes() == b"dispatched\n"
    with TestClient(create_app(settings, registry(marker, 0.4))) as client:
        result = client.get("/api/v1/jobs/" + job["id"]).json()
        assert result["status"] == "completed", result
        run = client.get("/api/v1/runs/" + result["run_id"]).json()
        assert run["scenario_snapshot"] == job["scenario_snapshot"]
        assert run["provenance"]["input_hash"] == job["input_hash"]
        assert len(client.get("/api/v1/runs").json()) == 1
        repeated, _ = submit(client, scenario=scenario)
        assert repeated["id"] == job["id"]


def test_long_engine_releases_db_and_heartbeat_preserves_lease(tmp_path):
    data, marker = tmp_path / "db", tmp_path / "calls"
    with TestClient(create_app(Settings(data_dir=data, seed_examples=False), registry(marker, 3))) as client:
        job, _ = submit(client)
        process = start_worker(data, marker, delay=3, lease=1)
        try:
            wait_until(marker.exists)
            started = time.monotonic()
            response = client.post("/api/v1/scenarios", json={"name": "API remains writable during engine execution"})
            assert response.status_code == 201, response.text
            assert time.monotonic() - started < 1
            time.sleep(1.5)
            observed = client.get("/api/v1/jobs/" + job["id"]).json()
            assert observed["status"] == "running", observed
            finish(process)
            assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "completed"
        finally:
            if process.poll() is None:
                process.kill(); process.wait()


def test_killed_dispatcher_is_not_replayed_and_external_slot_requires_reconciliation(tmp_path):
    data, marker = tmp_path / "db", tmp_path / "calls"
    with TestClient(create_app(Settings(data_dir=data, seed_examples=False), registry(marker, 10))) as client:
        job, scenario = submit(client)
        process = start_worker(data, marker, delay=10, lease=1)
        try:
            wait_until(marker.exists)
        finally:
            process.kill(); process.communicate(timeout=5)
        wait_until(lambda: client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "interrupted")
        repeated, _ = submit(client, scenario=scenario)
        assert repeated["id"] == job["id"] and repeated["outcome_uncertain"]
        second, _ = submit(client, key="two", scenario=scenario)
        assert "no claim" in finish(start_worker(data, marker))
        assert marker.read_bytes() == b"dispatched\n"
        resources = client.get("/api/v1/execution-resources").json()
        assert resources[0]["state"] == "reconcile_required"
        path = "/api/v1/execution-resources/observable-test/reconcile"
        assert client.post(path, json={"job_id": job["id"], "note": "missing explicit stop confirmation"}).status_code == 422
        # This fixture executes entirely in the killed process; no remote engine exists.
        response = client.post(path, json={"job_id": job["id"], "note": "Test dispatcher was killed and reaped; this local fixture has no remote activity", "confirmed_stopped": True})
        assert response.status_code == 200, response.text
        finish(start_worker(data, marker))
        assert client.get("/api/v1/jobs/" + second["id"]).json()["status"] == "completed"
        assert client.get("/api/v1/jobs/" + job["id"]).json()["status"] == "interrupted"
        assert marker.read_bytes() == b"dispatched\ndispatched\n"
