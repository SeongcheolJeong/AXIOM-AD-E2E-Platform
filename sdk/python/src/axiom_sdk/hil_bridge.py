"""Same project-scoped, versioned HIL bridge API used by the UI."""
from .collections import _key
from .perception import _identity
BASE='/hil-bridge'
class HilBridgeClientMixin:
    def save_hil_model_set(self,source,members,*,name='복합 모델 → Virtual ECU → CARLA',idempotency_key):
        if not 2<=len(members)<=4:raise ValueError('Select two to four model members')
        couplings=[]
        for member in members:
            pair=member['pair'];runs=member['runs']
            if pair['project_id']!=source['project_id']:raise ValueError('Model pair and source must belong to the same project')
            couplings.append(dict(pair_ref={k:pair[k] for k in ('id','revision','sha256')},
                baseline_execution=runs['baseline']['id'],faulted_execution=runs['faulted']['id'],
                start_ms=member.get('start_ms',3000),min_hold_ms=member.get('min_hold_ms',500),recovery_samples=member.get('recovery_samples',3)))
        return self.save_hil_binding(dict(project_id=source['project_id'],name=name,source_kind='vecu-network-test',
            source_ref={k:source[k] for k in ('id','revision','sha256')},model_couplings=couplings),idempotency_key=idempotency_key)
    def hil_model_injections(self,*,project_id='default'):
        return self._request('GET',BASE+'/model-injections',params={'project_id':_identity(project_id)}).json()
    def save_hil_model_loop(self,source,pair,runs,*,name='모델 출력 → Virtual ECU → CARLA',start_ms=3000,min_hold_ms=500,recovery_samples=3,idempotency_key):
        if source['project_id']!=pair['project_id']:raise ValueError('Model pair and source must belong to the same project')
        return self.save_hil_binding(dict(project_id=source['project_id'],name=name,source_kind='vecu-network-test',
            source_ref={k:source[k] for k in ('id','revision','sha256')},model_coupling=dict(
                pair_ref={k:pair[k] for k in ('id','revision','sha256')},
                baseline_execution=runs['baseline']['id'],faulted_execution=runs['faulted']['id'],
                start_ms=start_ms,min_hold_ms=min_hold_ms,recovery_samples=recovery_samples)),idempotency_key=idempotency_key)
    def hil_model_injection_catalog(self,*,project_id='default'):
        return self._request('GET',BASE+'/model-injections/catalog',params={'project_id':_identity(project_id)}).json()
    def save_hil_model_injection(self,body,*,idempotency_key):
        return self._request('POST',BASE+'/model-injections',json=body,headers=_key(idempotency_key)).json()
    def hil_model_injection(self,identity,*,project_id='default'):
        return self._request('GET',BASE+'/model-injections/'+_identity(identity),params={'project_id':_identity(project_id)}).json()
    def execute_hil_model_injection(self,pair,*,idempotency_key):
        """Submit the two native tests; inspect each returned job, never infer success from submission."""
        result={};prepared={}
        for side,ref in pair['tests'].items():
            test=self.engineering_lab_test(ref['id'],revision=ref['revision'],project_id=pair['project_id'])
            if test['sha256']!=ref['sha256']:raise ValueError('Model injection test hash changed')
            prepared[side]=self.preview_engineering_lab_test(test)
        for side,preview in prepared.items():
            key=idempotency_key+'-'+side
            experiment=self.create_experiment(**preview['experiment'],idempotency_key=key)
            result[side]=self.submit_experiment(experiment['id'],preview_hash=preview['preview_hash'],idempotency_key=key)
        return result
    def hil_bridge_profiles(self,*,project_id='default'):
        return self._request('GET',BASE+'/profiles',params={'project_id':_identity(project_id)}).json()
    def hil_bridge_readiness(self,*,profile_id='axiom-two-ecu-vcan',project_id='default'):
        return self._request('GET',BASE+'/readiness',params={'project_id':_identity(project_id),'profile_id':_identity(profile_id)}).json()
    def save_hil_binding(self,body,*,idempotency_key):
        return self._request('POST',BASE+'/bindings',json=body,headers=_key(idempotency_key)).json()
    def hil_bindings(self,*,project_id='default'):
        return self._request('GET',BASE+'/bindings',params={'project_id':_identity(project_id)}).json()
    def hil_binding(self,identity,*,revision,project_id='default'):
        return self._request('GET',BASE+'/bindings/'+_identity(identity),params={'project_id':_identity(project_id),'revision':revision}).json()
    def preview_hil_binding(self,binding):
        return self._request('POST',BASE+'/bindings/'+_identity(binding['id'])+'/preview',json={k:binding[k] for k in ('project_id','revision','sha256')}).json()
    def execute_hil_binding(self,binding,*,idempotency_key):
        return self._request('POST',BASE+'/bindings/'+_identity(binding['id'])+'/executions',json={k:binding[k] for k in ('project_id','revision','sha256')},headers=_key(idempotency_key)).json()
    def hil_bridge_executions(self,*,project_id='default'):
        return self._request('GET',BASE+'/executions',params={'project_id':_identity(project_id)}).json()
    def hil_bridge_execution(self,identity,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(identity),params={'project_id':_identity(project_id)}).json()
    def hil_bridge_causality(self,identity,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(identity)+'/causality',params={'project_id':_identity(project_id)}).json()
    def cancel_hil_bridge_execution(self,identity,*,project_id='default'):
        return self._request('POST',BASE+'/executions/'+_identity(identity)+'/cancel',params={'project_id':_identity(project_id)}).json()
    def export_hil_bridge_execution(self,identity,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(identity)+'/export',params={'project_id':_identity(project_id)}).content
