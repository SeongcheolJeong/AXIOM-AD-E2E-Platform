"""Compare two retained ADAS campaigns without starting services or executions.

Rehash every local artifact against its sealed execution export. Require matching
stimulus, committed pixels, fault dispositions, recovery, and terminal metadata.
Partial aborted pixels and timing/counter changes are reported separately. The
default inputs retain the original campaign and the new timing-closure campaign.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode


CASES = ('adas-normal', 'adas-wb', 'adas-stall-30us', 'adas-overflow-100us', 'adas-reset', 'adas-input-gap')
FAULTS = {'adas-overflow-100us': 'fifo_overflow', 'adas-reset': 'channel_reset', 'adas-input-gap': 'frame_boundary'}
ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(value, receipt_path):
    path = Path(value)
    candidates = [path] if path.is_absolute() else [receipt_path.parent / path, ROOT / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError('Retained evidence is missing: ' + value)


def _link_ooc_receipt(receipt, path):
    """Bind separately executed OOC evidence without rewriting either receipt."""
    path = Path(path).resolve()
    external = json.loads(path.read_text())
    require(external.get('schema_version') == 'adas-preboard-validation/1', 'Unsupported external OOC receipt schema')
    require('ecp5-ooc' not in receipt['runs'], 'Campaign already contains OOC evidence')
    item = dict(external['runs']['ooc'])
    require(item.get('operation') == 'build' and item.get('status') == 'completed', 'External OOC execution is not completed')
    require(item['test']['project_id'] == receipt['project_id'], 'External OOC project differs')
    require(item['summary']['design_hash'] == receipt['runs']['adas-normal']['summary']['design_hash'], 'External OOC design hash differs from the simulated DUT')
    folder = path.parent/(path.stem+'-artifacts')/'ooc'
    item['export_path'] = str(folder/'execution-export.json')
    retained = []
    for artifact in item['artifacts_verified']:
        target = folder/artifact['name']
        require(target.resolve().is_relative_to(folder.resolve()), 'External OOC artifact path escapes evidence folder')
        retained.append(artifact | dict(path=str(target)))
    item['artifacts_verified'] = retained
    receipt['runs']['ecp5-ooc'] = item
    return dict(path=str(path),sha256=checksum(path),job_id=item['job_id'])


def load_campaign(path, build_receipt=None):
    """Bind receipt data to rehashed immutable exports, not receipt flags alone."""
    path = Path(path).resolve()
    receipt = json.loads(path.read_text())
    require(receipt.get('schema_version') == 'adas-module-validation/1', 'Unsupported campaign schema')
    require(receipt.get('completed_at') and not receipt.get('smoke'), 'A completed full-resolution campaign is required')
    require(set(receipt['cases']) == set(CASES), 'All six ADAS cases are required')
    external_build = _link_ooc_receipt(receipt,build_receipt) if build_receipt is not None else None
    require(set(receipt['runs']) == {*CASES, 'ecp5-ooc'}, 'Six simulation runs plus OOC build are required')
    runs = {}
    for case, item in receipt['runs'].items():
        label = path.name + ':' + case
        export_path = local_path(item['export_path'], path)
        require(checksum(export_path) == item['export_sha256'], label + ': export SHA differs')
        export = json.loads(export_path.read_text())
        execution = export['execution']
        require(execution['id'] == item['job_id'] and execution['status'] == item['status'] == 'completed', label + ': execution differs')
        require(execution['summary'] == item['summary'], label + ': summary differs from sealed export')
        require(export['prepared']['input']['test'] == item['test'], label + ': saved test differs from sealed export')
        require(item['test']['project_id'] == receipt['project_id'], label + ': project differs')
        artifacts = {a['name']: a for a in execution['artifacts']}
        retained = {a['name']: a for a in item['artifacts_verified']}
        require(len(artifacts) == len(execution['artifacts']) and len(retained) == len(item['artifacts_verified']), label + ': duplicate artifact name')
        require(artifacts.keys() == retained.keys(), label + ': incomplete artifact set')
        paths = {}
        for name, artifact in artifacts.items():
            saved = retained[name]
            require(all(saved[k] == artifact[k] for k in ('sha256', 'size_bytes')), label + ': artifact metadata differs: ' + name)
            artifact_path = local_path(saved['path'], path)
            require(artifact_path.stat().st_size == artifact['size_bytes'] and checksum(artifact_path) == artifact['sha256'], label + ': artifact bytes differ: ' + name)
            paths[name] = artifact_path
        provenance = json.loads(paths['provenance.json'].read_text())
        test = item['test']
        require(json.loads(paths['test.json'].read_text()) == test, label + ': test artifact differs')
        require(json.loads(paths['summary.json'].read_text()) == item['summary'], label + ': summary artifact differs')
        require(provenance['test_ref'] == {k: test[k] for k in ('id', 'revision', 'sha256')}, label + ': provenance test differs')
        require(provenance['config_hash'] == test['config_hash'] == item['summary']['config_hash'], label + ': configuration hash differs')
        require(provenance['seed'] == test['seed'] and provenance['design_hash'] == item['summary']['design_hash'], label + ': provenance identity differs')
        require(provenance['tool_identity'] == export['prepared']['input']['tool_identity'], label + ': prepared tool identity differs')
        run = dict(item=item, provenance=provenance, artifact_count=len(artifacts), export_sha256=item['export_sha256'])
        if case != 'ecp5-ooc':
            require(item['config'] == test['config'], label + ': authored configuration differs')
            manifest = json.loads(paths['frames.json'].read_text())
            require(manifest['config'] == test['config'], label + ': frame configuration differs')
            require(checksum(paths['frames.json']) == item['frame_manifest']['sha256'], label + ': frame manifest hash differs')
            frames = {(f['channel'], f['frame']): f for f in manifest['frames']}
            require(len(frames) == len(manifest['frames']) == 120 and set(frames) == {(c, f) for c in range(4) for f in range(30)}, label + ': frame set differs')
            run.update(manifest=manifest, frames=frames)
        runs[case] = run
    return dict(path=str(path), sha256=checksum(path), receipt=receipt, runs=runs, external_build_receipt=external_build)


def changed(before, after, keys):
    return {key: dict(before=before.get(key), after=after.get(key)) for key in keys if before.get(key) != after.get(key)}


def statistic(values):
    return dict(count=len(values), min=min(values), max=max(values), mean=sum(values)/len(values)) if values else dict(count=0)


def verify_shared_store(before, after, token=None):
    """Verify copied baseline jobs in the new API before emitting cross-store links."""
    from axiom_sdk import AxiomClient
    verified = []
    url = after['receipt']['api_url']
    with AxiomClient(url, token=token, timeout=30) as client:
        for campaign in (before, after):
            for case, run in campaign['runs'].items():
                item = run['item']
                saved = client.experiment_execution(item['job_id'])
                require(saved['status'] == 'completed' and saved['manifest_hash'] == run['export_sha256'],
                        'New API retained execution differs: ' + item['job_id'])
                verified.append(dict(case=case,job_id=item['job_id'],manifest_hash=saved['manifest_hash']))
    return dict(api_url=url,executions=verified)


def compare_campaigns(before, after, shared_store=None):
    """Return both preservation and timing gates; timing success cannot hide pixel regressions."""
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)
    old, new = before['receipt'], after['receipt']
    check(old['project_id'] == new['project_id'], 'Campaign project differs')
    report = dict(schema_version='adas-module-timing-comparison/1', created_at=datetime.now(timezone.utc).isoformat(),
        before=dict(path=before['path'],sha256=before['sha256'],campaign_id=old['campaign_id'],
                    artifact_count=sum(r.get('artifact_count',0) for r in before['runs'].values()),exports_verified=len(before['runs'])),
        after=dict(path=after['path'],sha256=after['sha256'],campaign_id=new['campaign_id'],
                   artifact_count=sum(r.get('artifact_count',0) for r in after['runs'].values()),exports_verified=len(after['runs'])),
        scope='Native open RTL behavior and ECP5 out-of-context timing; no PHY, board, ECU or physical HIL validation',
        evidence_hashes_verified=True, cases={}, errors=errors,
        external_build_receipts=dict(before=before.get('external_build_receipt'),after=after.get('external_build_receipt')))
    for case in CASES:
        a, b = before['runs'][case], after['runs'][case]
        ai, bi = a['item'], b['item']
        ac, bc = ai['test']['config'], bi['test']['config']
        for config in (ac,bc):
            check((config.get('width'),config.get('height'),config.get('frames'),config.get('fps')) == (1920,1080,30,30), case + ': not full 1080p30')
        # Old retained revisions predate explicit zero-init / no-repeat defaults.
        # Normalize their meaning for comparison; never modify sealed evidence.
        from ade2e.adas_module_schemas import AdasModuleConfig
        try:
            normalized = [AdasModuleConfig.model_validate(config).model_dump(exclude={'trace_windows'}) for config in (ac,bc)]
            check(normalized[0] == normalized[1], case + ': stimulus/configuration differs')
        except ValueError:
            check(False, case + ': unsupported stimulus/configuration')
        check(ai['test']['seed'] == bi['test']['seed'], case + ': seed differs')
        frame_changes, partial_changes, delta = [], [], {}
        for key, af in a['frames'].items():
            bf = b['frames'][key]
            label = f'{case}: CH{key[0]} frame {key[1]}'
            for field in ('input_sha256', 'source_pixels', 'source_complete', 'status', 'loss_reason', 'config_version', 'epoch', 'terminal_output_epoch_checked'):
                check(field in af and field in bf and af[field] == bf[field], label + ': ' + field + ' differs/missing')
            check(af.get('terminal_output_epoch_checked') is True and bf.get('terminal_output_epoch_checked') is True, label + ': terminal epoch was not checked')
            if af['status'] == bf['status'] == 'committed':
                for field in ('output_sha256', 'reference_sha256', 'received_pixels'):
                    check(af[field] == bf[field], label + ': committed ' + field + ' differs')
            else:
                differences = changed(af, bf, ('output_sha256', 'reference_sha256', 'received_pixels', 'mismatch_count', 'mismatch_disposition'))
                if differences:
                    partial_changes.append(dict(channel=key[0],frame=key[1],changes=differences))
            differences = changed(af, bf, ('first_input_ns','first_output_ns','last_output_ns','terminal_ns','latency_ns','fifo_peak_pixels'))
            if differences:
                frame_changes.append(dict(channel=key[0],frame=key[1],changes=differences))
            for field in ('first_output_ns','terminal_ns','latency_ns'):
                if isinstance(af.get(field), (int,float)) and isinstance(bf.get(field), (int,float)):
                    delta.setdefault(field, []).append(bf[field]-af[field])
        # Re-evaluate each retained scenario's contracts rather than trusting its pass label.
        for side, campaign, run in (('before',before,a),('after',after,b)):
            summary = run['item']['summary']
            check(summary['verdict'] == 'passed', f'{side} {case}: native verdict failed')
            for field in ('committed_mismatch_count','unexpected_mismatch_count','unexpected_disposition_count','protocol_error_count','missing_frames','unadmitted_frames'):
                check(summary.get(field) == 0, f'{side} {case}: {field} is not zero')
            expected_aborted = int(case in FAULTS)
            check(summary['completed_frames'] == 120-expected_aborted and summary['aborted_frames'] == expected_aborted, f'{side} {case}: disposition totals differ')
            baseline = campaign['runs']['adas-normal']['frames']
            for key, frame in run['frames'].items():
                label = f'{side} {case}: CH{key[0]} frame {key[1]}'
                aborted = case in FAULTS and key == (0,0)
                expected_version = int(case == 'adas-wb' and key[0] == 0 and key[1] >= 3)
                expected_epoch = int(case in FAULTS and key[0] == 0 and key[1] > 0)
                check(frame['status'] == ('aborted' if aborted else 'committed'), label + ': unexpected disposition')
                check(frame['loss_reason'] == (FAULTS[case] if aborted else ''), label + ': unexpected loss reason')
                check(frame['config_version'] == expected_version and frame['epoch'] == expected_epoch, label + ': wrong version/epoch')
                check(frame['input_sha256'] == baseline[key]['input_sha256'] and frame.get('source_complete') is True, label + ': source differs/incomplete')
                check(bool(re.fullmatch('[0-9a-f]{64}',frame['input_sha256'])) and frame['source_pixels'] == 1920*1080, label + ': missing full source identity')
                if not aborted:
                    check(isinstance(frame['output_sha256'],str) and bool(re.fullmatch('[0-9a-f]{64}',frame['output_sha256'])) and
                          frame['mismatch_count'] == 0 and frame['output_sha256'] == frame['reference_sha256'], label + ': committed pixel check failed')
                    if not expected_version:
                        check(frame['output_sha256'] == baseline[key]['output_sha256'], label + ': recovery/unaffected output differs')
                    else:
                        check(frame['output_sha256'] != baseline[key]['output_sha256'], label + ': WB produced no expected pixel change')
                elif frame['mismatch_count']:
                    check(case == 'adas-input-gap' and frame['mismatch_disposition'] == 'invalidated_input_gap', label + ': unexplained discarded pixel errors')
            discarded = sum(f['mismatch_count'] for f in run['frames'].values() if f['status'] == 'aborted')
            check(summary.get('discarded_mismatch_count') == summary.get('expected_discarded_mismatch_count') == discarded, f'{side} {case}: discarded mismatch accounting differs')
            channels=run['manifest']['channels']
            check([c['channel'] for c in channels] == [0,1,2,3], f'{side} {case}: channel set differs')
            for channel in channels:
                frames=[f for f in run['frames'].values() if f['channel']==channel['channel']]
                for field,expected in [('output_pixels',sum(f['received_pixels'] for f in frames)),
                        ('mismatch_count',sum(f['mismatch_count'] for f in frames)),
                        ('committed_frames',sum(f['status']=='committed' for f in frames)),
                        ('aborted_frames',sum(f['status']=='aborted' for f in frames))]:
                    check(channel[field]==expected,f'{side} {case}: CH{channel["channel"]} {field} accounting differs')
                if case not in FAULTS or channel['channel']!=0:
                    check(channel['input_pixels']==1920*1080*30,f'{side} {case}: CH{channel["channel"]} source counter differs')
        channel_changes = []
        for ach, bch in zip(a['manifest']['channels'], b['manifest']['channels']):
            check(ach['channel'] == bch['channel'], case + ': channel order differs')
            diff = changed(ach,bch,('fifo_peak_pixels','input_pixels','output_pixels','mismatch_count','input_accepted_gbps','output_rgb_gbps'))
            if diff:
                channel_changes.append(dict(channel=ach['channel'], changes=diff))
        report['cases'][case] = dict(frame_count=120, committed_rgb_compared=sum(f['status']=='committed' for f in a['frames'].values()),
            before_job=ai['job_id'], after_job=bi['job_id'], config_hashes=dict(before=ai['test']['config_hash'],after=bi['test']['config_hash']),
            design_hashes=dict(before=ai['summary']['design_hash'],after=bi['summary']['design_hash']),
            latency_delta_ns={key:statistic(values) for key,values in delta.items()},
            timing_and_fifo_changes=frame_changes, discarded_partial_changes=partial_changes, channel_counter_changes=channel_changes,
            summary_counter_changes=changed(ai['summary'],bi['summary'],('cycles','drain_ns','cpu_wall_seconds','mismatch_count','discarded_mismatch_count','expected_discarded_mismatch_count')))
    builds = [before['runs']['ecp5-ooc'],after['runs']['ecp5-ooc']]
    bs = [x['item']['summary'] for x in builds]
    identities = [x['provenance']['tool_identity'] for x in builds]
    for key in ('build_profile_id','seed','target_clock_hz'):
        check(bs[0][key] == bs[1][key], 'Build ' + key + ' differs')
    check(identities[0]['profile'] == identities[1]['profile'], 'OOC target profile differs')
    check(identities[0]['tool_sha256'] == identities[1]['tool_sha256'], 'OOC native tool binaries differ')
    for side,campaign in (('before',before),('after',after)):
        designs = {run['item']['summary']['design_hash'] for run in campaign['runs'].values()}
        check(len(designs) == 1, side + ': simulation/build design hashes differ')
        check(campaign['runs']['adas-normal']['item']['summary']['acceptance_1080p30_passed'] is True, side + ': full 1080p30 normal acceptance failed')
    fmax = [s.get('fmax_mhz') for s in bs]
    report['build'] = dict(before=bs[0],after=bs[1],fmax_delta_mhz=fmax[1]-fmax[0] if all(isinstance(v,(int,float)) for v in fmax) else None,
        resource_delta={key:bs[1]['resources'][key]-bs[0]['resources'][key] for key in ('lut','ff','bram','dsp')},
        target_profile=identities[1]['profile'], tool_hashes=identities[1]['tool_sha256'],
        implementation_options=dict(before=identities[0].get('implementation_options',{}),
                                    after=identities[1].get('implementation_options',{})))
    source_old = before['runs']['adas-normal']['provenance']['tool_identity']
    source_new = after['runs']['adas-normal']['provenance']['tool_identity']
    report['source_identity_changes'] = changed(source_old.get('source_sha256',{}),source_new.get('source_sha256',{}),
        sorted(set(source_old.get('source_sha256',{})) | set(source_new.get('source_sha256',{}))))
    report['shared_runner_hashes'] = dict(before=source_old.get('shared_runner_sha256'),after=source_new.get('shared_runner_sha256'))
    report['links'] = {}
    same_store = (old['api_url'].rstrip('/') == new['api_url'].rstrip('/') or bool(shared_store)) and old['project_id'] == new['project_id']
    report['shared_store_verification'] = shared_store
    report['cross_run_links_same_api'] = same_store
    for case in CASES:
        ai,bi = before['runs'][case]['item'],after['runs'][case]['item']
        links = dict(before=ai['url'],after=bi['url'])
        if same_store:
            query = dict(project=new['project_id'],mode='fpga',target='engineering-lab',tab='module_rtl',
                test=bi['test']['id'],revision=bi['test']['revision'],execution=bi['job_id'],compare=ai['job_id'],channel=0,frame=3 if case=='adas-wb' else 0)
            links['new_pixel_a_old_pixel_b'] = new['ui_url'].rstrip('/')+'/#hil-sim?'+urlencode(query)
            if case == 'adas-normal':
                query.update(build=builds[1]['item']['job_id'],buildCompare=builds[0]['item']['job_id'])
                report['links']['build_new_vs_old'] = new['ui_url'].rstrip('/')+'/#hil-sim?'+urlencode(query)
        report['links'][case] = links
    timing_passed = (all(bs[1].get(k)=='passed' for k in ('verdict','synthesis_status','place_route_status','timing_status'))
        and bs[1]['target_clock_hz']==100000000 and bs[1]['timing_met'] is True and isinstance(fmax[1],(int,float)) and fmax[1]>=100)
    report['validation_gates'] = dict(behavior_preserved='failed' if errors else 'passed',new_ooc_100mhz='passed' if timing_passed else 'failed',physical_hil='not_configured')
    report['verdict'] = 'passed' if not errors and timing_passed else 'failed'
    return report


def markdown(report):
    build = report['build'];a,b = build['before'],build['after']
    lines = ['# ADAS RTL timing closure comparison','',f"Verdict: **{report['verdict']}**. Behavioral preservation: **{report['validation_gates']['behavior_preserved']}**.",'',
        '| Metric | Before | After |','|---|---:|---:|',
        f"| ECP5 OOC Fmax (MHz) | {a['fmax_mhz']} | {b['fmax_mhz']} |",
        f"| Timing status | {a['timing_status']} | {b['timing_status']} |"]
    for key in ('lut','ff','bram','dsp'):
        lines.append(f"| {key.upper()} | {a['resources'][key]} | {b['resources'][key]} |")
    lines += ['',f"Before design: `{a['design_hash']}`",f"After design: `{b['design_hash']}`",'',
        'All artifact bytes and sealed exports were rehashed. Committed RGB, source pixels, dispositions, recovery, configuration versions and terminal epochs are compared. Discarded partial pixels and measured timing/counter changes are recorded separately in the JSON.','',
        '| Case | Committed RGB frames | Latency delta min/max (ns) | Discarded partial changes |','|---|---:|---:|---:|']
    for name,case in report['cases'].items():
        d=case['latency_delta_ns']['latency_ns']
        lines.append(f"| {name} | {case['committed_rgb_compared']} | {d['min']} / {d['max']} | {len(case['discarded_partial_changes'])} |")
    lines += ['', '## Retained views', '']
    if 'build_new_vs_old' in report['links']:
        lines.append(f"- [New build A / previous build B]({report['links']['build_new_vs_old']})")
    for case in CASES:
        links=report['links'][case]
        if 'new_pixel_a_old_pixel_b' in links:
            lines.append(f"- [{case}: new pixels A / previous pixels B]({links['new_pixel_a_old_pixel_b']})")
        else:
            lines.append(f"- {case}: [before]({links['before']}) / [after]({links['after']}) (different API stores; no cross-run overlay)")
    if report['errors']:
        lines += ['', '## Regressions', ''] + ['- '+error for error in report['errors']]
    lines += ['',report['scope'], '']
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',default='docs/validation/adas-module-rtl-20261017.json')
    parser.add_argument('--after',default='docs/validation/adas-module-timing-closure-20261017.json')
    parser.add_argument('--after-build-receipt',help='Bind an independently executed OOC job from an adas-preboard-validation/1 receipt by exact design hash')
    parser.add_argument('--output',default='docs/validation/adas-module-timing-closure-20261017-comparison.json')
    parser.add_argument('--verify-shared-store',action='store_true',help='Read old/new job manifest hashes from the new API before emitting cross-run links')
    parser.add_argument('--token',default=None)
    args=parser.parse_args(argv)
    output=Path(args.output); prose=output.with_suffix('.md')
    require(not output.exists() and not prose.exists(), 'Comparison evidence exists; choose a new output path')
    before,after=load_campaign(args.before),load_campaign(args.after,args.after_build_receipt)
    shared=verify_shared_store(before,after,args.token) if args.verify_shared_store else None
    report=compare_campaigns(before,after,shared)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    with prose.open('x') as stream:
        stream.write(markdown(report))
    print(json.dumps(dict(verdict=report['verdict'],gates=report['validation_gates'],errors=len(report['errors']),
        fmax_before=report['build']['before']['fmax_mhz'],fmax_after=report['build']['after']['fmax_mhz'],report=str(output))))
    return 0 if report['verdict']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
