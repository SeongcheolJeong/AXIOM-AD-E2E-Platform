"""Native functional network coordinator. Live controls come only from C++ + CAN.

The deterministic 1ms supervisor transports bytes and advances real Trampoline,
autoas and SIL Kit processes. It does not emulate their APIs or vehicle policy.
"""
import hashlib,json,os,select,socket,struct,subprocess,sys,time
from pathlib import Path
try:from . import wire
except ImportError:import network_wire as wire

ROLES=('perception','adas','brake','restbus')
SAFE={'throttle':0.,'brake':1.,'steer':0.}
class Child:
    def __init__(self,args):
        self.p=subprocess.Popen([str(v) for v in args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,bufsize=0)
        self.buffer=b''
    def send(self,value):
        raw=json.dumps(value,allow_nan=False,separators=(',',':')).encode()+b'\n';self.p.stdin.write(raw)
    def receive(self):
        end=time.monotonic()+20
        while b'\n' not in self.buffer:
            if time.monotonic()>end:raise TimeoutError('Native participant did not acknowledge virtual step')
            if not select.select([self.p.stdout],[],[],.2)[0]:continue
            data=os.read(self.p.stdout.fileno(),1048576)
            if not data:raise RuntimeError('Native participant exited: '+str(self.p.args))
            self.buffer+=data
            if len(self.buffer)>1048576:raise ValueError('Native reply too large')
        line,self.buffer=self.buffer.split(b'\n',1);reply=json.loads(line)
        if reply.get('ok') is False or reply.get('status')=='error':raise RuntimeError('Native rejection: '+str(reply))
        return reply
    def call(self,value):self.send(value);return self.receive()
    def stop(self):
        if self.p.poll() is None:self.p.terminate()
        try:self.p.wait(timeout=2)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        self.p.stdin.close();self.p.stdout.close()

class Network:
    def __init__(self,init,root):
        self.root=Path(root);self.init=dict(init);self.runtime=self.init.pop('network_runtime');self.config=self.runtime['network'];self.faults=self.runtime['faults']
        manifest=json.loads((self.root/'network-install.json').read_bytes());self.paths={k:self.root/v for k,v in manifest['binaries'].items()}
        self.children=[];self.events=[];self.controller_steps=[];self.epoch=0;self.current=-1;self.last_external=-1
        self.pending={r:[] for r in ROLES};self.delayed=[];self.last_wire=None;self.fired=set();self.inhibit_until=0
        self.packet={'sequence':-1,'time_s':-.05,'objects':[]};self.delivery=False;self.controller_seen=False;self.controller_reply=None
        self.command=None;self.command_data=None;self.command_sequence=None;self.command_epoch=0;self.last_command=None;self.brake_bad=True;self.fresh_commands=0
        self.heartbeat=None;self.heartbeat_bad=True;self.heartbeat_fresh=0
        self.stats={k:0 for k in ('udp_received','udp_duplicates','udp_missing_intervals','can_commands','watchdog_events','heartbeat_timeouts','resets','os_control_tasks')}
        try:
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            uri=f'silkit://127.0.0.1:{port}'
            self.registry=self.spawn([self.paths['registry'],'--listen-uri',uri,'--log','off'])
            for _ in range(150):
                try:
                    with socket.create_connection(('127.0.0.1',port),.1):break
                except OSError:time.sleep(.02)
            else:raise TimeoutError('SIL Kit registry unavailable')
            self.nodes={r:self.spawn([self.paths['participant'],r,uri]) for r in ROLES}
            for role,node in self.nodes.items():
                reply=node.receive()
                if reply.get('role')!=role or reply.get('engine')!='silkit/5.0.8':raise ValueError('Transport identity mismatch')
            self.coordinator=self.spawn([self.paths['system_controller'],'--connect-uri',uri,'--log','off',*ROLES])
            self.bsw={r:self.spawn([self.paths['bsw']]) for r in ('adas','brake','restbus')}
            for p in self.bsw.values():p.call({'op':'init'})
            self.os=self.spawn([self.paths['os']]);self.os.call({'op':'init','protocol':'vecu-network-os/1'})
            self.new_controller()
        except BaseException:self.close(False);raise
    def spawn(self,args):
        child=Child(args);self.children.append(child);return child
    def new_controller(self):
        self.controller=self.spawn([self.paths['controller']]);reply=self.controller.call(self.init)
        if reply.get('dut_version')!='network-traffic-cpp/1' or reply.get('dut_profile')!=self.init['dut_profile']:raise ValueError('C++ controller identity/profile mismatch')
        self.controller_seen=False
    def event(self,layer,ecu,kind,**fields):
        self.events.append(dict(time_ms=self.current,tick_ms=self.current,layer=layer,ecu=ecu,participant=ecu,kind=kind,epoch=self.epoch,**fields))
    def active(self,kind,t):return any(f['kind']==kind and f['start_ms']<=t<f['end_ms'] for f in self.faults)
    def bsw_call(self,role,request):
        reply=self.bsw[role].call(request)
        # Every actual routing hook is retained; periodic main functions condensed into one event.
        self.event('bsw',role,request['op'],trace=reply['trace'],can_id=request.get('can_id'),data=request.get('data'),bsw_time_ms=reply['time_ms'],bsw_epoch=reply['epoch'])
        return reply
    def tx(self,role,can_id,data,outgoing,drop=False):
        result=self.bsw_call(role,{'op':'tx','can_id':can_id,'data':data})
        if len(result['frames'])!=1:raise ValueError('BSW did not produce exactly one driver frame')
        frame=result['frames'][0]
        if frame['can_id']!=can_id or frame['data']!=data:raise ValueError('BSW transmitted bytes differ')
        self.event('can',role,'can_tx',can_id=can_id,data=data,data_hex=bytes(data).hex(),dropped=drop)
        if drop:self.event('fault',role,'can_drop',can_id=can_id)
        else:outgoing[role].append({'bus':'can','can_id':can_id,'data':data})
    def receive(self):
        for role,frames in self.pending.items():
            for frame in sorted(frames,key=lambda f:(f['time_ns'],f['bus'],f.get('can_id',-1),bytes(f['data']))):
                if frame['bus']=='ethernet' and role=='adas':
                    packet=wire.decode_ethernet(frame['data'],self.config['ethernet']);self.stats['udp_received']+=1
                    duplicate=packet['sequence']<=self.packet['sequence']
                    self.event('ethernet',role,'udp_duplicate' if duplicate else 'udp_rx',sequence=packet['sequence'],source_time_ns=round(packet['time_s']*1e9),frame_sha256=hashlib.sha256(bytes(frame['data'])).hexdigest())
                    if duplicate:
                        self.stats['udp_duplicates']+=1
                        if packet['sequence']==self.packet['sequence']:self.delivery=True
                    else:self.packet=packet;self.delivery=True
                elif frame['bus']=='can':
                    cid=frame['can_id']
                    if (role=='brake' and cid==256) or (role=='adas' and cid in (257,512,513)):
                        response=self.bsw_call(role,{'op':'rx','can_id':cid,'data':frame['data']})
                        signals=response['signals']
                        if len(signals)!=1 or signals[0]['data']!=frame['data']:raise ValueError('Com receive signal differs from bus bytes')
                        data=signals[0]['data']
                        if role=='brake':
                            control,seq,epoch=wire.decode_command(data)
                            if epoch!=self.epoch%256:
                                self.event('can',role,'can_old_epoch_rejected',can_id=cid,data=data);continue
                            if self.command_sequence is not None and epoch==self.command_epoch and (seq-self.command_sequence)%256 not in range(1,128):
                                self.event('can',role,'can_duplicate_rejected',can_id=cid,data=data);continue
                            self.command=control;self.command_data=data;self.command_sequence=seq;self.command_epoch=epoch;self.last_command=self.current
                            if self.brake_bad:
                                self.fresh_commands+=1
                                if self.fresh_commands>=self.config['can']['recovery_packets']:self.brake_bad=False
                        elif cid==513:
                            self.heartbeat=self.current
                            if self.heartbeat_bad:
                                self.heartbeat_fresh+=1
                                if self.heartbeat_fresh>=3:self.heartbeat_bad=False
                        self.event('can',role,'can_rx',can_id=cid,data=data,data_hex=bytes(data).hex(),transport_time_ns=frame['time_ns'])
        self.pending={r:[] for r in ROLES}
    def brake(self):
        stale=self.last_command is None or self.current-self.last_command>=self.config['can']['watchdog_ms']
        reason='initial' if self.last_command is None else 'watchdog' if stale else 'recovery' if self.brake_bad else 'command'
        control=dict(self.command or SAFE)
        if reason!='command':control.update(throttle=0.,brake=1.)
        return dict(control=control,reason=reason,command_hex=bytes(self.command_data).hex() if self.command_data else None,
            command_sequence=self.command_sequence,last_command_ms=self.last_command,epoch=self.command_epoch)
    def tick(self,t,request=None):
        self.current=t;outgoing={r:[] for r in ROLES}
        for index,f in enumerate(self.faults):
            if t==f['start_ms']:self.event('fault',f.get('target_ecu','adas'),'fault_start',fault=f)
            if t==f['end_ms']:
                self.event('fault',f.get('target_ecu','adas'),'fault_end',fault=f)
                if f['kind']=='udp_delay':self.delayed=[]
            if f['kind']=='ecu_reset' and t==f['start_ms'] and index not in self.fired:
                self.fired.add(index);self.epoch+=1;self.stats['resets']+=1
                self.os.call({'op':'reset'});self.bsw_call('adas',{'op':'reset'});self.controller.call({'op':'close'});self.controller.stop();self.new_controller();self.controller_reply=None
                self.packet={'sequence':-1,'time_s':-.05,'objects':[]};self.delivery=False;self.pending['adas']=[];self.pending['brake']=[];self.delayed=[]
                self.inhibit_until=t+f.get('reset_restart_ms',200);self.event('ecu','adas','reset',inhibit_until_ms=self.inhibit_until)
        # Deadlines expire before arrivals at the same virtual tick.
        if self.last_command is not None and t-self.last_command>=self.config['can']['watchdog_ms'] and not self.brake_bad:
            self.brake_bad=True;self.fresh_commands=0;self.stats['watchdog_events']+=1;self.event('ecu','brake','watchdog')
        if self.heartbeat is not None and t-self.heartbeat>=100 and not self.heartbeat_bad:
            self.heartbeat_bad=True;self.heartbeat_fresh=0;self.stats['heartbeat_timeouts']+=1;self.event('ecu','adas','heartbeat_timeout')
        self.receive()
        scheduled={'bsw_due':False,'control_due':False,'events':[]}
        if t:
            scheduled=self.os.call({'op':'tick','tick_ms':t,'suppress_control_event':t<self.inhibit_until or self.active('os_event_drop',t)})
            for ev in scheduled['events']:self.event('os','adas',ev['name'],os_epoch=ev['epoch'])
        if scheduled['bsw_due']:
            for role in self.bsw:self.bsw_call(role,{'op':'tick','elapsed_ms':10})
            speed=max(0,min(65535,round(self.ego['speed_mps']*100)))
            self.tx('restbus',512,list(struct.pack('<H6x',speed)),outgoing,self.active('restbus_drop',t))
            self.tx('restbus',513,list(struct.pack('<IB3x',t//10,1)),outgoing,self.active('restbus_drop',t))
        if scheduled['control_due']:
            self.stats['os_control_tasks']+=1
            r={'op':'step','schema_version':'hil-dut-step/1','sequence':t//50,'time_s':t/1000,'ego':self.ego,
               'object_packet':self.packet,'object_delivery_present':self.delivery or (not self.controller_seen and self.packet['sequence']>=0)}
            reply=self.controller.call(r);self.controller_steps.append({'request':r,'reply':reply,'epoch':self.epoch})
            self.controller_seen=True;self.controller_reply=reply;self.delivery=False
            control=reply['control'] if not self.heartbeat_bad else dict(reply['control'],throttle=0.,brake=1.)
            data=wire.command_bytes(control,t//50,self.epoch);self.stats['can_commands']+=1
            self.event('ecu','adas','control',object_sequence=self.packet['sequence'],state=reply['state'],heartbeat_override=self.heartbeat_bad)
            self.tx('adas',256,data,outgoing,self.active('can_command_drop',t))
            self.tx('brake',257,wire.command_bytes(self.brake()['control'],t//50,self.epoch),outgoing)
            if not self.delivery and self.packet['sequence']<(t//50)-1:self.stats['udp_missing_intervals']+=1
        if request is not None:
            packet=request['object_packet'];frame=wire.ethernet(packet,self.config['ethernet'])
            if self.active('udp_repeat',t):frame=self.last_wire or frame
            else:self.last_wire=frame
            if self.active('udp_drop',t):self.event('fault','perception','udp_drop',sequence=packet['sequence'])
            elif self.active('udp_delay',t):self.delayed.append((t+400,frame));self.event('fault','perception','udp_queued',sequence=packet['sequence'],due_ms=t+400)
            else:outgoing['perception'].append({'bus':'ethernet','data':list(frame)})
        ready=[x for x in self.delayed if x[0]<=t];self.delayed=[x for x in self.delayed if x[0]>t]
        outgoing['perception'] += [{'bus':'ethernet','data':list(data)} for _,data in ready]
        for frame in outgoing['perception']:
            packet=wire.decode_ethernet(frame['data'],self.config['ethernet']);self.event('ethernet','perception','udp_tx',sequence=packet['sequence'],bytes=len(frame['data']),frame_sha256=hashlib.sha256(bytes(frame['data'])).hexdigest())
        for role,node in self.nodes.items():node.send({'op':'tick','time_ms':t,'frames':outgoing[role]})
        for role,node in self.nodes.items():
            result=node.receive()
            if result.get('time_ms')!=t or result.get('receiver')!=role:raise ValueError('SIL Kit barrier identity mismatch')
            self.pending[role]=result['frames']
            for ack in result['acks']:
                if ack['status']!=1:raise ValueError('SIL Kit transmit failed: '+str(ack))
    def step(self,request):
        seq=request['sequence'];t=seq*50
        if seq!=self.last_external+1 or abs(request['time_s']-t/1000)>1e-9:raise ValueError('External 50ms sequence/time mismatch')
        self.events=[];self.controller_steps=[]
        if not hasattr(self,'ego'):self.ego=request['ego']
        for current in range(self.current+1,t):self.tick(current)
        self.ego=request['ego'];self.tick(t,request);self.last_external=seq
        brake=self.brake();base=dict(self.controller_reply or {})
        state='degraded' if brake['reason']!='command' or self.heartbeat_bad else base.get('state','degraded')
        plan=dict(base.get('plan') or {})
        if not plan:
            # Geometry-only diagnostic while the actual OS has not requested control.
            # This never computes or replaces Brake ECU output.
            import city_control
            progress=city_control.project(self.ego,self.init['route'])['progress_m']
            plan=dict(progress_m=progress,steering_progress_m=progress,state='waiting_for_os_control',
                desired_speed_mps=0.,diagnostic_source='host_route_projection_without_control')
        plan['hil_state']=state
        return dict(base,op='step',schema_version='hil-dut-reply/1',sequence=seq,control=brake['control'],state=state,plan=plan,
            object_sequence=self.packet['sequence'],object_age_s=t/1000-self.packet['time_s'],fresh_recovery_steps=base.get('fresh_recovery_steps',0),
            network={'events':self.events,'controller_steps':self.controller_steps,'brake':brake,'stats':dict(self.stats),'epoch':self.epoch,
                'time_ms':t,'heartbeat_override':self.heartbeat_bad,'manifest_sha256':self.runtime['manifest_sha256']})
    def close(self,graceful=True):
        error=None
        if graceful:
            try:
                for node in self.nodes.values():node.send({'op':'close'})
                for node in self.nodes.values():node.receive()
                for child in [*self.bsw.values(),self.os,self.controller]:child.call({'op':'close'})
            except BaseException as e:error=e
        for child in reversed(self.children):child.stop()
        if error:raise error

def main():
    root=Path(__file__).resolve().parent;network=None
    try:
        for line in sys.stdin:
            request=json.loads(line);op=request['op']
            if op=='init' and network is None:
                from hil_network import validate_install
                validate_install(request['network_runtime'],root)
                network=Network(request,root)
                reply={'op':'init','schema_version':'hil-dut-reply/1','status':'ready','pid':os.getpid(),'dut_version':'network-ecu/1','dut_profile':request['dut_profile'],'manifest_sha256':request['network_runtime']['manifest_sha256']}
            elif op=='step' and network:reply=network.step(request)
            elif op=='close' and network:
                network.close();network=None;reply={'op':'close','schema_version':'hil-dut-reply/1','status':'closed'}
            else:raise ValueError('Invalid network lifecycle request')
            print(json.dumps(reply,allow_nan=False,separators=(',',':')),flush=True)
            if op=='close':break
    finally:
        if network:network.close(False)
if __name__=='__main__':main()
