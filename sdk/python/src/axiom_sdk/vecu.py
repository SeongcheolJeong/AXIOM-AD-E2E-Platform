"""Immutable vECU authoring and existing Experiment execution lifecycle."""
from .collections import _key
from .perception import _identity
BASE='/virtual-ecu'
class VecuClientMixin:
    def vecu_catalog(self,*,project_id='default'):
        return self._request('GET',BASE+'/catalog',params={'project_id':_identity(project_id)}).json()
    def build_vecu_package(self,*,name,source_id,project_id='default',idempotency_key):
        if source_id not in {'cpp-immediate','cpp-ramp'}:raise ValueError('Unknown operator-registered vECU source')
        return self._request('POST',BASE+'/builds',json={'project_id':_identity(project_id),'name':name,'source_id':source_id},headers=_key(idempotency_key)).json()
    def vecu_builds(self,*,project_id='default'):
        return self._request('GET',BASE+'/builds',params={'project_id':_identity(project_id)}).json()
    def vecu_build(self,execution_id,*,project_id='default'):
        return self._request('GET',BASE+'/builds/'+_identity(execution_id),params={'project_id':_identity(project_id)}).json()
    def vecu_records(self,collection,*,project_id='default'):
        if collection not in {'packages','inputs','tests'}:raise ValueError('Unknown vECU collection')
        return self._request('GET',BASE+'/records/'+collection,params={'project_id':_identity(project_id)}).json()
    def vecu_record(self,collection,identity,*,revision,project_id='default'):
        if collection not in {'packages','inputs','tests'}:raise ValueError('Unknown vECU collection')
        return self._request('GET',BASE+'/records/'+collection+'/'+_identity(identity),params={'project_id':_identity(project_id),'revision':revision}).json()
    def save_vecu_record(self,collection,body,*,idempotency_key):
        if collection not in {'packages','inputs','tests'}:raise ValueError('Unknown vECU collection')
        return self._request('POST',BASE+'/'+collection,json=body,headers=_key(idempotency_key)).json()
    def preview_vecu_test(self,test):
        return self._request('POST',BASE+'/tests/'+_identity(test['id'])+'/preview',json={k:test[k] for k in ('project_id','revision','sha256')}).json()
    def execute_vecu_test(self,test,*,idempotency_key):
        preview=self.preview_vecu_test(test)
        experiment=self.create_experiment(**preview['experiment'],idempotency_key=idempotency_key)
        return self.submit_experiment(experiment['id'],preview_hash=preview['preview_hash'],idempotency_key=idempotency_key)
    def vecu_executions(self,*,project_id='default'):
        return self._request('GET',BASE+'/executions',params={'project_id':_identity(project_id)}).json()
