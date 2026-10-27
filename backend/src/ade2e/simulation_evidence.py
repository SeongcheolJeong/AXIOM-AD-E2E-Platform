"""Validate bounded live execution evidence without importing an engine.

This verifies identities, representation transforms, and declared command/frame
relationships. It does not prove that a plugin executed a control or that sensor
returns establish free space. Raw simulator and transformed bytes stay distinct.
"""
import base64
import io
import json
import math
import struct
from decimal import Decimal, ROUND_HALF_EVEN

from .mcap_ingest import parse_mcap
from .recording_schemas import RecordingChannel, RecordingSchema
from .schemas import ArtifactPayload, Frame
from .sensor_decoders import SensorDecoderRegistry
from .simulation_context import (AnySimulationExecutionRequest, execution_correlation, simulation_source_hash,
    validate_execution_correlation, validate_simulation_adapter_context, validate_simulation_execution_request)
from .stack_replay_sources import decode_ego, supports_ego
from .stack_schemas import StackEgo, StackPacket, validate_stack_output

KINDS = {'live_stack_trace', 'physics_trace', 'sensor_recording', 'live_stack_points',
         'live_stack_raw_points', 'live_stack_manifest'}
LIVE_KINDS = KINDS - {'physics_trace', 'sensor_recording'}


def _require(condition, message):
    if not condition:
        raise ValueError('Live stack evidence: ' + message)


def _json(content):
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(content, parse_constant=invalid, object_pairs_hook=pairs)
    _require(isinstance(value, dict), 'JSON artifact must be an object')
    return value


def _ns(seconds):
    _require(type(seconds) in (float, int) and math.isfinite(seconds) and seconds >= 0, 'invalid simulator clock')
    value = int((Decimal(str(seconds))*1_000_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
    _require(value < 2**64, 'simulator clock exceeds uint64')
    return str(value)


def _range(ref, payload, offset, stride):
    _require(isinstance(ref, dict) and ref.get('artifact_name') == payload.name, 'unknown binary artifact reference')
    size = ref.get('size_bytes')
    _require(type(size) is int and 0 <= size <= 20_000*stride and size % stride == 0, 'invalid full-frame byte count')
    _require(type(ref.get('offset_bytes')) is int and ref['offset_bytes'] == offset and offset+size <= len(payload.content), 'noncontiguous or out-of-range binary reference')
    data = payload.content[offset:offset+size]
    _require(ref.get('sha256') == simulation_source_hash(data), 'binary frame hash mismatch')
    return data, offset+size


def _control(value):
    _require(isinstance(value, dict) and set(value) == {'throttle', 'brake', 'steer', 'reverse', 'hand_brake'}, 'invalid CARLA control fields')
    for key, minimum in [('throttle', 0), ('brake', 0), ('steer', -1)]:
        number = value[key]
        _require(type(number) in (float, int) and math.isfinite(number) and minimum <= number <= 1, 'invalid bounded CARLA control')
    _require(type(value['reverse']) is bool and type(value['hand_brake']) is bool, 'invalid CARLA control flags')


def _validate_simulation_artifacts(request: AnySimulationExecutionRequest, artifact_payloads, *, completed: bool,
                                  trajectory=None, adapter_version: str | None = None) -> None:
    """Reject a missing completed chain or a contradictory retained failure prefix.

    Failure responses may contain generic transport/startup diagnostics alone.
    If any live-stack kind is supplied, its entire internally consistent prefix
    is required. Every actual MCAP message is scanned with bounded CRC checking.
    """
    request = validate_simulation_execution_request(request.model_dump(mode='json'))
    configurable = request.context.schema_version == 'simulation-control-context/2'
    if adapter_version is not None:
        validate_simulation_adapter_context(request.context, adapter_version)
    payloads = [ArtifactPayload.model_validate(p.model_dump() if isinstance(p, ArtifactPayload) else p) for p in artifact_payloads]
    _require(len(payloads) <= 100 and sum(len(p.content) for p in payloads) <= 32*1024*1024, 'artifact budget exceeded')
    _require(len({p.name for p in payloads}) == len(payloads), 'duplicate artifact name')
    sensor_configuration = None
    if configurable:
        from .simulation_sensor_pose import validate_sensor_configuration_evidence
        configurations = [p for p in payloads if p.kind == 'sensor_configuration']
        _require(len(configurations) == 1, 'context/2 requires exactly one sensor configuration readback artifact')
        configuration = configurations[0]
        _require(configuration.name == 'carla-sensor-configuration.json' and configuration.media_type == 'application/json',
                 'sensor configuration artifact identity differs')
        sensor_configuration = _json(configuration.content)
    if not completed and not any(p.kind in LIVE_KINDS for p in payloads):
        if configurable:
            validate_sensor_configuration_evidence(sensor_configuration, request.context, execution_correlation(request),
                                                  completed=False, source_frames=[])
        return
    by_kind = {}
    for kind in KINDS:
        matches = [p for p in payloads if p.kind == kind]
        _require(len(matches) == 1, f'exactly one {kind} artifact is required')
        by_kind[kind] = matches[0]
    manifest = _json(by_kind['live_stack_manifest'].content)
    trace = _json(by_kind['live_stack_trace'].content)
    physics = _json(by_kind['physics_trace'].content)
    status = 'completed' if completed else 'partial'
    for data, schema in [(manifest, 'manifest'), (trace, 'trace'), (physics, 'physics')]:
        validate_execution_correlation(data, request)
        _require(data.get('schema_version') == f'carla-live-stack-{schema}/1' and data.get('status') == status, 'version or terminal status mismatch')
    _require(manifest.get('execution_context') == request.context.model_dump(mode='json'), 'manifest configuration differs from pinned context')
    validate_simulation_adapter_context(request.context, manifest.get('worker_adapter_version'))
    if adapter_version is not None:
        _require(manifest.get('worker_adapter_version') == adapter_version, 'worker adapter version mismatch')
    n = round(request.scenario.duration_s/.05)
    for key, expected in {'scenario_id': request.scenario.id, 'scenario_revision': request.scenario.revision,
        'seed': request.scenario.seed, 'fixed_delta_seconds': .05, 'expected_intervals': n, 'expected_observations': n+1}.items():
        _require(manifest.get(key) == expected, f'manifest {key} differs from request')
    expected = [{'name': p.name, 'kind': p.kind, 'sha256': simulation_source_hash(p.content), 'size_bytes': len(p.content)}
                for p in payloads if p.kind != 'live_stack_manifest']
    _require(manifest.get('artifacts') == expected, 'manifest artifact list/hash/size mismatch')
    ticks, rows = trace.get('ticks'), physics.get('observations')
    _require(isinstance(ticks, list) and len(ticks) == n and trace.get('expected_intervals') == n, 'tick denominator differs from requested intervals')
    _require(isinstance(rows, list) and len(rows) <= n+1, 'physics observation denominator is invalid')
    frames = []
    for index, row in enumerate(rows):
        _require(type(row.get('world_frame')) is int and 0 <= row['world_frame'] < 2**32, 'invalid world frame')
        _ns(row.get('source_time_s'))
        frame = Frame.model_validate(row.get('frame'))
        _require(abs(frame.time_s-index*.05) <= 1e-6, 'physics relative time is inconsistent')
        if index:
            _require(row['world_frame'] == rows[index-1]['world_frame']+1 and abs(row['source_time_s']-rows[index-1]['source_time_s']-.05) <= 1e-6, 'physics frame/time gap')
        frames.append(frame)
    _require(manifest.get('observed_frames') == len(rows) and manifest.get('last_observed_world_frame') == (rows[-1]['world_frame'] if rows else None), 'manifest physics count mismatch')
    if completed:
        _require(len(rows) == n+1 and manifest.get('error') is None and not manifest.get('cleanup_errors'), 'completed chain lacks clean final observation')
        if trajectory is not None:
            _require([f.model_dump(mode='json') for f in frames] == [Frame.model_validate(f.model_dump() if isinstance(f, Frame) else f).model_dump(mode='json') for f in trajectory], 'result trajectory differs from physics evidence')

    response_payloads = [p for p in payloads if p.kind == 'vehicle_response']
    _require(len(response_payloads) <= 1, 'duplicate vehicle response evidence')
    if manifest.get('worker_adapter_version') in {'0.4.1', '0.5.0', '0.6.0', '0.7.0', '0.8.0'}:
        _require(len(response_payloads) == 1, 'This CARLA version requires vehicle response evidence')
    if response_payloads:
        from .vehicle_response import validate_vehicle_response
        startup_payloads = [p for p in payloads if p.kind == 'startup_manifest']
        _require(len(startup_payloads) <= 1, 'duplicate startup evidence')
        response_report = validate_vehicle_response(request, response_payloads[0], physics,
            _json(startup_payloads[0].content) if startup_payloads else None,
            completed=completed, adapter_version=manifest.get('worker_adapter_version'))
        _require(response_report.server_version == manifest.get('server_version'), 'vehicle response server version differs')
        if response_report.after_rest is not None:
            _require(response_report.after_rest.actors['ego'].actor_id == manifest.get('ego_actor_id'),
                'vehicle response ego differs from acknowledged control actor')
        for index, observation in enumerate(response_report.observations[1:]):
            readback = observation.actors['ego'].reported_control
            recorded = ticks[index].get('observed_control')
            if readback.status == 'available' and recorded is not None:
                _require({key: getattr(readback.data, key) for key in recorded} == recorded,
                    'vehicle response control readback differs from live trace')

    class BoundedPayloadBuffer(io.BytesIO):
        def write(self, data):
            _require(self.tell()+len(data) <= 18*1024*1024, 'expanded source payload budget exceeded')
            return super().write(data)
    sidecar = BoundedPayloadBuffer()
    parsed = parse_mcap(by_kind['sensor_recording'].content, sidecar)
    _require(parsed.payload_size <= 18*1024*1024 and len(parsed.messages) <= 162, 'source recording budget exceeded')
    _require(set(parsed.channels) == {1, 2}, 'source recording channels differ from fixed roles')
    channels = {key: RecordingChannel.model_validate(value) for key, value in parsed.channels.items()}
    sensor_frame = request.context.sensor_profile.sensor_frame if configurable else 'lidar_roof'
    if configurable:
        _require(channels[1].metadata.get('frame_id') == sensor_frame,
                 'recorded LiDAR channel frame differs from selected rig sensor')
    schemas = {key: RecordingSchema.model_validate(value) for key, value in parsed.schemas.items()}
    _require(SensorDecoderRegistry().get('foxglove-json', '1').kind_for(channels[1], schemas[channels[1].schema_id]) == 'lidar', 'unsupported recorded LiDAR schema')
    _require(supports_ego(channels[2], schemas[channels[2].schema_id]), 'unsupported recorded ego schema')
    metadata = [item['metadata'] for item in parsed.metadata if item['name'] == 'axiom.live-stack.provenance']
    _require(len(metadata) == 1, 'missing or duplicate recording provenance')
    source = _json(metadata[0].get('context', ''))
    validate_execution_correlation(source, request)
    for key, expected in {'status': status, 'sensor_profile': request.context.sensor_profile.model_dump(mode='json'),
        'rig_snapshot': request.context.rig_snapshot.model_dump(mode='json'), 'ego_source_policy': request.context.ego_source_policy}.items():
        _require(source.get(key) == expected, 'source provenance differs from pinned context')
    observations = source.get('observations')
    _require(isinstance(observations, list) and len(observations) <= len(rows) and len(parsed.messages) == len(observations)*2, 'source prefix count mismatch')
    _require(manifest.get('retained_source_observations') == len(observations), 'manifest source count mismatch')
    if completed:
        _require(len(observations) == n+1, 'completed chain lacks terminal sensor observation')
    raw_offset = xyz_offset = 0
    source_bytes = sidecar.getvalue()
    for index, observation in enumerate(observations):
        _require(observation.get('world_frame') == rows[index]['world_frame'] and observation.get('source_time_s') == rows[index]['source_time_s'], 'source/physics observation mismatch')
        at = _ns(observation['source_time_s'])
        _require(_ns(observation.get('lidar_timestamp_s')) == at, 'source clocks do not match coherent snapshot')
        packet = StackPacket.model_validate(observation.get('packet'))
        _require(packet.sequence == index and packet.at_ns == at, 'source packet sequence/clock mismatch')
        decoded = {}
        refs = observation.get('source_refs', {})
        for role, channel_id in [('lidar', 1), ('ego', 2)]:
            message = parsed.messages[index*2+channel_id-1]
            ref = refs.get(role, {})
            expected = {'channel_id': channel_id, 'ordinal': message['ordinal'], 'message_sha256': message['sha256'],
                'message_size_bytes': message['payload_length'], 'world_frame': observation['world_frame'], 'log_time_ns': at, 'publish_time_ns': at}
            _require(message['channel_id'] == channel_id and message['sequence'] == observation['world_frame'] and str(int(message['log_time_key'])) == at and str(int(message['publish_time_key'])) == at, 'MCAP message identity/clock mismatch')
            _require(all(ref.get(key) == value for key, value in expected.items()), 'source message reference mismatch')
            if configurable and role == 'lidar':
                _require(ref.get('frame_id') == sensor_frame, 'source reference frame differs from selected rig sensor')
            offset, length = message['payload_offset'], message['payload_length']
            decoded[role] = _json(source_bytes[offset:offset+length])
        ego = decode_ego(json.dumps(decoded['ego'], allow_nan=False).encode())
        _require(packet.ego == StackEgo(**ego.model_dump(), capture_time_ns=at) and ego.timestamp_ns == at and ego.frame_id == 'ego' and ego.source == request.context.ego_source_policy, 'ego packet differs from original telemetry')
        raw, raw_offset = _range(refs['lidar'].get('raw_points'), by_kind['live_stack_raw_points'], raw_offset, 16)
        _require(refs['lidar'].get('raw_carla_xyzi_sha256') == simulation_source_hash(raw) and refs['lidar']['raw_points'].get('encoding') == 'xyzi_float32_le_carla', 'raw source encoding/hash mismatch')
        cloud = decoded['lidar']
        reflected = bytearray(raw)
        for offset in range(7, len(reflected), 16): reflected[offset] ^= 0x80
        _require(base64.b64decode(cloud.get('data', ''), validate=True) == reflected, 'MCAP LiDAR differs from exact reflected raw bytes')
        _require(cloud.get('point_stride') == 16 and cloud.get('fields') == [{'name': name, 'offset': i*4, 'type': 7} for i, name in enumerate(('x','y','z','intensity'))]
            and cloud.get('frame_id') == sensor_frame and cloud.get('timestamp') == {'sec': int(at)//10**9, 'nsec': int(at)%10**9}
            and cloud.get('pose') == {'position': {'x': 0, 'y': 0, 'z': 0}, 'orientation': {'x': 0, 'y': 0, 'z': 0, 'w': 1}}, 'recorded LiDAR layout/frame differs from pinned recipe')
        if packet.lidar is None:
            _require(observation.get('points') is None and bool(packet.unavailable_reasons), 'unavailable geometry lacks explicit reason')
        else:
            points, xyz_offset = _range(observation.get('points'), by_kind['live_stack_points'], xyz_offset, 12)
            point_ref, lidar = observation['points'], packet.lidar
            _require(point_ref.get('point_count') == lidar.point_count and point_ref.get('frame_id') == lidar.frame_id == 'ego'
                and lidar.points_sha256 == simulation_source_hash(points) and lidar.size_bytes == len(points)
                and lidar.source_message_sha256 == refs['lidar']['message_sha256'] and lidar.complete_frame
                and lidar.timestamp_ns == lidar.capture_time_ns == at, 'consumed geometry descriptor mismatch')
            transformed = bytearray()
            qx, qy, qz, qw = request.context.sensor_profile.mount.rotation_xyzw
            dx, dy, dz = request.context.sensor_profile.mount.translation_m
            for x, native_y, z, intensity in struct.iter_unpack('<ffff', raw):
                _require(all(math.isfinite(v) for v in (x,native_y,z,intensity)), 'invalid raw input cannot be consumed')
                y = -native_y
                # Preserve the native and recorded-replay operation order. Even
                # an identity quaternion plus zero translation can change the
                # sign bit of IEEE-754 zero; algebraic shortcuts are not byte
                # equivalent to the declared transformation implementation.
                tx, ty, tz = 2*(qy*z-qz*y), 2*(qz*x-qx*z), 2*(qx*y-qy*x)
                point = (x+qw*tx+qy*tz-qz*ty+dx, y+qw*ty+qz*tx-qx*tz+dy, z+qw*tz+qx*ty-qy*tx+dz)
                _require(all(math.isfinite(v) and abs(v) <= 1_000_000 for v in point), 'invalid raw input cannot be consumed')
                transformed.extend(struct.pack('<fff', *point))
            _require(bytes(transformed) == points, 'consumed points differ from declared source-to-ego transform')
    _require(raw_offset == len(by_kind['live_stack_raw_points'].content) and xyz_offset == len(by_kind['live_stack_points'].content), 'unreferenced binary bytes')
    if configurable:
        validate_sensor_configuration_evidence(sensor_configuration, request.context, execution_correlation(request),
                                              completed=completed, source_frames=[row['world_frame'] for row in observations])
        for mount_row, observation in zip(sensor_configuration['observations'], observations):
            _require(mount_row['source_time_s'] == observation['source_time_s']
                     and mount_row['ego_actor_id'] == manifest.get('ego_actor_id'),
                     'sensor mount time/ego differs from the retained native source')
        startup_mount = sensor_configuration['startup_mount']
        if startup_mount is not None and manifest.get('ego_actor_id') is not None:
            _require(startup_mount['ego_actor_id'] == manifest['ego_actor_id'],
                     'sensor startup ego differs from the run control actor')

    incomplete_seen = False
    previous_state = None
    for index, tick in enumerate(ticks):
        _require(tick.get('schema_version') == 'carla-live-stack-tick/1' and tick.get('sequence') == index, 'tick schema/sequence mismatch')
        tick_status = tick.get('status')
        _require(tick_status in {'completed', 'failed', 'not_attempted'}, 'terminal trace has invalid or running tick')
        _require(not incomplete_seen or tick_status == 'not_attempted', 'ticks are not a completed/failed/unattempted prefix')
        incomplete_seen |= tick_status != 'completed'
        if completed: _require(tick_status == 'completed', 'completed result has incomplete tick')
        if tick_status == 'not_attempted':
            _require(all(tick.get(key) is None for key in ('source_world_frame','source_time_s','packet','output','points','requested_carla_control','control_rpc_response','next_world_frame','next_time_s','observed_control','next_ego')) and tick.get('source_refs') == {'lidar': None, 'ego': None} and tick.get('control_acknowledged') is False and tick.get('application_status') == 'not_sent', 'unattempted tick contains execution claims')
            continue
        _require(index < len(observations), 'attempted tick lacks retained source')
        observation = observations[index]
        for key, source_key in [('source_world_frame','world_frame'),('source_time_s','source_time_s'),('packet','packet'),('source_refs','source_refs'),('points','points')]:
            _require(tick.get(key) == observation.get(source_key), 'tick differs from retained source observation')
        packet = StackPacket.model_validate(tick['packet'])
        output = validate_stack_output(tick['output']) if tick.get('output') is not None else None
        if output:
            _require(output.kernel_version == request.context.stack_snapshot.kernel_version
                and output.sequence == index and output.at_ns == packet.at_ns and output.components == request.context.stack_snapshot.component_metadata
                and output.input_points_sha256 == (packet.lidar.points_sha256 if packet.lidar else None), 'output identity/component/input mismatch')
            if packet.unavailable_reasons or packet.lidar is None:
                stop_modes = {'unknown_stop', 'emergency_stop', 'hold'} if output.kernel_version == 'stack-kernel/2' else {'unknown_stop'}
                _require(output.decision.mode in stop_modes and output.control.brake == 1 and output.control.throttle == 0, 'unavailable input was treated as motion permission')
                if output.kernel_version == 'stack-kernel/2':
                    _require(not output.observation.input_valid and output.observation.status == 'invalid'
                        and output.observation.coverage.status == 'invalid'
                        and output.observation.coverage.qualified_prefix_m is None
                        and output.observation.positive_surface is None and output.decision.force_brake,
                        'unavailable input claims valid observed geometry')
            if output.kernel_version == 'stack-kernel/2':
                if index == 0:
                    _require(output.state_before.mode == 'ready' and not output.state_before.latched
                        and output.state_before.latch_reason is None and output.state_before.stop_plan is None
                        and output.state_before.last_sequence is None and output.state_before.last_at_ns is None
                        and output.state_before.previous_speed_mps is None
                        and output.state_before.previous_target_acceleration_mps2 is None,
                        'stateful execution does not begin with a new session')
                else:
                    _require(previous_state is not None and output.state_before == previous_state,
                        'stateful execution prefix is discontinuous')
                _require(output.state_after.last_sequence == index and output.state_after.last_at_ns == packet.at_ns,
                    'stateful output clock/sequence differs from consumed packet')
                from .stack_v2_runtime import validate_v2_source_evidence
                ref = observation.get('points')
                source = (by_kind['live_stack_points'].content[ref['offset_bytes']:ref['offset_bytes']+ref['size_bytes']]
                          if ref is not None else None)
                validate_v2_source_evidence(request.context.stack_snapshot.definition, packet, source, output)
                previous_state = output.state_after
            control = output.control
            requested = {'throttle': control.throttle, 'brake': control.brake, 'steer': -control.steer, 'reverse': False, 'hand_brake': False}
            _require(tick.get('requested_carla_control') == requested, 'requested CARLA control differs from kernel output')
        else:
            _require(tick.get('requested_carla_control') is None, 'requested control lacks kernel output')
        app_status = tick.get('application_status')
        _require(app_status in {'not_sent','send_rejected','send_outcome_unknown','acknowledged_without_next_observation','next_frame_observed'} and type(tick.get('control_acknowledged')) is bool, 'invalid command acknowledgement')
        _require(tick['control_acknowledged'] == (app_status in {'acknowledged_without_next_observation','next_frame_observed'}), 'command acknowledgement/status mismatch')
        if app_status != 'not_sent': _require(output is not None, 'sent command lacks output')
        response = tick.get('control_rpc_response')
        if response is not None:
            _require(isinstance(response, dict) and set(response) == {'actor_id', 'error'}
                and (response['actor_id'] is None or type(response['actor_id']) is int and response['actor_id'] > 0)
                and (response['error'] is None or isinstance(response['error'], str) and 0 < len(response['error']) <= 2000), 'invalid control RPC response')
        if tick['control_acknowledged']:
            _require(type(manifest.get('ego_actor_id')) is int and manifest['ego_actor_id'] > 0
                and response == {'actor_id': manifest['ego_actor_id'], 'error': None}, 'acknowledgement lacks matching successful server response')
        if app_status == 'send_rejected':
            _require(response is not None and response['actor_id'] is None and bool(response['error']), 'rejected send lacks explicit server error')
        if app_status == 'not_sent':
            _require(response is None, 'unsent command contains an RPC response')
        if app_status == 'next_frame_observed':
            _require(index+1 < len(rows) and tick.get('next_world_frame') == rows[index+1]['world_frame'] and tick.get('next_time_s') == rows[index+1]['source_time_s'] and tick.get('next_ego') == frames[index+1].ego.model_dump(mode='json'), 'command outcome lacks matching next physics frame')
            if tick.get('observed_control') is not None: _control(tick['observed_control'])
        else:
            _require(all(tick.get(key) is None for key in ('next_world_frame','next_time_s','next_ego','observed_control')), 'unobserved command claims a next frame')
        if tick_status == 'completed':
            _require(app_status == 'next_frame_observed' and tick.get('observed_control') is not None and index+1 < len(observations), 'completed tick lacks command readback/next sensor observation')
    _require(manifest.get('acknowledged_commands') == sum(t['control_acknowledged'] for t in ticks)
        and manifest.get('next_frame_observed_commands') == sum(t['application_status'] == 'next_frame_observed' for t in ticks), 'manifest command counts mismatch')


def validate_simulation_artifacts(request: AnySimulationExecutionRequest, artifact_payloads, *, completed: bool,
                                  trajectory=None, adapter_version: str | None = None) -> None:
    """Public pure contract validator; malformed plugin data raises ValueError."""
    try:
        _validate_simulation_artifacts(request, artifact_payloads, completed=completed,
                                      trajectory=trajectory, adapter_version=adapter_version)
    except (TypeError, KeyError, IndexError, AttributeError, RecursionError, OverflowError, struct.error) as error:
        raise ValueError('Live stack evidence has a malformed bounded record') from error
