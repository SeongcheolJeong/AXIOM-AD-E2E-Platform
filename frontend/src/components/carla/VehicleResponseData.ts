import type { RunDetail } from "../../types";
import {
  liveArtifact,
  readLiveArtifact,
  validateLiveManifest,
  validateLiveTrace,
} from "./CarlaLiveData";
import type { CarlaLiveManifest, CarlaLiveTrace } from "./CarlaTypes";
import type {
  DiagnosticValue,
  ResponseObservation,
  RpcBracket,
  Vector3,
  VehicleResponse,
  WheelFields,
} from "./VehicleResponseTypes";
import { validateStartupReport } from "../CarlaStartupInspector";
import { runExecutionId } from "../jobs/RunEvidenceIdentity";

const roles = ["ego", "lead"] as const;
const telemetryFields = ["omega", "long_slip", "lat_slip", "tire_friction"];
const scalarFields = [
  "mass",
  "drag_coefficient",
  "max_rpm",
  "moi",
  "clutch_strength",
  "gear_switch_time",
  "final_ratio",
  "damping_rate_full_throttle",
  "damping_rate_zero_throttle_clutch_engaged",
  "damping_rate_zero_throttle_clutch_disengaged",
  "use_gear_autobox",
  "use_sweep_wheel_collision",
];
const wheelFields = [
  "tire_friction",
  "damping_rate",
  "max_steer_angle",
  "radius",
  "max_brake_torque",
  "max_handbrake_torque",
  "lat_stiff_max_load",
  "lat_stiff_value",
  "long_stiff_value",
];
function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
function object(value: unknown): Record<string, unknown> {
  requireValue(
    value && typeof value === "object" && !Array.isArray(value),
    "Expected a response object.",
  );
  return value as Record<string, unknown>;
}
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const integer = (
  value: unknown,
  minimum = 0,
  maximum = 4294967295,
): value is number =>
  Number.isSafeInteger(value) &&
  (value as number) >= minimum &&
  (value as number) <= maximum;
const text = (value: unknown, max = 4000): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= max;
const keysEqual = (value: unknown, keys: readonly string[]) =>
  Object.keys(object(value)).sort().join("|") === [...keys].sort().join("|");
function finiteTree(value: unknown, depth = 0, budget = { left: 100000 }) {
  requireValue(
    depth <= 20 && --budget.left >= 0,
    "Response JSON exceeds the structure bound.",
  );
  if (typeof value === "number")
    requireValue(finite(value), "Nonfinite JSON value.");
  if (typeof value === "string")
    requireValue(value.length <= 8000, "Response string exceeds its bound.");
  if (value && typeof value === "object")
    Object.values(value).forEach((item) => finiteTree(item, depth + 1, budget));
}
function vector(value: Vector3) {
  requireValue(
    keysEqual(value, ["x", "y", "z"]) && Object.values(value).every(finite),
    "Invalid native vector.",
  );
}
function bracket(value: RpcBracket, frame: number) {
  object(value);
  requireValue(
    ["available", "unavailable"].includes(value.status),
    "Invalid RPC status.",
  );
  for (const f of [value.before_world_frame, value.after_world_frame])
    requireValue(f === null || integer(f), "Invalid RPC frame.");
  if (value.status === "available")
    requireValue(
      value.before_world_frame === frame &&
        value.after_world_frame === frame &&
        value.error === null,
      "RPC data does not belong to this snapshot frame.",
    );
  else
    requireValue(text(value.error, 300), "Unavailable RPC lacks its reason.");
}
function diagnostic(value: DiagnosticValue, allowBoolean = false) {
  object(value);
  requireValue(
    ["available", "unavailable", "null", "nonfinite"].includes(value.status),
    "Invalid diagnostic status.",
  );
  if (value.status === "available")
    requireValue(
      (finite(value.value) ||
        (allowBoolean && typeof value.value === "boolean")) &&
        value.reason === null,
      "Available diagnostic lacks a finite value.",
    );
  else
    requireValue(
      value.value === null && text(value.reason, 300),
      "Missing/nonfinite diagnostic lacks a reason.",
    );
}
function wheels(value: WheelFields[], fields: string[]) {
  requireValue(
    Array.isArray(value) && value.length === 4,
    "Four ordered wheel records are required.",
  );
  value.forEach((wheel, index) => {
    requireValue(
      wheel.index === index && keysEqual(wheel.fields, fields),
      "Wheel field identity differs.",
    );
    Object.values(wheel.fields).forEach((item) => diagnostic(item));
  });
}
export const normSpeed = (velocity: Vector3) =>
  Math.hypot(velocity.x, velocity.y, velocity.z);
export const headingProjection = (vector: Vector3, yaw: number) =>
  vector.x * Math.cos((yaw * Math.PI) / 180) +
  vector.y * Math.sin((yaw * Math.PI) / 180);
const close = (a: number, b: number, tolerance = 2e-6) =>
  Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tolerance;

function observation(row: ResponseObservation, rest: ResponseObservation) {
  requireValue(
    integer(row.world_frame) &&
      finite(row.source_time_s) &&
      row.source_time_s >= 0 &&
      finite(row.delta_seconds) &&
      close(row.delta_seconds, 0.05),
    "Invalid response frame or clock.",
  );
  requireValue(keysEqual(row.actors, roles), "Response actor roles differ.");
  for (const role of roles) {
    const actor = row.actors[role];
    requireValue(
      integer(actor?.actor_id, 1) &&
        actor.actor_id === rest.actors[role].actor_id,
      "Response actor identity differs.",
    );
    for (const value of [
      actor.position_m,
      actor.forward_vector,
      actor.velocity_mps,
      actor.acceleration_mps2,
      actor.angular_velocity_deg_s,
    ])
      vector(value);
    requireValue(
      keysEqual(actor.rotation_deg, ["pitch", "yaw", "roll"]) &&
        Object.values(actor.rotation_deg).every(finite),
      "Invalid native rotation.",
    );
    const pitch = (actor.rotation_deg.pitch * Math.PI) / 180,
      yaw = (actor.rotation_deg.yaw * Math.PI) / 180;
    const forward = [
      Math.cos(pitch) * Math.cos(yaw),
      Math.cos(pitch) * Math.sin(yaw),
      Math.sin(pitch),
    ];
    requireValue(
      [
        actor.forward_vector.x,
        actor.forward_vector.y,
        actor.forward_vector.z,
      ].every((v, i) => Math.abs(v - forward[i]) <= 2e-6),
      "Forward vector differs from the native pose.",
    );
    bracket(actor.reported_control, row.world_frame);
    const control = actor.reported_control.data;
    if (actor.reported_control.status === "available")
      requireValue(
        control &&
          finite(control.throttle) &&
          control.throttle >= 0 &&
          control.throttle <= 1 &&
          finite(control.brake) &&
          control.brake >= 0 &&
          control.brake <= 1 &&
          finite(control.steer) &&
          Math.abs(control.steer) <= 1 &&
          integer(control.gear, -100, 100) &&
          [
            control.reverse,
            control.hand_brake,
            control.manual_gear_shift,
          ].every((v) => typeof v === "boolean"),
        "Invalid reported control.",
      );
    else requireValue(control === null, "Unavailable control contains data.");
    bracket(actor.telemetry, row.world_frame);
    requireValue(
      actor.telemetry.source_api === "vehicle.get_telemetry_data" &&
        actor.telemetry.interpretation === "raw_api_fields_units_unverified",
      "Unsupported telemetry interpretation.",
    );
    if (actor.telemetry.status === "available") {
      wheels(actor.telemetry.wheels, telemetryFields);
      requireValue(
        keysEqual(actor.telemetry.scalars, ["speed", "engine_rpm", "gear"]),
        "Telemetry scalar fields differ.",
      );
      Object.values(actor.telemetry.scalars).forEach((item) =>
        diagnostic(item),
      );
    } else
      requireValue(
        Array.isArray(actor.telemetry.wheels) &&
          actor.telemetry.wheels.length === 0 &&
          keysEqual(actor.telemetry.scalars, []),
        "Unavailable telemetry contains values.",
      );
  }
}

export function validateVehicleResponse(
  value: unknown,
  run: RunDetail,
  trace: CarlaLiveTrace,
  manifest: CarlaLiveManifest,
): VehicleResponse {
  finiteTree(value);
  const report = value as VehicleResponse;
  requireValue(
    report?.schema_version === "carla-vehicle-response/1" &&
      report.execution_id === runExecutionId(run) &&
      report.input_hash === run.provenance.input_hash &&
      report.context_hash === run.execution_context?.context_hash &&
      report.worker_adapter_version === run.provenance.adapter_version &&
      ["0.4.1", "0.5.0", "0.6.0", "0.7.0", "0.8.0"].includes(
        report.worker_adapter_version,
      ) &&
      report.server_version === manifest.server_version,
    "Response schema or execution context differs from the selected run.",
  );
  requireValue(
    report.coordinate_convention === "carla_left_handed_world" &&
      report.angular_velocity_basis ===
        "official_carla_0.9.16_degrees_per_second_installed_build_separate",
    "Unsupported native coordinate or angular-velocity interpretation.",
  );
  requireValue(
    report.status === trace.status &&
      report.expected_observations === trace.expected_intervals + 1 &&
      Array.isArray(report.observations) &&
      report.observations.length <= report.expected_observations &&
      report.observations.length <= manifest.observed_frames,
    "Response observation denominator differs from live physics.",
  );
  requireValue(
    report.error === null || text(report.error, 1000),
    "Invalid response error.",
  );
  requireValue(
    Array.isArray(report.limitations) &&
      report.limitations.length <= 16 &&
      report.limitations.every((item) => text(item)),
    "Invalid response limitations.",
  );
  requireValue(
    Array.isArray(report.initial_velocity_requests) &&
      report.initial_velocity_requests.length <= 2,
    "Invalid initial velocity requests.",
  );
  const configs = object(report.physics_configuration);
  requireValue(
    Object.keys(configs).every((role) => role === "ego" || role === "lead"),
    "Unexpected physics role.",
  );
  if (report.status === "completed")
    requireValue(
      report.observations.length === report.expected_observations &&
        report.error === null,
      "Complete response has missing observations.",
    );
  if (report.after_rest === null) {
    requireValue(
      report.status === "partial" &&
        report.observations.length === 0 &&
        report.initial_velocity_requests.length === 0 &&
        Object.keys(configs).length === 0,
      "Motion evidence lacks a rest boundary.",
    );
    return report;
  }
  const rest = report.after_rest;
  requireValue(
    rest.actors?.ego?.actor_id !== rest.actors?.lead?.actor_id &&
      rest.actors?.ego?.actor_id === manifest.ego_actor_id,
    "Rest boundary actor identities differ.",
  );
  observation(rest, rest);
  for (const role of roles) {
    const config = report.physics_configuration[role];
    if (!config) continue;
    bracket(config, rest.world_frame);
    requireValue(
      config.actor_id === rest.actors[role].actor_id &&
        config.requested_tire_friction ===
          run.scenario_snapshot.parameters.friction &&
        config.requested_tire_friction_float32 ===
          Math.fround(config.requested_tire_friction),
      "Physics request differs from the pinned scenario.",
    );
    if (config.status === "available") {
      requireValue(
        keysEqual(config.scalars, scalarFields),
        "Physics scalar fields differ.",
      );
      Object.entries(config.scalars).forEach(([field, item]) =>
        diagnostic(
          item,
          field === "use_gear_autobox" || field === "use_sweep_wheel_collision",
        ),
      );
      wheels(config.wheels, wheelFields);
      const matched = config.wheels.every(
        (wheel) =>
          wheel.fields.tire_friction.status === "available" &&
          wheel.fields.tire_friction.value ===
            config.requested_tire_friction_float32,
      );
      requireValue(
        config.friction_status === (matched ? "matched" : "mismatch"),
        "Friction gate contradicts per-wheel values.",
      );
    } else
      requireValue(
        config.friction_status === "unavailable",
        "Unavailable physics is labelled verified.",
      );
  }
  if (report.observations.length || report.initial_velocity_requests.length)
    requireValue(
      roles.every(
        (role) =>
          report.physics_configuration[role]?.friction_status === "matched",
      ),
      "Motion follows an unverified physics configuration.",
    );
  requireValue(
    new Set(report.initial_velocity_requests.map((item) => item.role)).size ===
      report.initial_velocity_requests.length,
    "Duplicate velocity request role.",
  );
  for (const request of report.initial_velocity_requests) {
    requireValue(
      roles.includes(request.role),
      "Unknown velocity request role.",
    );
    const actor = rest.actors[request.role],
      speed =
        request.role === "ego"
          ? run.scenario_snapshot.parameters.ego_speed_mps
          : run.scenario_snapshot.parameters.target_speed_mps;
    vector(request.velocity_mps);
    requireValue(
      request.actor_id === actor.actor_id &&
        request.after_world_frame === rest.world_frame &&
        request.requested_speed_mps === speed &&
        request.ack_status === "unknown" &&
        request.operation === "actor.set_target_velocity" &&
        (["x", "y", "z"] as const).every(
          (axis) =>
            request.velocity_mps[axis] ===
            Math.fround(actor.forward_vector[axis] * speed),
        ),
      "Initial velocity request differs from the rest frame/scenario.",
    );
  }
  if (report.observations.length)
    requireValue(
      report.initial_velocity_requests.length === 2,
      "Measured motion lacks both initial requests.",
    );
  report.observations.forEach((row, index) => {
    observation(row, rest);
    requireValue(
      row.world_frame === rest.world_frame + index + 1 &&
        close(row.source_time_s - rest.source_time_s, (index + 1) * 0.05),
      "Observation frame/time is not a consecutive measured prefix.",
    );
    const outgoing = trace.ticks.find(
      (tick) => tick.source_world_frame === row.world_frame,
    );
    const incoming = trace.ticks.find(
      (tick) => tick.next_world_frame === row.world_frame,
    );
    if (outgoing)
      requireValue(
        close(row.source_time_s, outgoing.source_time_s!) &&
          (outgoing.packet?.ego === null ||
            outgoing.packet?.ego === undefined ||
            close(
              normSpeed(row.actors.ego.velocity_mps),
              outgoing.packet.ego.speed_mps,
            )),
        "Source observation differs from the live packet.",
      );
    if (incoming) {
      const ego = row.actors.ego,
        stored = incoming.next_ego!;
      requireValue(
        close(row.source_time_s, incoming.next_time_s!) &&
          close(normSpeed(ego.velocity_mps), stored.speed_mps) &&
          close(ego.position_m.x, stored.x) &&
          close(-ego.position_m.y, stored.y),
        "Native response differs from the next physics observation.",
      );
      const readback = ego.reported_control.data;
      if (readback && incoming.observed_control)
        requireValue(
          (
            ["throttle", "brake", "steer", "reverse", "hand_brake"] as const
          ).every(
            (field) => readback[field] === incoming.observed_control![field],
          ),
          "Response control contradicts the live trace readback at the same frame.",
        );
    }
  });
  return report;
}

export async function loadVehicleResponse(run: RunDetail, signal: AbortSignal) {
  const artifact = liveArtifact(
    run,
    "carla-vehicle-response.json",
    "vehicle_response",
    2 * 1024 * 1024,
  );
  requireValue(
    artifact.media_type === "application/json",
    "Response artifact is not JSON.",
  );
  const traceArtifact = liveArtifact(
    run,
    "carla-stack-trace.json",
    "live_stack_trace",
    20 * 1024 * 1024,
  );
  const manifestArtifact = liveArtifact(
    run,
    "carla-stack-manifest.json",
    "live_stack_manifest",
    4 * 1024 * 1024,
  );
  const physicsArtifact = liveArtifact(
    run,
    "carla-stack-physics.json",
    "physics_trace",
    2 * 1024 * 1024,
  );
  const [bytes, traceBytes, manifestBytes, physicsBytes] = await Promise.all([
    readLiveArtifact(artifact, signal),
    readLiveArtifact(traceArtifact, signal),
    readLiveArtifact(manifestArtifact, signal),
    readLiveArtifact(physicsArtifact, signal),
  ]);
  const decode = (data: Uint8Array) =>
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data));
  const trace = validateLiveTrace(decode(traceBytes), run);
  const manifest = validateLiveManifest(decode(manifestBytes), run, trace);
  requireValue(
    manifest.artifacts.some(
      (item) =>
        item.name === artifact.name &&
        item.sha256 === artifact.sha256 &&
        item.size_bytes === artifact.size_bytes &&
        item.kind === artifact.kind,
    ),
    "Live manifest does not include this response artifact.",
  );
  const report = validateVehicleResponse(decode(bytes), run, trace, manifest);
  const physics = object(decode(physicsBytes));
  finiteTree(physics);
  requireValue(
    physics.schema_version === "carla-live-stack-physics/1" &&
      physics.execution_id === runExecutionId(run) &&
      physics.input_hash === report.input_hash &&
      physics.context_hash === report.context_hash &&
      physics.status === report.status &&
      Array.isArray(physics.observations) &&
      physics.observations.length === manifest.observed_frames,
    "Physics artifact context or coverage differs.",
  );
  report.observations.forEach((row, index) => {
    const source = object((physics.observations as unknown[])[index]),
      frame = object(source.frame);
    requireValue(
      source.world_frame === row.world_frame &&
        source.source_time_s === row.source_time_s,
      "Response clock differs from the original physics row.",
    );
    requireValue(
      Array.isArray(frame.actors) && frame.actors.length >= 1,
      "Physics lead state is missing.",
    );
    for (const role of roles) {
      const actor = row.actors[role],
        stored = object(
          role === "ego" ? frame.ego : (frame.actors as unknown[])[0],
        );
      requireValue(
        finite(stored.speed_mps) &&
          close(normSpeed(actor.velocity_mps), stored.speed_mps) &&
          stored.x === actor.position_m.x &&
          stored.y === -actor.position_m.y &&
          finite(stored.yaw) &&
          Math.abs(
            Math.atan2(
              Math.sin(stored.yaw + (actor.rotation_deg.yaw * Math.PI) / 180),
              Math.cos(stored.yaw + (actor.rotation_deg.yaw * Math.PI) / 180),
            ),
          ) <= 2e-6,
        "Native response differs from the original reflected physics state.",
      );
    }
  });
  if (report.after_rest) {
    const startupArtifact = liveArtifact(
      run,
      "carla-startup.json",
      "startup_manifest",
      2 * 1024 * 1024,
    );
    const startupRaw = decode(await readLiveArtifact(startupArtifact, signal));
    const startup = validateStartupReport(startupRaw, run),
      last = startup.trace.at(-1);
    requireValue(
      startup.status === "passed" &&
        last?.world_frame === report.after_rest.world_frame &&
        last.elapsed_seconds === report.after_rest.source_time_s,
      "Rest boundary differs from the verified startup evidence.",
    );
    const rawLast = object((object(startupRaw).trace as unknown[]).at(-1)),
      actors = object(rawLast.actors);
    for (const role of roles) {
      const original = object(actors[role]),
        actor = report.after_rest.actors[role];
      requireValue(
        original.actor_id === actor.actor_id,
        "Rest actor differs from startup.",
      );
      for (const field of [
        "position_m",
        "rotation_deg",
        "velocity_mps",
        "acceleration_mps2",
        "angular_velocity_deg_s",
      ] as const) {
        const raw = object(original[field]);
        requireValue(
          Object.entries(actor[field]).every(
            ([axis, value]) => raw[axis] === value,
          ),
          "Rest snapshot differs from startup raw vectors.",
        );
      }
    }
  }
  return { report, trace, bytes, artifact };
}
