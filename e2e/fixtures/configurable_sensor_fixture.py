"""Authored fake-world adapter evidence for browser verification; no CARLA server."""
import base64
import json
import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'integrations/carla/tests'))
from test_configured_sensor_live import configured_request, install_configured_world
from ade2e.schemas import Scenario
from ade2e.adapters import AdapterExecutionError
from axiom_carla.runner import CarlaRunner

scenario = Scenario(id='test-configurable-browser-scenario', name='Authored mounted sensor browser fixture',
    description='Authored fake-world delivery evidence, not actual CARLA or physical acceptance',
    category='lane_follow', map_name='Town01', duration_s=4, seed=17, revision=1,
    created_at='2026-09-14T00:00:00Z', updated_at='2026-09-14T00:00:00Z')
request = configured_request(scenario).model_copy(update={'execution_id': 'run_configurable_browser_' + sys.argv[1]})
with pytest.MonkeyPatch.context() as patch:
    install_configured_world(patch, fault='attributes' if sys.argv[1] == 'partial' else None)
    try:
        result = CarlaRunner().execute_with_context(request)
        payloads = result.artifact_payloads
        trajectory = [frame.model_dump(mode='json') for frame in result.trajectory]
        metrics = [metric.model_dump(mode='json') for metric in result.metrics]
        error = None
    except AdapterExecutionError as failed:
        payloads = failed.artifact_payloads
        trajectory, metrics, error = [], [], str(failed)
print(json.dumps({'request':request.model_dump(mode='json'), 'trajectory':trajectory, 'metrics':metrics, 'error':error,
    'payloads':[{'name':p.name,'kind':p.kind,'media_type':p.media_type,'base64':base64.b64encode(p.content).decode()} for p in payloads]}, allow_nan=False))
