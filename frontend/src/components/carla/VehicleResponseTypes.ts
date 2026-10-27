// Mirrors ade2e.vehicle_response: raw observation values are never policy inputs.
export type Vector3 = { x: number; y: number; z: number };
export type DiagnosticValue = {
  status: "available" | "unavailable" | "null" | "nonfinite";
  value: number | boolean | null;
  reason: string | null;
};
export type RpcBracket = {
  before_world_frame: number | null;
  after_world_frame: number | null;
  status: "available" | "unavailable";
  error: string | null;
};
export type ReportedControl = import("./CarlaTypes").CarlaVehicleControl & {
  gear: number;
  manual_gear_shift: boolean;
};
export type WheelFields = {
  index: number;
  fields: Record<string, DiagnosticValue>;
};
export type ResponseActor = {
  actor_id: number;
  position_m: Vector3;
  rotation_deg: { pitch: number; yaw: number; roll: number };
  forward_vector: Vector3;
  velocity_mps: Vector3;
  acceleration_mps2: Vector3;
  angular_velocity_deg_s: Vector3;
  reported_control: RpcBracket & { data: ReportedControl | null };
  telemetry: RpcBracket & {
    source_api: "vehicle.get_telemetry_data";
    interpretation: "raw_api_fields_units_unverified";
    scalars: Record<string, DiagnosticValue>;
    wheels: WheelFields[];
  };
};
export type ResponseObservation = {
  world_frame: number;
  source_time_s: number;
  delta_seconds: number;
  actors: Record<"ego" | "lead", ResponseActor>;
};
export type PhysicsReadback = RpcBracket & {
  actor_id: number;
  requested_tire_friction: number;
  requested_tire_friction_float32: number;
  friction_status: "matched" | "mismatch" | "unavailable";
  scalars: Record<string, DiagnosticValue>;
  wheels: WheelFields[];
};
export type VehicleResponse = {
  schema_version: "carla-vehicle-response/1";
  execution_id: string;
  input_hash: string;
  context_hash: string;
  worker_adapter_version: string;
  server_version: string;
  status: "completed" | "partial";
  coordinate_convention: "carla_left_handed_world";
  angular_velocity_basis: "official_carla_0.9.16_degrees_per_second_installed_build_separate";
  expected_observations: number;
  after_rest: ResponseObservation | null;
  physics_configuration: Partial<Record<"ego" | "lead", PhysicsReadback>>;
  initial_velocity_requests: {
    role: "ego" | "lead";
    actor_id: number;
    after_world_frame: number;
    velocity_mps: Vector3;
    requested_speed_mps: number;
    ack_status: "unknown";
    operation: "actor.set_target_velocity";
  }[];
  observations: ResponseObservation[];
  error: string | null;
  limitations: string[];
};
