import type { Artifact, RunDetail } from "../../types";
import { authHeaders } from "../../api";
import { verifyBuffer } from "../sensors/SensorData";
import { runExecutionId } from "../jobs/RunEvidenceIdentity";
import { stackAvailable } from "../stacks/StackKernels";
import {
  readSensorCapabilities,
  validConfiguration,
} from "./SensorConfigurationData";
import { validateStackOutputV2 } from "../stacks/StackV2Data";
import type {
  CarlaLiveTrace,
  CarlaVehicleControl,
  PreparedSimulationContext,
} from "./CarlaTypes";

const SHA = /^[a-f0-9]{64}$/;
const ns = (value: unknown): value is string =>
  typeof value === "string" &&
  /^(0|[1-9]\d{0,19})$/.test(value) &&
  BigInt(value) <= 18446744073709551615n;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const integer = (
  value: unknown,
  max = Number.MAX_SAFE_INTEGER,
): value is number =>
  Number.isSafeInteger(value) &&
  (value as number) >= 0 &&
  (value as number) <= max;
function requireEvidence(
  condition: unknown,
  reason: string,
): asserts condition {
  if (!condition) throw new Error(reason);
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
/** Bound parsed structures before they reach charts and nested controls. */
function finiteTree(value: unknown, depth = 0, budget = { remaining: 250000 }) {
  requireEvidence(
    depth <= 32 && --budget.remaining >= 0,
    "Trace structure exceeds the inspection bound.",
  );
  if (typeof value === "number")
    requireEvidence(
      Number.isFinite(value),
      "Trace contains a nonfinite number.",
    );
  if (typeof value === "string")
    requireEvidence(
      value.length <= 32768,
      "Trace text exceeds the inspection bound.",
    );
  if (value && typeof value === "object")
    for (const item of Object.values(value))
      finiteTree(item, depth + 1, budget);
}
export function validateLiveContext(context: PreparedSimulationContext) {
  requireEvidence(
    ["simulation-control-context/1", "simulation-control-context/2"].includes(
      context?.schema_version,
    ) &&
      context.mode === "stack" &&
      SHA.test(context.context_hash),
    "Unsupported or missing saved execution context.",
  );
  const stack = context.stack_snapshot,
    rig = context.rig_snapshot,
    profile = context.sensor_profile;
  requireEvidence(
    stack?.id &&
      integer(stack.revision) &&
      stack.revision > 0 &&
      SHA.test(stack.manifest_hash) &&
      stack.definition?.reference_frame === "ego",
    "Saved stack identity or frame is invalid.",
  );
  requireEvidence(
    Array.isArray(stack.component_metadata) &&
      stackAvailable(stack, stack.component_metadata),
    "Saved stack kernel and component versions are incompatible.",
  );
  requireEvidence(
    rig?.id &&
      integer(rig.revision) &&
      rig.revision > 0 &&
      SHA.test(rig.manifest_hash) &&
      rig.reference_frame === "ego" &&
      rig.coordinate_convention === "right_handed_z_up",
    "Saved rig identity or frame is invalid.",
  );
  requireEvidence(
    (context.schema_version === "simulation-control-context/2"
      ? profile?.id === "carla-raycast-lidar/1"
      : [
          "carla-lidar-ego/1",
          "carla-lidar-ego-dense/1",
          "carla-lidar-ego-near/1",
        ].includes(profile?.id)) &&
      profile.version === "1" &&
      (context.schema_version === "simulation-control-context/2"
        ? typeof profile.sensor_frame === "string" &&
          profile.sensor_frame.length > 0
        : profile.sensor_frame === "lidar_roof") &&
      profile.reference_frame === "ego" &&
      SHA.test(profile.profile_hash) &&
      profile.coordinate_policy ===
        "carla_xyzi_y_reflection_then_declared_sensor_to_ego/1" &&
      profile.clock_policy ===
        "carla_simulation_seconds_round_half_even_ns/1" &&
      (context.schema_version === "simulation-control-context/2"
        ? profile.max_points_per_frame === 20000
        : profile.max_points === 20000) &&
      context.ego_source_policy === "carla-ego-state-and-map-route/1",
    "Unsupported saved sensor or ego profile.",
  );
  const sensor = rig.sensors.find(
    (item) => item.key === context.lidar_sensor_key,
  );
  requireEvidence(
    sensor?.kind === "lidar" &&
      sensor.frame_id === profile.sensor_frame &&
      stable(sensor.transform) === stable(profile.mount) &&
      (context.schema_version === "simulation-control-context/2" ||
        (stable(profile.mount.translation_m) === "[0,0,2.2]" &&
          stable(profile.mount.rotation_xyzw) === "[0,0,0,1]")),
    "Saved LiDAR mounting differs from the fixed profile.",
  );
  if (context.schema_version === "simulation-control-context/2") {
    const configuration = context.sensor_configuration_snapshot;
    const capability = readSensorCapabilities([
      context.sensor_capability_snapshot,
    ])[0];
    const t = profile.mount.translation_m,
      q = profile.mount.rotation_xyzw;
    requireEvidence(
      validConfiguration(configuration) &&
        configuration.capability_schema_hash === capability.schema_hash &&
        configuration.capability_id === capability.id &&
        configuration.capability_version === capability.version &&
        capability.supported_kernel_versions.includes(stack.kernel_version) &&
        context.physical_mount_policy ===
          "carla_rh_rig_to_lh_native_matrix/1" &&
        context.source_serialization_policy === "carla_configured_lidar_json/1",
      "Saved sensor configuration/capability identity differs.",
    );
    requireEvidence(
      t.length === 3 &&
        t.every(finite) &&
        Math.abs(t[0]) <= 5 &&
        Math.abs(t[1]) <= 5 &&
        t[2] >= 0 &&
        t[2] <= 5 &&
        q.length === 4 &&
        q.every(finite) &&
        Math.abs(q.reduce((sum, n) => sum + n * n, 0) - 1) <= 1e-6,
      "Saved physical mount exceeds the declared capability.",
    );
    requireEvidence(
      Object.keys(profile.attributes).length === 14 &&
        Object.entries(configuration.parameters).every(
          ([name, n]) =>
            name in profile.attributes &&
            Number(profile.attributes[name]) === n,
        ) &&
        /^(0|[1-9]\d*)$/.test(profile.attributes.noise_seed) &&
        Number(profile.attributes.noise_seed) <= 2147483647,
      "Saved effective sensor attributes differ from the configuration.",
    );
  }
}
function control(value: CarlaVehicleControl | null) {
  if (value === null) return;
  requireEvidence(
    value &&
      finite(value.throttle) &&
      value.throttle >= 0 &&
      value.throttle <= 1 &&
      finite(value.brake) &&
      value.brake >= 0 &&
      value.brake <= 1 &&
      finite(value.steer) &&
      Math.abs(value.steer) <= 1 &&
      typeof value.reverse === "boolean" &&
      typeof value.hand_brake === "boolean",
    "Invalid CARLA control values.",
  );
}
export function validateLiveTrace(
  value: unknown,
  run: RunDetail,
): CarlaLiveTrace {
  finiteTree(value, 0, {
    remaining:
      run.execution_context?.stack_snapshot.kernel_version === "stack-kernel/2"
        ? 1500000
        : 250000,
  });
  requireEvidence(
    run.adapter_id === "carla" && run.execution_context,
    "The run has no saved CARLA stack context.",
  );
  validateLiveContext(run.execution_context);
  requireEvidence(
    run.execution_context.sensor_profile.id !== "carla-lidar-ego-dense/1" ||
      ["0.6.0", "0.7.0", "0.8.0"].includes(run.provenance.adapter_version),
    "Saved dense LiDAR profile requires CARLA adapter 0.6.0, 0.7.0 or 0.8.0.",
  );
  requireEvidence(
    (run.execution_context.stack_snapshot.kernel_version !== "stack-kernel/2" &&
      run.execution_context.sensor_profile.id !== "carla-lidar-ego-near/1") ||
      ["0.7.0", "0.8.0"].includes(run.provenance.adapter_version),
    "Saved kernel 2 or near-field profile requires CARLA adapter 0.7.0 or 0.8.0.",
  );
  requireEvidence(
    run.execution_context.schema_version !== "simulation-control-context/2" ||
      run.provenance.adapter_version === "0.8.0",
    "Configured sensor evidence requires CARLA adapter 0.8.0.",
  );
  const trace = value as CarlaLiveTrace;
  requireEvidence(
    trace?.schema_version === "carla-live-stack-trace/1" &&
      trace.execution_id === runExecutionId(run) &&
      trace.input_hash === run.provenance.input_hash &&
      trace.context_hash === run.execution_context.context_hash,
    "Trace does not match the selected run, input hash and context hash.",
  );
  requireEvidence(
    ["completed", "partial"].includes(trace.status) &&
      integer(trace.expected_intervals, 80) &&
      trace.expected_intervals >= 40 &&
      Math.abs(
        trace.expected_intervals * 0.05 - run.scenario_snapshot.duration_s,
      ) < 1e-6 &&
      Array.isArray(trace.ticks) &&
      trace.ticks.length === trace.expected_intervals,
    "Scheduled interval denominator differs from the saved scenario.",
  );
  const metadata = stable(
    run.execution_context.stack_snapshot.component_metadata,
  );
  const stringList = (items: unknown) =>
    Array.isArray(items) &&
    items.length <= 256 &&
    items.every((item) => typeof item === "string");
  for (const [index, tick] of trace.ticks.entries()) {
    requireEvidence(
      tick?.schema_version === "carla-live-stack-tick/1" &&
        tick.sequence === index &&
        ["not_attempted", "running", "completed", "failed"].includes(
          tick.status,
        ) &&
        typeof tick.control_acknowledged === "boolean" &&
        (tick.error === null || typeof tick.error === "string"),
      "Invalid scheduled tick identity or status.",
    );
    requireEvidence(
      tick.source_world_frame === null || integer(tick.source_world_frame),
      "Invalid source world frame.",
    );
    requireEvidence(
      tick.source_time_s === null ||
        (finite(tick.source_time_s) && tick.source_time_s >= 0),
      "Invalid source simulation clock.",
    );
    requireEvidence(
      tick.source_refs &&
        "lidar" in tick.source_refs &&
        "ego" in tick.source_refs,
      "Missing per-role source references.",
    );
    for (const source of Object.values(tick.source_refs)) {
      if (source === null) continue;
      requireEvidence(
        integer(source.channel_id, 65535) &&
          integer(source.ordinal) &&
          SHA.test(source.message_sha256) &&
          ns(source.log_time_ns) &&
          ns(source.publish_time_ns) &&
          source.world_frame === tick.source_world_frame,
        "Invalid MCAP source identity or clock.",
      );
    }
    if (tick.packet !== null) {
      const packet = tick.packet;
      requireEvidence(
        packet &&
          packet.sequence === index &&
          ns(packet.at_ns) &&
          stringList(packet.unavailable_reasons),
        "Packet identity or clock differs from its tick.",
      );
      if (tick.source_time_s !== null)
        requireEvidence(
          Math.abs(Number(packet.at_ns) / 1e9 - tick.source_time_s) < 1e-6,
          "Packet time differs from its source physics observation.",
        );
      if (packet.ego !== null)
        requireEvidence(
          packet.ego &&
            ns(packet.ego.timestamp_ns) &&
            (packet.ego.capture_time_ns === null ||
              ns(packet.ego.capture_time_ns)) &&
            packet.ego.frame_id === "ego" &&
            finite(packet.ego.speed_mps) &&
            packet.ego.speed_mps >= 0 &&
            packet.ego.speed_mps <= 100 &&
            finite(packet.ego.route_heading_error_rad) &&
            finite(packet.ego.pitch_rad) &&
            finite(packet.ego.roll_rad) &&
            typeof packet.ego.source === "string",
          "Invalid ego telemetry packet.",
        );
      if (packet.lidar !== null) {
        const lidar = packet.lidar;
        requireEvidence(
          lidar &&
            ns(lidar.timestamp_ns) &&
            (lidar.capture_time_ns === null || ns(lidar.capture_time_ns)) &&
            lidar.frame_id === "ego" &&
            integer(lidar.point_count, 20000) &&
            lidar.size_bytes === lidar.point_count * 12 &&
            SHA.test(lidar.points_sha256) &&
            SHA.test(lidar.source_message_sha256) &&
            typeof lidar.complete_frame === "boolean" &&
            lidar.coordinate_convention === "x_forward_y_left_z_up" &&
            lidar.encoding === "xyz_float32_le",
          "Invalid full LiDAR packet.",
        );
        requireEvidence(
          tick.source_refs.lidar?.message_sha256 ===
            lidar.source_message_sha256,
          "LiDAR packet and MCAP source hashes differ.",
        );
      }
    }
    if (tick.points !== null) {
      const points = tick.points;
      requireEvidence(
        points?.artifact_name === "carla-stack-points.bin" &&
          points.frame_id === "ego" &&
          integer(points.offset_bytes, 20 * 1024 * 1024) &&
          integer(points.point_count, 20000) &&
          points.size_bytes === points.point_count * 12 &&
          SHA.test(points.sha256),
        "Invalid consumed point slice descriptor.",
      );
      requireEvidence(
        tick.packet?.lidar &&
          points.sha256 === tick.packet.lidar.points_sha256 &&
          points.size_bytes === tick.packet.lidar.size_bytes &&
          points.point_count === tick.packet.lidar.point_count,
        "Consumed slice differs from the packet's full points.",
      );
    }
    if (tick.output !== null) {
      const output = tick.output;
      requireEvidence(
        output &&
          tick.packet &&
          output.kernel_version ===
            run.execution_context.stack_snapshot.kernel_version &&
          output.sequence === index &&
          output.at_ns === tick.packet.at_ns &&
          stable(output.components) === metadata &&
          stringList(output.warnings) &&
          finite(output.elapsed_ms) &&
          output.elapsed_ms >= 0,
        "Stack output identity or pinned components differ.",
      );
      requireEvidence(
        output.input_points_sha256 ===
          (tick.packet.lidar?.points_sha256 ?? null),
        "Stack output consumed point hash differs from its input.",
      );
      if (output.kernel_version === "stack-kernel/2") {
        validateStackOutputV2(output);
        if (index > 0) {
          const previous = trace.ticks[index - 1].output;
          requireEvidence(
            previous?.kernel_version === "stack-kernel/2" &&
              stable(output.state_before) === stable(previous.state_after),
            "Stateful live trace lost its complete stored prefix.",
          );
        }
      } else {
        const observation = output.observation,
          decision = output.decision;
        requireEvidence(
          observation &&
            ["obstacle_observed", "no_obstacle_observed", "unknown"].includes(
              observation.status,
            ) &&
            observation.semantics ===
              "observed_surface_distance_not_free_space_or_ttc" &&
            stringList(observation.reasons) &&
            typeof observation.coverage_qualified === "boolean",
          "Unsupported observation semantics.",
        );
        for (const count of [
          observation.input_points,
          observation.forward_points,
          observation.obstacle_points,
          observation.ground_points,
          observation.ground_bins_observed,
        ])
          requireEvidence(
            integer(count, 20000),
            "Invalid observation point count.",
          );
        for (const distance of [
          observation.distance_m,
          observation.supported_distance_m,
        ])
          requireEvidence(
            distance === null ||
              (finite(distance) && distance >= 0 && distance <= 1e6),
            "Invalid observed distance.",
          );
        requireEvidence(
          observation.nearest_point_xyz_m === null ||
            (Array.isArray(observation.nearest_point_xyz_m) &&
              observation.nearest_point_xyz_m.length === 3 &&
              observation.nearest_point_xyz_m.every(
                (coordinate) =>
                  finite(coordinate) && Math.abs(coordinate) <= 1e6,
              )),
          "Invalid nearest point.",
        );
        requireEvidence(
          decision &&
            [
              "unknown_stop",
              "obstacle_stop",
              "approach",
              "pilot_cruise",
              "hold",
            ].includes(decision.mode) &&
            finite(decision.target_speed_mps) &&
            decision.target_speed_mps >= 0 &&
            decision.target_speed_mps <= 100 &&
            finite(decision.stopping_distance_m) &&
            decision.stopping_distance_m >= 0 &&
            typeof decision.force_brake === "boolean" &&
            stringList(decision.reasons),
          "Invalid policy output.",
        );
      }
      requireEvidence(
        output.control?.steering_convention === "positive_left",
        "Unsupported kernel steering convention.",
      );
      control(output.control);
    }
    control(tick.requested_carla_control);
    control(tick.observed_control);
    if (tick.requested_carla_control !== null) {
      requireEvidence(
        tick.output,
        "CARLA request has no recorded kernel output.",
      );
      const requested = tick.requested_carla_control,
        produced = tick.output.control;
      requireEvidence(
        requested.throttle === produced.throttle &&
          requested.brake === produced.brake &&
          Math.abs(requested.steer + produced.steer) < 1e-12 &&
          requested.reverse === produced.reverse &&
          requested.hand_brake === produced.hand_brake,
        "CARLA request differs from the kernel output or steering reflection.",
      );
    }
    requireEvidence(
      [
        "not_sent",
        "send_outcome_unknown",
        "send_rejected",
        "acknowledged_without_next_observation",
        "next_frame_observed",
      ].includes(tick.application_status),
      "Unsupported command application state.",
    );
    const observed = tick.application_status === "next_frame_observed";
    const rpc = tick.control_rpc_response;
    requireEvidence(
      rpc === null ||
        (rpc &&
          (rpc.actor_id === null || integer(rpc.actor_id)) &&
          (rpc.error === null || typeof rpc.error === "string")),
      "Invalid command RPC response.",
    );
    if (tick.control_acknowledged)
      requireEvidence(
        rpc && integer(rpc.actor_id) && rpc.actor_id > 0 && rpc.error === null,
        "Command ACK lacks a successful RPC response.",
      );
    if (tick.application_status === "send_rejected")
      requireEvidence(
        rpc && typeof rpc.error === "string" && rpc.actor_id === null,
        "Rejected command lacks an RPC rejection record.",
      );
    requireEvidence(
      tick.control_acknowledged ===
        (observed ||
          tick.application_status === "acknowledged_without_next_observation"),
      "Command acknowledgement contradicts its application state.",
    );
    if (tick.application_status !== "not_sent")
      requireEvidence(
        tick.requested_carla_control !== null,
        "Attempted command is missing its request.",
      );
    if (observed) {
      requireEvidence(
        tick.source_world_frame !== null &&
          tick.next_world_frame === tick.source_world_frame + 1 &&
          tick.source_time_s !== null &&
          finite(tick.next_time_s) &&
          Math.abs(tick.next_time_s - tick.source_time_s - 0.05) < 1e-6 &&
          tick.next_ego,
        "Acknowledged command is missing its immediate next physics observation.",
      );
    } else
      requireEvidence(
        tick.next_world_frame === null &&
          tick.next_time_s === null &&
          tick.next_ego === null &&
          tick.observed_control === null,
        "Unobserved interval claims next-frame evidence.",
      );
    if (tick.next_ego !== null)
      for (const key of ["x", "y", "yaw", "speed_mps"] as const)
        requireEvidence(
          finite(tick.next_ego[key]),
          "Invalid next-frame ego observation.",
        );
    if (tick.status === "not_attempted")
      requireEvidence(
        tick.packet === null &&
          tick.output === null &&
          tick.points === null &&
          tick.source_world_frame === null &&
          tick.source_time_s === null &&
          tick.source_refs.ego === null &&
          tick.source_refs.lidar === null &&
          tick.requested_carla_control === null &&
          tick.application_status === "not_sent",
        "Unattempted interval contains dispatched evidence.",
      );
    if (tick.status === "completed")
      requireEvidence(
        observed && tick.output && tick.observed_control,
        "Completed interval lacks output or its next observation.",
      );
    if (trace.status === "completed")
      requireEvidence(
        tick.status === "completed",
        "Complete trace contains incomplete scheduled intervals.",
      );
    const previous = trace.ticks[index - 1];
    if (
      previous?.next_world_frame !== null &&
      previous?.next_world_frame !== undefined &&
      tick.source_world_frame !== null
    )
      requireEvidence(
        previous.next_world_frame === tick.source_world_frame &&
          Math.abs(previous.next_time_s! - tick.source_time_s!) < 1e-6,
        "Adjacent control intervals do not share the same physics boundary.",
      );
  }
  requireEvidence(
    trace.limitations === undefined || stringList(trace.limitations),
    "Invalid trace limitations.",
  );
  return trace;
}
export function liveArtifact(
  run: RunDetail,
  name: string,
  kind: string,
  maxBytes: number,
): Artifact {
  const artifacts = run.artifacts.filter((item) => item.name === name);
  requireEvidence(
    artifacts.length === 1,
    `Expected one ${name} artifact for this run.`,
  );
  const artifact = artifacts[0];
  requireEvidence(
    artifact.run_id === run.id &&
      artifact.kind === kind &&
      run.artifacts.filter((item) => item.id === artifact.id).length === 1 &&
      SHA.test(artifact.sha256) &&
      integer(artifact.size_bytes, maxBytes),
    "Artifact identity, kind, size or checksum is invalid.",
  );
  const url = new URL(artifact.download_url, window.location.origin);
  requireEvidence(
    url.origin === window.location.origin &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.pathname ===
        `/api/v1/artifacts/${encodeURIComponent(artifact.id)}/download`,
    "Artifact URL does not match this same-origin artifact ID.",
  );
  return artifact;
}
export async function readLiveArtifact(
  artifact: Pick<Artifact, "download_url" | "size_bytes" | "sha256">,
  signal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetch(artifact.download_url, {
    signal,
    headers: authHeaders(),
    redirect: "error",
  });
  requireEvidence(response.ok, `Artifact unavailable (${response.status}).`);
  const length = response.headers.get("Content-Length");
  requireEvidence(
    length === null ||
      (/^\d+$/.test(length) && Number(length) === artifact.size_bytes),
    "Artifact HTTP length differs from its descriptor.",
  );
  requireEvidence(response.body, "Artifact stream is unavailable.");
  const bytes = new Uint8Array(artifact.size_bytes),
    reader = response.body.getReader();
  let offset = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      requireEvidence(
        offset + value.length <= bytes.length,
        "Artifact exceeds its declared byte count.",
      );
      bytes.set(value, offset);
      offset += value.length;
    }
    requireEvidence(
      offset === bytes.length,
      "Artifact byte count differs from its descriptor.",
    );
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  await verifyBuffer(bytes, artifact.sha256);
  return bytes;
}

export function validateLiveManifest(
  value: unknown,
  run: RunDetail,
  trace: CarlaLiveTrace,
): import("./CarlaTypes").CarlaLiveManifest {
  finiteTree(value);
  const manifest = value as import("./CarlaTypes").CarlaLiveManifest;
  requireEvidence(
    manifest?.schema_version === "carla-live-stack-manifest/1" &&
      manifest.execution_id === runExecutionId(run) &&
      manifest.input_hash === trace.input_hash &&
      manifest.context_hash === trace.context_hash &&
      manifest.status === trace.status &&
      stable(manifest.execution_context) === stable(run.execution_context),
    "Manifest differs from the stored execution context or trace.",
  );
  requireEvidence(
    manifest.scenario_id === run.scenario_id &&
      manifest.scenario_revision === run.scenario_revision &&
      manifest.seed === run.provenance.seed &&
      manifest.worker_adapter_version === run.provenance.adapter_version &&
      manifest.fixed_delta_seconds === 0.05 &&
      manifest.expected_intervals === trace.expected_intervals &&
      manifest.expected_observations === trace.expected_intervals + 1,
    "Manifest scenario, adapter or interval context differs from the run.",
  );
  for (const count of [
    manifest.observed_frames,
    manifest.retained_source_observations,
  ])
    requireEvidence(
      integer(count, trace.expected_intervals + 1),
      "Invalid observation count in manifest.",
    );
  requireEvidence(
    manifest.ego_actor_id === null ||
      (integer(manifest.ego_actor_id) && manifest.ego_actor_id > 0),
    "Invalid manifest ego actor identity.",
  );
  for (const tick of trace.ticks)
    if (tick.control_acknowledged)
      requireEvidence(
        tick.control_rpc_response?.actor_id === manifest.ego_actor_id,
        "Acknowledged RPC actor differs from the preserved ego actor identity.",
      );
  requireEvidence(
    manifest.acknowledged_commands ===
      trace.ticks.filter((tick) => tick.control_acknowledged).length &&
      manifest.next_frame_observed_commands ===
        trace.ticks.filter(
          (tick) => tick.application_status === "next_frame_observed",
        ).length,
    "Manifest command counts differ from recorded intervals.",
  );
  if (trace.status === "completed")
    requireEvidence(
      manifest.observed_frames === trace.expected_intervals + 1 &&
        manifest.retained_source_observations === trace.expected_intervals + 1,
      "Completed trace lacks N+1 preserved observations.",
    );
  requireEvidence(
    (manifest.failure_phase === null ||
      typeof manifest.failure_phase === "string") &&
      (manifest.error === null || typeof manifest.error === "string") &&
      Array.isArray(manifest.cleanup_errors) &&
      manifest.cleanup_errors.every((item) => typeof item === "string") &&
      Array.isArray(manifest.limitations) &&
      manifest.limitations.every((item) => typeof item === "string"),
    "Invalid failure or limitation metadata.",
  );
  requireEvidence(
    Array.isArray(manifest.artifacts) &&
      manifest.artifacts.length <= 100 &&
      new Set(manifest.artifacts.map((item) => item.name)).size ===
        manifest.artifacts.length,
    "Invalid manifest artifact list.",
  );
  for (const item of manifest.artifacts) {
    const existing = run.artifacts.filter(
      (artifact) => artifact.name === item.name,
    );
    requireEvidence(
      existing.length === 1 &&
        existing[0].run_id === run.id &&
        existing[0].sha256 === item.sha256 &&
        existing[0].size_bytes === item.size_bytes &&
        existing[0].kind === item.kind,
      "Manifest artifact digest or size differs from the run's stored descriptors.",
    );
  }
  for (const name of [
    "carla-stack-trace.json",
    "carla-stack-points.bin",
    "carla-stack-raw-xyzi.bin",
    "carla-stack-sources.mcap",
    "carla-stack-physics.json",
  ])
    requireEvidence(
      manifest.artifacts.some((artifact) => artifact.name === name),
      "Manifest is missing a required original evidence artifact.",
    );
  return manifest;
}
