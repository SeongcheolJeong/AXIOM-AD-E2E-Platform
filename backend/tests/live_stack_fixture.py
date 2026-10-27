"""Authored full evidence chain for core protocol tests; no simulator measurements."""
import base64
import io
import json
import struct
from mcap.writer import Writer, CompressionType

from ade2e.schemas import ArtifactPayload
from ade2e.service import canonical_bytes
from ade2e.simulation_context import execution_correlation, simulation_source_hash as sha
from ade2e.stack_replay_sources import EGO_SCHEMA
from ade2e.stack_runtime import StackSession
from ade2e.stack_schemas import StackPacket, StackEgo, StackLidar
from test_sensors import sensor_schema


def make_artifacts(request, trajectory, adapter_version='context-fixture/1', *, partial=False, completed_ticks=None, raw=None, points_override=None):
    """Independent MCAP writer and control trace; preserves exact source bytes."""
    n = round(request.scenario.duration_s/.05)
    count = (0 if completed_ticks is None else completed_ticks+1) if partial else n+1
    count = min(count, n+1)
    completed_ticks = max(0, count-1) if partial else n
    identity = execution_correlation(request)
    status = 'partial' if partial else 'completed'
    raw = raw if raw is not None else struct.pack('<ffff', 4., 1., -2.2, .5)
    reflected = bytearray(raw)
    for offset in range(7, len(reflected), 16): reflected[offset] ^= 0x80
    qx,qy,qz,qw = request.context.sensor_profile.mount.rotation_xyzw
    dx,dy,dz = request.context.sensor_profile.mount.translation_m
    transformed = bytearray()
    for x,native_y,z,_ in struct.iter_unpack('<ffff',raw):
        y = -native_y
        tx,ty,tz = 2*(qy*z-qz*y), 2*(qz*x-qx*z), 2*(qx*y-qy*x)
        transformed.extend(struct.pack('<fff',x+qw*tx+qy*tz-qz*ty+dx,y+qw*ty+qz*tx-qx*tz+dy,z+qw*tz+qx*ty-qy*tx+dz))
    points = bytes(transformed)
    if points_override is not None:
        points = points_override
    stream = io.BytesIO()
    writer = Writer(stream, compression=CompressionType.ZSTD, enable_data_crcs=True)
    writer.start(profile='json')
    ls = writer.register_schema('foxglove.PointCloud', 'jsonschema', sensor_schema('foxglove.PointCloud'))
    es = writer.register_schema('axiom.EgoState.v1', 'jsonschema', canonical_bytes(EGO_SCHEMA))
    writer.register_channel('/lidar/roof/points', 'json', ls)
    writer.register_channel('/ego/state', 'json', es)
    observations, physics, ticks = [], [], []
    for index in range(count):
        at = 10_000_000_000+index*50_000_000
        frame = trajectory[index]
        source = {'timestamp_ns': str(at), 'frame_id': 'ego', 'speed_mps': float(frame.ego.speed_mps),
            'route_heading_error_rad': 0., 'pitch_rad': 0., 'roll_rad': 0., 'source': request.context.ego_source_policy}
        cloud = {'timestamp': {'sec': at//10**9, 'nsec': at%10**9}, 'frame_id': 'lidar_roof',
            'pose': {'position': {'x':0,'y':0,'z':0}, 'orientation': {'x':0,'y':0,'z':0,'w':1}},
            'point_stride': 16, 'fields': [{'name': name, 'offset': i*4, 'type':7} for i,name in enumerate(('x','y','z','intensity'))],
            'data': base64.b64encode(reflected).decode()}
        refs = {}
        for channel, role, content in [(1,'lidar',canonical_bytes(cloud)), (2,'ego',canonical_bytes(source))]:
            writer.add_message(channel, at, content, at, sequence=100+index)
            refs[role] = {'channel_id':channel, 'ordinal': index*2+channel-1, 'message_sha256':sha(content),
                'message_size_bytes':len(content), 'world_frame':100+index, 'log_time_ns':str(at), 'publish_time_ns':str(at)}
        refs['lidar'].update(raw_carla_xyzi_sha256=sha(raw), raw_points={'artifact_name':'carla-stack-raw-xyzi.bin',
            'offset_bytes':index*len(raw), 'size_bytes':len(raw), 'sha256':sha(raw), 'encoding':'xyzi_float32_le_carla'})
        point_ref = {'artifact_name':'carla-stack-points.bin', 'offset_bytes':index*len(points), 'size_bytes':len(points),
            'sha256':sha(points), 'point_count':len(points)//12, 'frame_id':'ego'}
        packet = StackPacket(sequence=index, at_ns=str(at), ego=StackEgo(**source,capture_time_ns=str(at)),
            lidar=StackLidar(timestamp_ns=str(at),capture_time_ns=str(at),frame_id='ego',point_count=len(points)//12,
                points_sha256=sha(points),size_bytes=len(points),source_message_sha256=refs['lidar']['message_sha256'],complete_frame=True))
        observations.append({'world_frame':100+index, 'source_time_s':10+index*.05, 'lidar_timestamp_s':10+index*.05,
            'source_refs':refs, 'points':point_ref, 'packet':packet.model_dump(mode='json')})
        physics.append({'world_frame':100+index, 'source_time_s':10+index*.05, 'frame':frame.model_dump(mode='json')})
    session = StackSession(request.context.stack_snapshot.definition)
    for index in range(n):
        tick = {'schema_version':'carla-live-stack-tick/1','sequence':index,'status':'not_attempted',
            'source_world_frame':None,'source_time_s':None,'packet':None,'output':None,'source_refs':{'lidar':None,'ego':None},
            'points':None,'requested_carla_control':None,'control_acknowledged':False,'control_rpc_response':None,
            'next_world_frame':None,'next_time_s':None,'observed_control':None,'next_ego':None,'application_status':'not_sent','error':None}
        if index < completed_ticks:
            obs = observations[index]
            output = session.step(StackPacket.model_validate(obs['packet']),points)
            control = output.control
            requested = {'throttle':control.throttle,'brake':control.brake,'steer':-control.steer,'reverse':False,'hand_brake':False}
            tick.update(status='completed',source_world_frame=obs['world_frame'],source_time_s=obs['source_time_s'],
                packet=obs['packet'],source_refs=obs['source_refs'],points=obs['points'],output=output.model_dump(mode='json'),
                requested_carla_control=requested,control_acknowledged=True,control_rpc_response={'actor_id':1,'error':None},
                next_world_frame=physics[index+1]['world_frame'],next_time_s=physics[index+1]['source_time_s'],
                next_ego=trajectory[index+1].ego.model_dump(mode='json'),observed_control=requested,application_status='next_frame_observed')
        ticks.append(tick)
    provenance = {**identity,'status':status,'sensor_profile':request.context.sensor_profile.model_dump(mode='json'),
        'rig_snapshot':request.context.rig_snapshot.model_dump(mode='json'),'ego_source_policy':request.context.ego_source_policy,'observations':observations}
    writer.add_metadata('axiom.live-stack.provenance', {'context':canonical_bytes(provenance).decode()})
    writer.finish()
    def blob(name,kind,data,media='application/json'):
        return ArtifactPayload(name=name,kind=kind,media_type=media,content=data)
    payloads = [blob('carla-stack-trace.json','live_stack_trace',canonical_bytes({'schema_version':'carla-live-stack-trace/1',**identity,'status':status,'expected_intervals':n,'ticks':ticks})),
        blob('carla-stack-physics.json','physics_trace',canonical_bytes({'schema_version':'carla-live-stack-physics/1',**identity,'status':status,'observations':physics})),
        blob('carla-stack-sources.mcap','sensor_recording',stream.getvalue(),'application/mcap'),
        blob('carla-stack-raw-xyzi.bin','live_stack_raw_points',raw*count,'application/octet-stream'),
        blob('carla-stack-points.bin','live_stack_points',points*count,'application/octet-stream')]
    manifest = {'schema_version':'carla-live-stack-manifest/1',**identity,'status':status,'worker_adapter_version':adapter_version,
        'execution_context':request.context.model_dump(mode='json'),'scenario_id':request.scenario.id,'scenario_revision':request.scenario.revision,
        'seed':request.scenario.seed,'fixed_delta_seconds':.05,'expected_intervals':n,'expected_observations':n+1,'observed_frames':count,
        'retained_source_observations':count,'acknowledged_commands':completed_ticks,'next_frame_observed_commands':completed_ticks,
        'last_observed_world_frame':physics[-1]['world_frame'] if physics else None,'error':'Authored failure' if partial else None,
        'cleanup_errors':[],'ego_actor_id':1 if count else None,'artifacts':[{'name':p.name,'kind':p.kind,'sha256':sha(p.content),'size_bytes':len(p.content)} for p in payloads]}
    return [*payloads,blob('carla-stack-manifest.json','live_stack_manifest',canonical_bytes(manifest))]
