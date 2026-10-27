"""Authored offline evidence tests. No CARLA engine, remote host or database."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_carla_throttle_isolation as analysis
import analyze_carla_brake_response as brake_analysis
import carla_brake_protocol as brake
import carla_throttle_isolation_protocol as protocol
from test_analyze_carla_brake_response import fixture, observation, command, available, seal


def set_position(row, x, intent):
    row["ego"]["position_m"]["x"] = row["ego"]["snapshot_transform"]["position_m"]["x"] = x
    row["ego"]["snapshot_transform"]["matrix_native"][0][3] = x
    source_protocol = intent["protocol"].get("controller_protocol", intent["protocol"])
    row["bounds"] = brake.spatial_check(row["ego"]["position_m"], row["ego"]["rotation_deg"], row["ego"]["velocity_mps"],
        intent["spawn_transform"], phase=row["phase"], protocol=source_protocol)


def rotating(row, rpm=500.):
    row["reported_control"]["data"]["gear"] = 1
    row["telemetry"]["scalars"]["engine_rpm"] = available(rpm)
    row["telemetry"]["wheels"] = [{"index": i, "fields": {"omega": available(6.)}} for i in range(4)]


def reference_fixture(tmp_path):
    root = tmp_path / "reference"; root.mkdir()
    directory, sources, original, intent, report = fixture(root, initialization="throttle", protocol_version=brake.VERSION)
    p = brake.make_protocol("throttle", version=brake.SETTLED_VERSION)
    intent.update(protocol=p, protocol_sha256=analysis.sha(brake.canonical(p)), sequence_sha256=analysis.sha(brake.canonical(brake.measurement_sequence(p))))
    report.update(protocol_sha256=intent["protocol_sha256"], sequence_sha256=intent["sequence_sha256"], intent_sha256=analysis.sha(brake.canonical(intent)),
        installed_module_hashes={name: str(i + 1) * 64 for i, name in enumerate(protocol.MODULES)}, inventory_refresh_ticks=0)
    for row in report["rest"]["observations"]: set_position(row, 0., intent)
    first = deepcopy(report["rest"]["observations"][-1]);first.update(index=0, phase="preparation");set_position(first, 0., intent)
    prep = dict(mode="throttle", status="running", commands=[], observations=[first], velocity_request=None,
                pi_steps=[], pi_state=None, entry_trace=[brake.settled_preparation_gate(p, [first], intent["spawn_transform"])])
    for i in range(225):
        step = brake.settled_preparation_step(p, prep["observations"][-1], prep["pi_state"])
        row = observation(i+1, "preparation", first["world_frame"]+i+1, 2., step["control"], intent["spawn_transform"], first["ego"]["role_name"], protocol=p)
        set_position(row, (i+1)*12./225, intent);rotating(row, 600. if i==224 else 500.)
        prep["commands"].append(command(step["control"], i, i*.05, prep["observations"][-1], row))
        prep["observations"].append(row);prep["pi_steps"].append(step["trace"]);prep["pi_state"]=step["state"]
        prep["entry_trace"].append(brake.settled_preparation_gate(p, prep["observations"], intent["spawn_transform"]))
    prep["entry_gate"] = deepcopy(prep["entry_trace"][-1]); assert not prep["entry_gate"]["admitted"] and prep["entry_gate"]["crossed_plane_now"]
    report["preparation"] = prep
    report["measurement"] = {"expected_commands":80,"expected_observations":81,"observations":[],
        "commands":[command(s["control"], s["step"], s["time_s"]) for s in brake.measurement_sequence(p)]}
    report.update(status="failed", error={"message":"AUTHORED first crossing rejected: raw RPM changed"})
    report["cleanup"].update(observed=deepcopy(report["original"]))
    seal(directory,intent,report)
    reference = analysis.load_reference(directory,sources,original)
    return directory,sources,original,reference


def isolation_fixture(tmp_path, reference, branch="continue_pi"):
    root=tmp_path/branch;root.mkdir()
    source_dir=root/'sources';source_dir.mkdir()
    source_files={}
    for name in protocol.SOURCE_FILES:
        content=("# AUTHORED new source descriptor, never executed: "+name).encode()
        (source_dir/name).write_bytes(content);source_files[name]=analysis.sha(content)
    p=protocol.make_protocol(reference['intent']['protocol'],branch,225)
    intent=deepcopy(reference['intent'])
    intent.update(schema_version=protocol.INTENT_VERSION,experiment_id=str(uuid.uuid4()),protocol=p,
        protocol_sha256=analysis.sha(brake.canonical(p)),sequence_sha256=analysis.sha(brake.canonical(protocol.sequence_descriptor(p,reference['reference']))),
        reference=deepcopy(reference['reference']),source_files=source_files)
    protocol.validate_intent(intent)
    report=deepcopy(reference['report'])
    report.update(schema_version=protocol.REPORT_VERSION,experiment_id=intent['experiment_id'],diagnostic_id='diagnostic_brake_'+intent['experiment_id'].replace('-',''),
        intent_sha256=analysis.sha(brake.canonical(intent)),protocol_sha256=intent['protocol_sha256'],sequence_sha256=intent['sequence_sha256'],
        source_files=source_files,isolation_protocol=p,reference=deepcopy(reference['reference']),status='collected',error=None)
    role='axiom_brake_'+intent['experiment_id'].replace('-','')+'_ego'
    report['spawn']['ego']['returned_identity']['role_name']=role
    for row in report['rest']['observations']:row['ego']['role_name']=role
    prep={'mode':'throttle','status':'passed','expected_commands':225,'expected_observations':226,
        'commands':[],'observations':[],'pi_steps':[],'pi_state':None,'initial_gate':None,'handoff_gate':None,'velocity_request':None}
    for row in reference['report']['preparation']['observations']:
        row=deepcopy(row);row['ego']['role_name']=role;prep['observations'].append(row)
    prep['initial_gate']=protocol.compare_entry(p,prep['observations'][0],reference['reference']['initial_observation'],rotating=False)
    for i in range(225):
        step=protocol.prefix_step(p,intent['reference'],i,prep['observations'][i],prep['pi_state']);assert step['matched']
        prep['pi_steps'].append({'command_index':i,**step});prep['pi_state']=step['state']
        prep['commands'].append(command(step['control'],i,i*.05,prep['observations'][i],prep['observations'][i+1]))
    prep['handoff_gate']=protocol.handoff_gate(p,intent['reference'],prep['observations'],len(report['rest']['trace']),0)
    report['preparation']=prep
    first=deepcopy(prep['observations'][-1]);first.update(phase='measurement',index=0)
    measurement={'expected_commands':40,'expected_observations':41,'commands':[],'observations':[first],
        'control_steps':[],'pi_state':deepcopy(prep['pi_state']),'entry_observation_frame':first['world_frame'],
        'handoff_gate':protocol.handoff_gate(p,intent['reference'],[*prep['observations'][:-1],first],len(report['rest']['trace']),0)}
    for i in range(40):
        step=protocol.extension_step(p,i,measurement['observations'][-1],measurement['pi_state'],intent['reference']['controls'][-1])
        speed=2.+(.002 if i%2 else -.002) if branch=='continue_pi' else 2.
        row=observation(i+1,'measurement',first['world_frame']+i+1,speed,step['control'],intent['spawn_transform'],role,protocol=p['controller_protocol'])
        set_position(row,12.+(i+1)*.1,intent);rotating(row,600.)
        measurement['commands'].append(command(step['control'],i,i*.05,measurement['observations'][-1],row))
        measurement['control_steps'].append({'command_index':i,**step});measurement['pi_state']=step['state'];measurement['observations'].append(row)
    report['measurement']=measurement
    capture=root/'capture';capture.mkdir();seal(capture,intent,report)
    return capture,source_dir,intent,report


@pytest.fixture
def captures(tmp_path):
    ref=reference_fixture(tmp_path)
    return ref,isolation_fixture(tmp_path,ref[3]),isolation_fixture(tmp_path,ref[3],'hold_throttle')


def test_full_prefix_and_both_extensions_recompute_without_source_execution(captures,tmp_path):
    ref,pi,hold=captures
    result=analysis.build_report([pi[0],hold[0]],[pi[1]],ref[0],ref[1],ref[2],tmp_path/'output')
    assert result['counts']=={'trials':2,'verified':2,'invalid':0,'collected':2,'failed_prefixes':0}
    assert result['comparison']['status']=='comparable',result['comparison']
    assert len(result['comparison']['aligned_samples'])==41
    assert len(result['analysis_tools'])==5
    assert (tmp_path/'output/analysis-tools/analyze_carla_throttle_isolation.py').read_bytes()==Path(analysis.__file__).read_bytes()
    for trial in result['trials']:
        assert trial['prefix']['completed_transitions']==225
        assert trial['extension']['command_states']=={'observed_next_frame':40}
        assert len(trial['samples'])==41 and trial['samples'][0]['interval_dv_dt_mps2'] is None
        assert trial['samples'][-1]['outgoing_requested_throttle'] is None
        assert trial['source_identity']['historical_shared_files_differing']==list(protocol.BASE_SOURCES)
        assert trial['reservation_release']['status']=='release_receipt_verified'
    assert result['trials'][0]['statistics']['extension']['signed_speed_mps']['difference_sign_changes']>30
    assert result['trials'][1]['statistics']['extension']['signed_speed_mps']['difference_sign_changes']==0
    assert (tmp_path/'output/trial-01/report.json').read_bytes()==(pi[0]/'report.json').read_bytes()
    page=(tmp_path/'output/index.html').read_text()
    assert '<svg id="omega"' in page and '<svg id="rpm"' in page and 'fetch(' not in page
    assert 'Original evidence downloads' in page and 'Inspect gates' in page and 'overflow-wrap:anywhere' in page


def test_sign_changes_have_no_deadband_and_do_not_bridge_zero_or_missing():
    result=analysis.sequence_summary([0.,1e-12,0.,None,4.,4.,3.,4.,3.])
    assert result['difference_sign_changes']==3
    assert result['unknown']==1 and result['adjacent_differences'][4] is None
    assert result['adjacent_differences'][5]==0.


@pytest.mark.parametrize('change,reason',[
    ('proposal','proposal/state/trace'),('state','committed state'),('control','source control'),
    ('null_control','Proposed control'),('frame','frame/time gap'),('hash','hash'),('unsealed','Unsealed'),('source','bundle')])
def test_tampering_is_not_accepted_even_with_resealed_report(captures,change,reason):
    ref,pi,_=captures;capture,sources,intent,report=pi
    if change=='proposal':report['preparation']['pi_steps'][2]['state']['integral']+=.01
    if change=='state':
        report['measurement']['pi_state']=deepcopy(report['measurement']['pi_state'])
        report['measurement']['pi_state']['integral']+=.01
    if change=='control':report['preparation']['commands'][2]['control']['throttle']+=.01
    if change=='null_control':report['measurement']['commands'][2]['control']=None
    if change=='frame':report['measurement']['observations'][8]['world_frame']+=1
    seal(capture,intent,report)
    if change=='hash':(capture/'events.jsonl').write_bytes((capture/'events.jsonl').read_bytes().replace(b'diagnostic_started',b'diagnostic_startXd',1))
    if change=='unsealed':
        report['event_chain']['sealed']=False;(capture/'report.json').write_bytes(brake.canonical(report))
    if change=='source':(sources/protocol.SOURCE_FILES[0]).write_bytes(b'# corruption')
    with pytest.raises((analysis.InvalidEvidence,ValueError),match=reason):analysis.load_trial(capture,[sources],ref[3])


def trim_extension(report, intent, completed, *, proposed=True):
    m=report['measurement'];m['observations']=m['observations'][:completed+1]
    m['control_steps']=m['control_steps'][:completed+int(proposed)]
    m['pi_state']=deepcopy(m['control_steps'][completed-1]['state'] if completed else report['preparation']['pi_state'])
    if proposed:
        m['commands'][completed].update(application_status='send_outcome_unknown',next_world_frame=None,next_observation_index=None,rpc_response=None,rpc_after_world_frame=None)
    for i in range(completed+int(proposed),40):m['commands'][i]=command(None,i,i*.05)
    report.update(status='failed',error={'message':'AUTHORED extension interruption'})


def test_failed_extension_retains_proposed_control_without_state_commit(captures):
    ref,pi,_=captures;capture,source,intent,report=pi
    trim_extension(report,intent,3)
    seal(capture,intent,report)
    result,_=analysis.load_trial(capture,[source],ref[3])
    assert result['extension']['proposals']==4 and result['extension']['completed_transitions']==3
    assert result['extension']['observations']==4
    assert result['extension']['command_states']=={'observed_next_frame':3,'send_outcome_unknown':1,'not_attempted':36}
    assert len(result['samples'])==4 and result['samples'][-1]['outgoing_attempted_throttle'] is not None
    report['measurement']['pi_state']=deepcopy(report['measurement']['control_steps'][-1]['state'])
    seal(capture,intent,report)
    with pytest.raises(analysis.InvalidEvidence,match='committed state'):analysis.load_trial(capture,[source],ref[3])


def test_fresh_handoff_failure_keeps_null_unproposed_full_denominator(captures):
    ref,pi,_=captures;capture,source,intent,report=pi
    m=report['measurement'];first=m['observations'][0];first['telemetry']=None
    m.update(observations=[first],control_steps=[],pi_state=None,commands=[command(None,i,i*.05) for i in range(40)],
        handoff_gate=protocol.handoff_gate(intent['protocol'],intent['reference'],[*report['preparation']['observations'][:-1],first],len(report['rest']['trace']),0))
    report.update(status='failed',error={'message':'AUTHORED fresh telemetry unavailable'})
    seal(capture,intent,report)
    result,_=analysis.load_trial(capture,[source],ref[3])
    assert result['extension']['command_states']=={'not_attempted':40}
    assert result['samples'][0]['outgoing_requested_throttle'] is None
    assert result['samples'][0]['raw_engine_rpm'] is None
    assert result['handoff']['fresh']['admitted'] is False
    m['pi_state']=deepcopy(report['preparation']['pi_state'])
    seal(capture,intent,report)
    with pytest.raises(analysis.InvalidEvidence,match='state initialized after rejected'):
        analysis.load_trial(capture,[source],ref[3])


def test_full_replay_rejected_handoff_preserves_reference_failure_and_no_extension(captures):
    ref,pi,_=captures;capture,source,intent,report=pi
    prep=report['preparation'];prep['status']='running'
    prep['observations'][-1]['telemetry']['scalars']['engine_rpm']['value']*=2
    prep['handoff_gate']=protocol.handoff_gate(intent['protocol'],intent['reference'],prep['observations'],len(report['rest']['trace']),0)
    report['measurement'].update(observations=[],control_steps=[],pi_state=None,handoff_gate=None,entry_observation_frame=None,
        commands=[command(None,i,i*.05) for i in range(40)])
    report.update(status='failed',error={'message':'AUTHORED handoff rejected'})
    seal(capture,intent,report)
    result,_=analysis.load_trial(capture,[source],ref[3])
    assert result['prefix']['completed_transitions']==225 and result['extension']['observations']==0
    assert result['handoff']['prefix']['admitted'] is False
    assert ref[3]['report']['preparation']['entry_gate']['admitted'] is False


def test_matched_false_proposal_may_be_retained_but_never_dispatched(captures):
    ref,pi,_=captures;capture,source,intent,report=pi
    prep=report['preparation'];prep['observations']=prep['observations'][:2]
    source_row=prep['observations'][-1]
    source_row['ego']['velocity_mps']['x']=1.99;source_row['signed_heading_speed_mps']=1.99
    set_position(source_row,source_row['ego']['position_m']['x'],intent)
    state=deepcopy(prep['pi_steps'][0]['state'])
    proposal=protocol.prefix_step(intent['protocol'],intent['reference'],1,source_row,state)
    assert not proposal['matched']
    prep.update(status='running',pi_state=state,pi_steps=[prep['pi_steps'][0],{'command_index':1,**proposal}],handoff_gate=None)
    for i in range(1,225):prep['commands'][i]=command(intent['reference']['controls'][i],i,i*.05)
    report['measurement'].update(observations=[],control_steps=[],pi_state=None,handoff_gate=None,entry_observation_frame=None,
        commands=[command(None,i,i*.05) for i in range(40)])
    report.update(status='failed',error={'message':'AUTHORED mismatch before dispatch'})
    seal(capture,intent,report)
    result,_=analysis.load_trial(capture,[source],ref[3])
    assert result['prefix']['proposals']==2 and result['prefix']['completed_transitions']==1
    prep['commands'][1].update(application_status='send_outcome_unknown',world_frame=source_row['world_frame'],rpc_before_world_frame=source_row['world_frame'])
    seal(capture,intent,report)
    with pytest.raises(analysis.InvalidEvidence,match='Unmatched prefix'):analysis.load_trial(capture,[source],ref[3])


def test_direct_pair_requires_cross_branch_agreement_not_only_anchor(captures):
    ref,pi,hold=captures
    a,_=analysis.load_trial(pi[0],[pi[1]],ref[3]);b,_=analysis.load_trial(hold[0],[hold[1]],ref[3])
    for trial,offset in ((a,.01),(b,-.01)):
        trial['pair_evidence']['entry']['ego']['position_m']['x']+=offset
        assert protocol.compare_entry(trial['pair_evidence']['intent']['protocol'],trial['pair_evidence']['entry'],ref[3]['reference']['entry_observation'])['admitted']
    result=analysis.compare_trials([a,b])
    assert result['status']=='not_comparable' and result['aligned_samples']==[]
    assert any('Direct branch-to-branch' in s for s in result['mismatches'])


@pytest.mark.parametrize('change',['source','history','gear','timing','duplicate_branch','extra_failed'])
def test_pairing_preserves_mismatches_and_never_selects_a_convenient_subset(captures,change):
    ref,pi,hold=captures
    a,_=analysis.load_trial(pi[0],[pi[1]],ref[3]);b,_=analysis.load_trial(hold[0],[hold[1]],ref[3])
    trials=[a,b]
    if change=='source':b['pair_evidence']['intent']['source_files'][protocol.SOURCE_FILES[-1]]='f'*64
    if change=='history':b['pair_evidence']['prefix_controls'][1]['throttle']+=.001
    if change=='gear':b['pair_evidence']['prefix_gears'][5]=2
    if change=='timing':b['samples'][10]['time_s']+=.001
    if change=='duplicate_branch':b['branch']='continue_pi'
    if change=='extra_failed':trials.append({'integrity_status':'invalid_evidence','verification_error':'Authored error'})
    result=analysis.compare_trials(trials)
    assert result['status']=='not_comparable' and result['aligned_samples']==[] and result['mismatches']


def test_duplicate_json_reference_tamper_and_exclusive_output(captures,tmp_path):
    ref,pi,_=captures
    with pytest.raises(analysis.InvalidEvidence,match='Duplicate'):analysis.strict_json(b'{"x":1,"x":2}')
    before={name:(ref[0]/name).read_bytes() for name in protocol.CAPTURE_FILES}
    output=tmp_path/'exclusive'
    analysis.build_report([pi[0]],[pi[1]],ref[0],ref[1],ref[2],output)
    assert before=={name:(ref[0]/name).read_bytes() for name in protocol.CAPTURE_FILES}
    with pytest.raises(analysis.InvalidEvidence,match='exists'):analysis.build_report([pi[0]],[pi[1]],ref[0],ref[1],ref[2],output)
    (ref[0]/'report.json').write_bytes(before['report.json']+b' ')
    # Even parse-equivalent report changes invalidate its original release SHA.
    with pytest.raises(analysis.InvalidEvidence,match='receipt'):analysis.load_reference(ref[0],ref[1],ref[2])


def test_self_contained_html_javascript_parses():
    node=shutil.which('node')
    if node is None:pytest.skip('Node is needed for offline JavaScript syntax check')
    code=analysis.HTML.split('</script><script>')[1].split('</script>')[0]
    result=subprocess.run([node,'--check'],input=code,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_native_serializer_interoperability_with_prescribed_world(tmp_path,monkeypatch):
    # Independent runner/recorder with authored world vectors, not native physics.
    from types import SimpleNamespace
    from axiom_carla.startup import StartupPolicy,_actor_state
    import test_diagnose_carla_brake_response as base_fixture
    import test_diagnose_carla_throttle_isolation as native_fixture
    module=base_fixture.diagnostic_module.__wrapped__(monkeypatch)
    env=base_fixture.environment.__wrapped__(module,tmp_path,SimpleNamespace(param=base_fixture.SETTLED))
    source,world,module=next(env)
    source.native.update(StartupPolicy=StartupPolicy,_actor_state=_actor_state)
    isolation=native_fixture.isolation_environment.__wrapped__((source,world,module),monkeypatch,tmp_path)
    runner,world,module,_=next(isolation)
    try:
        initial,_=native_fixture.fresh_start((runner,world,module,source),monkeypatch)
        runner.measure(runner.prepare(initial));runner.cleanup();assert runner.finalize()==0
        report=analysis.strict_json((runner.output/'report.json').read_bytes())
        common_events=brake_analysis.verify_chain((runner.output/'events.jsonl').read_bytes(),report,runner.intent)
        result=analysis.analyze_report(report,runner.intent)
        assert len(common_events)>1000 and result['extension']['completed_transitions']==40
        assert result['prefix']['completed_transitions']==225
    finally:
        isolation.close();env.close()


def test_boolean_actor_id_is_not_a_valid_native_identity(captures):
    ref,pi,_=captures
    intent,report=pi[2:]
    actor=deepcopy(report['spawn']['ego']['returned_identity']);actor['actor_id']=True
    row=deepcopy(report['preparation']['observations'][0]);row['ego']['actor_id']=True
    with pytest.raises(analysis.InvalidEvidence,match='Invalid spawned actor'):
        analysis.verify_observations([row],'preparation',226,actor,intent)


def test_real_javascript_trial_switch_clears_hover_and_keeps_zero_throttle(captures,tmp_path):
    node=shutil.which('node')
    if node is None:pytest.skip('Node needed for offline interaction handler check')
    ref,pi,hold=captures
    result=analysis.build_report([pi[0],hold[0]],[pi[1]],ref[0],ref[1],ref[2],tmp_path/'interactive')
    harness='''const assert=require('node:assert/strict');const nodes={};const document={getElementById(id){return nodes[id]??=({value:'measurement',innerHTML:'',textContent:'',clientWidth:600,
    addEventListener(kind,fn){this['on'+kind]=fn},setAttribute(){},getBoundingClientRect(){return {left:0,width:600}}})}};const window={addEventListener(){}};document.getElementById('payload').textContent=JSON.stringify(REPORT);'''.replace('REPORT',json.dumps(result))
    code=analysis.HTML.split('</script><script>')[1].split('</script>')[0]
    check='''
    assert.match(document.getElementById('samples').innerHTML,/continue_pi/);
    document.getElementById('throttle').onpointermove({clientX:150});
    assert.match(document.getElementById('cursor').textContent,/world/);
    document.getElementById('trials').onchange({target:{dataset:{index:'0'},checked:false}});
    assert.equal(document.getElementById('cursor').textContent,'');
    assert.doesNotMatch(document.getElementById('samples').innerHTML,/continue_pi/);
    assert.match(document.getElementById('samples').innerHTML,/hold_throttle/);
    document.getElementById('phase').value='preparation';document.getElementById('phase').onchange();
    assert.match(document.getElementById('samples').innerHTML,/preparation 225/);
    '''
    done=subprocess.run([node],input=harness+code+check,text=True,capture_output=True,timeout=20)
    assert done.returncode==0,done.stderr


@pytest.mark.parametrize('values,changes,pairs', [([1.,-1.,1.],2,2),([1.,0.,-1.],0,0),([1.,None,-1.,1.],1,1)])
def test_sample_sign_changes_are_distinct_from_difference_changes(values,changes,pairs):
    result=analysis.sequence_summary(values)
    assert result['sample_sign_changes']==changes
    assert result['assessable_adjacent_sample_pairs']==pairs
    assert 'difference_sign_changes' in result
