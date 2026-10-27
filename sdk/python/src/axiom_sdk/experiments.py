"""Project-scoped experiment/replay workflows on the shared versioned API."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from xml.etree import ElementTree as ET

from .collections import _key
from .perception import _identity, _digest_metadata


def _object_map_identity(value: str) -> str:
    value = _identity(value)
    if len(value) > 64:
        raise ValueError('Object map/project ID must have at most 64 characters')
    return value


def _object_map_revision(value: int, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'Object map revision must be an integer >= {minimum}')
    return value


def _object_map_definition(definition: dict) -> dict:
    """Bound requests locally; geometry validity remains the shared API's job."""
    if not isinstance(definition, dict):
        raise ValueError('Object map definition must be an object')
    for key, maximum in {'roads':128, 'connections':2048, 'junctions':128,
                         'signals':512, 'objects':512, 'unsupported':256}.items():
        if key in definition and (not isinstance(definition[key], list) or len(definition[key]) > maximum):
            raise ValueError(f'Object map {key} must be a list with at most {maximum} entries')
    for road in definition.get('roads', []):
        if not isinstance(road, dict):
            raise ValueError('Object map roads must contain objects')
        for key, maximum in {'lanes':12, 'geometries':32}.items():
            if key in road and (not isinstance(road[key], list) or not 1 <= len(road[key]) <= maximum):
                raise ValueError(f'Road {key} must contain 1..{maximum} entries')
    return definition


class ExperimentClientMixin:
    def object_sim_presets(self, *, project_id: str='default'):
        """Read explicitly pinned demo presets; ready describes authoring, not engine health."""
        return self._request('GET', '/object-sim/presets', params={
            'project_id': _object_map_identity(project_id)}).json()

    def preview_object_sim_preset(self, preset_id: str, *, project_id: str='default',
                                  parameters: dict|None=None, actor_poses: dict|None=None,
                                  actor_edits: dict|None=None, source_scenario_ref: dict|None=None):
        """Return a draft without saving or executing; native preflight remains required."""
        if preset_id not in {'obstacle_bypass', 'gap_release', 'junction_eight', 'urban_city', 'urban_right_turn',
                             'lead_stop_start', 'cut_in', 'urban_left_turn'}:
            raise ValueError('Unknown Object Sim preset')
        parameters = {} if parameters is None else parameters
        actor_poses = {} if actor_poses is None else actor_poses
        if not isinstance(parameters, dict) or len(parameters) > 5:
            raise ValueError('Preset parameters must be an object with at most five entries')
        if not isinstance(actor_poses, dict) or len(actor_poses) > 32:
            raise ValueError('Actor poses must be an object with at most 32 entries')
        limits = {'ego_speed_mps': (0,12), 'rear_speed_mps': (0,12),
                  'rear_gap_m': (5,60), 'obstacle_offset_m': (-15,15), 'lead_wait_s': (0,6),
                  'lead_speed_mps': (0,12), 'intruder_speed_mps': (0,12), 'crossing_speed_mps': (0,12),
                  'lead_start_s': (0,30), 'cut_in_start_s': (0,30)}
        allowed = {'obstacle_bypass': {'ego_speed_mps','obstacle_offset_m','allow_lane_changes'},
                   'gap_release': {'ego_speed_mps','rear_speed_mps','rear_gap_m','allow_lane_changes'},
                   'junction_eight': set(), 'urban_city': {'ego_speed_mps'}, 'urban_right_turn': {'lead_wait_s'},
                   'lead_stop_start': {'ego_speed_mps', 'lead_speed_mps', 'lead_wait_s', 'lead_start_s'},
                   'cut_in': {'ego_speed_mps', 'intruder_speed_mps', 'cut_in_start_s'},
                   'urban_left_turn': {'ego_speed_mps', 'crossing_speed_mps'}}[preset_id]
        for key, value in parameters.items():
            if key not in allowed:
                raise ValueError('Unsupported parameter for this preset')
            if key == 'allow_lane_changes':
                valid = type(value) is bool
            else:
                valid = type(value) in (int,float) and math.isfinite(value) and limits[key][0] <= value <= limits[key][1]
            if not valid:
                raise ValueError('Preset parameter is outside its supported type or limits')
        for identity, pose in actor_poses.items():
            _object_map_identity(identity)
            if not isinstance(pose, dict) or set(pose) != {'x','y','z','heading_rad'} or any(
                    type(value) not in (int,float) or not math.isfinite(value) for value in pose.values()):
                raise ValueError('Actor pose requires finite x, y, z and heading_rad')
        from .object_beta import _actor_edits, _scenario_ref
        body = {'project_id': _object_map_identity(project_id), 'parameters': parameters, 'actor_poses': actor_poses}
        if actor_edits is not None:
            body['actor_edits'] = _actor_edits(actor_edits)
        if source_scenario_ref is not None:
            body['source_scenario_ref'] = _scenario_ref(source_scenario_ref)
        return self._request('POST', f'/object-sim/presets/{preset_id}/preview', json=body).json()

    def bind_object_sim_preset(self, preset_id: str, scenario_ref: dict, actor_roles: dict, *,
                               project_id: str='default', expected_revision: int=0, saved_execution_id: str|None=None,
                               saved_comparison_execution_id: str|None=None):
        """Pin an existing project scenario; requires Engineer permission."""
        if preset_id not in {'obstacle_bypass','gap_release','junction_eight','urban_city','urban_right_turn',
                             'lead_stop_start','cut_in','urban_left_turn'}:
            raise ValueError('Unknown Object Sim preset')
        _object_map_revision(expected_revision, minimum=0)
        if not isinstance(scenario_ref, dict) or set(scenario_ref) != {'id','revision','configuration_hash','manifest_hash'}:
            raise ValueError('Scenario reference requires id, revision, configuration_hash and manifest_hash')
        _object_map_identity(scenario_ref['id']); _object_map_revision(scenario_ref['revision'])
        for key in ('configuration_hash','manifest_hash'):
            _digest_metadata(scenario_ref[key],1,1)
        if not isinstance(actor_roles, dict) or not actor_roles or set(actor_roles) - {'ego','obstacle','rear','lead','intruder','crossing'}:
            raise ValueError('Preset roles must name ego, obstacle, rear, lead, intruder or crossing actors')
        for value in actor_roles.values():
            _object_map_identity(value)
        if saved_execution_id is not None:
            _object_map_identity(saved_execution_id)
        if saved_comparison_execution_id is not None:
            _object_map_identity(saved_comparison_execution_id)
            if saved_execution_id is None or saved_comparison_execution_id == saved_execution_id:
                raise ValueError('Saved comparison requires a distinct saved baseline execution')
        return self._request('POST', f'/object-sim/presets/{preset_id}/bind', json={
            'project_id': _object_map_identity(project_id), 'expected_revision': expected_revision,
            'scenario_ref': scenario_ref, 'actor_roles': actor_roles, 'saved_execution_id': saved_execution_id,
            **({'saved_comparison_execution_id': saved_comparison_execution_id} if saved_comparison_execution_id is not None else {})}).json()

    def object_sim_comparison(self, left_execution_id: str, right_execution_id: str, *, project_id: str='default'):
        return self._request('GET', '/object-sim/comparison', params={
            'project_id': _object_map_identity(project_id),
            'left_execution_id': _object_map_identity(left_execution_id),
            'right_execution_id': _object_map_identity(right_execution_id)}).json()

    def object_sim_observations(self, *, project_id: str='default', execution_id: str|None=None,
                                limit: int=100, offset: int=0):
        """Read user observations; these are separate from execution verdicts."""
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('Observation pagination requires limit 1..200 and offset >= 0')
        params = {'project_id': _object_map_identity(project_id), 'limit': limit, 'offset': offset}
        if execution_id is not None:
            params['execution_id'] = _object_map_identity(execution_id)
        return self._request('GET', '/object-sim/observations', params=params).json()

    def object_sim_behavior_report(self, execution_id: str, *, project_id: str='default'):
        """Read retained planner actions separately from measured ego outcome."""
        return self._request('GET', f'/object-sim/executions/{_object_map_identity(execution_id)}/behavior',
            params={'project_id': _object_map_identity(project_id)}).json()

    def create_object_sim_observation(self, execution_id: str, scenario_ref: dict, *, time_s: float,
                                     title: str, note: str, idempotency_key: str, project_id: str='default',
                                     kind: str='observation', behavior_exercised: str='uncertain',
                                     ego_outcome: str='uncertain'):
        """Pin a manual observation to an executed revision and scenario time."""
        from .object_beta import _observation
        body = _observation({'project_id': _object_map_identity(project_id),
            'execution_id': _object_map_identity(execution_id), 'scenario_ref': scenario_ref, 'time_s': time_s,
            'title': title, 'note': note, 'kind': kind, 'behavior_exercised': behavior_exercised, 'ego_outcome': ego_outcome})
        return self._request('POST', '/object-sim/observations', json=body,
                             headers=_key(idempotency_key)).json()

    def object_sim_story(self, left_execution_id: str, right_execution_id: str|None=None, *, project_id: str='default'):
        """Read guided clips from sealed original planner events on a shared clock."""
        params = {'project_id': _object_map_identity(project_id),
                  'left_execution_id': _object_map_identity(left_execution_id)}
        if right_execution_id is not None:
            params['right_execution_id'] = _object_map_identity(right_execution_id)
        return self._request('GET', '/object-sim/story', params=params).json()

    def object_behaviors(self):
        return self._request('GET','/object-behaviors').json()

    def object_map_capabilities(self, *, project_id: str='default'):
        """Read compiler support; this is not a native simulator health probe."""
        return self._request('GET','/object-maps/capabilities',
            params={'project_id':_object_map_identity(project_id)}).json()

    def object_maps(self, *, project_id: str='default', limit: int=100, offset: int=0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError('Use an object map page limit of 1..1000 and nonnegative offset')
        return self._request('GET','/object-maps',params={
            'project_id':_object_map_identity(project_id),'limit':limit,'offset':offset}).json()

    def object_map(self, identity: str, *, project_id: str='default', revision: int|None=None):
        if revision is not None:
            _object_map_revision(revision)
        return self._request('GET',f'/object-maps/{_object_map_identity(identity)}',params={
            'project_id':_object_map_identity(project_id),
            **({'revision':revision} if revision is not None else {})}).json()

    def export_object_map(self, identity: str, *, project_id: str='default', revision: int|None=None,
                          format: str='opendrive') -> str:
        """Return exact UTF-8 XML or JSON text after verifying original response bytes.

        source_opendrive preserves an imported original; opendrive may compile
        an editable source. Neither export creates or runs a simulator world.
        """
        if not isinstance(format, str) or format not in {'opendrive','source_opendrive','json'}:
            raise ValueError('Object map export format must be opendrive, source_opendrive or json')
        if revision is not None:
            _object_map_revision(revision)
        response = self._request('GET',f'/object-maps/{_object_map_identity(identity)}/export',params={
            'project_id':_object_map_identity(project_id),'format':format,
            **({'revision':revision} if revision is not None else {})})
        if response.headers.get('X-Content-SHA256') != hashlib.sha256(response.content).hexdigest():
            raise ValueError('Object map export content hash mismatch')
        return response.content.decode('utf-8')

    def preview_object_map(self, definition: dict, *, project_id: str='default'):
        return self._request('POST','/object-maps/preview',json={
            'definition':_object_map_definition(definition),'project_id':_object_map_identity(project_id)}).json()

    def save_object_map(self, identity: str, definition: dict, *, expected_revision: int=0, project_id: str='default'):
        _object_map_revision(expected_revision, minimum=0)
        return self._request('POST','/object-maps',json={
            'id':_object_map_identity(identity),'definition':_object_map_definition(definition),
            'project_id':_object_map_identity(project_id),'expected_revision':expected_revision}).json()

    def build_object_map(self, identity: str, *, revision: int, sha256: str, server_version: str,
                         expected_map_revision: int=0, project_id: str='default'):
        _object_map_revision(revision)
        _object_map_revision(expected_map_revision, minimum=0)
        _digest_metadata(sha256,1,1)
        if not isinstance(server_version, str) or not 1 <= len(server_version) <= 200:
            raise ValueError('Requested CARLA server version must contain 1..200 characters')
        return self._request('POST',f'/object-maps/{_object_map_identity(identity)}/build',json={
            'project_id':_object_map_identity(project_id),'revision':revision,'sha256':sha256,
            'server_version':server_version,'expected_map_revision':expected_map_revision}).json()

    def import_object_map(self, content: str, rights: dict, *, name: str='Imported road map', project_id: str='default'):
        if not isinstance(content, str) or not 1 <= len(content) <= 20_000_000:
            raise ValueError('OpenDRIVE content must contain 1..20000000 characters')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 180:
            raise ValueError('Imported map name must contain 1..180 characters')
        if not isinstance(rights, dict):
            raise ValueError('Imported map rights must be an object')
        return self._request('POST','/object-maps/import',json={'content':content,'rights':rights,
            'name':name,'project_id':_object_map_identity(project_id)}).json()

    def scenario_v2_capabilities(self):
        return self._request('GET','/scenario-v2/capabilities').json()

    def scenarios_v2(self, *, project_id: str='default', limit: int=50, offset: int=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
            raise ValueError('Use a page limit of 1..100 and nonnegative offset')
        return self._request('GET','/scenario-v2',params={'project_id':_identity(project_id),'limit':limit,'offset':offset}).json()

    def scenario_v2(self, scenario_id: str, *, revision: int|None=None):
        if revision is not None and (type(revision) is not int or revision<1):
            raise ValueError('Scenario revision must be a positive integer')
        return self._request('GET',f'/scenario-v2/{_identity(scenario_id)}',params={'revision':revision} if revision else {}).json()

    def validate_scenario_yaml(self, yaml: str, *, project_id: str='default'):
        return self._request('POST','/scenario-v2/yaml/validate',json={'yaml':yaml,'project_id':_identity(project_id)}).json()

    def import_scenario_yaml(self, yaml: str, *, idempotency_key: str, project_id: str='default'):
        return self._request('POST','/scenario-v2/yaml/import',json={'yaml':yaml,'project_id':_identity(project_id)},
                             headers=_key(idempotency_key)).json()

    def create_scenario_v2(self, scenario: dict, *, idempotency_key: str, project_id: str='default', source_yaml: str|None=None):
        return self._request('POST','/scenario-v2',json={'project_id':_identity(project_id),'scenario':scenario,
                             **({'source_yaml':source_yaml} if source_yaml is not None else {})},headers=_key(idempotency_key)).json()

    def revise_scenario_v2(self, scenario_id: str, scenario: dict, *, expected_revision: int,
                           idempotency_key: str, source_yaml: str|None=None):
        if type(expected_revision) is not int or expected_revision<1:
            raise ValueError('Expected scenario revision must be a positive integer')
        return self._request('POST',f'/scenario-v2/{_identity(scenario_id)}/revisions',json={
            'expected_revision':expected_revision,'scenario':scenario,
            **({'source_yaml':source_yaml} if source_yaml is not None else {})},headers=_key(idempotency_key)).json()

    def export_scenario_yaml(self, scenario_id: str, *, revision: int|None=None, original: bool=False):
        if revision is not None and (type(revision) is not int or revision<1):
            raise ValueError('Scenario revision must be a positive integer')
        response=self._request('GET',f'/scenario-v2/{_identity(scenario_id)}/export',params={
            'format':'source_yaml' if original else 'yaml',**({'revision':revision} if revision else {})})
        expected=response.headers.get('X-Content-SHA256')
        if expected != hashlib.sha256(response.content).hexdigest():
            raise ValueError('Scenario YAML download content hash mismatch')
        return response.text

    def simulation_catalog(self, kind: str, *, project_id: str='default', limit: int=100, offset: int=0):
        if kind not in {'maps','assets','controllers'}:
            raise ValueError('Catalog kind must be maps, assets or controllers')
        if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or offset<0:
            raise ValueError('Use a catalog page limit of 1..1000 and nonnegative offset')
        return self._request('GET',f'/{kind}',params={'project_id':_identity(project_id),'limit':limit,'offset':offset}).json()

    def simulation_catalog_revision(self, kind: str, identity: str, *, project_id: str='default', revision: int|None=None):
        if kind not in {'maps','assets','controllers'}:
            raise ValueError('Catalog kind must be maps, assets or controllers')
        if revision is not None and (type(revision) is not int or revision<1):
            raise ValueError('Catalog revision must be a positive integer')
        return self._request('GET',f'/{kind}/{_identity(identity)}',params={'project_id':_identity(project_id),
            **({'revision':revision} if revision else {})}).json()

    def register_simulation_catalog(self, kind: str, identity: str, definition: dict, *, expected_revision: int=0, project_id: str='default'):
        if kind not in {'maps','assets','controllers'}:
            raise ValueError('Catalog kind must be maps, assets or controllers')
        if type(expected_revision) is not int or expected_revision<0:
            raise ValueError('Expected catalog revision must be nonnegative')
        return self._request('POST',f'/{kind}',json={'project_id':_identity(project_id),'id':_identity(identity),
            'expected_revision':expected_revision,'definition':definition}).json()

    def plan_city_route(self, map_id: str, *, project_id: str='default', revision: int, sha256: str,
                        start: dict, goal: dict, via: list[dict]|None=None, max_snap_distance_m: float=5):
        _digest_metadata(sha256,1,1)
        if type(revision) is not int or revision<1:
            raise ValueError('Map revision must be a positive integer')
        return self._request('POST',f'/maps/{_identity(map_id)}/route',json={
            'project_id':_identity(project_id),'revision':revision,'sha256':sha256,'start':start,'goal':goal,
            'via':via or [],'max_snap_distance_m':max_snap_distance_m}).json()

    def team_identity(self):
        return self._request('GET','/team/whoami').json()

    def projects(self):
        return self._request('GET','/projects').json()

    def create_project(self, name: str):
        return self._request('POST','/projects',json={'name':name}).json()

    def project_tokens(self, project_id: str):
        return self._request('GET',f'/projects/{_identity(project_id)}/tokens').json()

    def create_project_token(self, project_id: str, *, user_label: str, role: str):
        """Return a newly generated project token once; the caller must store it securely."""
        if role not in {'Viewer','Engineer','Reviewer','Admin'}:
            raise ValueError('Unknown project role')
        return self._request('POST',f'/projects/{_identity(project_id)}/tokens',json={'user_label':user_label,'role':role}).json()

    def revoke_project_token(self, project_id: str, token_id: str):
        return self._request('POST',f'/projects/{_identity(project_id)}/tokens/{_identity(token_id)}/revoke').json()

    def experiment_capabilities(self):
        return self._request('GET','/experiments/capabilities').json()

    def experiments(self, *, project_id: str='default'):
        return self._request('GET','/experiments',params={'project_id':_identity(project_id)}).json()

    def experiment(self, experiment_id: str):
        return self._request('GET',f'/experiments/{_identity(experiment_id)}').json()

    def preview_experiment(self, **definition: Any):
        return self._request('POST','/experiments/preview',json=definition).json()

    def create_experiment(self, *, idempotency_key: str, **definition: Any):
        return self._request('POST','/experiments',json=definition,headers=_key(idempotency_key)).json()

    def submit_experiment(self, experiment_id: str, *, preview_hash: str, idempotency_key: str):
        _digest_metadata(preview_hash,1,1)
        return self._request('POST',f'/experiments/{_identity(experiment_id)}/executions',json={'preview_hash':preview_hash},
            headers=_key(idempotency_key)).json()

    def experiment_execution(self, job_id: str):
        return self._request('GET',f'/experiments/executions/{_identity(job_id)}').json()

    def experiment_executions(self, *, project_id: str='default', limit: int=50, before_seq: int|None=None):
        if type(limit) is not int or not 1<=limit<=100 or before_seq is not None and (type(before_seq) is not int or before_seq<1):
            raise ValueError('Execution page limit must be 1..100; before_seq must be a positive integer')
        return self._request('GET','/experiments/executions',params={'project_id':_identity(project_id),'limit':limit,
            **({'before_seq':before_seq} if before_seq is not None else {})}).json()

    def experiment_verification_capabilities(self):
        return self._request('GET','/experiment-verification-plans/capabilities').json()

    def experiment_verification_plans(self, *, project_id: str='default'):
        return self._request('GET','/experiment-verification-plans',params={'project_id':_identity(project_id)}).json()

    def experiment_verification_plan(self, plan_id: str, *, revision: int|None=None):
        if revision is not None and (type(revision) is not int or revision<1):
            raise ValueError('Plan revision must be a positive integer')
        return self._request('GET',f'/experiment-verification-plans/{_identity(plan_id)}',
            params={'revision':revision} if revision is not None else {}).json()

    def create_experiment_verification_plan(self, *, idempotency_key: str, **definition: Any):
        return self._request('POST','/experiment-verification-plans',json=definition,headers=_key(idempotency_key)).json()

    def revise_experiment_verification_plan(self, plan_id: str, *, idempotency_key: str, **definition: Any):
        return self._request('POST',f'/experiment-verification-plans/{_identity(plan_id)}/revisions',
            json=definition,headers=_key(idempotency_key)).json()

    def evaluate_experiment_verification_plan(self, plan_id: str, *, plan_revision: int, execution_ids: list[str], idempotency_key: str):
        if type(plan_revision) is not int or plan_revision<1:
            raise ValueError('Plan revision must be a positive integer')
        if (not isinstance(execution_ids,list) or not 1<=len(execution_ids)<=128
                or any(not isinstance(item,str) for item in execution_ids) or len(set(execution_ids))!=len(execution_ids)):
            raise ValueError('Select 1..128 distinct execution IDs; repeats do not add independent evidence')
        return self._request('POST',f'/experiment-verification-plans/{_identity(plan_id)}/evaluations',
            json={'plan_revision':plan_revision,'execution_ids':[_identity(item) for item in execution_ids]},
            headers=_key(idempotency_key)).json()

    def export_experiment_verification_evaluation(self, plan_id: str, evaluation_id: str, destination: str|Path):
        detail = self.experiment_verification_plan(plan_id)
        result = next((item for item in detail['evaluations'] if item['id']==evaluation_id),None)
        if result is None:
            raise ValueError('Evaluation is not part of the selected experiment verification plan')
        return self._experiment_stream_download(
            f'/experiment-verification-plans/{_identity(plan_id)}/evaluations/{_identity(evaluation_id)}/export',
            destination,result['manifest_hash'],None,4*1024*1024)

    def cancel_experiment(self, job_id: str):
        return self._request('POST',f'/experiments/executions/{_identity(job_id)}/cancel').json()

    def wait_experiment(self, job_id: str, *, timeout: float=120, poll_interval: float=.5):
        if not 0<timeout<=86_400 or not .1<=poll_interval<=60:
            raise ValueError('Use a positive bounded timeout and a poll interval from 0.1 to 60 seconds')
        deadline = time.monotonic()+timeout
        while True:
            result = self.experiment_execution(job_id)
            if result['status'] in {'completed','failed','cancelled','interrupted'}:
                return result
            if time.monotonic()>=deadline:
                raise TimeoutError('Experiment is still active; no cancellation or resubmission was performed')
            time.sleep(min(poll_interval,max(0,deadline-time.monotonic())))

    def scene_sequences(self, *, project_id: str='default'):
        return self._request('GET','/scene-sequences',params={'project_id':_identity(project_id)}).json()

    def scene_sequence(self, sequence_id: str):
        return self._request('GET',f'/scene-sequences/{_identity(sequence_id)}').json()

    def create_scene_sequence(self, **definition: Any):
        return self._request('POST','/scene-sequences',json=definition).json()

    def preview_scene_sequence(self, **definition: Any):
        """Inspect exact retained input requirements without creating or executing anything."""
        return self._request('POST','/scene-sequences/preview',json=definition).json()

    def freeze_scene_sequence(self, definition: dict, *, preview_sha256: str):
        _digest_metadata(preview_sha256,1,1)
        return self._request('POST','/scene-sequences/freeze',json={'definition':definition,'preview_sha256':preview_sha256}).json()

    def grant_recording_source(self, project_id: str, recording_id: str, *, recording_sha256: str,
                               idempotency_key: str, note: str=''):
        """Operator-only exact source grant; a grant cannot relax source-use restrictions."""
        _digest_metadata(recording_sha256,1,1)
        return self._request('POST','/project-recordings/grants',json={'project_id':_identity(project_id),
            'recording_id':_identity(recording_id),'recording_sha256':recording_sha256,'note':note},
            headers=_key(idempotency_key)).json()

    def project_recordings(self, *, project_id: str='default', q: str='', limit: int=50):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Source limit must be 1..100')
        return self._request('GET','/project-recordings',params={'project_id':_identity(project_id),'q':q,'limit':limit}).json()

    def project_recording(self, recording_id: str, *, project_id: str='default'):
        return self._request('GET',f'/project-recordings/{_identity(recording_id)}',params={'project_id':_identity(project_id)}).json()

    def download_project_recording(self, recording_id: str, destination: str|Path, *, project_id: str='default'):
        """Stream a granted original from this API and verify exact SHA/byte size.

        Returned download URLs are never followed. A failed download preserves
        an existing destination and removes its temporary staging file.
        """
        recording = self.project_recording(recording_id, project_id=project_id)
        if recording.get('id') != recording_id:
            raise ValueError('Project recording identity differs from the selected original')
        _digest_metadata(recording['sha256'],recording['size_bytes'],128*1024*1024)
        return self._experiment_stream_download(f'/project-recordings/{_identity(recording_id)}/download',
            destination,recording['sha256'],recording['size_bytes'],128*1024*1024,
            params={'project_id':_identity(project_id)},require_source_headers=True)

    def project_recording_samples(self, recording_id: str, channel_id: int, *, project_id: str='default',
                                  at_ns: str|None=None, from_start: bool=False, after_ordinal: int|None=None,
                                  from_ordinal: int|None=None, limit: int=50):
        if (type(channel_id) is not int or not 0<=channel_id<=65535 or type(limit) is not int or not 1<=limit<=100
                or type(from_start) is not bool or sum((at_ns is not None,from_start,after_ordinal is not None,from_ordinal is not None))>1
                or any(value is not None and (type(value) is not int or value<0) for value in (after_ordinal,from_ordinal))):
            raise ValueError('Use one bounded sample selector, channel ID and page limit')
        if at_ns is not None and (not isinstance(at_ns,str) or not at_ns.isascii() or not at_ns.isdecimal()
                                 or str(int(at_ns))!=at_ns or int(at_ns)>2**64-1):
            raise ValueError('Timestamp must be an unsigned64 canonical decimal string')
        selectors={key:value for key,value in {'at_ns':at_ns,'after_ordinal':after_ordinal,'from_ordinal':from_ordinal}.items() if value is not None}
        return self._request('GET',f'/project-recordings/{_identity(recording_id)}/samples',params={
            'project_id':_identity(project_id),'channel_id':channel_id,'limit':limit,
            **({'from_start':True} if from_start else {}),**selectors}).json()

    def replay_capabilities(self):
        return self._request('GET','/replay-specs/capabilities').json()

    def replay_sources(self, recording_id: str, *, project_id: str='default'):
        return self._request('GET',f'/scene-sequences/sources/{_identity(recording_id)}',params={'project_id':_identity(project_id)}).json()

    def replay_specs(self, *, project_id: str='default'):
        return self._request('GET','/replay-specs',params={'project_id':_identity(project_id)}).json()

    def replay_spec(self, replay_spec_id: str):
        return self._request('GET',f'/replay-specs/{_identity(replay_spec_id)}').json()

    def create_replay_spec(self, **definition: Any):
        return self._request('POST','/replay-specs',json=definition).json()

    def preflight_replay(self, replay_spec_id: str):
        return self._request('POST',f'/replay-specs/{_identity(replay_spec_id)}/preflight').json()

    def driving_sequences(self):
        return self._request('GET','/driving-sequences').json()

    def driving_sequence(self, sequence_id: str):
        return self._request('GET',f'/driving-sequences/{_identity(sequence_id)}').json()

    def create_driving_sequence(self, manifest: dict, *, name: str, rights_note: str, research_isolated: bool):
        if research_isolated is not True:
            raise ValueError('Explicit isolated noncommercial research confirmation is required')
        return self._request('POST','/driving-sequences',json={'name':name,'rights_note':rights_note,
            'research_isolated':True,'manifest':manifest}).json()

    def import_driving_sequence(self, relative_path: str, *, name: str, rights_note: str, research_isolated: bool):
        if research_isolated is not True:
            raise ValueError('Explicit isolated noncommercial research confirmation is required')
        return self._request('POST','/driving-sequences/import',json={'name':name,'rights_note':rights_note,
            'research_isolated':True,'relative_path':relative_path}).json()

    def export_experiment_execution(self, job_id: str, destination: str|Path):
        detail = self.experiment_execution(job_id)
        if not detail.get('manifest_hash'):
            raise ValueError('Execution is not terminal and has no sealed export')
        return self._experiment_stream_download(f'/experiments/executions/{_identity(job_id)}/export',destination,
            detail['manifest_hash'],None,4*1024*1024)

    def download_experiment_artifact(self, job_id: str, artifact_id: str, destination: str|Path):
        detail = self.experiment_execution(job_id)
        artifact = next((value for value in detail['artifacts'] if value['id']==artifact_id),None)
        if artifact is None:
            raise ValueError('Artifact is not part of the selected experiment execution')
        _digest_metadata(artifact['sha256'],artifact['size_bytes'],2*1024**3,allow_empty=True)
        return self._experiment_stream_download(f'/experiments/executions/{_identity(job_id)}/artifacts/{_identity(artifact_id)}',
            destination,artifact['sha256'],artifact['size_bytes'],2*1024**3)

    def download_object_scene(self, job_id: str, destination: str|Path):
        """Download object-scene/1 after checking its sealed identity and full trace.

        Uses the existing project-scoped export and managed artifact routes.
        Older executions without this artifact remain available through ordinary
        artifact downloads. Invalid bytes or identities preserve the destination.
        This is authored object kinematics, not physical sensor validation.
        """
        detail = self.experiment_execution(job_id)
        if (detail.get('id') != job_id or detail.get('status') != 'completed'
                or detail.get('experiment_kind') != 'object_sim'
                or detail.get('fidelity') != 'authored_object_kinematics'
                or not detail.get('manifest_hash')):
            raise ValueError('Select a completed sealed authored Object Sim execution')
        summary = detail.get('summary') or {}
        pointer = summary.get('object_scene') or {}
        if pointer.get('schema_version') != 'object-scene/1' or pointer.get('artifact_name') != 'object-scene.json':
            raise ValueError('Execution has no supported object-scene/1 artifact; use original artifact downloads')
        artifacts = {}
        for name in ('object-scene.json', 'object-trace.json', 'road.xodr', 'scenario.xosc', 'native-trace.csv', 'engine.log'):
            matching = [item for item in detail['artifacts'] if item.get('name') == name]
            if len(matching) != 1:
                raise ValueError('Object scene requires unique original source artifacts')
            artifacts[name] = matching[0]
        if pointer.get('sha256') != artifacts['object-scene.json']['sha256']:
            raise ValueError('Object scene pointer differs from its pinned artifact SHA-256')
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.axiom-object-scene-', dir=target.parent) as folder:
            folder = Path(folder)
            export = self._experiment_stream_download(f'/experiments/executions/{_identity(job_id)}/export',
                folder/'export.json', detail['manifest_hash'], None, 4*1024*1024)
            sealed = _strict_object_json(export)
            execution = sealed.get('execution', {})
            selected = detail | {'manifest_hash': None, 'download_url': None}
            if sealed.get('schema_version') != 1 or execution != selected:
                raise ValueError('Sealed Object Sim export differs from the selected execution')
            pinned = _check_object_prepared(sealed.get('prepared'), detail)
            paths = {}
            for name in ('object-scene.json', 'object-trace.json', 'engine.log'):
                artifact = artifacts[name]
                maximum = 1024*1024 if name == 'engine.log' else 16*1024*1024
                _digest_metadata(artifact['sha256'], artifact['size_bytes'], maximum, allow_empty=name == 'engine.log')
                paths[name] = self._experiment_stream_download(
                    f'/experiments/executions/{_identity(job_id)}/artifacts/{_identity(artifact["id"])}',
                    folder/name, artifact['sha256'], artifact['size_bytes'], maximum)
            _check_object_scene(_strict_object_json(paths['object-scene.json']),
                _strict_object_json(paths['object-trace.json']), pinned, detail, artifacts,
                paths['engine.log'].read_text(encoding='utf-8'))
            os.replace(paths['object-scene.json'], target)
        return target

    def _experiment_stream_download(self,path,destination,sha256,expected_size,maximum,*,params=None,require_source_headers=False):
        _digest_metadata(sha256,1,1)
        target = Path(destination)
        target.parent.mkdir(parents=True,exist_ok=True)
        staging = None
        try:
            with self._http.stream('GET','/api/v1'+path,params=params) as response:
                if response.is_error:
                    response.read()
                    from .client import AxiomError
                    try:
                        detail = response.json().get('detail',response.text)
                    except ValueError:
                        detail = response.text
                    raise AxiomError(response.status_code,detail)
                if require_source_headers:
                    if response.headers.get('content-encoding','identity') != 'identity':
                        raise ValueError('Original recording download uses an unexpected encoding')
                    if (response.headers.get('x-content-sha256') != sha256
                            or response.headers.get('content-length') != str(expected_size)):
                        raise ValueError('Original recording download headers differ from its pinned SHA-256 or byte size')
                with tempfile.NamedTemporaryFile(prefix='.axiom-download-',dir=target.parent,delete=False) as stream:
                    staging = Path(stream.name)
                    actual,count = hashlib.sha256(),0
                    for chunk in response.iter_bytes(1024*1024):
                        count+=len(chunk)
                        if count>maximum or expected_size is not None and count>expected_size:
                            raise ValueError('Experiment download exceeds its pinned byte bound')
                        actual.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if actual.hexdigest()!=sha256 or expected_size is not None and count!=expected_size:
                raise ValueError('Experiment download SHA-256 or byte size differs from its pinned artifact')
            os.replace(staging,target)
            staging = None
            return target
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)


def _strict_object_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate Object Sim JSON field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite Object Sim JSON value: '+value)
    value = json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError('Object Sim artifact must be a JSON object')
    def finite(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError('Nonfinite Object Sim JSON number')
        if isinstance(item, dict):
            for child in item.values(): finite(child)
        elif isinstance(item, list):
            for child in item: finite(child)
    finite(value)
    return value


_OBJECT_COORDINATES = 'world_m_x_forward_y_left_z_up'
_OBJECT_BINARY = '7f31d9a52a1da6c9d062a89f15b714f004308eb0d3c007107f09cce85afbf619'
_OBJECT_STATE_FIELDS = {'id', 'x', 'y', 'z', 'heading_rad', 'speed_mps', 'vx_mps', 'vy_mps',
                        'ax_mps2', 'ay_mps2', 'bb_x', 'bb_y', 'length_m', 'width_m', 'lane_id', 'lane_offset_m'}


def _object_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _object_sha(content):
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()


def _object_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('Unsupported or incomplete Object Sim '+label)


def _object_number(value, label, minimum=None, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or minimum is not None and value < minimum or maximum is not None and value > maximum):
        raise ValueError('Invalid Object Sim '+label)


def _check_object_prepared(prepared, detail):
    _object_fields(prepared, {'schema_version', 'spec', 'handler', 'input'}, 'prepared snapshot')
    if prepared['schema_version'] != 1 or _object_sha(_object_bytes(prepared)) != detail.get('input_hash'):
        raise ValueError('Object Sim prepared snapshot differs from the sealed input SHA-256')
    spec, handler, pinned = (prepared[key] for key in ('spec', 'handler', 'input'))
    if not all(isinstance(value, dict) for value in (spec, handler, pinned)):
        raise ValueError('Invalid Object Sim pinned handler/input')
    version = detail.get('adapter_version')
    if (detail.get('adapter_id') != 'esmini-object' or spec.get('adapter_id') != 'esmini-object'
            or handler.get('id') != 'esmini-object' or spec.get('kind') != 'object_sim'
            or spec.get('comparison_kind') != 'single' or spec.get('model') is not None or spec.get('sensor') is not None
            or handler.get('version') != version or spec.get('adapter_version') != version
            or pinned.get('adapter_version') != version or handler.get('status') != 'available'
            or handler.get('kinds') != ['object_sim'] or handler.get('fidelity') != 'authored_object_kinematics'
            or pinned.get('schema_version') != 'object-sim-input/1'
            or pinned.get('project_id') != detail.get('project_id') or spec.get('project_id') != detail.get('project_id')
            or spec.get('seed') != pinned.get('seed')):
        raise ValueError('Object Sim prepared handler/spec/source identity differs')
    selected = spec.get('input')
    if (not isinstance(selected, dict) or set(selected) - {'scenario_id', 'scenario_revision', 'configuration_hash'}
            or selected.get('scenario_id') != pinned.get('scenario_id')
            or selected.get('scenario_revision') != pinned.get('scenario_revision')
            or selected.get('configuration_hash') not in (None, pinned.get('configuration_hash'))):
        raise ValueError('Object Sim prepared scenario selection differs')
    _identity(pinned['scenario_id'])
    if type(pinned.get('scenario_revision')) is not int or pinned['scenario_revision'] < 1:
        raise ValueError('Object Sim scenario revision must be a positive integer')
    if type(pinned.get('seed')) is not int or not 0 <= pinned['seed'] <= 2147483647:
        raise ValueError('Object Sim seed differs from the bounded native contract')
    scenario = pinned.get('scenario')
    if (not isinstance(scenario, dict) or scenario.get('schema_version') != 'scenario-v2/1'
            or scenario.get('coordinate_system') != _OBJECT_COORDINATES
            or _object_sha(_object_bytes(scenario)) != pinned.get('configuration_hash')):
        raise ValueError('Object Sim scenario differs from its configuration SHA-256')
    for key in ('configuration_hash', 'scenario_manifest_hash', 'runner_sha256', 'engine_binary_sha256'):
        _digest_metadata(pinned.get(key), 1, 1)
    if pinned['engine_binary_sha256'] != _OBJECT_BINARY:
        raise ValueError('Unsupported Object Sim native binary identity')
    _object_number(scenario.get('duration_s'), 'duration', 1, 30)
    _object_number(scenario.get('timestep_s'), 'timestep', .02, .2)
    request = pinned.get('native_request')
    _object_fields(request, {'schema_version', 'scenario_xml', 'road_xml', 'duration_s', 'timestep_s', 'seed', 'actor_ids'}, 'native request')
    if (request['schema_version'] != 'esmini-native-request/1' or request['duration_s'] != scenario['duration_s']
            or request['timestep_s'] != scenario['timestep_s'] or request['seed'] != pinned['seed']
            or not isinstance(request['scenario_xml'], str) or not isinstance(request['road_xml'], str)):
        raise ValueError('Object Sim native request differs from its pinned scenario')
    authored = scenario.get('actors')
    if not isinstance(authored, list) or not 1 <= len(authored) <= 8:
        raise ValueError('Object Sim requires one to eight pinned actors')
    ids = [actor.get('id') for actor in authored if isinstance(actor, dict)]
    if len(ids) != len(authored) or len(set(ids)) != len(ids) or ids != request['actor_ids']:
        raise ValueError('Object Sim native request actor identities differ')
    for actor in authored:
        _identity(actor['id'])
        if actor.get('kind') not in {'vehicle', 'pedestrian'} or actor.get('role') not in {'ego', 'other'}:
            raise ValueError('Unsupported Object Sim actor kind/role')
        dimensions = actor.get('dimensions')
        _object_fields(dimensions, {'length_m', 'width_m', 'height_m'}, 'authored dimensions')
        for field, maximum in (('length_m', 30), ('width_m', 5), ('height_m', 5)):
            _object_number(dimensions[field], field, .000001, maximum)
        pose = actor.get('pose')
        _object_fields(pose, {'x', 'y', 'heading_rad'}, 'authored pose')
        for field in pose:
            _object_number(pose[field], field)
        _object_number(actor.get('speed_mps'), 'authored speed', 0, 60)
        if not isinstance(actor.get('route'), list) or not isinstance(actor.get('actions'), list):
            raise ValueError('Invalid Object Sim authored route/actions')
        if any(not isinstance(action, dict) or action.get('kind') not in {'speed', 'stop', 'lane_change', 'follow_path'}
               for action in actor['actions']):
            raise ValueError('Unsupported Object Sim authored action')
    if sum(actor['role'] == 'ego' for actor in authored) != 1:
        raise ValueError('Object Sim requires exactly one pinned ego actor')
    return pinned


def _object_road(pinned, road_hash):
    """One exact authored OpenDRIVE subset; arbitrary external geometry is rejected."""
    authored = pinned['scenario']['map']
    _object_fields(authored, {'kind', 'id', 'length_m', 'lane_width_m'}, 'authored map')
    if authored['kind'] != 'authored_straight_road' or authored['id'] != 'axiom-straight-road-r1':
        raise ValueError('Unsupported Object Sim authored map')
    _object_number(authored['length_m'], 'road length', 100, 2000)
    _object_number(authored['lane_width_m'], 'lane width', 2.5, 5)
    raw = pinned['native_request']['road_xml']
    if _object_sha(raw) != road_hash or '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
        raise ValueError('Object Sim original OpenDRIVE SHA/subset differs')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError('Invalid Object Sim original OpenDRIVE') from error
    def node(tag, attrs=None, children=()):
        return tag, sorted((attrs or {}).items()), list(children)
    length, width = str(float(authored['length_m'])), str(float(authored['lane_width_m']))
    expected = node('OpenDRIVE', children=[
        node('header', {'revMajor':'1','revMinor':'4','name':authored['id'],'version':'1','date':'2026-10-07',
                        'north':'0','south':'0','east':'0','west':'0'}),
        node('road', {'name':'AXIOM authored straight road','length':length,'id':'1','junction':'-1'}, [
            node('link'), node('planView', children=[node('geometry', {'s':'0','x':'0','y':'0','hdg':'0','length':length}, [node('line')])]),
            node('lanes', children=[node('laneSection', {'s':'0'}, [
                node('center', children=[node('lane', {'id':'0','type':'none','level':'false'}, [node('link')])]),
                node('right', children=[node('lane', {'id':str(lane),'type':'driving','level':'false'}, [
                    node('link'), node('width', {'sOffset':'0','a':width,'b':'0','c':'0','d':'0'}),
                    node('roadMark', {'sOffset':'0','type':'broken' if lane == -1 else 'solid','weight':'standard',
                                     'color':'standard','width':'0.12','laneChange':'both'})]) for lane in (-1, -2)])])])])])
    def structure(element):
        if (element.text or '').strip() or (element.tail or '').strip():
            raise ValueError('Unsupported Object Sim OpenDRIVE text')
        return node(element.tag, element.attrib, [structure(child) for child in element])
    if structure(root) != expected:
        raise ValueError('Unsupported Object Sim original OpenDRIVE geometry/lanes')
    road = root.find('./road')
    native_length = float(road.attrib['length'])
    def line(lateral):
        return [{'x':0.,'y':lateral,'z':0.}, {'x':native_length,'y':lateral,'z':0.}]
    lanes, lateral = [], 0.
    for lane in road.findall('./lanes/laneSection/right/lane'):
        native_width = float(lane.find('./width').attrib['a'])
        lanes.append({'id':int(lane.attrib['id']), 'width_m':native_width, 'centerline':line(lateral-native_width/2),
                      'left_boundary':line(lateral), 'right_boundary':line(lateral-native_width)})
        lateral -= native_width
    return {'kind':authored['kind'], 'id':authored['id'], 'road_id':1, 'length_m':native_length,
            'lane_width_m':lanes[0]['width_m'], 'source_artifact':'road.xodr', 'source_sha256':road_hash,
            'geometry_provenance':'validated_pinned_opendrive_line_and_lane_widths', 'reference_line':line(0.), 'lanes':lanes}


def _check_object_scene(scene, trace, pinned, detail, artifacts, native_log):
    _object_fields(scene, {'schema_version', 'coordinate_system', 'fidelity', 'physical_sensor_validation', 'source',
                          'map', 'actors', 'frames', 'events', 'event_derivation_version', 'limitations'}, 'scene artifact')
    _object_fields(trace, {'schema_version', 'coordinate_system', 'frames'}, 'trace artifact')
    if (scene.get('schema_version') != 'object-scene/1'
            or scene.get('coordinate_system') != _OBJECT_COORDINATES
            or scene.get('fidelity') != 'authored_object_kinematics'
            or scene.get('physical_sensor_validation') is not False
            or trace.get('schema_version') != 'object-trace/1'
            or trace.get('coordinate_system') != scene['coordinate_system']
            or scene.get('event_derivation_version') != 'object-scene-events/1'):
        raise ValueError('Unsupported Object Sim scene, coordinates or fidelity')
    source, summary = scene.get('source', {}), detail['summary']
    _object_fields(source, {'scenario_id', 'scenario_revision', 'scenario_manifest_hash', 'configuration_hash', 'seed',
                           'adapter_version', 'engine_version', 'engine_build', 'engine_binary_sha256', 'runner_sha256',
                           'scenario_xml_sha256', 'road_xml_sha256', 'native_csv_sha256', 'native_log_sha256',
                           'object_trace_sha256'}, 'scene source')
    for key in ('scenario_id', 'scenario_revision', 'scenario_manifest_hash', 'configuration_hash', 'seed', 'adapter_version'):
        if source.get(key) != pinned.get(key) or source.get(key) != summary.get(key):
            raise ValueError('Object scene source differs from its pinned '+key)
    receipt = summary.get('engine_receipt')
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 'esmini-native-result/1'
            or receipt.get('engine_version') != 'v3.8.2' or receipt.get('engine_build') != '6400'
            or receipt.get('binary_sha256') != _OBJECT_BINARY or receipt.get('seed') != pinned['seed']
            or receipt.get('runner_sha256') != pinned['runner_sha256']
            or receipt.get('requested_timestep_s') != pinned['scenario']['timestep_s']
            or receipt.get('fidelity') != 'authored_object_kinematics' or receipt.get('physical_sensor_validation') is not False):
        raise ValueError('Object scene native engine receipt differs from its pinned profile')
    for field, receipt_field in {'engine_version':'engine_version','engine_build':'engine_build',
                                 'engine_binary_sha256':'binary_sha256','runner_sha256':'runner_sha256'}.items():
        if source[field] != receipt[receipt_field]:
            raise ValueError('Object scene source differs from its pinned '+field)
    for key, name in {'object_trace_sha256':'object-trace.json', 'road_xml_sha256':'road.xodr',
            'scenario_xml_sha256':'scenario.xosc', 'native_csv_sha256':'native-trace.csv', 'native_log_sha256':'engine.log'}.items():
        if source.get(key) != artifacts[name]['sha256'] or summary.get('original_artifact_hashes', {}).get(name) != artifacts[name]['sha256']:
            raise ValueError('Object scene original source SHA-256 differs: '+name)
    if (summary['original_artifact_hashes'].get('object-scene.json') != artifacts['object-scene.json']['sha256']
            or _object_sha(pinned['native_request']['scenario_xml']) != artifacts['scenario.xosc']['sha256']
            or receipt.get('scenario_xml_sha256') != artifacts['scenario.xosc']['sha256']
            or receipt.get('road_xml_sha256') != artifacts['road.xodr']['sha256']
            or receipt.get('native_csv_sha256') != artifacts['native-trace.csv']['sha256']
            or _object_sha(native_log) != source['native_log_sha256']):
        raise ValueError('Object scene original XML/native receipt SHA-256 differs')
    frames = scene.get('frames')
    if not isinstance(frames, list) or frames != trace.get('frames') or len(frames) != summary.get('sample_count') or len(frames) < 2:
        raise ValueError('Object scene does not contain the complete pinned native trace')
    expected_ids = [actor['id'] for actor in pinned['scenario']['actors']]
    if not isinstance(scene['actors'], list) or [actor.get('id') for actor in scene['actors'] if isinstance(actor, dict)] != expected_ids:
        raise ValueError('Object scene actor identities differ from the pinned scenario')
    lines = native_log.splitlines()
    for actor, authored in zip(scene['actors'], pinned['scenario']['actors']):
        _object_fields(actor, {'id','kind','role','dimensions_m','metadata','authored_intent'}, 'scene actor')
        intent = {'initial_pose':authored['pose'], 'initial_speed_mps':authored['speed_mps'],
                  'route':authored['route'], 'actions':authored['actions'],
                  'interpretation':'authored_intent_not_observed_completion'}
        if (actor['kind'] != authored['kind'] or actor['role'] != authored['role']
                or actor['dimensions_m'] != authored['dimensions'] or actor['authored_intent'] != intent):
            raise ValueError('Object scene actor metadata/intent differs from pinned authored actor')
        for field, value in actor['dimensions_m'].items():
            _object_number(value, 'scene authored '+field)
        metadata = actor['metadata']
        _object_fields(metadata, {'origin','dimensions_source','native_dimensions_fields','height_source',
                                  'native_entity_id','native_entity_id_provenance'}, 'actor metadata')
        if (metadata['origin'] != 'pinned_scenario_revision' or metadata['dimensions_source'] != 'authored_scenario'
                or metadata['native_dimensions_fields'] != ['length_m','width_m','bb_x','bb_y']
                or metadata['height_source'] != 'authored_scenario_not_native_observation'):
            raise ValueError('Unsupported Object scene actor dimension provenance')
        identifier, provenance = metadata['native_entity_id'], metadata['native_entity_id_provenance']
        if identifier is None:
            if provenance is not None:
                raise ValueError('Object scene native actor ID lacks an observed ID')
        else:
            if type(identifier) is not int or identifier < 0:
                raise ValueError('Invalid Object scene native actor ID')
            _object_fields(provenance, {'source','artifact','sha256','line_number','raw_line'}, 'native actor ID provenance')
            number = provenance['line_number']
            if (provenance['source'] != 'native_log' or provenance['artifact'] != 'engine.log'
                    or provenance['sha256'] != source['native_log_sha256'] or type(number) is not int
                    or not 1 <= number <= len(lines) or provenance['raw_line'] != lines[number-1]
                    or lines[number-1] != f'[] [info] Add and deactivate new object "{actor["id"]}" (id {identifier})'):
                raise ValueError('Object scene native actor ID differs from original log')
    previous = -math.inf
    for frame in frames:
        _object_fields(frame, {'time_s', 'actors'}, 'native frame')
        timestamp = frame.get('time_s')
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0 or timestamp <= previous:
            raise ValueError('Object scene has an invalid native frame clock')
        previous = timestamp
        observed = frame.get('actors')
        if not isinstance(observed, list) or len(observed) != len(expected_ids):
            raise ValueError('Object scene native frame actor count differs')
        ids = [actor.get('id') for actor in observed if isinstance(actor, dict)]
        if len(ids) != len(expected_ids) or len(set(ids)) != len(ids) or set(ids) != set(expected_ids):
            raise ValueError('Object scene frame actors differ from the pinned scenario')
        for actor in observed:
            _object_fields(actor, _OBJECT_STATE_FIELDS, 'native actor state')
            for field in _OBJECT_STATE_FIELDS - {'id', 'lane_id'}:
                _object_number(actor[field], 'native '+field)
            if type(actor['lane_id']) is not int or actor['length_m'] <= 0 or actor['width_m'] <= 0:
                raise ValueError('Invalid Object scene native dimensions/lane ID')
    duration, dt = pinned['scenario']['duration_s'], pinned['scenario']['timestep_s']
    if (len(frames) > math.ceil(duration/dt)+4 or frames[0]['time_s'] != 0
            or frames[-1]['time_s'] < duration-dt or frames[-1]['time_s'] > duration+4*dt):
        raise ValueError('Object scene native observation window differs from pinned timing')
    if scene['map'] != _object_road(pinned, artifacts['road.xodr']['sha256']):
        raise ValueError('Object scene map geometry differs from pinned supported OpenDRIVE')
    if (not isinstance(scene['events'], list) or not isinstance(scene['limitations'], list)
            or any(not isinstance(item, str) for item in scene['limitations'])
            or summary['object_scene'].get('event_count') != len(scene['events'])):
        raise ValueError('Object scene event count or limitations differ from its declared contract')
    _check_object_events(scene, pinned['scenario'], lines)


def _object_footprints_overlap(first, second):
    """Check the sampled world-XY native boxes, including rotated center offsets."""
    polygons, axes = [], []
    for actor in (first, second):
        c, s = math.cos(actor['heading_rad']), math.sin(actor['heading_rad'])
        x = actor['x']+actor['bb_x']*c-actor['bb_y']*s
        y = actor['y']+actor['bb_x']*s+actor['bb_y']*c
        length, width = actor['length_m']/2, actor['width_m']/2
        polygons.append([(x+u*c-v*s, y+u*s+v*c) for u,v in ((-length,-width),(length,-width),(length,width),(-length,width))])
        axes.extend(((c,s),(-s,c)))
    for axis in axes:
        intervals = []
        for polygon in polygons:
            projections = [x*axis[0]+y*axis[1] for x,y in polygon]
            intervals.append((min(projections),max(projections)))
        if max(intervals[0][0],intervals[1][0]) > min(intervals[0][1],intervals[1][1]):
            return False
    return True


def _check_object_events(scene, scenario, lines):
    if not isinstance(scene['events'], list):
        raise ValueError('Object scene events must be an original event array')
    frames, source = scene['frames'], scene['source']
    actor_ids = {actor['id'] for actor in scenario['actors']}
    by_actor = {actor['id']: actor for actor in scenario['actors']}
    for event in scene['events']:
        _object_fields(event, {'kind','actor_ids','time_s','end_time_s','provenance','details'}, 'event')
        if event['kind'] not in {'collision','lane_transition','stopped','native_action'}:
            raise ValueError('Unsupported Object scene event kind')
        ids = event['actor_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 2 or any(not isinstance(aid,str) for aid in ids) or len(set(ids)) != len(ids)
                or not set(ids).issubset(actor_ids) or not isinstance(event['details'], dict)):
            raise ValueError('Invalid Object scene event actor identities/details')
        _object_number(event['time_s'], 'event time', 0)
        if event['end_time_s'] is not None:
            _object_number(event['end_time_s'], 'event end time', event['time_s'])
        provenance = event['provenance']
        _object_fields(provenance, {'source','artifact','sha256','method','frame_indices','line_numbers'}, 'event provenance')
        indices, numbers, details = provenance['frame_indices'], provenance['line_numbers'], event['details']
        if not isinstance(indices, list) or not isinstance(numbers, list):
            raise ValueError('Invalid Object scene event source references')
        if provenance['source'] == 'native_trace':
            if (provenance['artifact'] != 'object-trace.json' or provenance['sha256'] != source['object_trace_sha256']
                    or len(indices) != 2 or numbers or any(type(index) is not int or not 0 <= index < len(frames) for index in indices)
                    or indices[0] > indices[1]):
                raise ValueError('Object scene event differs from full native trace provenance')
            first, last = (frames[index] for index in indices)
            expected_method = {'collision':'sampled_obb_overlap','lane_transition':'sampled_lane_id_change',
                               'stopped':'sampled_speed_threshold_crossing'}.get(event['kind'])
            if provenance['method'] != expected_method:
                raise ValueError('Object scene sampled event method differs')
            if event['kind'] == 'collision':
                if (len(ids) != 2 or event['time_s'] != first['time_s'] or event['end_time_s'] != last['time_s']
                        or details.get('coordinate_plane') != 'world_xy'
                        or details.get('interpretation') != 'overlap_at_native_samples_not_continuous_contact_time'
                        or details.get('open_at_start') is not (indices[0] == 0)
                        or details.get('open_at_end') is not (indices[1] == len(frames)-1)
                        or details.get('before_time_s') != (frames[indices[0]-1]['time_s'] if indices[0] else None)
                        or details.get('after_time_s') != (frames[indices[1]+1]['time_s'] if indices[1]+1 < len(frames) else None)):
                    raise ValueError('Object scene collision sample interval differs from original trace')
                def overlap(index):
                    actors = {actor['id']:actor for actor in frames[index]['actors']}
                    return _object_footprints_overlap(actors[ids[0]],actors[ids[1]])
                if (not all(overlap(index) for index in range(indices[0],indices[1]+1))
                        or indices[0] > 0 and overlap(indices[0]-1)
                        or indices[1]+1 < len(frames) and overlap(indices[1]+1)):
                    raise ValueError('Object scene collision interval was not observed in native boxes')
            else:
                if (len(ids) != 1 or indices[1] != indices[0]+1 or event['time_s'] != last['time_s']
                        or event['end_time_s'] is not None or details.get('previous_time_s') != first['time_s']):
                    raise ValueError('Object scene transition interval differs from original trace')
                previous = next(actor for actor in first['actors'] if actor['id'] == ids[0])
                current = next(actor for actor in last['actors'] if actor['id'] == ids[0])
                if event['kind'] == 'lane_transition':
                    if (previous['lane_id'] == current['lane_id'] or details.get('from_lane_id') != previous['lane_id']
                            or details.get('to_lane_id') != current['lane_id']
                            or details.get('interpretation') != 'native_lane_id_changed_between_samples_not_action_completion'):
                        raise ValueError('Object scene lane transition was not observed in native states')
                elif (details.get('threshold_mps') != .05 or abs(previous['speed_mps']) <= .05
                      or abs(current['speed_mps']) > .05 or details.get('speed_mps') != current['speed_mps']
                      or details.get('interpretation') != 'sampled_speed_threshold_not_stop_action_completion'):
                    raise ValueError('Object scene stopped marker was not observed in native states')
        elif provenance['source'] == 'native_log':
            if (provenance['artifact'] != 'engine.log' or provenance['sha256'] != source['native_log_sha256']
                    or indices or len(numbers) != 1 or type(numbers[0]) is not int or not 1 <= numbers[0] <= len(lines)
                    or details.get('raw_line') != lines[numbers[0]-1] or event['end_time_s'] is not None):
                raise ValueError('Object scene event differs from original engine log provenance')
            match = re.fullmatch(r'\[(\d+(?:\.\d+)?)\] \[(?:info|warn)\] (.+)', details['raw_line'])
            if not match or float(match[1]) != event['time_s']:
                raise ValueError('Object scene event time differs from original log timestamp')
            message = match[2]
            if event['kind'] == 'collision':
                expected = f'Collision between {ids[0]} and {ids[1]}' if len(ids) == 2 else ''
                phase = details.get('phase')
                if (provenance['method'] != 'esmini_collision_log' or phase not in {'reported','dissolved'}
                        or details.get('interpretation') != 'engine_reported_collision_marker'
                        or message != expected + (' dissolved' if phase == 'dissolved' else '')):
                    raise ValueError('Object scene collision marker differs from original engine log')
            elif event['kind'] == 'native_action':
                index, state = details.get('action_index'), details.get('engine_state')
                if (len(ids) != 1 or provenance['method'] != 'esmini_event_state_log' or type(index) is not int
                        or not 0 <= index < len(by_actor[ids[0]]['actions']) or state not in {'runningState','completeState'}
                        or details.get('action_kind') != by_actor[ids[0]]['actions'][index]['kind']
                        or details.get('interpretation') != 'engine_event_transition_not_observed_behavior_completion'
                        or not re.fullmatch(re.escape(f'{ids[0]}_{index}_event') + r' \w+ -> \w+ -> '+state, message)):
                    raise ValueError('Object scene action marker differs from original engine event')
            else:
                raise ValueError('Unsupported Object scene engine log event kind')
        else:
            raise ValueError('Unsupported Object scene event provenance source')
