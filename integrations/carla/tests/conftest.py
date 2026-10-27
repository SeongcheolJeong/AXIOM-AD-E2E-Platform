"""Protocol fixtures only: no CARLA wheel, GPU, or engine connection."""
import pytest

from ade2e.schemas import Scenario


@pytest.fixture
def carla_scenario():
    return Scenario(
        id="test-scenario", name="Straight-road contract trial", category="lane_follow",
        map_name="Town01", duration_s=4, seed=17, revision=1,
        created_at="2026-09-12T00:00:00Z", updated_at="2026-09-12T00:00:00Z",
    )


@pytest.fixture
def worker_payload():
    return {
        "trajectory": [{
            "time_s": 0, "ego": {"x": 1, "y": -2, "yaw": 0.25, "speed_mps": 5},
            "actors": [], "ttc_s": None, "acceleration_mps2": 0,
        }],
        "events": [],
        "metrics": [{"name": "collision_count", "value": 0, "unit": "contacts",
                     "threshold": 0, "operator": "eq", "passed": True}],
        "model_fidelity": "Test-only simulated worker payload; no engine execution",
        "artifact_payloads": [{"name": "frame.bin", "kind": "test", "media_type": "application/octet-stream",
                               "content_base64": "AP9DQVI="}],
    }
