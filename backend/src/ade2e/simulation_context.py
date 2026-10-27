"""Pinned simulation control inputs and optional adapter DTOs; no engine imports."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from .base_schemas import Name, StrictModel
from .model_schemas import Sha256
from .scenario_schemas import Scenario
from .sensor_schemas import RigCreate, RigDetail, RigSnapshot, SensorKey, SensorTransform
from .stack_schemas import StackDetail, StackSnapshot, stack_kernel_version
from .simulation_sensor_schemas import SensorConfigurationDetail, SensorExecutionCapability
from .simulation_sensor_capabilities import (simulation_sensor_capabilities, effective_lidar_attributes,
    validate_sensor_configuration_snapshot, validate_configurable_mount, estimate_full_execution_evidence)

SensorProfileId = Literal['carla-lidar-ego/1', 'carla-lidar-ego-dense/1', 'carla-lidar-ego-near/1']
BASELINE_SENSOR_PROFILE = 'carla-lidar-ego/1'
DENSE_SENSOR_PROFILE = 'carla-lidar-ego-dense/1'
NEAR_SENSOR_PROFILE = 'carla-lidar-ego-near/1'


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def simulation_source_hash(content: bytes) -> str:
    """Hash exact source/representation bytes; never conflate distinct encodings."""
    return hashlib.sha256(content).hexdigest()


def _hash(value) -> str:
    return simulation_source_hash(_canonical(value))


class SimulationControlSelection(StrictModel):
    mode: Literal['stack'] = 'stack'
    stack_id: Name
    stack_revision: int = Field(ge=1, le=2**31-1, strict=True)
    rig_id: Name
    rig_revision: int = Field(ge=1, le=2**31-1, strict=True)
    lidar_sensor_key: SensorKey
    sensor_profile_id: SensorProfileId = BASELINE_SENSOR_PROFILE


class SimulationControlSelectionV2(StrictModel):
    schema_version: Literal['simulation-control-selection/2']
    mode: Literal['stack'] = 'stack'
    stack_id: Name
    stack_revision: int = Field(ge=1, le=2**31-1, strict=True)
    rig_id: Name
    rig_revision: int = Field(ge=1, le=2**31-1, strict=True)
    lidar_sensor_key: SensorKey
    sensor_configuration_id: Name
    sensor_configuration_revision: int = Field(ge=1, le=2**31-1, strict=True)


AnySimulationControlSelection = SimulationControlSelection | SimulationControlSelectionV2


class CarlaSensorProfile(StrictModel):
    id: SensorProfileId = BASELINE_SENSOR_PROFILE
    version: Literal['1'] = '1'
    blueprint_id: Literal['sensor.lidar.ray_cast'] = 'sensor.lidar.ray_cast'
    attributes: dict[str, str]
    mount: SensorTransform
    sensor_frame: Literal['lidar_roof'] = 'lidar_roof'
    reference_frame: Literal['ego'] = 'ego'
    coordinate_policy: Literal['carla_xyzi_y_reflection_then_declared_sensor_to_ego/1'] = 'carla_xyzi_y_reflection_then_declared_sensor_to_ego/1'
    clock_policy: Literal['carla_simulation_seconds_round_half_even_ns/1'] = 'carla_simulation_seconds_round_half_even_ns/1'
    max_points: Literal[20000] = 20000
    source: str
    profile_hash: Sha256

    @property
    def max_points_per_frame(self):
        return self.max_points


# Full recipe, explicitly set and read back by the native integration. The
# FLidarDescription defaults below are sourced from official 0.9.16, not an
# assumption about an installed custom build. Missing attributes fail closed.
_LIDAR_ATTRIBUTES = {
    'channels': '16', 'range': '80', 'points_per_second': '32000',
    'rotation_frequency': '20', 'sensor_tick': '0.0',
    'upper_fov': '10.0', 'lower_fov': '-30.0', 'horizontal_fov': '360.0',
    'atmosphere_attenuation_rate': '0.004', 'dropoff_general_rate': '0.45',
    'dropoff_intensity_limit': '0.8', 'dropoff_zero_intensity': '0.4',
    'noise_stddev': '0.0',
}
_PROFILE_SOURCE = 'https://github.com/carla-simulator/carla/blob/0.9.16/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/LidarDescription.h'


def sensor_profile_for_seed(seed: int, profile_id: str = BASELINE_SENSOR_PROFILE) -> CarlaSensorProfile:
    if type(seed) is not int or not 0 <= seed <= 2**31-1:
        raise ValueError('Sensor noise seed must match a valid scenario seed')
    if profile_id not in (BASELINE_SENSOR_PROFILE, DENSE_SENSOR_PROFILE, NEAR_SENSOR_PROFILE):
        raise ValueError('Unsupported simulation sensor profile')
    attributes = {**_LIDAR_ATTRIBUTES, 'noise_seed': str(seed)}
    if profile_id == DENSE_SENSOR_PROFILE:
        attributes['points_per_second'] = '128000'
    elif profile_id == NEAR_SENSOR_PROFILE:
        attributes.update(channels='64', points_per_second='256000', lower_fov='-65.0')
    value = {'id': profile_id, 'version': '1', 'blueprint_id': 'sensor.lidar.ray_cast',
        'attributes': attributes,
        'mount': {'translation_m': [0.0, 0.0, 2.2], 'rotation_xyzw': [0.0, 0.0, 0.0, 1.0]},
        'sensor_frame': 'lidar_roof', 'reference_frame': 'ego',
        'coordinate_policy': 'carla_xyzi_y_reflection_then_declared_sensor_to_ego/1',
        'clock_policy': 'carla_simulation_seconds_round_half_even_ns/1', 'max_points': 20000,
        'source': _PROFILE_SOURCE}
    return CarlaSensorProfile(**value, profile_hash=_hash(value))


def validate_sensor_profile_adapter(profile_id: str, adapter_version: str) -> None:
    """Prevent new recipes from being dispatched/read under a historical contract.

    Baseline predates this catalog; preserve its existing plugin/test versions.
    Native dispatch and durable readers independently enforce engine versions.
    """
    if profile_id not in (BASELINE_SENSOR_PROFILE, DENSE_SENSOR_PROFILE, NEAR_SENSOR_PROFILE):
        raise ValueError('Unsupported simulation sensor profile')
    if profile_id == DENSE_SENSOR_PROFILE and adapter_version not in {'0.6.0', '0.7.0', '0.8.0'}:
        raise ValueError('Dense LiDAR profile requires CARLA adapter 0.6.0, 0.7.0 or 0.8.0')
    if profile_id == NEAR_SENSOR_PROFILE and adapter_version not in {'0.7.0', '0.8.0'}:
        raise ValueError('Near-field LiDAR profile requires CARLA adapter 0.7.0 or 0.8.0')


def validate_simulation_adapter_context(context, adapter_version: str) -> None:
    """Fail closed before dispatch/read when the engine cannot execute a kernel."""
    if context.schema_version == 'simulation-control-context/2':
        if adapter_version not in context.sensor_capability_snapshot.supported_adapter_versions:
            raise ValueError('Configurable sensor context/2 requires CARLA adapter 0.8.0')
    else:
        validate_sensor_profile_adapter(context.sensor_profile.id, adapter_version)
    if context.stack_snapshot.kernel_version == 'stack-kernel/2' and adapter_version not in {'0.7.0', '0.8.0'}:
        raise ValueError('Stack kernel/2 requires CARLA adapter 0.7.0 or 0.8.0')


class SimulationSensorProfileOption(StrictModel):
    id: SensorProfileId
    version: Literal['1'] = '1'
    name: str
    description: str
    adapter_id: Literal['carla'] = 'carla'
    supported_adapter_versions: list[str]
    attributes: dict[str, str]
    seed_policy: Literal['effective_scenario_seed'] = 'effective_scenario_seed'
    mount: SensorTransform
    max_points: Literal[20000] = 20000
    nominal_points_per_frame: int
    source: str


def simulation_sensor_profiles() -> list[SimulationSensorProfileOption]:
    """Discover immutable recipes without preparing or starting an engine run.

    Seed is deliberately absent: the effective scenario seed is pinned when the
    request is prepared. Nominal rays are not observed return/valid point counts.
    """
    options = []
    for profile_id, name, description, versions in (
        (BASELINE_SENSOR_PROFILE, 'Baseline LiDAR · 32k rays/s',
         'Original 16-channel ray-cast recipe. Uncalibrated; actual returns vary with geometry and dropout.',
         ['0.4.0', '0.4.1', '0.5.0', '0.6.0', '0.7.0', '0.8.0']),
        (DENSE_SENSOR_PROFILE, 'Dense LiDAR · 128k rays/s',
         'Four times the nominal horizontal sampling; same mount, channels, FOV, rotation, noise and dropout. Uncalibrated; increased compute and evidence size.',
         ['0.6.0', '0.7.0', '0.8.0']),
        (NEAR_SENSOR_PROFILE, 'Near-field LiDAR · 64 channels',
         'Explicit experimental 64-channel recipe with -65° lower FOV and 256k nominal rays/s. Same roof mount and dropout. Qualify actual near-field samples separately; no free-space or calibrated sensor guarantee.',
         ['0.7.0', '0.8.0']),
    ):
        profile = sensor_profile_for_seed(0, profile_id)
        options.append(SimulationSensorProfileOption(id=profile_id, name=name, description=description,
            supported_adapter_versions=versions,
            attributes={k: v for k, v in profile.attributes.items() if k != 'noise_seed'},
            mount=profile.mount, nominal_points_per_frame=int(profile.attributes['points_per_second']) // 20,
            source=profile.source))
    return options


class PreparedSimulationContext(StrictModel):
    schema_version: Literal['simulation-control-context/1'] = 'simulation-control-context/1'
    mode: Literal['stack'] = 'stack'
    stack_snapshot: StackDetail
    rig_snapshot: RigDetail
    lidar_sensor_key: SensorKey
    sensor_profile: CarlaSensorProfile
    ego_source_policy: Literal['carla-ego-state-and-map-route/1'] = 'carla-ego-state-and-map-route/1'
    context_hash: Sha256

    @model_validator(mode='after')
    def content_integrity(self):
        stack = self.stack_snapshot
        manifest_fields = {'manifest_hash', 'manifest_size_bytes', 'download_url'}
        content = _canonical(StackSnapshot.model_validate(stack.model_dump(mode='json', exclude=manifest_fields)).model_dump(mode='json'))
        if (simulation_source_hash(content) != stack.manifest_hash or len(content) != stack.manifest_size_bytes
                or stack.download_url != f'/api/v1/stacks/{stack.id}/export?revision={stack.revision}'):
            raise ValueError('Pinned stack snapshot failed its integrity check')
        rig = self.rig_snapshot
        snapshot = RigSnapshot.model_validate(rig.model_dump(mode='json', exclude=manifest_fields))
        definition = RigCreate.model_validate({key: getattr(snapshot, key) for key in RigCreate.model_fields})
        content = _canonical(snapshot.model_dump(mode='json'))
        if (simulation_source_hash(content) != rig.manifest_hash or len(content) != rig.manifest_size_bytes
                or rig.definition_hash != _hash(definition.model_dump(mode='json'))
                or rig.download_url != f'/api/v1/sensor-rigs/{rig.id}/export?revision={rig.revision}'):
            raise ValueError('Pinned rig snapshot failed its integrity check')
        if self.context_hash != _hash(self.model_dump(mode='json', exclude={'context_hash'})):
            raise ValueError('Simulation context hash does not match its contents')
        try:
            seed = int(self.sensor_profile.attributes['noise_seed'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Pinned sensor profile is missing its seed') from error
        if self.sensor_profile != sensor_profile_for_seed(seed, self.sensor_profile.id):
            raise ValueError('Pinned sensor profile is not the complete supported recipe')
        sensors = [sensor for sensor in rig.sensors if sensor.key == self.lidar_sensor_key]
        if (stack.definition.reference_frame != 'ego' or rig.reference_frame != 'ego'
                or rig.coordinate_convention != 'right_handed_z_up' or len(sensors) != 1
                or sensors[0].kind != 'lidar' or sensors[0].frame_id != self.sensor_profile.sensor_frame
                or sensors[0].transform != self.sensor_profile.mount):
            raise ValueError('Selected rig LiDAR frame/mount does not match the pinned ego sensor recipe')
        return self


class CarlaConfigurableSensorProfile(StrictModel):
    id: Literal['carla-raycast-lidar/1'] = 'carla-raycast-lidar/1'
    version: Literal['1'] = '1'
    blueprint_id: Literal['sensor.lidar.ray_cast'] = 'sensor.lidar.ray_cast'
    attributes: dict[str, str]
    mount: SensorTransform
    sensor_frame: Name
    reference_frame: Literal['ego'] = 'ego'
    coordinate_policy: Literal['carla_xyzi_y_reflection_then_declared_sensor_to_ego/1'] = 'carla_xyzi_y_reflection_then_declared_sensor_to_ego/1'
    clock_policy: Literal['carla_simulation_seconds_round_half_even_ns/1'] = 'carla_simulation_seconds_round_half_even_ns/1'
    max_points_per_frame: Literal[20000] = 20000
    source: str
    profile_hash: Sha256

    @property
    def max_points(self):
        return self.max_points_per_frame


def configurable_sensor_profile(configuration, rig, sensor_key, seed):
    configuration = validate_sensor_configuration_snapshot(configuration)
    sensors = [item for item in rig.sensors if item.key == sensor_key]
    if (rig.reference_frame != 'ego' or rig.coordinate_convention != 'right_handed_z_up'
            or len(sensors) != 1 or sensors[0].kind != 'lidar'):
        raise ValueError('Configured LiDAR requires a selected rig sensor in the right-handed ego frame')
    sensor = sensors[0]
    validate_configurable_mount(sensor.transform)
    value = dict(id='carla-raycast-lidar/1', version='1', blueprint_id='sensor.lidar.ray_cast',
        attributes=effective_lidar_attributes(configuration.parameters, seed),
        mount=sensor.transform.model_dump(mode='json'), sensor_frame=sensor.frame_id, reference_frame='ego',
        coordinate_policy='carla_xyzi_y_reflection_then_declared_sensor_to_ego/1',
        clock_policy='carla_simulation_seconds_round_half_even_ns/1', max_points_per_frame=20000,
        source=_PROFILE_SOURCE)
    return CarlaConfigurableSensorProfile(**value, profile_hash=_hash(value))


class PreparedSimulationContextV2(StrictModel):
    schema_version: Literal['simulation-control-context/2'] = 'simulation-control-context/2'
    mode: Literal['stack'] = 'stack'
    stack_snapshot: StackDetail
    rig_snapshot: RigDetail
    lidar_sensor_key: SensorKey
    sensor_profile: CarlaConfigurableSensorProfile
    sensor_configuration_snapshot: SensorConfigurationDetail
    sensor_capability_snapshot: SensorExecutionCapability
    physical_mount_policy: Literal['carla_rh_rig_to_lh_native_matrix/1'] = 'carla_rh_rig_to_lh_native_matrix/1'
    source_serialization_policy: Literal['carla_configured_lidar_json/1'] = 'carla_configured_lidar_json/1'
    ego_source_policy: Literal['carla-ego-state-and-map-route/1'] = 'carla-ego-state-and-map-route/1'
    context_hash: Sha256

    @model_validator(mode='after')
    def content_integrity(self):
        # Reuse context/1's immutable snapshot proof with its original recipe,
        # without applying its fixed roof-mount restriction to this new context.
        manifest_fields = {'manifest_hash', 'manifest_size_bytes', 'download_url'}
        for detail, model, path in ((self.stack_snapshot, StackSnapshot, 'stacks'),
                                    (self.rig_snapshot, RigSnapshot, 'sensor-rigs')):
            snapshot = model.model_validate(detail.model_dump(mode='json', exclude=manifest_fields))
            content = _canonical(snapshot.model_dump(mode='json'))
            if (simulation_source_hash(content) != detail.manifest_hash or len(content) != detail.manifest_size_bytes
                    or detail.download_url != f'/api/v1/{path}/{detail.id}/export?revision={detail.revision}'):
                raise ValueError('Pinned stack or rig snapshot failed its integrity check')
        definition = RigCreate.model_validate({k:getattr(self.rig_snapshot,k) for k in RigCreate.model_fields})
        if self.rig_snapshot.definition_hash != _hash(definition.model_dump(mode='json')):
            raise ValueError('Pinned rig definition hash mismatch')
        validate_sensor_configuration_snapshot(self.sensor_configuration_snapshot)
        if self.sensor_capability_snapshot != simulation_sensor_capabilities()[0]:
            raise ValueError('Pinned sensor capability differs from the supported versioned contract')
        try:
            seed = int(self.sensor_profile.attributes['noise_seed'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Configured sensor recipe is missing its seed') from error
        expected = configurable_sensor_profile(self.sensor_configuration_snapshot, self.rig_snapshot, self.lidar_sensor_key, seed)
        if self.sensor_profile != expected or self.stack_snapshot.definition.reference_frame != 'ego':
            raise ValueError('Configured sensor profile differs from its pinned configuration or rig')
        if self.context_hash != _hash(self.model_dump(mode='json', exclude={'context_hash'})):
            raise ValueError('Simulation context hash does not match its contents')
        return self


AnyPreparedSimulationContext = PreparedSimulationContext | PreparedSimulationContextV2


def validate_prepared_context(value) -> AnyPreparedSimulationContext:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Simulation context must be an object')
    if value.get('schema_version', 'simulation-control-context/1') == 'simulation-control-context/1':
        return PreparedSimulationContext.model_validate(value)
    if value.get('schema_version') == 'simulation-control-context/2':
        return PreparedSimulationContextV2.model_validate(value)
    raise ValueError('Unsupported simulation context version')


def build_simulation_context(stack: StackDetail, rig: RigDetail, selection: AnySimulationControlSelection,
                             seed: int, *, sensor_configuration=None) -> AnyPreparedSimulationContext:
    if (stack.id, stack.revision, rig.id, rig.revision) != (selection.stack_id, selection.stack_revision, selection.rig_id, selection.rig_revision):
        raise ValueError('Selected stack/rig revisions differ from their snapshots')
    if isinstance(selection, SimulationControlSelectionV2):
        if sensor_configuration is None or (sensor_configuration.id, sensor_configuration.revision) != (selection.sensor_configuration_id, selection.sensor_configuration_revision):
            raise ValueError('Selected sensor configuration revision differs from its snapshot')
        value = dict(schema_version='simulation-control-context/2', mode='stack',
            stack_snapshot=stack.model_dump(mode='json'), rig_snapshot=rig.model_dump(mode='json'),
            lidar_sensor_key=selection.lidar_sensor_key,
            sensor_profile=configurable_sensor_profile(sensor_configuration, rig, selection.lidar_sensor_key, seed).model_dump(mode='json'),
            sensor_configuration_snapshot=sensor_configuration.model_dump(mode='json'),
            sensor_capability_snapshot=simulation_sensor_capabilities()[0].model_dump(mode='json'),
            physical_mount_policy='carla_rh_rig_to_lh_native_matrix/1',
            source_serialization_policy='carla_configured_lidar_json/1',
            ego_source_policy='carla-ego-state-and-map-route/1')
        return PreparedSimulationContextV2(**value, context_hash=_hash(value))
    if sensor_configuration is not None:
        raise ValueError('A context/1 request cannot include a configurable sensor snapshot')
    value = {'schema_version': 'simulation-control-context/1', 'mode': 'stack',
        'stack_snapshot': stack.model_dump(mode='json'), 'rig_snapshot': rig.model_dump(mode='json'),
        'lidar_sensor_key': selection.lidar_sensor_key, 'sensor_profile': sensor_profile_for_seed(seed, selection.sensor_profile_id).model_dump(mode='json'),
        'ego_source_policy': 'carla-ego-state-and-map-route/1'}
    return PreparedSimulationContext(**value, context_hash=_hash(value))


def validate_simulation_context(scenario: Scenario, context: PreparedSimulationContext, *, stack_registry=None, verify_components=True) -> PreparedSimulationContext:
    context = validate_prepared_context(context)
    if scenario.category not in {'lane_follow', 'emergency_brake'} or not 2 <= scenario.duration_s <= 4 or abs(scenario.duration_s/.05-round(scenario.duration_s/.05)) > 1e-6:
        raise ValueError('Live CARLA stack supports lane_follow/emergency_brake for 2–4 seconds at 20 Hz')
    expected_profile = (configurable_sensor_profile(context.sensor_configuration_snapshot, context.rig_snapshot,
        context.lidar_sensor_key, scenario.seed) if isinstance(context, PreparedSimulationContextV2)
        else sensor_profile_for_seed(scenario.seed, context.sensor_profile.id))
    if context.sensor_profile != expected_profile:
        raise ValueError('Pinned sensor seed differs from the effective scenario')
    if isinstance(context, PreparedSimulationContextV2):
        budget = estimate_full_execution_evidence(context, scenario)
        if not budget.admissible:
            raise ValueError('Configured sensor exceeds full-run evidence budget: '+', '.join(budget.reasons))
    if verify_components:
        from .stack_runtime import StackRegistry
        registry = stack_registry or StackRegistry()
        metadata = registry.validate_definition(context.stack_snapshot.definition)
        if context.stack_snapshot.kernel_version != stack_kernel_version(context.stack_snapshot.definition) or metadata != context.stack_snapshot.component_metadata:
            raise ValueError('Pinned stack kernel/component metadata differs from this installation')
        for role in ('perception', 'policy', 'controller'):
            selection = getattr(context.stack_snapshot.definition, role)
            component = registry.get(selection.id, selection.version)
            normalized = component.validate_parameters(json.loads(_canonical(selection.parameters)))
            if _canonical(normalized) != _canonical(selection.parameters):
                raise ValueError('Pinned stack parameters no longer match the component contract')
        if registry.validate_definition(context.stack_snapshot.definition) != context.stack_snapshot.component_metadata:
            raise ValueError('Stack component metadata changed during parameter validation')
    return context


def simulation_input_hash(scenario: Scenario, adapter_id: str, adapter_version: str, context: PreparedSimulationContext | None = None) -> str:
    value = {'scenario': scenario.model_dump(mode='json'), 'adapter_id': adapter_id, 'adapter_version': adapter_version}
    if context is not None:
        value.update(schema_version='simulation-input/3' if isinstance(context, PreparedSimulationContextV2) else 'simulation-input/2', context=context.model_dump(mode='json'))
    return _hash(value)


class SimulationExecutionRequest(StrictModel):
    schema_version: Literal['simulation-execution-request/1'] = 'simulation-execution-request/1'
    execution_id: Name
    input_hash: Sha256
    scenario: Scenario
    context: PreparedSimulationContext

    @model_validator(mode='after')
    def consistent_source(self):
        validate_simulation_context(self.scenario, self.context, verify_components=False)
        return self


def execution_correlation(request: SimulationExecutionRequest) -> dict[str, str]:
    return {'execution_id': request.execution_id, 'input_hash': request.input_hash, 'context_hash': request.context.context_hash}


def validate_execution_correlation(value: dict, request: SimulationExecutionRequest) -> None:
    if any(value.get(key) != expected for key, expected in execution_correlation(request).items()):
        raise ValueError('Execution result correlation differs from the pinned request')


class SimulationExecutionRequestV2(StrictModel):
    schema_version: Literal['simulation-execution-request/2'] = 'simulation-execution-request/2'
    execution_id: Name
    input_hash: Sha256
    scenario: Scenario
    context: PreparedSimulationContextV2

    @model_validator(mode='after')
    def consistent_source(self):
        validate_simulation_context(self.scenario, self.context, verify_components=False)
        return self


AnySimulationExecutionRequest = SimulationExecutionRequest | SimulationExecutionRequestV2


def validate_simulation_execution_request(value) -> AnySimulationExecutionRequest:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Simulation execution request must be an object')
    version = value.get('schema_version', 'simulation-execution-request/1')
    if version == 'simulation-execution-request/1':
        return SimulationExecutionRequest.model_validate(value)
    if version == 'simulation-execution-request/2':
        return SimulationExecutionRequestV2.model_validate(value)
    raise ValueError('Unsupported simulation execution request version')


def make_simulation_execution_request(*, execution_id, input_hash, scenario, context) -> AnySimulationExecutionRequest:
    context = validate_prepared_context(context)
    model = SimulationExecutionRequestV2 if isinstance(context, PreparedSimulationContextV2) else SimulationExecutionRequest
    return model(execution_id=execution_id, input_hash=input_hash, scenario=scenario, context=context)
