"""Project-scoped native CAN/Ethernet vECU network execution contract."""
from .collections import _key
from .perception import _identity
BASE='/virtual-ecu-network'

class VecuNetworkClientMixin:
    def vecu_network_catalog(self,*,project_id='default'):
        return self._request('GET',BASE+'/catalog',params={'project_id':_identity(project_id)}).json()
    def vecu_network_readiness(self,*,profile_id='actual_silkit_autoas_trampoline',project_id='default'):
        return self._request('GET',BASE+'/readiness',params={'project_id':_identity(project_id),'profile_id':_identity(profile_id)}).json()
    def vecu_network_records(self,collection,*,project_id='default'):
        if collection not in {'networks','tests'}:raise ValueError('Unknown network collection')
        return self._request('GET',BASE+'/records/'+collection,params={'project_id':_identity(project_id)}).json()
    def vecu_network_record(self,collection,identity,*,revision,project_id='default'):
        if collection not in {'networks','tests'}:raise ValueError('Unknown network collection')
        return self._request('GET',BASE+'/records/'+collection+'/'+_identity(identity),params={'project_id':_identity(project_id),'revision':revision}).json()
    def save_vecu_network_record(self,collection,body,*,idempotency_key):
        if collection not in {'networks','tests'}:raise ValueError('Unknown network collection')
        return self._request('POST',BASE+'/'+collection,json=body,headers=_key(idempotency_key)).json()
    def preview_vecu_network_test(self,test):
        return self._request('POST',BASE+'/tests/'+_identity(test['id'])+'/preview',json={k:test[k] for k in ('project_id','revision','sha256')}).json()
    def execute_vecu_network_test(self,test,*,idempotency_key):
        return self._request('POST',BASE+'/tests/'+_identity(test['id'])+'/executions',
            json={k:test[k] for k in ('project_id','revision','sha256')},headers=_key(idempotency_key)).json()
    def vecu_network_executions(self,*,project_id='default'):
        return self._request('GET',BASE+'/executions',params={'project_id':_identity(project_id)}).json()
    def vecu_network_execution(self,execution_id,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(execution_id),params={'project_id':_identity(project_id)}).json()
    def vecu_network_causality(self,execution_id,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(execution_id)+'/causality',params={'project_id':_identity(project_id)}).json()
    def cancel_vecu_network_execution(self,execution_id,*,project_id='default'):
        return self._request('POST',BASE+'/executions/'+_identity(execution_id)+'/cancel',params={'project_id':_identity(project_id)}).json()
    def export_vecu_network_execution(self,execution_id,*,project_id='default'):
        return self._request('GET',BASE+'/executions/'+_identity(execution_id)+'/export',params={'project_id':_identity(project_id)}).content
