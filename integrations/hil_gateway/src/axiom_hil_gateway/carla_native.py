"""Native software-DUT transport, simulated object faults and source-pinned launcher."""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
try:
    from ade2e import hil_scene_profiles
except ImportError:
    import hil_scene_profiles
try:
    from axiom_carla import city_native, city_control, hil_dut, hil_network, hil_gateway
    from . import policy as hil_policy
except ImportError:
    import city_native
    import city_control
    import hil_dut
    import hil_policy
    import hil_network
    import hil_gateway

VERSION = '1.0.0'
TRACE_SCHEMA = 'hil-demo-trace/1'


def source_hashes():
    return {'hil_native_sha256': city_native.file_sha(__file__),
            'city_control_sha256': city_native.file_sha(city_control.__file__),
            'hil_dut_sha256': city_native.file_sha(hil_dut.__file__),
            'hil_scene_profiles_sha256': city_native.file_sha(hil_scene_profiles.__file__),
            'hil_policy_sha256':city_native.file_sha(hil_policy.__file__),
            'hil_network_sha256':city_native.file_sha(hil_network.__file__),
            'hil_gateway_sha256':city_native.file_sha(hil_gateway.__file__),
            'vecu_remote_sha256':city_native.file_sha(Path(hil_dut.__file__).with_name('vecu_remote.py'))}


def lead_motion(profile, time_s):
    """Select the authored pre-tick target; physical feedback remains its owner."""
    phase = max((p for p in profile['lead_speed_schedule'] if p['at_s'] <= time_s + 1e-9),
                key=lambda p: p['at_s'])
    label = ('cruise' if phase['at_s'] == 0 else 'deceleration' if profile['id'] == 'lead_deceleration'
             else 'stop' if phase['target_speed_mps'] == 0 else 'restart')
    return {'phase': label, 'at_s': phase['at_s'], 'target_speed_mps': phase['target_speed_mps']}


def lead_presentation(profile, row, lead, previous, prior_events):
    """Keep acknowledged command boundaries separate from observed speed changes."""
    hil_dut.state_check(lead)
    motion = lead_motion(profile, row['time_s'])
    if lead.get('plan', {}).get('hil_lead_motion') != motion:
        raise ValueError('Lead response plan differs from frozen scene overlay')
    fields = {'lead_actor_id': lead['id'], 'lead_speed_mps': lead['speed_mps'],
              'lead_target_speed_mps': motion['target_speed_mps'], 'lead_phase': motion['phase']}
    events = []
    time_s = (row['sequence'] + 1) * .05
    def emit(kind, provenance):
        events.append({'time_s': time_s, 'input_time_s': row['time_s'], 'type': kind,
                       'label': kind, 'actor_id': lead['id'], 'provenance': provenance})
    if previous and previous['lead_phase'] != motion['phase']:
        emit('lead_' + motion['phase'] + '_commanded', 'acknowledged_lead_control')
    seen = {event['type'] for event in prior_events}
    if previous:
        before, speed = previous['lead_speed_mps'], lead['speed_mps']
        if motion['phase'] == 'deceleration' and before > .75 >= speed and 'lead_slowed' not in seen:
            emit('lead_slowed', 'native_response_speed')
        if motion['phase'] == 'stop' and before > .2 >= speed and 'lead_stopped' not in seen:
            emit('lead_stopped', 'native_response_speed')
        if (motion['phase'] == 'restart' and 'lead_stopped' in seen and before <= .5 < speed
                and 'lead_moving_again' not in seen):
            emit('lead_moving_again', 'native_response_speed')
    return fields, events


def validate_fault(fault):
    if not isinstance(fault, dict) or set(fault) != {'kind', 'start_s', 'duration_s', 'delay_ms'}:
        raise ValueError('HIL fault requires kind/start_s/duration_s/delay_ms')
    if fault['kind'] not in ('delay', 'drop', 'repeat') or any(not hil_dut.finite(fault[k]) for k in ('start_s', 'duration_s', 'delay_ms')):
        raise ValueError('Unsupported HIL fault')
    if not 1 <= fault['start_s'] <= 17 or abs(fault['start_s'] * 2 - round(fault['start_s'] * 2)) > 1e-8:
        raise ValueError('HIL fault start requires 1..17 seconds in 0.5 second steps')
    if not .5 <= fault['duration_s'] <= 4 or abs(fault['duration_s'] * 2 - round(fault['duration_s'] * 2)) > 1e-8:
        raise ValueError('HIL fault duration requires 0.5..4 seconds in 0.5 second steps')
    if fault['start_s'] + fault['duration_s'] > 17.5:
        raise ValueError('HIL fault must end by 17.5 seconds to retain recovery in the 18 second scene')
    if fault['kind'] == 'delay':
        if not 50 <= fault['delay_ms'] <= 1000 or fault['delay_ms'] % 50:
            raise ValueError('HIL delay requires 50..1000ms in 50ms steps')
    elif fault['delay_ms'] != 0:
        raise ValueError('Drop does not accept a delay')
    return copy.deepcopy(fault)


def side_contract(config):
    side = config.get('side')
    mode = config.get('comparison_kind', 'same_scene_fault_ab')
    if side not in ('baseline', 'faulted') or mode not in ('same_scene_fault_ab', 'same_fault_dut_ab','package_ab'):
        raise ValueError('Unsupported HIL side or comparison')
    fault = validate_fault(config['fault'])
    if mode == 'package_ab':
        runtime=hil_policy.validate_runtime(config.get('runtime'))
        if type(config.get('fault_enabled')) is not bool:raise ValueError('Package fault enable must be explicit')
        if runtime['kind']=='network_reference':
            if config['fault_enabled'] or runtime['side']!=side:
                raise ValueError('Network runtime owns fault injection and must match its side')
            return None, hil_dut.validate_dut_profile(config.get('dut_profile'))
        return fault if config['fault_enabled'] else None, hil_dut.validate_dut_profile(config.get('dut_profile'))
    if mode == 'same_fault_dut_ab':
        return fault, hil_dut.validate_dut_profile(config.get('dut_profile'))
    if 'dut_profile' in config:
        raise ValueError('Legacy fault comparison does not accept a DUT profile')
    return fault if side == 'faulted' else None, None


class ObjectFault:
    """Fault delivery uses simulation time. It never sleeps or changes fresh ego."""
    def __init__(self, fault=None):
        self.fault = validate_fault(fault) if fault else None
        self.history = []
        self.delivered = None

    def step(self, sequence, objects):
        now = sequence * .05
        packet = {'sequence': sequence, 'time_s': now, 'objects': copy.deepcopy(objects)}
        self.history.append(packet)
        active = bool(self.fault and self.fault['start_s'] <= now < self.fault['start_s'] + self.fault['duration_s'])
        candidate = packet
        if active and self.fault['kind'] == 'drop':
            candidate = None
        elif active and self.fault['kind'] == 'repeat':
            candidate = copy.deepcopy(self.delivered)
        elif active:
            cutoff = now - self.fault['delay_ms'] / 1000
            candidate = next((p for p in reversed(self.history) if p['time_s'] <= cutoff + 1e-8), None)
        updated = bool(candidate and (self.delivered is None or candidate['sequence'] > self.delivered['sequence']))
        if updated:
            self.delivered = candidate
        if self.delivered is None:
            raise ValueError('Object fault cannot start before the initial object packet')
        # A 1 second maximum delay requires only 21 records; retain a small margin.
        self.history = self.history[-max(42,int(self.fault['delay_ms']/50)+2 if self.fault else 42):]
        return copy.deepcopy(self.delivered), {'active': active, 'kind': self.fault['kind'] if active else None,
                                               'new_packet_delivered': updated, 'generated_sequence': sequence,
                                               'delivery_present': candidate is not None,
                                               'duplicate_packet_delivered': bool(candidate and not updated)}


class DutProcess:
    def __init__(self, init, log_path, *, timeout_s=2., runtime=None):
        profile = hil_dut.validate_dut_profile(init['dut_profile']) if 'dut_profile' in init else None
        self.runtime=runtime
        self.gateway=bool(runtime and runtime.get('kind')=='hil_gateway')
        self.network=bool(runtime and runtime.get('kind') in ('network_reference','hil_gateway'))
        if self.gateway:
            command=hil_gateway.command(runtime,Path(__file__).parent)
            init=copy.deepcopy(init);init['gateway_runtime']=copy.deepcopy(runtime)
        elif self.network:
            command=hil_network.command(runtime,Path(__file__).parent)
            init=copy.deepcopy(init);init['network_runtime']=copy.deepcopy(runtime)
        else:
            command=[str(hil_policy.executable(runtime,Path(__file__).parent))] if runtime else [sys.executable,str(Path(hil_dut.__file__).resolve())]
        self.timeout_s = max(timeout_s,30.) if self.network else timeout_s
        self.buffer = b''
        self.log = Path(log_path).open('wb')
        self.process = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        try:
            self.initial, _ = self.exchange(init)
            if (self.initial.get('op') != 'init' or self.initial.get('status') != 'ready'
                    or (not self.gateway and self.initial.get('pid') != self.process.pid) or self.initial.get('dut_version') != (runtime['dut_version'] if runtime else hil_dut.VERSION)):
                raise ValueError('DUT initialization identity mismatch')
            if (profile is None and 'dut_profile' in self.initial
                    or profile is not None and self.initial.get('dut_profile') != profile):
                raise ValueError('DUT initialization profile mismatch')
            if profile is not None:
                hil_dut.validate_dut_profile(self.initial['dut_profile'])
        except BaseException:
            self.close(graceful=False)
            raise

    def exchange(self, message):
        raw = city_native.canonical(message) + b'\n'
        limit = hil_gateway.MAX_MESSAGE if self.gateway else hil_dut.MAX_MESSAGE
        if len(raw) > limit:
            raise ValueError('DUT request exceeds protocol bound')
        started = time.perf_counter_ns()
        deadline = time.monotonic() + self.timeout_s
        offset = 0
        while offset < len(raw):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.process.stdin], [], max(0, remaining))[1]:
                raise TimeoutError('DUT request timed out')
            offset += os.write(self.process.stdin.fileno(), raw[offset:])
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                raise TimeoutError('DUT reply timed out')
            data = os.read(self.process.stdout.fileno(), limit + 1)
            if not data:
                raise RuntimeError('DUT process exited without a complete reply')
            self.buffer += data
            if len(self.buffer) > limit:
                raise ValueError('DUT reply exceeds protocol bound')
        raw, self.buffer = self.buffer.split(b'\n', 1)
        reply = json.loads(raw)
        if not isinstance(reply, dict) or reply.get('schema_version') != 'hil-dut-reply/1':
            raise ValueError('Unsupported DUT response')
        return reply, (time.perf_counter_ns() - started) / 1e6

    def close(self, graceful=True):
        try:
            if graceful and self.process.poll() is None:
                reply, _ = self.exchange({'op': 'close'})
                if reply.get('status') != 'closed':
                    raise ValueError('DUT close acknowledgement missing')
                self.process.wait(timeout=2)
        finally:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.log):
                stream.close()
            if graceful and self.runtime:
                if self.gateway:hil_gateway.command(self.runtime,Path(__file__).parent)
                elif self.network:hil_network.verified_install(self.runtime,Path(__file__).parent)
                else:hil_policy.executable(self.runtime,Path(__file__).parent)


class HilBridge:
    def __init__(self, config, ego_spec, physics, out, *, lead_spec=None):
        if config.get('source_hashes') != source_hashes():
            raise ValueError('Native HIL sources changed after preview')
        fault, profile = side_contract(config)
        self.network=bool(config.get('runtime',{}).get('kind') in ('network_reference','hil_gateway'))
        self.gateway=bool(config.get('runtime',{}).get('kind')=='hil_gateway')
        self.scene_profile = hil_scene_profiles.validate_scene_profile(config['scene_profile']) if 'scene_profile' in config else None
        if self.scene_profile is not None and (not lead_spec or lead_spec['id'] == ego_spec['id']
                or lead_spec['controller']['kind'] != 'route_follow' or lead_spec['speed_mps'] != 2
                or lead_spec['controller'].get('stop_time_s') != 4):
            raise ValueError('Scene overlay requires the source-pinned route-follow lead')
        self.lead_id = lead_spec['id'] if self.scene_profile is not None else None
        self.ego_id = ego_spec['id']
        self.observation_route = copy.deepcopy(ego_spec['route'])
        self.injector = ObjectFault(fault)
        init = {'op': 'init', 'schema_version': 'hil-dut-init/1',
            'route': ego_spec['route'], 'target_speed_mps': ego_spec['speed_mps'], 'physics': physics}
        if profile is not None:
            init['dut_profile'] = profile
        self.dut = DutProcess(init, Path(out) / 'hil-dut.stderr.log',runtime=config.get('runtime'))
        self.trace = {'schema_version': TRACE_SCHEMA, 'execution_target': 'software_dut', 'side': config['side'],
            'object_source': 'carla_actor_ground_truth', 'fault_time_basis': 'simulation_time',
            'ipc_time_basis': 'parent_monotonic_wall_clock', 'sample_hz': 20,
            'max_object_age_s': profile['parameters']['max_object_age_ms'] / 1000 if profile else hil_dut.MAX_OBJECT_AGE_S,
            'presentation_time_basis':'response_time_s; input_time_s is one20Hz step earlier',
            'application_contract':'synchronous_vehicle_command_batch_ack_before_explicit_tick',
            'control_readback_source':'carla_client_actor_snapshot_after_response_tick',
            'recovery_fresh_steps': profile['parameters']['recovery_fresh_steps'] if profile else hil_dut.RECOVERY_STEPS,
            'fault': fault,
            'source_hashes': source_hashes(), 'dut_initialization': self.dut.initial, 'rows': [], 'frames': [], 'events': [],
            'status': 'running', 'physical_hardware_connected': False}
        if self.gateway:
            self.trace.update(execution_target='gateway',fault_time_basis='gateway_monotonic_wall_clock',
                physical_hardware_connected=config['runtime']['device']['target']=='hardware')
        if profile is not None:
            self.trace.update(comparison_kind=config.get('comparison_kind','same_fault_dut_ab'), dut_profile=profile)
        if config.get('runtime'):
            self.trace['runtime']=copy.deepcopy(config['runtime'])
            self.trace['fault_enabled']=config['fault_enabled']
        if self.scene_profile is not None:
            self.trace['scene_profile'] = copy.deepcopy(self.scene_profile)

    def lead_motion(self, time_s):
        return lead_motion(self.scene_profile, time_s)

    def step(self, sequence, current, input_frame):
        objects = [value for key, value in current.items() if key != self.ego_id]
        packet, injection = self.injector.step(sequence, objects)
        request = {'op': 'step', 'schema_version': 'hil-dut-step/1', 'sequence': sequence,
                   'time_s': sequence * .05, 'ego': current[self.ego_id], 'object_packet': packet,
                   'object_delivery_present': injection['delivery_present']}
        self.trace['pending_request']=copy.deepcopy(request)
        reply, elapsed_ms = self.dut.exchange(request)
        if (reply.get('op') != 'step' or reply.get('sequence') != sequence
                or (not self.network and reply.get('object_sequence') != packet['sequence'])
                or (self.network and (type(reply.get('object_sequence')) is not int or not -1<=reply['object_sequence']<=sequence))):
            raise ValueError('DUT reply does not match the input sequence')
        control = reply.get('control', {})
        if (set(control) != {'throttle', 'brake', 'steer'} or any(not hil_dut.finite(v) for v in control.values())
                or not 0 <= control['throttle'] <= 1 or not 0 <= control['brake'] <= 1 or not -1 <= control['steer'] <= 1):
            raise ValueError('DUT command is invalid')
        self.trace['rows'].append({'sequence': sequence, 'time_s': sequence * .05, 'input_frame': input_frame,
            'generated_objects': copy.deepcopy(objects), 'request': copy.deepcopy(request), 'injection': injection,
            'object_sequence': reply['object_sequence'], 'object_age_s': reply['object_age_s'], 'dut_state': reply['state'],
            'ipc_wall_latency_ms': elapsed_ms, 'requested_control': copy.deepcopy(control), 'reply': copy.deepcopy(reply)})
        self.trace.pop('pending_request',None)
        plan=reply['plan']
        if self.gateway and not reply['bridge']['diagnostic_observed']:
            # CARLA bookkeeping needs route progress even when the ECU is reset.
            # This geometric observation never changes received Brake control or
            # fills in the absent ECU diagnostic in the retained reply.
            observation=city_control.project(current[self.ego_id],self.observation_route)
            plan=dict(progress_m=observation['progress_m'],steering_progress_m=observation['progress_m'],
                evidence_source='host_route_observation',ecu_diagnostic_observed=False)
            self.trace['rows'][-1]['host_route_observation']=copy.deepcopy(plan)
        return control, plan

    def apply_controls(self, carla, client, actors, commands):
        identities=list(commands)
        batch=[carla.command.ApplyVehicleControl(actors[key].id,carla.VehicleControl(**commands[key])) for key in identities]
        responses=client.apply_batch_sync(batch,False)
        acknowledgements=[{'actor_id':int(reply.actor_id),'error':reply.error or None} for reply in responses]
        row=self.trace['rows'][-1]
        row['command_batch']=[{'actor_id':actors[key].id,'id':key,'requested_control':copy.deepcopy(commands[key])} for key in identities]
        row['command_acknowledgements']=acknowledgements
        if (len(responses)!=len(identities) or any(reply.error or reply.actor_id!=actors[key].id for key,reply in zip(identities,responses))):
            raise RuntimeError('HIL vehicle command batch was not acknowledged: '+json.dumps(acknowledgements))

    def observed(self, ego, frame, applied_control, *, lead=None):
        row=self.trace['rows'][-1]
        if self.gateway:row['vehicle_observed_monotonic_ns']=time.monotonic_ns()
        # Save the observed cached value even if it fails correspondence. A
        # command acknowledgement and an actor snapshot are distinct evidence.
        row.update(next_ego=copy.deepcopy(ego), response_frame=frame, applied_control=copy.deepcopy(applied_control))
        if any(abs(applied_control[k]-row['requested_control'][k])>1e-5 for k in ('throttle','brake','steer')):
            raise ValueError('Native control readback differs from acknowledged DUT command: '+json.dumps(
                {'sequence':row['sequence'],'requested':row['requested_control'],'readback':applied_control}))
        dut_lead=row['reply']['plan'].get('lead')
        response_time=(row['sequence']+1)*.05
        compact={'time_s':response_time, 'input_time_s':row['time_s'], 'sequence':row['sequence'], 'input_age_ms':row['object_age_s']*1000 if row['object_age_s'] is not None else None,
            'dut_state':row['dut_state'], 'ego_speed_mps':ego['speed_mps'], 'lead_gap_m':dut_lead['origin_gap_m'] if dut_lead else None,
            'command':copy.deepcopy(applied_control), 'ipc_wall_latency_ms':row['ipc_wall_latency_ms'],
            'object_sequence':row['object_sequence'], 'response_frame':frame}
        previous=self.trace['rows'][-2] if len(self.trace['rows'])>1 else None
        for changed,kind in ((row['injection']['active'] != bool(previous and previous['injection']['active']),
                              'fault_started' if row['injection']['active'] else 'fault_ended'),
                             (not previous or row['dut_state']!=previous['dut_state'], 'dut_'+row['dut_state'])):
            if changed:self.trace['events'].append({'time_s':response_time,'input_time_s':row['time_s'],'type':kind,'label':kind})
        if self.scene_profile is not None:
            if lead is None or lead['id'] != self.lead_id:
                raise ValueError('Scene overlay response is missing its lead actor')
            row['next_lead'] = copy.deepcopy(lead)
            fields, events = lead_presentation(self.scene_profile, row, lead,
                self.trace['frames'][-1] if self.trace['frames'] else None, self.trace['events'])
            compact.update(fields)
            self.trace['events'].extend(events)
        self.trace['frames'].append(compact)


def capture_model_loop(payload, output, carla, client, lease, args):
    # A bounded model loop retains many acyclic JSON telemetry objects. Defer
    # cyclic collection until capture ends; reference-count cleanup still runs.
    # This reduces host pauses, but never relaxes the recorded timing budget.
    import gc
    enabled=gc.isenabled()
    coupled=bool(payload.get('hil',{}).get('runtime',{}).get('model_input'))
    if coupled:gc.collect();gc.disable()
    try:
        result=city_native.capture(payload,output,carla,client,lease,args)
        if coupled:result['model_loop_host_collection']='cyclic_gc_deferred_during_bounded_capture'
        return result
    finally:
        if coupled and enabled:gc.enable()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    for key in ('owner', 'spool'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    carla, client = city_native.connect(args.port, args.owner)
    if args.probe:
        print(json.dumps({**city_native.probe(client), 'hil_source_hashes': source_hashes()}))
        return
    if not args.output:
        raise ValueError('HIL output directory required')
    raw = sys.stdin.buffer.read(64 * 1024**2 + 1)
    if len(raw) > 64 * 1024**2:
        raise ValueError('HIL input exceeds bound')
    payload = json.loads(raw)
    if 'hil' not in payload:
        raise ValueError('HIL launcher requires an explicit HIL configuration')
    args.autoware_command = None
    with city_native.lease(args.spool, Path(args.output).name) as lease:
        result = capture_model_loop(payload, args.output, carla, client, lease, args)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    def cancel(*_):
        city_native.CANCELLED = True
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    main()
