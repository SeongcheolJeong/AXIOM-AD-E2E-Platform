"""Executable SA8650 reference behavior, deliberately independent of silicon RTL.

One SimPy clock contains every sender, line arbitration and ECU process. Public
part identities inform the block layout; every rate/timing below is a saved
assumption, not a measured or guaranteed property of a Qualcomm controller.
"""
import csv
from collections import deque
import random
from pathlib import Path

import simpy

from ade2e.ecu_lab_schemas import ADAPTER, FIDELITY, EcuCommand, EcuFault, validate_runtime
from ade2e.fpga_lab_schemas import TimingConfig
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.service import canonical_bytes


def simulate(profile_config, channels, commands, faults, seed=42, should_cancel=lambda: False, *, receiver_factory=None):
    """Finite, deterministic line pipeline with frame-level observations.

    Receiver queues count waiting complete frames, excluding the active processor.
    Input deadlines start at the nominal source time; processing deadlines start
    at complete CSI reception. All queues drain after the finite replay. A late
    frame can complete, but its deadline failure remains in the result.
    """
    p = validate_runtime(profile_config, channels, commands, faults)
    commands = [EcuCommand.model_validate(c).model_dump() for c in commands]
    faults = [EcuFault.model_validate(f).model_dump() for f in faults]
    ids = sorted(c['channel'] for c in channels)
    if not ids or len(ids) > 4 or len(ids) != len(set(ids)) or len({c['vc'] for c in channels}) != len(ids):
        raise ValueError('One to four unique channels and VCs are required')
    if any(c['channel'] not in range(4) or c['vc'] not in range(4) for c in channels):
        raise ValueError('Channel and VC must be between zero and three')
    if len(commands) > 32 or len(faults) > 32 or len({c['at_us'] for c in commands}) != len(commands):
        raise ValueError('Command/fault budget or unique command timestamps exceeded')
    for i, a in enumerate(faults):
        if a['channel'] is not None and a['channel'] not in ids:
            raise ValueError('Fault references an inactive channel')
        for b in faults[i + 1:]:
            if a['kind'] == b['kind'] and a['channel'] == b['channel'] and max(a['at_us'], b['at_us']) < min(a['at_us'] + a['duration_us'], b['at_us'] + b['duration_us']):
                raise ValueError('Overlapping faults of the same kind and scope are unsupported')
    env = simpy.Environment()
    receiver = receiver_factory(env) if receiver_factory else None
    events, frames = [], []
    states = {}
    controls = dict(configured=False, started=False, generation=0, boot_until=p.boot_us * 1000,
                    processor_stalled=False, heartbeat_ok=True)
    csi_wake, processor_wake, processor_change = env.event(), env.event(), env.event()
    operations = 0

    def checked():
        nonlocal operations
        operations += 1
        if operations % 1024 == 1 and should_cancel():
            from ade2e.adapters import AdapterCancelledBeforeDispatch
            raise AdapterCancelledBeforeDispatch('ECU behavioral calculation cancelled')

    def global_state():
        if not controls['heartbeat_ok']:
            return 'degraded'
        if not controls['configured']:
            return 'booting' if env.now < controls['boot_until'] else 'unconfigured'
        if not controls['started']:
            return 'stopped'
        values = {s['state'] for s in states.values()}
        return 'degraded' if 'degraded' in values else 'locking' if 'locking' in values else 'recovering' if 'recovering' in values else 'normal'

    def record(kind, channel=None, frame=None, line=None, cause=None, **details):
        checked()
        if len(events) >= 1000000:
            raise ValueError('Event budget exhausted; execution failed without truncation')
        s = states.get(channel)
        e = dict(id=len(events), time_ns=int(env.now), kind=kind, channel=channel,
                 frame=frame, line=line, cause_event_id=cause,
                 des_bytes=s['des_used'] if s else sum(x['des_used'] for x in states.values()),
                 receive_queue_frames=len(s['receive']) if s else sum(len(x['receive']) for x in states.values()),
                 processing_queue_frames=sum(len(x['receive']) for x in states.values()),
                 queue_depths={str(k): len(v['receive']) for k, v in states.items()},
                 state=s['state'] if s else global_state(), link_locked=s['locked'] if s else None,
                 **details)
        events.append(e)
        return e['id']

    def health(channel, value, cause=None):
        s = states[channel]
        if value != s['state']:
            s['state'] = value
            record('health_' + value, channel, cause=cause)

    def degrade(channel, cause=None):
        states[channel]['good'] = 0
        health(channel, 'degraded', cause)

    def lose(f, reason, cause=None):
        if f['completed_ns'] is not None or f['loss_reason'] is not None:
            return
        f['loss_reason'] = reason
        f['status'] = 'dropped'
        f['cause_event_id'] = record(reason, f['channel'], f['frame'], cause=cause)
        degrade(f['channel'], f['cause_event_id'])

    def wake_csi():
        nonlocal csi_wake
        old, csi_wake = csi_wake, env.event()
        if not old.triggered:
            old.succeed()

    def wake_processor():
        nonlocal processor_wake
        old, processor_wake = processor_wake, env.event()
        if not old.triggered:
            old.succeed()

    def change_processor():
        nonlocal processor_change
        old, processor_change = processor_change, env.event()
        if not old.triggered:
            old.succeed()

    def duration(size, bytes_per_second):
        return (size * 1000000000 + bytes_per_second - 1) // bytes_per_second

    for channel in sorted(channels, key=lambda value: value['channel']):
        ch = channel['channel']
        c = TimingConfig.model_validate(channel['design']['config'])
        offset = channel.get('source_offset_us', 120000)
        duplicate = channel.get('duplicate_every', 0)
        if type(offset) is not int or not 0 <= offset <= 1000000 or type(duplicate) is not int or not 0 <= duplicate <= 120:
            raise ValueError('Invalid source offset or duplicate interval')
        s = states[ch] = dict(config=c, channel=channel, ready=[env.event() for _ in range(c.frames)],
            fifo=simpy.Container(env, capacity=c.fifo_bytes, init=c.fifo_bytes), lines=simpy.Store(env),
            writer=simpy.Resource(env), ddr=c.preload_frames*c.frame_bytes, fifo_used=0,
            peak_ddr=c.preload_frames*c.frame_bytes, peak_fifo=0, des=deque(), des_used=0,
            peak_des=0, receive=deque(), peak_receive=0, locked=False, physical_link=True,
            state='booting', good=0, seen_ids=set(), frames=[], link_epoch=0,
            last_frame_id=-1, missing_frame_ids=set())
        rng = random.Random(f"ecu-lab/1:{seed}:{channel['design'].get('seed', 42)}:{ch}")
        previous_trigger = 0
        for i in range(c.frames):
            source = offset * 1000 + i * 1000000000 // c.fps
            trigger = max(previous_trigger, source + c.start_delay_us * 1000 + rng.randint(-c.jitter_us, c.jitter_us) * 1000, 0)
            previous_trigger = trigger
            wire_id = s['frames'][-1]['frame_id'] if i and duplicate and (i+1) % duplicate == 0 else i
            f = dict(channel=ch, frame=i, frame_id=wire_id,
                source_ns=source, expected_ns=offset*1000+i*1000000000//p.fps,
                trigger_ns=trigger, supplied_ns=None, tx_start_ns=None, tx_end_ns=None,
                csi_start_ns=None, csi_end_ns=None, received_ns=None, processing_start_ns=None,
                completed_ns=None, status='pending', loss_reason=None, cause_event_id=None,
                input_deadline_exceeded=False, processing_deadline_exceeded=False,
                latency_ns=None, underflow=False, lines_received=0, ingress_lines=0,
                ingress_complete_ns=None, generation=None, link_epoch=None)
            s['frames'].append(f)
            frames.append(f)

    def boot(generation):
        yield env.timeout(max(0, controls['boot_until'] - env.now))
        if generation == controls['generation']:
            record('boot_ready')
            if not controls['configured']:
                for ch in ids:
                    health(ch, 'unconfigured')

    def lock(ch, generation, link_epoch):
        s = states[ch]
        yield env.timeout(p.link_lock_us * 1000)
        if generation == controls['generation'] and link_epoch == s['link_epoch'] and controls['configured'] and s['physical_link']:
            s['locked'] = True
            event = record('link_locked', ch)
            health(ch, 'recovering' if controls['started'] else 'stopped', event)

    def command(item):
        yield env.timeout(item['at_us'] * 1000)
        name = item['command']
        cause = record('command_' + name)
        if name == 'configure':
            if env.now < controls['boot_until']:
                record('command_rejected', cause=cause, reason='boot_not_ready')
                return
            controls['configured'] = True
            for ch in ids:
                states[ch]['locked'] = False
                states[ch]['good'] = 0
                states[ch]['link_epoch'] += 1
                health(ch, 'locking', cause)
                env.process(lock(ch, controls['generation'], states[ch]['link_epoch']))
        elif name == 'start':
            if not controls['configured']:
                record('command_rejected', cause=cause, reason='not_configured')
                return
            controls['started'] = True
            for ch in ids:
                states[ch]['good'] = 0
                health(ch, 'recovering' if states[ch]['locked'] else 'locking', cause)
        elif name == 'stop':
            controls['started'] = False
            for ch in ids:
                for f in states[ch]['frames']:
                    if f['tx_start_ns'] is not None and f['received_ns'] is None:
                        lose(f, 'stopped_mid_frame', cause)
                health(ch, 'stopped', cause)
        else:
            controls['generation'] += 1
            controls['configured'] = controls['started'] = False
            controls['boot_until'] = env.now + p.boot_us * 1000
            for ch in ids:
                s = states[ch]
                for f in s['frames']:
                    if f['tx_start_ns'] is not None and f['completed_ns'] is None:
                        lose(f, 'reset_discard', cause)
                s['receive'].clear()
                s['locked'], s['good'] = False, 0
                s['link_epoch'] += 1
                s['seen_ids'].clear()
                s['last_frame_id'] = -1
                health(ch, 'booting', cause)
            change_processor()
            env.process(boot(controls['generation']))

    def heartbeat_failure(item, cause):
        at = item['at_us'] * 1000
        end = (item['at_us'] + item['duration_us']) * 1000
        period = p.heartbeat_period_us * 1000
        heartbeat_losses = sorted((f['at_us']*1000, (f['at_us']+f['duration_us'])*1000) for f in faults if f['kind'] == 'heartbeat_loss')
        last_tick = max(0, (at - 1) // period * period)
        for begin, finish in reversed(heartbeat_losses):
            if begin <= last_tick < finish:
                last_tick = max(0, (begin-1)//period*period)
        timeout_at = max(at, last_tick + p.heartbeat_timeout_us * 1000)
        resume_at = ((end + period - 1) // period) * period
        for begin, finish in heartbeat_losses:
            if begin <= resume_at < finish:
                resume_at = ((finish+period-1)//period)*period
        if timeout_at < resume_at:
            yield env.timeout(timeout_at - env.now)
            if controls['heartbeat_ok']:
                controls['heartbeat_ok'] = False
                record('heartbeat_timeout', cause=cause)
            yield env.timeout(resume_at - env.now)
            if not controls['heartbeat_ok']:
                controls['heartbeat_ok'] = True
                record('heartbeat_recovered', cause=cause)

    def fault(item):
        yield env.timeout(item['at_us'] * 1000)
        ch, kind = item['channel'], item['kind']
        cause = record('fault_' + kind, ch)
        if kind == 'link_down':
            s = states[ch]
            s['physical_link'] = s['locked'] = False
            s['link_epoch'] += 1
            degrade(ch, cause)
            for f in s['frames']:
                if f['tx_start_ns'] is not None and f['ingress_complete_ns'] is None:
                    lose(f, 'link_loss_partial', cause)
        elif kind == 'processor_stall':
            controls['processor_stalled'] = True
            change_processor()
        else:
            env.process(heartbeat_failure(item, cause))
        yield env.timeout(item['duration_us'] * 1000)
        record('fault_cleared_' + kind, ch, cause=cause)
        if kind == 'link_down':
            s['physical_link'] = True
            if controls['configured']:
                health(ch, 'locking', cause)
                env.process(lock(ch, controls['generation'], s['link_epoch']))
        elif kind == 'processor_stall':
            controls['processor_stalled'] = False
            change_processor()

    def source(ch, i):
        s, f = states[ch], states[ch]['frames'][i]
        c = s['config']
        yield env.timeout(0 if i < c.preload_frames else f['source_ns'] + c.supply_delay_us*1000)
        if c.drop_every and (i+1) % c.drop_every == 0:
            if i < c.preload_frames:
                s['ddr'] -= c.frame_bytes
            lose(f, 'injected_drop')
            s['ready'][i].succeed(False)
            return
        if i < c.preload_frames:
            f['supplied_ns'] = 0
            record('preloaded', ch, i)
            s['ready'][i].succeed(True)
            return
        if s['ddr'] + c.frame_bytes > c.ddr_bytes:
            lose(f, 'ddr_overflow')
            s['ready'][i].succeed(False)
            return
        s['ddr'] += c.frame_bytes
        s['peak_ddr'] = max(s['peak_ddr'], s['ddr'])
        admission = record('ddr_admit', ch, i)
        with s['writer'].request() as request:
            yield request
            yield env.timeout(duration(c.frame_bytes, c.write_MBps*1000000))
        f['supplied_ns'] = int(env.now)
        record('ddr_ready', ch, i, cause=admission)
        s['ready'][i].succeed(True)

    def reader(ch):
        s, c = states[ch], states[ch]['config']
        for i, f in enumerate(s['frames']):
            yield env.timeout(max(0, f['trigger_ns'] - env.now))
            record('trigger', ch, i)
            if not s['ready'][i].triggered:
                f['underflow'] = True
                record('source_underflow', ch, i)
            if not (yield s['ready'][i]):
                continue
            for line in range(c.height):
                checked()
                yield s['fifo'].get(c.line_bytes)
                yield env.timeout(duration(c.line_bytes, c.read_MBps*1000000))
                s['ddr'] -= c.line_bytes
                s['fifo_used'] += c.line_bytes
                s['peak_fifo'] = max(s['peak_fifo'], s['fifo_used'])
                yield s['lines'].put((i, line))
        yield s['lines'].put(None)

    def ingress(ch, i, line, cause):
        s, f = states[ch], states[ch]['frames'][i]
        c = s['config']
        yield env.timeout(c.propagation_us*1000)
        if f['loss_reason']:
            return
        if not controls['started'] or not controls['configured'] or not s['locked']:
            lose(f, 'ecu_not_ready' if s['physical_link'] else 'link_unavailable', cause)
            return
        if line == 0:
            if f['frame_id'] in s['seen_ids']:
                lose(f, 'duplicate_frame_id', cause)
                return
            if f['frame_id'] > s['last_frame_id']+1:
                missing = list(range(s['last_frame_id']+1, f['frame_id']))
                s['missing_frame_ids'].update(missing)
                gap = record('frame_id_gap', ch, i, cause=cause, missing_frame_ids=missing)
                degrade(ch, gap)
            s['seen_ids'].add(f['frame_id'])
            s['last_frame_id'] = f['frame_id']
            f['generation'] = controls['generation']
            f['link_epoch'] = s['link_epoch']
            record('des_frame_start', ch, i, cause=cause)
        if f['generation'] != controls['generation']:
            lose(f, 'reset_discard', cause)
            return
        if s['des_used'] + c.line_bytes > p.des_buffer_bytes:
            lose(f, 'des_overflow', cause)
            return
        s['des_used'] += c.line_bytes
        f['ingress_lines'] += 1
        if line == c.height-1:
            f['ingress_complete_ns'] = int(env.now)
        s['peak_des'] = max(s['peak_des'], s['des_used'])
        s['des'].append((f, line, cause))
        wake_csi()

    def transmitter(ch):
        s, c = states[ch], states[ch]['config']
        last = -1
        while True:
            empty_since = env.now if not s['lines'].items else None
            item = yield s['lines'].get()
            if item is None:
                return
            i, line = item
            f = s['frames'][i]
            if empty_since is not None and env.now > empty_since and i == last and not f['underflow']:
                f['underflow'] = True
                record('fifo_underflow', ch, i, line)
            last = i
            cause = f['cause_event_id']
            if line == 0:
                f['tx_start_ns'] = int(env.now)
                cause = record('tx_start', ch, i)
            yield env.timeout(duration(c.line_bytes+c.overhead_bytes, c.link_Mbps*1000000//8))
            s['fifo_used'] -= c.line_bytes
            yield s['fifo'].put(c.line_bytes)
            if line == c.height-1:
                f['tx_end_ns'] = int(env.now)
                cause = record('tx_end', ch, i)
            env.process(ingress(ch, i, line, cause))

    def choose_queue(key, last):
        available = [ch for ch in ids if states[ch][key]]
        return next((ch for ch in available if ch > last), available[0]) if available else None

    def csi():
        last = -1
        while True:
            if not any(s['des'] for s in states.values()):
                yield csi_wake
            # Resolve all simultaneous arrivals before applying channel-ID ties.
            yield env.timeout(0)
            ch = choose_queue('des', last)
            if ch is None:
                continue
            last = ch
            s, c = states[ch], states[ch]['config']
            f, line, cause = s['des'].popleft()
            if f['loss_reason']:
                s['des_used'] -= c.line_bytes
                continue
            if f['csi_start_ns'] is None:
                f['csi_start_ns'] = int(env.now)
                cause = record('csi_start', ch, f['frame'], cause=cause)
            # Integer ceiling avoids floating point drift in shared bandwidth.
            numerator = (c.line_bytes+c.overhead_bytes)*8*1000000000*100
            denominator = p.csi_lanes*p.csi_lane_Mbps*1000000*p.csi_efficiency_percent
            yield env.timeout((numerator+denominator-1)//denominator)
            s['des_used'] -= c.line_bytes
            if f['loss_reason']:
                continue
            f['lines_received'] += 1
            if line != c.height-1:
                continue
            if f['lines_received'] != c.height:
                lose(f, 'incomplete_frame', cause)
                continue
            f['csi_end_ns'] = f['received_ns'] = int(env.now)
            cause = record('frame_received', ch, f['frame'], cause=cause)
            if receiver is not None:
                receiver(f)
                continue
            if len(s['receive']) >= p.receive_queue_frames:
                lose(f, 'receive_queue_full', cause)
                continue
            f['status'] = 'queued'
            f['cause_event_id'] = cause
            s['receive'].append(f)
            s['peak_receive'] = max(s['peak_receive'], len(s['receive']))
            record('receive_enqueued', ch, f['frame'], cause=cause)
            wake_processor()
            env.process(processing_deadline(f))

    def processing_deadline(f):
        yield env.timeout(p.processing_deadline_us*1000+1)
        if f['completed_ns'] is None and f['loss_reason'] is None:
            f['processing_deadline_exceeded'] = True
            cause = record('processing_deadline', f['channel'], f['frame'], cause=f['cause_event_id'])
            degrade(f['channel'], cause)

    def processor():
        last = -1
        while True:
            if not any(s['receive'] for s in states.values()):
                yield processor_wake
            yield env.timeout(0)
            ch = choose_queue('receive', last)
            if ch is None:
                continue
            last = ch
            f = states[ch]['receive'].popleft()
            if f['loss_reason']:
                continue
            f['processing_start_ns'] = int(env.now)
            cause = record('processing_start', ch, f['frame'], cause=f['cause_event_id'])
            remaining = p.processing_us*1000
            generation = controls['generation']
            while remaining > 0 and generation == controls['generation']:
                checked()
                if controls['processor_stalled']:
                    yield processor_change
                    continue
                start = env.now
                yield env.timeout(remaining) | processor_change
                remaining -= int(env.now-start)
            if f['loss_reason'] or generation != controls['generation']:
                continue
            f['completed_ns'] = int(env.now)
            f['latency_ns'] = int(env.now)-f['expected_ns']
            f['processing_deadline_exceeded'] |= env.now-f['received_ns'] > p.processing_deadline_us*1000
            failed_deadline = f['input_deadline_exceeded'] or f['processing_deadline_exceeded']
            f['status'] = 'deadline_exceeded' if failed_deadline else 'completed'
            f['cause_event_id'] = record('processing_complete', ch, f['frame'], cause=cause)
            if failed_deadline:
                degrade(ch, f['cause_event_id'])
            elif controls['started'] and states[ch]['locked'] and f['link_epoch'] == states[ch]['link_epoch']:
                states[ch]['good'] += 1
                health(ch, 'normal' if states[ch]['good'] >= p.recovery_good_frames else 'recovering', f['cause_event_id'])

    def input_deadline(f):
        yield env.timeout(f['expected_ns']+p.input_timeout_us*1000+1)
        if f['received_ns'] is None or f['received_ns'] > f['expected_ns']+p.input_timeout_us*1000:
            f['input_deadline_exceeded'] = True
            cause = record('input_timeout', f['channel'], f['frame'], cause=f['cause_event_id'])
            if f['completed_ns'] is not None:
                f['status'] = 'deadline_exceeded'
            degrade(f['channel'], cause)

    record('power_on')
    env.process(boot(0))
    for item in commands:
        env.process(command(item))
    for item in faults:
        env.process(fault(item))
    for ch in ids:
        for i, f in enumerate(states[ch]['frames']):
            env.process(source(ch, i))
            env.process(input_deadline(f))
        env.process(reader(ch))
        env.process(transmitter(ch))
    env.process(csi())
    if receiver is None:
        env.process(processor())
    env.run()
    if receiver is not None:
        return dict(frames=frames, events=events, channels=channels, profile_config=p.model_dump())
    for f in frames:
        if f['completed_ns'] is None and f['loss_reason'] is None:
            lose(f, 'missing_timeout')
    per_channel = []
    for ch in ids:
        s, subset = states[ch], states[ch]['frames']
        per_channel.append(dict(channel=ch, vc=s['channel']['vc'], total_frames=len(subset),
            received=sum(f['received_ns'] is not None for f in subset), completed=sum(f['completed_ns'] is not None for f in subset),
            dropped=sum(f['loss_reason'] is not None for f in subset),
            deadline_exceeded=sum(f['input_deadline_exceeded'] or f['processing_deadline_exceeded'] for f in subset),
            missing_frame_ids=sorted(s['missing_frame_ids']), duplicate_frames=sum(f['loss_reason']=='duplicate_frame_id' for f in subset),
            peak_des_bytes=s['peak_des'], peak_receive_queue_frames=s['peak_receive'],
            peak_ddr_bytes=s['peak_ddr'], peak_fifo_bytes=s['peak_fifo'], final_state=s['state'], link_locked=s['locked']))
    summary = dict(schema_version='ecu-lab-result/1', fidelity=FIDELITY, total_frames=len(frames),
        received=sum(x['received'] for x in per_channel), completed=sum(x['completed'] for x in per_channel),
        dropped=sum(x['dropped'] for x in per_channel), deadline_exceeded=sum(x['deadline_exceeded'] for x in per_channel),
        max_latency_ns=max((f['latency_ns'] for f in frames if f['latency_ns'] is not None), default=None),
        event_count=len(events), channels=per_channel, final_state=global_state(),
        peak_receive_queue_frames=max((x['peak_receive_queue_frames'] for x in per_channel), default=0),
        csi_effective_Mbps=p.csi_lanes*p.csi_lane_Mbps*p.csi_efficiency_percent/100,
        heartbeat_ok=controls['heartbeat_ok'])
    return dict(schema_version='ecu-lab-trace/1', profile_config=p.model_dump(), channels=channels,
                commands=commands, faults=faults, seed=seed, summary=summary, frames=frames, events=events)


class EcuTimingHandler:
    def __init__(self, database=None):
        self.database = database

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version='0.1.0', kinds=['ecu_timing'],
            status='available', fidelity=FIDELITY, cancellation_supported=True,
            description='Shared-clock SimPy SA8650 reference behavior; no silicon, RTL, PHY or physical ECU validation')

    def prepare(self, spec):
        from ade2e.ecu_lab import resolve_test, hydrate_test
        if set(spec.input) != {'test_ref'} or spec.comparison_kind != 'single':
            raise ValueError('ECU timing requires one saved test_ref')
        if any(value is not None for value in (spec.model, spec.sensor, spec.evaluator)):
            raise ValueError('External models, sensors and evaluators are unsupported')
        with self.database.read_session() as session:
            test = resolve_test(session, spec.project_id, spec.input['test_ref'])
            snapshot = hydrate_test(session, test)
        if spec.seed != test['seed']:
            raise ValueError('Seed must match the saved test revision')
        return snapshot

    def execute(self, snapshot, work_dir: Path, should_cancel):
        test, profile = snapshot['test'], snapshot['profile']
        result = simulate(profile['config'], snapshot['channels'], test['commands'], test['faults'], test['seed'], should_cancel)
        result['test_ref'] = {k: test[k] for k in ('id', 'revision', 'sha256')}
        result['profile_ref'] = {k: profile[k] for k in ('id', 'revision', 'sha256')}
        result['test'], result['profile'] = test, profile
        result['adapter_version'] = self.metadata().version
        result['summary'].update(test_ref=result['test_ref'], profile_ref=result['profile_ref'], adapter_version=self.metadata().version)
        artifacts = []
        for name, value in [('ecu-trace.json', result), ('summary.json', result['summary'])]:
            path = work_dir / name
            path.write_bytes(canonical_bytes(value))
            artifacts.append(ExperimentOutputFile(path, name, 'application/json'))
        path = work_dir / 'frames.csv'
        with path.open('w', newline='') as stream:
            fields = sorted({k for f in result['frames'] for k in f})
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(result['frames'])
        artifacts.append(ExperimentOutputFile(path, path.name, 'text/csv'))
        return ExperimentExecution(summary=result['summary'], artifacts=artifacts, fidelity=FIDELITY)
