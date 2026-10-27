"""FPGA Lab shares versioned design and Experiment APIs with the UI."""
from .collections import _key
from .perception import _identity

BASE = '/hil-sim/fpga-lab'

class FpgaLabClientMixin:
    def fpga_lab_templates(self, *, project_id='default'):
        return self._request('GET', BASE+'/templates', params={'project_id':_identity(project_id)}).json()

    def fpga_lab_designs(self, *, project_id='default'):
        return self._request('GET', BASE+'/designs', params={'project_id':_identity(project_id)}).json()

    def save_fpga_lab_design(self, *, name, config, idempotency_key, project_id='default', seed=42,
                             design_id=None, expected_revision=0):
        return self._request('POST', BASE+'/designs', json=dict(name=name, config=config, seed=seed,
            project_id=_identity(project_id), id=_identity(design_id) if design_id else None,
            expected_revision=expected_revision), headers=_key(idempotency_key)).json()

    def fpga_lab_design(self, design_id, *, revision, project_id='default'):
        return self._request('GET', BASE+'/designs/'+_identity(design_id),
            params={'project_id':_identity(project_id),'revision':revision}).json()

    def preview_fpga_lab_design(self, design, *, project_id=None):
        return self._request('POST', BASE+'/designs/'+_identity(design['id'])+'/preview',
            json=dict(project_id=project_id or design['project_id'], revision=design['revision'], sha256=design['sha256'])).json()

    def execute_fpga_lab_design(self, design, *, idempotency_key):
        """Retry with the same key after uncertain transport; never invent a new run."""
        preview = self.preview_fpga_lab_design(design)
        experiment = self.create_experiment(**preview['experiment'], idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'], preview_hash=preview['preview_hash'], idempotency_key=idempotency_key)

    def fpga_lab_executions(self, *, project_id='default'):
        return self._request('GET', BASE+'/executions', params={'project_id':_identity(project_id)}).json()

    def export_fpga_lab_execution(self, execution_id):
        run = self.experiment_execution(execution_id)
        if run['experiment_kind'] != 'fpga_timing':
            raise ValueError('Not an FPGA timing execution')
        return self._request('GET', '/experiments/executions/'+_identity(execution_id)+'/export').json()
