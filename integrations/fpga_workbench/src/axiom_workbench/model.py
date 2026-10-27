"""Atomic pre-edge FIFO network model, independent of the generated RTL."""
from collections import deque
import simpy
from axiom_rtl_lab.model import input_word,scoreboard as fifo_scoreboard

FIELDS=('in_valid','in_ready','in_data','out_valid','out_ready','out_data','occupancy','full','empty','accepted_in','accepted_out','occupancy_after')

def decorate(row, compiled):
    """Bind physical port observations to saved graph identities (never compute RTL state)."""
    nodes=row['nodes']
    nodes[compiled['source_id']]={k:row[v] for k,v in [('out_valid','in_valid'),('out_ready','in_ready'),('out_data','in_data'),('accepted_out','accepted_in')]}
    nodes[compiled['sink_id']]={k:row[v] for k,v in [('in_valid','out_valid'),('in_ready','out_ready'),('in_data','out_data'),('accepted_in','accepted_out')]}
    nodes[compiled['ready_id']]={'value':row['out_ready']}
    edges={}
    for i,edge_id in enumerate(compiled['stream_edges']):
        n=nodes[compiled['source_id'] if i==0 else compiled['fifo_order'][i-1]]
        edges[edge_id]=dict(valid=n['out_valid'],ready=n['out_ready'],data=n['out_data'],accepted=n['accepted_out'])
    edges[compiled['ready_edge_id']]={'value':row['out_ready']}
    row['edges']=edges
    return row

def reference(compiled, seed=42, check=lambda:None):
    c=compiled['config'];depths=c['depths'];ids=compiled['fifo_order'];queues=[deque() for _ in depths]
    env=simpy.Environment();rows=[];stimulus=[];resets=set(c['resets'])
    def run():
        source_index=0
        for cycle in range(c['max_cycles']):
            if cycle%256==0:check()
            reset=int(cycle in resets);out_ready=int(not any(s['start_cycle']<=cycle<s['end_cycle'] for s in c['stalls']))
            valid=int(source_index<c['words'] and not reset);data=input_word(source_index,seed) if valid else 0
            ov=[int(bool(q)) for q in queues];od=[q[0] if q else 0 for q in queues];occupied=[len(q) for q in queues]
            ready=[0]*len(queues)
            for i in reversed(range(len(queues))):
                downstream=out_ready if i==len(queues)-1 else ready[i+1]
                ready[i]=int(len(queues[i])<depths[i] or (ov[i] and downstream))
            row=dict(cycle=cycle,time_ns=int(env.now)+5,reset=reset,in_valid=valid,in_ready=ready[0],in_data=data,
              out_valid=ov[-1],out_ready=out_ready,out_data=od[-1],accepted_in=int(bool(valid and ready[0] and not reset)),
              accepted_out=int(bool(ov[-1] and out_ready and not reset)),nodes={})
            for i,q in enumerate(queues):
                iv=valid if i==0 else ov[i-1];ind=data if i==0 else od[i-1];orr=out_ready if i==len(queues)-1 else ready[i+1]
                push=int(bool(iv and ready[i] and not reset));pop=int(bool(ov[i] and orr and not reset))
                if reset:q.clear()
                else:
                    if pop:q.popleft()
                    if push:q.append(ind)
                row['nodes'][ids[i]]=dict(in_valid=iv,in_ready=ready[i],in_data=ind,out_valid=ov[i],out_ready=orr,out_data=od[i],
                  occupancy=occupied[i],full=int(occupied[i]==depths[i]),empty=int(occupied[i]==0),accepted_in=push,accepted_out=pop,occupancy_after=len(q))
            source_index+=row['accepted_in']
            stimulus.append(dict(cycle=cycle,reset=reset,in_valid=valid,in_data=data,out_ready=out_ready))
            rows.append(decorate(row,compiled))
            if source_index==c['words'] and not any(queues):break
            yield env.timeout(10)
    env.process(run());env.run()
    return rows,stimulus

def scoreboard(rows, compiled):
    """Per-FIFO queue oracles plus an independent end-to-end source/sink ledger."""
    config=compiled['config'];errors=[];per_node={};node_reset=0
    for i,node_id in enumerate(compiled['fifo_order']):
        local=[dict(cycle=r['cycle'],time_ns=r['time_ns'],reset=r['reset'],**r['nodes'][node_id]) for r in rows]
        b,s,_=fifo_scoreboard(local,dict(depth=config['depths'][i],words=sum(r['accepted_in'] for r in local)))
        per_node[node_id]=s;node_reset+=s['reset_discarded'];errors.extend(dict(node_id=node_id,**e) for e in b['errors'])
    queue=deque();transfers=[];input_count=output_count=discarded=peak=in_stall=out_stall=0;latencies=[]
    def error(cycle,signal,expected,actual):
        if len(errors)<100:errors.append(dict(cycle=cycle,node_id=None,signal=signal,expected=expected,actual=actual))
    for row in rows:
        cycle=row['cycle'];nodes=row['nodes'];occupancy=sum(nodes[n]['occupancy'] for n in compiled['fifo_order'])
        if occupancy!=len(queue):error(cycle,'total_occupancy',len(queue),occupancy)
        chain=[nodes[compiled['source_id']],*[nodes[n] for n in compiled['fifo_order']],nodes[compiled['sink_id']]]
        for a,b in zip(chain,chain[1:]):
            for left,right in [('out_valid','in_valid'),('out_ready','in_ready'),('out_data','in_data'),('accepted_out','accepted_in')]:
                if a[left]!=b[right]:error(cycle,'connection_'+left,a[left],b[right])
        if row['reset']:
            for data,start,index in queue:transfers.append(dict(index=index,data=data,input_cycle=start,output_cycle=None,reset_cycle=cycle,latency_cycles=None,status='reset_discarded'))
            discarded+=len(queue);queue.clear()
        else:
            if row['out_valid'] and row['out_ready']:
                if not queue:error(cycle,'unexpected_output',None,row['out_data'])
                else:
                    data,start,index=queue.popleft();latencies.append(cycle-start)
                    if row['out_data']!=data:error(cycle,'out_data',data,row['out_data'])
                    transfers.append(dict(index=index,data=data,input_cycle=start,output_cycle=cycle,reset_cycle=None,latency_cycles=cycle-start,status='delivered'))
                output_count+=1
            if row['in_valid'] and row['in_ready']:queue.append((row['in_data'],cycle,input_count));input_count+=1
            in_stall+=int(bool(row['in_valid'] and not row['in_ready']));out_stall+=int(bool(row['out_valid'] and not row['out_ready']))
        after=sum(nodes[n]['occupancy_after'] for n in compiled['fifo_order']);peak=max(peak,occupancy,after)
        if after!=len(queue):error(cycle,'total_occupancy_after',len(queue),after)
    if discarded!=node_reset:error(rows[-1]['cycle'] if rows else 0,'reset_accounting',discarded,node_reset)
    for data,start,index in queue:transfers.append(dict(index=index,data=data,input_cycle=start,output_cycle=None,reset_cycle=None,latency_cycles=None,status='pending'))
    drained=input_count==config['words'] and not queue
    summary=dict(input_transfers=input_count,output_transfers=output_count,reset_discarded=discarded,peak_occupancy=peak,
      input_stall_cycles=in_stall,output_stall_cycles=out_stall,max_latency_cycles=max(latencies,default=0),mean_latency_cycles=sum(latencies)/len(latencies) if latencies else 0,
      cycles=len(rows),drained=drained,timeout=not drained,pending_words=len(queue),unsubmitted_words=config['words']-input_count,per_node=per_node)
    return dict(passed=not errors,errors=errors[:100],pending=len(queue),reset_discarded=discarded),summary,transfers

def compare(expected,actual,compiled):
    first=None;count=0
    for a,b in zip(expected,actual):
        # Prefer the observed FIFO port: the same fault may also appear on a
        # global source/sink alias, which otherwise hides its graph location.
        for node_id in compiled['fifo_order']:
            for signal in FIELDS:
                if a['nodes'][node_id][signal]!=b['nodes'][node_id][signal]:
                    count+=1
                    if first is None:first=dict(cycle=a['cycle'],node_id=node_id,edge_id=None,signal=signal,expected=a['nodes'][node_id][signal],actual=b['nodes'][node_id][signal])
        for signal in ['cycle','time_ns','reset','in_valid','in_ready','in_data','out_valid','out_ready','out_data','accepted_in','accepted_out']:
            if a[signal]!=b[signal]:
                count+=1
                if first is None:
                    location = ('source_id',0) if signal.startswith('in_') or signal=='accepted_in' else (('sink_id',-1) if signal.startswith('out_') or signal=='accepted_out' else None)
                    first=dict(cycle=a['cycle'],node_id=compiled[location[0]] if location else None,edge_id=compiled['stream_edges'][location[1]] if location else None,signal=signal,expected=a[signal],actual=b[signal])
    if len(expected)!=len(actual):
        count+=abs(len(expected)-len(actual))
        if first is None:first=dict(cycle=min(len(expected),len(actual)),node_id=None,edge_id=None,signal='trace_length',expected=len(expected),actual=len(actual))
    e,_,_=scoreboard(expected,compiled);a,_,_=scoreboard(actual,compiled)
    return dict(matched=count==0 and e['passed'] and a['passed'],mismatch_count=count,first_mismatch=first,scoreboards={'simpy':e,'rtl':a})
