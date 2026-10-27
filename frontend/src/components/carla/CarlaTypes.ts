export type CarlaSensorProfileId =
  "carla-lidar-ego/1" | "carla-lidar-ego-dense/1" | "carla-lidar-ego-near/1";

export interface SimulationSensorProfileOption {
  id: CarlaSensorProfileId;
  version: "1";
  name: string;
  description: string;
  adapter_id: "carla";
  supported_adapter_versions: string[];
  attributes: Record<string, string>;
  seed_policy: "effective_scenario_seed";
  mount: {
    translation_m: [number, number, number];
    rotation_xyzw: [number, number, number, number];
  };
  max_points: 20000;
  nominal_points_per_frame: number;
  source: string;
}

interface SimulationControlBase {
  mode: "stack";
  stack_id: string;
  stack_revision: number;
  rig_id: string;
  rig_revision: number;
  lidar_sensor_key: string;
}

export interface SimulationControlSelectionV1 extends SimulationControlBase {
  schema_version?: never;
  sensor_profile_id: CarlaSensorProfileId;
}
export interface SimulationControlSelectionV2 extends SimulationControlBase {
  schema_version: "simulation-control-selection/2";
  sensor_configuration_id: string;
  sensor_configuration_revision: number;
}
export type SimulationControlSelection =
  SimulationControlSelectionV1 | SimulationControlSelectionV2;
export const isConfiguredControl = (
  value: SimulationControlSelection,
): value is SimulationControlSelectionV2 =>
  value.schema_version === "simulation-control-selection/2";

export const EMPTY_CARLA_CONTROL: SimulationControlSelectionV1 = {
  mode: "stack",
  stack_id: "",
  stack_revision: 0,
  rig_id: "",
  rig_revision: 0,
  lidar_sensor_key: "",
  sensor_profile_id: "carla-lidar-ego/1",
};

interface SimulationContextBase {
  mode: "stack";
  stack_snapshot: import("../stacks/StackTypes").StackDetail;
  rig_snapshot: import("../sensors/SensorTypes").RigDetail;
  lidar_sensor_key: string;
  sensor_profile: {
    id: string;
    version: "1";
    blueprint_id: "sensor.lidar.ray_cast";
    attributes: Record<string, string>;
    mount: {
      translation_m: [number, number, number];
      rotation_xyzw: [number, number, number, number];
    };
    sensor_frame: string;
    reference_frame: "ego";
    coordinate_policy: "carla_xyzi_y_reflection_then_declared_sensor_to_ego/1";
    clock_policy: "carla_simulation_seconds_round_half_even_ns/1";
    max_points?: 20000;
    max_points_per_frame?: 20000;
    source: string;
    profile_hash: string;
  };
  ego_source_policy: "carla-ego-state-and-map-route/1";
  context_hash: string;
}
export interface PreparedSimulationContextV1 extends SimulationContextBase {
  schema_version: "simulation-control-context/1";
}
export interface PreparedSimulationContextV2 extends SimulationContextBase {
  schema_version: "simulation-control-context/2";
  physical_mount_policy: "carla_rh_rig_to_lh_native_matrix/1";
  source_serialization_policy: "carla_configured_lidar_json/1";
  sensor_configuration_snapshot: import("./SensorConfigurationTypes").SensorConfigurationDetail;
  sensor_capability_snapshot: import("./SensorConfigurationTypes").SensorExecutionCapability;
}
export type PreparedSimulationContext =
  PreparedSimulationContextV1 | PreparedSimulationContextV2;
export interface CarlaVehicleControl {
  throttle: number;
  brake: number;
  steer: number;
  reverse: boolean;
  hand_brake: boolean;
}
export interface CarlaSourceRef {
  channel_id: number;
  ordinal: number;
  message_sha256: string;
  log_time_ns: string;
  publish_time_ns: string;
  world_frame: number;
  raw_carla_xyzi_sha256?: string;
  raw_points?: {
    artifact_name: "carla-stack-raw-xyzi.bin";
    offset_bytes: number;
    size_bytes: number;
    sha256: string;
    encoding: "xyzi_float32_le_carla";
  };
}
export interface CarlaPointSlice {
  artifact_name: "carla-stack-points.bin";
  offset_bytes: number;
  size_bytes: number;
  sha256: string;
  point_count: number;
  frame_id: "ego";
}
export interface CarlaLiveTick {
  schema_version: "carla-live-stack-tick/1";
  sequence: number;
  status: "not_attempted" | "running" | "completed" | "failed";
  source_world_frame: number | null;
  source_time_s: number | null;
  packet: import("../stacks/StackTypes").StackPacket | null;
  output: import("../stacks/StackTypes").StackOutput | null;
  source_refs: { lidar: CarlaSourceRef | null; ego: CarlaSourceRef | null };
  points: CarlaPointSlice | null;
  requested_carla_control: CarlaVehicleControl | null;
  control_acknowledged: boolean;
  control_rpc_response: {
    actor_id: number | null;
    error: string | null;
  } | null;
  next_world_frame: number | null;
  next_time_s: number | null;
  observed_control: CarlaVehicleControl | null;
  next_ego: import("../../types").Pose | null;
  application_status:
    | "not_sent"
    | "send_outcome_unknown"
    | "send_rejected"
    | "acknowledged_without_next_observation"
    | "next_frame_observed";
  error: string | null;
}
export interface CarlaLiveTrace {
  schema_version: "carla-live-stack-trace/1";
  execution_id: string;
  input_hash: string;
  context_hash: string;
  status: "completed" | "partial";
  expected_intervals: number;
  ticks: CarlaLiveTick[];
  limitations?: string[];
  error?: string | null;
}
export interface CarlaLiveManifest {
  ego_actor_id: number | null;
  actual_lidar_attributes?: Record<string, { type: string; value: number }>;
  control_transport?: string;
  schema_version: "carla-live-stack-manifest/1";
  execution_id: string;
  input_hash: string;
  context_hash: string;
  status: "completed" | "partial";
  execution_context: PreparedSimulationContext;
  worker_adapter_version: string;
  server_version: string;
  map_name: string;
  scenario_id: string;
  scenario_revision: number;
  seed: number;
  fixed_delta_seconds: number;
  expected_intervals: number;
  expected_observations: number;
  observed_frames: number;
  retained_source_observations: number;
  acknowledged_commands: number;
  next_frame_observed_commands: number;
  failure_phase: string | null;
  error: string | null;
  cleanup_errors: string[];
  limitations: string[];
  sensor_scope: string;
  artifacts: {
    name: string;
    kind: string;
    sha256: string;
    size_bytes: number;
  }[];
}
