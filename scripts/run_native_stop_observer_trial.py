#!/usr/bin/env python3
"""Run one existing physics/stack candidate on an owned, isolated CARLA2300.

This entry never connects to production2000, changes its store, deploys a
controller, attaches a debugger, or restarts an engine. GDB must have launched
the private process. It reuses a verified historical source bundle and saves
new artifacts; neither old outcomes nor metric definitions are overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from native_stop_observer_gdb import validate_owner, BINARY_SHA256


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def save(path, value):
    raw = value if isinstance(value, bytes) else canonical(value)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def qualify_terminal_report(report):
    """A finished computation is not a clean completed experiment."""
    if report.get('world_restored') is not True:
        report['status'] = 'failed'
        report['quality_verdict'] = 'not_evaluated'
    return report


def execute(args):
    root = args.observer_root.resolve()
    selection = None
    if getattr(args, 'model_validation', False):
        if not getattr(args, 'sticky_damping', False):
            raise ValueError('Model selection requires explicit native damping')
        from carla_vehicle_model_validation import validate_selection_files
        selection = validate_selection_files(root)
    bundle = root / 'bundle'
    source_bytes = (bundle / 'candidate-source-manifest.json').read_bytes()
    source_manifest = json.loads(source_bytes)
    for name, expected in source_manifest['files'].items():
        path = bundle / name
        if path.is_symlink() or not path.resolve().is_relative_to(bundle) or digest(path.read_bytes()) != expected:
            raise ValueError('Historical bundle has changed: ' + name)
    candidates = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[1]) == args.debugger_pid and b'-carla-rpc-port=2300' in (proc / 'cmdline').read_bytes().split(b'\0'):
                candidates.append(int(proc.name))
        except (OSError, ValueError):
            continue
    if len(candidates) != 1:
        raise ValueError('Exactly one GDB-owned isolated engine is required')
    owner = validate_owner(candidates[0], args.debugger_pid, root)
    os.environ.update(ADE2E_CARLA_EXCLUSIVE='1', ADE2E_CARLA_HOST='127.0.0.1', ADE2E_CARLA_PORT='2300')
    sys.path[:0] = [str(bundle / 'backend/src'), str(bundle / 'integrations/carla/src')]
    from ade2e.adapters import AdapterExecutionError
    from ade2e.simulation_context import validate_simulation_execution_request
    from ade2e.simulation_evidence import validate_simulation_artifacts
    from axiom_carla.runner import CarlaRunner
    import ade2e, axiom_carla
    for module in (ade2e, axiom_carla):
        if not Path(module.__file__).resolve().is_relative_to(bundle):
            raise ValueError('Imports escaped the verified bundle')
    # Reuse the exact historical request/context, except for the new execution
    # identifier. It is not claimed to have identical hidden initial state.
    original_bytes = (root / 'historical-intent.json').read_bytes()
    original = json.loads(original_bytes)
    if digest(source_bytes) != original['source_manifest_sha256']:
        raise ValueError('Historical source manifest differs from the pinned original intent')
    value = original['request']
    value['execution_id'] = 'native_observer_' + uuid.uuid4().hex
    if selection is not None:
        from carla_vehicle_model_validation import derive_scenario
        from ade2e.schemas import Scenario
        from ade2e.simulation_context import (validate_simulation_context,
            simulation_input_hash, make_simulation_execution_request)
        scenario = Scenario.model_validate(derive_scenario(value['scenario'], selection['case']['case_id']))
        context = validate_simulation_context(scenario, value['context'])
        value = make_simulation_execution_request(execution_id=value['execution_id'], scenario=scenario,
            context=context, input_hash=simulation_input_hash(scenario, 'carla', '0.8.0', context)).model_dump(mode='json')
    request = validate_simulation_execution_request(value)
    output = root / args.label
    output.mkdir(mode=0o700, exist_ok=False)
    intent = {'schema_version': 'carla-isolated-native-stop-intent/1', 'request': value,
        'physics_profile': original['physics_profile'], 'owner': owner,
        'reference_intent_sha256': digest(original_bytes), 'source_manifest_sha256': digest(source_bytes),
        'driver_sha256': digest(Path(__file__).read_bytes()), 'debugger_instrumented': args.instrumented,
        'scope': 'Private engine state observation, not a new production-quality acceptance or wall-time benchmark'}
    if getattr(args, 'sticky_damping', False):
        from sticky_damping_contract import validate_profile
        selected = validate_profile(json.loads((root / 'sticky-profile.json').read_bytes()))
        header = json.loads((root / 'native-sticky-damping.jsonl').open().readline())['event']
        if header['kind'] != 'intervention_loaded' or header['profile'] != selected:
            raise ValueError('Native damping hook is not ready with the pinned profile')
        files = ('sticky_damping_contract.py', 'native_sticky_damping_gdb.py',
                 'native_stop_observer_gdb.py', 'native_physics_clock_gdb.py',
                 'launch_native_stop_observer.py', 'run_native_stop_observer_trial.py')
        if selection is not None:
            files = (*files, 'carla_vehicle_model_validation.py')
        intent['native_intervention'] = {'profile': selected,
            'source_hashes': {name: digest((root / name).read_bytes()) for name in files},
            'write_scope': header['write_scope'], 'production_rollout': False}
        intent['native_candidate_hash'] = digest(canonical({
            'base_input_hash': request.input_hash, 'physics_profile': original['physics_profile'],
            'intervention': intent['native_intervention']}))
        intent['scope'] = 'Private generated-constraint damping experiment; no real-world calibration or production rollout'
    if selection is not None:
        intent['model_validation'] = selection
        intent['model_execution_hash'] = digest(canonical({'native_candidate_hash': intent['native_candidate_hash'],
            'model_sha256': selection['model_sha256'], 'selection_sha256': selection['selection_sha256']}))
    save(output / 'source-intent.json', intent)
    sys.path.insert(0, str(bundle / 'scripts'))
    from run_carla_physics_candidate import world_state
    runner = CarlaRunner()
    _, client, world, version = runner._connect()
    before = world_state(world)
    if version != original['engine_build'] or before['map'] != original['expected_world']['map']:
        raise ValueError('Private map/build differs from the historical experiment')
    if before['settings']['values']['synchronous_mode'] or len(before['actors']) != 23:
        raise ValueError('Private engine is not in the expected empty startup state')
    report = {'schema_version': 'carla-isolated-native-stop-result/1', 'execution_id': request.execution_id,
        'status': 'running', 'intent_sha256': digest(canonical(intent)), 'owner': owner,
        'before': before, 'started_at': datetime.now(timezone.utc).isoformat(),
        'artifacts': [], 'world_restored': False, 'error': None, 'quality_verdict': 'not_evaluated'}
    if selection is not None:
        report.update(model_sha256=selection['model_sha256'], model_execution_hash=intent['model_execution_hash'],
                      validation_case=selection['case']['case_id'])
    result = None
    payloads = []
    started = time.monotonic()

    def deadline(signum, frame):
        signal.alarm(0)
        raise RuntimeError('Isolated trial exceeded240s; cleanup evidence required')

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(240)
    try:
        result = runner.execute_physics_candidate(request, original['physics_profile'])
        payloads = result.artifact_payloads
        validate_simulation_artifacts(request, payloads, completed=True, trajectory=result.trajectory, adapter_version='0.8.0')
        report['status'] = 'completed'
        report['quality_verdict'] = 'passed' if all(m.passed for m in result.metrics) else 'failed'
    except Exception as error:
        if isinstance(error, AdapterExecutionError):
            payloads = error.artifact_payloads
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        try:
            after = world_state(client.get_world())
            for _ in range(20):
                if after == before:
                    break
                time.sleep(.1)
                after = world_state(client.get_world())
            report.update(after=after, world_restored=after == before)
        except Exception as error:
            report['world_check_error'] = str(error)
        qualify_terminal_report(report)
        artifacts = output / 'artifacts'
        artifacts.mkdir()
        for payload in payloads:
            if Path(payload.name).name != payload.name:
                raise ValueError('Unsafe artifact name')
            save(artifacts / payload.name, payload.content)
            report['artifacts'].append({'name': payload.name, 'kind': payload.kind,
                'sha256': digest(payload.content), 'size_bytes': len(payload.content)})
        if result is not None:
            raw = canonical(result.model_dump(mode='json', exclude={'artifact_payloads'}))
            save(output / 'result.json', raw)
            report.update(result_sha256=digest(raw), metrics=[m.model_dump(mode='json') for m in result.metrics])
        report.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_wall_s=time.monotonic() - started)
        save(output / 'report.json', report)
    print(json.dumps({k: report.get(k) for k in ('status', 'execution_id', 'quality_verdict', 'world_restored', 'metrics', 'error')}), flush=True)
    return 0 if report['status'] == 'completed' and report['world_restored'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observer-root', type=Path, required=True)
    parser.add_argument('--debugger-pid', type=int, required=True)
    parser.add_argument('--label', choices=('baseline', 'observed'), required=True)
    parser.add_argument('--instrumented', action='store_true')
    parser.add_argument('--sticky-damping', action='store_true')
    parser.add_argument('--model-validation', action='store_true')
    raise SystemExit(execute(parser.parse_args()))
