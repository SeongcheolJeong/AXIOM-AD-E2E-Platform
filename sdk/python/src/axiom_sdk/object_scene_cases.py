"""Investigation Cases over exact retained simulator frames; no new execution."""
from __future__ import annotations

import hashlib
import math

from .collections import _key
from .experiments import _object_map_identity, _object_map_revision
from .map_sources import _pin


def _base(execution_id: str) -> str:
    return f'/object-sim/executions/{_object_map_identity(execution_id)}/cases'


def _case_identity(value: dict, project_id: str, execution_id: str, case_id: str | None = None, revision: int | None = None):
    link = value.get('object_sim_evidence') or {}
    if (link.get('project_id') != project_id or link.get('execution_id') != execution_id
            or case_id is not None and value.get('id') != case_id
            or revision is not None and value.get('revision') != revision):
        raise ValueError('Object Sim Case response differs from the pinned project/execution/revision')
    return value


class ObjectSceneCasesClientMixin:
    def create_object_scene_case(self, execution_id: str, *, execution_manifest_hash: str, scene_sha256: str,
                                 frame_index: int, time_s: float, title: str, note: str,
                                 idempotency_key: str, project_id: str = 'default', assignee: str = ''):
        """Pin the exact selected frame into a recording-backed investigation Case.

        The preserved JSON frame retains CARLA coordinates. This is simulation
        evidence, not a measured sensor capture or a new safety assessment.
        """
        project_id = _object_map_identity(project_id)
        path = _base(execution_id)
        if type(frame_index) is not int or not 0 <= frame_index <= 100000:
            raise ValueError('frame_index must be an integer in 0..100000')
        if type(time_s) not in (int, float) or not math.isfinite(time_s) or not 0 <= time_s <= 120:
            raise ValueError('time_s must be a finite simulation time in 0..120')
        if not isinstance(title, str) or not title.strip() or len(title) > 180 or not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise ValueError('A bounded Case title and investigation note are required')
        body = {'project_id': project_id, 'execution_manifest_hash': _pin(1, execution_manifest_hash)['sha256'],
                'scene_sha256': _pin(1, scene_sha256)['sha256'], 'frame_index': frame_index, 'time_s': time_s,
                'title': title, 'note': note, 'assignee': assignee}
        value = _case_identity(self._request('POST', path, json=body, headers=_key(idempotency_key)).json(), project_id, execution_id)
        link = value['object_sim_evidence']
        if any(link.get(key) != body[key] for key in ('execution_manifest_hash', 'scene_sha256', 'frame_index', 'time_s')):
            raise ValueError('Object Sim Case response differs from the selected frame hashes/time')
        return value

    def object_scene_case(self, execution_id: str, case_id: str, *, revision: int = 1, project_id: str = 'default'):
        """Read a project-authorized immutable Case revision and its lineage."""
        _object_map_revision(revision)
        project_id = _object_map_identity(project_id)
        path = _base(execution_id) + '/' + _object_map_identity(case_id)
        value = self._request('GET', path, params={'project_id': project_id, 'revision': revision}).json()
        return _case_identity(value, project_id, execution_id, case_id, revision)

    def export_object_scene_case(self, execution_id: str, case_id: str, *, expected_manifest_hash: str,
                                 revision: int = 1, project_id: str = 'default') -> bytes:
        """Download the sealed JSON and verify it against the caller's exact pin."""
        _object_map_revision(revision)
        expected = _pin(1, expected_manifest_hash)['sha256']
        path = _base(execution_id) + '/' + _object_map_identity(case_id) + '/export'
        response = self._request('GET', path, params={'project_id': _object_map_identity(project_id), 'revision': revision})
        if (not 0 < len(response.content) <= 2 * 1024 * 1024
                or response.headers.get('X-Content-SHA256') != expected
                or hashlib.sha256(response.content).hexdigest() != expected):
            raise ValueError('Object Sim Case export differs from the pinned manifest SHA-256')
        return response.content
