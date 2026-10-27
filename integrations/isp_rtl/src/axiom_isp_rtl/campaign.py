"""Actual sequential native campaigns on one immutable Engineering execution.

Every child executes the same generated DUT. This orchestrator never substitutes
reference-only output. A cancelled job is not a completed campaign.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
from . import adas
from .runtime import digest


def validate(config):
    from ade2e.adas_validation_schemas import AdasCampaignConfig
    return AdasCampaignConfig.model_validate(config).model_dump()


def identity():
    result = adas.identity()
    from . import preboard
    ready = preboard.readiness()
    result['preboard_identity'] = preboard.identity() if ready['available'] else ready
    from ade2e import adas_validation_schemas
    result['campaign_planner_sha256'] = digest(adas_validation_schemas.__file__)
    return result


def generated_design(config, outdir):
    from ade2e.adas_validation_schemas import campaign_plan
    return adas.generated_design(campaign_plan(validate(config))[0]['config'], outdir)


def _load(path):
    return json.loads(Path(path).read_text())


def _consumer_errors(summary, frames, artifact):
    """Require transactional evidence; absence is never equivalent to zero errors."""
    errors = []
    fields = ('consumer_published_frames', 'consumer_discarded_pixels',
              'consumer_late_discarded_pixels', 'consumer_protocol_error_count',
              'consumer_aborted_visible_frames')
    for key in fields:
        if type(summary.get(key)) is not int or summary[key] < 0:
            errors.append('missing or invalid '+key)
    if errors:
        return errors
    if summary['consumer_protocol_error_count'] or summary['consumer_aborted_visible_frames']:
        errors.append('downstream consumer exposed an invalid frame or protocol')
    try:
        consumer = _load(artifact)
        if consumer['schema_version'] != 'adas-transactional-consumer/1':
            raise ValueError('unsupported consumer schema')
        records = consumer['frames']
        by_key = {(f['channel'], f['frame']): f for f in records}
        expected = {(f['channel'], f['frame']): f for f in frames['frames']}
        if len(by_key) != len(records) or len(expected) != len(frames['frames']) or set(by_key) != set(expected):
            raise ValueError('consumer frame identities missing or duplicated')
        if summary['consumer_published_frames'] != summary['completed_frames']:
            errors.append('consumer publications differ from completed DUT frames')
        for field in fields:
            if consumer[field.removeprefix('consumer_')] != summary[field]:
                errors.append('consumer artifact disagrees with '+field)
        counts = dict(published_frames=0, discarded_pixels=0, late_discarded_pixels=0)
        for key, frame in expected.items():
            row = by_key[key]
            if type(row['visible']) is not bool:
                raise ValueError('invalid consumer visibility')
            if any(type(row[k]) is not int for k in ('received_pixels', 'discarded_pixels', 'late_discarded_pixels', 'published_at_ns', 'epoch', 'config_version')):
                raise ValueError('invalid consumer numeric field')
            if not 0 <= row['late_discarded_pixels'] <= row['discarded_pixels'] <= row['received_pixels']:
                raise ValueError('invalid consumer discard counts')
            if row['received_pixels'] != frame['received_pixels']:
                errors.append('consumer pixel count differs from DUT frame '+str(key))
            counts['published_frames'] += row['visible']
            counts['discarded_pixels'] += row['discarded_pixels']
            counts['late_discarded_pixels'] += row['late_discarded_pixels']
            if frame['status'] == 'committed':
                if not (row['visible'] and row['status'] == 'published' and
                        row['published_sha256'] == frame['output_sha256'] and
                        row['published_at_ns'] == frame['terminal_ns'] and
                        row['epoch'] == frame['epoch'] and row['config_version'] == frame['config_version'] and
                        row['discarded_pixels'] == 0):
                    errors.append('committed frame publication differs from DUT '+str(key))
            elif row['visible'] or row['published_sha256'] is not None or row['published_at_ns']:
                errors.append('uncommitted frame became visible '+str(key))
            elif frame['status'] == 'aborted' and (row['status'] != 'discarded' or row['discarded_pixels'] != frame['received_pixels']):
                errors.append('aborted partial pixels were not all discarded '+str(key))
        for field, value in counts.items():
            if value != consumer[field]:
                errors.append('consumer frame totals disagree with '+field)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append('consumer evidence unavailable or invalid: '+str(exc))
    return errors


def run(config, work_dir, check, tool_identity, seed=42):
    from ade2e.adas_validation_schemas import campaign_plan, AdasPreboardConfig
    from . import preboard
    c = validate(config)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = campaign_plan(c)
    plan_bytes = json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()
    (work_dir/'campaign-plan.json').write_bytes(plan_bytes)
    started = time.monotonic()
    result_cases, manifests, errors, incomplete = [], {}, [], []
    fault_errors = []
    design_hash = None

    def progress(current_case=None, final=False):
        path = work_dir/'campaign-progress.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(dict(
            schema_version='adas-campaign-progress/1', completed=len(result_cases),
            total=len(plan)+int(c['include_preboard']), current_case=current_case,
            final=final, cases=[dict(id=x['id'], status=x['status'], verdict=x.get('validation_verdict',x['summary']['verdict'])) for x in result_cases]), indent=2))
        temporary.replace(path)

    for item in plan:
        check()
        case_error_start = len(errors)
        progress(item['id'])
        folder = work_dir/'cases'/item['id']
        with (work_dir/'campaign.log').open('a') as log:
            log.write('START '+item['id']+'\n')
        result = adas.run(item['config'], folder, check, tool_identity, item['seed'])
        summary = result['summary']
        frames = result['frames']
        manifests[item['id']] = frames
        if design_hash is None:
            design_hash = summary['design_hash']
        if summary['design_hash'] != design_hash:
            errors.append(item['id']+': generated RTL changed between cases')
        if summary['verdict'] != 'passed':
            errors.append(item['id']+': native verdict '+summary['verdict'])
        errors.extend(item['id']+': '+why for why in _consumer_errors(summary, frames, folder/'consumer.json'))
        for key in ('committed_mismatch_count', 'unexpected_mismatch_count', 'protocol_error_count', 'missing_frames'):
            if type(summary.get(key)) is not int or summary[key] != 0:
                errors.append(item['id']+': missing or nonzero '+key)
        by_key = {(f['channel'],f['frame']):f for f in frames['frames']}
        if item['category'] in {'continuous', 'initialization'} and summary['completed_frames'] != 4*item['config']['frames']:
            errors.append(item['id']+': unexpected frame loss in fault-free case')
        if item['category'] in {'repeated_reset', 'simultaneous'}:
            before = case_error_start
            faulted_channels = {f['channel'] for f in item['config']['faults']}
            for ch in range(4):
                if by_key[ch,item['config']['frames']-1]['status'] != 'committed':
                    errors.append(item['id']+': channel did not recover by final frame')
                if ch not in faulted_channels and any(f['status']!='committed' for f in frames['frames'] if f['channel']==ch):
                    errors.append(item['id']+': fault leaked to unaffected channel')
            if item['category']=='repeated_reset' and summary['aborted_frames'] != c['reset_count']:
                errors.append(item['id']+': repeated resets did not invalidate exactly their active frames')
            if item['category']=='simultaneous' and summary['aborted_frames'] != len(c['simultaneous_channels']):
                errors.append(item['id']+': simultaneous faults were not all observed')
            fault_errors.extend(errors[before:])
        case_errors = errors[case_error_start:]
        if item['category']=='fifo_sweep':
            fault_errors.extend(case_errors)
        child = item | dict(status='completed', summary=summary, validation_errors=case_errors,
            validation_verdict='failed' if case_errors else 'passed',
            frame_manifest_artifact='cases/'+item['id']+'/frames.json',
            summary_artifact='cases/'+item['id']+'/summary.json',
            consumer_artifact='cases/'+item['id']+'/consumer.json',
            trace_artifact='cases/'+item['id']+'/waveform.vcd')
        result_cases.append(child)
        progress()
        with (work_dir/'campaign.log').open('a') as log:
            log.write('FINISH '+item['id']+' '+summary['verdict']+'\n')

    cases_by_id = {item['id']:item for item in result_cases}
    init_comparisons = []
    seed_inputs = {}
    for case_seed in c['seeds']:
        zero = manifests['init-zero-'+str(case_seed)]['frames']
        random = manifests['init-random-'+str(case_seed)]['frames']
        equal = len(zero)==len(random) and all(
            all(a[k]==b[k] for k in ('channel','frame','status','input_sha256','output_sha256','config_version','epoch'))
            for a,b in zip(zero,random))
        source_signature = hashlib.sha256(json.dumps(sorted((f['channel'],f['frame'],f['input_sha256']) for f in zero),separators=(',',':')).encode()).hexdigest()
        seed_inputs.setdefault(source_signature,[]).append(case_seed)
        valid = all(cases_by_id[prefix+str(case_seed)]['validation_verdict']=='passed' for prefix in ('init-zero-', 'init-random-'))
        init_comparisons.append(dict(seed=case_seed, frames=len(zero), equal=equal, valid=valid, input_signature_sha256=source_signature))
        if not equal:
            errors.append('init seed '+str(case_seed)+': post-reset frame behavior depends on two-state initialization')

    distinct_inputs = len(seed_inputs)==len(c['seeds']) and len(seed_inputs)>=2
    if len(c['seeds'])>=2 and not distinct_inputs:
        incomplete.append('Configured seeds produced duplicate RAW inputs; choose distinct pixel stimulus seeds')

    sweep=[]
    for item in result_cases:
        if item['category']!='fifo_sweep':
            continue
        frames=manifests[item['id']]['frames']
        ch0=next(ch for ch in manifests[item['id']]['channels'] if ch['channel']==0)
        aborted=any(f['loss_reason']=='fifo_overflow' for f in frames if f['channel']==0)
        sweep.append(dict(case_id=item['id'], stall_cycles=item['stall_cycles'],
            fifo_peak_pixels=ch0['fifo_peak_pixels'], overflow=aborted,
            valid=item['validation_verdict']=='passed',
            recovered=next(f for f in frames if f['channel']==0 and f['frame']==2)['status']=='committed'))
    bracket=any(not x['overflow'] for x in sweep) and any(x['overflow'] for x in sweep)
    sweep_valid=all(x['valid'] for x in sweep)
    monotonic=all(not a['overflow'] or b['overflow'] for a,b in zip(sweep,sweep[1:]))
    if not monotonic or not all(x['recovered'] for x in sweep):
        errors.append('FIFO sweep lost monotonic overflow behavior or recovery')
        fault_errors.append(errors[-1])
    # A sweep that misses the transition remains explicitly incomplete.
    if not bracket:
        incomplete.append('FIFO sweep did not bracket absorption and overflow; extend configured durations')
    if len(c['seeds']) < 2:
        incomplete.append('Multiple stimulus seeds were not evaluated')

    preboard_summary = None
    if c['include_preboard']:
        check()
        progress('preboard')
        if not preboard.readiness()['available']:
            raise ValueError('Requested pre-board native tools are unavailable; no substitute result')
        pc=AdasPreboardConfig(engine='adas_preboard_checks',core_config=plan[0]['config']).model_dump()
        pr=preboard.run(pc,work_dir/'preboard',check,tool_identity['preboard_identity'],seed)
        preboard_summary=pr['summary']
        result_cases.append(dict(id='preboard',name='Initialization / clock boundary checks',category='preboard',
            seed=seed,status='completed',summary=preboard_summary,summary_artifact='preboard/summary.json',config=pc))
        if preboard_summary.get('design_hash') != design_hash:
            errors.append('Pre-board source identity differs from native campaign')
        if preboard_summary['verdict']!='passed':
            errors.append('Pre-board native checks did not pass')
        progress()
    else:
        incomplete.append('Pre-board checks were disabled for this campaign')
    normal=result_cases[0]['summary']
    summary=dict(verdict='failed' if errors else 'incomplete' if incomplete else 'passed',
        execution_verdict='failed' if errors else 'passed', readiness='incomplete', board_ready=False,
        design_hash=design_hash,
        mode=c['mode'],cases=result_cases,errors=errors,incomplete_reasons=incomplete,total_cases=len(result_cases),
        long_frames_per_channel=c['long_frames'],continuous_source_seconds=normal['source_window_ns']/1e9,
        completed_frames=sum(x['summary'].get('completed_frames',0) for x in result_cases),
        aborted_frames=sum(x['summary'].get('aborted_frames',0) for x in result_cases),
        committed_mismatch_count=sum(x['summary'].get('committed_mismatch_count',0) for x in result_cases),
        initialization_comparisons=init_comparisons,fifo_sweep=sweep,
        source_seed_diversity=dict(requested_seeds=len(c['seeds']),unique_inputs=len(seed_inputs),
            complete=distinct_inputs, groups=[dict(input_signature_sha256=k,seeds=v) for k,v in seed_inputs.items()],
            scope='Observed RAW SHA-256 across all channels and frames of each zero-initialized baseline'),
        plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),cpu_wall_seconds=time.monotonic()-started,
        gates=dict(full1080p_continuous='passed' if c['mode']=='full1080p' and result_cases[0]['validation_verdict']=='passed' and normal['completed_frames']==4*c['long_frames'] else 'failed' if c['mode']=='full1080p' else 'not_evaluated',
            multi_seed='failed' if not all(x['equal'] and x['valid'] for x in init_comparisons) else 'passed' if distinct_inputs else 'incomplete',
            fault_recovery='failed' if fault_errors else 'passed',fifo_threshold='failed' if not sweep_valid or not monotonic else 'passed' if bracket else 'incomplete',
            initialization_two_state='passed' if all(x['equal'] and x['valid'] for x in init_comparisons) else 'failed',
            preboard_checks=('failed' if preboard_summary.get('design_hash')!=design_hash else preboard_summary['verdict']) if preboard_summary else 'not_run',
            actual_board_cdc='not_configured',pins_pll_io_timing='not_configured',physical_hil='not_configured'),
        preboard=preboard_summary,scope='Actual native digital RTL campaign; four-state boundary checks are separately identified; no actual board, PHY or physical HIL verification')
    progress(final=True)
    (work_dir/'summary.json').write_text(json.dumps(summary,indent=2))
    return dict(summary=summary,axis=dict(unit='case',label='Completed native case'),signals=[],samples=[])
