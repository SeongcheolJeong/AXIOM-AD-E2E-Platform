"""Generate actual native bridge/kernel bytes over the existing mocked world.

This is an independent browser contract fixture, not a CARLA physics run.
No real server, database, network or actor is accessed.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'integrations/carla/tests'))
from test_live_stack import install_live_world, live_request
from ade2e.schemas import Scenario
from axiom_carla.runner import CarlaRunner
from axiom_carla.version import ADAPTER_VERSION

output_dir = Path(sys.argv[1])
output_dir.mkdir(parents=True, exist_ok=True)
variant = sys.argv[2] if len(sys.argv) > 2 else "known"
scenario = Scenario(id='browser-native-scenario', name='Authored native bridge browser fixture', category='lane_follow',
    description='Actual shared-kernel and bridge over a mocked world; no CARLA dynamics acceptance', map_name='Town01', duration_s=4,
    seed=17, revision=1, created_at='2026-09-13T00:00:00Z', updated_at='2026-09-13T00:00:00Z')
with pytest.MonkeyPatch.context() as monkeypatch:
    install_live_world(monkeypatch)
    request = live_request(scenario, ground=variant != "unknown").model_copy(update={"execution_id": f"browser-native-{variant}-run"})
    result = CarlaRunner().execute_with_context(request)
scenario = request.scenario
artifacts = []
for index, payload in enumerate(result.artifact_payloads):
    (output_dir / payload.name).write_bytes(payload.content)
    artifact_id = f'browser-native-{variant}-artifact-{index}'
    artifacts.append({'id': artifact_id, 'run_id': request.execution_id, 'name': payload.name, 'kind': payload.kind,
        'media_type': payload.media_type, 'size_bytes': len(payload.content), 'sha256': hashlib.sha256(payload.content).hexdigest(),
        'created_at': scenario.created_at, 'download_url': f'/api/v1/artifacts/{artifact_id}/download',
        'source': 'Authored mocked world; original native bridge and shared-kernel output'})
run = {'id': request.execution_id, 'scenario_id': scenario.id, 'scenario_name': scenario.name, 'scenario_revision': scenario.revision,
    'adapter_id': 'carla', 'status': 'completed', 'verdict': 'failed', 'created_at': scenario.created_at, 'duration_s': scenario.duration_s,
    'execution_ms': 0, 'metrics': [], 'provenance': {'input_hash': request.input_hash, 'output_hash': '2'*64,
    'seed': scenario.seed, 'adapter_version': ADAPTER_VERSION, 'model_fidelity': 'Authored mocked world; actual bridge/kernel contract, not actual CARLA physics'},
    'scenario_snapshot': scenario.model_dump(mode='json'), 'execution_context': request.context.model_dump(mode='json'),
    'trajectory': [frame.model_dump(mode='json') for frame in result.trajectory], 'events': [], 'artifacts': artifacts}
(output_dir / 'browser-run.json').write_text(json.dumps(run), encoding='utf-8')
print(json.dumps({'path': str(output_dir / 'browser-run.json'), 'scope': 'authored mocked world, not CARLA'}))
