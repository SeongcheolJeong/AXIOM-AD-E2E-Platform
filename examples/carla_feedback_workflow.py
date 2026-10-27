"""Preview a bounded CARLA feedback experiment; submit only with --submit.

Uses the normal versioned API/SDK. Remote simulator paths remain operator
configuration; the definition contains no SSH host, credentials or script path.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from axiom_sdk import AxiomClient


def definition(project_id: str = 'default') -> dict:
    camera = {'key': 'front', 'kind': 'rgb', 'translation_m': [1.5, 0, 1.8],
              'rotation_deg': [0, 0, 0], 'rotation_order': 'pitch_yaw_roll_right_hand_rule', 'parameters': {}}
    return {'project_id': project_id, 'name': 'CARLA map-reference feedback and RGB',
            'kind': 'sensor_capture', 'adapter_id': 'carla-feedback-sensor', 'comparison_kind': 'single', 'seed': 42,
            'input': {'schema_version': 'carla-feedback-sensor-spec/1', 'duration_seconds': 4, 'sample_hz': 10,
                      'spawn_index': 0, 'vehicle_blueprint': 'vehicle.tesla.model3',
                      'expected_map': 'Town10HD_Opt', 'expected_server_version': '1ae5356',
                      'configuration_revision': 1, 'coordinate_convention': 'right_handed_x_forward_y_left_z_up',
                      'controller': {'id': 'native-map-lane-follow/1', 'target_speed_mps': 3,
                                     'lookahead_m': 6, 'route_length_m': 60}, 'sensors': [camera]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8021')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--project', default='default')
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--idempotency-key')
    args = parser.parse_args()
    if args.submit and not args.idempotency_key:
        parser.error('--submit requires an explicit --idempotency-key')
    with AxiomClient(base_url=args.api_url, token=args.token_file.read_text().strip()) as client:
        payload = definition(args.project)
        preview = client.preview_experiment(**payload)
        print(json.dumps({'preview_hash': preview['preview_hash'], 'resource_id': preview['resource_id'],
                          'workload': preview['prepared']['input']['workload'],
                          'evaluation_status': preview['evaluation_status']}, indent=2))
        if args.submit:
            experiment = client.create_experiment(idempotency_key=args.idempotency_key, **payload)
            result = client.submit_experiment(experiment['id'], preview_hash=experiment['preview_hash'],
                                             idempotency_key=args.idempotency_key)
            print(json.dumps({'experiment_id': experiment['id'], 'execution_id': result['id'], 'status': result['status']}))


if __name__ == '__main__':
    main()
