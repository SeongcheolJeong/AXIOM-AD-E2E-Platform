"""Common project beta workspace on the same versioned API as the UI.

These methods record guidance and link existing evidence. They never submit,
retry, or restart a simulator execution.
"""
from __future__ import annotations

import math
import re

from .collections import _revision
from .experiments import _object_map_identity
from .hil_beta import _headers
from .perception import _identity


def _choice(value, choices, label):
    if value not in choices:
        raise ValueError(f'Unsupported {label}; choose from {", ".join(choices)}')
    return value


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'Text must contain {0 if empty else 1}..{maximum} characters')
    return value.strip()


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 86400:
        raise ValueError('time_s must be finite and within 0..86400 seconds')
    return value


def _source_ref(value):
    """Check pin shape locally; the server checks project ownership and lineage."""
    object_keys = {'id', 'revision', 'configuration_hash', 'manifest_hash'}
    hil_keys = {'id', 'revision', 'sha256'}
    if not isinstance(value, dict) or set(value) not in (object_keys, hil_keys):
        raise ValueError('Supply an exact Object scenario, HIL test or Sensor layout revision reference')
    _identity(value['id'])
    _revision(value['revision'])
    for key in set(value) - {'id', 'revision'}:
        if not isinstance(value[key], str) or re.fullmatch('[a-f0-9]{64}', value[key]) is None:
            raise ValueError('Source references require lowercase SHA-256 hashes')
    return dict(value)


class BetaWorkspaceClientMixin:
    def beta_readiness(self, *, project_id='default'):
        """Inspect authoring, retained playback and fresh execution separately."""
        return self._request('GET', '/beta/readiness',
            params={'project_id': _object_map_identity(project_id)}).json()

    def beta_evidence(self, module, *, project_id='default'):
        """List project-owned evidence choices for a guided task."""
        return self._request('GET', '/beta/evidence', params={
            'project_id': _object_map_identity(project_id),
            'module': _choice(module, ('object', 'hil', 'log', 'sensor'), 'guided module'),
        }).json()

    def beta_tasks(self, *, project_id='default'):
        return self._request('GET', '/beta/tasks',
            params={'project_id': _object_map_identity(project_id)}).json()

    def beta_task(self, task_id, *, project_id='default'):
        return self._request('GET', f'/beta/tasks/{_identity(task_id)}',
            params={'project_id': _object_map_identity(project_id)}).json()

    def create_beta_task(self, module, *, title, idempotency_key, project_id='default',
                         mode='new_execution', pack_id=None, guide_id=None):
        """Start guidance only. A Log task resumes through the existing Log API."""
        body = dict(project_id=_object_map_identity(project_id),
            module=_choice(module, ('object', 'hil', 'log', 'sensor'), 'guided module'),
            title=_text(title, 180),
            mode=_choice(mode, ('new_execution', 'retained_playback'), 'task mode'))
        if module == 'log' and (pack_id is None or mode != 'new_execution'):
            raise ValueError('Log guidance requires a pack_id and new_execution mode')
        if pack_id is not None:
            body['pack_id'] = _identity(pack_id)
        if guide_id is not None:
            if module != 'sensor':
                raise ValueError('Sensor guides belong to Sensor Sim tasks')
            body['guide_id'] = _choice(guide_id, ('camera_direction', 'lidar_height', 'radar_direction'), 'Sensor guide')
        return self._request('POST', '/beta/tasks', json=body,
            headers=_headers(idempotency_key)).json()

    def update_beta_task(self, task_id, *, expected_revision, idempotency_key,
                         project_id='default', source_ref=None, execution_id=None,
                         observation_id=None, result_opened=None, time_s=None, sensor_key=None):
        """Link exact Object/HIL/Sensor evidence; use update_log_sim_beta_task for Log."""
        _revision(expected_revision)
        body = dict(project_id=_object_map_identity(project_id), expected_revision=expected_revision)
        if source_ref is not None:
            body['source_ref'] = _source_ref(source_ref)
        for key, value in (('execution_id', execution_id), ('observation_id', observation_id)):
            if value is not None:
                body[key] = _identity(value)
        if result_opened is not None:
            if type(result_opened) is not bool:
                raise ValueError('result_opened must be a boolean')
            body['result_opened'] = result_opened
        if time_s is not None:
            body['time_s'] = _time(time_s)
        if sensor_key is not None:
            body['sensor_key'] = _choice(sensor_key, ('front', 'rear', 'lidar', 'radar'), 'Sensor key')
        return self._request('POST', f'/beta/tasks/{_identity(task_id)}/progress',
            json=body, headers=_headers(idempotency_key)).json()

    def beta_feedback(self, *, project_id='default', task_id=None):
        params = {'project_id': _object_map_identity(project_id)}
        if task_id is not None:
            params['task_id'] = _identity(task_id)
        return self._request('GET', '/beta/feedback', params=params).json()

    def create_beta_feedback(self, module, *, kind, message, idempotency_key,
                             project_id='default', task_id=None, execution_id=None, stage=None,
                             error=None, time_s=None):
        """Capture task/execution context verified by the server, without file attachments."""
        body = dict(project_id=_object_map_identity(project_id),
            module=_choice(module, ('object', 'log', 'hil', 'sensor', 'neural'), 'feedback module'),
            kind=_choice(kind, ('issue', 'help', 'completion'), 'feedback kind'),
            message=_text(message, 4000))
        if task_id is not None:
            body['task_id'] = _identity(task_id)
        if execution_id is not None:
            body['execution_id'] = _identity(execution_id)
        for key, value, maximum in (('stage', stage, 80), ('error', error, 1000)):
            if value is not None:
                body[key] = _text(value, maximum)
        if time_s is not None:
            if task_id is None and execution_id is None:
                raise ValueError('A replay time requires verified task or execution evidence')
            body['time_s'] = _time(time_s)
        return self._request('POST', '/beta/feedback', json=body,
            headers=_headers(idempotency_key)).json()

    def triage_beta_feedback(self, feedback_id, *, expected_revision, status,
                             idempotency_key, project_id='default', note=None):
        """Operator-only issue processing; project Admin tokens cannot triage."""
        _revision(expected_revision)
        body = dict(project_id=_object_map_identity(project_id), expected_revision=expected_revision,
            status=_choice(status, ('received', 'investigating', 'resolved'), 'triage status'))
        if note is not None:
            body['note'] = _text(note, 4000, empty=True)
        return self._request('POST', f'/beta/feedback/{_identity(feedback_id)}/triage',
            json=body, headers=_headers(idempotency_key)).json()

    def beta_overview(self, *, format='json'):
        """Operator-only cross-project overview; CSV returns text without parsing."""
        _choice(format, ('json', 'csv'), 'export format')
        response = self._request('GET', '/beta/overview', params={'format': format})
        return response.json() if format == 'json' else response.text
