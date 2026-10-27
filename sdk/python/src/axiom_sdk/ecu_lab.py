"""SA8650 reference ECU authoring and execution through the shared versioned API."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/ecu-lab'

class EcuLabClientMixin:
    def ecu_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE+'/templates', params={'project_id': _identity(project_id)}).json()

    def ecu_lab_profiles(self, *, project_id='default'):
        return self._request('GET', BASE+'/profiles', params={'project_id': _identity(project_id)}).json()

    def save_ecu_lab_profile(self, *, name, config, idempotency_key, project_id='default', profile_id=None, expected_revision=0):
        return self._request('POST', BASE+'/profiles', json=dict(name=name, config=config,
            project_id=_identity(project_id), id=_identity(profile_id) if profile_id else None,
            expected_revision=expected_revision), headers=_key(idempotency_key)).json()

    def ecu_lab_profile(self, profile_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/profiles/'+_identity(profile_id),
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def ecu_lab_tests(self, *, project_id='default'):
        return self._request('GET', BASE+'/tests', params={'project_id': _identity(project_id)}).json()

    def save_ecu_lab_test(self, *, name, profile_ref, channels, idempotency_key, project_id='default',
                          commands=None, faults=None, seed=42, test_id=None, expected_revision=0):
        body = dict(name=name, project_id=_identity(project_id), profile_ref=profile_ref, channels=channels,
                    seed=seed, faults=faults or [], id=_identity(test_id) if test_id else None,
                    expected_revision=expected_revision)
        if commands is not None:
            body['commands'] = commands
        return self._request('POST', BASE+'/tests', json=body, headers=_key(idempotency_key)).json()

    def ecu_lab_test(self, test_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/tests/'+_identity(test_id),
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def preview_ecu_lab_test(self, test):
        return self._request('POST', BASE+'/tests/'+_identity(test['id'])+'/preview',
            json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def execute_ecu_lab_test(self, test, *, idempotency_key):
        """Keep the same idempotency key on retry after uncertain transport."""
        preview = self.preview_ecu_lab_test(test)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def ecu_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE+'/executions', params={'project_id': _identity(project_id)}).json()

    def export_ecu_lab_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] != 'ecu_timing':
            raise ValueError('Not an ECU timing execution')
        return self._request('GET', '/experiments/executions/'+_identity(execution_id)+'/export').json()
