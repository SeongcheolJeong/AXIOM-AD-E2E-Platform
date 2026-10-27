"""Immutable RTL/SPICE/ISP/ADAS authoring and native engineering execution API."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/engineering-lab'


class EngineeringLabClientMixin:
    def preview_adas_campaign_plan(self, config, *, project_id="default"):
        """Read deterministic native case schedules without executing or requiring a worker."""
        return self._request("POST", BASE + "/campaign-plan", params={"project_id": _identity(project_id)}, json=config).json()

    def engineering_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE + '/templates', params={'project_id': _identity(project_id)}).json()

    def engineering_lab_readiness(self, *, project_id='default'):
        return self._request('GET', BASE + '/readiness', params={'project_id': _identity(project_id)}).json()

    def engineering_lab_tests(self, *, project_id='default'):
        return self._request('GET', BASE + '/tests', params={'project_id': _identity(project_id)}).json()

    def save_engineering_lab_test(self, *, name, config, idempotency_key, project_id='default',
                          seed=42, test_id=None, expected_revision=0):
        """Save the engine-specific configuration unchanged, including four-channel ADAS RAW refs."""
        body = dict(name=name, project_id=_identity(project_id), config=config, seed=seed,
                    id=_identity(test_id) if test_id else None, expected_revision=expected_revision)
        return self._request('POST', BASE + '/tests', json=body, headers=_key(idempotency_key)).json()

    def engineering_lab_test(self, test_id, *, revision, project_id='default'):
        return self._request('GET', BASE + '/tests/' + _identity(test_id),
                             params={'project_id': _identity(project_id), 'revision': revision}).json()

    def preview_engineering_lab_test(self, test):
        return self._request('POST', BASE + '/tests/' + _identity(test['id']) + '/preview',
                             json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def preflight_engineering_lab_test(self, test):
        return self._request('POST', BASE + '/tests/' + _identity(test['id']) + '/preflight',
                             json=dict(project_id=test['project_id'], revision=test['revision'], sha256=test['sha256'])).json()

    def execute_engineering_lab_test(self, test, *, idempotency_key):
        """Execute the exact saved revision; works for bundled ISP/ADAS and native RTL/SPICE.

        Reuse the same key on retry after an uncertain transport outcome. ADAS hardware
        binding is unsupported; this API schedules the native software RTL simulator.
        """
        preview = self.preview_engineering_lab_test(test)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def engineering_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE + '/executions', params={'project_id': _identity(project_id)}).json()

    def adas_campaign_progress(self, execution_id, *, project_id='default'):
        """Read provisional counters; only a sealed terminal result supplies a verdict."""
        return self._request('GET', BASE + '/executions/' + _identity(execution_id) + '/campaign-progress',
            params={'project_id': _identity(project_id)}).json()

    def preview_engineering_lab_build(self, test, *, build_profile_id='ecp5-85k-cabga381-6-ooc/1'):
        return self._request('POST', BASE + '/tests/' + _identity(test['id']) + '/build-preview',
            json=dict(project_id=test['project_id'],revision=test['revision'],sha256=test['sha256'],
                      build_profile_id=build_profile_id)).json()

    def execute_engineering_lab_build(self, test, *, idempotency_key, build_profile_id='ecp5-85k-cabga381-6-ooc/1'):
        """Build is a separate job/domain; completion never establishes pixel correctness."""
        preview = self.preview_engineering_lab_build(test, build_profile_id=build_profile_id)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def export_engineering_lab_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] not in {'engineering_lab', 'fpga_build'}:
            raise ValueError('Not an Engineering Lab execution')
        return self._request('GET', '/experiments/executions/' + _identity(execution_id) + '/export').json()
