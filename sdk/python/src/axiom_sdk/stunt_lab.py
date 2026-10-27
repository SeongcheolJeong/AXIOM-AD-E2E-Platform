"""Stunt Lab uses the same immutable design and Experiment contracts as the UI."""
from .collections import _key
from .perception import _identity

BASE = '/object-sim/stunt-lab'


class StuntLabClientMixin:
    def stunt_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE + '/templates', params={'project_id': _identity(project_id)}).json()

    def stunt_lab_readiness(self, *, project_id='default'):
        return self._request('GET', BASE + '/readiness', params={'project_id': _identity(project_id)}).json()

    def stunt_lab_designs(self, *, project_id='default'):
        return self._request('GET', BASE + '/designs', params={'project_id': _identity(project_id)}).json()

    def save_stunt_lab_design(self, *, name, config, idempotency_key, project_id='default', seed=42,
                              design_id=None, expected_revision=0):
        return self._request('POST', BASE + '/designs', json=dict(name=name, config=config, seed=seed,
            project_id=_identity(project_id), id=_identity(design_id) if design_id else None,
            expected_revision=expected_revision), headers=_key(idempotency_key)).json()

    def stunt_lab_design(self, design_id, *, revision, project_id='default'):
        return self._request('GET', BASE + '/designs/' + _identity(design_id),
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def preview_stunt_lab_design(self, design, *, project_id=None):
        return self._request('POST', BASE + '/designs/' + _identity(design['id']) + '/preview',
            json=dict(project_id=project_id or design['project_id'], revision=design['revision'], sha256=design['sha256'])).json()

    def execute_stunt_lab_design(self, design, *, idempotency_key):
        """Retry with the same key after uncertain transport; never invent another run."""
        preview = self.preview_stunt_lab_design(design)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def stunt_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE + '/executions', params={'project_id': _identity(project_id)}).json()

    def stunt_lab_comparison(self, left_execution_id, right_execution_id, *, project_id='default'):
        return self._request('GET', BASE + '/comparison', params=dict(project_id=_identity(project_id),
            left_execution_id=_identity(left_execution_id), right_execution_id=_identity(right_execution_id))).json()

    def export_stunt_lab_execution(self, execution_id, *, project_id='default'):
        run = self.experiment_execution(execution_id)
        if run['adapter_id'] != 'carla-stunt' or run['project_id'] != project_id:
            raise ValueError('Not a stunt execution in the requested project')
        return self._request('GET', '/experiments/executions/' + _identity(execution_id) + '/export').json()
