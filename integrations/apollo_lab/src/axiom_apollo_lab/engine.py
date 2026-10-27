"""Shared-clock, bounded frame-level Apollo resources coupled to existing FPGA/CSI ingress."""
from collections import deque
import simpy
from ade2e.apollo_lab_schemas import validate_snapshot
from ade2e.ecu_lab_schemas import EcuProfileConfig, DEFAULT_COMMANDS
from axiom_fpga_lab.ecu import simulate as ingress_simulate

class Pipeline:
    def __init__(self,env,profile,channels,captures,check):
        self.env=env;self.p=profile;self.channels={c['channel']:c for c in channels};self.check=check
        self.resources={k:simpy.Resource(env,capacity=1) for k in ['TFE0','TFE1','TFE2','OFE','IPE','DDR']}
        self.rings={k:deque() for k in self.channels};self.events=[];self.jobs=[];self.used=0;self.peak=0
        self.metrics={k:dict(service_ns=0,wait_ns=0,bytes=0,peak_queue=0) for k in self.resources}
        self.tokens=[]
        for index,c in enumerate(sorted(captures,key=lambda c:(c['at_us'],c['channel']))):env.process(self.capture(index,c))
    def record(self,kind,job=None,node=None,**details):
        self.check()
        if len(self.events)>=1000000 or self.env.now>60000000000:raise ValueError('Apollo event/time budget exceeded')
        event=dict(id=len(self.events),time_ns=int(self.env.now),kind=kind,node=node,job_id=job['id'] if job else None,
          channel=job['channel'] if job else None,frame=job['frame'] if job else None,memory_bytes=self.used,
          queue_depths={k:len(v.queue) for k,v in self.resources.items()},cause_event_id=job.get('last_event') if job else None,**details)
        self.events.append(event)
        if job is not None:job['last_event']=event['id']
        return event['id']
    def allocate(self,size,job):
        if self.used+size>self.p.memory_bytes:
            self.drop(job,'memory_capacity');return False
        self.used+=size;self.peak=max(self.peak,self.used);self.record('buffer_allocate',job,bytes=size);return True
    def release(self,token,job):
        token['refs']-=1
        if token['refs']==0:
            self.used-=token['bytes'];self.record('buffer_release',job,bytes=token['bytes'])
    def drop(self,job,reason):
        job['status']='dropped';job['loss_reason']=reason;self.record(reason,job)
    def job(self,identity,ch,frame,kind,source,request):
        j=dict(id=identity,channel=ch,frame=frame,kind=kind,source_ns=source,request_ns=request,status='queued',loss_reason=None,completed_ns=None,stages=[],last_event=None)
        self.jobs.append(j);return j
    def transfer(self,job,size,operation):
        begin=int(self.env.now);wait=service=0;remaining=size;r=self.resources['DDR'];metric=self.metrics['DDR']
        self.record('memory_request',job,'DDR',bytes=size,operation=operation)
        while remaining:
            batch=min(65536,remaining);queued=int(self.env.now)
            with r.request() as req:
                metric['peak_queue']=max(metric['peak_queue'],len(r.queue));yield req
                wait+=int(self.env.now)-queued;duration=(batch*1000+self.p.memory_MBps-1)//self.p.memory_MBps
                yield self.env.timeout(duration);service+=duration;remaining-=batch
        metric['wait_ns']+=wait;metric['service_ns']+=service;metric['bytes']+=size
        job['stages'].append(dict(node='DDR',operation=operation,queued_ns=begin,start_ns=begin+wait,end_ns=int(self.env.now),wait_ns=wait,service_ns=service,bytes=size,interleaved=True))
        self.record('memory_complete',job,'DDR',bytes=size,wait_ns=wait,service_ns=service,operation=operation)
    def compute(self,job,node,us):
        r=self.resources[node];metric=self.metrics[node];queued=int(self.env.now)
        if len(r.queue)>=self.p.queue_frames:
            self.drop(job,node+'_queue_full');return False
        self.record('node_queued',job,node)
        with r.request() as req:
            metric['peak_queue']=max(metric['peak_queue'],len(r.queue));yield req;start=int(self.env.now)
            self.record('node_start',job,node);yield self.env.timeout(us*1000)
            metric['service_ns']+=us*1000;metric['wait_ns']+=start-queued
            job['stages'].append(dict(node=node,queued_ns=queued,start_ns=start,end_ns=int(self.env.now),wait_ns=start-queued,service_ns=us*1000))
            self.record('node_complete',job,node)
        return True
    def receive(self,f):
        ch=f['channel'];j=self.job(f'p-{ch}-{f["frame"]}',ch,f['frame'],'preview',f['source_ns'],f['source_ns'])
        j['received_ns']=f['received_ns'];j['ingress']=dict(f)
        j['stages'].append(dict(node='CSI',queued_ns=f['tx_start_ns'],start_ns=f['csi_start_ns'],end_ns=f['received_ns'],wait_ns=max(0,f['csi_start_ns']-f['tx_start_ns']),service_ns=f['received_ns']-f['csi_start_ns']))
        self.record('csi_received',j,'CSI');self.env.process(self.preview(j))
    def preview(self,j):
        c=self.channels[j['channel']]['design']['config'];size=c['width']*c['height']*2
        if not self.allocate(size,j):return
        token=dict(bytes=size,refs=1,frame=j['frame'],source_ns=j['source_ns'],job_id=j['id']);self.tokens.append(token)
        try:
            yield from self.transfer(j,size,'CSI write Bayer16')
            yield from self.transfer(j,size,'TFE read Bayer16')
            if not (yield from self.compute(j,'TFE'+str(self.channels[j['channel']]['tfe']),self.p.tfe_us)):return
            yield from self.transfer(j,size,'TFE write Bayer16')
            token['ready_ns']=int(self.env.now)
            ring=self.rings[j['channel']]
            if len(ring)>=self.p.zsl_frames:self.release(ring.popleft(),j)
            token['refs']+=1;ring.append(token);self.record('zsl_retain',j,'ZSL',ring_frames=len(ring))
            yield from self.downstream(j,size)
        finally:self.release(token,j)
    def downstream(self,j,size):
        yuv=size//2*3
        if not self.allocate(yuv,j):return
        try:
            yield from self.transfer(j,size,'OFE read Bayer16')
            if not (yield from self.compute(j,'OFE',self.p.ofe_us)):return
            yield from self.transfer(j,yuv,'OFE write YUV444')
            yield from self.transfer(j,yuv,'IPE read YUV444')
            if not (yield from self.compute(j,'IPE',self.p.ipe_us)):return
            yield from self.transfer(j,yuv,'IPE write YUV444')
            j['completed_ns']=int(self.env.now);j['latency_ns']=j['completed_ns']-j['request_ns']
            j['deadline_exceeded']=j['latency_ns']>self.p.deadline_us*1000
            j['status']='late' if j['deadline_exceeded'] else 'completed'
            self.record('output_complete',j,'Output')
        finally:
            self.used-=yuv;self.record('buffer_release',j,bytes=yuv)
    def capture(self,index,c):
        yield self.env.timeout(c['at_us']*1000)
        # Complete same-time buffer arrivals before resolving the capture.
        yield self.env.timeout(0)
        ring=self.rings[c['channel']];eligible=[t for t in ring if t['source_ns']<=self.env.now]
        token=max(eligible,key=lambda t:(t['source_ns'],t['frame'])) if eligible else None
        j=self.job(f's-{index}',c['channel'],token['frame'] if token else None,'snapshot',token['source_ns'] if token else None,int(self.env.now))
        if token is None:self.drop(j,'zsl_empty');return
        token['refs']+=1;j.update(parent_job=token['job_id'],raw_ready_ns=token['ready_ns'],frame_age_ns=int(self.env.now)-token['source_ns'])
        self.record('capture_pin',j,'ZSL',parent_job=token['job_id'])
        try:yield from self.downstream(j,token['bytes'])
        finally:self.release(token,j)
    def finish(self,ingress):
        known={(j['channel'],j['frame']) for j in self.jobs if j['kind']=='preview'}
        for f in ingress['frames']:
            if (f['channel'],f['frame']) not in known:
                j=self.job(f'p-{f["channel"]}-{f["frame"]}',f['channel'],f['frame'],'preview',f['source_ns'],f['source_ns'])
                j.update(status='dropped',loss_reason=f['loss_reason'] or 'ingress_missing',ingress=dict(f))
        for ring in self.rings.values():
            while ring:self.release(ring.popleft(),None)
        if self.used or any(t['refs'] for t in self.tokens):raise ValueError('Leaked reference or memory bytes')
        summary={}
        for kind in ('preview','snapshot'):
            jobs=[j for j in self.jobs if j['kind']==kind];completed=[j for j in jobs if j['completed_ns'] is not None]
            summary[kind]=dict(total=len(jobs),completed=len(completed),dropped=sum(j['status']=='dropped' for j in jobs),late=sum(j.get('deadline_exceeded',False) for j in jobs),on_time=sum(not j['deadline_exceeded'] for j in completed),max_latency_ns=max((j['latency_ns'] for j in completed),default=None))
        return dict(schema_version='apollo-trace/1',jobs=self.jobs,events=self.events,summary=summary|dict(resources=self.metrics,peak_memory_bytes=self.peak,final_memory_bytes=self.used,virtual_end_ns=int(self.env.now)),ingress=ingress)

def simulate(profile,channels,captures,seed=42,should_cancel=lambda:False):
    p=validate_snapshot(profile,channels,captures);pipeline=None
    def checked():
        if should_cancel():
            from ade2e.adapters import AdapterCancelledBeforeDispatch
            raise AdapterCancelledBeforeDispatch('Apollo reference execution cancelled')
    def factory(env):
        nonlocal pipeline
        pipeline=Pipeline(env,p,channels,captures,checked);return pipeline.receive
    c=channels[0]['design']['config']
    ingress_profile=EcuProfileConfig(width=c['width'],height=c['height'],bits=c['bits'],fps=c['fps'],csi_lane_Mbps=p.csi_lane_Mbps,processing_us=1).model_dump()
    ingress_channels=[ch|{'duplicate_every':0} for ch in channels]
    ingress=ingress_simulate(ingress_profile,ingress_channels,DEFAULT_COMMANDS,[],seed,should_cancel,receiver_factory=factory)
    return pipeline.finish(ingress)
