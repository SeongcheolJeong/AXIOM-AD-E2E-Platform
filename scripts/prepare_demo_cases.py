#!/usr/bin/env python3
"""Refresh the retained CARLA presenter-case evidence through normal APIs.

This command only reads product APIs and writes a local evidence receipt. It
does not submit experiments, copy databases, rebind presets, or restart engines.
Authentication is read from each workspace's private token file and never saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


SOURCES = {
    'object': ('object-demo-20261009', 8033, 'project_bc937f94c7c94a7884993c074213a86c'),
    'sensor': ('sensor-demo-20261009', 8034, 'project_c653516fae7447e0bd77f53af15c6964'),
    'hil': ('hil-demo-20261010', 8036, 'project_9f48a965d4194be0b18859e334e007bb'),
    'neural': ('neural-demo-20261010', 8040, 'project_c653516fae7447e0bd77f53af15c6964'),
}
CASES = [
    ('object_lead_wait', 'object', ['job_81baf12c08d34a64a0479a9515e6846a', 'job_b3539a82055046cb9c70bafbf4353eb2'],
     {'lead_wait_s': [0, 6]}, 'object-sim', {'mode': 'compare'}),
    ('log_lead_wait', 'object', ['job_b3539a82055046cb9c70bafbf4353eb2', 'job_e88f5bf203d8437da3e6367f2be51d9b'],
     {'lead_wait_s': [6, 2]}, 'log-sim', {'source': 'carla-urban-right-turn', 'variant': 'variant_1edd55ee23facc57a8c5d3fd47a7d02d', 'view': 'compare'}),
    ('object_gap_release', 'object', ['job_68d33d33944f42c980f387999cde986c', 'job_f59b6f152033430facda77854e1905d6'],
     {'rear_speed_mps': [7, 5], 'rear_gap_m': [8, 6]}, 'object-sim', {'mode': 'compare', 'preset': 'gap_release'}),
    ('sensor_driving_fov', 'sensor', ['job_0441911a8a6445cf9c33f6605f6ef1a4'],
     {'front_fov_deg': [90, 70]}, 'sensor-sim', {'preset': 'driving_camera_fov'}),
    ('sensor_lidar_density', 'sensor', ['job_2b98809dcb5343c69afe62c6379cfee7'],
     {'lidar_points_per_second': [32000, 8000]}, 'sensor-sim', {'preset': 'lidar_density'}),
    ('sensor_radar_range', 'sensor', ['job_56d62cd8cc1d4cc5a7571ab515469dea'],
     {'radar_range_m': [80, 30]}, 'sensor-sim', {'preset': 'radar_range'}),
    ('hil_delay_200', 'hil', ['job_3f30f238d95948eda6327fde25c6cc5d'],
     {'delay_ms': 200}, 'hil-sim', {'preset': 'input_delay'}),
    ('hil_delay_400', 'hil', ['job_e3d92d43f0bc4fa6a5dc1c25cc8f17ed'],
     {'delay_ms': 400}, 'hil-sim', {'preset': 'input_delay'}),
    ('hil_dropout_1', 'hil', ['job_eaf27bb3a3cf495b896af70f83fd64c8'],
     {'duration_s': 1}, 'hil-sim', {'preset': 'input_dropout'}),
    ('neural_frozen_sweep', 'neural', ['job_fb3aabe95d254b6db65603ac4de50ca9'],
     {'mode': 'sweep', 'lateral_m': 1.5, 'height_m': 0, 'source_frame_index': 50},
     'neural-sim', {'preset': 'sweep'}),
]

# Reviewed anchors from retained native city/trace artifacts. Sensor anchors are
# selected observation times, not detected safety events. Neural source time is
# fixed and camera-path time has a different clock, so it has no replay anchor.
MOMENTS = {
    'object_lead_wait': [('B 앞차 정지에 반응', 6.45), ('B 재추종', 8.95), ('A 도착', 13.85), ('B 도착', 21.45)],
    'log_lead_wait': [('원본 앞차 정지 반응', 6.45), ('2초 조건 도착', 17.45), ('6초 원본 도착', 21.45)],
    'object_gap_release': [('A 우회 시작', 6.35), ('B 우회 시작', 7.45), ('두 조건 도착', 32.65)],
    'sensor_driving_fov': [('주행 중 같은 상대 시각의 FoV 비교', 10.0)],
    'sensor_lidar_density': [('정지 장면 포인트 밀도 비교', 2.0)],
    'sensor_radar_range': [('정지 장면 관측 범위 비교', 2.0)],
    'hil_delay_200': [('200ms 지연 중 DUT 상태와 차량 응답', 5.0), ('지연 반영', 3.05), ('정상 입력 복구', 7.05)],
    'hil_delay_400': [('지연 반영', 3.05), ('DUT degraded', 3.30), ('정상 입력 복구', 7.05), ('DUT tracking 복귀', 7.15)],
    'hil_dropout_1': [('단절 반영', 3.55), ('DUT degraded', 3.80), ('정상 입력 복구', 4.55), ('DUT tracking 복귀', 4.65)],
    'neural_frozen_sweep': [],
}
OBSERVATIONS = {
    'object_lead_wait': '앞차 대기 0초/6초 조건에서 자차 arrived 상태의 첫 관측은 각각 13.85초/21.45초다. 6초 조건에서는 6.45초부터 앞차 정지 반응이 기록되고 8.95초에 재추종한다. CARLA의 객체 GT 기반 주행 결과다.',
    'log_lead_wait': '저장된 Case에서 앞차 대기를 6초→2초로 변경한 native 재실행이다. 자차 arrived 첫 관측은 원본 21.45초, 변경 17.45초로 4.00초 빠르다. 원본/변경 모두 RGB 720장과 실행 출처를 보존한다.',
    'object_gap_release': '옆 차로 차량 속도 7→5m/s와 초기 간격 8→6m를 함께 바꾼 비교다. 우회 시작은 A 6.35초/B 7.45초지만 arrived 첫 관측은 둘 다 32.65초다. 두 조건이 함께 바뀌었으므로 한 변수의 인과 효과로 해석하지 않는다.',
    'sensor_driving_fov': '실제 센서 readback에서 전방 FoV는 A 90°/B 70°다. 각 조건은 200시점×4센서, 누락 0, 이동 거리 56.85m를 기록했다. 센서 출력은 주행 제어 입력이 아니며 FoV 변경의 행동 효과를 평가한 결과는 아니다.',
    'sensor_lidar_density': 'LiDAR 설정 readback은 A 32,000/B 8,000points/s다. 저장된 정지 장면 100시점에서 실제 반환 점수는 프레임당 평균 1,634.41/408.41개다. 설정 발사율과 관측 반환 점수는 구분한다.',
    'sensor_radar_range': 'Radar 설정 readback은 A 80m/B 30m다. 저장된 정지 장면 100시점의 반환 점수는 프레임당 평균 71.76/13.01개다. 이 장면의 native 관측이며 다른 장면의 탐지 성능을 보장하지 않는다.',
    'hil_delay_200': '200ms 지연은 응답 시각 3.05–7.05초에 반영되고 최대 입력 age는 200ms다. 참조 DUT의 250ms 기준 아래여서 A/B 모두 tracking을 유지했지만, 응답 시각 5.00초의 자차 속도는 A 0.86/B 1.60m/s로 다르다. 각 조건 360개 제어·RGB 기록, 누락 0인 Software DUT와 CARLA 결과다.',
    'hil_delay_400': '400ms 지연은 응답 영상 기준 3.05초부터 반영된다. DUT는 3.30초에 degraded, 7.05초에 recovering, 7.15초에 tracking으로 복귀했다. 별도 프로세스 reference Software DUT와 CARLA 결과다.',
    'hil_dropout_1': '1초 단절은 응답 영상 기준 3.55초부터 반영된다. DUT는 3.80초에 degraded, 4.55초에 recovering, 4.65초에 tracking으로 복귀했다. 실제 ECU나 하드 실시간 검증은 포함하지 않는다.',
    'neural_frozen_sweep': 'CARLA 원본 5.0초의 장면을 고정하고 native Splatfacto로 61개 카메라 시점을 렌더했다. 카메라 경로는 좌우 ±1.5m이고 재생 시간 0–6초 동안 원본 시간은 5.0초로 유지된다. 관측되지 않은 영역의 재구성 품질 한계는 남아 있다.',
}


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Source:
    def __init__(self, runtime_root: Path, name: str):
        directory, port, self.project = SOURCES[name]
        self.origin = f'http://127.0.0.1:{port}'
        self.api = self.origin + '/api/v1'
        self.workspace = runtime_root / directory / 'workspace'
        self.token = (runtime_root / directory / 'access' / 'api-token').read_text().strip()

    def get(self, path: str, expected_sha256: str | None = None, save_as: Path | None = None):
        request = urllib.request.Request(self.api + path,
            headers={'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            expected = expected_sha256 or response.headers.get('X-Content-SHA256')
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Retained API evidence hash mismatch: ' + path)
            if save_as is not None:
                save_as.parent.mkdir(parents=True, exist_ok=True)
                save_as.write_bytes(data)
            return json.loads(data)


def collect(runtime_root: Path, output: Path):
    sources = {key: Source(runtime_root, key) for key in SOURCES}
    receipt = {'schema_version': 'presenter-case-evidence/1',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'access_scope': 'local_presenter_screen_share',
        'rights_scope': 'Existing CARLA code/engine/assets terms remain separate. No nuScenes input.',
        'native_jobs_submitted': 0, 'workspaces': {}, 'items': []}
    executions = {}
    for name, source in sources.items():
        listing = source.get('/experiments/executions?' + urllib.parse.urlencode(
            {'project_id': source.project, 'limit': 100}))
        active = [item for item in listing['items'] if item['status'] in ('queued', 'running', 'cancel_requested')]
        receipt['workspaces'][name] = {'origin': source.origin, 'project_id': source.project,
            'workspace': str(source.workspace.resolve()), 'active_executions': active,
            'live_workers': [item for item in source.get('/workers') if item.get('alive')],
            'execution_resources': source.get('/execution-resources')}
        write(output.parent / 'snapshots' / (name + '-presets.json'),
              source.get(f'/{name}-sim/presets?project_id={source.project}'))
    for identity, name, execution_ids, parameters, page, query in CASES:
        source = sources[name]
        item = {'id': identity, 'workspace': name, 'origin': source.origin, 'project_id': source.project,
            'parameters': parameters, 'pins': [], 'state': 'retained_actual' if execution_ids else 'not_executed'}
        query = {'project': source.project, **query}
        if len(execution_ids) == 2 and page == 'object-sim':
            query.update(left=execution_ids[0], right=execution_ids[1])
        elif len(execution_ids) == 1:
            query['result'] = execution_ids[0]
        item['url'] = source.origin + '/#' + page + '?' + urllib.parse.urlencode(query)
        if not execution_ids:
            item['reason'] = 'No retained 200 ms native run. Configured CARLA2100 must be verified available before explicit submission.'
        for job in execution_ids:
            key = (name, job)
            if key not in executions:
                detail = source.get('/experiments/executions/' + job)
                if detail['project_id'] != source.project:
                    raise ValueError('Execution project does not match the selected workspace')
                source.get('/experiments/executions/' + job + '/export', detail['manifest_hash'],
                           output.parent / 'snapshots' / (job + '-export.json'))
                executions[key] = detail
                write(output.parent / 'snapshots' / (job + '.json'), detail)
                for artifact in detail.get('artifacts', []):
                    if artifact['name'] in ('city-scene.json', 'faulted-hil-trace.json') or artifact['name'].endswith('capture-manifest.json'):
                        source.get('/experiments/executions/' + job + '/artifacts/' + artifact['id'],
                                   artifact['sha256'], output.parent / 'snapshots' / (job + '-' + artifact['name']))
            detail = executions[key]
            if detail['status'] != 'completed' or detail.get('outcome_uncertain'):
                item['state'] = 'unavailable'
            pin = {key: detail.get(key) for key in ('id', 'project_id', 'status', 'adapter_id',
                'adapter_version', 'input_hash', 'manifest_hash', 'fidelity', 'finished_at')}
            pin['summary'] = detail.get('summary')
            pin['artifacts'] = [{key: artifact.get(key) for key in ('id', 'name', 'sha256', 'size_bytes')}
                                for artifact in detail.get('artifacts', [])]
            item['pins'].append(pin)
        if identity == 'log_lead_wait':
            case = source.get('/log-sim/sources/carla-urban-right-turn/cases/case_6de43896bdd246d582e186eaaf21f8d9?project_id=' + source.project)
            write(output.parent / 'snapshots' / 'log-case.json', case)
            item['case_ref'] = {key: case.get(key) for key in ('id', 'revision', 'manifest_hash')}
        receipt['items'].append(item)
    receipt['counts'] = {'curated_cases': len(receipt['items']),
        'retained_actual_cases': sum(item['state'] == 'retained_actual' for item in receipt['items']),
        'not_executed_cases': sum(item['state'] == 'not_executed' for item in receipt['items']),
        'unique_execution_snapshots': len(executions),
        'independent_road_scenes': 'not inferred from case, execution, or frame count'}
    write(output, receipt)
    # Each local API owns its own data and credentials. Never merge databases or
    # treat an identically named project in another workspace as local evidence.
    for name, source in sources.items():
        cases = []
        for item in receipt['items']:
            if item['workspace'] != name or item['state'] != 'retained_actual':
                continue
            roles = ['left', 'right'] if len(item['pins']) == 2 else ['result']
            curated = {'id': item['id'], 'executions': [
                {'role': role, **{key: pin[key] for key in ('id', 'input_hash', 'manifest_hash')}}
                for role, pin in zip(roles, item['pins'])],
                'key_moments': [{'label': label, 'time_s': at} for label, at in MOMENTS[item['id']]],
                'observation': OBSERVATIONS[item['id']]}
            if item['id'] == 'log_lead_wait':
                curated['log_variant_id'] = 'variant_1edd55ee23facc57a8c5d3fd47a7d02d'
            cases.append(curated)
        write(output.parent / (name + '-curation.json'), {'schema_version': 'demo-case-curation/1',
            'projects': [{'project_id': source.project, 'cases': cases}]})
    return receipt['counts']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path('.runtime'))
    parser.add_argument('--output', type=Path, default=Path('.runtime/demo-cases-20261010/evidence.json'))
    args = parser.parse_args()
    print(json.dumps(collect(args.runtime_root, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
