"""Project-scoped graph authoring and native FPGA workbench executions."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/fpga-workbench'

class FpgaWorkbenchClientMixin:
    def fpga_workbench_templates(self, *, project_id='default'):
        return self._request('GET', BASE+'/templates', params={'project_id': _identity(project_id)}).json()

    def fpga_workbench_readiness(self, *, project_id='default'):
        return self._request('GET', BASE+'/readiness', params={'project_id': _identity(project_id)}).json()

    def fpga_workbench_benches(self, *, project_id='default'):
        return self._request('GET', BASE+'/benches', params={'project_id': _identity(project_id)}).json()

    def save_fpga_workbench(self, *, name, graph, idempotency_key, settings=None, seed=42, probes=None,
                            panel_order=None, project_id='default', bench_id=None, expected_revision=0):
        return self._request('POST', BASE+'/benches', headers=_key(idempotency_key), json=dict(
            name=name, project_id=_identity(project_id), graph=graph, settings=settings if settings is not None else {},
            seed=seed, probes=probes or [], panel_order=panel_order or [], id=_identity(bench_id) if bench_id else None,
            expected_revision=expected_revision)).json()

    def fpga_workbench(self, bench_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/benches/'+_identity(bench_id), params={
            'revision': revision, 'project_id': _identity(project_id)}).json()

    def validate_fpga_workbench(self, *, name, graph, settings=None, seed=42, probes=None, panel_order=None, project_id='default'):
        return self._request('POST', BASE+'/validate', json=dict(name=name, graph=graph,
            project_id=_identity(project_id), settings=settings if settings is not None else {}, seed=seed,
            probes=probes or [], panel_order=panel_order or [])).json()

    def preview_fpga_workbench(self, bench):
        return self._request('POST', BASE+'/benches/'+_identity(bench['id'])+'/preview', json={
            k: bench[k] for k in ['project_id', 'revision', 'sha256']}).json()

    def execute_fpga_workbench(self, bench, *, idempotency_key):
        """Reuse the same key after an uncertain response; no automatic replacement run."""
        preview = self.preview_fpga_workbench(bench)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def fpga_workbench_executions(self, *, project_id='default'):
        return self._request('GET', BASE+'/executions', params={'project_id': _identity(project_id)}).json()

    def export_fpga_workbench_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] != 'fpga_workbench':
            raise ValueError('Not an FPGA workbench execution')
        return self._request('GET', '/experiments/executions/'+_identity(execution_id)+'/export').json()
