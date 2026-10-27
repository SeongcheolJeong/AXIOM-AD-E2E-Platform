"""SimPy is intentionally imported only by this optional integration."""
import csv
import random
from pathlib import Path
import simpy
from ade2e.fpga_lab_schemas import ADAPTER, FIDELITY, TimingConfig
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.service import canonical_bytes


def simulate(config, seed=42, should_cancel=lambda: False):
    """Whole-frame DDR admission, line FIFO, serialized link, independent propagation.

    DDR space is reserved on admission and released as lines finish reading. Read
    and write ports are independent declared rates, not a DRAM controller model.
    FIFO occupancy includes the currently transmitting payload until its completion.
    Finite source drains completely; missing frames are observed through timeout.
    """
    c = TimingConfig.model_validate(config)
    env = simpy.Environment()
    rng = random.Random(seed)
    fifo = simpy.Container(env, capacity=c.fifo_bytes, init=c.fifo_bytes)
    lines = simpy.Store(env)
    writer = simpy.Resource(env, capacity=1)
    ready = [env.event() for _ in range(c.frames)]
    trace, frames = [], []
    ddr = c.preload_frames * c.frame_bytes
    fifo_used = 0
    peak_ddr, peak_fifo = ddr, 0
    def record(kind, frame, line=None, cause=None):
        nonlocal peak_ddr, peak_fifo
        if len(trace) >= 1000000:
            raise ValueError('Event budget exhausted; execution failed without truncation')
        if len(trace) % 1024 == 0 and should_cancel():
            from ade2e.adapters import AdapterCancelledBeforeDispatch
            raise AdapterCancelledBeforeDispatch('FPGA timing calculation cancelled')
        peak_ddr, peak_fifo = max(peak_ddr, ddr), max(peak_fifo, fifo_used)
        event = dict(id=len(trace), time_ns=int(env.now), kind=kind, frame=frame,
                     line=line, cause_event_id=cause, ddr_bytes=ddr, fifo_bytes=fifo_used)
        trace.append(event)
        return event['id']
    def duration(size, bytes_per_second):
        return (size * 1000000000 + bytes_per_second - 1) // bytes_per_second
    previous_trigger = 0
    for i in range(c.frames):
        source_time = i * 1000000000 // c.fps
        trigger = max(previous_trigger, source_time + c.start_delay_us * 1000 + rng.randint(-c.jitter_us, c.jitter_us) * 1000, 0)
        previous_trigger = trigger
        frames.append(dict(frame=i, source_ns=source_time, expected_ns=i * 1000000000 // c.expected_fps,
                           trigger_ns=trigger, supplied_ns=None, tx_start_ns=None, tx_end_ns=None,
                           arrival_ns=None, underflow=False, loss_reason=None, cause_event_id=None))
    def source(i):
        nonlocal ddr
        f = frames[i]
        yield env.timeout(0 if i < c.preload_frames else f['source_ns'] + c.supply_delay_us * 1000)
        if c.drop_every and (i + 1) % c.drop_every == 0:
            if i < c.preload_frames:
                ddr -= c.frame_bytes
            f['loss_reason'] = 'injected_drop'
            f['cause_event_id'] = record('injected_drop', i)
            ready[i].succeed(False)
            return
        if i < c.preload_frames:
            f['supplied_ns'] = 0
            record('preloaded', i)
            ready[i].succeed(True)
            return
        if ddr + c.frame_bytes > c.ddr_bytes:
            f['loss_reason'] = 'ddr_overflow'
            f['cause_event_id'] = record('ddr_overflow', i)
            ready[i].succeed(False)
            return
        ddr += c.frame_bytes
        admission = record('ddr_admit', i)
        with writer.request() as request:
            yield request
            yield env.timeout(duration(c.frame_bytes, c.write_MBps * 1000000))
        f['supplied_ns'] = int(env.now)
        record('ddr_ready', i, cause=admission)
        ready[i].succeed(True)
    def trigger(i):
        yield env.timeout(frames[i]['trigger_ns'])
        record('trigger', i)
    def reader():
        nonlocal ddr, fifo_used
        for i, f in enumerate(frames):
            yield env.timeout(max(0, f['trigger_ns'] - env.now))
            if not ready[i].triggered:
                f['underflow'] = True
                f['cause_event_id'] = record('source_underflow', i)
            admitted = yield ready[i]
            if not admitted:
                continue
            for line in range(c.height):
                yield fifo.get(c.line_bytes)
                yield env.timeout(duration(c.line_bytes, c.read_MBps * 1000000))
                ddr -= c.line_bytes
                fifo_used += c.line_bytes
                event = record('fifo_ready', i, line)
                yield lines.put((i, line, event))
        yield lines.put(None)
    def receive(i, cause):
        yield env.timeout(c.propagation_us * 1000)
        frames[i]['arrival_ns'] = int(env.now)
        record('ecu_arrival', i, cause=cause)
    def transmitter():
        nonlocal fifo_used
        last_frame = -1
        while True:
            # A gap within the same frame is a FIFO starvation interval.
            empty_since = env.now if not lines.items else None
            item = yield lines.get()
            if item is None:
                break
            i, line, cause = item
            if empty_since is not None and env.now > empty_since and i == last_frame:
                frames[i]['underflow'] = True
                cause = record('fifo_underflow', i, line, cause)
                frames[i]['cause_event_id'] = cause
            last_frame = i
            if line == 0:
                frames[i]['tx_start_ns'] = int(env.now)
            record('tx_start', i, line, cause)
            yield env.timeout(duration(c.line_bytes + c.overhead_bytes, c.link_Mbps * 1000000 // 8))
            fifo_used -= c.line_bytes
            event = record('tx_end', i, line, cause)
            yield fifo.put(c.line_bytes)
            if line == c.height - 1:
                frames[i]['tx_end_ns'] = int(env.now)
                env.process(receive(i, event))
    def timeout(i):
        f = frames[i]
        yield env.timeout(f['expected_ns'] + c.timeout_us * 1000)
        # Resolve same-time receive events before declaring timeout.
        yield env.timeout(0)
        if f['arrival_ns'] is None or f['arrival_ns'] > f['expected_ns'] + c.timeout_us * 1000:
            record('ecu_timeout', i, cause=f['cause_event_id'])
    for i in range(c.frames):
        env.process(source(i)); env.process(trigger(i)); env.process(timeout(i))
    env.process(reader()); env.process(transmitter())
    env.run()
    for f in frames:
        f['latency_ns'] = None if f['arrival_ns'] is None else f['arrival_ns'] - f['expected_ns']
        f['status'] = ('missing_timeout' if f['arrival_ns'] is None else
                       'late_timeout' if f['latency_ns'] > c.timeout_us * 1000 else
                       'deadline_exceeded' if f['latency_ns'] > c.deadline_us * 1000 else 'received')
        if f['arrival_ns'] is not None:
            f['buffer_wait_ns'] = f['tx_start_ns'] - f['supplied_ns']
    summary = dict(schema_version='fpga-lab-result/1', fidelity=FIDELITY,
                   frame_bytes=c.frame_bytes, payload_bytes_per_second=c.frame_bytes * c.fps,
                   frames=c.frames, received=sum(f['arrival_ns'] is not None for f in frames),
                   missing=sum(f['arrival_ns'] is None for f in frames),
                   deadline_exceeded=sum(f['status'] in ('deadline_exceeded', 'late_timeout') for f in frames),
                   underflow_frames=sum(f['underflow'] for f in frames),
                   overflow_frames=sum(f['loss_reason'] == 'ddr_overflow' for f in frames),
                   max_latency_ns=max((f['latency_ns'] for f in frames if f['latency_ns'] is not None), default=None),
                   peak_ddr_bytes=peak_ddr, peak_fifo_bytes=peak_fifo, event_count=len(trace))
    return dict(schema_version='fpga-lab-trace/1', config=c.model_dump(), seed=seed,
                summary=summary, frames=frames, events=trace)


class TimingHandler:
    def __init__(self, database=None):
        self.database = database

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version='0.1.0', kinds=['fpga_timing'],
            status='available', fidelity=FIDELITY, cancellation_supported=True,
            description='SimPy 4.1.1 line-level behavioral timing; no RTL, PHY or hardware validation')

    def prepare(self, spec):
        from ade2e.fpga_lab import resolve_design
        if set(spec.input) != {'design_ref'} or spec.comparison_kind != 'single':
            raise ValueError('FPGA timing requires one saved design_ref')
        if any(value is not None for value in (spec.model, spec.sensor, spec.evaluator)):
            raise ValueError('External models, sensors and evaluators are unsupported')
        with self.database.read_session() as session:
            design = resolve_design(session, spec.project_id, spec.input['design_ref'])
        if spec.seed != design['seed']:
            raise ValueError('Seed must match the saved design revision')
        return dict(schema_version='fpga-lab-input/1', design=design,
                    source=design['source'], execution_fidelity=FIDELITY)

    def execute(self, snapshot, work_dir: Path, should_cancel):
        design = snapshot['design']
        result = simulate(design['config'], design['seed'], should_cancel)
        result['design_ref'] = {k: design[k] for k in ('id', 'revision', 'sha256')}
        result['source'] = snapshot['source']
        result['adapter_version'] = self.metadata().version
        result['summary']['design_ref'] = result['design_ref']
        artifacts = []
        for name, value in [('timing-trace.json', result), ('summary.json', result['summary'])]:
            path = work_dir / name
            path.write_bytes(canonical_bytes(value))
            artifacts.append(ExperimentOutputFile(path, name, 'application/json'))
        path = work_dir / 'frames.csv'
        with path.open('w', newline='') as stream:
            fields = sorted({k for f in result['frames'] for k in f})
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(result['frames'])
        artifacts.append(ExperimentOutputFile(path, path.name, 'text/csv'))
        # A bounded pattern thumbnail; descriptor identifies the full synthetic source.
        from PIL import Image
        path = work_dir / 'synthetic-pattern.png'
        im = Image.new('L', (256, 128))
        c = design['config']; maximum = (1 << c['bits']) - 1
        im.putdata([((x * c['width'] // 256 + y * c['height'] // 128 + design['seed']) % (maximum + 1)) * 255 // maximum for y in range(128) for x in range(256)])
        im.save(path)
        artifacts.append(ExperimentOutputFile(path, path.name, 'image/png'))
        return ExperimentExecution(summary=result['summary'], artifacts=artifacts, fidelity=FIDELITY)
