"""Capture an exact retained CARLA frame as a recording-backed investigation Case.

The MCAP message is a lossless JSON extraction from a hashed scene artifact,
not a new sensor measurement, simulation, or validation verdict.
"""
import hashlib
import io
import json
import math
import tempfile
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Request, Response
from mcap.writer import CompressionType, Writer
from pydantic import Field
from sqlalchemy import select

from . import cases, experiment_jobs, jobs
from .case_models import CaseRow
from .case_schemas import CaseFields, RecordingSelection
from .experiment_models import ExperimentExecutionRow
from .job_models import JobRow
from .object_demo import _execution
from .object_story import StoryUnavailable, read_verified_scene
from .recording_schemas import RecordingImportMetadata
from .recordings import import_recording
from .scenario_v2_schemas import Identity
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_schemas import Sha256
from .team_access import project_authorize

_IMPORT_LOCK = RLock()
FIDELITIES = {'native_carla_object_physics', 'native_carla_mixed_physics_and_replay', 'native_carla_timed_position_replay'}


class CityCaseCreate(CaseFields):
    project_id: Identity = 'default'
    execution_manifest_hash: Sha256
    scene_sha256: Sha256
    frame_index: int = Field(ge=0, le=100000, strict=True)
    time_s: float = Field(ge=0, le=120)


def _source(db, session, project, identity):
    side, snapshot = _execution(session, project, identity)
    execution = side['execution']
    if execution['adapter_id'] != 'carla-city-object' or execution['fidelity'] not in FIDELITIES:
        raise HTTPException(422, 'Case capture requires a retained native CARLA city execution')
    try:
        scene, receipt = read_verified_scene(db, session, side, snapshot, require_planner_events=False)
    except StoryUnavailable as error:
        raise HTTPException(409, 'Retained native Case source unavailable: ' + str(error)) from error
    if (scene.get('physical_sensor_validation') is not False
            or scene.get('map', {}).get('external') != snapshot.get('external_map_provenance')):
        raise HTTPException(409, 'Native map lineage or physical-validation boundary differs from the sealed input')
    return side, snapshot, scene, receipt


def _record_frame(db, frame, lineage):
    raw_frame = canonical_bytes(frame)
    if not 0 < len(raw_frame) <= 2 * 1024 * 1024:
        raise HTTPException(413, 'Selected native frame exceeds 2 MiB Case capture bound')
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.NONE, enable_crcs=True, enable_data_crcs=True)
    writer.start(profile='axiom-native-city-case/1', library='axiom-object-case/1')
    schema = writer.register_schema('axiom.native-city-frame/1', 'jsonschema', canonical_bytes({'type': 'object', 'description': 'Exact retained CARLA city frame; simulator state, not measured sensor data'}))
    channel = writer.register_channel('/simulation/carla/native-frame', 'json', schema)
    writer.add_metadata('axiom.native_city_case', {'schema_version': 'native-city-case-extraction/1',
        'lineage_json': canonical_bytes(lineage).decode(), 'extraction': 'exact_frame_json_without_coordinate_conversion',
        'physical_validation': 'false', 'safety_verdict': 'not_evaluated'})
    at = round(frame['time_s'] * 1e9)
    writer.add_message(channel, at, raw_frame, at, sequence=frame['index'])
    writer.finish()
    raw = output.getvalue()
    with tempfile.TemporaryDirectory(prefix='axiom-city-case-') as temp:
        path = Path(temp) / 'retained-frame.mcap'
        path.write_bytes(raw)
        recording, _ = import_recording(db, _IMPORT_LOCK, path, RecordingImportMetadata(
            name='CARLA retained frame · ' + lineage['execution_id'], source_kind='simulation',
            source='Lossless JSON extraction from city-scene.json SHA-256 ' + lineage['scene_sha256']),
            hashlib.sha256(raw).hexdigest(), len(raw))
    return recording, channel, str(at), hashlib.sha256(raw_frame).hexdigest()


def validate_case_link(session, case):
    link = case.get('object_sim_evidence')
    if link is None:
        return
    try:
        subject = session.get(ExperimentExecutionRow, link['execution_id'])
        row = session.scalar(select(JobRow).where(JobRow.id == link['execution_id']))
        if subject is None or row is None or subject.project_id != link['project_id']:
            raise ValueError('project/execution')
        execution = experiment_jobs.detail(session, row).model_dump(mode='json')
        snapshot = row.prepared['input']
        scene = [item for item in execution['artifacts'] if item['name'] == 'city-scene.json']
        expected_scenario = { 'id': snapshot['source']['scenario_id'], 'revision': snapshot['source']['scenario_revision'],
            'configuration_hash': snapshot['source']['configuration_hash'], 'manifest_hash': snapshot['source']['scenario_manifest_hash'] }
        if (link['schema_version'] != 'object-sim-case-evidence/1' or execution['status'] != 'completed'
                or execution['outcome_uncertain'] or execution['adapter_id'] != 'carla-city-object'
                or execution['fidelity'] not in FIDELITIES or execution['manifest_hash'] != link['execution_manifest_hash']
                or execution['input_hash'] != link['input_hash'] or len(scene) != 1
                or scene[0]['sha256'] != link['scene_sha256'] or scene[0]['id'] != link['scene_artifact_id']
                or execution['adapter_id'] != link['adapter_id'] or execution['adapter_version'] != link['adapter_version']
                or execution['fidelity'] != link['fidelity'] or link['scenario_ref'] != expected_scenario
                or link['map_ref'] != snapshot['scenario']['map']
                or link['coordinate_system'] != snapshot['scenario']['coordinate_system']
                or link['external_map_provenance'] != snapshot.get('external_map_provenance')
                or case['segment']['recording_id'] != link['recording_id']
                or case['segment']['recording_sha256'] != link['recording_sha256']
                or case['segment']['sample_count'] != 1
                or case['segment']['samples'][0]['sha256'] != link['frame_sha256']
                or case['segment']['samples'][0]['sequence'] != link['frame_index']
                or case['segment']['start_time_ns'] != str(round(link['time_s'] * 1e9))
                or link['extraction'] != 'exact_frame_json_without_coordinate_conversion'
                or link['physical_validation'] is not False or link['safety_verdict'] != 'not_evaluated'):
            raise ValueError('pinned source')
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise HTTPException(409, 'Object Sim Case lineage differs from the sealed source') from error


def capture_case(db, identity, body, key):
    creation_key = experiment_jobs._key(body.project_id, key, 'object-scene-case')
    request_hash = digest({'execution_id': identity, **body.model_dump(mode='json')})
    with db.read_session() as session:
        side, snapshot, scene, receipt = _source(db, session, body.project_id, identity)
        execution = side['execution']
        if body.execution_manifest_hash != execution['manifest_hash'] or body.scene_sha256 != receipt['artifact_sha256']:
            raise HTTPException(409, 'Case selection differs from the displayed execution or scene hash')
        if body.frame_index >= len(scene['frames']):
            raise HTTPException(422, 'Selected Case frame does not exist')
        frame = scene['frames'][body.frame_index]
        if not math.isfinite(body.time_s) or frame['index'] != body.frame_index or abs(frame['time_s'] - body.time_s) > 1e-9:
            raise HTTPException(409, 'Case frame index and displayed simulation time differ')
        existing = session.scalar(select(CaseRow).where(CaseRow.creation_key == creation_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key already identifies another Object Sim Case')
            return cases._get(session, existing.id, 1)
        lineage = dict(schema_version='object-sim-case-evidence/1', project_id=body.project_id, execution_id=identity,
            execution_manifest_hash=execution['manifest_hash'], input_hash=execution['input_hash'],
            adapter_id=execution['adapter_id'], adapter_version=execution['adapter_version'], fidelity=execution['fidelity'],
            scene_sha256=receipt['artifact_sha256'], scene_artifact_id=receipt['artifact_id'],
            scenario_ref=side['scenario_ref'], map_ref=side['scenario']['map'],
            external_map_provenance=snapshot.get('external_map_provenance'), frame_index=frame['index'],
            time_s=frame['time_s'], coordinate_system=scene['coordinate_system'],
            extraction='exact_frame_json_without_coordinate_conversion', physical_validation=False, safety_verdict='not_evaluated')
    recording, channel, at, frame_hash = _record_frame(db, frame, lineage)
    with jobs._write(db) as session:
        existing = session.scalar(select(CaseRow).where(CaseRow.creation_key == creation_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key already identifies another Object Sim Case')
            return cases._get(session, existing.id, 1)
        segment, segment_hash, links = cases._make_segment(session, db, RecordingSelection(
            recording_id=recording.id, channel_id=channel, start_time_ns=at, end_time_ns=at))
        lineage.update(recording_id=recording.id, recording_sha256=recording.sha256, frame_sha256=frame_hash)
        value = dict(schema_version='axiom-investigation-case/1', id=uid('case'), revision=1, created_at=utcnow(),
            title=body.title, note=body.note, assignee=body.assignee, state='open', disposition_note='',
            segment=segment, segment_hash=segment_hash, evaluations=links, previous_manifest_hash=None,
            workflow_state_is_test_verdict=False, assignee_is_authenticated_identity=False, object_sim_evidence=lineage)
        head = CaseRow(id=value['id'], revision=1, segment_id=segment['id'], title=body.title, state='open',
            created_at=value['created_at'], creation_key=creation_key, request_hash=request_hash)
        session.add(head)
        session.flush()
        return cases._publish(session, head, value, 'create:' + head.id, request_hash)


def _read_case(db, project, execution_id, case_id, revision):
    with db.read_session() as session:
        _, _, scene, _ = _source(db, session, project, execution_id)
        value = cases._get(session, case_id, revision)
        link = value.get('object_sim_evidence', {})
        if link.get('project_id') != project or link.get('execution_id') != execution_id:
            raise HTTPException(404, 'Case does not belong to this project execution')
        index = link['frame_index']
        if not 0 <= index < len(scene['frames']) or digest(scene['frames'][index]) != link['frame_sha256']:
            raise HTTPException(409, 'Case frame differs from its retained scene')
        return value


def register_object_scene_case_routes(api, db):
    base = '/object-sim/executions/{execution_id}/cases'

    def exposed(value, project_id, execution_id):
        query = urlencode({'project_id': project_id, 'revision': value['revision']})
        return value | {'download_url': f"/api/v1/object-sim/executions/{execution_id}/cases/{value['id']}/export?{query}"}

    @api.post(base, status_code=201, tags=['Object Sim'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def create(execution_id: str, body: CityCaseCreate, request: Request,
               idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        return exposed(capture_case(db, execution_id, body, idempotency_key), body.project_id, execution_id)

    @api.get(base + '/{case_id}', tags=['Object Sim'])
    def read(execution_id: str, case_id: str, request: Request, project_id: str = 'default', revision: int = Query(1, ge=1)):
        project_authorize(request, db, project_id)
        return exposed(_read_case(db, project_id, execution_id, case_id, revision), project_id, execution_id)

    @api.get(base + '/{case_id}/export', tags=['Object Sim'])
    def export(execution_id: str, case_id: str, request: Request, project_id: str = 'default', revision: int = Query(1, ge=1)):
        project_authorize(request, db, project_id)
        value = _read_case(db, project_id, execution_id, case_id, revision)
        raw = canonical_bytes({key: item for key, item in value.items() if key not in {'manifest_hash', 'manifest_size_bytes', 'download_url'}})
        if hashlib.sha256(raw).hexdigest() != value['manifest_hash']:
            raise HTTPException(409, 'Case export differs from the sealed revision')
        return Response(raw, media_type='application/json', headers={
            'Content-Disposition': f'attachment; filename="{case_id}-r{revision}.json"',
            'X-Content-SHA256': value['manifest_hash']})
