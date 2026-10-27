"""Sealed native measurements -> bounded reference-ECU input replay, never ECU decisions."""
import json
import math
from bisect import bisect_right
from fastapi import HTTPException
from sqlalchemy import select
from . import experiment_jobs
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from .job_models import JobRow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .service import digest


def sample_measurements(boundary, data, channel, start_ms, duration_ms):
    """50ms envelopes preserve sub-sample excursions; no native time scaling.

    Before replay the monitor is inactive. After replay the last observed voltage
    or frame state is held explicitly. No new native frame is invented.
    """
    stream=boundary in ('camera_raw_csi','fpga')
    values=sorted([f for f in data['frames'] if f['channel']==channel],key=lambda f:f['terminal_ns']) if stream else data['samples']
    if not values:raise ValueError('No native measurements')
    times=[v['terminal_ns']/1e6 if stream else v['time']*1000 for v in values]
    if any(not math.isfinite(t) or t<0 for t in times) or any(a>b for a,b in zip(times,times[1:])):raise ValueError('Invalid native time axis')
    if not stream and any(not math.isfinite(v['v(out)']) for v in values):raise ValueError('Nonfinite native voltage')
    output=[]
    for seq in range(duration_ms//50):
        t=seq*50-start_ms;previous=t-50
        hi=bisect_right(times,t);lo=bisect_right(times,previous)
        selected=values[lo:hi];last=values[hi-1] if hi else None
        packet=dict(sequence=seq,boundary=boundary,phase='before' if t<0 else 'tail_hold' if previous>=times[-1] else 'native_replay',
            native_time_ms=max(0,min(t,times[-1])),window_start_ms=max(0,min(previous,times[-1])),window_end_ms=max(0,min(t,times[-1])),
            source_indices=[lo,hi],source_sample_count=len(selected))
        packet['source_index_basis']='selected channel sorted by terminal_ns' if stream else 'native trace.samples'
        if stream:
            packet.update(channel=channel,abort_count=sum(v['status']=='aborted' for v in selected),
                commit_count=sum(v['status']=='committed' for v in selected),last_status=last['status'] if last else 'pending',
                last_frame=last['frame'] if last else None,
                loss_reasons=sorted({v['loss_reason'] for v in selected if v['status']=='aborted'}))
        else:
            # Include the value held at the interval's left edge. Excursions may
            # not disappear just because the source returned before the ECU tick.
            volts=[v['v(out)'] for v in selected]
            if lo:volts.append(values[lo-1]['v(out)'])
            if not volts:volts=[values[0]['v(out)']]
            packet.update(min_v=min(volts),max_v=max(volts),last_v=(last or values[0])['v(out)'])
        output.append(packet)
    return output


def compile_coupling(db,session,project,config,duration_ms):
    if hasattr(config,'model_dump'):config=config.model_dump()
    ref=config['pair_ref'];row=session.get(Row,(project,'hil-model-injection',ref['id'],ref['revision']))
    if row is None:raise HTTPException(404,'Model pair not found in the binding project')
    pair=json.loads(row.manifest)
    if digest(pair)!=row.manifest_hash or row.manifest_hash!=ref['sha256']:raise HTTPException(409,'Model pair hash changed')
    plan=pair['plan'];settings=plan['settings'];boundary=settings['boundary'];start=config['start_ms']
    if boundary not in ('camera_raw_csi','fpga','power','analog'):raise HTTPException(422,'Unsupported measurement boundary')
    if plan['sha256']!=digest({k:v for k,v in plan.items() if k!='sha256'}):raise HTTPException(409,'Native plan changed')
    policy=dict(boundary=boundary,min_hold_ms=config['min_hold_ms'],recovery_samples=config['recovery_samples'],
        power_min_v=8.,analog_min_v=2.,analog_max_v=3.,period_ms=50)
    runs={};samples={}
    for side in ('baseline','faulted'):
        identity=config[side+'_execution'];subject=session.get(ExperimentExecutionRow,identity);job=session.scalar(select(JobRow).where(JobRow.id==identity))
        if subject is None or subject.project_id!=project or job is None:raise HTTPException(404,'Model execution not found in binding project')
        if job.status!='completed' or job.adapter_id!='native-engineering-lab':raise HTTPException(422,'Completed native Engineering model run required')
        detail=experiment_jobs.detail(session,job);summary=detail.summary
        if summary.get('test_ref')!=pair['tests'][side] or summary.get('config_hash')!=digest(plan['sides'][side]):
            raise HTTPException(422,'Normal/fault execution does not match its immutable model pair side')
        if summary.get('seed',plan['seed'])!=plan['seed']:raise HTTPException(422,'Model seed changed')
        stream=boundary in ('camera_raw_csi','fpga')
        if stream and (summary.get('verdict')!='passed' or summary.get('committed_mismatch_count') or summary.get('protocol_error_count') or summary.get('unexpected_mismatch_count')):
            raise HTTPException(422,'Native RTL model failed; cannot drive ECU measurements')
        if not stream and summary.get('engine')!='spice':raise HTTPException(422,'Native ngspice run required')
        name='frames.json' if stream else 'trace.json'
        artifact=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==identity,ExperimentArtifactRow.name==name))
        if artifact is None or artifact.size_bytes>32*1024*1024:raise HTTPException(422,'Bounded native measurement artifact required')
        data=json.loads(experiment_jobs.artifact_path(db,artifact).read_bytes())
        try:samples[side]=sample_measurements(boundary,data,settings.get('channel',0),start,duration_ms)
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,'Native measurement contract invalid: '+str(exc)) from exc
        native_end=samples[side][-1]['native_time_ms']
        if start+native_end+config['min_hold_ms']+config['recovery_samples']*50>=duration_ms:
            raise HTTPException(422,'Replay must leave a full recovery window')
        runs[side]=dict(execution_id=identity,manifest_sha256=detail.manifest_hash,artifact_id=artifact.id,artifact_name=name,
            artifact_sha256=artifact.sha256,test_ref=pair['tests'][side],native_duration_ms=native_end)
    begin=start+(settings['at_us']/1000 if 'at_us' in settings else settings['start_ms'])
    end=start+((settings['at_us']+settings['duration_us'])/1000 if 'at_us' in settings else settings['end_ms'])
    summary=dict(schema_version='hil-model-coupling/1',boundary=boundary,pair_ref=ref,policy=policy,runs=runs,start_ms=start,
        time_scale=1,measurement_period_ms=50,extension='Before replay: inactive. After replay: hold last observed state/value.',
        mode='sealed_native_measurement_replay',carla_closed_loop=True,bidirectional_model_coupling=False,pixel_perception=False,
        physical_hardware_connected=False,injection=dict(kind='model_'+boundary,start_ms=begin,end_ms=end))
    if settings.get('repeats'):
        summary['injections']=[summary['injection']]+[dict(kind='model_'+boundary,
            start_ms=start+(f['at_us']/1000 if 'at_us' in f else f['start_ms']),
            end_ms=start+((f['at_us']+f['duration_us'])/1000 if 'at_us' in f else f['end_ms'])) for f in settings['repeats']]
    return dict(summary=summary|{'sha256':digest(summary)},samples=samples,policy=policy)


def compile_model_set(db,session,project,configs,duration_ms):
    """Independent source provenance and recovery latches; any protector holds braking."""
    members=[compile_coupling(db,session,project,c,duration_ms) for c in configs]
    names=[m['summary']['boundary'] for m in members]
    if len(set(names))!=len(names):raise HTTPException(422,'One model per measurement boundary is required')
    policy=dict(boundary='multiple',combination='any_protect',members={n:m['policy'] for n,m in zip(names,members)},
        release_qualification=dict(algorithm='fresh-window-v2',period_ms=50,fresh_windows=2,max_age_ms=10))
    samples={side:[dict(sequence=i,boundary='multiple',samples={n:m['samples'][side][i] for n,m in zip(names,members)})
        for i in range(duration_ms//50)] for side in ('baseline','faulted')}
    runs={side:dict(members={n:m['summary']['runs'][side] for n,m in zip(names,members)}) for side in samples}
    summary=dict(schema_version='hil-model-coupling-set/1',boundary='multiple',combination='any_protect',
        members=[m['summary'] for m in members],policy=policy,runs=runs,
        injections=[f for m in members for f in m['summary'].get('injections',[m['summary']['injection']])],time_scale=1,measurement_period_ms=50,
        mode='sealed_native_measurement_replay',carla_closed_loop=True,bidirectional_model_coupling=False,
        pixel_perception=False,physical_hardware_connected=False)
    return dict(summary=summary|{'sha256':digest(summary)},samples=samples,policy=policy)
