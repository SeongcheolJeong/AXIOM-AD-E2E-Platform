"""Common process contract and explicitly selected reference-policy checks.

These checks validate software contracts, not vehicle safety. The Python oracle
is independent from the compiled implementation and never supplies live control.
"""
import copy
import math
import re
from pathlib import Path
try:
    from . import hil_dut
except ImportError:
    import hil_dut

POLICIES={'immediate':'reference-traffic-cpp/1','ramp':'ramp-traffic-cpp/1'}

def validate_runtime(value):
    if isinstance(value,dict) and value.get('kind')=='network_reference':
        try:from . import hil_network
        except ImportError:import hil_network
        return hil_network.validate_runtime(value)
    if (not isinstance(value,dict) or value.get('kind')!='compiled_reference'
        or value.get('policy_id') not in POLICIES
        or value.get('dut_version')!=POLICIES[value['policy_id']]
        or value.get('protocol')!='hil-dut/1'
        or any(not re.fullmatch('[0-9a-f]{64}',value.get(k,'')) for k in ('binary_sha256','source_sha256','runtime_sha256'))
        or not re.fullmatch('[0-9a-f]{32}',value.get('build_key',''))):
        raise ValueError('Unsupported compiled ECU runtime identity')
    return value

def executable(value,root):
    import hashlib
    validate_runtime(value)
    filename='reference-traffic-cpp' if value['policy_id']=='immediate' else 'ramp-traffic-cpp'
    path=Path(root)/'vecu-builds'/value['build_key']/filename
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=value['binary_sha256']:
        raise ValueError('Compiled ECU binary missing or changed')
    return path

def common_check(request,reply):
    if (not isinstance(reply,dict) or reply.get('schema_version')!='hil-dut-reply/1'
        or reply.get('op')!='step' or type(reply.get('sequence')) is not int or reply.get('sequence')!=request['sequence']):
        raise ValueError('ECU reply sequence/protocol mismatch')
    control=reply.get('control',{})
    if (set(control)!={'throttle','brake','steer'}
        or any(type(x) not in (int,float) or not math.isfinite(x) for x in control.values())
        or not 0<=control['throttle']<=1 or not 0<=control['brake']<=1 or not -1<=control['steer']<=1):
        raise ValueError('ECU control output outside finite bounds')
    if reply.get('state') not in ('tracking','recovering','degraded'):
        raise ValueError('Unsupported ECU diagnostic state')

def close_enough(actual,expected):
    if isinstance(expected,dict):
        return isinstance(actual,dict) and actual.keys()==expected.keys() and all(close_enough(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(close_enough(a,b) for a,b in zip(actual,expected))
    if type(expected) is int:return type(actual) is int and actual==expected
    if type(expected) is float and type(actual) in (int,float):
        return math.isfinite(actual) and math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-9)
    return type(actual) is type(expected) and actual==expected

class PolicyOracle:
    def __init__(self,init,policy='immediate'):
        if policy not in POLICIES:raise ValueError('Policy oracle is not registered')
        self.reference=hil_dut.ReferenceDut(init);self.policy=policy
        self.previous={'throttle':0.,'brake':0.,'steer':0.}
        self.braking_start=None;self.braking_from=0.;self.recovery_start=None;self.recovery_from=None
        self.was_fault=False;self.in_fault_episode=False
    def expected(self,request):
        result=self.reference.step(request)
        if self.policy=='immediate':return result
        now=request['time_s'];stale=result['state']=='degraded';gate=result['state']=='recovering'
        control=copy.deepcopy(result['control']);phase='tracking';elapsed=0.
        if stale:
            if not self.was_fault:
                self.braking_start=now;self.braking_from=self.previous['brake']
            self.in_fault_episode=True;self.recovery_start=None
            elapsed=now-self.braking_start
            control['throttle']=0.;control['brake']=self.braking_from+(1-self.braking_from)*min(1.,elapsed/.3)
            phase='braking_ramp' if elapsed<.3-1e-9 else 'degraded_hold'
        elif gate:
            self.recovery_start=None
            elapsed=now-self.braking_start if self.braking_start is not None else 0.
            control['throttle']=0.;control['brake']=self.braking_from+(1-self.braking_from)*min(1.,elapsed/.3)
            phase='recovering_gate'
        elif self.in_fault_episode:
            if self.recovery_start is None:
                self.recovery_start=now;self.recovery_from=copy.deepcopy(self.previous)
            elapsed=now-self.recovery_start;alpha=min(1.,elapsed/.5)
            for key in ('throttle','brake'):
                control[key]=self.recovery_from[key]+(control[key]-self.recovery_from[key])*alpha
            if alpha<1.-1e-9:
                result['state']='recovering';phase='recovery_ramp'
            else:self.in_fault_episode=False
        if control['brake']>1e-9:control['throttle']=0.
        result['control']=control;result['plan']['hil_state']=result['state']
        result['policy_phase']=phase;result['phase_elapsed_s']=elapsed
        self.previous=copy.deepcopy(control);self.was_fault=stale
        return result
    def check(self,request,reply):
        common_check(request,reply)
        expected=self.expected(request)
        if not close_enough(reply,expected):
            fields=[k for k,v in expected.items() if not close_enough(reply.get(k),v)]
            raise ValueError('Selected ECU policy oracle mismatch: '+','.join(fields))
