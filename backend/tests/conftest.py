from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, seed_examples=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, Registry(load_plugins=False))) as client:
        yield client


@pytest.fixture
def make_scenario(client):
    def make(**values):
        response = client.post("/api/v1/scenarios", json={"name": "Test baseline", "category": "lane_follow", **values})
        assert response.status_code == 201, response.text
        return response.json()
    return make
