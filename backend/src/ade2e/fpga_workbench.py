"""Immutable graphical FIFO workbench, compilation validation and experiment facade."""
import json
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .fpga_lab_schemas import DesignAction
from .job_models import JobRow
from .fpga_workbench_schemas import ADAPTER, FIDELITY, WorkbenchSave, RevisionRef
from .rtl_lab_schemas import RtlConfig
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

SIGNAL_REGISTRY = dict(nodes=dict(
    source=['out_valid', 'out_ready', 'out_data', 'accepted_out'],
    fifo=['in_valid', 'in_ready', 'in_data', 'out_valid', 'out_ready', 'out_data', 'occupancy', 'occupancy_after', 'full', 'empty', 'accepted_in', 'accepted_out'],
    sink=['in_valid', 'in_ready', 'in_data', 'accepted_in'], ready=['value']),
    edges=dict(stream=['valid', 'ready', 'data', 'accepted'], ready=['value']))
ASSUMPTIONS = ['Bundled single-clock 32-bit FIFO graphs, one source/sink/ready controller and 1–4 serial FIFOs.',
               '100 MHz virtual clock; independent SimPy and Verilator runs, not FPGA board or chip timing validation.',
               'Labels, positions and instruments affect presentation only; executable topology and settings are separately hashed.']


def execution_hash(bench):
    graph = bench['graph']
    nodes = sorted((dict(id=n['id'], type=n['type'], params=n['params']) for n in graph['nodes']), key=lambda n: n['id'])
    edges = sorted(graph['edges'], key=lambda e: e['id'])
    return digest(dict(schema_version='fpga-workbench-execution/1', nodes=nodes, edges=edges,
                       settings=bench['settings'], seed=bench['seed']))


def validate_bench(bench):
    graph = bench['graph']; nodes = {n['id']: n for n in graph['nodes']}; edges = {e['id']: e for e in graph['edges']}
    errors = []
    def error(code, message, ns=(), es=(), ps=()):
        errors.append(dict(code=code, message=message, node_ids=list(ns), edge_ids=list(es), probe_ids=list(ps)))
    groups = {t: [n['id'] for n in nodes.values() if n['type'] == t] for t in ['source', 'fifo', 'sink', 'ready']}
    for kind in ['source', 'sink', 'ready']:
        if len(groups[kind]) != 1:
            error('node_count', f'Exactly one {kind} is required', groups[kind])
    if not 1 <= len(groups['fifo']) <= 4:
        error('fifo_count', 'One to four serial FIFOs are required', groups['fifo'])
    stream = []; control = []
    for edge in edges.values():
        source, target = nodes.get(edge['source']), nodes.get(edge['target'])
        if source is None or target is None:
            error('missing_endpoint', 'Edge refers to a missing node', [edge['source'], edge['target']], [edge['id']]); continue
        if edge['target_port'] == 'ready':
            if source['type'] != 'ready' or target['type'] != 'sink':
                error('invalid_port', 'Ready output must connect to sink.ready', [source['id'], target['id']], [edge['id']])
            control.append(edge)
        elif source['type'] not in ['source', 'fifo'] or target['type'] not in ['fifo', 'sink']:
            error('invalid_port', 'Data output must connect source/FIFO to FIFO/sink input', [source['id'], target['id']], [edge['id']])
        else:
            stream.append(edge)
    if len(control) != 1:
        error('ready_connection', 'Exactly one Ready → Sink.ready edge is required', groups['ready'] + groups['sink'], [e['id'] for e in control])
    for node in nodes.values():
        if node['type'] == 'ready': continue
        incoming = [e for e in stream if e['target'] == node['id']]
        outgoing = [e for e in stream if e['source'] == node['id']]
        expected = (0, 1) if node['type'] == 'source' else (1, 0) if node['type'] == 'sink' else (1, 1)
        if (len(incoming), len(outgoing)) != expected:
            error('serial_connection', f'{node["type"]} needs {expected[0]} data input and {expected[1]} data output; fan-out/fan-in is unsupported', [node['id']], [e['id'] for e in incoming + outgoing])
    order = []; ordered_edges = []; visited = set()
    if len(groups['source']) == len(groups['sink']) == 1:
        current = groups['source'][0]
        while current not in visited:
            visited.add(current)
            outgoing = [e for e in stream if e['source'] == current]
            if not outgoing: break
            if len(outgoing) != 1: break
            edge = outgoing[0]; ordered_edges.append(edge['id']); current = edge['target']
            if nodes[current]['type'] == 'fifo': order.append(current)
        if current in visited and any(e['source'] == current for e in stream):
            error('cycle', 'Combinational feedback/cyclic paths are unsupported', list(visited), ordered_edges)
        if current != groups['sink'][0] or set(order) != set(groups['fifo']) or len(ordered_edges) != len(stream):
            error('disconnected_path', 'Every FIFO must lie on one complete Source → FIFO(s) → Sink path', sorted(set(groups['fifo']) - set(order)), [e['id'] for e in stream if e['id'] not in ordered_edges])
    for probe in bench['probes']:
        target = nodes.get(probe['target_id']) if probe['target_type'] == 'node' else edges.get(probe['target_id'])
        if target is None:
            error('orphan_probe', 'Probe target is missing', ps=[probe['id']]); continue
        allowed = SIGNAL_REGISTRY['nodes'][target['type']] if probe['target_type'] == 'node' else SIGNAL_REGISTRY['edges']['ready' if target['target_port'] == 'ready' else 'stream']
        if set(probe['signals']) - set(allowed):
            error('unsupported_signal', 'Probe signal is unsupported for this target', ps=[probe['id']])
        if probe['instrument'] == 'buffer':
            occupancy = {'occupancy', 'occupancy_after'}
            if (probe['target_type'] != 'node' or target.get('type') != 'fifo'
                    or not occupancy.intersection(probe['signals'])
                    or set(probe['signals']) - (occupancy | {'full', 'empty'})):
                error('invalid_buffer_probe', 'Buffer instruments require a FIFO occupancy signal, optionally full/empty', ps=[probe['id']])
        if probe['instrument'] == 'counter':
            numeric = {'data', 'in_data', 'out_data', 'occupancy', 'occupancy_after'}
            if numeric.intersection(probe['signals']):
                error('invalid_counter_probe', 'Counters accept boolean assertion/transfer signals, not data or occupancy values', ps=[probe['id']])
    probe_ids = {p['id'] for p in bench['probes']}
    if set(bench['panel_order']) - probe_ids:
        error('orphan_panel', 'Panel order refers to a missing probe', ps=sorted(set(bench['panel_order']) - probe_ids))
    config = None
    if len(groups['source']) == len(groups['ready']) == 1 and order:
        config = dict(words=nodes[groups['source'][0]]['params']['words'], **bench['settings'],
                      stalls=nodes[groups['ready'][0]]['params']['stalls'], depths=[nodes[n]['params']['depth'] for n in order])
        try:
            RtlConfig.model_validate({k: v for k, v in config.items() if k != 'depths'})
        except ValueError as exc:
            error('invalid_schedule', str(exc), groups['ready'])
    compiled = None
    execution = execution_hash(bench)
    if not errors:
        compiled = dict(fifo_order=order, depths=config['depths'], source_id=groups['source'][0], sink_id=groups['sink'][0],
                        ready_id=groups['ready'][0], stream_edges=ordered_edges, ready_edge_id=control[0]['id'], config=config, execution_hash=execution)
    return dict(valid=not errors, errors=errors, warnings=[], compiled=compiled, execution_hash=execution)


def read_bench(row):
    if row is None: raise HTTPException(404, 'Workbench revision not found in project')
    try:
        value = json.loads(row.manifest)
        if (digest(value) != row.manifest_hash or value['id'] != row.id or value['revision'] != row.revision
                or value['project_id'] != row.project_id or value['schema_version'] != 'fpga-workbench/1'
                or value['fidelity'] != FIDELITY
                or value['execution_hash'] != execution_hash(value)):
            raise ValueError('Workbench revision identity differs')
        WorkbenchSave.model_validate({k: value[k] for k in ['project_id', 'name', 'graph', 'settings', 'seed', 'probes', 'panel_order']})
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, 'Workbench revision integrity check failed') from exc
    return value | {'sha256': row.manifest_hash}


def resolve_bench(session, project_id, ref):
    ref = RevisionRef.model_validate(ref).model_dump()
    bench = read_bench(session.get(Row, (project_id, 'fpga-workbench', ref['id'], ref['revision'])))
    if bench['sha256'] != ref['sha256']: raise HTTPException(409, 'Workbench revision hash differs')
    return bench


def hydrate_bench(bench):
    result = validate_bench(bench)
    if not result['valid']: raise ValueError('Invalid workbench: ' + '; '.join(e['message'] for e in result['errors']))
    return dict(bench=bench, compiled=result['compiled'])


def templates_payload():
    def preset(identity, name, count=1, stalls=None, reset=False):
        nodes = [dict(id='source', type='source', label='Source', position=dict(x=40., y=160.), params=dict(words=4096))]
        for i in range(count): nodes.append(dict(id=f'fifo{i+1}', type='fifo', label=f'FIFO {i+1}', position=dict(x=260.+i*220, y=160.), params=dict(depth=64)))
        nodes += [dict(id='sink', type='sink', label='Sink', position=dict(x=260.+count*220, y=160.), params={}),
                  dict(id='ready', type='ready', label='Ready schedule', position=dict(x=260.+count*220, y=350.), params=dict(stalls=stalls or []))]
        path = ['source'] + [f'fifo{i+1}' for i in range(count)] + ['sink']
        edges = [dict(id=f'stream{i}', source=a, source_port='out', target=b, target_port='in') for i, (a, b) in enumerate(zip(path, path[1:]))]
        edges.append(dict(id='ready_sink', source='ready', source_port='out', target='sink', target_port='ready'))
        probes = [dict(id='fifo_buffer', target_type='node', target_id='fifo1', signals=['occupancy', 'full', 'empty'], instrument='buffer'),
                  dict(id='source_logic', target_type='edge', target_id='stream0', signals=['valid', 'ready', 'accepted'], instrument='logic'),
                  dict(id='sink_data', target_type='node', target_id='sink', signals=['in_data', 'accepted_in'], instrument='data')]
        return dict(id=identity, name=name, graph=dict(nodes=nodes, edges=edges), seed=42,
                    settings=dict(max_cycles=20000, resets=[0, 1, 160] if reset else [0, 1]), probes=probes, panel_order=[p['id'] for p in probes])
    return dict(schema_version='fpga-workbench-templates/1', fidelity=FIDELITY, constants=dict(clock_hz=100000000, data_width=32),
                signal_registry=SIGNAL_REGISTRY, assumptions=ASSUMPTIONS, authoring_schema=WorkbenchSave.model_json_schema(),
                items=[preset('one-fifo', 'Workbench · 1 FIFO'), preset('two-fifo', 'Workbench · 2 FIFO', 2),
                       preset('stall', 'Workbench · 수신 정체·회복', 2, [dict(start_cycle=128, end_cycle=384)]),
                       preset('reset', 'Workbench · 실행 중 리셋', 2, [dict(start_cycle=128, end_cycle=384)], True)])


def register_fpga_workbench_routes(api, db, registry):
    prefix = '/hil-sim/fpga-workbench'
    def readiness_payload():
        handler = registry.get(ADAPTER)
        if handler is None: return dict(adapter_installed=False, available=False, worker_readiness='not_verified', status='not_configured')
        metadata = handler.metadata()
        value = dict(adapter_installed=True, status=metadata.status, adapter_version=metadata.version)
        if callable(getattr(handler, 'readiness', None)): value.update(handler.readiness())
        return value | dict(worker_readiness='not_verified')
    @api.get(prefix + '/templates', tags=['fpga-workbench'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return templates_payload() | readiness_payload()
    @api.get(prefix + '/readiness', tags=['fpga-workbench'])
    def readiness(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return readiness_payload()
    @api.post(prefix + '/validate', tags=['fpga-workbench'])
    def validate(body: WorkbenchSave, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        return validate_bench(body.model_dump())
    @api.post(prefix + '/benches', status_code=201, tags=['fpga-workbench'])
    def save_bench(body: WorkbenchSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, idempotency_key, 'workbench-save')
        key_id, body_hash = digest(idempotency_key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            receipt_row = session.get(Row, (body.project_id, 'fpga-workbench-write', key_id, 1))
            if receipt_row:
                receipt = json.loads(receipt_row.manifest)
                if digest(receipt) != receipt_row.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another Workbench write')
                return resolve_bench(session, body.project_id, receipt['ref'])
            identity = body.id or uid('fpwb')
            latest = session.scalar(select(func.max(Row.revision)).where(Row.project_id == body.project_id, Row.kind == 'fpga-workbench', Row.id == identity)) or 0
            if latest != body.expected_revision or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'Workbench changed; reopen its latest revision')
            value = body.model_dump(exclude={'id', 'expected_revision'}) | dict(schema_version='fpga-workbench/1', id=identity, revision=latest+1, created_at=utcnow(), fidelity=FIDELITY)
            value['execution_hash'] = execution_hash(value); sha = digest(value)
            session.add(Row(project_id=body.project_id, kind='fpga-workbench', id=identity, revision=latest+1, manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest+1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind='fpga-workbench-write', id=key_id, revision=1, manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | dict(sha256=sha)
    @api.get(prefix + '/benches', tags=['fpga-workbench'])
    def benches(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == 'fpga-workbench').order_by(Row.id, Row.revision.desc()))
            return dict(items=[read_bench(row) for row in rows])
    @api.get(prefix + '/benches/{identity}', tags=['fpga-workbench'])
    def bench(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session: return read_bench(session.get(Row, (project_id, 'fpga-workbench', identity, revision)))
    @api.post(prefix + '/benches/{identity}/preview', tags=['fpga-workbench'])
    @api.post(prefix + '/benches/{identity}/preflight', tags=['fpga-workbench'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            bench = resolve_bench(session, body.project_id, reference)
            result = validate_bench(bench)
            if not result['valid']: raise HTTPException(422, result)
        spec = ExperimentCreate(project_id=body.project_id, name=bench['name'], kind='fpga_workbench', adapter_id=ADAPTER, input={'bench_ref': reference}, seed=bench['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash, fidelity=FIDELITY, execution_hash=bench['execution_hash'], worker_readiness='not_verified')
    @api.get(prefix + '/executions', tags=['fpga-workbench'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(ExperimentRow.project_id == project_id, ExperimentRow.kind == 'fpga_workbench').order_by(JobRow.created_at.desc()).limit(100)).all()
            return dict(items=[dict(id=x.job_id, status=j.status, name=e.name, bench_ref=experiment_jobs.experiment_get(session, e.id)[1]['prepared']['spec']['input']['bench_ref']) for e, x, j in rows])
