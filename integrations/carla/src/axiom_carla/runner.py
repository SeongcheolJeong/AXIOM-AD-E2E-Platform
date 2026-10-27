"""Experimental straight-road CARLA implementation.

This is a ground-truth controller with sensor capture, not a perception stack.
No server reload, no Traffic Manager ownership, no global actor deletion.
"""
from __future__ import annotations

import importlib
import hashlib
import json
import math
import os
import queue
import struct
import sys
import threading
import time

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.schemas import Actor, ArtifactPayload, EgoState, Event, Frame, Metric, Scenario, SimulationResult
from .encoding import bgra_png
from .recording import CapturedFrame, capture_recording
from .startup import StartupFailure, wait_for_stability
from .vehicle_response import read_physics_configuration, require_physics_configuration
from . import durability, native_journal as journal


def _angle(degrees: float) -> float:
    return (degrees + 180) % 360 - 180


def _speed(actor) -> float:
    velocity = actor.get_velocity()
    return math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)


def _state(actor) -> EgoState:
    transform = actor.get_transform()
    # CARLA is left-handed (Y right). Application world is XY, Y left.
    return EgoState(x=transform.location.x, y=-transform.location.y,
                    yaw=-math.radians(transform.rotation.yaw), speed_mps=_speed(actor))


def _sensor_frame(stream: queue.Queue, frame_id: int, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = stream.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise AdapterUnavailable(f"Sensor timed out waiting for frame {frame_id}") from exc
        if item.frame == frame_id:
            return item
        if item.frame > frame_id:
            raise AdapterUnavailable(f"Sensor frame gap: expected {frame_id}, received {item.frame}")
    raise AdapterUnavailable("Sensor alignment deadline exceeded")


def _copy_transform(carla, value, z_offset=0.0):
    """Native CARLA values need explicit copies; map spawn objects stay intact."""
    return carla.Transform(carla.Location(x=value.location.x, y=value.location.y, z=value.location.z+z_offset),
        carla.Rotation(pitch=value.rotation.pitch, yaw=value.rotation.yaw, roll=value.rotation.roll))


def _transform_evidence(value):
    return {'position_m': {key: float(getattr(value.location,key)) for key in ('x','y','z')},
        'rotation_deg': {key: float(getattr(value.rotation,key)) for key in ('pitch','yaw','roll')}}


def _startup_payload(report, context):
    content = json.dumps({**report, 'context': context}, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return ArtifactPayload(name='carla-startup.json', kind='startup_manifest', media_type='application/json', content=content)


class CarlaRunner:
    dt = 0.05

    def execution_context_capabilities(self):
        from ade2e.simulation_sensor_capabilities import simulation_sensor_capabilities
        return simulation_sensor_capabilities()

    def validate_execution_context(self, scenario, context):
        from .stack_bridge import validate_context
        validate_context(scenario, context)

    def preflight_with_context(self, scenario, context):
        from .stack_bridge import connect_preflight
        _, _, _, version, road_map, _, _ = connect_preflight(self, scenario, context)
        return {"status": "ready", "version": version, "map": road_map.name}

    def execute_with_context(self, request):
        from .stack_bridge import run_live_stack
        return run_live_stack(self, request)

    def execute_physics_candidate(self, request, physics_profile):
        """Explicit standalone commissioning; ordinary execution stays unchanged."""
        from .stack_bridge import run_live_stack
        return run_live_stack(self, request, physics_profile=physics_profile)

    def _connect(self):
        if os.getenv("ADE2E_CARLA_EXCLUSIVE") != "1":
            raise AdapterUnavailable("Set ADE2E_CARLA_EXCLUSIVE=1 only for a dedicated disposable server")
        try:
            carla = importlib.import_module("carla")
            client = carla.Client(os.getenv("ADE2E_CARLA_HOST", "127.0.0.1"),
                                  int(os.getenv("ADE2E_CARLA_PORT", "2000")))
            client.set_timeout(10)
            server_version, client_version = client.get_server_version(), client.get_client_version()
            if server_version != client_version:
                raise AdapterUnavailable(f"CARLA version mismatch: client {client_version}, server {server_version}")
            world = client.get_world()
            return carla, client, world, server_version
        except (ImportError, RuntimeError, ValueError) as exc:
            raise AdapterUnavailable(f"CARLA preflight failed: {exc}") from exc

    def preflight(self) -> dict:
        _, _, world, version = self._connect()
        return {"status": "ready", "version": version, "map": world.get_map().name,
                "scope": "Experimental controller and sensor capture; not perception validation"}

    def _straight_spawn(self, road_map, scenario):
        points = road_map.get_spawn_points()
        if not points:
            raise AdapterInputError("Current map has no vehicle spawn points")
        distance = scenario.parameters.initial_gap_m + 5
        horizon = distance + max(scenario.parameters.ego_speed_mps,
                                 scenario.parameters.target_speed_mps) * scenario.duration_s + 30
        for offset in range(len(points)):
            transform = points[(scenario.seed + offset) % len(points)]
            waypoint = road_map.get_waypoint(transform.location)
            start = waypoint
            traversed = 0
            valid = True
            while traversed < horizon:
                if waypoint.is_junction or abs(_angle(waypoint.transform.rotation.yaw - transform.rotation.yaw)) > 3:
                    valid = False
                    break
                options = waypoint.next(5)
                if len(options) != 1:
                    valid = False
                    break
                waypoint = options[0]
                traversed += 5
            lead_options = start.next(distance)
            if valid and len(lead_options) == 1:
                return transform, lead_options[0].transform
        raise AdapterInputError("No unbranched straight road fits this speed/duration/gap; choose a suitable map or shorten the trial")

    @staticmethod
    def _control(carla, road_map, actor, target_speed: float, force_brake: bool = False):
        transform = actor.get_transform()
        waypoint = road_map.get_waypoint(transform.location)
        candidates = waypoint.next(max(4, _speed(actor) * 0.6))
        if not candidates:
            raise AdapterInputError("Vehicle reached the end of supported road geometry")
        aim = candidates[0].transform.location
        desired_yaw = math.degrees(math.atan2(aim.y - transform.location.y, aim.x - transform.location.x))
        error = target_speed - _speed(actor)
        requested = carla.VehicleControl(
            throttle=0 if force_brake else max(0, min(0.7, error * 0.25)),
            brake=1 if force_brake else max(0, min(0.8, -error * 0.2)),
            steer=max(-0.5, min(0.5, _angle(desired_yaw - transform.rotation.yaw) / 45)),
        )
        journal.mutation('actor.apply_control', lambda: actor.apply_control(requested),
            {'actor_id': actor.id, 'requested': {name: getattr(requested, name) for name in ('throttle', 'brake', 'steer')},
             'transport': 'asynchronous; return is not server acknowledgement'} if durability.enabled() else None)

    def execute(self, scenario: Scenario) -> SimulationResult:
        if scenario.category not in ("lane_follow", "emergency_brake"):
            raise AdapterInputError("Only lane_follow and emergency_brake are implemented")
        if scenario.duration_s > 20 or abs(scenario.duration_s / self.dt - round(scenario.duration_s / self.dt)) > 1e-6:
            raise AdapterInputError("Duration must be 2–20 seconds in 0.05 second increments")
        carla, _, world, version = self._connect()
        road_map = world.get_map()
        if scenario.map_name != road_map.name.split("/")[-1]:
            raise AdapterInputError(f"Scenario map_name must match loaded map {road_map.name.split('/')[-1]}; no automatic map reload")
        existing = world.get_actors()
        if list(existing.filter("vehicle.*")) or list(existing.filter("walker.*")) or list(existing.filter("sensor.*")):
            raise AdapterInputError("Dedicated world must have no other vehicles, walkers or sensors")
        original_settings = world.get_settings()
        if original_settings.synchronous_mode:
            raise AdapterInputError("World already uses synchronous mode; refusing to share tick ownership")
        original_weather = world.get_weather()
        ego_transform, lead_transform = self._straight_spawn(road_map, scenario)
        actors, sensors = [], []
        frames, events, payloads = [], [], []
        collision_events: list[tuple[int, float]] = []
        collision_lock = threading.Lock()

        def record_collision(event):
            durable.collisions.receive(event)
            with collision_lock:
                collision_events.append((event.other_actor.id, event.timestamp))

        p = scenario.parameters
        capture = os.getenv("ADE2E_CARLA_CAPTURE", "1") == "1"
        cleanup_errors = []
        world_mutation_started = False
        durable = journal.LegacyEvidence(scenario, capture)
        startup_summary = None
        from .version import ADAPTER_VERSION
        startup_context = {'adapter_version': ADAPTER_VERSION, 'scenario_id': scenario.id, 'scenario_revision': scenario.revision, 'seed': scenario.seed,
            'server_version': version, 'map_name': road_map.name, 'stage': 'rest_before_initial_target_velocity',
            'requested_initial_speeds_mps': {'ego': p.ego_speed_mps, 'lead': p.target_speed_mps},
            'spawn_z_offset_m': 0.3, 'spawn_transforms': {}}
        try:
            journal.begin(world, original_settings, original_weather, mode='legacy_gt', scenario=scenario, version=version)
            durable.checkpoint(payloads, frames, events, 'before_world_mutation')
            settings = world.get_settings()
            settings.synchronous_mode = True
            settings.fixed_delta_seconds = self.dt
            settings.substepping = True
            settings.max_substep_delta_time = 0.01
            settings.max_substeps = 10
            settings.no_rendering_mode = False if capture else original_settings.no_rendering_mode
            durability.event('world_mutation_intent', {'operation': 'world.apply_settings', 'world_id': str(world.id)} if durability.enabled() else {})
            world_mutation_started = True
            journal.mutation('world.apply_settings', lambda: world.apply_settings(settings), {'requested': journal.fields(settings, journal.SETTINGS)} if durability.enabled() else None)
            weather = carla.WeatherParameters(
                cloudiness=80 if scenario.weather != "clear" else 10,
                precipitation=70 if scenario.weather == "rain" else 0,
                wetness=80 if scenario.weather == "rain" else 0,
                fog_density=60 if scenario.weather == "fog" else 0,
                sun_altitude_angle={"day": 65, "dusk": 5, "night": -30}[scenario.time_of_day],
            )
            journal.mutation('world.set_weather', lambda: world.set_weather(weather), {'requested': journal.fields(weather, journal.WEATHER)} if durability.enabled() else None)
            library = world.get_blueprint_library()
            blueprint = library.find("vehicle.tesla.model3")
            for role, source_transform in (("axiom_ego", ego_transform), ("axiom_lead", lead_transform)):
                blueprint.set_attribute("role_name", role)
                transform = _copy_transform(carla, source_transform, z_offset=0.3)
                startup_context['spawn_transforms'][role] = {'source': _transform_evidence(source_transform),
                    'requested': _transform_evidence(transform)}
                actor = journal.spawn(world, blueprint, transform, role, optional=True, owned=actors)
                if actor is None:
                    raise AdapterInputError(f"Unable to spawn {role}; no existing actor was destroyed")
            # Same-blueprint spawns can reset earlier tire configuration. Apply
            # physics after all vehicle creation, then verify after stabilization.
            for actor in actors:
                physics = actor.get_physics_control()
                wheels = physics.wheels
                for wheel in wheels:
                    wheel.tire_friction = p.friction
                physics.wheels = wheels
                journal.mutation('actor.apply_physics_control', lambda: actor.apply_physics_control(physics), {'actor_id': actor.id, 'requested_tire_friction': p.friction})
            ego, lead = actors
            collision = journal.spawn(world, library.find('sensor.other.collision'), carla.Transform(), 'axiom_collision', parent=ego, owned=sensors)
            collision.listen(record_collision)
            # Preserve every preparation snapshot; motion is imposed only after
            # both bodies satisfy the bounded, versioned rest-state policy.
            def startup_progress(report):
                durable.checkpoint([*payloads, _startup_payload(journal.interrupted_startup(report), startup_context)], frames, events, 'startup_sample')
            try:
                startup = wait_for_stability(journal.StartupWorld(world) if durability.enabled() else world, {'ego': ego, 'lead': lead}, dt=self.dt, on_sample=journal.startup_sample if durability.enabled() else None, on_progress=startup_progress if durability.enabled() else None)
            except StartupFailure as error:
                payloads.append(_startup_payload(error.report, startup_context))
                raise AdapterExecutionError('CARLA startup stability gate failed: ' + str(error), artifact_payloads=payloads) from error
            # Retain the completed rest trace before any subsequent RPC can fail.
            startup_payload_index = len(payloads)
            payloads.append(_startup_payload(startup, startup_context))
            durable.checkpoint(payloads, frames, events, 'startup_complete')
            rest_snapshot = world.get_snapshot()
            journal.snapshot(rest_snapshot, {'ego': ego, 'lead': lead}, 'after_rest_before_optional_rpc')
            startup_context['physics_configuration'] = read_physics_configuration(rest_snapshot, world,
                {'ego': ego, 'lead': lead}, p.friction)
            startup_artifact = _startup_payload(startup, startup_context)
            payloads[startup_payload_index] = startup_artifact
            durable.checkpoint(payloads, frames, events, 'physics_readback')
            if (rest_snapshot.frame != startup['trace'][-1]['world_frame']
                    or rest_snapshot.timestamp.elapsed_seconds != startup['trace'][-1]['elapsed_seconds']):
                raise AdapterExecutionError('World advanced after the startup boundary; initial velocity was not requested', artifact_payloads=payloads)
            require_physics_configuration(startup_context['physics_configuration'])
            startup_summary = {'name': startup_artifact.name, 'kind': startup_artifact.kind,
                'sha256': hashlib.sha256(startup_artifact.content).hexdigest(), 'size_bytes': len(startup_artifact.content),
                'policy_version': startup['policy']['version'], 'status': startup['status'],
                'completed_ticks': startup['completed_ticks'], 'stable_streak': startup['stable_streak'],
                'elapsed_simulated_seconds': startup['elapsed_simulated_seconds']}
            for actor, speed in ((ego, p.ego_speed_mps), (lead, p.target_speed_mps)):
                forward = actor.get_transform().get_forward_vector()
                velocity = carla.Vector3D(forward.x * speed, forward.y * speed, forward.z * speed)
                journal.mutation('actor.set_target_velocity', lambda: actor.set_target_velocity(velocity), {'actor_id': actor.id, 'requested_velocity_mps': journal.vector(velocity)} if durability.enabled() else None)
            camera_queue, lidar_queue = queue.Queue(), queue.Queue()
            if capture:
                camera_bp = library.find("sensor.camera.rgb")
                for key, value in {"image_size_x": "480", "image_size_y": "270", "fov": "90", "sensor_tick": "0.0"}.items():
                    camera_bp.set_attribute(key, value)
                camera = journal.spawn(world, camera_bp, carla.Transform(carla.Location(x=1.5, z=1.7)), 'axiom_camera', parent=ego, owned=sensors)
                camera.listen(camera_queue.put)
                lidar_bp = library.find("sensor.lidar.ray_cast")
                for key, value in {"channels": "16", "range": "80", "points_per_second": "32000",
                                   "rotation_frequency": "20", "sensor_tick": "0.0", "noise_seed": str(scenario.seed)}.items():
                    if lidar_bp.has_attribute(key):
                        lidar_bp.set_attribute(key, value)
                lidar = journal.spawn(world, lidar_bp, carla.Transform(carla.Location(z=2.2)), 'axiom_lidar', parent=ego, owned=sensors)
                lidar.listen(lidar_queue.put)
            steps = round(scenario.duration_s / self.dt)
            capture_steps = {0, steps // 2, steps}
            captured_frames = []
            from .version import ADAPTER_VERSION
            manifest = {"schema_version": "2", "worker_adapter_version": ADAPTER_VERSION,
                        "server_version": version, "map_name": road_map.name,
                        "scenario_id": scenario.id, "scenario_revision": scenario.revision, "seed": scenario.seed,
                        "startup": startup_summary,
                        "fixed_delta_seconds": self.dt, "coordinate_transform": "CARLA x,y,z -> app x,-y,z; yaw sign inverted",
                        "controller": "axiom ground-truth proportional lane following v0.1",
                        "tire_friction": p.friction, "weather": str(weather), "captures": [],
                        "sensor_scope": "RGB postprocess output and ray-cast LiDAR; no RAW/ISP or perception inference" if capture
                                        else "RGB/LiDAR capture disabled; collision sensor only",
                        "limitations": ["collision_count reports observed callbacks within the measured interval, including callbacks received during teardown",
                                        "CARLA sensor.stop() unsubscribes; it does not certify completion of in-flight collision callbacks",
                                        "No observed contacts does not establish collision-stream completeness; real-server validation remains required"]}
            if capture:
                manifest["sensor_rig_declaration"] = {
                    "reference_frame": "ego", "coordinate_convention": "right_handed_z_up",
                    "status": "adapter-declared static mount; no measured calibration",
                    "sensors": [
                        {"key": "camera_front", "kind": "camera", "frame_id": "camera_front_optical",
                         "transform": {"translation_m": [1.5, 0, 1.7], "rotation_xyzw": [-0.5, 0.5, -0.5, 0.5]},
                         "intrinsics": {"width": 480, "height": 270, "fx": 240, "fy": 240, "cx": 240, "cy": 135,
                                        "distortion_model": "unknown", "distortion_coefficients": []},
                         "note": "Nominal pinhole intrinsics from requested 90 degree horizontal FOV; lens/postprocess defaults are not a validated calibration."},
                        {"key": "lidar_roof", "kind": "lidar", "frame_id": "lidar_roof",
                         "transform": {"translation_m": [0, 0, 2.2], "rotation_xyzw": [0, 0, 0, 1]},
                         "note": "MCAP point coordinates negate source y. Original JSON artifact preserves CARLA local left-handed coordinates."},
                    ],
                }
            durable.manifest, durable.captured = manifest, captured_frames
            hazard_since = None
            brake_logged = False
            start_timestamp = None
            for index in range(steps + 1):
                t = round(index * self.dt, 6)
                if index:
                    ego_state, lead_state = _state(ego), _state(lead)
                    gap = math.hypot(lead_state.x - ego_state.x, lead_state.y - ego_state.y) - 5
                    hazard = gap < 4 + ego_state.speed_mps * 1.5
                    hazard_since = (hazard_since if hazard_since is not None else t) if hazard else None
                    brake = hazard_since is not None and t - hazard_since >= p.response_delay_s
                    durable.checkpoint(payloads, frames, events, 'before_legacy_control')
                    durable.control(index-1, 'ego', ego.id, p.ego_speed_mps, brake, lambda: self._control(carla, road_map, ego, p.ego_speed_mps, brake))
                    triggered = scenario.category == "emergency_brake" and t >= p.trigger_time_s
                    durable.control(index-1, 'lead', lead.id, p.target_speed_mps, triggered, lambda: self._control(carla, road_map, lead, p.target_speed_mps, triggered))
                    durable.checkpoint(payloads, frames, events, 'legacy_control_returned')
                    if triggered and not brake_logged:
                        events.append(Event(time_s=t, type="trigger", message="Lead emergency braking commanded"))
                        brake_logged = True
                frame_id = journal.tick(world, phase='legacy_measured')
                snapshot = world.get_snapshot()
                journal.snapshot(snapshot, {'ego': ego, 'lead': lead}, 'legacy_measured_before_actor_getters')
                if durability.enabled():
                    durable.observations.append({'sequence': index, 'world_frame': snapshot.frame, 'tick_returned_frame': frame_id, 'source_time_s': snapshot.timestamp.elapsed_seconds})
                    durable.checkpoint(payloads, frames, events, 'legacy_physics_before_actor_getters')
                if start_timestamp is None:
                    start_timestamp = snapshot.timestamp.elapsed_seconds
                ego_state, lead_state = _state(ego), _state(lead)
                dx, dy = lead_state.x - ego_state.x, lead_state.y - ego_state.y
                longitudinal = dx * math.cos(ego_state.yaw) + dy * math.sin(ego_state.yaw)
                lateral = abs(-dx * math.sin(ego_state.yaw) + dy * math.cos(ego_state.yaw))
                gap = max(0, longitudinal - (ego.bounding_box.extent.x + lead.bounding_box.extent.x))
                closing = ego_state.speed_mps - lead_state.speed_mps
                ttc = gap / closing if longitudinal > 0 and lateral < 2.2 and closing > 0.05 else None
                acceleration = (ego_state.speed_mps - frames[-1].ego.speed_mps) / self.dt if frames else 0
                frames.append(Frame(time_s=t, ego=ego_state,
                                    actors=[Actor(id="lead", type="vehicle", **lead_state.model_dump())],
                                    ttc_s=ttc, acceleration_mps2=acceleration))
                durable.checkpoint(payloads, frames, events, 'legacy_trajectory_retained')
                if capture:
                    image = _sensor_frame(camera_queue, frame_id)
                    if index in capture_steps:
                        journal.raw_source('legacy_rgb_raw', bytes(image.raw_data), {'world_frame': image.frame, 'timestamp_s': image.timestamp, 'width': image.width, 'height': image.height, 'encoding': 'bgra_uint8'})
                    cloud = _sensor_frame(lidar_queue, frame_id)
                    if index in capture_steps:
                        image_name, lidar_name = f"rgb-{index:04d}.png", f"lidar-{index:04d}.json"
                        journal.raw_source('legacy_lidar_raw', bytes(cloud.raw_data), {'world_frame': cloud.frame, 'timestamp_s': cloud.timestamp, 'encoding': 'xyzi_float32_le_carla'})
                        png = bgra_png(image.width, image.height, bytes(image.raw_data))
                        payloads.append(ArtifactPayload(name=image_name, kind="camera_rgb", media_type="image/png", content=png))
                        points = [list(point) for point in struct.iter_unpack("<ffff", bytes(cloud.raw_data))]
                        cloud_data = {"frame": cloud.frame, "timestamp": cloud.timestamp,
                                      "coordinate_frame": "sensor local CARLA left-handed; x,y,z,intensity",
                                      "points": points}
                        payloads.append(ArtifactPayload(name=lidar_name, kind="lidar", media_type="application/json",
                                                        content=json.dumps(cloud_data, allow_nan=False).encode()))
                        manifest["captures"].append({"simulation_time_s": t, "world_frame": frame_id,
                                                    "camera_timestamp": image.timestamp, "lidar_timestamp": cloud.timestamp,
                                                    "camera": image_name, "lidar": lidar_name})
                        captured_frames.append(CapturedFrame(frame_id, image.timestamp, cloud.timestamp, png, bytes(cloud.raw_data)))
                        durable.checkpoint(payloads, frames, events, 'legacy_selected_capture_retained')
        except AdapterExecutionError as error:
            if durability.enabled():
                raise AdapterExecutionError(str(error), artifact_payloads=durable.artifacts(payloads, frames, events, 'execution_failed')) from error
            raise
        except Exception as exc:
            raise AdapterExecutionError("CARLA trial failed after world execution was attempted: " + type(exc).__name__ + ': ' + str(exc)[:500], artifact_payloads=durable.artifacts(payloads, frames, events, 'execution_failed') if durability.enabled() else payloads) from exc
        finally:
            preceding_error = sys.exc_info()[1]
            for sensor in reversed(sensors):
                try:
                    journal.cleanup('sensor.stop', sensor.stop, {'actor_id': getattr(sensor, 'id', None)})
                except Exception as exc:
                    cleanup_errors.append(str(exc))
                try:
                    if not journal.cleanup('sensor.destroy', sensor.destroy, {'actor_id': getattr(sensor, 'id', None)}, acknowledgement=True):
                        cleanup_errors.append("Sensor destruction was not acknowledged")
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            for actor in reversed(actors):
                try:
                    if not journal.cleanup('actor.destroy', actor.destroy, {'actor_id': actor.id}, acknowledgement=True):
                        cleanup_errors.append("Vehicle destruction was not acknowledged")
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            try:
                if world_mutation_started:
                    journal.cleanup('world.set_weather', lambda: world.set_weather(original_weather))
            except Exception as exc:
                cleanup_errors.append(str(exc))
            try:
                if world_mutation_started:
                    journal.cleanup('world.apply_settings', lambda: world.apply_settings(original_settings))
            except Exception as exc:
                cleanup_errors.append(str(exc))
            try:
                if world_mutation_started:
                    durable.collisions.flush()
                    durability.event('cleanup_finished', {'errors': cleanup_errors, 'world_verified': False})
            except Exception as error:
                cleanup_errors.append(type(error).__name__ + ': ' + str(error)[:500])
            if cleanup_errors:
                payloads.append(ArtifactPayload(name='carla-cleanup.json', kind='execution_diagnostic', media_type='application/json',
                    content=json.dumps({'schema_version': '1', 'status': 'failed',
                        'scope': 'Acknowledged cleanup errors; no claim of an empty or restored server',
                        'preceding_error': {'type': type(preceding_error).__name__, 'message': str(preceding_error)[:1000]} if preceding_error else None,
                        'errors': [message[:1000] for message in cleanup_errors[:20]]}, allow_nan=False, sort_keys=True).encode()))
                raise AdapterExecutionError("CARLA cleanup/settings restore failed; inspect the dedicated server before another run", artifact_payloads=durable.artifacts(payloads, frames, events, "cleanup_failed") if durability.enabled() else payloads)
        try:
            # Freeze observed evidence only after teardown, so callbacks arriving
            # during stop/destroy are included. Stop is an unsubscribe, not a public
            # stream-completion barrier; this limitation remains in the evidence.
            with collision_lock:
                observed_collisions = sorted(collision_events, key=lambda event: event[1])
            collision_contacts: dict[int, float] = {}
            for actor_id, timestamp in observed_collisions:
                if start_timestamp <= timestamp <= snapshot.timestamp.elapsed_seconds:
                    collision_contacts.setdefault(actor_id, timestamp)
            for actor_id, timestamp in collision_contacts.items():
                events.append(Event(time_s=max(0, timestamp - start_timestamp), type="collision",
                                    message=f"Observed CARLA collision sensor contact with actor {actor_id}"))
            events.append(Event(time_s=0, type="scope", message="Ground-truth controller; no perception evaluation. Collision count includes observed callbacks; collision-stream completeness is unverified."))
            if captured_frames:
                payloads.append(ArtifactPayload(name="carla-sensors.mcap", kind="sensor_recording", media_type="application/mcap",
                                                content=capture_recording(captured_frames, metadata=manifest)))
            payloads.append(ArtifactPayload(name="carla-capture-manifest.json", kind="sensor_manifest",
                                            media_type="application/json", content=json.dumps(manifest, allow_nan=False).encode()))
            finite_ttc = [f.ttc_s for f in frames if f.ttc_s is not None]
            distance = sum(math.hypot(b.ego.x - a.ego.x, b.ego.y - a.ego.y) for a, b in zip(frames, frames[1:]))
            values = [("collision_count", len(collision_contacts), "observed contacts", 0, "eq"),
                      ("min_ttc_s", min(finite_ttc) if finite_ttc else None, "s", 1.5, "gte"),
                      ("max_deceleration_mps2", max(0, -min(f.acceleration_mps2 for f in frames)), "m/s²", 6, "lte"),
                      ("max_jerk_mps3", max(abs(b.acceleration_mps2 - a.acceleration_mps2) / self.dt for a, b in zip(frames, frames[1:])), "m/s³", 10, "lte"),
                      ("distance_m", distance, "m", 0, "gte")]
            metrics = [Metric(name=n, value=v, unit=u, threshold=threshold, operator=op,
                              passed=(v is None and n == "min_ttc_s") or (v is not None and (
                                  v >= threshold if op == "gte" else v <= threshold if op == "lte" else v == threshold)))
                       for n, v, u, threshold, op in values]
            return SimulationResult(trajectory=frames, events=sorted(events, key=lambda e: e.time_s), metrics=metrics,
                                    model_fidelity=f"CARLA {version}; experimental ground-truth controller; " + (
                                        "uncalibrated RGB/LiDAR capture" if capture else "RGB/LiDAR capture disabled") +
                                        "; observed collision callbacks, stream completeness unverified",
                                    artifact_payloads=payloads)
        except Exception as exc:
            raise AdapterExecutionError("CARLA result assembly failed after world execution and cleanup: " + type(exc).__name__ + ": " + str(exc)[:500], artifact_payloads=durable.artifacts(payloads, frames, events, "result_assembly_failed") if durability.enabled() else payloads) from exc
