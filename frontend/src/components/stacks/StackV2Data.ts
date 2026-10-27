import type { StackOutputV2, StopPlanV2 } from "./StackV2Types";
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown): value is number =>
  finite(value) && value >= 0;
const count = (value: unknown, max = 20000) =>
  Number.isSafeInteger(value) && nonnegative(value) && value <= max;
const optional = (value: unknown) => value === null || nonnegative(value);
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) &&
  value.length <= 256 &&
  value.every((item) => typeof item === "string" && item.length <= 8192);
const ns = (value: unknown) =>
  typeof value === "string" &&
  /^(0|[1-9]\d{0,19})$/.test(value) &&
  BigInt(value) <= 18446744073709551615n;
const xyz = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((item) => finite(item) && Math.abs(item) <= 1e6);
const bounds = (value: unknown): value is [number, number] =>
  Array.isArray(value) &&
  value.length === 2 &&
  value.every(finite) &&
  value[1] > value[0];
const same = (a: number, b: number) => Math.abs(a - b) <= 1e-7;
const samePlan = (a: StopPlanV2 | null, b: StopPlanV2 | null) =>
  a === null
    ? b === null
    : b !== null &&
      (Object.keys(a) as (keyof StopPlanV2)[]).every(
        (key) => a[key] === b[key],
      );
function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
function plan(value: StopPlanV2 | null) {
  if (value === null) return;
  requireValue(
    value?.schema_version === "symmetric-s-curve-stop/1" &&
      ns(value.started_at_ns) &&
      value.model_status === "assumed_uncalibrated" &&
      value.initial_acceleration_mps2 === 0 &&
      value.initial_acceleration_source === "model_assumption_not_measurement",
    "Unsupported kernel 2 stop plan.",
  );
  for (const field of [
    "initial_speed_mps",
    "jerk_limit_mps3",
    "deceleration_limit_mps2",
    "peak_deceleration_mps2",
    "latency_s",
    "ramp_s",
    "constant_deceleration_s",
    "duration_s",
    "distance_m",
  ] as const)
    requireValue(nonnegative(value[field]), "Invalid stop plan value.");
  requireValue(
    value.jerk_limit_mps3 > 0 &&
      value.deceleration_limit_mps2 > 0 &&
      Math.abs(
        value.duration_s -
          value.latency_s -
          2 * value.ramp_s -
          value.constant_deceleration_s,
      ) <= 1e-7,
    "Stop plan phase durations are inconsistent.",
  );
}
/** Validate stored v2 shape and geometry before rendering; never infer observations. */
export function validateStackOutputV2(value: unknown): StackOutputV2 {
  const output = value as StackOutputV2;
  requireValue(
    output?.kernel_version === "stack-kernel/2" &&
      count(output.sequence, Number.MAX_SAFE_INTEGER) &&
      ns(output.at_ns) &&
      nonnegative(output.elapsed_ms) &&
      strings(output.warnings) &&
      Array.isArray(output.components) &&
      output.components.length === 3 &&
      output.components.every(
        (item, index) =>
          item.role === ["perception", "policy", "controller"][index] &&
          item.implementation?.kernel === "stack-kernel/2",
      ) &&
      (output.input_points_sha256 === null ||
        /^[a-f0-9]{64}$/.test(output.input_points_sha256)),
    "Invalid kernel 2 output identity.",
  );
  const observation = output.observation;
  requireValue(
    observation?.schema_version === "2" &&
      typeof observation.input_valid === "boolean" &&
      ["invalid", "sampled"].includes(observation.status) &&
      strings(observation.reasons) &&
      [
        observation.input_points,
        observation.forward_points,
        observation.ground_points,
      ].every((value) => count(value)),
    "Invalid kernel 2 observation.",
  );
  const coverage = observation.coverage;
  requireValue(
    coverage &&
      ["invalid", "partial", "qualified"].includes(coverage.status) &&
      coverage.sample_model === "ground-hit-grid/1" &&
      coverage.free_space_certified === false &&
      nonnegative(coverage.ego_front_x_m) &&
      finite(coverage.lookahead_x_m) &&
      coverage.lookahead_x_m > coverage.ego_front_x_m &&
      finite(coverage.longitudinal_cell_m) &&
      coverage.longitudinal_cell_m > 0 &&
      finite(coverage.lateral_cell_m) &&
      coverage.lateral_cell_m > 0 &&
      count(coverage.min_ground_hits_per_cell) &&
      coverage.min_ground_hits_per_cell > 0 &&
      count(coverage.expected_cells, 256) &&
      count(coverage.qualified_cells, 256) &&
      optional(coverage.qualified_prefix_m) &&
      Array.isArray(coverage.cells) &&
      coverage.cells.length <= 256 &&
      coverage.cells.length === coverage.expected_cells &&
      Array.isArray(coverage.unknown_regions) &&
      coverage.unknown_regions.length <= 256,
    "Invalid bounded coverage grid.",
  );
  requireValue(
    coverage.longitudinal_cell_m === 1 &&
      coverage.lateral_cell_m === 0.8 &&
      coverage.min_ground_hits_per_cell === 2 &&
      coverage.expected_cells ===
        3 * Math.ceil(coverage.lookahead_x_m - coverage.ego_front_x_m),
    "Coverage denominator differs from its declared grid model.",
  );
  const identities = new Set<string>();
  for (const [index, cell] of coverage.cells.entries()) {
    const id = `${cell.longitudinal_index}/${cell.lateral_index}`;
    requireValue(
      count(cell.longitudinal_index, 255) &&
        count(cell.lateral_index, 2) &&
        !identities.has(id) &&
        bounds(cell.x_bounds_m) &&
        bounds(cell.y_bounds_m) &&
        count(cell.ground_hits) &&
        ["qualified", "unknown"].includes(cell.state) &&
        strings(cell.reasons),
      "Invalid coverage cell identity or bounds.",
    );
    identities.add(id);
    const row = Math.floor(index / 3),
      band = index % 3,
      lateral = [
        [-1.2, -0.4],
        [-0.4, 0.4],
        [0.4, 1.2],
      ][band];
    requireValue(
      cell.longitudinal_index === row &&
        cell.lateral_index === band &&
        same(cell.x_bounds_m[0], coverage.ego_front_x_m + row) &&
        same(
          cell.x_bounds_m[1],
          Math.min(coverage.lookahead_x_m, coverage.ego_front_x_m + row + 1),
        ) &&
        cell.y_bounds_m.every((value, axis) => same(value, lateral[axis])),
      "Grid cell geometry differs from its stored index.",
    );
    requireValue(
      cell.state ===
        (cell.ground_hits >= coverage.min_ground_hits_per_cell &&
        coverage.status !== "invalid"
          ? "qualified"
          : "unknown") &&
        (cell.state === "qualified"
          ? cell.reasons.length === 0
          : cell.reasons.length > 0),
      "Cell qualification or reasons differ from its declared hit count.",
    );
    if (cell.ground_hits === 0)
      requireValue(
        cell.point_min_xyz_m === null && cell.point_max_xyz_m === null,
        "Empty cells cannot contain point extents.",
      );
    else {
      requireValue(
        xyz(cell.point_min_xyz_m) && xyz(cell.point_max_xyz_m),
        "Cell hit extents are unavailable.",
      );
      requireValue(
        cell.point_min_xyz_m.every(
          (item, index) => item <= cell.point_max_xyz_m![index],
        ),
        "Cell hit extents are reversed.",
      );
      requireValue(
        [cell.x_bounds_m, cell.y_bounds_m].every(
          (bound, axis) =>
            cell.point_min_xyz_m![axis] >= bound[0] &&
            cell.point_max_xyz_m![axis] <= bound[1],
        ),
        "Actual point extents lie outside their cell.",
      );
    }
    if (cell.state === "unknown" && coverage.qualified_prefix_m !== null)
      requireValue(
        coverage.qualified_prefix_m <=
          cell.x_bounds_m[0] - coverage.ego_front_x_m + 1e-7,
        "Qualified prefix crosses an unknown cell.",
      );
  }
  requireValue(
    coverage.qualified_cells ===
      coverage.cells.filter((cell) => cell.state === "qualified").length,
    "Qualified cell denominator differs from retained cells.",
  );
  for (const region of coverage.unknown_regions)
    requireValue(
      bounds(region.x_bounds_m) &&
        bounds(region.y_bounds_m) &&
        strings(region.reasons),
      "Invalid unknown-region geometry.",
    );
  const unknownCells = coverage.cells.filter(
    (cell) => cell.state === "unknown",
  );
  requireValue(
    coverage.unknown_regions.length === unknownCells.length &&
      coverage.unknown_regions.every(
        (region, index) =>
          JSON.stringify(region.x_bounds_m) ===
            JSON.stringify(unknownCells[index].x_bounds_m) &&
          JSON.stringify(region.y_bounds_m) ===
            JSON.stringify(unknownCells[index].y_bounds_m) &&
          JSON.stringify(region.reasons) ===
            JSON.stringify(unknownCells[index].reasons),
      ),
    "Unknown regions differ from retained cells.",
  );
  requireValue(
    observation.ground_points <= observation.forward_points &&
      observation.forward_points <= observation.input_points &&
      coverage.cells.reduce((total, cell) => total + cell.ground_hits, 0) ===
        observation.ground_points,
    "Observation hit counts differ from the grid denominator.",
  );
  if (observation.input_valid) {
    const expectedPrefix =
      (unknownCells[0]?.x_bounds_m[0] ?? coverage.lookahead_x_m) -
      coverage.ego_front_x_m;
    requireValue(
      observation.status === "sampled" &&
        coverage.status === (unknownCells.length ? "partial" : "qualified") &&
        coverage.qualified_prefix_m !== null &&
        same(coverage.qualified_prefix_m, expectedPrefix),
      "Sample status or contiguous prefix differs from retained cells.",
    );
  }
  const surface = observation.positive_surface;
  if (surface !== null)
    requireValue(
      observation.input_valid &&
        surface?.meaning ===
          "retained_height-band_surface_not_object_identity" &&
        nonnegative(surface.distance_from_bumper_m) &&
        xyz(surface.nearest_point_xyz_m) &&
        count(surface.observed_points) &&
        surface.observed_points > 0 &&
        surface.observed_points + observation.ground_points <=
          observation.forward_points &&
        surface.nearest_point_xyz_m[0] >= 0 &&
        surface.nearest_point_xyz_m[0] <= coverage.lookahead_x_m &&
        Math.abs(surface.nearest_point_xyz_m[1]) <= 1.2 &&
        same(
          surface.distance_from_bumper_m,
          Math.max(0, surface.nearest_point_xyz_m[0] - coverage.ego_front_x_m),
        ),
      "Invalid positive surface evidence.",
    );
  if (!observation.input_valid)
    requireValue(
      observation.status === "invalid" &&
        coverage.status === "invalid" &&
        coverage.qualified_prefix_m === null &&
        coverage.qualified_cells === 0 &&
        surface === null &&
        observation.forward_points === 0 &&
        observation.ground_points === 0,
      "Invalid inputs cannot qualify coverage or positive surfaces.",
    );
  const decision = output.decision;
  requireValue(
    decision?.schema_version === "2" &&
      decision.model_status === "assumed_uncalibrated" &&
      [
        "pilot_cruise",
        "controlled_stop",
        "hold",
        "emergency_stop",
        "unknown_stop",
      ].includes(decision.mode) &&
      nonnegative(decision.target_speed_mps) &&
      decision.target_speed_mps <= 20 &&
      finite(decision.target_acceleration_mps2) &&
      finite(decision.target_jerk_mps3) &&
      typeof decision.force_brake === "boolean" &&
      nonnegative(decision.reserve_m) &&
      strings(decision.reasons),
    "Invalid kernel 2 motion envelope.",
  );
  for (const field of [
    "qualified_prefix_m",
    "positive_surface_distance_m",
    "limiting_horizon_m",
    "available_stopping_distance_m",
    "current_stopping_distance_m",
    "planned_stopping_distance_m",
    "plan_elapsed_s",
    "plan_remaining_distance_m",
  ] as const)
    requireValue(
      optional(decision[field]),
      "Invalid optional stopping envelope value.",
    );
  requireValue(
    decision.speed_tracking_error_mps === null ||
      finite(decision.speed_tracking_error_mps),
    "Invalid speed tracking diagnostic.",
  );
  requireValue(
    optional(decision.applied_dt_s) &&
      (decision.observed_speed_acceleration_mps2 === null ||
        finite(decision.observed_speed_acceleration_mps2)) &&
      nonnegative(decision.allowed_positive_acceleration_mps2) &&
      decision.acceleration_source ===
        "speed_norm_finite_difference_or_initial_unknown" &&
      decision.stopping_bound_model ===
        "positive_accel_latency_jerk_release_then_zero_accel_s_curve/1",
    "Invalid acceleration observation or model assumption.",
  );
  plan(decision.stop_plan);
  for (const state of [output.state_before, output.state_after]) {
    requireValue(
      state &&
        [
          "ready",
          "controlled_stop",
          "hold",
          "emergency_stop",
          "unknown_stop",
        ].includes(state.mode) &&
        typeof state.latched === "boolean" &&
        (state.latch_reason === null ||
          typeof state.latch_reason === "string") &&
        (state.last_sequence === null ||
          count(state.last_sequence, Number.MAX_SAFE_INTEGER)) &&
        (state.last_at_ns === null || ns(state.last_at_ns)) &&
        optional(state.previous_speed_mps) &&
        (state.previous_target_acceleration_mps2 === null ||
          finite(state.previous_target_acceleration_mps2)) &&
        state.reset_policy === "new_session_only",
      "Invalid saved plan state.",
    );
    plan(state.stop_plan);
    requireValue(
      state.latched === (state.mode !== "ready") &&
        state.latched === (state.latch_reason !== null) &&
        (state.last_sequence === null) === (state.last_at_ns === null) &&
        (state.mode !== "ready" || state.stop_plan === null) &&
        (state.mode !== "controlled_stop" || state.stop_plan !== null),
      "Saved latch or plan contradicts its state.",
    );
  }
  requireValue(
    output.state_after.last_sequence === output.sequence &&
      output.state_after.last_at_ns === output.at_ns &&
      decision.qualified_prefix_m === coverage.qualified_prefix_m &&
      decision.positive_surface_distance_m ===
        (surface?.distance_from_bumper_m ?? null),
    "Saved state or stopping horizon differs from its source output.",
  );
  const before = output.state_before,
    after = output.state_after;
  const dt =
    before.last_at_ns === null
      ? null
      : Number(BigInt(output.at_ns) - BigInt(before.last_at_ns)) / 1e9;
  requireValue(
    dt === null
      ? output.sequence === 0 &&
          before.mode === "ready" &&
          before.previous_speed_mps === null &&
          before.previous_target_acceleration_mps2 === null
      : dt > 0 && output.sequence === before.last_sequence! + 1,
    "State history cannot begin or resume at an arbitrary sequence.",
  );
  requireValue(
    decision.applied_dt_s === dt &&
      after.previous_target_acceleration_mps2 ===
        decision.target_acceleration_mps2 &&
      after.mode ===
        (decision.mode === "pilot_cruise" ? "ready" : decision.mode) &&
      (!before.latched || after.latched) &&
      samePlan(after.stop_plan, decision.stop_plan) &&
      (before.stop_plan === null ||
        samePlan(before.stop_plan, after.stop_plan)),
    "Saved plan state was reset or changed inconsistently.",
  );
  if (before.stop_plan === null && after.stop_plan !== null)
    requireValue(
      after.stop_plan.started_at_ns === output.at_ns &&
        after.stop_plan.initial_speed_mps === after.previous_speed_mps,
      "A new stop plan must begin at the current source speed and time.",
    );
  if (decision.stop_plan !== null && decision.plan_elapsed_s !== null)
    requireValue(
      same(
        decision.plan_elapsed_s,
        Number(
          BigInt(output.at_ns) - BigInt(decision.stop_plan.started_at_ns),
        ) / 1e9,
      ),
      "Plan elapsed time differs from the selected source timestamp.",
    );
  const observedAcceleration =
    dt === null ||
    before.previous_speed_mps === null ||
    after.previous_speed_mps === null
      ? null
      : (after.previous_speed_mps - before.previous_speed_mps) / dt;
  requireValue(
    observedAcceleration === null
      ? decision.observed_speed_acceleration_mps2 === null
      : decision.observed_speed_acceleration_mps2 !== null &&
          same(observedAcceleration, decision.observed_speed_acceleration_mps2),
    "Observed acceleration differs from the retained speed history.",
  );
  const control = output.control;
  requireValue(
    control &&
      [control.throttle, control.brake].every(
        (value) => nonnegative(value) && value <= 1,
      ) &&
      finite(control.steer) &&
      Math.abs(control.steer) <= 1 &&
      control.reverse === false &&
      control.hand_brake === false &&
      control.steering_convention === "positive_left" &&
      !(control.throttle > 0 && control.brake > 0),
    "Invalid kernel 2 actuator request.",
  );
  requireValue(
    decision.force_brake ===
      ["hold", "unknown_stop", "emergency_stop"].includes(decision.mode) &&
      (!decision.force_brake ||
        (control.throttle === 0 &&
          control.brake === 1 &&
          decision.target_speed_mps === 0 &&
          decision.target_acceleration_mps2 === 0 &&
          decision.target_jerk_mps3 === 0)),
    "Force-brake mode contradicts its final request.",
  );
  return output;
}
