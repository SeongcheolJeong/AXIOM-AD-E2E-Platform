"""Pure versioned configuration, physical mount and evidence admission helpers."""
from __future__ import annotations

import hashlib
import json
import math

from .simulation_sensor_schemas import (CarlaLidarParameters, SensorExecutionCapability,
    SensorConfigurationCreate, SensorConfigurationSnapshot, SensorConfigurationDetail, ExecutionEvidenceBudget)

MANIFEST_FIELDS = {'manifest_hash', 'manifest_size_bytes', 'download_url'}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def content_hash(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def simulation_sensor_capabilities() -> list[SensorExecutionCapability]:
    schema = CarlaLidarParameters.model_json_schema()
    return [SensorExecutionCapability(name='Configurable ray-cast LiDAR',
        supported_adapter_versions=['0.8.0'], supported_context_versions=['simulation-control-context/2'],
        supported_kernel_versions=['stack-kernel/1', 'stack-kernel/2'],
        parameter_schema=schema, default_parameters=CarlaLidarParameters(), schema_hash=content_hash(schema),
        mount_rules={'reference_frame': 'ego', 'coordinate_convention': 'right_handed_z_up',
            'translation_min_m': [-5.0, -5.0, 0.0], 'translation_max_m': [5.0, 5.0, 5.0],
            'rotation': 'finite_unit_quaternion_xyzw_no_normalization',
            'physical_mount_policy': 'carla_rh_rig_to_lh_native_matrix/1',
            'frame_policy': 'selected_rig_sensor_frame'},
        limits={'max_points_per_frame': 20000, 'max_observation_frames': 81,
            'duration_min_s': 2.0, 'duration_max_s': 4.0, 'fixed_delta_seconds': 0.05,
            'max_artifact_bytes': 20*1024**2, 'max_total_payload_bytes': 32*1024**2,
            'max_wire_bytes': 48*1024**2, 'max_lidars': 1},
        limitations=['Ray-cast geometry is not optical or hardware calibration.',
            'A declared mount is not a collision mesh or visibility guarantee.',
            'No point deletion, owner filtering or semantic labels enter control.',
            'Full-run admission assumes all emitted rays return; dropout does not reduce the budget.',
            'Capability availability and exact native readback are checked before dispatch.'])]


def validate_sensor_configuration_snapshot(value) -> SensorConfigurationDetail:
    detail = SensorConfigurationDetail.model_validate(value)
    snapshot = SensorConfigurationSnapshot.model_validate(detail.model_dump(mode='json', exclude=MANIFEST_FIELDS))
    definition = SensorConfigurationCreate.model_validate({k: getattr(snapshot, k) for k in SensorConfigurationCreate.model_fields})
    data = canonical(snapshot.model_dump(mode='json'))
    if (len(data) != detail.manifest_size_bytes or hashlib.sha256(data).hexdigest() != detail.manifest_hash
            or content_hash(definition.model_dump(mode='json')) != detail.definition_hash
            or detail.capability_schema_hash != simulation_sensor_capabilities()[0].schema_hash
            or detail.download_url != f'/api/v1/simulation-sensor-configurations/{detail.id}/export?revision={detail.revision}'):
        raise ValueError('Sensor configuration snapshot failed its integrity check')
    return detail


def effective_lidar_attributes(parameters: CarlaLidarParameters, seed: int) -> dict[str, str]:
    parameters = CarlaLidarParameters.model_validate(parameters.model_dump(mode='json'))
    if type(seed) is not int or not 0 <= seed <= 2**31-1:
        raise ValueError('Sensor seed must be the effective scenario seed')
    # This new recipe's text contract is deliberately separate from context/1.
    return {**{key: json.dumps(value, allow_nan=False, separators=(',', ':'))
               for key, value in parameters.model_dump(mode='json').items()}, 'noise_seed': str(seed)}


def validate_configurable_mount(mount) -> None:
    if mount is None:
        raise ValueError('A configured physical sensor requires a declared transform')
    x, y, z = mount.translation_m
    if not (-5 <= x <= 5 and -5 <= y <= 5 and 0 <= z <= 5):
        raise ValueError('Physical LiDAR mount must have x/y within ±5 m and z within 0–5 m')
    if any(not math.isfinite(v) for v in (*mount.translation_m, *mount.rotation_xyzw)):
        raise ValueError('Physical sensor mount must be finite')


def estimate_full_execution_evidence(context, scenario) -> ExecutionEvidenceBudget:
    """Reserve uncompressed full-return evidence; native actual caps still apply.

    Metadata allowance is deliberately bounded and conservative: 128 KiB per
    observation plus 4 MiB fixed for startup/configuration/manifests. This covers
    the bounded v1/v2 grid/plan/state and duplicate source metadata, not arbitrary
    plugins or unconstrained diagnostics. No compression/dropout savings assumed.
    """
    intervals = round(scenario.duration_s/.05)
    frames = intervals+1
    a = context.sensor_profile.attributes
    channels, pps = int(a['channels']), int(a['points_per_second'])
    points = channels*((pps+20*channels-1)//(20*channels))
    raw = frames*points*16
    consumed = frames*points*12
    # Foxglove JSON stores all XYZ+intensity bytes as base64; reserve MCAP
    # framing/schema plus every per-message JSON envelope without compression.
    recording = frames*(4*((points*16+2)//3)+8192)+1024**2
    metadata = frames*128*1024+4*1024**2
    total = raw+consumed+recording+metadata
    # Exact upper bound on per-artifact base64 padding, plus bounded wire fields.
    wire = 4*((total+2)//3)+4*16+1024**2
    artifact_limit, total_limit, wire_limit = 20*1024**2, 32*1024**2, 48*1024**2
    reasons=[]
    if not 40 <= intervals <= 80 or abs(scenario.duration_s/.05-intervals)>1e-6:
        reasons.append('duration_outside_2_to_4_seconds_at_20_hz')
    if points>20000: reasons.append('frame_point_limit_exceeded')
    if max(raw, consumed, recording, metadata)>artifact_limit: reasons.append('conservative_artifact_limit_exceeded')
    if total>total_limit: reasons.append('conservative_total_payload_limit_exceeded')
    if wire>wire_limit: reasons.append('conservative_wire_limit_exceeded')
    return ExecutionEvidenceBudget(measurement_intervals=intervals, observation_frames=frames,
        conservative_points_per_frame=points, raw_points_bytes=raw, consumed_points_bytes=consumed,
        recording_bytes=recording, metadata_bytes=metadata, total_payload_bytes=total, wire_bytes=wire,
        per_artifact_limit_bytes=artifact_limit, total_payload_limit_bytes=total_limit,
        wire_limit_bytes=wire_limit, admissible=not reasons, reasons=reasons,
        assumptions=['Every emitted ray returns; no dropout or compression discount.',
            'Full XYZI raw, full XYZ consumed and MCAP base64 copies are retained.',
            '128 KiB per observation and 4 MiB fixed metadata allowance; native actual caps remain mandatory.'])
