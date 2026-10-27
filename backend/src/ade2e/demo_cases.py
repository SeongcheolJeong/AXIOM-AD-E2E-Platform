"""Read-only questions over explicitly pinned, sealed project evidence.

No adapter registry, live preflight, latest-execution discovery or credentials.
The optional local curation manifest is read once when the app is registered.
"""
import copy
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from fastapi import HTTPException, Request
from sqlalchemy import select
from . import experiment_jobs, object_demo, sensor_demo, hil_demo, neural_demo
from .demo_case_data import TEMPLATES
from .demo_case_schemas import DemoCaseCuration
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from .job_models import JobRow
from .scenario_v2 import get_scenario
from .service import digest
from .team_access import project_authorize

VERSION = 'demo-cases/1'
CURATION_ENV = 'AXIOM_DEMO_CASES_MANIFEST'
_IDS = {item['id'] for item in TEMPLATES}
_BINDINGS = {
    'object_lead_wait': (object_demo, 'urban_right_turn'),
    'object_gap_release': (object_demo, 'gap_release'),
    'sensor_driving_fov': (sensor_demo, 'driving_camera_fov'),
    'sensor_lidar_density': (sensor_demo, 'lidar_density'),
    'sensor_radar_range': (sensor_demo, 'radar_range'),
    'hil_dropout_1': (hil_demo, 'input_dropout'),
    'neural_frozen_sweep': (neural_demo, 'sweep'),
}


def _curation(path):
    if not path:
        return {}, None
    try:
        source = Path(path)
        if source.is_symlink() or source.stat().st_size > 1024 * 1024:
            raise ValueError('Unbounded or linked curation file')
        model = DemoCaseCuration.model_validate_json(source.read_bytes())
        if any(case.id not in _IDS for project in model.projects for case in project.cases):
            raise ValueError('Unknown case template')
        return {project.project_id: {case.id: case.model_dump(mode='json') for case in project.cases}
                for project in model.projects}, None
    except (OSError, ValueError):
        return {}, '로컬 사례 고정 파일의 형식이나 무결성을 확인할 수 없습니다.'


def _resolve(session, db, project, identity, pin=None):
    subject = session.get(ExperimentExecutionRow, identity)
    row = session.scalar(select(JobRow).where(JobRow.id == identity))
    if subject is None or subject.project_id != project or row is None:
        raise HTTPException(404, '이 프로젝트에 고정한 실행이 없습니다.')
    detail = experiment_jobs.detail(session, row).model_dump(mode='json')
    if detail['status'] != 'completed' or detail['outcome_uncertain'] or not detail['manifest_hash']:
        raise HTTPException(422, '완료되고 결과가 확정된 봉인 실행이 필요합니다.')
    if pin and any(detail[key] != pin[key] for key in ('input_hash', 'manifest_hash')):
        raise HTTPException(409, '고정한 입력 또는 결과 manifest hash가 일치하지 않습니다.')
    # Listing is bounded metadata verification; downloads independently hash bytes.
    root = db.experiment_dir / 'artifacts'
    for artifact in session.scalars(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id == identity)):
        path = root / artifact.storage_key
        if (path.is_symlink() or path.parent.resolve() != root.resolve() or not path.is_file()
                or path.stat().st_size != artifact.size_bytes):
            raise HTTPException(409, '보존 원본 파일이 없거나 크기·저장 경계가 다릅니다.')
    return dict(detail=detail, spec=row.prepared['spec'], snapshot=row.prepared['input'])


def _pin(value, role):
    return {key: value['detail'][key] for key in ('id', 'input_hash', 'manifest_hash')} | {'role': role}


def _bound(session, db, project, identity):
    if identity not in _BINDINGS:
        return None, None
    module, preset = _BINDINGS[identity]
    binding = module._binding(session, project, preset)
    if not binding:
        return None, None
    meta = dict(kind=module.KIND, id=preset, revision=binding['revision'], sha256=binding['sha256'])
    definition = binding['definition']
    saved = definition.get('saved_execution_id')
    if not saved:
        return None, meta
    if module is object_demo:
        object_demo._bound_scenario(session, project, preset, definition)
        object_demo._validate_saved(session, project, definition, preset)
    elif module is sensor_demo:
        sensor_demo._validate_saved(session, project, preset, definition)
    elif module is hil_demo:
        hil_demo._bound_source(session, project, definition)
        hil_demo._validate_saved(session, project, preset, definition)
    values = []
    if identity in ('object_lead_wait', 'object_gap_release'):
        if not definition.get('saved_comparison_execution_id'):
            return None, meta
        values.append(_pin(_resolve(session, db, project, definition['saved_comparison_execution_id']), 'left'))
        values.append(_pin(_resolve(session, db, project, saved), 'right'))
    else:
        values.append(_pin(_resolve(session, db, project, saved), 'result'))
    return dict(id=identity, executions=values, key_moments=[]), meta


def _family(value, label):
    return dict(id='source_' + digest(value), label=label, kind='synthetic_carla')


def _city_family(scenario):
    # Deliberately group control variants of the same authored route/scene.
    geometry = {key: scenario.get(key) for key in ('map', 'coordinate_system', 'environment')}
    geometry['actors'] = [{key: actor.get(key) for key in ('id', 'role', 'asset', 'pose', 'route', 'route_mode')}
                          for actor in sorted(scenario['actors'], key=lambda actor: actor['id'])]
    return _family(geometry, 'CARLA 객체 경로·배치 묶음')


def _capture_family(spec):
    baseline = copy.deepcopy(spec['input']['rig_a'])
    baseline.pop('sensors', None)
    baseline.pop('rig_revision', None)
    return _family(baseline, 'CARLA 동일 캡처 장면·주행 묶음')


def _lead_wait(scenario):
    lead = next((actor for actor in scenario['actors'] if actor['id'] == 'lead'), None)
    if lead is None:
        raise ValueError('앞차 lead가 없습니다.')
    actions = lead.get('behavior', {}).get('actions', [])
    waits = [action for action in actions if action.get('id') == 'lead_wait']
    if not waits:
        return 0
    if len(waits) != 1 or waits[0].get('kind') != 'wait' or waits[0]['duration_s'] <= 0:
        raise ValueError('0초 기준은 앞차 대기 행동이 없어야 합니다.')
    return waits[0]['duration_s']


def _object(session, project, identity, resolved):
    if set(resolved) != {'left', 'right'}:
        raise ValueError('객체 사례에는 고정된 A/B 쌍이 필요합니다.')
    for value in resolved.values():
        if value['detail']['adapter_id'] != 'carla-city-object' or value['detail']['fidelity'] != 'native_carla_object_physics':
            raise ValueError('외부 사례에는 native CARLA 객체 실행만 연결합니다.')
        summary = value['detail']['summary'] or {}
        artifacts = {artifact['name']: artifact for artifact in value['detail']['artifacts']}
        if (summary.get('schema_version') != 'carla-city-result/1'
                or summary.get('execution_status') != 'completed' or summary.get('cleanup_status') != 'confirmed'
                or not {'city-scene.json', 'city-evaluation.json', 'map.xodr'}.issubset(artifacts)
                or summary.get('scene_sha256') != artifacts['city-scene.json']['sha256']):
            raise ValueError('완료된 CARLA 장면·지도·평가 원본 근거가 부족합니다.')
    pair = object_demo._comparison(session, project, resolved['left']['detail']['id'], resolved['right']['detail']['id'])
    if not pair['compatibility']['compatible']:
        raise ValueError('고정 A/B의 지도·seed·adapter 또는 제어 빌드가 다릅니다.')
    if identity == 'object_gap_release':
        allowed = ('/actors/rear/speed_mps', '/actors/rear/pose/x', '/actors/rear/pose/y',
                   '/actors/rear/route', '/actors/rear/destination', '/actors/rear/behavior/planner/target_speed_mps')
        if not pair['changes'] or any(change['path'] not in allowed for change in pair['changes']):
            raise ValueError('뒤차 속도·초기 간격 외의 설정도 바뀐 실행입니다.')
        for side, speed, gap in [('left', 7, 8), ('right', 5, 6)]:
            actors = {actor['id']: actor for actor in pair[side]['scenario']['actors']}
            ego, rear = actors['ego'], actors['rear']; heading = ego['pose']['heading_rad']
            actual_gap = (ego['pose']['x']-rear['pose']['x'])*math.cos(heading) + (ego['pose']['y']-rear['pose']['y'])*math.sin(heading)
            if rear['speed_mps'] != speed or not math.isclose(actual_gap, gap, abs_tol=1e-5):
                raise ValueError('뒤차 속도·초기 간격이 사례의 표시값과 다릅니다.')
        left_rear = next(actor for actor in pair['left']['scenario']['actors'] if actor['id'] == 'rear')
        right_rear = next(actor for actor in pair['right']['scenario']['actors'] if actor['id'] == 'rear')
        route_a, route_b = left_rear['route'], right_rear['route']
        if (not route_b or len(route_b) > len(route_a) or route_a[-len(route_b):] != route_b
                or route_b[0] != right_rear['pose']
                or any(actor.get('destination') not in (None, route_a[-1]) for actor in (left_rear, right_rear))):
            raise ValueError('변경한 뒤차 경로가 기존 경로·목적지를 유지하지 않습니다.')
        return _city_family(pair['left']['scenario']), pair['left']['scenario']['duration_s']
    expected = (0, 6) if identity == 'object_lead_wait' else (6, 2)
    if tuple(_lead_wait(pair[side]['scenario']) for side in ('left', 'right')) != expected:
        raise ValueError('앞차 대기 시간이 이 사례의 질문과 다릅니다.')
    changes = pair['changes']
    allowed = {'/actors/lead/behavior/actions/lead_wait'} if identity == 'object_lead_wait' else {'/actors/lead/behavior/actions/lead_wait/duration_s'}
    if not changes or any(change['path'] not in allowed for change in changes):
        raise ValueError('앞차 대기 외의 설정도 바뀐 실행입니다.')
    return _city_family(pair['left']['scenario']), pair['left']['scenario']['duration_s']


def _sensor(session, project, identity, value):
    preset = _BINDINGS[identity][1]
    spec = value['spec']
    source = dict(capture_spec=spec['input']['rig_a'], adapter_version=spec['adapter_version'],
                  seed=spec['seed'], saved_execution_id=value['detail']['id'])
    sensor_demo._validate_saved(session, project, preset, source)
    return _capture_family(spec), spec['input']['rig_a']['duration_seconds']


def _hil(session, project, identity, value):
    spec, snapshot, detail = value['spec'], value['snapshot'], value['detail']
    fault = dict(kind='drop', start_s=3.5, duration_s=1, delay_ms=0) if identity == 'hil_dropout_1' else dict(
        kind='delay', start_s=3, duration_s=4, delay_ms=200 if identity == 'hil_delay_200' else 400)
    if (detail['experiment_kind'] != 'hil_demo' or detail['adapter_id'] != hil_demo.ADAPTER
            or detail['fidelity'] != hil_demo.FIDELITY or spec['input'].get('fault') != fault
            or snapshot.get('schema_version') != 'carla-software-dut-experiment/1'
            or snapshot.get('fault') != fault or spec['comparison_kind'] != 'same_scene_fault_ab'):
        raise ValueError('Software DUT 또는 고장 입력이 사례와 다릅니다.')
    source = get_scenario(session, spec['input']['scenario_id'], spec['input']['scenario_revision'])
    ref = {key: source[key] for key in ('id', 'revision', 'configuration_hash', 'manifest_hash')}
    definition = dict(scenario_ref=ref, adapter_id=hil_demo.ADAPTER, seed=spec['seed'])
    hil_demo._bound_source(session, project, definition)
    hil_demo._snapshot_source(snapshot, definition)
    summary = detail['summary'] or {}
    names = {artifact['name'] for artifact in detail['artifacts']}
    required = {f'{side}-{suffix}' for side in ('baseline', 'faulted') for suffix in ('city-scene.json', 'hil-trace.json')}
    if (summary.get('schema_version') != 'hil-demo-result/1' or summary.get('execution_target') != 'software_dut'
            or summary.get('fault') != fault or set(summary.get('sides', {})) != {'baseline', 'faulted'}
            or not required.issubset(names) or any(side.get('cleanup_status') != 'confirmed'
                or side.get('execution_status') != 'completed' for side in summary['sides'].values())):
        raise ValueError('완료된 Software DUT A/B 원본 근거가 부족합니다.')
    return _city_family(source['scenario']), source['scenario']['duration_s']


def _neural(session, db, project, value):
    detail, spec, snapshot = value['detail'], value['spec'], value['snapshot']
    if (detail['experiment_kind'] != 'neural_render' or detail['adapter_id'] != neural_demo.ADAPTER
            or detail['fidelity'] != neural_demo.FIDELITY or spec['input'].get('mode') != 'camera_sweep'):
        raise ValueError('외부 사례에는 CARLA 원본의 고정 시각 Neural sweep만 연결합니다.')
    source_id = snapshot['source_pin']['execution_id']
    reconstruction_id = snapshot['reconstruction_pin']['execution_id']
    _resolve(session, db, project, source_id, snapshot['source_pin'])
    _resolve(session, db, project, reconstruction_id, snapshot['reconstruction_pin'])
    source = neural_demo._resolve(session, db, project, source_id)
    reconstruction = neural_demo._resolve(session, db, project, reconstruction_id, 'neural_reconstruction')
    neural_demo._source(source)
    if source['kind'] != 'sensor_capture' or source['fidelity'] != neural_demo.SOURCE_FIDELITY:
        raise ValueError('실도로·nuScenes 소스는 외부 사례 모음에서 제외합니다.')
    neural_demo._reconstruction(reconstruction, source)
    summary = detail['summary'] or {}
    if (snapshot['source_pin'] != neural_demo.execution_pin(source)
            or snapshot['reconstruction_pin'] != neural_demo.execution_pin(reconstruction)
            or spec['input'].get('reconstruction_execution_id') != reconstruction_id
            or summary.get('schema_version') != 'neural-render-summary/2'
            or any(summary.get(key) != val for key, val in spec['input'].items())
            or summary.get('source_execution_id') != source_id
            or not {'neural-render-result.json', 'neural-render-frames.zip'}.issubset({a['name'] for a in detail['artifacts']})):
        raise ValueError('Neural sweep의 원본·checkpoint·렌더 입력 연결이 다릅니다.')
    return _capture_family(source['spec']), source['spec']['input']['rig_a']['duration_seconds']


def _href(page, project, identity, **params):
    return '#' + page + '?' + urlencode(dict(project=project, demo_case=identity, **params))


def _links(session, project, template, pins, resolved):
    identity, module = template['id'], template['module']
    links = []
    if module in ('object_sim', 'log_sim'):
        params = dict(mode='compare', left=resolved['left']['detail']['id'], right=resolved['right']['detail']['id']) if 'left' in resolved else dict(execution=resolved['result']['detail']['id'])
        primary = dict(label='저장 결과 열기', href=_href('object-sim', project, identity, **params))
        if module == 'log_sim':
            from . import log_sim
            links.append(dict(label='원본 로그·Case 조사', href=_href('log-sim', project, identity, source=log_sim.NATIVE_ID)))
            variant_id = pins.get('log_variant_id')
            if variant_id:
                variant = log_sim._catalog(session, project, log_sim.VARIANT_KIND, variant_id)
                baseline = log_sim._native_source(session, project)
                if (not variant or variant['definition'].get('execution_id') != resolved['right']['detail']['id']
                        or variant['definition'].get('source_id') != log_sim.NATIVE_ID
                        or baseline['baseline_execution_id'] != resolved['left']['detail']['id']):
                    raise ValueError('Log Sim 변형 링크가 고정 실행과 다릅니다.')
                primary = dict(label='원본·변경 로그 비교', href=_href('log-sim', project, identity, source=log_sim.NATIVE_ID, variant=variant_id, view='compare'))
    else:
        page = {'sensor_sim': 'sensor-sim', 'hil': 'hil-sim', 'neural_sim': 'neural-sim'}[module]
        params = dict(result=resolved['result']['detail']['id'])
        if module == 'sensor_sim': params.update(mode='demo', preset=_BINDINGS[identity][1])
        elif module == 'neural_sim': params.update(preset='sweep')
        else: params.update(preset='input_dropout' if identity == 'hil_dropout_1' else 'input_delay', value=str(template['change']['after']))
        primary = dict(label='저장 결과 열기', href=_href(page, project, identity, **params))
    return primary, links


def _item(session, db, project, template, curated, curation_error):
    result = copy.deepcopy(template) | dict(ready=False, status='unavailable', reason='이 프로젝트에 이 질문의 완료 결과가 고정되지 않았습니다.',
        source_family=None, executions=[], primary_link=None, links=[], key_moments=[], binding=None,
        observation_source='investigation_guidance')
    try:
        if curation_error:
            raise ValueError(curation_error)
        pins = curated.get(template['id'])
        if pins is None:
            pins, result['binding'] = _bound(session, db, project, template['id'])
        if pins is None:
            return result
        resolved = {pin['role']: _resolve(session, db, project, pin['id'], pin) for pin in pins['executions']}
        module = template['module']
        if module in ('object_sim', 'log_sim'):
            family, duration = _object(session, project, template['id'], resolved)
        else:
            if set(resolved) != {'result'}:
                raise ValueError('이 사례는 봉인된 단일 A/B 또는 sweep 결과를 요구합니다.')
            value = resolved['result']
            if module == 'sensor_sim': family, duration = _sensor(session, project, template['id'], value)
            elif module == 'hil': family, duration = _hil(session, project, template['id'], value)
            else: family, duration = _neural(session, db, project, value)
        primary, links = _links(session, project, template, pins, resolved)
        moments = []
        for moment in pins.get('key_moments', []):
            if moment['time_s'] > duration:
                raise ValueError('고정한 조사 시각이 원본 실행 구간을 벗어납니다.')
            if module == 'neural_sim':
                # Camera sweep is a fixed source frame, not a simulation replay.
                raise ValueError('Neural sweep에는 재생 시각 링크 대신 고정 렌더 입력을 사용합니다.')
            moments.append(moment | dict(clock='simulation', href=primary['href'] + '&' + urlencode({'time_s': moment['time_s']})))
        executions = []
        for role, value in resolved.items():
            d = value['detail']; source = value['snapshot'].get('source', value['snapshot'].get('scenario_source', {}))
            executions.append({key: d[key] for key in ('id', 'project_id', 'input_hash', 'manifest_hash', 'adapter_id', 'adapter_version', 'fidelity')} |
                dict(role=role, configuration_hash=source.get('configuration_hash')))
        result.update(ready=True, status='ready', reason=None, source_family=family, executions=executions,
                      primary_link=primary, links=links, key_moments=moments)
        if pins.get('observation'):
            result.update(observation=pins['observation'], observation_source='curator_note_for_pinned_results')
    except (HTTPException, ValueError, KeyError, TypeError, OSError) as exc:
        result.update(status='invalid', reason=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return result


def register_demo_case_routes(api, db, *, curation_path=None):
    curated, error = _curation(curation_path if curation_path is not None else os.environ.get(CURATION_ENV))

    @api.get('/demo-cases', tags=['Demo cases'])
    def demo_cases(request: Request, project_id: str = 'default'):
        """Questions over explicit same-project retained evidence; never starts engines."""
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            items = [_item(session, db, project_id, template, curated.get(project_id, {}), error) for template in TEMPLATES]
        ready = [item for item in items if item['ready']]
        families = {}
        for item in ready:
            family = item['source_family']
            families.setdefault(family['id'], family | {'ready_case_count': 0})['ready_case_count'] += 1
        return dict(schema_version=VERSION, project_id=project_id, collection='carla_external', items=items,
            counts=dict(templates=len(items), ready=len(ready), unavailable=len(items)-len(ready),
                unique_executions=len({execution['id'] for item in ready for execution in item['executions']}),
                source_families=len(families)), source_families=list(families.values()),
            ready_scope='sealed_execution_configuration_and_artifact_presence',
            limits=['질문 수와 소스 묶음 수는 독립적인 장면 다양성·검증 커버리지가 아닙니다.',
                    '목록은 봉인 입력·결과 hash와 파일 존재·크기를 확인합니다. 원본 bytes hash는 Viewer·다운로드에서 검증합니다.',
                    'CARLA 계열의 보존 결과만 연결하며 실도로·nuScenes는 제외합니다. 실제 엔진의 현재 연결 상태는 조회하지 않습니다.'])
