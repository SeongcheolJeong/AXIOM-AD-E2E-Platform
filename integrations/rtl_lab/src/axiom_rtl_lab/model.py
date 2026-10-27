"""Independent queue-based oracle and deterministic, shared stimulus. Time units: ns."""
from collections import deque
import simpy

SIGNALS = ('reset','in_valid','in_ready','in_data','out_valid','out_ready','out_data',
           'occupancy','full','empty','accepted_in','accepted_out','occupancy_after')

def input_word(index, seed):
    # A permutation of 32-bit indices: no accidental duplicates in the bounded input.
    return ((index ^ seed) * 2654435761) & 0xffffffff

def reference(config, seed=42, check=lambda: None):
    env = simpy.Environment(); queue = deque(); cycles = []; stimulus = []
    resets = set(config['resets']); stalls = config['stalls']; depth = config['depth']
    def source():
        index = 0
        for cycle in range(config['max_cycles']):
            if cycle % 256 == 0: check()
            reset = int(cycle in resets)
            ready = int(not any(s['start_cycle'] <= cycle < s['end_cycle'] for s in stalls))
            valid = int(index < config['words'] and not reset)
            data = input_word(index, seed) if valid else 0
            occupied = len(queue); ov = int(bool(queue)); od = queue[0] if queue else 0
            ir = int(occupied < depth or (ov and ready))
            push, pop = int(bool(valid and ir and not reset)), int(bool(ov and ready and not reset))
            stimulus.append(dict(cycle=cycle,reset=reset,in_valid=valid,in_data=data,out_ready=ready))
            if reset: queue.clear()
            else:
                if pop: queue.popleft()
                if push: queue.append(data); index += 1
            cycles.append(dict(cycle=cycle,time_ns=int(env.now)+5,reset=reset,in_valid=valid,
              in_ready=ir,in_data=data,out_valid=ov,out_ready=ready,out_data=od,occupancy=occupied,
              full=int(occupied==depth),empty=int(occupied==0),accepted_in=push,accepted_out=pop,
              occupancy_after=len(queue)))
            if index==config['words'] and not queue: break
            yield env.timeout(10)
    env.process(source());env.run()
    return cycles, stimulus

def scoreboard(cycles, config):
    """Check observed handshakes, order, boundary rules and stable stalled signals independently."""
    queue = deque(); errors = []; transfers = []; reset_discarded = 0; input_count = output_count = 0
    peak = in_stall = out_stall = 0; latencies = []; previous = None
    def error(cycle, signal, expected, actual):
        if len(errors)<100: errors.append(dict(cycle=cycle,signal=signal,expected=expected,actual=actual))
    for r in cycles:
        c=r['cycle'];reset=r['reset'];occ=len(queue);peak=max(peak,occ,r['occupancy_after'])
        expected={'occupancy':occ,'empty':int(occ==0),'full':int(occ==config['depth']),
          'out_valid':int(occ>0),'in_ready':int(occ<config['depth'] or (occ>0 and r['out_ready']))}
        for k,v in expected.items():
            if r[k]!=v:error(c,k,v,r[k])
        if previous and not reset and not previous['reset']:
            for prefix in ('in','out'):
                if previous[prefix+'_valid'] and not previous[('in_ready' if prefix=='in' else 'out_ready')]:
                    if not r[prefix+'_valid'] or r[prefix+'_data']!=previous[prefix+'_data']:
                        error(c,prefix+'_stalled_data',previous[prefix+'_data'],r[prefix+'_data'])
        push=int(bool(r['in_valid'] and r['in_ready'] and not reset));pop=int(bool(r['out_valid'] and r['out_ready'] and not reset))
        for k,v in [('accepted_in',push),('accepted_out',pop)]:
            if r[k]!=v:error(c,k,v,r[k])
        if reset:
            for data,start,idx in queue:
                transfers.append(dict(index=idx,data=data,input_cycle=start,output_cycle=None,reset_cycle=c,latency_cycles=None,status='reset_discarded'))
            reset_discarded+=len(queue);queue.clear()
        else:
            if pop:
                if not queue:error(c,'unexpected_output',None,r['out_data'])
                else:
                    data,start,idx=queue.popleft();latencies.append(c-start)
                    if r['out_data']!=data:error(c,'out_data',data,r['out_data'])
                    transfers.append(dict(index=idx,data=data,input_cycle=start,output_cycle=c,reset_cycle=None,latency_cycles=c-start,status='delivered'))
                output_count+=1
            if push:queue.append((r['in_data'],c,input_count));input_count+=1
            if r['in_valid'] and not r['in_ready']:in_stall+=1
            if r['out_valid'] and not r['out_ready']:out_stall+=1
        if r['occupancy_after']!=len(queue):error(c,'occupancy_after',len(queue),r['occupancy_after'])
        previous=r
    for data,start,idx in queue:
        transfers.append(dict(index=idx,data=data,input_cycle=start,output_cycle=None,reset_cycle=None,latency_cycles=None,status='pending'))
    drained=input_count==config['words'] and not queue
    summary=dict(input_transfers=input_count,output_transfers=output_count,reset_discarded=reset_discarded,
      peak_occupancy=peak,input_stall_cycles=in_stall,output_stall_cycles=out_stall,max_latency_cycles=max(latencies,default=0),
      mean_latency_cycles=sum(latencies)/len(latencies) if latencies else 0,cycles=len(cycles),drained=drained,timeout=not drained,pending_words=len(queue),unsubmitted_words=config['words']-input_count)
    return dict(passed=not errors,errors=errors,pending=len(queue),reset_discarded=reset_discarded),summary,transfers

def compare(expected, actual, config):
    first=None;count=0
    for a,b in zip(expected,actual):
        for signal in ('cycle','time_ns')+SIGNALS:
            if a[signal]!=b[signal]:
                count+=1
                if first is None:first=dict(cycle=a['cycle'],signal=signal,expected=a[signal],actual=b[signal])
    if len(expected)!=len(actual):
        count+=abs(len(expected)-len(actual))
        if first is None:first=dict(cycle=min(len(expected),len(actual)),signal='trace_length',expected=len(expected),actual=len(actual))
    e,_,_=scoreboard(expected,config);a,_,_=scoreboard(actual,config)
    return dict(matched=count==0 and e['passed'] and a['passed'],mismatch_count=count,first_mismatch=first,scoreboards={'simpy':e,'rtl':a})
