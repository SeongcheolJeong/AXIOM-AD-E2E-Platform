import type { StackComponentMetadata, StackControl } from "./StackTypes";
export interface GridCellV2 {
  longitudinal_index: number;
  lateral_index: number;
  x_bounds_m: [number, number];
  y_bounds_m: [number, number];
  ground_hits: number;
  point_min_xyz_m: [number, number, number] | null;
  point_max_xyz_m: [number, number, number] | null;
  state: "qualified" | "unknown";
  reasons: string[];
}
export interface GridCoverageV2 {
  status: "invalid" | "partial" | "qualified";
  ego_front_x_m: number;
  lookahead_x_m: number;
  longitudinal_cell_m: number;
  lateral_cell_m: number;
  min_ground_hits_per_cell: number;
  expected_cells: number;
  qualified_cells: number;
  qualified_prefix_m: number | null;
  cells: GridCellV2[];
  unknown_regions: {
    x_bounds_m: [number, number];
    y_bounds_m: [number, number];
    reasons: string[];
  }[];
  sample_model: "ground-hit-grid/1";
  free_space_certified: false;
}
export interface PositiveSurfaceV2 {
  distance_from_bumper_m: number;
  nearest_point_xyz_m: [number, number, number];
  observed_points: number;
  meaning: "retained_height-band_surface_not_object_identity";
}
export interface CorridorObservationV2 {
  schema_version: "2";
  input_valid: boolean;
  status: "invalid" | "sampled";
  input_points: number;
  forward_points: number;
  ground_points: number;
  positive_surface: PositiveSurfaceV2 | null;
  coverage: GridCoverageV2;
  reasons: string[];
}
export interface StopPlanV2 {
  schema_version: "symmetric-s-curve-stop/1";
  started_at_ns: string;
  initial_speed_mps: number;
  jerk_limit_mps3: number;
  deceleration_limit_mps2: number;
  peak_deceleration_mps2: number;
  latency_s: number;
  ramp_s: number;
  constant_deceleration_s: number;
  duration_s: number;
  distance_m: number;
  model_status: "assumed_uncalibrated";
  initial_acceleration_mps2: number;
  initial_acceleration_source: "model_assumption_not_measurement";
}
export interface StackStateV2 {
  mode:
    "ready" | "controlled_stop" | "hold" | "emergency_stop" | "unknown_stop";
  latched: boolean;
  latch_reason: string | null;
  stop_plan: StopPlanV2 | null;
  last_sequence: number | null;
  last_at_ns: string | null;
  reset_policy: "new_session_only";
  previous_speed_mps: number | null;
  previous_target_acceleration_mps2: number | null;
}
export interface MotionEnvelopeV2 {
  schema_version: "2";
  mode:
    | "pilot_cruise"
    | "controlled_stop"
    | "hold"
    | "emergency_stop"
    | "unknown_stop";
  target_speed_mps: number;
  target_acceleration_mps2: number;
  target_jerk_mps3: number;
  force_brake: boolean;
  qualified_prefix_m: number | null;
  positive_surface_distance_m: number | null;
  limiting_horizon_m: number | null;
  reserve_m: number;
  available_stopping_distance_m: number | null;
  current_stopping_distance_m: number | null;
  planned_stopping_distance_m: number | null;
  plan_elapsed_s: number | null;
  plan_remaining_distance_m: number | null;
  speed_tracking_error_mps: number | null;
  applied_dt_s: number | null;
  observed_speed_acceleration_mps2: number | null;
  allowed_positive_acceleration_mps2: number;
  acceleration_source: "speed_norm_finite_difference_or_initial_unknown";
  stopping_bound_model: "positive_accel_latency_jerk_release_then_zero_accel_s_curve/1";
  stop_plan: StopPlanV2 | null;
  reasons: string[];
  model_status: "assumed_uncalibrated";
}
export interface StackOutputV2 {
  kernel_version: "stack-kernel/2";
  sequence: number;
  at_ns: string;
  components: StackComponentMetadata[];
  input_points_sha256: string | null;
  observation: CorridorObservationV2;
  decision: MotionEnvelopeV2;
  control: StackControl;
  state_before: StackStateV2;
  state_after: StackStateV2;
  warnings: string[];
  elapsed_ms: number;
}
