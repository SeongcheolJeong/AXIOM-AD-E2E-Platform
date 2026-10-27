"""Read-only Sensor Sim original downloads through the normal versioned SDK.

No simulator dispatch, scene reconstruction or physical validation is performed.
Set AXIOM_API_TOKEN outside this script; do not pass credentials on the command line.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from axiom_sdk import AxiomClient


def investigate(client: AxiomClient, execution_id: str, destination: Path):
    detail = client.experiment_execution(execution_id)
    if (detail.get('id') != execution_id or detail.get('status') != 'completed'
            or detail.get('experiment_kind') != 'sensor_capture' or not detail.get('manifest_hash')):
        raise ValueError('Select a completed, sealed Sensor Sim execution')
    summary = detail.get('summary') or {}
    if summary.get('schema_version') == 'carla-feedback-sensor-result/1':
        names = ['feedback-capture-manifest.json', 'feedback-trace.json']
        if summary.get('scene_artifact') is not None:
            if summary.get('scene_artifact') != 'sensor-scene.json' or summary.get('map_artifact') != 'map.xodr':
                raise ValueError('Unsupported original scene/map pointers')
            names += ['sensor-scene.json', 'map.xodr']
    elif summary.get('schema_version') == 'sensor-experiment-result/1':
        stages = summary.get('captures')
        if not isinstance(stages, dict) or not stages or not set(stages) <= {'single', 'rig_a', 'rig_b'}:
            raise ValueError('Unsupported native capture stages')
        names = [stage + '-capture-manifest.json' for stage in sorted(stages)]
    else:
        raise ValueError('Unsupported Sensor Sim original contract')
    artifacts = []
    for name in names:
        matches = [item for item in detail['artifacts'] if item['name'] == name]
        if len(matches) != 1:
            raise ValueError('Original artifact is missing or ambiguous: ' + name)
        artifacts.append(matches[0])
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    export = client.export_experiment_execution(execution_id, destination / 'execution.json')
    sealed = json.loads(export.read_bytes())
    if sealed.get('execution') != detail | {'manifest_hash': None, 'download_url': None}:
        raise ValueError('Selected execution differs from its sealed export')
    for item in artifacts:
        client.download_experiment_artifact(execution_id, item['id'], destination / item['name'])
    return {'execution_id': execution_id, 'manifest_hash': detail['manifest_hash'],
            'fidelity': detail['fidelity'], 'evaluation_status': summary.get('evaluation_status'),
            'downloaded_originals': names, 'engine_dispatches': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8021')
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get('AXIOM_API_TOKEN')
    if not token:
        raise SystemExit('Set AXIOM_API_TOKEN in your environment')
    with AxiomClient(args.base_url, token=token) as client:
        print(json.dumps(investigate(client, args.execution_id, args.output_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
