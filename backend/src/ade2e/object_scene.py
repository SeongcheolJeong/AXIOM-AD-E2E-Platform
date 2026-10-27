"""Versioned scene evidence derived only from a pinned map and native observations.

The road is the bounded authored OpenDRIVE subset. Sampled geometry events and
engine log events retain separate provenance; neither certifies authored intent.
"""
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Annotated, Literal
from xml.etree import ElementTree as ET

from pydantic import ConfigDict, Field, StringConstraints, model_validator
from .base_schemas import StrictModel
from .object_sim_observers import _overlap
from .scenario_v2_schemas import Action, Dimensions, Identity, Pose, ScenarioV2
from .scenario_v2_xml import digest, road_xml, scenario_xml

SCENE_VERSION = 'object-scene/1'
EVENT_VERSION = 'object-scene-events/1'
COORDINATE_SYSTEM = 'world_m_x_forward_y_left_z_up'
ContentHash = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


def sha256(content: bytes | str) -> str:
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()


class Point3(StrictModel):
    x: float
    y: float
    z: float


class SceneLane(StrictModel):
    id: int
    width_m: float = Field(gt=0)
    centerline: list[Point3] = Field(min_length=2)
    left_boundary: list[Point3] = Field(min_length=2)
    right_boundary: list[Point3] = Field(min_length=2)


class SceneMap(StrictModel):
    kind: Literal['authored_straight_road']
    id: Literal['axiom-straight-road-r1']
    road_id: int
    length_m: float = Field(gt=0)
    lane_width_m: float = Field(gt=0)
    source_artifact: Literal['road.xodr'] = 'road.xodr'
    source_sha256: ContentHash
    geometry_provenance: Literal['validated_pinned_opendrive_line_and_lane_widths']
    reference_line: list[Point3] = Field(min_length=2)
    lanes: list[SceneLane] = Field(min_length=2, max_length=2)


class SceneSource(StrictModel):
    scenario_id: str
    scenario_revision: int = Field(ge=1)
    scenario_manifest_hash: ContentHash
    configuration_hash: ContentHash
    seed: int
    adapter_version: str
    engine_version: str
    engine_build: str
    engine_binary_sha256: ContentHash
    runner_sha256: ContentHash
    scenario_xml_sha256: ContentHash
    road_xml_sha256: ContentHash
    native_csv_sha256: ContentHash
    native_log_sha256: ContentHash
    object_trace_sha256: ContentHash


class SceneActorMetadata(StrictModel):
    origin: Literal['pinned_scenario_revision'] = 'pinned_scenario_revision'
    dimensions_source: Literal['authored_scenario'] = 'authored_scenario'
    native_dimensions_fields: list[str] = Field(default_factory=lambda: ['length_m', 'width_m', 'bb_x', 'bb_y'])
    height_source: Literal['authored_scenario_not_native_observation'] = 'authored_scenario_not_native_observation'
    native_entity_id: int | None = None
    native_entity_id_provenance: dict | None = None


class AuthoredIntent(StrictModel):
    initial_pose: Pose
    initial_speed_mps: float
    route: list[Pose]
    actions: list[Action]
    interpretation: Literal['authored_intent_not_observed_completion'] = 'authored_intent_not_observed_completion'


class SceneActor(StrictModel):
    id: Identity
    kind: Literal['vehicle', 'pedestrian']
    role: Literal['ego', 'other']
    dimensions_m: Dimensions
    metadata: SceneActorMetadata
    authored_intent: AuthoredIntent


class NativeActorState(StrictModel):
    """The complete current native CSV state; no missing native field is filled."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    id: Identity
    x: float
    y: float
    z: float
    heading_rad: float
    speed_mps: float
    vx_mps: float
    vy_mps: float
    ax_mps2: float
    ay_mps2: float
    bb_x: float
    bb_y: float
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    lane_id: int
    lane_offset_m: float


class NativeFrame(StrictModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    time_s: float = Field(ge=0)
    actors: list[NativeActorState] = Field(min_length=1, max_length=8)


class EventProvenance(StrictModel):
    source: Literal['native_trace', 'native_log']
    artifact: Literal['object-trace.json', 'engine.log']
    sha256: ContentHash
    method: Literal['sampled_obb_overlap', 'sampled_lane_id_change', 'sampled_speed_threshold_crossing',
                    'esmini_event_state_log', 'esmini_collision_log']
    frame_indices: list[int] = Field(default_factory=list)
    line_numbers: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def source_references(self):
        if self.source == 'native_trace':
            if (self.artifact != 'object-trace.json' or not self.frame_indices or self.line_numbers or
                    not self.method.startswith('sampled_') or any(index < 0 for index in self.frame_indices)):
                raise ValueError('Trace events require native frame indices and the full trace artifact')
        elif (self.artifact != 'engine.log' or not self.line_numbers or self.frame_indices or
              not self.method.startswith('esmini_') or any(number < 1 for number in self.line_numbers)):
            raise ValueError('Log events require original engine log line numbers')
        return self


class SceneEvent(StrictModel):
    kind: Literal['collision', 'lane_transition', 'stopped', 'native_action']
    actor_ids: list[Identity] = Field(min_length=1, max_length=2)
    time_s: float = Field(ge=0)
    end_time_s: float | None = None
    provenance: EventProvenance
    details: dict


class ObjectScene(StrictModel):
    schema_version: Literal['object-scene/1'] = SCENE_VERSION
    coordinate_system: Literal['world_m_x_forward_y_left_z_up'] = COORDINATE_SYSTEM
    fidelity: Literal['authored_object_kinematics'] = 'authored_object_kinematics'
    physical_sensor_validation: Literal[False] = False
    source: SceneSource
    map: SceneMap
    actors: list[SceneActor] = Field(min_length=1, max_length=8)
    frames: list[NativeFrame] = Field(min_length=2)
    events: list[SceneEvent]
    event_derivation_version: Literal['object-scene-events/1'] = EVENT_VERSION
    limitations: list[str]

    @model_validator(mode='after')
    def observed_identities(self):
        ids = [actor.id for actor in self.actors]
        if len(set(ids)) != len(ids):
            raise ValueError('Scene actor identities must be unique')
        for index, frame in enumerate(self.frames):
            observed = [actor.id for actor in frame.actors]
            if len(observed) != len(ids) or set(observed) != set(ids):
                raise ValueError('Native frame identities differ from pinned scene actors')
            if index and frame.time_s <= self.frames[index - 1].time_s:
                raise ValueError('Native scene clock must strictly increase')
        for event in self.events:
            if not set(event.actor_ids).issubset(ids):
                raise ValueError('Event references unknown scene actor')
            if event.end_time_s is not None and event.end_time_s < event.time_s:
                raise ValueError('Event end precedes its first observed time')
            provenance = event.provenance
            expected_hash = (self.source.object_trace_sha256 if provenance.source == 'native_trace'
                             else self.source.native_log_sha256)
            if provenance.sha256 != expected_hash:
                raise ValueError('Event provenance hash differs from its original scene source')
            if any(index >= len(self.frames) for index in provenance.frame_indices):
                raise ValueError('Event references a native frame outside the full trace')
        if self.map.source_sha256 != self.source.road_xml_sha256:
            raise ValueError('Scene map hash differs from pinned OpenDRIVE source')
        return self


def _xml_structure(node):
    if (node.text or '').strip() or (node.tail or '').strip():
        raise ValueError('Unexpected OpenDRIVE text')
    return node.tag, sorted(node.attrib.items()), [_xml_structure(child) for child in node]


def map_geometry(scenario: ScenarioV2, pinned_road_xml: str) -> dict:
    """Derive coordinates from the actual source after an exact supported-subset check."""
    if '<!DOCTYPE' in pinned_road_xml.upper() or '<!ENTITY' in pinned_road_xml.upper():
        raise ValueError('OpenDRIVE DTD/entities are unsupported')
    root = ET.fromstring(pinned_road_xml)
    if _xml_structure(root) != _xml_structure(ET.fromstring(road_xml(scenario))):
        raise ValueError('Pinned OpenDRIVE differs from the supported authored straight-road map')
    road = root.find('./road')
    geometry = road.find('./planView/geometry')
    length = float(geometry.attrib['length'])
    x, y, heading = (float(geometry.attrib[key]) for key in ('x', 'y', 'hdg'))
    c, s = math.cos(heading), math.sin(heading)

    def line(lateral):
        return [{'x': x - lateral*s, 'y': y + lateral*c, 'z': 0.},
                {'x': x + length*c - lateral*s, 'y': y + length*s + lateral*c, 'z': 0.}]

    lanes = []
    lateral = 0.
    for lane in road.findall('./lanes/laneSection/right/lane'):
        width = float(lane.find('./width').attrib['a'])
        lanes.append({'id': int(lane.attrib['id']), 'width_m': width,
                      'centerline': line(lateral-width/2), 'left_boundary': line(lateral),
                      'right_boundary': line(lateral-width)})
        lateral -= width
    return SceneMap.model_validate({
        'kind': scenario.map.kind, 'id': root.find('./header').attrib['name'],
        'road_id': int(road.attrib['id']), 'length_m': float(road.attrib['length']),
        'lane_width_m': lanes[0]['width_m'], 'source_sha256': sha256(pinned_road_xml),
        'geometry_provenance': 'validated_pinned_opendrive_line_and_lane_widths',
        'reference_line': line(0), 'lanes': lanes,
    }).model_dump(mode='json')


def validate_native_frames(frames, actor_ids):
    """Validate without replacing native numbers, field values, or sample order."""
    if not isinstance(frames, list) or len(frames) < 2:
        raise ValueError('At least two full native frames are required')
    for index, frame in enumerate(frames):
        parsed = NativeFrame.model_validate(frame)
        observed = [state.id for state in parsed.actors]
        if len(observed) != len(actor_ids) or set(observed) != set(actor_ids):
            raise ValueError('Native actor identities differ from pinned scenario')
        if index and parsed.time_s <= frames[index-1]['time_s']:
            raise ValueError('Native clock must strictly increase')


def derive_events(scenario, frames, native_log, trace_hash, log_hash):
    ids = [actor['id'] for actor in scenario['actors']]
    validate_native_frames(frames, ids)
    events = []

    def event(kind, actors, time_s, method, details, indices=(), end=None, line=None):
        events.append({'kind': kind, 'actor_ids': actors, 'time_s': time_s, 'end_time_s': end,
                       'provenance': {'source': 'native_log' if line else 'native_trace',
                                      'artifact': 'engine.log' if line else 'object-trace.json',
                                      'sha256': log_hash if line else trace_hash, 'method': method,
                                      'frame_indices': list(indices), 'line_numbers': [line] if line else []},
                       'details': details})

    states = [{state['id']: state for state in frame['actors']} for frame in frames]
    for first, aid in enumerate(ids):
        for bid in ids[first+1:]:
            start = None
            for index in range(len(frames)+1):
                overlap = index < len(frames) and _overlap(states[index][aid], states[index][bid])
                if overlap and start is None:
                    start = index
                elif not overlap and start is not None:
                    last = index-1
                    event('collision', [aid, bid], frames[start]['time_s'], 'sampled_obb_overlap',
                          {'open_at_start': start == 0, 'open_at_end': index == len(frames),
                           'before_time_s': frames[start-1]['time_s'] if start else None,
                           'after_time_s': frames[index]['time_s'] if index < len(frames) else None,
                           'coordinate_plane': 'world_xy',
                           'interpretation': 'overlap_at_native_samples_not_continuous_contact_time'},
                          (start, last), frames[last]['time_s'])
                    start = None
    for index in range(1, len(frames)):
        for aid in ids:
            previous, current = states[index-1][aid], states[index][aid]
            if previous['lane_id'] != current['lane_id']:
                event('lane_transition', [aid], frames[index]['time_s'], 'sampled_lane_id_change',
                      {'from_lane_id': previous['lane_id'], 'to_lane_id': current['lane_id'],
                       'previous_time_s': frames[index-1]['time_s'],
                       'interpretation': 'native_lane_id_changed_between_samples_not_action_completion'}, (index-1, index))
            if abs(previous['speed_mps']) > .05 and abs(current['speed_mps']) <= .05:
                event('stopped', [aid], frames[index]['time_s'], 'sampled_speed_threshold_crossing',
                      {'threshold_mps': .05, 'previous_time_s': frames[index-1]['time_s'],
                       'speed_mps': current['speed_mps'], 'interpretation': 'sampled_speed_threshold_not_stop_action_completion'}, (index-1, index))

    actions = {f"{actor['id']}_{index}_event": (actor['id'], index, action['kind'])
               for actor in scenario['actors'] for index, action in enumerate(actor['actions'])}
    for number, line in enumerate(native_log.splitlines(), 1):
        match = re.fullmatch(r'\[(\d+(?:\.\d+)?)\] \[(?:info|warn)\] (.+)', line)
        if not match:
            continue
        time_s, message = float(match[1]), match[2]
        collision = re.fullmatch(r'Collision between ([A-Za-z0-9_-]+) and ([A-Za-z0-9_-]+)( dissolved)?', message)
        if collision and collision[1] in ids and collision[2] in ids:
            event('collision', [collision[1], collision[2]], time_s, 'esmini_collision_log',
                  {'phase': 'dissolved' if collision[3] else 'reported', 'raw_line': line,
                   'interpretation': 'engine_reported_collision_marker'}, line=number)
        transition = re.fullmatch(r'([A-Za-z0-9_-]+) (\w+) -> (\w+) -> (runningState|completeState)', message)
        if transition and transition[1] in actions:
            aid, action_index, action_kind = actions[transition[1]]
            event('native_action', [aid], time_s, 'esmini_event_state_log',
                  {'action_index': action_index, 'action_kind': action_kind, 'engine_state': transition[4],
                   'raw_line': line, 'interpretation': 'engine_event_transition_not_observed_behavior_completion'}, line=number)
    return sorted(events, key=lambda value: (value['time_s'], value['kind'], value['actor_ids'], value['provenance']['source']))


def build_object_scene(snapshot, native, object_trace_content: bytes) -> dict:
    scenario = ScenarioV2.model_validate(snapshot['scenario'])
    if (digest(snapshot['scenario']) != snapshot['configuration_hash'] or
            snapshot['native_request']['scenario_xml'] != scenario_xml(scenario)):
        raise ValueError('Scene source differs from pinned scenario configuration/XML identity')
    if (snapshot['seed'] != native['seed'] or snapshot['runner_sha256'] != native['runner_sha256'] or
            snapshot.get('engine_binary_sha256') != native['binary_sha256']):
        raise ValueError('Scene native receipt differs from pinned engine/runner/seed identity')
    frames = native['frames']
    validate_native_frames(frames, [actor.id for actor in scenario.actors])
    road_content = snapshot['native_request']['road_xml']
    log_hash, trace_hash = sha256(native['native_log']), sha256(object_trace_content)
    if (sha256(road_content) != native['road_xml_sha256'] or
            sha256(snapshot['native_request']['scenario_xml']) != native['scenario_xml_sha256'] or
            sha256(native['native_csv']) != native['native_csv_sha256']):
        raise ValueError('Scene native source hashes differ from pinned original contents')
    parsed_trace = json.loads(object_trace_content)
    if parsed_trace != {'schema_version': 'object-trace/1', 'coordinate_system': scenario.coordinate_system, 'frames': frames}:
        raise ValueError('Scene frames differ from full original object trace')
    actors = []
    for actor in scenario.actors:
        metadata = SceneActorMetadata().model_dump(mode='json')
        pattern = re.compile(r'\[\] \[info\] Add and deactivate new object "' + re.escape(actor.id) + r'" \(id (\d+)\)')
        matches = [(number, line, pattern.fullmatch(line)) for number, line in enumerate(native['native_log'].splitlines(), 1)]
        matches = [(number, line, match) for number, line, match in matches if match]
        if len(matches) == 1:
            number, line, match = matches[0]
            metadata.update(native_entity_id=int(match[1]), native_entity_id_provenance={
                'source': 'native_log', 'artifact': 'engine.log', 'sha256': log_hash,
                'line_number': number, 'raw_line': line})
        actors.append({'id': actor.id, 'kind': actor.kind, 'role': actor.role,
                       'dimensions_m': actor.dimensions.model_dump(mode='json'), 'metadata': metadata,
                       'authored_intent': {'initial_pose': actor.pose.model_dump(mode='json'),
                                          'initial_speed_mps': actor.speed_mps,
                                          'route': [pose.model_dump(mode='json') for pose in actor.route],
                                          'actions': [action.model_dump(mode='json') for action in actor.actions]}})
    scene = {
        'source': {key: snapshot[key] for key in ('scenario_id', 'scenario_revision', 'scenario_manifest_hash',
                   'configuration_hash', 'seed', 'adapter_version', 'runner_sha256')},
        'map': map_geometry(scenario, road_content), 'actors': actors, 'frames': deepcopy(frames),
        'events': derive_events(snapshot['scenario'], frames, native['native_log'], trace_hash, log_hash),
        'limitations': ['Authored road and object kinematics; no physical sensor or real-world validation',
                       'World-XY footprint collision geometry is observed only at native samples; engine log markers are separate',
                       'Authored actions and engine event transitions do not certify the intended behavior occurred',
                       'Actor height is authored; native CSV has no bounding-box height or vertical center offset'],
    }
    scene['source'].update({key: native[key] for key in ('engine_version', 'engine_build', 'scenario_xml_sha256',
                           'road_xml_sha256', 'native_csv_sha256')})
    scene['source'].update(engine_binary_sha256=native['binary_sha256'], native_log_sha256=log_hash, object_trace_sha256=trace_hash)
    validated = ObjectScene.model_validate(scene).model_dump(mode='json')
    # Preserve every native number and original state key, rather than serializing coercions.
    validated['frames'] = deepcopy(frames)
    return validated


def trajectory_preview(frames, limit=64):
    """Bounded native samples including both observation-window endpoints."""
    if limit < 2:
        raise ValueError('A trajectory preview requires both native endpoints')
    if len(frames) <= limit:
        return list(frames)
    indices = [index * (len(frames)-1) // (limit-1) for index in range(limit)]
    return [frames[index] for index in indices]
