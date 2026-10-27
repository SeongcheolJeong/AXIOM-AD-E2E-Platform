"""Project-owned multi-log reference replay; writes and retries are explicit."""
from .collections import _key, _revision
from .perception import _identity


def _selection(source_ids, project_id, library_id, selected_scene_ids):
    if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 64 or len(set(source_ids)) != len(source_ids):
        raise ValueError('Select 1..64 distinct source intervals')
    result = {'project_id': _identity(project_id), 'source_ids': [_identity(value) for value in source_ids]}
    if library_id is not None:
        result['library_id'] = _identity(library_id)
    if selected_scene_ids is not None:
        if library_id is None or not isinstance(selected_scene_ids, list) or not 1 <= len(selected_scene_ids) <= 20 or len(set(selected_scene_ids)) != len(selected_scene_ids):
            raise ValueError('Select 1..20 distinct scenes from a pinned library')
        result['selected_scene_ids'] = [_identity(value) for value in selected_scene_ids]
    return result


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Use the exact preview or report SHA-256')
    return value


class LogSimBatchClientMixin:
    def log_sim_library(self, *, project_id='default', library_id=None):
        params = {'project_id': _identity(project_id)}
        if library_id is not None:
            params['library_id'] = _identity(library_id)
        return self._request('GET', '/log-sim/library', params=params).json()

    def preview_log_sim_batch(self, source_ids, *, project_id='default', library_id=None, selected_scene_ids=None, comparison_ref=None):
        body = _selection(source_ids, project_id, library_id, selected_scene_ids)
        if comparison_ref is not None: body['comparison_ref'] = comparison_ref
        return self._request('POST', '/log-sim/batches/preview', json=body).json()

    def submit_log_sim_batch(self, source_ids, *, preview_hash, idempotency_key,
                             project_id='default', library_id=None, selected_scene_ids=None, comparison_ref=None):
        body = _selection(source_ids, project_id, library_id, selected_scene_ids)
        if comparison_ref is not None: body['comparison_ref'] = comparison_ref
        return self._request('POST', '/log-sim/batches', json=body | {'preview_hash': _hash(preview_hash)},
            headers=_key(idempotency_key)).json()

    def log_sim_batches(self, *, project_id='default'):
        return self._request('GET', '/log-sim/batches', params={'project_id': _identity(project_id)}).json()

    def log_sim_batch(self, batch_id, *, project_id='default'):
        return self._request('GET', f'/log-sim/batches/{_identity(batch_id)}',
            params={'project_id': _identity(project_id)}).json()

    def retry_log_sim_batch(self, batch_id, *, idempotency_key, project_id='default'):
        return self._request('POST', f'/log-sim/batches/{_identity(batch_id)}/retry',
            json={'project_id': _identity(project_id)}, headers=_key(idempotency_key)).json()

    def save_log_sim_case_bundle(self, batch_id, *, name, candidate_ids, report_hash, idempotency_key, project_id='default'):
        if not isinstance(name, str) or not 1 <= len(name) <= 180:
            raise ValueError('Case bundle name must contain 1..180 characters')
        if not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= 100 or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError('Select 1..100 distinct evaluated candidate IDs')
        return self._request('POST', f'/log-sim/batches/{_identity(batch_id)}/case-bundles',
            json={'project_id': _identity(project_id), 'name': name,
                'candidate_ids': [_identity(value) for value in candidate_ids], 'report_hash': _hash(report_hash)},
            headers=_key(idempotency_key)).json()

    def log_sim_case_bundles(self, *, project_id='default'):
        return self._request('GET', '/log-sim/case-bundles', params={'project_id': _identity(project_id)}).json()

    def log_sim_case_bundle(self, bundle_id, *, project_id='default'):
        return self._request('GET', f'/log-sim/case-bundles/{_identity(bundle_id)}',
            params={'project_id': _identity(project_id)}).json()

    def upload_log_sim_mcap(self, path, *, project_id='default'):
        from pathlib import Path
        path = Path(path)
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('MCAP upload exceeds 128 MiB')
        return self._request('POST', '/log-sim/uploads', params={'project_id': _identity(project_id), 'name': path.name},
            content=path.read_bytes(), headers={'Content-Type': 'application/octet-stream'}).json()

    def log_sim_uploads(self, *, project_id='default'):
        return self._request('GET', '/log-sim/uploads', params={'project_id': _identity(project_id)}).json()

    def inspect_log_sim_input(self, **selection):
        return self._request('POST', '/log-sim/inputs/inspect', json=selection).json()

    def register_log_sim_input(self, *, preview_hash, **selection):
        return self._request('POST', '/log-sim/inputs', json=selection | {'preview_hash': _hash(preview_hash)}).json()

    def log_sim_comparison_settings(self, *, project_id='default'):
        return self._request('GET', '/log-sim/comparison-settings', params={'project_id': _identity(project_id)}).json()

    def save_log_sim_comparison_settings(self, *, idempotency_key, **definition):
        return self._request('POST', '/log-sim/comparison-settings', json=definition, headers=_key(idempotency_key)).json()

    def log_sim_reviews(self, case_id, *, project_id='default'):
        return self._request('GET', '/log-sim/reviews', params={'project_id': _identity(project_id), 'case_id': _identity(case_id)}).json()

    def save_log_sim_review(self, *, idempotency_key, **definition):
        return self._request('POST', '/log-sim/reviews', json=definition, headers=_key(idempotency_key)).json()

    def export_log_sim_batch(self, batch_id, *, project_id='default', format='json'):
        if format not in ('json', 'csv'): raise ValueError('Choose json or csv')
        response = self._request('GET', f'/log-sim/batches/{_identity(batch_id)}/export',
            params={'project_id': _identity(project_id), 'format': format})
        return response.json() if format == 'json' else response.text

    def log_sim_packs(self, *, project_id='default'):
        return self._request('GET', '/log-sim/packs', params={'project_id': _identity(project_id)}).json()

    def bind_log_sim_pack(self, pack_id, **definition):
        return self._request('POST', f'/log-sim/packs/{_identity(pack_id)}/binding', json=definition).json()

    def log_sim_example(self, *, project_id='default', invalid=False):
        return self._request('GET', '/log-sim/example.mcap', params={'project_id': _identity(project_id), 'invalid': invalid}).content

    def log_sim_comparison_setting(self, setting_id, *, revision, project_id='default'):
        """Read the exact saved revision used by a resumed beta task."""
        _revision(revision)
        return self._request('GET', f'/log-sim/comparison-settings/{_identity(setting_id)}',
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def create_log_sim_beta_task(self, pack_id, *, idempotency_key, project_id='default'):
        """Start guided authoring; creating a task does not execute its comparison."""
        return self._request('POST', '/log-sim/beta-tasks',
            json={'project_id': _identity(project_id), 'pack_id': _identity(pack_id)},
            headers=_key(idempotency_key)).json()

    def log_sim_beta_tasks(self, *, project_id='default'):
        return self._request('GET', '/log-sim/beta-tasks',
            params={'project_id': _identity(project_id)}).json()

    def log_sim_beta_task(self, task_id, *, project_id='default'):
        """Read authoritative progress derived from saved execution and Case evidence."""
        return self._request('GET', f'/log-sim/beta-tasks/{_identity(task_id)}',
            params={'project_id': _identity(project_id)}).json()

    def update_log_sim_beta_task(self, task_id, *, expected_revision, idempotency_key,
                                project_id='default', comparison_ref=None, batch_id=None,
                                case_id=None, result_opened=None, source_id=None):
        _revision(expected_revision)
        body = {'project_id': _identity(project_id), 'expected_revision': expected_revision}
        if comparison_ref is not None:
            body['comparison_ref'] = comparison_ref
        for key, value in (('batch_id', batch_id), ('case_id', case_id), ('source_id', source_id)):
            if value is not None:
                body[key] = _identity(value)
        if result_opened is not None:
            if type(result_opened) is not bool:
                raise ValueError('result_opened must be a boolean')
            body['result_opened'] = result_opened
        return self._request('POST', f'/log-sim/beta-tasks/{_identity(task_id)}/progress',
            json=body, headers=_key(idempotency_key)).json()

    def save_log_sim_beta_feedback(self, task_id, *, stage, kind, message, idempotency_key,
                                   project_id='default', needs_help=None):
        if stage not in ('source', 'settings', 'execution', 'investigation', 'review', 'completed'):
            raise ValueError('Choose a guided beta task stage')
        if kind not in ('blocked', 'completion'):
            raise ValueError('Choose blocked or completion feedback')
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError('Feedback must contain 1..4000 characters')
        if needs_help is not None and type(needs_help) is not bool:
            raise ValueError('needs_help must be a boolean or None')
        return self._request('POST', '/log-sim/beta-feedback', json={
            'project_id': _identity(project_id), 'task_id': _identity(task_id),
            'stage': stage, 'kind': kind, 'message': message, 'needs_help': needs_help,
        }, headers=_key(idempotency_key)).json()

    def log_sim_beta_feedback(self, *, project_id='default', task_id=None):
        params = {'project_id': _identity(project_id)}
        if task_id is not None:
            params['task_id'] = _identity(task_id)
        return self._request('GET', '/log-sim/beta-feedback', params=params).json()

    def log_sim_beta_overview(self, *, format='json'):
        """Operator-only cross-project progress; CSV is returned as text."""
        if format not in ('json', 'csv'):
            raise ValueError('Choose json or csv')
        response = self._request('GET', '/log-sim/beta-overview', params={'format': format})
        return response.json() if format == 'json' else response.text
