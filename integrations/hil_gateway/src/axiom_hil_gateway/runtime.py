"""Real wall-clock UDP / two-segment SocketCAN bench. Reference ECUs are separate processes.

The gateway never calculates ADAS detection or Brake watchdog decisions. Only received
Brake output bytes supply vehicle commands. vcan and physical devices share CanBus.
"""
import argparse,copy,fcntl,hashlib,json,os,queue,select,signal,socket,struct,subprocess,sys,threading,time,uuid
from pathlib import Path
try:
    from .socketcan import CanBus,setup_vcan
    from .codecs import load_codec
    from .hooks import ReferenceHooks,validate_runtime_faults
    from .model_monitor import ModelMonitor
except ImportError:
    from bridge_socketcan import CanBus,setup_vcan
    from bridge_codecs import load_codec
    from bridge_hooks import ReferenceHooks,validate_runtime_faults
    from bridge_model_monitor import ModelMonitor
try:
    from axiom_vecu_network import wire
    from axiom_carla.hil_dut import ReferenceDut
except ImportError:
    import network_wire as wire
    from hil_dut import ReferenceDut

SAFE={'throttle':0.,'brake':1.,'steer':0.}
CLOCK='linux_monotonic_shared_kernel'

def udp(bind='127.0.0.1'):
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET,40,1) # SO_RXQ_OVFL: Linux runtime only
        s.bind((bind,0));s.setblocking(False);return s
    except BaseException:s.close();raise

def receive_udp(s):
    raw,anc,flags,peer=s.recvmsg(65535,128)
    if flags&socket.MSG_TRUNC:raise OSError('UDP payload truncated')
    for level,kind,value in anc:
        if level==socket.SOL_SOCKET and kind==40 and struct.unpack('=I',value[:4])[0]:
            raise OSError('UDP receive queue overflow')
    return raw,peer

def send_json(s,target,value):s.sendto(json.dumps(value,separators=(',',':'),allow_nan=False).encode(),tuple(target))

def reference(role,config):
    """Standalone reference ECU task. Its telemetry is explicitly reference_process evidence."""
    bus=CanBus(config['can_a' if role=='adas' else 'can_b']);control=udp();objects=udp() if role=='adas' else None
    telemetry=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);session=config['session'];start=None;last_lease=time.monotonic()
    print(json.dumps(dict(role=role,pid=os.getpid(),control_port=control.getsockname()[1],object_port=objects.getsockname()[1] if objects else None)),flush=True)
    t0=0;next_tick=0;counter=0;ego=None;packet=None;present=False;last_hb=None;hb_bad=True;hb_fresh=0
    last_command=None;command=SAFE.copy();last_seq=None;epoch=None;fresh=0;brake_bad=True;dut=None
    hooks=ReferenceHooks(config.get('reference_faults',[]) if role=='adas' else [])
    monitor=ModelMonitor(config['model_policy']) if role=='adas' and config.get('model_policy') else None
    model_sample=None
    def emit(kind,**fields):
        send_json(telemetry,config['telemetry'],dict(session=session,ecu=role,kind=kind,monotonic_ns=time.monotonic_ns(),
            clock_id=CLOCK,evidence_source='reference_process',pid=os.getpid(),**fields))
    try:
        while time.monotonic()-last_lease<1.:
            now=time.monotonic_ns()
            if start and role=='adas':
                for kind,fields in hooks.advance((now-t0)/1e6):
                    emit(kind,**fields)
                    if kind=='reset_asserted':
                        packet=None;present=False;last_hb=None;hb_bad=True;hb_fresh=0;dut=None
                    elif kind=='reset_released':
                        dut=ReferenceDut(start['initialization']);dut.degraded=True
                        emit('reinitialized',epoch=hooks.epoch,initialization_sha256=start['initialization_sha256'])
            for s in [control]+([objects] if objects else []):
                for _ in range(128):
                    try:raw,_=receive_udp(s)
                    except BlockingIOError:break
                    if s is objects:
                        if hooks.reset:
                            emit('object_discarded_during_reset');continue
                        candidate=wire.parse_objects(raw)
                        if packet is None or candidate['sequence']>packet['sequence']:packet=candidate;present=True
                        else:emit('packet_repeat',sequence=candidate['sequence']);present=True
                        emit('object_rx',sequence=candidate['sequence'],payload_sha256=hashlib.sha256(raw).hexdigest());continue
                    v=json.loads(raw)
                    if v.get('session')!=session:continue
                    op=v['op']
                    if op=='close':return
                    if op=='lease':last_lease=time.monotonic()
                    if op=='start':
                        start=v;last_lease=time.monotonic();t0=v['t0_ns'];ego=v['ego']
                        next_tick=t0+(5_000_000 if role=='adas' else 8_000_000)
                        if role=='adas':dut=ReferenceDut(v['initialization'])
                        emit('initialized',initialization_sha256=v['initialization_sha256'])
                    if op=='pose':ego=v['ego'];emit('pose_rx',sequence=v['sequence'])
                    if op=='measurement' and monitor:
                        model_sample=v['sample']
                        emit('model_measurement_rx',sample=model_sample,source=config['model_source'])
            for _ in range(128):
                frame=bus.receive()
                if frame is None:break
                if frame.get('frame_type')=='error':emit('bus_error',frame=frame);raise OSError('Reference ECU CAN error frame')
                if frame['local_echo'] and config['target']=='hardware':continue
                cid,data=frame['can_id'],frame['data'];now=time.monotonic_ns()
                if role=='adas' and cid==513:
                    if hooks.reset:continue
                    last_hb=now;hb_fresh+=1
                    if hb_fresh>=3:hb_bad=False
                    emit('heartbeat_rx',data_hex=frame['data_hex'])
                if role=='brake' and cid==256:
                    received,seq,ep=wire.decode_command(data)
                    if epoch is not None and ep!=epoch:
                        fresh=0;brake_bad=True
                        emit('command_epoch_changed',previous_epoch=epoch,epoch=ep)
                    if last_seq is not None and ep==epoch and (seq-last_seq)%256 not in range(1,128):
                        emit('command_rejected',reason='duplicate_or_old_counter');continue
                    command=received;last_command=now;last_seq=seq;epoch=ep
                    fresh+=1
                    if fresh>=3:brake_bad=False
                    emit('can_rx',can_id=cid,data_hex=frame['data_hex'],command_sequence=seq)
            if start and now>=t0:
                if role=='adas' and last_hb is not None and now-last_hb>=100_000_000 and not hb_bad:
                    hb_bad=True;hb_fresh=0;emit('detection',reason='heartbeat_timeout',silence_ms=(now-last_hb)/1e6,threshold_ms=100)
                if role=='brake' and last_command is not None and now-last_command>=100_000_000 and not brake_bad:
                    brake_bad=True;fresh=0;emit('detection',reason='command_watchdog',silence_ms=(now-last_command)/1e6,threshold_ms=100)
                if now>=next_tick:
                    lateness=(now-next_tick)/1e6
                    if lateness>config['max_lateness_ms']:emit('timing_error',lateness_ms=lateness,task=role)
                    if role=='adas':
                        if hooks.reset or hooks.os_blocked:
                            emit('task_suppressed',sequence=counter,reason='reset' if hooks.reset else 'os_event_drop',epoch=hooks.epoch)
                            counter+=1;next_tick+=50_000_000
                            if now>=next_tick:emit('timing_error',reason='missed_period');return
                            continue
                        if packet is None and hooks.epoch:
                            # Reboot needs an observed input; no invented empty camera frame.
                            emit('awaiting_fresh_input',sequence=counter,epoch=hooks.epoch)
                            counter+=1;next_tick+=50_000_000
                            if now>=next_tick:emit('timing_error',reason='missed_period');return
                            continue
                        if packet is None or packet['sequence']>counter:
                            emit('timing_error',reason='object_not_available_at_sampling_boundary',sequence=counter)
                            raise TimeoutError('ADAS input missing at initial/current sampling boundary')
                        # Own 20Hz clock, not a gateway-provided control decision.
                        request=dict(op='step',schema_version='hil-dut-step/1',sequence=counter,time_s=counter*.05,
                            ego=ego,object_packet=packet,object_delivery_present=present)
                        # Future input is held until this ECU's sampling boundary.
                        if packet['sequence']<=counter:
                            # The harness clock continues while tasks are suppressed. Only the
                            # protocol cursor is rebased; controller state is not advanced.
                            dut.sequence=counter-1
                            reply=dut.step(request);present=False
                            if reply['state']=='degraded' and getattr(dut,'reported_state',None)!='degraded':
                                emit('detection',reason='object_age',age_ms=reply['object_age_s']*1000,threshold_ms=250)
                            dut.reported_state=reply['state'];counter+=1
                            output=dict(reply['control'])
                            if hb_bad:output.update(throttle=0.,brake=1.)
                            model_report=monitor.step(model_sample,counter-1,(now-t0)/1e6) if monitor else None
                            if model_report:
                                members=model_report.get('members',{monitor.policy['boundary']:model_report})
                                for boundary,member in members.items():
                                    source=config['model_source'].get('members',{}).get(boundary,config['model_source'])
                                    common=dict(measurement=member,source=source,aggregate_protect=model_report['protect'],remaining_boundaries=model_report.get('active_boundaries',[]))
                                    if member['detected']:emit('detection',reason='model_'+boundary,**common)
                                    if member['recovered']:emit('model_recovered',**common)
                                if monitor.members and model_report['recovered']:
                                    emit('model_group_recovered',measurement=model_report,source=config['model_source'])
                                if model_report['missing']:emit('timing_error',reason='model_measurement_missing',sequence=counter-1)
                                if model_report['protect']:output.update(throttle=0.,brake=1.)
                            data=wire.command_bytes(output,counter-1,hooks.epoch);bus.send(256,data)
                            emit('can_tx',can_id=256,data_hex=bytes(data).hex(),control=output)
                            diagnostic=dict(reply,state='degraded' if hb_bad else reply['state'],control=output)
                            if model_report and model_report['protect'] and not hb_bad:diagnostic['state']=model_report['state']
                            emit('state',reply=diagnostic,model_monitor=model_report,epoch=hooks.epoch,heartbeat_override=hb_bad,reason='heartbeat_timeout' if hb_bad else reply['state'])
                    elif role=='brake':
                        output=dict(command)
                        reason='initial' if last_command is None else 'watchdog' if now-last_command>=100_000_000 else 'recovery' if brake_bad else 'command'
                        if reason!='command':output.update(throttle=0.,brake=1.)
                        data=wire.command_bytes(output,counter,0);counter+=1;bus.send(257,data)
                        emit('output',can_id=257,data_hex=bytes(data).hex(),control=output,reason=reason,
                            accepted_command_sequence=last_seq)
                    next_tick+=50_000_000 if role=='adas' else 10_000_000
                    # A missed period is an invalid bench run; never burst catch-up output.
                    if now-next_tick>=0:emit('timing_error',reason='missed_period');return
            time.sleep(.0005)
    finally:
        bus.close();control.close();telemetry.close()
        if objects:objects.close()

class Bench:
    def __init__(self,runtime,initialization):
        validate_runtime_faults(runtime)
        self.runtime=runtime;self.config=runtime['device'];self.init=initialization;self.session=uuid.uuid4().hex
        self.codec=load_codec(self.config,Path(__file__).parent);self.ids=self.codec.can_ids
        self.lock=open(self.config['lock_path'],'a+');self.closed=False;self.children=[];self.buses=[];self.stop=threading.Event()
        self.guard=threading.RLock();self.events=[];self.errors=[];self.latest=None;self.diagnostic=None;self.started=False;self.t0=None
        self.pending=[];self.delayed=[];self.last_wire=None;self.injected=set();self.ended=set();self.failure=None;self.next_step=0
        self.sent={};self.received={};self.dropped={};self.status_count=0;self.last_status_seq=None;self.last_status_epoch=None
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.a=CanBus(self.config['can_a']);self.buses.append(self.a)
            self.b=CanBus(self.config['can_b']);self.buses.append(self.b)
            self.telemetry=udp(self.config.get('bind_ip','127.0.0.1'));self.sender=udp(self.config.get('bind_ip','127.0.0.1'));self.endpoints={}
            if self.config['target']=='vcan':
                for role in ('adas','brake'):
                    config=dict(self.config,session=self.session,telemetry=self.telemetry.getsockname(),max_lateness_ms=runtime['max_lateness_ms'],reference_faults=runtime['faults'])
                    if runtime.get('model_input'):
                        config.update(model_policy=runtime['model_input']['policy'],model_source=runtime['model_input']['source'])
                    child=subprocess.Popen([sys.executable,__file__,'--role',role,'--config',json.dumps(config)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=sys.stderr,text=True)
                    self.children.append(child)
                    if not select.select([child.stdout],[],[],5)[0]:raise TimeoutError('Reference ECU startup timed out')
                    ready=json.loads(child.stdout.readline())
                    if ready['role']!=role or ready['pid']!=child.pid:raise ValueError('Reference process identity mismatch')
                    self.endpoints[role]=dict(control=('127.0.0.1',ready['control_port']),objects=('127.0.0.1',ready['object_port']) if role=='adas' else None)
            else:
                # Operator registration supplies physical endpoint/identity driver, never public paths.
                self.endpoints=copy.deepcopy(self.config['endpoints'])
                try:from .devices import initialize_devices
                except ImportError:from bridge_devices import initialize_devices
                initialize_devices(self.config,self.session,self.init,(self.config['advertise_ip'],self.telemetry.getsockname()[1]))
            self.thread=threading.Thread(target=self.loop,name='hil-bus-gateway',daemon=True);self.thread.start()
        except BaseException:self.close();raise
    def emit(self,kind,**fields):
        with self.guard:
            stamp=time.monotonic_ns()
            self.events.append(dict(kind=kind,monotonic_ns=stamp,time_ms=(stamp-self.t0)/1e6 if self.t0 else None,
                clock_id=CLOCK,evidence_source='gateway_observation',**fields))
    def error(self,reason,**details):
        with self.guard:
            value=dict(reason=reason,**details);self.errors.append(value);self.emit('validity_error',**value)
    def active(self,kind,ms):return any(f['kind']==kind and f['start_ms']<=ms<f['end_ms'] for f in self.runtime['faults'])
    def tx(self,bus,cid,data):
        bus.send(cid,data);self.emit('can_tx_accepted',interface=bus.interface,can_id=cid,data_hex=bytes(data).hex())
    def control(self,role,op,**fields):send_json(self.sender,self.endpoints[role]['control'],dict(op=op,session=self.session,**fields))
    def loop(self):
        next_rest=0;next_lease=0;next_model=0
        try:
            while not self.stop.is_set():
                now=time.monotonic_ns()
                if any(c.poll() is not None for c in self.children):raise RuntimeError('Reference ECU exited during active bench')
                with self.guard:
                    if self.t0 is not None:
                        ms=(now-self.t0)/1e6
                        if ms>=0:
                            model=self.runtime.get('model_input')
                            if model and next_model<len(model['samples'])*50 and ms>=next_model:
                                late=ms-next_model;sample=model['samples'][next_model//50]
                                if late>self.runtime['max_lateness_ms']:self.error('model_schedule_late',sequence=sample['sequence'],lateness_ms=late)
                                self.control('adas','measurement',sample=sample)
                                self.emit('model_measurement_tx',sample=sample,source=model['source'],scheduled_ms=next_model,lateness_ms=late)
                                next_model+=50
                                if next_model<=ms:raise TimeoutError('Model replay missed a full period; no catch-up')
                            for i,f in enumerate(self.runtime['faults']):
                                if i not in self.injected and ms>=f['start_ms']:
                                    self.injected.add(i);self.emit('fault_start',fault_index=i,fault=f,scheduled_ms=f['start_ms'],lateness_ms=ms-f['start_ms'])
                                    if ms-f['start_ms']>self.runtime['max_lateness_ms']:self.error('fault_schedule_late',lateness_ms=ms-f['start_ms'])
                                if i in self.injected and i not in self.ended and ms>=f['end_ms']:
                                    self.ended.add(i);self.emit('fault_end',fault_index=i,scheduled_ms=f['end_ms'])
                                    if f['kind']=='udp_delay':self.delayed=[]
                            if ms>=next_rest:
                                late=ms-next_rest
                                if late>self.runtime['max_lateness_ms']:self.error('restbus_schedule_late',lateness_ms=late)
                                state=self.codec.vehicle_state(self.ego)
                                if not self.active('restbus_drop',ms):
                                    self.tx(self.a,self.ids['vehicle_state'],state);self.tx(self.a,self.ids['heartbeat'],self.codec.heartbeat(int(next_rest//10)))
                                else:self.emit('injection_drop',boundary='restbus.can.tx',can_ids=[self.ids['vehicle_state'],self.ids['heartbeat']])
                                next_rest+=10
                                if next_rest<=ms:next_rest=(int(ms//10)+1)*10;self.error('restbus_period_missed')
                            if ms>=next_lease:
                                for role in self.endpoints:self.control(role,'lease')
                                next_lease=ms+50
                        while self.pending and self.pending[0][0]<=now:
                            due,request=self.pending.pop(0);late=(now-due)/1e6
                            if late>self.runtime['max_lateness_ms']:self.error('input_schedule_late',sequence=request['sequence'],lateness_ms=late)
                            self.ego=request['ego'];self.control('adas','pose',ego=self.ego,sequence=request['sequence'])
                            payload=self.codec.object_payload(request['object_packet']);sha=hashlib.sha256(payload).hexdigest()
                            self.emit('object_generated',sequence=request['sequence'],payload_sha256=sha,scheduled_ms=request['sequence']*50)
                            if self.active('udp_repeat',ms):
                                payload=self.last_wire or payload
                                self.emit('injection_repeat',boundary='perception.udp.tx',generated_sequence=request['sequence'],delivered_sequence=self.codec.parse_objects(payload)['sequence'])
                            else:self.last_wire=payload
                            if self.active('udp_drop',ms):self.emit('injection_drop',boundary='perception.udp.tx',sequence=request['sequence'])
                            elif self.active('udp_delay',ms):
                                if len(self.delayed)>=64:raise OverflowError('UDP delay queue limit exceeded')
                                delay=next(f.get('delay_ms',400) for f in self.runtime['faults'] if f['kind']=='udp_delay' and f['start_ms']<=ms<f['end_ms'])
                                self.delayed.append((now+int(delay*1e6),payload));self.emit('object_queued',sequence=request['sequence'],delay_ms=delay)
                            else:self.send_object(payload)
                        ready=[p for p in self.delayed if p[0]<=now];self.delayed=[p for p in self.delayed if p[0]>now]
                        for due,payload in ready:
                            if (now-due)/1e6>self.runtime['max_lateness_ms']:self.error('delay_delivery_late',lateness_ms=(now-due)/1e6)
                            self.send_object(payload)
                    for bus in self.buses:
                        for _ in range(128):
                            frame=bus.receive()
                            if frame is None:break
                            if frame.get('frame_type')=='error':
                                self.error('can_bus_error',frame=frame);raise OSError('CAN bus error; bench halted')
                            if frame['local_echo'] and self.config['target']=='hardware':continue
                            self.emit('can_rx',**frame)
                            cid=frame['can_id'];self.received[cid]=self.received.get(cid,0)+1
                            if bus is self.a and cid==self.ids['command']:
                                command,seq,epoch=self.codec.decode_command(frame['data'])
                                self.emit('can_command_observed',control=command,data_hex=frame['data_hex'],command_sequence=seq,interface=bus.interface)
                                if self.t0 and self.active('can_command_drop',(now-self.t0)/1e6):
                                    self.emit('injection_drop',boundary='adas_brake.can.bridge',data_hex=frame['data_hex']);continue
                                self.tx(self.b,cid,frame['data'])
                            elif bus is self.b and cid==self.ids['status']:
                                self.tx(self.a,cid,frame['data'])
                                if self.t0 and self.active('brake_feedback_drop',(now-self.t0)/1e6):
                                    self.emit('injection_drop',boundary='brake.plant.feedback');continue
                                output,seq,epoch=self.codec.decode_command(frame['data'])
                                if self.last_status_seq is not None and epoch==self.last_status_epoch and (seq-self.last_status_seq)%256 not in range(1,128):
                                    self.error('duplicate_brake_feedback');continue
                                self.last_status_seq=seq;self.last_status_epoch=epoch;self.status_count+=1
                                self.latest=dict(frame,control=output);self.emit('brake_output_observed',control=output,data_hex=frame['data_hex'],status_sequence=seq)
                    for _ in range(128):
                        try:raw,_=receive_udp(self.telemetry)
                        except BlockingIOError:break
                        event=json.loads(raw)
                        if event.get('session')!=self.session:continue
                        event['gateway_received_monotonic_ns']=time.monotonic_ns()
                        event['gateway_received_time_ms']=(event['gateway_received_monotonic_ns']-self.t0)/1e6 if self.t0 else None
                        event['time_ms']=(event['monotonic_ns']-self.t0)/1e6 if self.t0 and event.get('clock_id')==CLOCK and self.config['target']=='vcan' else None
                        self.events.append(event)
                        if event['kind']=='state':self.diagnostic=event
                        if event.get('ecu')=='adas' and event['kind'] in ('reset_asserted','task_suppressed','awaiting_fresh_input'):self.diagnostic=None
                        if event['kind']=='timing_error':self.error('ecu_schedule_late',details=event)
                time.sleep(.0004)
        except BaseException as exc:self.failure=exc;self.stop.set()
    def send_object(self,payload):
        self.sender.sendto(payload,tuple(self.endpoints['adas']['objects']))
        self.emit('object_tx_accepted',sequence=self.codec.parse_objects(payload)['sequence'],payload_sha256=hashlib.sha256(payload).hexdigest())
    def step(self,request):
        if self.closed:raise RuntimeError('Bench is closed')
        seq=request['sequence']
        if seq!=self.next_step or request['time_s']!=seq*.05:raise ValueError('Sequential 50ms source input required')
        self.next_step+=1
        if self.t0 is None:
            with self.guard:
                self.t0=time.monotonic_ns()+100_000_000;self.ego=request['ego']
                for role in self.endpoints:self.control(role,'start',t0_ns=self.t0,ego=self.ego,initialization=self.init,
                    initialization_sha256=hashlib.sha256(json.dumps(self.init,sort_keys=True).encode()).hexdigest())
        due=self.t0+seq*50_000_000
        late=(time.monotonic_ns()-due)/1e6
        if late>=50:raise TimeoutError('Plant missed an entire 50ms input period; no catch-up playback')
        with self.guard:
            if self.pending:raise OverflowError('Plant input queue has an unconsumed sample')
            self.pending.append((due,copy.deepcopy(request)))
        # ADAS samples at +5ms; Brake reports at +8ms. Leave +12..50ms for the plant.
        deadline=due+12_000_000
        while time.monotonic_ns()<deadline:
            if self.closed:raise RuntimeError('Bench closed during sample')
            if self.failure:raise self.failure
            time.sleep(.0005)
        if self.failure:raise self.failure
        with self.guard:
            latest=copy.deepcopy(self.latest);diagnostic=copy.deepcopy(self.diagnostic);events=self.events;self.events=[]
        if latest is None or time.monotonic_ns()-latest['observed_monotonic_ns']>=100_000_000:
            raise TimeoutError('Brake output feedback missing/stale; no software replacement command')
        base=copy.deepcopy(diagnostic['reply']) if diagnostic else {}
        # State is an ECU diagnostic only. It is never inferred from braking bytes.
        output=dict(op='step',schema_version='hil-dut-reply/1',sequence=seq,control=latest['control'],
            state=base.get('state','unobserved'),object_sequence=base.get('object_sequence',-1),
            object_age_s=base.get('object_age_s'),duplicate_packet_count=base.get('duplicate_packet_count'),
            missing_packet_count=base.get('missing_packet_count'),fresh_recovery_steps=base.get('fresh_recovery_steps'),plan=base.get('plan',{'diagnostic_source':'unobserved'}),
            bridge=dict(schema_version='hil-gateway-observation/1',session=self.session,target=self.config['target'],
                feedback_sample_phase_ms=12,clock_id=CLOCK,clock_uncertainty_ns=0 if self.config['target']=='vcan' else None,
                t0_monotonic_ns=self.t0,source_time_ms=seq*50,observed_time_ms=(time.monotonic_ns()-self.t0)/1e6,
                diagnostic_observed=diagnostic is not None,diagnostic_source=('reference_process' if self.config['target']=='vcan' else 'physical_ecu_report') if diagnostic else 'unobserved',
                model_monitor=diagnostic.get('model_monitor') if diagnostic else None,
                events=events,brake_feedback=latest,validity_errors=copy.deepcopy(self.errors),
                physical_hardware_connected=self.config['target']=='hardware'))
        self.emit('plant_command_selected',sequence=seq,control=latest['control'],feedback_data_hex=latest['data_hex'])
        return output
    def close(self):
        if self.closed:return
        self.closed=True;self.stop.set()
        if hasattr(self,'thread'):self.thread.join(timeout=2)
        if hasattr(self,'sender'):
            for role in self.endpoints:
                try:self.control(role,'close')
                except OSError:pass
        for child in self.children:
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=2)
            child.stdout.close()
        for bus in self.buses:bus.close()
        for name in ('telemetry','sender'):
            if hasattr(self,name):getattr(self,name).close()
        self.lock.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=['adas','brake']);parser.add_argument('--config');parser.add_argument('--setup',action='store_true');args=parser.parse_args()
    if args.setup:setup_vcan();print('vcan_ready');return
    if args.role:reference(args.role,json.loads(args.config));return
    bench=None;replay_frames=None;replay_thread=None;replay_results=queue.Queue(maxsize=32);replay_errors=[]
    def replay_loop():
        try:
            for index,frame in enumerate(replay_frames):
                request=dict(op="step",schema_version="hil-dut-step/1",sequence=index,time_s=index*.05,ego=frame["ego"],
                    object_packet=dict(sequence=index,time_s=index*.05,objects=frame["objects"]),object_delivery_present=True)
                reply=bench.step(request)
                replay_results.put((request,reply),timeout=.1)
        except BaseException as exc:replay_errors.append(exc)
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(143))
    try:
        for line in sys.stdin:
            request=json.loads(line)
            if request['op']=='init':
                runtime=request.pop('gateway_runtime');replay_frames=request.pop('replay_frames',None)
                if replay_frames is not None and not 20<=len(replay_frames)<=1200:raise ValueError('Replay frame count outside contract')
                bench=Bench(runtime,request)
                reply=dict(op='init',schema_version='hil-dut-reply/1',status='ready',pid=os.getpid(),dut_version='hil-gateway/1',dut_profile=request.get('dut_profile'))
            elif request['op']=='step':
                if replay_frames is None:reply=bench.step(request)
                else:
                    if replay_thread is None:
                        replay_thread=threading.Thread(target=replay_loop,name='linux-replay-clock',daemon=True);replay_thread.start()
                    while True:
                        if replay_errors:raise replay_errors[0]
                        try:frozen,reply=replay_results.get(timeout=.1);break
                        except queue.Empty:continue
                    if frozen!=request:raise ValueError('Host sample request differs from frozen replay input')
            elif request['op']=='close':
                bench.close();reply=dict(op='close',schema_version='hil-dut-reply/1',status='closed',children_reaped=True)
            else:raise ValueError('Unsupported gateway operation')
            print(json.dumps(reply,separators=(',',':'),allow_nan=False),flush=True)
            if request['op']=='close':break
    except BaseException as exc:
        if bench:
            print(json.dumps({'schema_version':'hil-gateway-failure/1','error':str(exc),'events':bench.events,'validity_errors':bench.errors}),file=sys.stderr,flush=True)
        raise
    finally:
        if bench:bench.close()
        if replay_thread:replay_thread.join(timeout=1)
if __name__=='__main__':main()
