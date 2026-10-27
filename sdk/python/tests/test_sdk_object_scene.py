"""Complete standalone authored SDK fixtures; no simulator execution is claimed."""
from copy import deepcopy
import hashlib
import json
from xml.etree import ElementTree as ET

import httpx
import pytest

from axiom_sdk import AxiomClient

BINARY = '7f31d9a52a1da6c9d062a89f15b714f004308eb0d3c007107f09cce85afbf619'
VERSION = '0.2.0+esmini3.8.2'


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(content):
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()


def road_xml():
    return '''<OpenDRIVE><header revMajor="1" revMinor="4" name="axiom-straight-road-r1" version="1" date="2026-10-07" north="0" south="0" east="0" west="0" />
<road name="AXIOM authored straight road" length="500.0" id="1" junction="-1"><link />
<planView><geometry s="0" x="0" y="0" hdg="0" length="500.0"><line /></geometry></planView>
<lanes><laneSection s="0"><center><lane id="0" type="none" level="false"><link /></lane></center><right>
<lane id="-1" type="driving" level="false"><link /><width sOffset="0" a="3.5" b="0" c="0" d="0" /><roadMark sOffset="0" type="broken" weight="standard" color="standard" width="0.12" laneChange="both" /></lane>
<lane id="-2" type="driving" level="false"><link /><width sOffset="0" a="3.5" b="0" c="0" d="0" /><roadMark sOffset="0" type="solid" weight="standard" color="standard" width="0.12" laneChange="both" /></lane>
</right></laneSection></lanes></road></OpenDRIVE>'''


def fixture(mutate=None, *, mutate_frames=None, mutate_prepared=None, bad_input_hash=False, road_override=None):
    trigger = {'kind':'time','value':.5,'actor_id':None,'target_actor_id':None,'rule':'greaterThan','state':'speed'}
    action = {'kind':'stop','trigger':trigger,'target_speed_mps':None,'target_lane':None,'transition_s':.1,'path':[]}
    actors = [
        {'id':'ego','kind':'vehicle','role':'ego','pose':{'x':5.,'y':-1.75,'heading_rad':0.},
         'dimensions':{'length_m':4.5,'width_m':1.8,'height_m':1.5},'speed_mps':8.,'route':[],'actions':[action]},
        {'id':'other','kind':'vehicle','role':'other','pose':{'x':8.,'y':-5.25,'heading_rad':0.},
         'dimensions':{'length_m':4.5,'width_m':1.8,'height_m':1.5},'speed_mps':2.,'route':[],
         'actions':[{**deepcopy(action),'kind':'lane_change','target_lane':-1,'transition_s':.3,
                     'trigger':{**trigger,'value':.2}}]},
    ]
    scenario = {'schema_version':'scenario-v2/1','name':'Authored SDK boundary fixture','template':None,
                'coordinate_system':'world_m_x_forward_y_left_z_up','map':{'kind':'authored_straight_road',
                    'id':'axiom-straight-road-r1','length_m':500.,'lane_width_m':3.5},
                'duration_s':1.,'timestep_s':.1,'actors':actors,'environment':{'lighting':'day','weather':'dry'},
                'observers':[],'sweep':[]}
    root = ET.Element('OpenSCENARIO'); header = ET.SubElement(root, 'FileHeader')
    properties = ET.SubElement(header, 'Properties')
    ET.SubElement(properties, 'Property', name='axiom_exporter', value='axiom-osc-subset/1')
    ET.SubElement(properties, 'Property', name='axiom_scenario_v2', value=encode(scenario).decode())
    ET.SubElement(ET.SubElement(root, 'RoadNetwork'), 'LogicFile', filepath='road.xodr')
    xosc = ET.tostring(root, encoding='unicode')
    road = road_override if road_override is not None else road_xml()
    log_lines = ['[] [info] Add and deactivate new object "ego" (id 0)',
                 '[] [info] Add and deactivate new object "other" (id 1)',
                 '[0.300] [info] other_0_event standbyState -> startTransition -> runningState',
                 '[0.500] [info] other_0_event runningState -> endTransition -> completeState',
                 '[0.500] [warn] Collision between ego and other',
                 '[0.600] [info] ego_0_event standbyState -> startTransition -> runningState',
                 '[0.700] [info] ego_0_event runningState -> endTransition -> completeState']
    log = '\n'.join(log_lines)+'\n'
    frames = []
    for index in range(12):
        t = round(index*.1, 1)
        states = []
        for number, actor in enumerate(actors):
            ego = number == 0
            speed = 8. if ego and index < 7 else 0. if ego else 2.
            states.append({'id':actor['id'],'x':5.+8.*min(t,.7) if ego else 8.+2.*t,
                           'y':-1.75 if ego or index >= 5 else -5.25,'z':0.,'heading_rad':0.,
                           'speed_mps':speed,'vx_mps':speed,'vy_mps':0.,'ax_mps2':0.,'ay_mps2':0.,
                           'bb_x':1.4,'bb_y':0.,'length_m':4.5,'width_m':1.8,
                           'lane_id':-1 if ego or index >= 5 else -2,'lane_offset_m':0.})
        frames.append({'time_s':t,'actors':states})
    if mutate_frames: mutate_frames(frames)
    trace = {'schema_version':'object-trace/1','coordinate_system':scenario['coordinate_system'],'frames':frames}
    content = {'object-trace.json':encode(trace),'road.xodr':road.encode(),'scenario.xosc':xosc.encode(),
               'native-trace.csv':b'authored SDK contract fixture, not actual native CSV\n','engine.log':log.encode()}
    hashes = {name:sha(raw) for name, raw in content.items()}
    identity = {'scenario_id':'scenario_test','scenario_revision':1,'scenario_manifest_hash':'a'*64,
                'configuration_hash':sha(encode(scenario)),'seed':42,'adapter_version':VERSION}
    pinned = {**identity,'schema_version':'object-sim-input/1','project_id':'default','scenario':deepcopy(scenario),
              'runner_sha256':'b'*64,'engine_binary_sha256':BINARY,'selection':[],
              'evaluator_version':'object-trace-observers/1','command_hash':'c'*64,
              'native_request':{'schema_version':'esmini-native-request/1','scenario_xml':xosc,'road_xml':road,
                                'duration_s':1.,'timestep_s':.1,'seed':42,'actor_ids':['ego','other']}}
    scene_actors = []
    for number, actor in enumerate(actors):
        scene_actors.append({'id':actor['id'],'kind':actor['kind'],'role':actor['role'],'dimensions_m':deepcopy(actor['dimensions']),
            'metadata':{'origin':'pinned_scenario_revision','dimensions_source':'authored_scenario',
                'native_dimensions_fields':['length_m','width_m','bb_x','bb_y'],
                'height_source':'authored_scenario_not_native_observation','native_entity_id':number,
                'native_entity_id_provenance':{'source':'native_log','artifact':'engine.log','sha256':hashes['engine.log'],
                    'line_number':number+1,'raw_line':log_lines[number]}},
            'authored_intent':{'initial_pose':deepcopy(actor['pose']),'initial_speed_mps':actor['speed_mps'],
                'route':[],'actions':deepcopy(actor['actions']),'interpretation':'authored_intent_not_observed_completion'}})
    def line(y): return [{'x':0.,'y':y,'z':0.},{'x':500.,'y':y,'z':0.}]
    scene = {'schema_version':'object-scene/1','coordinate_system':scenario['coordinate_system'],
        'fidelity':'authored_object_kinematics','physical_sensor_validation':False,
        'source':{**identity,'engine_version':'v3.8.2','engine_build':'6400','engine_binary_sha256':BINARY,'runner_sha256':'b'*64,
            **{key:hashes[name] for key,name in {'object_trace_sha256':'object-trace.json','road_xml_sha256':'road.xodr',
                'scenario_xml_sha256':'scenario.xosc','native_csv_sha256':'native-trace.csv','native_log_sha256':'engine.log'}.items()}},
        'actors':scene_actors,'frames':deepcopy(frames),'events':[],'event_derivation_version':'object-scene-events/1',
        'limitations':['Authored SDK contract fixture; no simulator run or physical validation'],
        'map':{'kind':'authored_straight_road','id':'axiom-straight-road-r1','road_id':1,'length_m':500.,'lane_width_m':3.5,
            'source_artifact':'road.xodr','source_sha256':hashes['road.xodr'],
            'geometry_provenance':'validated_pinned_opendrive_line_and_lane_widths','reference_line':line(0.),
            'lanes':[{'id':-1,'width_m':3.5,'centerline':line(-1.75),'left_boundary':line(0.),'right_boundary':line(-3.5)},
                     {'id':-2,'width_m':3.5,'centerline':line(-5.25),'left_boundary':line(-3.5),'right_boundary':line(-7.)}]}}
    def event(kind, ids, t, method, details, indices=(), end=None, number=None):
        scene['events'].append({'kind':kind,'actor_ids':ids,'time_s':t,'end_time_s':end,'details':details,
            'provenance':{'source':'native_log' if number else 'native_trace','artifact':'engine.log' if number else 'object-trace.json',
                'sha256':hashes['engine.log' if number else 'object-trace.json'],'method':method,
                'frame_indices':list(indices),'line_numbers':[number] if number else []}})
    event('collision',['ego','other'],.5,'sampled_obb_overlap',
          {'open_at_start':False,'open_at_end':True,'before_time_s':.4,'after_time_s':None,'coordinate_plane':'world_xy',
           'interpretation':'overlap_at_native_samples_not_continuous_contact_time'},(5,11),1.1)
    event('lane_transition',['other'],.5,'sampled_lane_id_change',
          {'from_lane_id':-2,'to_lane_id':-1,'previous_time_s':.4,
           'interpretation':'native_lane_id_changed_between_samples_not_action_completion'},(4,5))
    event('stopped',['ego'],.7,'sampled_speed_threshold_crossing',
          {'threshold_mps':.05,'previous_time_s':.6,'speed_mps':0.,
           'interpretation':'sampled_speed_threshold_not_stop_action_completion'},(6,7))
    for number, aid, t, kind, state in ((3,'other',.3,'lane_change','runningState'),(4,'other',.5,'lane_change','completeState'),
                                      (6,'ego',.6,'stop','runningState'),(7,'ego',.7,'stop','completeState')):
        event('native_action',[aid],t,'esmini_event_state_log',{'action_index':0,'action_kind':kind,'engine_state':state,
              'raw_line':log_lines[number-1],'interpretation':'engine_event_transition_not_observed_behavior_completion'},number=number)
    event('collision',['ego','other'],.5,'esmini_collision_log',
          {'phase':'reported','raw_line':log_lines[4],'interpretation':'engine_reported_collision_marker'},number=5)
    if mutate: mutate(scene)
    content['object-scene.json'] = encode(scene)
    hashes['object-scene.json'] = sha(content['object-scene.json'])
    artifacts = [{'id':'artifact_'+str(i),'name':name,'sha256':hashes[name],'size_bytes':len(raw),
                  'media_type':'application/json' if name.endswith('.json') else 'text/plain',
                  'download_url':'https://untrusted.example/'+name} for i,(name,raw) in enumerate(content.items())]
    summary = {**identity,'schema_version':'object-sim-evidence/1','sample_count':len(frames),'original_artifact_hashes':hashes,
        'object_scene':{'schema_version':'object-scene/1','artifact_name':'object-scene.json',
                        'sha256':hashes['object-scene.json'],'event_count':len(scene['events'])},
        'engine_receipt':{'schema_version':'esmini-native-result/1','adapter_version':'0.1.0','engine_version':'v3.8.2','engine_build':'6400',
            'binary_sha256':BINARY,'runner_sha256':'b'*64,'seed':42,'requested_timestep_s':.1,'elapsed_s':.01,
            'fidelity':'authored_object_kinematics','physical_sensor_validation':False,
            'scenario_xml_sha256':hashes['scenario.xosc'],'road_xml_sha256':hashes['road.xodr'],'native_csv_sha256':hashes['native-trace.csv']}}
    prepared = {'schema_version':1,'input':pinned,
        'handler':{'id':'esmini-object','version':VERSION,'kinds':['object_sim'],'status':'available',
                   'fidelity':'authored_object_kinematics','resource_id':None,'cancellation_supported':False,'description':'SDK fixture'},
        'spec':{'project_id':'default','name':scenario['name'],'kind':'object_sim','adapter_id':'esmini-object','adapter_version':VERSION,
                'input':{key:identity[key] for key in ('scenario_id','scenario_revision','configuration_hash')},
                'model':None,'sensor':None,'evaluator':None,'comparison_kind':'single','seed':42}}
    if mutate_prepared: mutate_prepared(prepared)
    detail = {'id':'job_test','experiment_id':'experiment_test','project_id':'default','status':'completed','kind':'experiment',
              'experiment_kind':'object_sim','adapter_id':'esmini-object','adapter_version':VERSION,
              'input_hash':'0'*64 if bad_input_hash else sha(encode(prepared)),'summary':summary,'artifacts':artifacts,
              'fidelity':'authored_object_kinematics','manifest_hash':None,'download_url':None}
    export = encode({'schema_version':1,'execution':deepcopy(detail),'prepared':prepared})
    detail.update(manifest_hash=sha(export),download_url='/api/v1/experiments/executions/job_test/export')
    return content, detail, export


def client_for(content, detail, export, requests, corrupt=None):
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/export'):
            return httpx.Response(200, content=export)
        if '/artifacts/' in request.url.path:
            selected = next(row for row in detail['artifacts'] if row['id'] == request.url.path.rsplit('/',1)[-1])
            return httpx.Response(200, content=b'tampered' if corrupt in {True, selected['name']} else content[selected['name']])
        return httpx.Response(200, json=detail)
    return AxiomClient('http://workspace.test',token='test-token',transport=httpx.MockTransport(respond))


def test_scene_checks_complete_sealed_revision_frames_geometry_and_log_on_same_host(tmp_path):
    content, detail, export = fixture(); requests=[]
    with client_for(content, detail, export, requests) as client:
        assert client.download_object_scene('job_test',tmp_path/'scene.json').read_bytes() == content['object-scene.json']
    assert len(requests) == 5  # detail, sealed export, scene, full trace, original engine log
    assert all(request.url.host == 'workspace.test' and request.headers['Authorization'] == 'Bearer test-token' for request in requests)
    assert not list(tmp_path.glob('.axiom-*'))


@pytest.mark.parametrize('mutation', [
    lambda s:s.update(schema_version='object-scene/2'),
    lambda s:s.update(physical_sensor_validation=True),
    lambda s:s['source'].update(scenario_revision=2),
    lambda s:s['source'].update(road_xml_sha256='f'*64),
    lambda s:s['source'].update(engine_version='unsupported-engine'),
    lambda s:s['source'].update(runner_sha256='0'*64),
    lambda s:s['source'].update(engine_binary_sha256='0'*64),
    lambda s:s['frames'].pop(),
    lambda s:s['frames'][0]['actors'][0].update(x=99.),
    lambda s:s['actors'][0].update(kind='pedestrian',role='other'),
    lambda s:s['actors'][0]['dimensions_m'].update(height_m=9.),
    lambda s:s['actors'][0]['authored_intent']['actions'][0].update(kind='lane_change'),
    lambda s:s['actors'][0]['metadata'].update(height_source='native_observation'),
    lambda s:s['actors'][0]['metadata']['native_entity_id_provenance'].update(line_number=2),
    lambda s:s['map'].update(length_m=120.),
    lambda s:s['map'].update(lanes=[]),
    lambda s:s['map']['reference_line'][0].update(y=1000.),
    lambda s:s['map']['lanes'][1]['centerline'][0].update(y=-2.),
    lambda s:s['events'][0]['provenance'].update(frame_indices=[5,99]),
    lambda s:s['events'][0]['provenance'].update(sha256='0'*64),
    lambda s:s['events'][0]['details'].update(coordinate_plane='world_xyz'),
    lambda s:s['events'][1]['details'].update(from_lane_id=-1),
    lambda s:s['events'][2]['details'].update(speed_mps=1.),
    lambda s:s['events'][3]['details'].update(raw_line='invented log action'),
    lambda s:s['events'][3].update(time_s=.4),
    lambda s:s['events'][3]['provenance'].update(line_numbers=[99]),
    lambda s:s['events'][3]['details'].update(interpretation='observed_behavior_completed'),
])
def test_resealed_invalid_scene_contract_preserves_destination(tmp_path, mutation):
    content, detail, export = fixture(mutation); target = tmp_path/'scene.json'; target.write_bytes(b'prior')
    with client_for(content, detail, export, []) as client, pytest.raises(ValueError):
        client.download_object_scene('job_test',target)
    assert target.read_bytes() == b'prior' and not list(tmp_path.glob('.axiom-*'))


@pytest.mark.parametrize('mutation', [
    lambda f:f[0]['actors'][0].pop('bb_x'),
    lambda f:f[0]['actors'][0].update(bb_z=0.),
    lambda f:f[0]['actors'][0].update(x='5'),
    lambda f:f[0]['actors'][0].update(x=True),
    lambda f:f[0]['actors'][0].update(x=float('nan')),
    lambda f:f[0]['actors'][0].update(width_m=0.),
    lambda f:f[0]['actors'][0].update(lane_id=-1.),
    lambda f:f[0]['actors'].append(deepcopy(f[0]['actors'][0])),
    lambda f:f[1].update(time_s=0.),
    lambda f:f[0].update(time_s=.01),
    lambda f:f[5]['actors'][1].update(x=100.),
])
def test_resealed_scene_and_trace_both_invalid_fail_closed(tmp_path, mutation):
    content, detail, export = fixture(mutate_frames=mutation); target=tmp_path/'scene.json'; target.write_bytes(b'prior')
    with client_for(content, detail, export, []) as client, pytest.raises(ValueError):
        client.download_object_scene('job_test',target)
    assert target.read_bytes() == b'prior' and not list(tmp_path.glob('.axiom-*'))


@pytest.mark.parametrize('mutation', [
    lambda p:p['handler'].update(id='different-handler'),
    lambda p:p['spec'].update(seed=43),
    lambda p:p['spec'].update(project_id='another-project'),
    lambda p:p['input'].update(engine_binary_sha256='0'*64),
    lambda p:p['input']['scenario']['actors'][0].update(kind='unknown'),
    lambda p:p['input']['native_request'].update(actor_ids=['other','ego']),
    lambda p:p['input']['native_request'].update(scenario_xml='different original XML'),
])
def test_pinned_prepared_identity_drift_rejected_even_with_resealed_export(tmp_path, mutation):
    content, detail, export = fixture(mutate_prepared=mutation); target=tmp_path/'scene.json'; target.write_bytes(b'prior')
    with client_for(content, detail, export, []) as client, pytest.raises(ValueError):
        client.download_object_scene('job_test',target)
    assert target.read_bytes() == b'prior' and not list(tmp_path.glob('.axiom-*'))


def test_prepared_canonical_sha_is_independently_verified(tmp_path):
    content, detail, export = fixture(bad_input_hash=True); requests=[]
    with client_for(content, detail, export, requests) as client, pytest.raises(ValueError, match='input SHA-256'):
        client.download_object_scene('job_test',tmp_path/'scene.json')
    assert len(requests) == 2 and not (tmp_path/'scene.json').exists()


@pytest.mark.parametrize('source', [
    road_xml().replace('<line />','<arc curvature="0.1" />'),
    road_xml().replace('</road>','<elevationProfile /></road>'),
    '<!DOCTYPE OpenDRIVE>' + road_xml(),
])
def test_unsupported_road_source_rejected_despite_matching_all_xml_hashes(tmp_path, source):
    content, detail, export = fixture(road_override=source)
    with client_for(content, detail, export, []) as client, pytest.raises(ValueError, match='OpenDRIVE'):
        client.download_object_scene('job_test',tmp_path/'scene.json')
    assert not (tmp_path/'scene.json').exists() and not list(tmp_path.glob('.axiom-*'))


def test_native_frame_actor_order_is_preserved_without_inventing_ids(tmp_path):
    content, detail, export = fixture(mutate_frames=lambda frames:[frame['actors'].reverse() for frame in frames])
    with client_for(content, detail, export, []) as client:
        assert client.download_object_scene('job_test',tmp_path/'scene.json').read_bytes() == content['object-scene.json']


def test_optional_selected_configuration_hash_is_resolved_from_sealed_input(tmp_path):
    content, detail, export = fixture(mutate_prepared=lambda prepared:prepared['spec']['input'].update(configuration_hash=None))
    with client_for(content, detail, export, []) as client:
        assert client.download_object_scene('job_test',tmp_path/'scene.json').read_bytes() == content['object-scene.json']


@pytest.mark.parametrize('name', ['object-scene.json','object-trace.json','engine.log'])
def test_corrupt_managed_original_preserves_existing_destination(tmp_path, name):
    content, detail, export = fixture(); target=tmp_path/'scene.json'; target.write_bytes(b'prior')
    with client_for(content, detail, export, [], corrupt=name) as client, pytest.raises(ValueError, match='SHA-256'):
        client.download_object_scene('job_test',target)
    assert target.read_bytes() == b'prior' and not list(tmp_path.glob('.axiom-*'))


def test_legacy_result_remains_available_through_ordinary_artifact_downloads(tmp_path):
    content, detail, export = fixture(); detail['summary'].pop('object_scene'); requests=[]
    target=tmp_path/'scene.json'; target.write_bytes(b'prior')
    with client_for(content, detail, export, requests) as client, pytest.raises(ValueError, match='original artifact downloads'):
        client.download_object_scene('job_test',target)
    assert len(requests) == 1 and target.read_bytes() == b'prior'
