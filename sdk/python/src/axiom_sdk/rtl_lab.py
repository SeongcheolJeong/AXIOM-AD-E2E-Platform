"""Immutable reference FIFO authoring and real RTL A/B execution API."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/rtl-lab'


class RtlLabClientMixin:
    def rtl_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE + '/templates', params={'project_id': _identity(project_id)}).json()

    def rtl_lab_readiness(self, *, project_id='default'):
        return self._request('GET', BASE + '/readiness', params={'project_id': _identity(project_id)}).json()

    def rtl_lab_tests(self, *, project_id='default'):
        return self._request('GET', BASE + '/tests', params={'project_id': _identity(project_id)}).json()

    def save_rtl_lab_test(self, *, name, config, idempotency_key, project_id='default',
                          seed=42, test_id=None, expected_revision=0):
        body = dict(name=name, project_id=_identity(project_id), config=config, seed=seed,
                    id=_identity(test_id) if test_id else None, expected_revision=expected_revision)
        return self._request('POST', BASE + '/tests', json=body, headers=_key(idempotency_key)).json()

    def rtl_lab_test(self, test_id, *, revision, project_id='default'):
        return self._request('GET', BASE + '/tests/' + _identity(test_id),
                             params={'project_id': _identity(project_id), 'revision': revision}).json()

    def preview_rtl_lab_test(self, test):
        return self._request('POST', BASE + '/tests/' + _identity(test['id']) + '/preview',
                             json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def preflight_rtl_lab_test(self, test):
        return self._request('POST', BASE + '/tests/' + _identity(test['id']) + '/preflight',
                             json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def execute_rtl_lab_test(self, test, *, idempotency_key):
        """Reuse the same key on retry after an uncertain transport outcome."""
        preview = self.preview_rtl_lab_test(test)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def rtl_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE + '/executions', params={'project_id': _identity(project_id)}).json()

    def export_rtl_lab_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] != 'fpga_rtl':
            raise ValueError('Not a reference FIFO RTL execution')
        return self._request('GET', '/experiments/executions/' + _identity(execution_id) + '/export').json()
