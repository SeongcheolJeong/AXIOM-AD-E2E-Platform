export type ComponentRole = "perception" | "policy" | "controller";
export const componentRoles: ComponentRole[] = [
  "perception",
  "policy",
  "controller",
];
export const roleLabels: Record<ComponentRole, string> = {
  perception: "Observation",
  policy: "Policy",
  controller: "Control",
};
export interface ParameterSchema {
  type?: string;
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  enum?: (string | number | boolean)[];
  default?: unknown;
  properties?: Record<string, ParameterSchema>;
  required?: string[];
  additionalProperties?: boolean;
  anyOf?: ParameterSchema[];
  minLength?: number;
  maxLength?: number;
}
export interface StackComponentMetadata {
  id: string;
  name: string;
  version: string;
  role: ComponentRole;
  status: "available" | "unavailable";
  parameter_schema: ParameterSchema;
  default_parameters: Record<string, unknown>;
  implementation: Record<string, string>;
  limitations: string[];
}
export interface ModuleSelection {
  id: string;
  version: string;
  parameters: Record<string, unknown>;
}
export type StackKernelVersion = "stack-kernel/1" | "stack-kernel/2";
interface StackDefinitionFields {
  reference_frame: string;
  perception: ModuleSelection;
  policy: ModuleSelection;
  controller: ModuleSelection;
}
export interface StackDefinitionV1 extends StackDefinitionFields {
  schema_version: "1";
}
export interface StackDefinitionV2 extends StackDefinitionFields {
  schema_version: "2";
  kernel_version: "stack-kernel/2";
}
export type StackDefinition = StackDefinitionV1 | StackDefinitionV2;
export interface StackCreate {
  name: string;
  description: string;
  note: string;
  definition: StackDefinition;
}
export interface StackSnapshot extends StackCreate {
  schema_version: "1";
  id: string;
  revision: number;
  created_at: string;
  kernel_version: StackKernelVersion;
  component_metadata: StackComponentMetadata[];
}
export interface StackDetail extends StackSnapshot {
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
export interface StackEgo {
  timestamp_ns: string;
  capture_time_ns: string | null;
  frame_id: string;
  speed_mps: number;
  route_heading_error_rad: number;
  pitch_rad: number;
  roll_rad: number;
  source: string;
}
export interface StackLidar {
  timestamp_ns: string;
  capture_time_ns: string | null;
  frame_id: string;
  point_count: number;
  points_sha256: string;
  size_bytes: number;
  source_message_sha256: string;
  complete_frame: boolean;
  coordinate_convention: "x_forward_y_left_z_up";
  encoding: "xyz_float32_le";
}
export interface StackPacket {
  sequence: number;
  at_ns: string;
  ego: StackEgo | null;
  lidar: StackLidar | null;
  unavailable_reasons: string[];
}
export interface CorridorObservation {
  status: "obstacle_observed" | "no_obstacle_observed" | "unknown";
  distance_m: number | null;
  nearest_point_xyz_m: [number, number, number] | null;
  input_points: number;
  forward_points: number;
  obstacle_points: number;
  ground_points: number;
  ground_bins_observed: number;
  supported_distance_m: number | null;
  coverage_qualified: boolean;
  reasons: string[];
  semantics: "observed_surface_distance_not_free_space_or_ttc";
}
export interface StackDecision {
  mode: "unknown_stop" | "obstacle_stop" | "approach" | "pilot_cruise" | "hold";
  target_speed_mps: number;
  stopping_distance_m: number;
  force_brake: boolean;
  reasons: string[];
}
export interface StackControl {
  throttle: number;
  brake: number;
  steer: number;
  reverse: false;
  hand_brake: false;
  steering_convention: "positive_left";
}
export interface StackOutputV1 {
  kernel_version: "stack-kernel/1";
  sequence: number;
  at_ns: string;
  observation: CorridorObservation;
  decision: StackDecision;
  control: StackControl;
  input_points_sha256: string | null;
  components: StackComponentMetadata[];
  warnings: string[];
  elapsed_ms: number;
}
export type StackOutput =
  StackOutputV1 | import("./StackV2Types").StackOutputV2;
