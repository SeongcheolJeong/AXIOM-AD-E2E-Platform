"""Revisioned Software-DUT tests and user observations on the shared HIL API."""
from __future__ import annotations

import math
import re
from .experiments import _object_map_identity, _object_map_revision
from .hil_demo import _preset, _validated_parameters


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise ValueError('A lowercase SHA-256 value is required')
    return value


def _ref(value):
    if not isinstance(value, dict) or set(value) != {'id', 'revision', 'sha256'}:
        raise ValueError('Test reference requires id, revision and sha256')
    _object_map_identity(value['id']); _object_map_revision(value['revision']); _hash(value['sha256'])
    return dict(value)


def _scene_ref(value):
    value = _ref(value)
    if value['id'] not in {'lead_deceleration', 'lead_stop_go'} or value['revision'] != 1:
        raise ValueError('Unsupported HIL scene profile reference')
    return value


def _headers(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 180:
        raise ValueError('idempotency_key must contain 1..180 characters')
    return {'Idempotency-Key': key}


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError('Text must be nonempty and within the supported length')
    return value.strip()


def _page(project_id, limit, offset, *, maximum=100):
    if type(limit) is not int or not 1 <= limit <= maximum or type(offset) is not int or offset < 0:
        raise ValueError(f'Pagination requires limit1..{maximum} and nonnegative offset')
    return dict(project_id=_object_map_identity(project_id), limit=limit, offset=offset)


class HilExecutionLinkError(RuntimeError):
    """Execution exists; retry attachment using this ID instead of submitting anew."""
    def __init__(self, execution, test_ref):
        super().__init__('HIL execution was submitted but its test link was not confirmed. Retry attach_hil_sim_test_execution with the retained execution_id.')
        self.execution = execution
        self.execution_id = execution['id']
        self.test_ref = dict(test_ref)


class HilBetaClientMixin:
    def hil_sim_dut_profiles(self, *, project_id='default', limit=100, offset=0):
        return self._request('GET', '/hil-sim/dut-profiles', params=_page(project_id, limit, offset)).json()

    def hil_sim_dut_profile(self, profile_id, *, project_id='default', revision=None):
        params = {'project_id': _object_map_identity(project_id)}
        if revision is not None:
            _object_map_revision(revision); params['revision'] = revision
        return self._request('GET', f'/hil-sim/dut-profiles/{_object_map_identity(profile_id)}', params=params).json()

    def save_hil_sim_dut_profile(self, *, name, parameters, idempotency_key, project_id='default',
                                 profile_id=None, expected_revision=0, note=''):
        """Save an immutable named profile; this does not contact the DUT/engine."""
        _object_map_revision(expected_revision, minimum=0)
        if not isinstance(parameters, dict) or set(parameters) != {'max_object_age_ms', 'recovery_fresh_steps'}:
            raise ValueError('DUT profile requires input age and fresh recovery count')
        age, recovery = parameters['max_object_age_ms'], parameters['recovery_fresh_steps']
        if type(age) is not int or not 50 <= age <= 1000 or age % 50 or type(recovery) is not int or not 1 <= recovery <= 10:
            raise ValueError('DUT profile requires integer age50..1000ms in50ms steps and recovery1..10')
        body = dict(project_id=_object_map_identity(project_id), name=_text(name,180), note=_text(note,4000,empty=True),
                    parameters=dict(parameters), expected_revision=expected_revision)
        if profile_id is not None:
            body['id'] = _object_map_identity(profile_id)
        return self._request('POST', '/hil-sim/dut-profiles', json=body, headers=_headers(idempotency_key)).json()

    def hil_sim_tests(self, *, project_id='default', limit=100, offset=0):
        return self._request('GET', '/hil-sim/tests', params=_page(project_id, limit, offset)).json()

    def hil_sim_test(self, test_id, *, project_id='default', revision=None):
        params = {'project_id': _object_map_identity(project_id)}
        if revision is not None:
            _object_map_revision(revision); params['revision'] = revision
        return self._request('GET', f'/hil-sim/tests/{_object_map_identity(test_id)}', params=params).json()

    def save_hil_sim_test(self, *, name, preset_id, binding_revision, binding_sha256, parameters,
                          idempotency_key, project_id='default', test_id=None, expected_revision=0,
                          note='', copied_from=None, dut_profiles=None, scene_profile=None):
        """Save a pinned test without connecting to or executing an engine."""
        _preset(preset_id); _object_map_revision(binding_revision)
        _object_map_revision(expected_revision, minimum=0)
        parameters = _validated_parameters(preset_id, parameters, require_primary=True)
        body = dict(project_id=_object_map_identity(project_id), name=_text(name,180), note=_text(note,4000,empty=True),
                    preset_id=preset_id, binding_revision=binding_revision, binding_sha256=_hash(binding_sha256),
                    parameters=parameters, expected_revision=expected_revision)
        if test_id is not None: body['id'] = _object_map_identity(test_id)
        if copied_from is not None: body['copied_from'] = _ref(copied_from)
        if dut_profiles is not None:
            if not isinstance(dut_profiles, dict) or set(dut_profiles) != {'baseline', 'faulted'}:
                raise ValueError('DUT comparison requires exact baseline and faulted profile references')
            body['dut_profiles'] = {side: _ref(reference) for side, reference in dut_profiles.items()}
        if scene_profile is not None:
            body['scene_profile'] = _scene_ref(scene_profile)
        return self._request('POST', '/hil-sim/tests', json=body, headers=_headers(idempotency_key)).json()

    def preview_hil_sim_test(self, test_ref, *, project_id='default'):
        """Preflight the exact saved revision. Native unavailability remains an error."""
        reference = _ref(test_ref)
        return self._request('POST', f'/hil-sim/tests/{reference["id"]}/preview',
                             json=dict(project_id=_object_map_identity(project_id), test_ref=reference)).json()

    def attach_hil_sim_test_execution(self, test_ref, execution_id, *, idempotency_key, project_id='default'):
        """Associate an existing execution; the server verifies its exact source and fault."""
        reference = _ref(test_ref)
        return self._request('POST', f'/hil-sim/tests/{reference["id"]}/executions',
            json=dict(project_id=_object_map_identity(project_id), test_ref=reference,
                      execution_id=_object_map_identity(execution_id)), headers=_headers(idempotency_key)).json()

    def hil_sim_test_executions(self, test_id, *, project_id='default', revision=None, limit=100, offset=0):
        params = _page(project_id, limit, offset)
        if revision is not None:
            _object_map_revision(revision); params['revision'] = revision
        return self._request('GET', f'/hil-sim/tests/{_object_map_identity(test_id)}/executions', params=params).json()

    def create_hil_sim_observation(self, *, execution_id, time_s, title, note, idempotency_key,
                                   project_id='default', test_ref=None, side='both', kind='observation'):
        if type(time_s) not in (int,float) or not math.isfinite(time_s) or not 0 <= time_s <= 18:
            raise ValueError('Observation response time must be finite and within0..18 seconds')
        if side not in {'baseline','faulted','both'} or kind not in {'observation','issue'}:
            raise ValueError('Unsupported observation side or kind')
        body = dict(project_id=_object_map_identity(project_id), execution_id=_object_map_identity(execution_id),
                    time_s=time_s, title=_text(title,180), note=_text(note,4000), side=side, kind=kind)
        if test_ref is not None: body['test_ref'] = _ref(test_ref)
        return self._request('POST','/hil-sim/observations',json=body,headers=_headers(idempotency_key)).json()

    def hil_sim_observations(self, *, project_id='default', execution_id=None, test_id=None, revision=None,
                             limit=100, offset=0):
        params = _page(project_id,limit,offset,maximum=200)
        for key,value in [('execution_id',execution_id),('test_id',test_id)]:
            if value is not None: params[key] = _object_map_identity(value)
        if revision is not None:
            _object_map_revision(revision); params['revision'] = revision
        return self._request('GET','/hil-sim/observations',params=params).json()

    def export_hil_sim_test(self, test_id, *, project_id='default', revision=None):
        """Export test, linked receipts and user notes, without raw credentials or videos."""
        params = {'project_id': _object_map_identity(project_id)}
        if revision is not None:
            _object_map_revision(revision); params['revision'] = revision
        return self._request('GET',f'/hil-sim/tests/{_object_map_identity(test_id)}/export',params=params).json()
