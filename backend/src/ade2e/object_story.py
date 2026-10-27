"""Read-only guided clips selected from sealed native reactive planner events.

The story never infers perception, safety, risk scores or counterfactual outcomes.
Both sides retain the same simulation clock; original execution verdicts remain.
"""
import hashlib
import json
import math
from pathlib import Path
from fastapi import HTTPException, Request
from . import experiment_jobs
from .experiment_models import ExperimentArtifactRow
from .scenario_v2_xml import canonical_bytes
from .team_access import project_authorize

RULE_VERSION = 'object-story-rules/5'
MAX_SCENE_BYTES = 128 * 1024 * 1024
LIMITS = {'max_segments': 5, 'max_total_s': 60, 'pre_event_s': 2, 'post_event_s': 4, 'hold_s': 2}
CATEGORIES = ('wait', 'bypass', 'return', 'arrived', 'blocked', 'resume')
LABELS = {
    'wait': ('양보·간격 대기', 'planner가 주변 상태를 바탕으로 대기·간격 조절 결정을 기록했습니다.'),
    'bypass': ('우회 경로 선택', 'planner가 장애물 우회를 위한 경로 또는 우회 결정을 기록했습니다.'),
    'return': ('우회 행동 완료', 'planner가 복귀 지점 도달에 따른 우회 행동 완료를 기록했습니다.'),
    'arrived': ('목적지 도착 판단', 'planner가 목적지 도착 상태를 기록했습니다. 실행의 기존 평가 판정은 별도로 유지됩니다.'),
    'blocked': ('진행 제한', 'planner가 진행 제한·행동 중단 결정을 기록했습니다. 원래 이유를 아래 이벤트에서 확인할 수 있습니다.'),
    'resume': ('경로 추종 판단 재개', 'planner가 선행차 간격 조절 판단에서 경로 추종 판단으로 전환했습니다.'),
}
WAIT_REASONS = {'gap_wait', 'active_lane_gap_wait', 'follow_gap', 'follow_lead_stop', 'yield_conflict', 'yield_target',
                'yield_predicted_crossing', 'signal_wait', 'stop_sign_wait', 'yield_sign_wait', 'wait'}
AUTHORED_WAIT_LABEL = ('설정한 대기', '시나리오에 설정한 wait 명령의 실행을 planner가 기록했습니다.')
NEUTRAL_WAIT_LABEL = ('대기', 'planner가 대기 이벤트를 기록했습니다. 원본 이벤트만으로 대기 원인을 확정하지 않습니다.')
BLOCKED_REASONS = {'lane_changes_disabled', 'deadlock_no_legal_progress', 'behavior_failed',
    'action_timeout_stop', 'action_timeout_fail', 'footprint_emergency_hold', 'no_legal_route',
    'no_legal_adjacent_lane', 'no_legal_return_lane', 'candidate_hits_observed_obstacle',
    'selected_branch_cannot_reach_destination', 'return_lane_cannot_reach_destination',
    'target_lane_cannot_reach_destination', 'closed_route_blocked', 'reroute_disabled'}


class StoryUnavailable(Exception):
    """A bounded missing-evidence reason, safe to return without filesystem details."""


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _category(event):
    kind, reason = event.get('type'), event.get('reason')
    if kind == 'action_completed' and reason in {'obstacle_bypass', 'overtake', 'automatic_bypass_rejoined'}:
        return 'return'
    if kind == 'route_replanned' and reason in {'obstacle_bypass', 'overtake'}:
        return 'bypass'
    if kind != 'decision_changed':
        return None
    if reason in WAIT_REASONS:
        return 'wait'
    if reason in {'obstacle_bypass', 'automatic_obstacle_bypass', 'overtake'}:
        return 'bypass'
    if reason == 'arrived':
        return 'arrived'
    if reason in BLOCKED_REASONS:
        return 'blocked'
    return None


def _follow_context(event, frame, actor_ids):
    """Read the recorded input-GT decision, never infer a target from proximity.

    The frame's actor poses are the following physics response. Its attached
    reactive diagnostics preserve the decision made from event.input_frame.
    Lead speed must therefore not be replaced with the response actor's speed.
    """
    states = frame.get('actors')
    if not isinstance(states, list):
        return None
    matching = [state for state in states if isinstance(state, dict) and state.get('id') == event['actor_id']]
    if len(matching) != 1:
        return None
    plan = matching[0].get('plan')
    decision = plan.get('reactive') if isinstance(plan, dict) else None
    if not isinstance(decision, dict) or decision.get('reason') != event['reason']:
        return None
    diagnostics = decision.get('diagnostics')
    if not isinstance(diagnostics, dict) or diagnostics.get('observation_basis') != 'current_snapshot_ground_truth':
        return None
    lead = diagnostics.get('lead')
    if not isinstance(lead, dict):
        return None
    target = lead.get('actor_id')
    if (not isinstance(target, str) or target not in actor_ids or target == event['actor_id']
            or sum(isinstance(state, dict) and state.get('id') == target for state in states) != 1
            or not all(_number(value) for value in (lead.get('gap_m'), lead.get('speed_mps'), decision.get('speed_mps')))
            or lead['speed_mps'] < 0 or decision['speed_mps'] < 0 or type(decision.get('stop')) is not bool):
        return None
    context = {'source': 'retained_planner_diagnostics', 'frame_index': event['index'],
        'input_frame': event['input_frame'], 'target_actor_id': target, 'gap_m': lead['gap_m'],
        'lead_speed_mps': lead['speed_mps'], 'requested_speed_mps': decision['speed_mps'], 'stop': decision['stop']}
    hold = diagnostics.get('lead_hold')
    if isinstance(hold, dict) and hold.get('actor_id') == target and hold.get('state') in {'holding', 'released'}:
        numbers = ('gap_m', 'speed_mps', 'enter_gap_m', 'release_gap_m', 'enter_speed_mps', 'release_speed_mps')
        if (all(_number(hold.get(key)) for key in numbers)
                and hold['gap_m'] == lead['gap_m'] and hold['speed_mps'] == lead['speed_mps']
                and 0 <= hold['enter_speed_mps'] < hold['release_speed_mps']
                and 0 <= hold['enter_gap_m'] < hold['release_gap_m']):
            if hold['state'] == 'holding':
                if decision['stop'] and decision['speed_mps'] == 0:
                    context['lead_hold'] = {key: hold[key] for key in ('actor_id', 'state', *numbers)}
            else:
                release = hold.get('release_reason')
                supported_release = (release == 'lead_moving' and hold['speed_mps'] >= hold['release_speed_mps']
                    or release == 'gap_opened' and hold['gap_m'] >= hold['release_gap_m'])
                if supported_release:
                    context['lead_hold'] = {key: hold[key] for key in ('actor_id', 'state', *numbers, 'release_reason')}
    return context


def read_verified_scene(db, session, side, snapshot, *, require_planner_events=True):
    """Read one bounded sealed native scene with its source and event clocks.

    Shared by story selection and the beta action/response report. Return the
    original verified scene, not just the story's small category selection.
    """
    execution = side['execution']
    if execution['status'] != 'completed' or execution['outcome_uncertain'] or not execution['manifest_hash']:
        raise StoryUnavailable('completed_sealed_execution_required')
    summary = execution.get('summary') or {}
    if summary.get('scene_artifact') != 'city-scene.json':
        raise StoryUnavailable('original_city_scene_missing')
    candidates = [item for item in execution['artifacts'] if item['name'] == 'city-scene.json']
    if len(candidates) != 1:
        raise StoryUnavailable('original_city_scene_missing_or_ambiguous')
    descriptor = candidates[0]
    if not 0 < descriptor['size_bytes'] <= MAX_SCENE_BYTES:
        raise StoryUnavailable('original_city_scene_exceeds_story_read_limit')
    if summary.get('scene_sha256') != descriptor['sha256']:
        raise StoryUnavailable('original_city_scene_source_hash_mismatch')
    row = session.get(ExperimentArtifactRow, descriptor['id'])
    if row is None or row.job_id != execution['id'] or row.sha256 != descriptor['sha256']:
        raise StoryUnavailable('original_city_scene_artifact_identity_mismatch')
    # Existing helper verifies byte size/hash. Bound and validate the stored path
    # first, then re-hash the same bounded bytes we parse to avoid a read race.
    root = db.experiment_dir / 'artifacts'
    key = row.storage_key
    if not key or Path(key).name != key or key in {'.', '..'} or '\\' in key:
        raise StoryUnavailable('original_city_scene_artifact_path_invalid')
    path = root / key
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise StoryUnavailable('original_city_scene_artifact_path_invalid')
    try:
        path = experiment_jobs.artifact_path(db, row)
        with path.open('rb') as source:
            raw = source.read(MAX_SCENE_BYTES + 1)
        if len(raw) != descriptor['size_bytes'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise StoryUnavailable('original_city_scene_integrity_failed')
        def reject_nonfinite(_):
            raise ValueError('nonfinite JSON')
        scene = json.loads(raw, parse_constant=reject_nonfinite)
    except StoryUnavailable:
        raise
    except (HTTPException, OSError, ValueError, TypeError, RecursionError):
        raise StoryUnavailable('original_city_scene_integrity_failed') from None
    scenario = side['scenario']
    if not isinstance(scene, dict) or scene.get('schema_version') != 'carla-city-scene/1':
        raise StoryUnavailable('unsupported_original_city_scene')
    source = scene.get('source')
    expected = {**snapshot['source'], 'adapter_version': execution['adapter_version'],
                'planner_sha256': snapshot.get('planner_script_sha256')}
    if not isinstance(source, dict) or any(source.get(key) != value for key, value in expected.items()):
        raise StoryUnavailable('original_city_scene_provenance_mismatch')
    if scene.get('coordinate_system') != scenario['coordinate_system'] or scene.get('fidelity') != execution['fidelity']:
        raise StoryUnavailable('original_city_scene_coordinate_or_fidelity_mismatch')
    frames = scene.get('frames'); dt = scenario['timestep_s']; count = round(scenario['duration_s'] / dt)
    if not isinstance(frames, list) or len(frames) != count:
        raise StoryUnavailable('original_city_scene_clock_incomplete')
    for index, frame in enumerate(frames):
        if (not isinstance(frame, dict) or type(frame.get('index')) is not int or frame['index'] != index
                or not _number(frame.get('time_s')) or abs(frame['time_s'] - (index + 1) * dt) > 1e-6
                or type(frame.get('input_frame')) is not int):
            raise StoryUnavailable('original_city_scene_clock_invalid')
    receipt = {'execution_id': execution['id'], 'manifest_hash': execution['manifest_hash'],
               'artifact_id': descriptor['id'], 'artifact_name': 'city-scene.json',
               'artifact_sha256': descriptor['sha256'], 'scenario_ref': side['scenario_ref']}
    # Route-follow captures need no reactive planner events. Preserve the exact
    # scene as read; do not add an empty event array or change its artifact hash.
    if not require_planner_events and 'planner_events' not in scene:
        return scene, receipt
    events = scene.get('planner_events')
    if not isinstance(events, list) or len(events) > 200000:
        raise StoryUnavailable('original_planner_events_missing_or_oversized')
    actors = {actor['id'] for actor in scenario['actors']}
    for event in events:
        if (not isinstance(event, dict) or event.get('actor_id') not in actors
                or event.get('provenance') != 'observed_state_reactive_planner'
                or not _number(event.get('time_s')) or type(event.get('index')) is not int
                or not 0 <= event['index'] < count
                or abs(event['time_s'] - event['index'] * dt) > 1e-6
                or event.get('input_frame') != frames[event['index']]['input_frame']
                or not isinstance(event.get('type'), str) or not isinstance(event.get('reason'), str)):
            raise StoryUnavailable('original_planner_event_clock_or_identity_invalid')
    return scene, receipt


def _read_scene(db, session, side, snapshot):
    scene, receipt = read_verified_scene(db, session, side, snapshot)
    events = scene['planner_events']; frames = scene['frames']
    actors = {actor['id'] for actor in side['scenario']['actors']}
    selected = []; previous_decisions = {}
    for event in sorted(events, key=lambda item: item['index']):
        category = _category(event)
        previous = previous_decisions.get(event['actor_id'])
        context = (_follow_context(event, frames[event['index']], actors)
            if event['type'] == 'decision_changed' and event['reason'] in {'follow_gap', 'follow_lead_stop', 'route_follow'} else None)
        resumed = (event['type'] == 'decision_changed' and event['reason'] == 'route_follow'
            and previous is not None and previous['reason'] == 'follow_gap' and previous['index'] < event['index'])
        if (event['type'] == 'decision_changed' and event['reason'] == 'follow_gap'
                and previous is not None and previous['reason'] == 'follow_lead_stop' and previous['index'] < event['index']):
            prior_context = _follow_context(previous, frames[previous['index']], actors)
            resumed = bool(context and context.get('lead_hold', {}).get('state') == 'released'
                and not context['stop'] and context['requested_speed_mps'] > 0
                and prior_context and prior_context.get('lead_hold', {}).get('state') == 'holding'
                and prior_context['target_actor_id'] == context['target_actor_id'])
        if resumed:
            category = 'resume'
        if event['type'] == 'decision_changed':
            previous_decisions[event['actor_id']] = event
        if category is not None:
            item = {**event, 'category': category, 'observation_time_s': frames[event['index']]['time_s']}
            # These additive fields are derived here, never trusted from a raw event.
            item.pop('decision_context', None); item.pop('previous_decision', None)
            if (event['reason'] in {'follow_gap', 'follow_lead_stop'} or resumed) and context is not None:
                item['decision_context'] = context
            if resumed:
                item['previous_decision'] = {key: previous[key] for key in ('reason', 'index', 'time_s', 'input_frame')}
            selected.append(item)
    return selected, receipt


def _stopped_follow(event):
    return (event['reason'] in {'follow_gap', 'follow_lead_stop'}
        and (event.get('decision_context') or {}).get('stop') is True)


def _representatives(events, ego_id, side):
    # One original event per side/category, preferring ego before chronological
    # order. This prevents eight-actor noise from consuming the complete story.
    result = []
    for category in CATEGORIES:
        candidates = [event for event in events if event['category'] == category]
        if candidates:
            def priority(event):
                # Prefer an actual stop request over an earlier moving follow
                # transition, only when that request is in a recorded event.
                return (event['actor_id'] != ego_id, not _stopped_follow(event), event['time_s'],
                        event['actor_id'], event['type'], event['reason'])
            event = min(candidates, key=priority)
            result.append({**event, 'side': side})
    return result


def _event_label(event, scenarios):
    if event['category'] == 'resume' and event.get('previous_decision', {}).get('reason') == 'follow_lead_stop':
        context = event['decision_context']; hold = context['lead_hold']
        if hold['release_reason'] == 'lead_moving':
            return ('앞차 이동 후 주행 재개 요청',
                f"앞차 {context['target_actor_id']}의 속도가 {context['lead_speed_mps']:.1f}m/s로 기록되어 "
                f"정지 대기를 해제하고 {context['requested_speed_mps']:.1f}m/s를 요청했습니다.")
        return ('간격 확보 후 주행 재개 요청',
            f"앞차 {context['target_actor_id']}와의 간격이 {context['gap_m']:.1f}m로 기록되어 "
            f"정지 대기를 해제하고 {context['requested_speed_mps']:.1f}m/s를 요청했습니다.")
    if event['category'] != 'wait':
        return LABELS[event['category']]
    if event.get('reason') == 'follow_lead_stop':
        context = event.get('decision_context') or {}
        if context.get('lead_hold', {}).get('state') == 'holding':
            return ('앞차 정지에 맞춰 대기',
                f"앞차 {context['target_actor_id']}와의 간격 {context['gap_m']:.1f}m에서 정지를 요청했습니다. "
                f"앞차 속도는 {context['lead_speed_mps']:.1f}m/s입니다.")
        return ('앞차 정지 대기 판단', '정지 대기 판단이 기록되었습니다. 이 시점의 정지 유지 조건은 미보존입니다.')
    if event.get('reason') == 'follow_gap':
        context = event.get('decision_context')
        if context:
            return ('앞차와 간격 확보',
                f"앞차 {context['target_actor_id']}와의 간격 {context['gap_m']:.1f}m에 맞춰 "
                f"요청 속도를 {context['requested_speed_mps']:.1f}m/s로 조절했습니다. "
                f"앞차 속도는 {context['lead_speed_mps']:.1f}m/s입니다.")
        return ('앞차 간격 조절', '앞차 간격에 따른 속도 조절이 기록되었습니다. 이 시점의 대상·간격 값은 미보존입니다.')
    # The current observed gap/signal reason can override an active authored
    # wait, so action identity alone cannot establish why this event waited.
    if event.get('reason') in WAIT_REASONS - {'wait'}:
        return LABELS['wait']
    if event.get('reason') == 'wait' and event.get('action_id'):
        scenario = (scenarios or {}).get(event.get('side')) or {}
        actor = next((a for a in scenario.get('actors', []) if a['id'] == event['actor_id']), None)
        if actor and any(action['id'] == event['action_id'] and action['kind'] == 'wait'
                         for action in actor.get('behavior', {}).get('actions', [])):
            return AUTHORED_WAIT_LABEL
    return NEUTRAL_WAIT_LABEL


def _segments(events, duration, sources, first_frame_s, scenarios=None):
    anchors = []
    # At most five category windows. Each spans <=6 seconds before merging,
    # so original replay plus the 2-second explanation per segment is <=40s.
    for category in CATEGORIES:
        choices = [event for event in events if event['category'] == category]
        if choices:
            # Apply the same stop priority across A/B; an initial moving follow
            # on one side must not displace the other side's actual stop event.
            # End a paired story at the later retained arrival so the earlier
            # side's arrival cannot hide the other side's final approach.
            paired_arrival = category == 'arrived' and len({event['side'] for event in choices}) > 1
            anchor = min(choices, key=lambda e: (not (category == 'wait' and _stopped_follow(e)),
                -e['observation_time_s'] if paired_arrival else e['time_s'], e['side'], e['actor_id'], e['type']))
            anchors.append({'start_time_s': max(first_frame_s, anchor['observation_time_s'] - LIMITS['pre_event_s']),
                'end_time_s': min(duration, anchor['observation_time_s'] + LIMITS['post_event_s']), 'anchors': [anchor]})
    # Preserve the existing five-category coverage first. Resume is additional
    # when there is capacity; six disjoint categories must not disable a story.
    anchors = anchors[:LIMITS['max_segments']]
    groups = []
    for window in sorted(anchors, key=lambda s: (s['start_time_s'], s['end_time_s'])):
        if groups and window['start_time_s'] <= groups[-1]['end_time_s'] + 1e-9:
            groups[-1]['end_time_s'] = max(groups[-1]['end_time_s'], window['end_time_s'])
            groups[-1]['anchors'].extend(window['anchors'])
        else:
            groups.append(window)
    segments = []
    for group in groups:
        start, end = group['start_time_s'], group['end_time_s']
        selected = sorted((event for event in events if start <= event['observation_time_s'] <= end),
                          key=lambda e: (e['time_s'], e['side'], e['actor_id'], e['type'], e['reason']))
        labels = list(dict.fromkeys(_event_label(event, scenarios) for event in selected))
        anchor = min(group['anchors'], key=lambda e: (CATEGORIES.index(e['category']), e['time_s'], e['side']))
        identity = hashlib.sha256(canonical_bytes({'rule_version': RULE_VERSION, 'sources': sources,
            'start': start, 'end': end, 'events': selected})).hexdigest()[:20]
        segments.append({'id': 'story_' + identity, 'start_time_s': round(start, 6), 'end_time_s': round(end, 6),
            'anchor_time_s': anchor['observation_time_s'], 'hold_s': LIMITS['hold_s'], 'playback_rate': 1,
            'title': ' → '.join(label[0] for label in labels),
            'description': ' '.join(label[1] for label in labels),
            'actor_ids': sorted({event['actor_id'] for event in selected} |
                {event['decision_context']['target_actor_id'] for event in selected if event.get('decision_context')}), 'events': selected})
    total = round(sum(segment['end_time_s'] - segment['start_time_s'] + segment['hold_s'] for segment in segments), 6)
    if len(segments) > LIMITS['max_segments'] or total > LIMITS['max_total_s']:
        raise StoryUnavailable('story_presentation_budget_exceeded')
    return segments, total


def build_story(db, session, project, left_id, right_id=None):
    from .object_demo import _comparison, _execution
    left, left_snapshot = _execution(session, project, left_id)
    right, right_snapshot = _execution(session, project, right_id) if right_id else (None, None)
    result = {'schema_version': 'object-sim-story/1', 'rule_version': RULE_VERSION, 'project_id': project,
        'status': 'unavailable', 'reason': None, 'manual_replay_available': True, 'clock': 'shared_simulation_time_s',
        'event_clock': 'command_index_times_timestep', 'observation_clock': 'retained_response_frame_time_s',
        'left': left, 'right': right, 'sources': {'left': None, 'right': None}, 'segments': [],
        'total_presentation_s': 0, 'limits': LIMITS,
        'limitations': ['봉인된 원본의 planner 이벤트를 골라 설명합니다. 독립적인 안전 판정이나 인지 결과가 아닙니다.',
            'A/B는 같은 시뮬레이션 시각을 사용하며 이벤트가 발생한 시각을 서로 맞춰 바꾸지 않습니다.',
            '원본 이벤트 time_s는 명령 시각입니다. 설명 정지와 구간은 같은 index의 보존된 응답 프레임 observation_time_s를 사용합니다.',
            '각 종류의 대표 이벤트를 선택하므로 모든 판단 전환을 포함하지 않습니다. 전체 원본은 수동 재생에서 확인합니다.']}
    try:
        if right is not None:
            compatibility = _comparison(session, project, left_id, right_id)['compatibility']
            if not compatibility['compatible']:
                raise StoryUnavailable('comparison_incompatible: ' + '; '.join(compatibility['reasons']))
        representatives = []
        for name, side, snapshot in [('left', left, left_snapshot), ('right', right, right_snapshot)]:
            if side is None:
                continue
            events, receipt = _read_scene(db, session, side, snapshot)
            result['sources'][name] = receipt
            ego = next(actor['id'] for actor in side['scenario']['actors'] if actor['role'] == 'ego')
            representatives.extend(_representatives(events, ego, name))
        if not representatives:
            raise StoryUnavailable('no_representative_planner_events')
        duration = min(side['scenario']['duration_s'] for side in (left, right) if side is not None)
        result['segments'], result['total_presentation_s'] = _segments(representatives, duration, result['sources'],
            max(side['scenario']['timestep_s'] for side in (left, right) if side is not None),
            {'left': left['scenario'], 'right': right['scenario'] if right else None})
        result['status'] = 'available'
    except StoryUnavailable as exc:
        result.update(status='unavailable', reason=str(exc), segments=[], total_presentation_s=0)
    return result


def register_object_story_routes(api, db):
    @api.get('/object-sim/story', tags=['Object Sim'], openapi_extra={'x-axiom-min-role': 'Viewer', 'x-axiom-read-only': True})
    def story(request: Request, left_execution_id: str, project_id: str = 'default', right_execution_id: str | None = None):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return build_story(db, session, project_id, left_execution_id, right_execution_id)
