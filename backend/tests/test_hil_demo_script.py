"""Offline receipt recovery; no native execution or API server is started."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_binding_response_loss_resumes_same_result_without_resubmit(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('hil_demo_rehearsal_test', scripts / 'hil_sim_demo.py')
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    source = dict(scenario_ref=dict(id='scenario_demo', revision=1, configuration_hash='a' * 64, manifest_hash='b' * 64),
                  adapter_id='carla-software-dut', adapter_version='1.0.0', seed=42, saved_execution_id=None)
    args = SimpleNamespace(api='http://unused/api/v1', prepared=tmp_path / 'prepared.json',
                           output=tmp_path / 'rehearsal.json', timeout=1)
    demo.write(args.prepared, dict(complete=True, target_api=args.api, project_id='project_demo',
        presets={'input_delay': {'source': source}}))
    demo.write(args.output, dict(schema_version='hil-sim-demo-rehearsal/1', id='rehearsal_same',
        target_api=args.api, project_id='project_demo', complete=False,
        trials=[dict(preset='input_delay', experiment_id='experiment_existing', execution_id='job_existing')]))
    current = dict(id='input_delay', ready=True, binding_revision=1, source=copy.deepcopy(source))
    calls = []

    class FakeApi:
        def request(self, path, body=None, key=None):
            calls.append((path, body))
            if path == '/experiments/executions/job_existing':
                return dict(id='job_existing', status='completed', outcome_uncertain=False)
            if path.startswith('/hil-sim/presets?'):
                return {'items': [copy.deepcopy(current)]}
            if path == '/hil-sim/presets/input_delay/bindings':
                assert body['saved_execution_id'] == 'job_existing' and body['expected_revision'] == 1
                current.update(binding_revision=2, source={**source, 'saved_execution_id': 'job_existing'})
                raise ConnectionError('Binding committed, response lost')
            pytest.fail('Resume attempted an unexpected request or native resubmit: ' + path)

    monkeypatch.setattr(demo, 'connection', lambda _: FakeApi())
    with pytest.raises(ConnectionError, match='response lost'):
        demo.rehearse(args)
    assert not json.loads(args.output.read_text())['trials'][0].get('bound')
    result = demo.rehearse(args)
    receipt = json.loads(args.output.read_text())
    assert result['complete'] and receipt['trials'][0]['bound']
    assert receipt['trials'][0]['saved_binding_revision'] == 2
    assert sum(path.endswith('/bindings') for path, _ in calls) == 1
    assert receipt['trials'][0]['execution_id'] == 'job_existing'
    # A completed local receipt must not hide another user's later rebind.
    current['source']['saved_execution_id'] = 'job_other'
    with pytest.raises(ValueError, match='Preset changed'):
        demo.rehearse(args)
