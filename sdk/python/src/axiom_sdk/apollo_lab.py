"""Apollo reference camera authoring and execution through the shared versioned API."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/apollo-lab'

class ApolloLabClientMixin:
    def apollo_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE+'/templates', params={'project_id': _identity(project_id)}).json()

    def apollo_lab_profiles(self, *, project_id='default'):
        return self._request('GET', BASE+'/profiles', params={'project_id': _identity(project_id)}).json()

    def save_apollo_lab_profile(self, *, name, config, idempotency_key, project_id='default', profile_id=None, expected_revision=0):
        return self._request('POST', BASE+'/profiles', json=dict(name=name, config=config,
            project_id=_identity(project_id), id=_identity(profile_id) if profile_id else None,
            expected_revision=expected_revision), headers=_key(idempotency_key)).json()

    def apollo_lab_profile(self, profile_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/profiles/'+_identity(profile_id),
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def apollo_lab_tests(self, *, project_id='default'):
        return self._request('GET', BASE+'/tests', params={'project_id': _identity(project_id)}).json()

    def save_apollo_lab_test(self, *, name, profile_ref, channels, idempotency_key, project_id='default',
                          captures=None, seed=42, test_id=None, expected_revision=0):
        body = dict(name=name, project_id=_identity(project_id), profile_ref=profile_ref, channels=channels,
                    seed=seed, captures=captures or [], id=_identity(test_id) if test_id else None,
                    expected_revision=expected_revision)
        return self._request('POST', BASE+'/tests', json=body, headers=_key(idempotency_key)).json()

    def apollo_lab_test(self, test_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/tests/'+_identity(test_id),
            params={'project_id': _identity(project_id), 'revision': revision}).json()

    def preview_apollo_lab_test(self, test):
        return self._request('POST', BASE+'/tests/'+_identity(test['id'])+'/preview',
            json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def execute_apollo_lab_test(self, test, *, idempotency_key):
        """Keep the same idempotency key on retry after uncertain transport."""
        preview = self.preview_apollo_lab_test(test)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def apollo_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE+'/executions', params={'project_id': _identity(project_id)}).json()

    def export_apollo_lab_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] != 'apollo_camera':
            raise ValueError('Not an Apollo camera execution')
        return self._request('GET', '/experiments/executions/'+_identity(execution_id)+'/export').json()

    def apollo_lab_assets(self, *, project_id='default'):
        return self._request('GET',BASE+'/assets',params={'project_id':project_id}).json()

    def upload_apollo_lab_raw(self, data, *, metadata, name='RAW input', project_id='default'):
        import json
        return self._request('POST',BASE+'/assets',params={'project_id':project_id,'name':name,'metadata':json.dumps(metadata)},content=data,headers={'Content-Type':'application/octet-stream'}).json()
