"""Offline model-suite review: immutable inputs, original metrics and behavior.

No captured source is imported. A missing/failed case remains in the preregistered
denominator, and metric success is not a claim that a cruise task was completed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_sticky_damping import trial
from carla_vehicle_model_validation import (CASES, EXECUTION_FILES, canonical, digest,
    derive_scenario, evaluate_behavior, read_json, validate_selection_files)


def verify_binding(root, planned, reviewed):
    root = Path(root)
    selection = validate_selection_files(root)
    if selection != planned:
        raise ValueError('Executed selection differs from the preregistered suite')
    intent, _ = read_json(root / 'observed/source-intent.json')
    report, _ = read_json(root / 'observed/report.json')
    launch, _ = read_json(root / 'launcher-result.json')
    reference, _ = read_json(root / 'historical-intent.json')
    if intent.get('model_validation') != selection or launch.get('model_validation') != selection:
        raise ValueError('Model selection is missing from the launch or execution')
    if report.get('model_sha256') != selection['model_sha256']:
        raise ValueError('Result is not bound to its model revision')
    binding = {'native_candidate_hash': intent['native_candidate_hash'],
               'model_sha256': selection['model_sha256'], 'selection_sha256': selection['selection_sha256']}
    expected = digest(canonical(binding))
    if report.get('model_execution_hash') != expected or intent.get('model_execution_hash') != expected:
        raise ValueError('Model execution binding differs')
    if (intent['native_intervention']['source_hashes'] != selection['model']['execution_source_hashes']
            or intent['physics_profile'] != selection['model']['physics_profile']
            or intent['source_manifest_sha256'] != selection['model']['source_manifest_sha256']):
        raise ValueError('Declared model implementation was not used')
    request = intent['request']
    case_id = selection['case']['case_id']
    if request['scenario'] != derive_scenario(reference['request']['scenario'], case_id):
        raise ValueError('Scenario differs from its declared case')
    if request['context'] != reference['request']['context']:
        raise ValueError('Controller, perception, policy or sensor definition changed')
    input_value = {'schema_version': 'simulation-input/3', 'scenario': request['scenario'],
                   'adapter_id': 'carla', 'adapter_version': '0.8.0', 'context': request['context']}
    if request['input_hash'] != digest(canonical(input_value)):
        raise ValueError('Derived scenario input hash differs')
    metrics = {m['name']: m for m in reviewed['native']['metrics']}
    for name, contract in selection['case']['criteria'].items():
        if any(metrics[name][key] != value for key, value in contract.items()):
            raise ValueError('Original criterion was relaxed')
    if ('passed' if all(m['passed'] for m in reviewed['native']['metrics']) else 'failed') != report['quality_verdict']:
        raise ValueError('Original quality verdict contradicts metrics')
    return selection


def analyze(root):
    root = Path(root)
    suite, raw = read_json(root / 'suite.json')
    if digest(raw) != (root / 'suite.sha256').read_text():
        raise ValueError('Suite changed after registration')
    planned_keys = [case + '-' + label for case in CASES for label in ('sham', 'candidate')]
    if [row['key'] for row in suite['cases']] != planned_keys:
        raise ValueError('Suite case denominator changed')
    cases, details = [], {}
    for planned in suite['cases']:
        key = planned['key']; folder = root / key; cap = folder / 'capture'
        brief = {'key': key, 'case_id': planned['selection']['case']['case_id'],
                 'label': key.rsplit('-', 1)[-1],
                 'model_id': planned['selection']['model']['model_id'],
                 'model_sha256': planned['selection']['model_sha256'],
                 'status': 'not_executed', 'metric_verdict': 'not_evaluated',
                 'behavior_verdict': 'not_evaluated', 'acceptance_verdict': 'not_evaluated'}
        cases.append(brief)
        report_path = cap / 'observed/report.json'
        if not report_path.is_file():
            if (folder / 'launch-output.log').exists():
                brief['status'] = 'started_outcome_not_recovered'
            continue
        report, _ = read_json(report_path)
        brief['execution_id'] = report['execution_id']
        if report['status'] != 'completed':
            brief.update(status='execution_failed', error=report.get('error'))
            continue
        inventory, _ = read_json(cap / 'capture-hashes.json')
        for name, expected in inventory.items():
            path = cap / name
            if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                    or digest(path.read_bytes()) != expected):
                raise ValueError('Recovered capture identity mismatch')
        sham = root / (brief['case_id'] + '-sham') / 'capture/observed'
        # The self-reference is only used if the preregistered sham never
        # completed; such a condition will not receive a paired-comparison flag.
        baseline = sham if (sham / 'result.json').exists() else cap / 'observed'
        reviewed = trial(cap, baseline)
        selected = verify_binding(cap, planned['selection'], reviewed)
        policy = reviewed['request']['context']['stack_snapshot']['definition']['policy']['parameters']
        behavior = evaluate_behavior(selected['case'], reviewed['native']['rows'], policy)
        metric_verdict = reviewed['native']['quality_verdict']
        brief.update(status='verified', metric_verdict=metric_verdict,
                     behavior_verdict=behavior['verdict'], behavior=behavior,
                     acceptance_verdict='passed' if metric_verdict == behavior['verdict'] == 'passed' else 'failed',
                     metrics=reviewed['native']['metrics'], mode_counts=reviewed['mode_counts'],
                     model_execution_hash=report['model_execution_hash'],
                     source_manifest_sha256=reviewed['source_manifest_sha256'],
                     initial_speed_mps=reviewed['native']['rows'][0]['speed_mps'],
                     final_speed_mps=reviewed['terminal_speed_mps'],
                     damping_fields_verified=reviewed['intervention']['forward_rows'],
                     report_sha256=reviewed['report_sha256'], all_samples_included=True)
        details[key] = reviewed
    pairs = []
    for case_id in CASES:
        a, b = (details.get(case_id + '-' + label) for label in ('sham', 'candidate'))
        pair = {'case_id': case_id, 'status': 'incomplete_pair'}
        if a and b:
            clean = lambda r: {k: v for k, v in r['request'].items() if k != 'execution_id'}
            if (clean(a) != clean(b) or a['source_manifest_sha256'] != b['source_manifest_sha256']
                    or a['physics_profile'] != b['physics_profile']):
                raise ValueError('Matched case differs beyond the model damping')
            pair.update(status='same_authored_inputs_except_native_damping',
                        initial_speed_difference_mps=b['native']['rows'][0]['speed_mps']-a['native']['rows'][0]['speed_mps'])
        pairs.append(pair)
    candidate_cases = [c for c in cases if c['label'] == 'candidate']
    ready = all(c['status'] == 'verified' and c['acceptance_verdict'] == 'passed' for c in candidate_cases)
    return {'schema_version': 'carla-private-model-validation-analysis/1',
            'suite_sha256': digest(raw), 'cases': cases, 'pairs': pairs, 'details': details,
            'planned_executions': len(cases), 'verified_executions': len(details),
            'commands': 80 * len(details), 'observations': 81 * len(details),
            'candidate_metric_passes': sum(c['metric_verdict'] == 'passed' for c in candidate_cases),
            'candidate_behavior_passes': sum(c['behavior_verdict'] == 'passed' for c in candidate_cases),
            'candidate_accepted_cases': sum(c['acceptance_verdict'] == 'passed' for c in candidate_cases),
            'candidate_denominator': len(candidate_cases),
            'suite_acceptance': 'passed' if ready else 'not_accepted',
            'production_rollout': False, 'real_world_calibration': False,
            'unsupported_cases': suite['unsupported_cases'],
            'scope': 'Private GDB-only model validation; no normal worker capability or statistical acceptance claim.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = analyze(args.root)
    with args.output.open('xb') as f:
        f.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode())
    print(json.dumps({k: v for k, v in result.items() if k not in ('details', 'cases')}, ensure_ascii=False))
    print(json.dumps(result['cases'], ensure_ascii=False))
