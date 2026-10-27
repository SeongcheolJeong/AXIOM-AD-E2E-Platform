"""Native sensor/actuator boundary for the shared stack, with bounded evidence.

The only kernel inputs are a coherent ego snapshot, map route heading and the
matching ray-cast LiDAR callback. Lead scripting and GT metrics are observers.
No engine imports occur at module import time.
"""
from __future__ import annotations

import hashlib
import copy
import math
import os
import queue
import struct
import threading

from ade2e.adapters import AdapterExecutionError, AdapterInputError
from ade2e.schemas import Actor, ArtifactPayload, EgoState, Event, Frame, Metric, SimulationResult
from ade2e.simulation_context import (SimulationExecutionRequest, execution_correlation,
    simulation_input_hash, validate_simulation_context, validate_simulation_adapter_context,
    validate_simulation_execution_request)
from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.stack_schemas import StackEgo, StackLidar, StackPacket

from .recording import (_json, live_source_messages, live_stack_recording,
    simulation_timestamp_ns, validate_live_source_prefix)
from .startup import StartupFailure, wait_for_stability
from .version import ADAPTER_VERSION
from .vehicle_response import VehicleResponseEvidence
from . import durability, native_journal as journal
from .configured_sensor import configured, native_mount, SensorConfigurationEvidence

TOTAL_BUDGET = 32 * 1024 * 1024
ARTIFACT_BUDGET = 20 * 1024 * 1024
# Covers a full maximum-size next observation (raw + XYZ + base64 message),
# one next-frame physics/trace row, final failure/cleanup and envelope metadata.
NEXT_OBSERVATION_RESERVE = 2 * 1024 * 1024
SOURCE_BUDGET = 18 * 1024 * 1024
FIDELITY = "CARLA physics + shared LiDAR geometric stack + ego/map oracle + uncalibrated ray-cast sensor; no RGB capture; observed collision callbacks, stream completeness unverified"


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_context(scenario, context):
    try:
        if os.getenv("ADE2E_CARLA_CAPTURE", "1") != "1":
            raise ValueError("Live stack requires LiDAR capture; ADE2E_CARLA_CAPTURE must be 1")
        validate_simulation_adapter_context(context, ADAPTER_VERSION)
        return validate_simulation_context(scenario, context)
    except Exception as error:
        raise AdapterInputError("Unsupported live stack context: " + str(error)[:800]) from error


def _configured_lidar(library, profile, carla, readback=None):
    blueprint = library.find(profile.blueprint_id)
    for name, expected in profile.attributes.items():
        if not blueprint.has_attribute(name):
            raise AdapterInputError(f"Required LiDAR blueprint attribute is unavailable: {name}")
        blueprint.set_attribute(name, expected)
        attribute = blueprint.get_attribute(name)
        integer = name in {"channels", "points_per_second", "noise_seed"}
        wanted_type = carla.ActorAttributeType.Int if integer else carla.ActorAttributeType.Float
        if attribute.type != wanted_type:
            raise AdapterInputError(f"LiDAR blueprint attribute type changed: {name}")
        actual = attribute.as_int() if integer else attribute.as_float()
        # CARLA stores float attributes as float32; compare that representation,
        # never ActorAttribute.__str__, which is a descriptive debug string.
        target = int(expected) if integer else struct.unpack("<f", struct.pack("<f", float(expected)))[0]
        equal = math.isfinite(actual) and actual == target
        if not equal:
            raise AdapterInputError(f"LiDAR blueprint did not retain pinned {name}: requested {expected}, read {actual}")
        if readback is not None:
            readback[name] = {"type": "int" if integer else "float32", "value": actual}
    return blueprint


def connect_preflight(runner, scenario, context):
    """Only read the engine or configure detached blueprint objects; no world writes."""
    context = validate_context(scenario, context)
    carla, client, world, version = runner._connect()
    if not callable(getattr(client, "apply_batch_sync", None)) or not callable(getattr(getattr(carla, "command", None), "ApplyVehicleControl", None)):
        raise AdapterInputError("Native client lacks acknowledged ApplyVehicleControl batch support")
    road_map = world.get_map()
    if scenario.map_name != road_map.name.split("/")[-1]:
        raise AdapterInputError("Scenario map must match the loaded map; live stack never reloads maps")
    existing = world.get_actors()
    if any(list(existing.filter(pattern)) for pattern in ("vehicle.*", "walker.*", "sensor.*")):
        raise AdapterInputError("Live stack needs an empty dedicated world and sole tick ownership")
    if world.get_settings().synchronous_mode:
        raise AdapterInputError("World already has synchronous tick ownership")
    _configured_lidar(world.get_blueprint_library(), context.sensor_profile, carla)
    if configured(context):
        native_mount(carla, context.sensor_profile.mount)
    transforms = runner._straight_spawn(road_map, scenario)
    return carla, client, world, version, road_map, transforms, context


def _rh_rotation(transform):
    """Reflect the native rotation matrix, then extract standard RH Euler angles."""
    matrix = transform.get_matrix()
    signs = (1, -1, 1)
    rotation = [[float(matrix[i][j]) * signs[i] * signs[j] for j in range(3)] for i in range(3)]
    if any(not math.isfinite(value) for row in rotation for value in row):
        raise ValueError("Ego transform matrix contains nonfinite values")
    yaw = math.atan2(rotation[1][0], rotation[0][0])
    pitch = math.atan2(-rotation[2][0], math.hypot(rotation[0][0], rotation[1][0]))
    roll = math.atan2(rotation[2][1], rotation[2][2])
    return yaw, pitch, roll


def _ego_source(snapshot, ego_id, road_map):
    """No surrounding actor can cross this source boundary."""
    actor = snapshot.find(ego_id)
    if actor is None:
        raise ValueError("Coherent snapshot is missing the ego actor")
    pose, velocity = actor.get_transform(), actor.get_velocity()
    yaw, pitch, roll = _rh_rotation(pose)
    speed = math.sqrt(sum(float(getattr(velocity, key)) ** 2 for key in ("x", "y", "z")))
    waypoint = road_map.get_waypoint(pose.location)
    if waypoint is None:
        raise ValueError("Map oracle has no ego route waypoint")
    options = waypoint.next(max(4.0, speed * .6))
    if len(options) != 1:
        raise ValueError("Map oracle route is absent or ambiguous")
    aim = options[0].transform.location
    dx, dy = float(aim.x-pose.location.x), float(-aim.y+pose.location.y)
    if not math.isfinite(dx) or not math.isfinite(dy) or math.hypot(dx, dy) < 1e-6:
        raise ValueError("Map oracle route heading is unavailable")
    error = (math.atan2(dy, dx)-yaw+math.pi) % (2*math.pi)-math.pi
    at = str(simulation_timestamp_ns(snapshot.timestamp.elapsed_seconds))
    source = {"timestamp_ns": at, "frame_id": "ego", "speed_mps": speed,
        "route_heading_error_rad": error, "pitch_rad": pitch, "roll_rad": roll,
        "source": "carla-ego-state-and-map-route/1"}
    telemetry = StackEgo(**source, capture_time_ns=at)
    return source, telemetry


def _consumed_xyz(raw, mount):
    if len(raw) % 16 or len(raw) > 20_000 * 16:
        raise ValueError("Full valid LiDAR frame is required; size/layout is unsupported")
    output = bytearray()
    qx, qy, qz, qw = mount.rotation_xyzw
    dx, dy, dz = mount.translation_m
    for x, native_y, z, intensity in struct.iter_unpack("<ffff", raw):
        if any(not math.isfinite(value) for value in (x, native_y, z, intensity)):
            raise ValueError("LiDAR contains nonfinite values; none were dropped")
        y = -native_y
        tx, ty, tz = 2*(qy*z-qz*y), 2*(qz*x-qx*z), 2*(qx*y-qy*x)
        point = (x+qw*tx+qy*tz-qz*ty+dx, y+qw*ty+qz*tx-qx*tz+dy, z+qw*tz+qx*ty-qy*tx+dz)
        if any(not math.isfinite(value) or abs(value) > 1_000_000 for value in point):
            raise ValueError("Transformed LiDAR exceeds bounded finite kernel geometry")
        output.extend(struct.pack("<fff", *point))
    return bytes(output)


def carla_control(control):
    return {"throttle": control.throttle, "brake": control.brake, "steer": -control.steer,
            "reverse": False, "hand_brake": False}


def _control_readback(actor):
    control = actor.get_control()
    result = {name: float(getattr(control, name)) for name in ("throttle", "brake", "steer")}
    if (any(not math.isfinite(value) for value in result.values()) or
        not 0 <= result["throttle"] <= 1 or not 0 <= result["brake"] <= 1 or not -1 <= result["steer"] <= 1):
        raise ValueError("Actuator readback is outside its finite control contract")
    result.update({name: bool(getattr(control, name)) for name in ("reverse", "hand_brake")})
    return result


def _advance(world, previous, dt):
    frame = journal.tick(world, phase='measured')
    snapshot = world.get_snapshot()
    at, before = float(snapshot.timestamp.elapsed_seconds), float(previous.timestamp.elapsed_seconds)
    if frame != snapshot.frame or frame != previous.frame+1:
        raise ValueError("Physics frame gap: tick return/snapshot/previous frame are not consecutive")
    delta = float(snapshot.timestamp.delta_seconds)
    if (not math.isfinite(at) or not math.isfinite(before) or not math.isfinite(delta) or
        abs(at-before-dt) > 1e-6 or abs(delta-dt) > 1e-6):
        raise ValueError("Physics clock/delta differs from the pinned 20 Hz interval")
    return snapshot


def _observer(snapshot, ego, lead, previous, relative_time, dt):
    """GT is limited to physics visualization/TTC and never returned to StackSession."""
    def state(actor):
        observed = snapshot.find(actor.id)
        if observed is None:
            raise ValueError("Physics snapshot is missing an observer actor")
        pose, velocity = observed.get_transform(), observed.get_velocity()
        yaw, _, _ = _rh_rotation(pose)
        return EgoState(x=pose.location.x, y=-pose.location.y, yaw=yaw,
            speed_mps=math.sqrt(sum(float(getattr(velocity, key))**2 for key in ("x", "y", "z"))))
    ego_state, lead_state = state(ego), state(lead)
    dx, dy = lead_state.x-ego_state.x, lead_state.y-ego_state.y
    longitudinal = dx*math.cos(ego_state.yaw)+dy*math.sin(ego_state.yaw)
    lateral = abs(-dx*math.sin(ego_state.yaw)+dy*math.cos(ego_state.yaw))
    gap = max(0, longitudinal-ego.bounding_box.extent.x-lead.bounding_box.extent.x)
    closing = ego_state.speed_mps-lead_state.speed_mps
    ttc = gap/closing if longitudinal > 0 and lateral < 2.2 and closing > .05 else None
    acceleration = (ego_state.speed_mps-previous.ego.speed_mps)/dt if previous else 0
    return Frame(time_s=relative_time, ego=ego_state,
        actors=[Actor(id="lead", type="vehicle", **lead_state.model_dump())], ttc_s=ttc, acceleration_mps2=acceleration)


def _tick(index):
    return {"schema_version": "carla-live-stack-tick/1", "sequence": index, "status": "not_attempted",
        "source_world_frame": None, "source_time_s": None, "packet": None, "output": None,
        "source_refs": {"lidar": None, "ego": None}, "points": None,
        "requested_carla_control": None, "control_acknowledged": False, "control_rpc_response": None,
        "next_world_frame": None, "next_time_s": None, "observed_control": None, "next_ego": None,
        "application_status": "not_sent", "error": None}


class LiveEvidence:
    """All payloads remain bounded before command dispatch; prefix writes are atomic."""
    def __init__(self, request, version, map_name):
        self.request = request
        self.identity = execution_correlation(request)
        self.base = []
        self.messages, self.observations, self.physics = [], [], []
        self.raw, self.xyz = bytearray(), bytearray()
        self.ticks = [_tick(index) for index in range(round(request.scenario.duration_s/.05))]
        self.status, self.phase, self.error = "partial", "configuration", None
        self.version, self.map_name, self.cleanup = version, map_name, []
        self.actual_lidar_attributes = {}
        self.ego_actor_id = None
        self.sensor_configuration = SensorConfigurationEvidence(request) if configured(request.context) else None
        # One bounded entry shared by synchronous checkpoint projections. The
        # complete value key detects changed source data, metadata and status;
        # neither object identity nor message count authorizes reuse.
        self._source_recording_cache = {"entry": None}
        self.collision_journal = journal.CollisionJournal()
        self.vehicle_response = VehicleResponseEvidence(request, version, ADAPTER_VERSION)

    def _blob(self, name, kind, content, media_type="application/json"):
        return ArtifactPayload(name=name, kind=kind, media_type=media_type, content=content)

    def _source_recording(self, metadata):
        validate_live_source_prefix(self.messages)
        # Snapshot every value, including bytes, rather than retaining aliases
        # to a caller's message object. Type tags cannot make a malformed float
        # frame compare equal to an already validated integer frame.
        messages = tuple((type(item.channel_id), item.channel_id,
            type(item.world_frame), item.world_frame, type(item.timestamp_ns), item.timestamp_ns,
            type(item.content), bytes(item.content)) for item in self.messages)
        key = (ADAPTER_VERSION, self.version, messages, _json(metadata))
        entry = self._source_recording_cache["entry"]
        if entry is not None and entry[0] == key:
            return entry[1]
        options = {'source_frame_id': self.request.context.sensor_profile.sensor_frame} if self.sensor_configuration else {}
        content = live_stack_recording(self.messages, metadata=metadata, **options)
        # A caller can still receive the normal encoder/budget error without
        # retaining an oversized metadata key or payload in the cache itself.
        if len(key[-1]) <= ARTIFACT_BUDGET and len(content) <= ARTIFACT_BUDGET:
            self._source_recording_cache["entry"] = (key, content)
        return content

    def artifacts(self):
        trace = {"schema_version": "carla-live-stack-trace/1", **self.identity, "status": self.status,
            "expected_intervals": len(self.ticks), "ticks": self.ticks,
            "scope": "Native closed-loop commands; next-frame observation is not proof of actuator performance."}
        metadata = {**self.identity, "status": self.status,
            "sensor_profile": self.request.context.sensor_profile.model_dump(mode="json"),
            "rig_snapshot": self.request.context.rig_snapshot.model_dump(mode="json"),
            "ego_source_policy": self.request.context.ego_source_policy,
            "observations": self.observations}
        payloads = [*self.base,
            self.vehicle_response.artifact(completed=self.status == "completed", error=self.error),
            self._blob("carla-stack-trace.json", "live_stack_trace", _json(trace)),
            self._blob("carla-stack-physics.json", "physics_trace", _json({"schema_version": "carla-live-stack-physics/1",
                **self.identity, "status": self.status, "observations": self.physics,
                "scope": "Coherent simulator actor snapshots; surrounding GT is observer-only."})),
            self._blob("carla-stack-sources.mcap", "sensor_recording", self._source_recording(metadata), "application/mcap"),
            self._blob("carla-stack-raw-xyzi.bin", "live_stack_raw_points", bytes(self.raw), "application/octet-stream"),
            self._blob("carla-stack-points.bin", "live_stack_points", bytes(self.xyz), "application/octet-stream")]
        if self.sensor_configuration:
            payloads.append(self._blob('carla-sensor-configuration.json', 'sensor_configuration',
                _json(self.sensor_configuration.payload(completed=self.status == 'completed', phase=self.phase, error=self.error))))
        manifest = {"schema_version": "carla-live-stack-manifest/1", **self.identity, "status": self.status,
            "worker_adapter_version": ADAPTER_VERSION, "server_version": self.version, "map_name": self.map_name,
            "ego_actor_id": self.ego_actor_id,
            "execution_context": self.request.context.model_dump(mode="json"),
            "scenario_id": self.request.scenario.id, "scenario_revision": self.request.scenario.revision,
            "seed": self.request.scenario.seed, "fixed_delta_seconds": .05,
            "actual_lidar_attributes": self.actual_lidar_attributes,
            "control_transport": "client.apply_batch_sync([ApplyVehicleControl], False); one successful response before a separate world.tick",
            "expected_intervals": len(self.ticks), "expected_observations": len(self.ticks)+1,
            "observed_frames": len(self.physics), "retained_source_observations": len(self.observations),
            "acknowledged_commands": sum(t["control_acknowledged"] for t in self.ticks),
            "next_frame_observed_commands": sum(t["application_status"] == "next_frame_observed" for t in self.ticks),
            "failure_phase": self.phase if self.status == "partial" else None, "error": self.error,
            "last_observed_world_frame": self.physics[-1]["world_frame"] if self.physics else None,
            "cleanup_errors": self.cleanup, "sensor_scope": "LiDAR control input and ego/map oracle; no RGB capture in this mode",
            "model_fidelity": FIDELITY,
            "limitations": ["Startup stability is measured before initial target velocity; post-target vertical/pose convergence is not certified.",
                "Map heading and ego telemetry are simulator oracles, not perception/localization estimates.",
                "Rig/ground declarations and ray-cast returns are not measured calibration or safety evidence.",
                "Collision callbacks are observations; unsubscribe does not certify stream completeness.",
                "Sensor double seconds are rounded half-even to ns without additional precision.",
                "Actuator readback is read after the associated snapshot, under sole tick ownership."],
            "artifacts": [{"name": p.name, "kind": p.kind, "sha256": _hash(p.content), "size_bytes": len(p.content)} for p in payloads]}
        payloads.append(self._blob("carla-stack-manifest.json", "live_stack_manifest", _json(manifest)))
        if len(payloads) > 100 or sum(len(p.content) for p in payloads) > TOTAL_BUDGET:
            raise ValueError("Live evidence exceeds its total artifact budget")
        return payloads

    def checkpoint(self, phase):
        if not durability.enabled():
            return
        # A checkpoint is an immutable interrupted projection, never the running
        # object or a success claim. Only top-level running-row fields change.
        # Serialization and the pure response validators finish synchronously
        # on the owner thread before any future observation can mutate history.
        # Returned ArtifactPayload content is immutable bytes, never an alias.
        self.collision_journal.flush()
        frozen = copy.copy(self)
        frozen.ticks = [dict(tick) if tick['status'] == 'running' else tick for tick in self.ticks]
        frozen.status, frozen.phase = 'partial', phase
        frozen.error = 'Interrupted at durable checkpoint: ' + phase
        frozen.vehicle_response = copy.copy(self.vehicle_response)
        for tick in frozen.ticks:
            if tick['status'] == 'running':
                tick.update(status='failed', error=frozen.error)
        durability.checkpoint(frozen.artifacts(), phase)

    def reserve_before_dispatch(self):
        payloads = self.artifacts()
        if (sum(len(p.content) for p in payloads)+NEXT_OBSERVATION_RESERVE > TOTAL_BUDGET or
            any(len(p.content)+NEXT_OBSERVATION_RESERVE > ARTIFACT_BUDGET for p in payloads)):
            raise ValueError("Insufficient evidence budget for the next command and observation")

    def retain(self, snapshot, raw, cloud_time, packet, points, messages):
        if sum(len(item.content) for item in [*self.messages, *messages]) > SOURCE_BUDGET:
            raise ValueError("Full source preservation budget exhausted before command dispatch")
        previous_recording_cache = self._source_recording_cache["entry"]
        raw_ref = {"artifact_name": "carla-stack-raw-xyzi.bin", "offset_bytes": len(self.raw),
            "size_bytes": len(raw), "sha256": _hash(raw), "encoding": "xyzi_float32_le_carla"}
        refs = {"lidar": messages[0].reference(len(self.messages)), "ego": messages[1].reference(len(self.messages)+1)}
        refs["lidar"].update(raw_carla_xyzi_sha256=_hash(raw), raw_points=raw_ref)
        if self.sensor_configuration:
            refs['lidar']['frame_id'] = self.request.context.sensor_profile.sensor_frame
        point_ref = None if points is None else {"artifact_name": "carla-stack-points.bin", "offset_bytes": len(self.xyz),
            "size_bytes": len(points), "sha256": _hash(points), "point_count": len(points)//12, "frame_id": "ego"}
        observation = {"world_frame": snapshot.frame, "source_time_s": snapshot.timestamp.elapsed_seconds,
            "lidar_timestamp_s": cloud_time, "source_refs": refs, "points": point_ref,
            "packet": packet.model_dump(mode="json")}
        self.raw.extend(raw)
        if points is not None:
            self.xyz.extend(points)
        self.messages.extend(messages)
        self.observations.append(observation)
        try:
            self.artifacts()
        except Exception:
            # Keep the previously valid, downloadable prefix if the new source
            # itself cannot be represented within the fixed preservation bounds.
            del self.raw[raw_ref["offset_bytes"]:]
            if point_ref:
                del self.xyz[point_ref["offset_bytes"]:]
            del self.messages[-2:]
            self.observations.pop()
            self._source_recording_cache["entry"] = previous_recording_cache
            raise
        return refs, point_ref


def run_live_stack(runner, request, *, physics_profile=None):
    """N commands over N+1 measured observations, one persistent shared session."""
    from .runner import _copy_transform, _transform_evidence, _startup_payload, _sensor_frame

    try:
        request = validate_simulation_execution_request(request.model_dump(mode="json"))
        if request.input_hash != simulation_input_hash(request.scenario, "carla", ADAPTER_VERSION, request.context):
            raise ValueError("Execution input hash differs from the pinned request")
    except Exception as error:
        raise AdapterInputError("Invalid live execution request: " + str(error)[:700]) from error
    scenario = request.scenario
    candidate = None
    if physics_profile is not None:
        from .physics_candidate import CandidateEvidence
        candidate = CandidateEvidence(physics_profile, request, runner.dt)
    try:
        carla, client, world, version, road_map, transforms, context = connect_preflight(runner, scenario, request.context)
    except Exception as error:
        if configured(request.context):
            initial = SensorConfigurationEvidence(request)
            payload = ArtifactPayload(name='carla-sensor-configuration.json', kind='sensor_configuration',
                media_type='application/json', content=_json(initial.payload(completed=False, phase='native_preflight',
                    error=type(error).__name__+': '+str(error)[:1000])))
            raise AdapterExecutionError('Configured sensor preflight failed before world mutation', artifact_payloads=[payload]) from error
        raise
    registry = StackRegistry()
    validate_simulation_context(scenario, context, stack_registry=registry)
    session = StackSession(context.stack_snapshot.definition, registry)
    evidence = LiveEvidence(request, version, road_map.name)
    if candidate:
        evidence.base.append(candidate.artifact())

    def observe_candidate(snapshot, phase):
        if candidate:
            try:
                candidate.observe(world, snapshot, phase)
            finally:
                # Retain a failed readback too, before normal failure cleanup.
                evidence.base[0] = candidate.artifact()
    # The static denominator/context itself must fit before the first world write.
    evidence.reserve_before_dispatch()
    original_settings, original_weather = world.get_settings(), world.get_weather()
    actors, sensors, frames, events = [], [], [], []
    collisions, collision_lock = [], threading.Lock()
    failure = None
    world_mutation_started = False
    p = scenario.parameters
    startup_context = {"adapter_version": ADAPTER_VERSION, **evidence.identity,
        "scenario_id": scenario.id, "scenario_revision": scenario.revision, "seed": scenario.seed,
        "server_version": version, "map_name": road_map.name, "stage": "rest_before_initial_target_velocity",
        "requested_initial_speeds_mps": {"ego": p.ego_speed_mps, "lead": p.target_speed_mps},
        "spawn_z_offset_m": .3, "spawn_transforms": {}}

    def collision_callback(event):
        evidence.collision_journal.receive(event)
        with collision_lock:
            collisions.append((event.other_actor.id, event.timestamp))

    try:
        journal.begin(world, original_settings, original_weather, mode='live_stack', scenario=scenario, version=version)
        evidence.checkpoint('before_world_mutation')
        evidence.phase = "world_configuration"
        settings = world.get_settings()
        settings.synchronous_mode, settings.fixed_delta_seconds = True, runner.dt
        settings.substepping, settings.max_substep_delta_time, settings.max_substeps = True, .01, 10
        if candidate:
            candidate.configure(settings)
            evidence.base[0] = candidate.artifact()
        durability.event('world_mutation_intent', {'operation': 'world.apply_settings', 'world_id': str(world.id)} if durability.enabled() else {})
        world_mutation_started = True
        journal.mutation('world.apply_settings', lambda: world.apply_settings(settings), {'requested': journal.fields(settings, journal.SETTINGS)} if durability.enabled() else None)
        observe_candidate(world.get_snapshot(), 'before_actor_spawn') if candidate else None
        weather = carla.WeatherParameters(cloudiness=80 if scenario.weather != "clear" else 10,
            precipitation=70 if scenario.weather == "rain" else 0, wetness=80 if scenario.weather == "rain" else 0,
            fog_density=60 if scenario.weather == "fog" else 0,
            sun_altitude_angle={"day": 65, "dusk": 5, "night": -30}[scenario.time_of_day])
        journal.mutation('world.set_weather', lambda: world.set_weather(weather), {'requested': journal.fields(weather, journal.WEATHER)} if durability.enabled() else None)
        library = world.get_blueprint_library()
        blueprint = library.find("vehicle.tesla.model3")
        for role, source_transform in zip(("axiom_ego", "axiom_lead"), transforms):
            blueprint.set_attribute("role_name", role)
            pose = _copy_transform(carla, source_transform, z_offset=.3)
            startup_context["spawn_transforms"][role] = {"source": _transform_evidence(source_transform), "requested": _transform_evidence(pose)}
            actor = journal.spawn(world, blueprint, pose, role, optional=True, owned=actors)
            if actor is None:
                raise ValueError("Unable to spawn " + role)
        # Spawn the complete vehicle set before configuring physics: a later
        # same-blueprint spawn can reset an earlier vehicle's tire scale.
        # The post-startup readback gate verifies the actual values before motion.
        for actor in actors:
            physics = actor.get_physics_control()
            wheels = physics.wheels
            for wheel in wheels:
                wheel.tire_friction = p.friction
            physics.wheels = wheels
            journal.mutation('actor.apply_physics_control', lambda: actor.apply_physics_control(physics), {'actor_id': actor.id, 'requested_tire_friction': p.friction})
        ego, lead = actors
        evidence.ego_actor_id = ego.id
        if evidence.sensor_configuration:
            evidence.phase = 'sensor_configuration'
            lidar_bp = _configured_lidar(library, context.sensor_profile, carla, evidence.actual_lidar_attributes)
            evidence.sensor_configuration.detached_attributes = evidence.actual_lidar_attributes
            mount_pose, evidence.sensor_configuration.spawn = native_mount(carla, context.sensor_profile.mount)
            lidar = journal.spawn(world, lidar_bp, mount_pose, 'axiom_lidar', parent=ego, owned=sensors)
            evidence.sensor_configuration.read_actor_attributes(lidar)
            evidence.checkpoint('configured_sensor_spawned')
        collision = journal.spawn(world, library.find('sensor.other.collision'), carla.Transform(), 'axiom_collision', parent=ego, owned=sensors)
        collision.listen(evidence.sensor_configuration.gate.wrap(collision_callback) if evidence.sensor_configuration else collision_callback)
        evidence.phase = "startup_stability"
        def startup_progress(report):
            frozen = copy.copy(evidence)
            frozen.base = [*evidence.base, _startup_payload(journal.interrupted_startup(report), startup_context)]
            frozen.checkpoint('startup_sample')
        try:
            startup = wait_for_stability(journal.StartupWorld(world) if durability.enabled() else world, {'ego': ego, 'lead': lead}, dt=runner.dt, on_sample=journal.startup_sample if durability.enabled() else None, on_progress=startup_progress if durability.enabled() else None)
        except StartupFailure as error:
            evidence.base.append(_startup_payload(error.report, startup_context))
            raise
        evidence.base.append(_startup_payload(startup, startup_context))
        evidence.checkpoint('startup_complete')
        evidence.phase = "vehicle_physics_readback"
        rest_snapshot = world.get_snapshot()
        observe_candidate(rest_snapshot, 'after_rest_before_target_velocity')
        if evidence.sensor_configuration:
            evidence.phase = 'sensor_mount_readback'
            evidence.sensor_configuration.observe_mount(world, rest_snapshot, lidar, ego)
            evidence.checkpoint('sensor_mount_readback')
            evidence.phase = 'vehicle_physics_readback'
        journal.snapshot(rest_snapshot, {'ego': ego, 'lead': lead}, 'after_rest_before_optional_rpc')
        evidence.vehicle_response.capture_after_rest(rest_snapshot, world, {"ego": ego, "lead": lead})
        evidence.checkpoint('physics_readback')
        evidence.phase = "initial_target_velocity"
        for role, actor, speed in (("ego", ego, p.ego_speed_mps), ("lead", lead, p.target_speed_mps)):
            forward = rest_snapshot.find(actor.id).get_transform().get_forward_vector()
            velocity = carla.Vector3D(forward.x*speed, forward.y*speed, forward.z*speed)
            evidence.vehicle_response.record_initial_velocity_request(role, velocity)
            journal.mutation('actor.set_target_velocity', lambda: actor.set_target_velocity(velocity), {'role': role, 'actor_id': actor.id, 'requested_velocity_mps': journal.vector(velocity)} if durability.enabled() else None)
            evidence.checkpoint('initial_velocity_returned_' + role)
        lidar_queue = queue.Queue(maxsize=4)
        sensor_errors = []
        def receive_lidar(cloud):
            try:
                lidar_queue.put_nowait(cloud)
            except queue.Full:
                sensor_errors.append("LiDAR callback queue overflow; sole tick/frame ownership is violated")
        if not evidence.sensor_configuration:
            lidar_bp = _configured_lidar(library, context.sensor_profile, carla, evidence.actual_lidar_attributes)
            lidar = journal.spawn(world, lidar_bp, carla.Transform(carla.Location(z=2.2)), 'axiom_lidar', parent=ego, owned=sensors)
        lidar.listen(evidence.sensor_configuration.gate.wrap(receive_lidar) if evidence.sensor_configuration else receive_lidar)
        previous = world.get_snapshot()
        evidence.phase = "initial_observation"
        snapshot = _advance(world, previous, runner.dt)
        start_time = snapshot.timestamp.elapsed_seconds

        def retain_physics(snapshot, index):
            observe_candidate(snapshot, 'measurement_' + str(index))
            journal.snapshot(snapshot, {'ego': ego, 'lead': lead}, 'measured_before_optional_rpc')
            frame = _observer(snapshot, ego, lead, frames[-1] if frames else None, round(index*runner.dt, 6), runner.dt)
            frames.append(frame)
            evidence.physics.append({"world_frame": snapshot.frame, "source_time_s": snapshot.timestamp.elapsed_seconds,
                "frame": frame.model_dump(mode="json")})
            return frame

        def observe(snapshot, index):
            if sensor_errors:
                raise ValueError(sensor_errors[0])
            cloud = _sensor_frame(lidar_queue, snapshot.frame)
            if simulation_timestamp_ns(cloud.timestamp) != simulation_timestamp_ns(snapshot.timestamp.elapsed_seconds):
                raise ValueError("LiDAR callback clock does not match the coherent physics snapshot")
            if evidence.sensor_configuration:
                evidence.sensor_configuration.observe_mount(world, snapshot, lidar, ego, cloud)
            raw = bytes(cloud.raw_data)
            journal.raw_source('live_lidar_raw', raw, {'world_frame': snapshot.frame, 'sensor_timestamp_s': cloud.timestamp, 'encoding': 'xyzi_float32_le_carla'})
            source, telemetry = _ego_source(snapshot, ego.id, road_map)
            packet = StackPacket(sequence=index, at_ns=source["timestamp_ns"], ego=telemetry)
            try:
                options = {'source_frame_id':context.sensor_profile.sensor_frame} if evidence.sensor_configuration else {}
                messages = live_source_messages(snapshot.frame, cloud.timestamp, raw, source, **options)
            except ValueError:
                if len(raw) <= 20_000 * 16:
                    evidence.base.append(evidence._blob("carla-stack-invalid-source.bin", "execution_diagnostic", raw, "application/octet-stream"))
                raise
            points = None
            try:
                points = _consumed_xyz(raw, context.sensor_profile.mount)
                at = str(simulation_timestamp_ns(cloud.timestamp))
                packet.lidar = StackLidar(timestamp_ns=at, capture_time_ns=at, frame_id="ego",
                    point_count=len(points)//12, points_sha256=_hash(points), size_bytes=len(points),
                    source_message_sha256=_hash(messages[0].content), complete_frame=True)
            except ValueError as error:
                packet.unavailable_reasons.append("lidar_unavailable: " + str(error)[:400])
            refs, point_ref = evidence.retain(snapshot, raw, cloud.timestamp, packet, points, messages)
            evidence.checkpoint('source_retained')
            return packet, points, refs, point_ref

        retain_physics(snapshot, 0)
        evidence.checkpoint('initial_physics_before_optional_rpc')
        evidence.vehicle_response.capture_observation(snapshot, world, {"ego": ego, "lead": lead})
        packet, points, refs, point_ref = observe(snapshot, 0)
        for index, tick in enumerate(evidence.ticks):
            tick.update(status="running", source_world_frame=snapshot.frame, source_time_s=snapshot.timestamp.elapsed_seconds,
                packet=packet.model_dump(mode="json"), source_refs=refs, points=point_ref)
            evidence.phase = "shared_stack_step"
            output = session.step(packet, points)
            tick["output"] = output.model_dump(mode="json")
            requested = carla_control(output.control)
            tick["requested_carla_control"] = requested
            evidence.phase = "source_preservation_reservation"
            evidence.reserve_before_dispatch()
            evidence.checkpoint('before_lead_control')
            # Lead behavior alone may read GT through the legacy controller.
            evidence.phase = "lead_scenario_script"
            triggered = scenario.category == "emergency_brake" and index*runner.dt >= p.trigger_time_s
            journal.mutation('lead_scenario_control', lambda: runner._control(carla, road_map, lead, p.target_speed_mps, triggered), {'actor_id': lead.id, 'sequence': index, 'target_speed_mps': p.target_speed_mps, 'force_brake': triggered})
            if triggered and not any(event.type == "trigger" for event in events):
                events.append(Event(time_s=index*runner.dt, type="trigger", message="Lead emergency braking commanded by scenario script"))
            evidence.phase = "ego_control_send"
            tick["application_status"] = "send_outcome_unknown"
            evidence.checkpoint('before_ego_control_send')
            durability.event('control_intent', {'sequence': index, 'actor_id': ego.id, 'source_world_frame': snapshot.frame, 'requested': requested, 'transport': 'apply_batch_sync_without_tick'})
            responses = client.apply_batch_sync([carla.command.ApplyVehicleControl(ego.id, carla.VehicleControl(**requested))], False)
            if not isinstance(responses, (list, tuple)) or len(responses) != 1:
                raise ValueError("Actuator batch response did not contain exactly one command result")
            response = responses[0]
            rejected = response.has_error()
            if type(rejected) is not bool:
                raise ValueError("Actuator response error flag is invalid")
            if rejected:
                # A response error has no successful actor result; don't access
                # the RPC variant's actor_id branch when it contains an error.
                error_text = str(response.error)[:1000]
                tick["control_rpc_response"] = {"actor_id": None, "error": error_text}
                tick["application_status"] = "send_rejected"
                durability.event('control_rejected', {'sequence': index, 'response': tick['control_rpc_response']})
                evidence.checkpoint('ego_control_rejected')
                raise ValueError("Actuator command was rejected: " + error_text)
            actor_id = response.actor_id
            tick["control_rpc_response"] = {"actor_id": actor_id if type(actor_id) is int else None, "error": None}
            if type(actor_id) is not int or actor_id != ego.id:
                raise ValueError("Actuator batch response actor identity differs from ego")
            tick.update(control_acknowledged=True, application_status="acknowledged_without_next_observation")
            durability.event('control_acknowledged', {'sequence': index, 'response': tick['control_rpc_response']})
            evidence.checkpoint('ego_control_acknowledged')
            evidence.phase = "next_physics_observation"
            snapshot = _advance(world, snapshot, runner.dt)
            # Store the next physics snapshot before any sensor/decoder failure.
            next_frame = retain_physics(snapshot, index+1)
            tick.update(next_world_frame=snapshot.frame, next_time_s=snapshot.timestamp.elapsed_seconds,
                next_ego=next_frame.ego.model_dump(mode="json"))
            tick["application_status"] = "next_frame_observed"
            evidence.checkpoint('next_physics_before_optional_rpc')
            evidence.vehicle_response.capture_observation(snapshot, world, {"ego": ego, "lead": lead})
            tick["observed_control"] = _control_readback(ego)
            evidence.phase = "next_sensor_observation"
            packet, points, refs, point_ref = observe(snapshot, index+1)
            tick["status"] = "completed"
        evidence.status, evidence.phase = "completed", "cleanup"
    except Exception as error:
        failure = error
        evidence.error = type(error).__name__ + ": " + str(error)[:1000]
        for tick in evidence.ticks:
            if tick["status"] == "running":
                tick.update(status="failed", error=evidence.error)
    finally:
        # BaseException still receives cleanup; ordinary failures additionally
        # publish the retained prefix through the core failure evidence path.
        if evidence.sensor_configuration:
            evidence.sensor_configuration.gate.stop_accepting()
        for sensor in reversed(sensors):
            for method in ("stop", "destroy"):
                try:
                    result = journal.cleanup('sensor.' + method, lambda: getattr(sensor, method)(), {'actor_id': getattr(sensor, 'id', None)}, acknowledgement=method == 'destroy')
                    if method == "destroy" and not result:
                        evidence.cleanup.append("Sensor destruction was not acknowledged")
                except Exception as error:
                    evidence.cleanup.append(str(error)[:500])
        if evidence.sensor_configuration:
            try:
                evidence.sensor_configuration.gate.drain()
            except Exception as error:
                evidence.cleanup.append(str(error)[:500])
        for actor in reversed(actors):
            try:
                if not journal.cleanup('actor.destroy', actor.destroy, {'actor_id': actor.id}, acknowledgement=True):
                    evidence.cleanup.append("Vehicle destruction was not acknowledged")
            except Exception as error:
                evidence.cleanup.append(str(error)[:500])
        for operation, value in (((world.set_weather, original_weather), (world.apply_settings, original_settings)) if world_mutation_started else ()):
            try:
                journal.cleanup('world.' + operation.__name__, lambda: operation(value))
            except Exception as error:
                evidence.cleanup.append(str(error)[:500])
        try:
            if world_mutation_started:
                evidence.collision_journal.flush()
                durability.event('cleanup_finished', {'errors': evidence.cleanup, 'world_verified': False})
        except Exception as error:
            evidence.cleanup.append(type(error).__name__ + ': ' + str(error)[:500])
        if evidence.cleanup:
            evidence.status = "partial"
            evidence.error = evidence.error or "Cleanup/settings restore was not acknowledged"
            failure = failure or RuntimeError(evidence.error)
    if failure is not None:
        raise AdapterExecutionError("CARLA live stack failed during " + evidence.phase + ": " + evidence.error,
            artifact_payloads=evidence.artifacts()) from failure
    try:
        with collision_lock:
            contacts = {}
            for actor_id, at in sorted(collisions, key=lambda value: value[1]):
                if start_time <= at <= snapshot.timestamp.elapsed_seconds:
                    contacts.setdefault(actor_id, at)
        for actor_id, at in contacts.items():
            events.append(Event(time_s=max(0, at-start_time), type="collision", message=f"Observed simulator collision contact with actor {actor_id}"))
        events.append(Event(time_s=0, type="scope", message="Shared geometric LiDAR stack with simulator ego/map oracle. Surrounding GT is used only for scenario scripting and observer metrics; no RGB capture."))
        finite_ttc = [frame.ttc_s for frame in frames if frame.ttc_s is not None]
        distance = sum(math.hypot(b.ego.x-a.ego.x, b.ego.y-a.ego.y) for a, b in zip(frames, frames[1:]))
        values = [("collision_count", len(contacts), "observed contacts", 0, "eq"),
            ("min_ttc_s", min(finite_ttc) if finite_ttc else None, "s", 1.5, "gte"),
            ("max_deceleration_mps2", max(0, -min(frame.acceleration_mps2 for frame in frames)), "m/s²", 6, "lte"),
            ("max_jerk_mps3", max(abs(b.acceleration_mps2-a.acceleration_mps2)/runner.dt for a, b in zip(frames, frames[1:])), "m/s³", 10, "lte"),
            ("distance_m", distance, "m", 0, "gte")]
        metrics = [Metric(name=name, value=value, unit=unit, threshold=threshold, operator=operator,
            passed=value is None and name == "min_ttc_s" or value is not None and
                (value == threshold if operator == "eq" else value >= threshold if operator == "gte" else value <= threshold))
            for name, value, unit, threshold, operator in values]
        return SimulationResult(trajectory=frames, events=sorted(events, key=lambda event: event.time_s), metrics=metrics,
            model_fidelity=f"CARLA {version}; " + FIDELITY +
                (f"; STANDALONE physics candidate {candidate.data['candidate_input_hash']}" if candidate else ''),
            artifact_payloads=evidence.artifacts())
    except Exception as error:
        evidence.status, evidence.phase, evidence.error = "partial", "result_assembly", type(error).__name__ + ": " + str(error)[:1000]
        raise AdapterExecutionError("CARLA live stack result assembly failed: " + evidence.error,
            artifact_payloads=evidence.artifacts()) from error
