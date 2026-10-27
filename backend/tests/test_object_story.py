"""Guided-story selection from authored sealed fixtures, not native simulation."""
import hashlib
import json
import types
import copy
from pathlib import Path
import pytest
from ade2e.experiment_runtime import ExperimentExecution, ExperimentOutputFile
from ade2e.object_story import _segments, RULE_VERSION
from test_object_demo import env, save, execute


def event(time_s, category, actor='ego'):
    kind,reason={'wait':('decision_changed','gap_wait'),'bypass':('route_replanned','obstacle_bypass'),
        'return':('action_completed','automatic_bypass_rejoined'),'arrived':('decision_changed','arrived'),
        'blocked':('decision_changed','lane_changes_disabled')}[category]
    index=round(time_s/.05)
    return {'time_s':time_s,'actor_id':actor,'type':kind,'reason':reason,'action_id':None,'route_revision':1,
            'index':index,'input_frame':1000+index,'provenance':'observed_state_reactive_planner'}


@pytest.fixture
def story_env(env):
    db,client,handler,registry,scenario=env
    scenario['duration_s']=60
    handler.story_events=[event(5,'wait'),event(15,'bypass'),event(25,'return'),event(45,'arrived'),event(55,'blocked')]
    handler.scene_damage=None
    handler.story_frame_actors={}
    def output(self,snapshot,work_dir,should_cancel):
        dt=snapshot['scenario']['timestep_s'];count=round(snapshot['scenario']['duration_s']/dt)
        scene={'schema_version':'carla-city-scene/1','fidelity':'authored_demo_contract',
            'coordinate_system':snapshot['scenario']['coordinate_system'],
            'source':{**snapshot['source'],'adapter_version':'1','planner_sha256':self.planner_hash},
            'frames':[{'index':i,'time_s':(i+1)*dt,'input_frame':1000+i,
                **({'actors':self.story_frame_actors[i]} if i in self.story_frame_actors else {})} for i in range(count)],
            'planner_events':self.story_events}
        if self.scene_damage=='source':scene['source']['configuration_hash']='0'*64
        if self.scene_damage=='clock':scene['frames'][10]['time_s']+=1
        if self.scene_damage=='event_clock':scene['planner_events']=[{**event(5,'wait'),'time_s':5.025}]
        if self.scene_damage=='partial':scene['frames'].pop()
        path=work_dir/'city-scene.json';raw=json.dumps(scene).encode();path.write_bytes(raw)
        return ExperimentExecution(summary={'scene_artifact':'city-scene.json','scene_sha256':hashlib.sha256(raw).hexdigest(),
            'metrics':[]},artifacts=[ExperimentOutputFile(path,path.name,'application/json')],fidelity='authored_demo_contract')
    handler.execute=types.MethodType(output,handler)
    return env


def story(client,left,right=None,project='default'):
    params={'project_id':project,'left_execution_id':left}
    if right is not None:params['right_execution_id']=right
    response=client.get('/api/v1/object-sim/story',params=params)
    return response


def test_single_story_uses_original_hashes_events_and_budget(story_env):
    _,client,_,_,scenario=story_env;ref=save(client,scenario);identity=execute(story_env,ref)
    response=story(client,identity);assert response.status_code==200,response.text
    value=response.json();assert value['status']=='available' and value['rule_version']==RULE_VERSION
    assert len(value['segments'])==5 and value['total_presentation_s']==40
    assert value['right'] is None and value['sources']['right'] is None
    assert value['sources']['left']['scenario_ref']==ref
    assert value['sources']['left']['manifest_hash']==value['left']['execution']['manifest_hash']
    for segment in value['segments']:
        assert segment['hold_s']==2 and segment['playback_rate']==1
        assert segment['start_time_s']==pytest.approx(segment['anchor_time_s']-2)
        assert segment['end_time_s']==pytest.approx(segment['anchor_time_s']+4)
        assert segment['events'][0]['provenance']=='observed_state_reactive_planner'
    assert story(client,identity).json()==value


def test_ab_keeps_shared_event_clock_and_merges_overlap(story_env):
    _,client,handler,_,scenario=story_env
    handler.story_events=[event(5,'wait'),event(7,'bypass')]
    ref=save(client,scenario);left=execute(story_env,ref)
    handler.story_events=[event(6,'wait'),event(8,'bypass')]
    right=execute(story_env,ref,'right')
    value=story(client,left,right).json();assert value['status']=='available',value
    assert value['clock']=='shared_simulation_time_s'
    assert len(value['segments'])==1
    segment=value['segments'][0]
    assert (segment['start_time_s'],segment['end_time_s'],segment['anchor_time_s'])==pytest.approx((3.05,11.05,5.05))
    assert [(e['side'],e['time_s']) for e in segment['events']]==[('left',5),('right',6),('left',7),('right',8)]
    assert value['total_presentation_s']==10


def test_representative_selection_prefers_ego_and_does_not_invent_missing_category(story_env):
    _,client,handler,_,scenario=story_env
    handler.story_events=[event(1,'wait','rear'),event(4,'wait','ego'),event(9,'wait','ego')]
    ref=save(client,scenario);identity=execute(story_env,ref)
    value=story(client,identity).json();assert value['status']=='available'
    assert len(value['segments'])==1 and value['segments'][0]['actor_ids']==['ego']
    assert value['segments'][0]['anchor_time_s']==4.05
    assert {e['category'] for e in value['segments'][0]['events']}=={'wait'}


@pytest.mark.parametrize('damage,reason',[('source','provenance_mismatch'),('clock','clock_invalid'),
    ('event_clock','event_clock_or_identity_invalid'),('partial','clock_incomplete')])
def test_invalid_original_evidence_reports_unavailable(story_env,damage,reason):
    _,client,handler,_,scenario=story_env;handler.scene_damage=damage
    ref=save(client,scenario);identity=execute(story_env,ref)
    value=story(client,identity).json()
    assert value['status']=='unavailable' and reason in value['reason']
    assert value['segments']==[] and value['total_presentation_s']==0


def test_missing_artifact_and_pending_execution_keep_manual_fallback(story_env):
    db,client,_,_,scenario=story_env;ref=save(client,scenario)
    pending=execute(story_env,ref,'pending',complete=False)
    value=story(client,pending).json()
    assert value['status']=='unavailable' and value['reason']=='completed_sealed_execution_required'
    assert value['left']['execution']['status']=='queued' and value['manual_replay_available']
    complete=execute(story_env,ref,'complete')
    detail=client.get('/api/v1/experiments/executions/'+complete).json()
    (db.experiment_dir/'artifacts'/detail['artifacts'][0]['id']).unlink()
    value=story(client,complete).json()
    assert value['status']=='unavailable' and value['reason']=='original_city_scene_integrity_failed'


def test_unrecognized_events_do_not_generate_fictional_story(story_env):
    _,client,handler,_,scenario=story_env
    handler.story_events=[{**event(2,'wait'),'reason':'route_follow'}]
    ref=save(client,scenario);identity=execute(story_env,ref)
    value=story(client,identity).json()
    assert value['reason']=='no_representative_planner_events' and not value['segments']


def test_ab_build_difference_returns_unavailable(story_env):
    _,client,handler,_,scenario=story_env;ref=save(client,scenario);left=execute(story_env,ref)
    handler.planner_hash='b'*64;right=execute(story_env,ref,'right')
    value=story(client,left,right).json()
    assert value['status']=='unavailable' and value['reason'].startswith('comparison_incompatible:')


def test_story_project_viewer_can_read_but_cross_project_cannot(story_env):
    _,client,_,_,scenario=story_env;ref=save(client,scenario);identity=execute(story_env,ref)
    token=client.post('/api/v1/projects/default/tokens',json={'role':'Viewer','user_label':'story-viewer'}).json()['token']
    client.headers['Authorization']='Bearer '+token
    assert story(client,identity).status_code==200
    assert story(client,identity,project='other').status_code==404


def test_window_clips_to_zero_and_recorded_end_and_caps_five():
    selected=[]
    for index,category in enumerate(('wait','bypass','return','arrived','blocked')):
        selected.append({**event(index*10,category),'side':'left','category':category,'observation_time_s':index*10+.05})
    segments,total=_segments(selected,40.5,{'left':{'artifact_sha256':'a'*64},'right':None},.05)
    assert len(segments)==5 and total<=60
    assert segments[0]['start_time_s']==.05 and segments[-1]['end_time_s']==40.5


def test_command_zero_event_displays_first_real_response_frame(story_env):
    _,client,handler,_,scenario=story_env
    original=event(0,'bypass')
    handler.story_events=[original]
    ref=save(client,scenario);left=execute(story_env,ref)
    handler.story_events=[event(.1,'bypass')]
    right=execute(story_env,ref,'delayed')
    value=story(client,left,right).json()
    assert value['status']=='available'
    assert value['event_clock']=='command_index_times_timestep'
    assert value['observation_clock']=='retained_response_frame_time_s'
    segment=value['segments'][0]
    assert segment['start_time_s']==segment['anchor_time_s']==.05
    assert segment['end_time_s']==4.05
    observed={item['side']:item for item in segment['events']}
    # The command-time event and input observation identity remain unchanged.
    assert all(observed['left'][key]==val for key,val in original.items())
    assert observed['left']['observation_time_s']==.05
    assert observed['right']['time_s']==.1
    assert observed['right']['observation_time_s']==pytest.approx(.15)
    assert observed['left']['observation_time_s']!=observed['right']['observation_time_s']


def test_authored_wait_label_requires_original_actor_action_and_reason(story_env):
    _,client,handler,_,scenario=story_env
    scenario['actors'][0]['behavior']={'actions':[{'id':'brief_wait','kind':'wait','duration_s':2}]}
    original={**event(3,'wait'),'reason':'wait','action_id':'brief_wait'}
    handler.story_events=[original]
    ref=save(client,scenario);identity=execute(story_env,ref)
    value=story(client,identity).json()
    assert value['status']=='available' and value['rule_version']==RULE_VERSION
    segment=value['segments'][0]
    assert segment['title']=='설정한 대기'
    assert segment['description']=='시나리오에 설정한 wait 명령의 실행을 planner가 기록했습니다.'
    assert all(segment['events'][0][key]==val for key,val in original.items())
    assert segment['events'][0]['category']=='wait' and segment['anchor_time_s']==pytest.approx(3.05)


@pytest.mark.parametrize('event_actor,action_id,action_kind',[
    ('ego',None,'wait'),('ego','unknown_action','wait'),('rear','brief_wait','wait'),('ego','brief_wait','route_follow')])
def test_unconfirmed_wait_stays_neutral(story_env,event_actor,action_id,action_kind):
    _,client,handler,_,scenario=story_env
    scenario['actors'][0]['behavior']={'actions':[{'id':'brief_wait','kind':action_kind}]}
    handler.story_events=[{**event(3,'wait',event_actor),'reason':'wait','action_id':action_id}]
    ref=save(client,scenario);identity=execute(story_env,ref)
    value=story(client,identity).json();assert value['status']=='available'
    segment=value['segments'][0]
    assert segment['title']=='대기'
    assert '원인을 확정하지 않습니다' in segment['description']
    assert '설정한' not in segment['description'] and '주변 상태' not in segment['description']


def test_mixed_ab_authored_wait_and_actual_gap_keep_distinct_labels(story_env):
    _,client,handler,_,scenario=story_env
    scenario['actors'][0]['behavior']={'actions':[{'id':'brief_wait','kind':'wait','duration_s':2}]}
    handler.story_events=[{**event(3,'wait'),'reason':'wait','action_id':'brief_wait'}]
    ref=save(client,scenario);left=execute(story_env,ref)
    # An observed gap can override the reason while the wait action is active.
    handler.story_events=[{**event(4,'wait'),'action_id':'brief_wait'}]
    right=execute(story_env,ref,'gap')
    value=story(client,left,right).json();assert value['status']=='available'
    assert len(value['segments'])==1
    segment=value['segments'][0]
    assert segment['title']=='설정한 대기 → 양보·간격 대기'
    assert '설정한 wait 명령' in segment['description'] and '주변 상태를 바탕으로' in segment['description']
    assert [(e['side'],e['reason'],e['time_s']) for e in segment['events']]==[('left','wait',3),('right','gap_wait',4)]
    # A standalone gap remains the existing gap story, without an authored claim.
    gap=story(client,right).json()['segments'][0]
    assert gap['title']=='양보·간격 대기' and '설정한' not in gap['description']


def test_unknown_wait_description_does_not_claim_authored_or_reactive_cause():
    selected=[{**event(3,'wait'),'reason':'unknown_wait_reason','category':'wait','side':'left','observation_time_s':3.05}]
    segments,_=_segments(selected,10,{},.05)
    assert segments[0]['title']=='대기'
    assert '원인을 확정하지 않습니다' in segments[0]['description']


def follow_event(time_s, reason='follow_gap'):
    return {**event(time_s,'wait'),'reason':reason}


def follow_frame(*, reason='follow_gap', lead_speed=0., requested=0., stop=True):
    return [{'id':'ego','speed_mps':1.2,'plan':{'reactive':{'reason':reason,'speed_mps':requested,'stop':stop,
        'diagnostics':{'observation_basis':'current_snapshot_ground_truth',
            'lead':{'actor_id':'rear','gap_m':3.25,'speed_mps':lead_speed}}}}},
        # Response speed differs from the preceding input-GT diagnostic on purpose.
        {'id':'rear','speed_mps':lead_speed+.1}, {'id':'parked','speed_mps':0.}]


def test_follow_gap_context_preserves_input_target_and_request_without_authored_wait_claim(story_env):
    _,client,handler,_,scenario=story_env
    scenario['actors'][0]['behavior']={'actions':[{'id':'brief_wait','kind':'wait','duration_s':2}]}
    original={**follow_event(3),'action_id':'brief_wait'}
    handler.story_events=[original];handler.story_frame_actors={60:follow_frame()}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json();assert result['status']=='available'
    segment=result['segments'][0];assert segment['title']=='앞차와 간격 확보'
    assert segment['actor_ids']==['ego','rear'] and segment['anchor_time_s']==pytest.approx(3.05)
    assert '앞차 rear' in segment['description'] and '요청 속도를 0.0m/s' in segment['description']
    assert '설정한' not in segment['description'] and '인지' not in segment['description']
    selected=segment['events'][0]
    assert all(selected[k]==v for k,v in original.items())
    assert selected['decision_context']=={'source':'retained_planner_diagnostics','frame_index':60,
        'input_frame':1060,'target_actor_id':'rear','gap_m':3.25,'lead_speed_mps':0.,
        'requested_speed_mps':0.,'stop':True}
    assert 'previous_decision' not in selected


def test_actual_route_follow_after_follow_gap_is_resume_without_claiming_lead_restart_cause(story_env):
    _,client,handler,_,scenario=story_env
    before=follow_event(2);after=follow_event(12,'route_follow')
    handler.story_events=[before,after]
    handler.story_frame_actors={40:follow_frame(),240:follow_frame(reason='route_follow',lead_speed=2.5,requested=4.,stop=False)}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json();assert result['status']=='available'
    resume=next(s for s in result['segments'] if any(e['category']=='resume' for e in s['events']))
    selected=resume['events'][0]
    assert resume['title']=='경로 추종 판단 재개' and resume['anchor_time_s']==pytest.approx(12.05)
    assert '선행차 재출발' not in resume['description'] and '인지' not in resume['description']
    assert selected['reason']=='route_follow' and selected['category']=='resume'
    assert selected['previous_decision']=={k:before[k] for k in ('reason','index','time_s','input_frame')}
    assert selected['decision_context']['lead_speed_mps']==2.5
    assert selected['decision_context']['requested_speed_mps']==4.
    assert all(selected[k]==v for k,v in after.items())


@pytest.mark.parametrize('intervening',[None,'wait','signal_wait'])
def test_route_follow_does_not_invent_follow_resume_without_immediate_prior_follow_decision(story_env,intervening):
    _,client,handler,_,scenario=story_env
    handler.story_events=([follow_event(2),follow_event(4,intervening)] if intervening else [])+[follow_event(12,'route_follow')]
    handler.story_frame_actors={240:follow_frame(reason='route_follow',lead_speed=3.,requested=4.,stop=False)}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json()
    assert not any(e['category']=='resume' for s in result['segments'] for e in s['events'])


@pytest.mark.parametrize('damage',['missing','foreign_target','same_actor','wrong_reason','boolean_speed','not_gt','duplicate_target'])
def test_missing_or_mismatched_follow_diagnostics_never_fabricate_a_target(story_env,damage):
    _,client,handler,_,scenario=story_env
    states=follow_frame();decision=states[0]['plan']['reactive'];lead=decision['diagnostics']['lead']
    if damage=='missing':del decision['diagnostics']['lead']
    elif damage=='foreign_target':lead['actor_id']='not-in-scene'
    elif damage=='same_actor':lead['actor_id']='ego'
    elif damage=='wrong_reason':decision['reason']='wait'
    elif damage=='boolean_speed':lead['speed_mps']=False
    elif damage=='not_gt':decision['diagnostics']['observation_basis']='image_detection'
    elif damage=='duplicate_target':states.append(copy.deepcopy(states[1]))
    # An untrusted raw auxiliary field must not bypass context reconstruction.
    handler.story_events=[{**follow_event(3),'decision_context':{'target_actor_id':'fabricated'}}]
    handler.story_frame_actors={60:states}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json();assert result['status']=='available'
    segment=result['segments'][0]
    assert segment['actor_ids']==['ego'] and segment['title']=='앞차 간격 조절'
    assert '미보존' in segment['description']
    assert 'decision_context' not in segment['events'][0]


def test_follow_representative_prefers_recorded_stop_request_but_does_not_create_stop_event(story_env):
    _,client,handler,_,scenario=story_env
    handler.story_events=[follow_event(2),follow_event(6,'route_follow'),follow_event(10)]
    handler.story_frame_actors={40:follow_frame(lead_speed=3.,requested=2.,stop=False),
        120:follow_frame(reason='route_follow',lead_speed=3.,requested=4.,stop=False),200:follow_frame()}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json()
    waits=[e for s in result['segments'] for e in s['events'] if e['category']=='wait']
    assert len(waits)==1 and waits[0]['index']==200 and waits[0]['decision_context']['stop'] is True
    # Keeping only the initial decision cannot promote the later stationary
    # frame into a new stop/restart event, even though those poses are retained.
    handler.story_events=[follow_event(2)]
    other=execute(story_env,ref,'constant-reason')
    segment=story(client,other).json()['segments'][0]
    assert segment['title']=='앞차와 간격 확보'
    assert segment['anchor_time_s']==pytest.approx(2.05)
    assert segment['events'][0]['decision_context']['lead_speed_mps']==3.
    assert len(segment['events'])==1


def test_resume_category_never_expands_existing_five_segment_budget():
    selected=[{**event(i*10,category),'side':'left','category':category,'observation_time_s':i*10+.05}
        for i,category in enumerate(('wait','bypass','return','arrived','blocked'))]
    selected.append({**follow_event(55,'route_follow'),'side':'left','category':'resume','observation_time_s':55.05})
    segments,total=_segments(selected,60,{},.05)
    assert len(segments)==5 and total<=60
    assert {e['category'] for s in segments for e in s['events']}=={'wait','bypass','return','arrived','blocked'}


def lead_hold_frame(state='holding', release_reason=None):
    released=state=='released'
    rows=follow_frame(reason='follow_gap' if released else 'follow_lead_stop',
        lead_speed=.4 if released else .02,requested=.6 if released else 0.,stop=not released)
    decision=rows[0]['plan']['reactive'];lead=decision['diagnostics']['lead']
    decision['diagnostics']['lead_hold']={**lead,'state':state,'enter_gap_m':4.,'release_gap_m':4.5,
        'enter_speed_mps':.1,'release_speed_mps':.3}
    if release_reason:
        decision['diagnostics']['lead_hold']['release_reason']=release_reason
    return rows


@pytest.mark.parametrize('release_reason',['lead_moving','gap_opened'])
def test_actual_lead_hold_and_observed_release_replace_initial_follow_clip(story_env,release_reason):
    _,client,handler,_,scenario=story_env
    holding=follow_event(5,'follow_lead_stop');released=follow_event(8)
    released_rows=lead_hold_frame('released',release_reason)
    if release_reason=='gap_opened':
        for item in ('lead','lead_hold'):
            released_rows[0]['plan']['reactive']['diagnostics'][item].update(gap_m=4.6,speed_mps=0.)
    handler.story_events=[follow_event(0),holding,released]
    handler.story_frame_actors={0:follow_frame(lead_speed=3.,requested=2.,stop=False),
        100:lead_hold_frame(),160:released_rows}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json();assert result['status']=='available'
    segment=result['segments'][0]
    assert segment['anchor_time_s']==pytest.approx(5.05)
    assert (segment['start_time_s'],segment['end_time_s'])==pytest.approx((3.05,12.05))
    assert segment['actor_ids']==['ego','rear']
    assert '앞차 정지에 맞춰 대기' in segment['title']
    assert ('앞차 이동 후' if release_reason=='lead_moving' else '간격 확보 후') in segment['title']
    assert '요청' in segment['description'] and '설정한' not in segment['description']
    assert [(e['reason'],e['category'],e['index']) for e in segment['events']]==[
        ('follow_lead_stop','wait',100),('follow_gap','resume',160)]
    resume=segment['events'][1]
    assert resume['previous_decision']=={key:holding[key] for key in ('reason','index','time_s','input_frame')}
    assert resume['decision_context']['lead_hold']['release_reason']==release_reason
    assert resume['observation_time_s']==pytest.approx(8.05)
    assert all(resume[key]==value for key,value in released.items())


@pytest.mark.parametrize('damage',['missing','cleared','wrong_actor','below_threshold','other_target','still_stop','intervening'])
def test_hold_release_story_requires_same_target_and_retained_release_condition(story_env,damage):
    _,client,handler,_,scenario=story_env
    released=lead_hold_frame('released','lead_moving');decision=released[0]['plan']['reactive']
    if damage=='missing':del decision['diagnostics']['lead_hold']
    elif damage=='cleared':decision['diagnostics']['lead_hold']['state']='cleared'
    elif damage=='wrong_actor':decision['diagnostics']['lead_hold']['actor_id']='parked'
    elif damage=='below_threshold':
        decision['diagnostics']['lead']['speed_mps']=.2
        decision['diagnostics']['lead_hold']['speed_mps']=.2
    elif damage=='other_target':
        decision['diagnostics']['lead']['actor_id']='parked'
        decision['diagnostics']['lead_hold']['actor_id']='parked'
    elif damage=='still_stop':decision.update(stop=True,speed_mps=0.)
    handler.story_events=[follow_event(5,'follow_lead_stop')]
    if damage=='intervening':handler.story_events.append(follow_event(6,'signal_wait'))
    handler.story_events.append(follow_event(8))
    handler.story_frame_actors={100:lead_hold_frame(),160:released}
    ref=save(client,scenario);identity=execute(story_env,ref)
    result=story(client,identity).json();assert result['status']=='available'
    assert not any(e['category']=='resume' for s in result['segments'] for e in s['events'])
    assert all('재개 요청' not in s['title'] for s in result['segments'])


def test_ab_wait_anchor_keeps_actual_B_stop_instead_of_A_initial_moving_follow(story_env):
    _,client,handler,_,scenario=story_env
    handler.story_events=[follow_event(0)]
    handler.story_frame_actors={0:follow_frame(lead_speed=3.,requested=2.,stop=False)}
    ref=save(client,scenario);left=execute(story_env,ref,'continuous-A')
    hold=follow_event(6.4,'follow_lead_stop');release=follow_event(8.8)
    handler.story_events=[follow_event(0),hold,release]
    handler.story_frame_actors={0:follow_frame(lead_speed=3.,requested=2.,stop=False),
        128:lead_hold_frame(),176:lead_hold_frame('released','lead_moving')}
    right=execute(story_env,ref,'stopped-B')
    result=story(client,left,right).json();assert result['status']=='available'
    assert result['clock']=='shared_simulation_time_s'
    assert result['sources']['left']['execution_id']==left and result['sources']['right']['execution_id']==right
    assert len(result['segments'])==1
    segment=result['segments'][0]
    assert (segment['start_time_s'],segment['anchor_time_s'],segment['end_time_s'])==pytest.approx((4.45,6.45,12.85))
    assert '앞차 정지에 맞춰 대기' in segment['title']
    assert [(e['side'],e['category'],e['time_s']) for e in segment['events']]==[
        ('right','wait',6.4),('right','resume',8.8)]
    assert all(segment['events'][0][key]==value for key,value in hold.items())
    assert all(segment['events'][1][key]==value for key,value in release.items())
    assert result['total_presentation_s']==pytest.approx(10.4)


def test_ab_arrival_window_uses_later_retained_arrival_without_extending_budget(story_env):
    _,client,handler,_,scenario=story_env
    scenario['duration_s']=24
    early=event(13.8,'arrived');late=event(21.4,'arrived')
    handler.story_events=[early]
    ref=save(client,scenario);left=execute(story_env,ref,'early-A')
    handler.story_events=[late]
    right=execute(story_env,ref,'later-B')
    result=story(client,left,right).json();assert result['status']=='available'
    assert result['clock']=='shared_simulation_time_s'
    assert len(result['segments'])==1
    segment=result['segments'][0]
    assert (segment['start_time_s'],segment['anchor_time_s'],segment['end_time_s'])==pytest.approx((19.45,21.45,24))
    assert [(e['side'],e['time_s']) for e in segment['events']]==[('right',21.4)]
    assert all(segment['events'][0][key]==value for key,value in late.items())
    assert result['sources']['left']['manifest_hash']==result['left']['execution']['manifest_hash']
    assert result['sources']['right']['manifest_hash']==result['right']['execution']['manifest_hash']
    assert result['total_presentation_s']==pytest.approx(6.55)
