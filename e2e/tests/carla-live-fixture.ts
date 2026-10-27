import { createHash } from "node:crypto";
import type { APIRequestContext, Page } from "@playwright/test";
export const digest = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
const json = (value: unknown) => Buffer.from(JSON.stringify(value));
export async function authoredContext(request: APIRequestContext) {
  const modules = await (await request.get("/api/v1/stack-components")).json();
  const definition: Record<string, unknown> = { schema_version: "1", reference_frame: "ego" };
  for (const [role, id] of [["perception", "lidar-corridor"], ["policy", "stopping-headway"], ["controller", "proportional-control"]]) {
    const matches = modules.filter((item: any) => item.id === id && item.version === "1");
    if (matches.length !== 1 || matches[0].role !== role || matches[0].status !== "available")
      throw new Error(`Authored browser fixture requires one available ${role} component ${id}/1`);
    const module = matches[0];
    definition[role] = { id: module.id, version: module.version, parameters: { ...module.default_parameters } };
  }
  const response = await request.post("/api/v1/stacks", { data: { name: "Authored live browser stack", description: "Authored browser diagnostic fixture, not an actual CARLA run", note: "Unknown ground is deliberately retained", definition } });
  if (response.status() !== 201) throw new Error(await response.text());
  const stack = await response.json();
  const mount = { translation_m: [0, 0, 2.2], rotation_xyzw: [0, 0, 0, 1] };
  const rigResponse = await request.post("/api/v1/sensor-rigs", { data: { name: "Authored browser live rig", description: "Declared fixture mount", reference_frame: "ego", coordinate_convention: "right_handed_z_up", source_kind: "synthetic", source: "Authored browser protocol fixture; no calibrated sensor or engine execution", note: "Fixed CARLA profile mounting", sensors: [{ key: "roof", name: "Roof LiDAR", kind: "lidar", frame_id: "lidar_roof", transform: mount, intrinsics: null }] } });
  if (rigResponse.status() !== 201) throw new Error(await rigResponse.text());
  const rig = await rigResponse.json();
  const scenarioResponse = await request.post("/api/v1/scenarios", { data: { name: "Authored live control browser fixture", description: "No CARLA executed by browser test", category: "lane_follow", map_name: "Town10HD_Opt", weather: "clear", time_of_day: "day", duration_s: 2, seed: 41, tags: [], parameters: { ego_speed_mps: 4, target_speed_mps: 4, initial_gap_m: 30, trigger_time_s: 1, friction: .9, response_delay_s: .5 } } });
  if (scenarioResponse.status() !== 201) throw new Error(await scenarioResponse.text());
  const scenario = await scenarioResponse.json();
  const context = { schema_version: "simulation-control-context/1", mode: "stack", stack_snapshot: stack, rig_snapshot: rig, lidar_sensor_key: "roof", sensor_profile: { id: "carla-lidar-ego/1", version: "1", blueprint_id: "sensor.lidar.ray_cast", attributes: { channels: "16", range: "80", points_per_second: "32000", rotation_frequency: "20", sensor_tick: "0.0", noise_seed: "41" }, mount, sensor_frame: "lidar_roof", reference_frame: "ego", coordinate_policy: "carla_xyzi_y_reflection_then_declared_sensor_to_ego/1", clock_policy: "carla_simulation_seconds_round_half_even_ns/1", max_points: 20000, source: "Authored protocol fixture profile; not CARLA evidence", profile_hash: "c".repeat(64) }, ego_source_policy: "carla-ego-state-and-map-route/1", context_hash: "d".repeat(64) };
  return { context, scenario };
}
/** Authored browser diagnostics. The artifacts deliberately do not claim a native engine run. */
export function liveFixture(input: Awaited<ReturnType<typeof authoredContext>>, suffix: string, partial = false) {
  const { context, scenario } = structuredClone(input);
  const execution_id = `run_live_browser_${suffix}`, input_hash = "1".repeat(64);
  const xyz = Buffer.alloc(36); [5, 0, .7, 12, 1, 0, 26, -1, 0].forEach((value, index) => xyz.writeFloatLE(value, index * 4));
  const raw = Buffer.alloc(48); [5, 0, -1.5, 1, 12, -1, -2.2, 1, 26, 1, -2.2, 1].forEach((value, index) => raw.writeFloatLE(value, index * 4));
  const ticks: any[] = Array.from({ length: 40 }, (_, sequence) => {
    const tick: any = { schema_version: "carla-live-stack-tick/1", sequence, status: "not_attempted", source_world_frame: null, source_time_s: null, packet: null, output: null, source_refs: { lidar: null, ego: null }, points: null, requested_carla_control: null, control_acknowledged: false, control_rpc_response: null, next_world_frame: null, next_time_s: null, observed_control: null, next_ego: null, application_status: "not_sent", error: null };
    if (partial && sequence > 1) return tick;
    const at = String(5000000000n + BigInt(sequence) * 50000000n), time = 5 + sequence * .05;
    const source = (channel_id: number, ordinal: number) => ({ channel_id, ordinal, message_sha256: digest(json({ sequence, channel_id })), log_time_ns: at, publish_time_ns: at, world_frame: 100 + sequence });
    const lidarSource = { ...source(1, sequence * 2), raw_carla_xyzi_sha256: digest(raw), raw_points: { artifact_name: "carla-stack-raw-xyzi.bin", offset_bytes: sequence * raw.length, size_bytes: raw.length, sha256: digest(raw), encoding: "xyzi_float32_le_carla" } };
    const output = { kernel_version: "stack-kernel/1", sequence, at_ns: at, observation: { status: "unknown", distance_m: null, nearest_point_xyz_m: null, input_points: 3, forward_points: 0, obstacle_points: 0, ground_points: 0, ground_bins_observed: 0, supported_distance_m: null, coverage_qualified: false, reasons: ["Authored output fixture; no geometric inference was performed by this browser test"], semantics: "observed_surface_distance_not_free_space_or_ttc" }, decision: { mode: "unknown_stop", target_speed_mps: 0, stopping_distance_m: 6, force_brake: true, reasons: ["Authored control diagnostic"] }, control: { throttle: 0, brake: 1, steer: .1, reverse: false, hand_brake: false, steering_convention: "positive_left" }, input_points_sha256: digest(xyz), components: context.stack_snapshot.component_metadata, warnings: ["Authored diagnostic fixture, not native CARLA execution"], elapsed_ms: .2 };
    Object.assign(tick, { status: "completed", source_world_frame: 100 + sequence, source_time_s: time, packet: { sequence, at_ns: at, ego: { timestamp_ns: at, capture_time_ns: at, frame_id: "ego", speed_mps: Math.max(0, 4 - sequence * .1), route_heading_error_rad: .05, pitch_rad: 0, roll_rad: 0, source: "Authored ego/map oracle fixture" }, lidar: { timestamp_ns: at, capture_time_ns: at, frame_id: "ego", point_count: 3, points_sha256: digest(xyz), size_bytes: xyz.length, source_message_sha256: lidarSource.message_sha256, complete_frame: true, coordinate_convention: "x_forward_y_left_z_up", encoding: "xyz_float32_le" }, unavailable_reasons: [] }, output, source_refs: { lidar: lidarSource, ego: source(2, sequence * 2 + 1) }, points: { artifact_name: "carla-stack-points.bin", offset_bytes: sequence * xyz.length, size_bytes: xyz.length, sha256: digest(xyz), point_count: 3, frame_id: "ego" }, requested_carla_control: { throttle: 0, brake: 1, steer: -.1, reverse: false, hand_brake: false }, control_acknowledged: true, control_rpc_response: { actor_id: 11, error: null }, next_world_frame: 101 + sequence, next_time_s: time + .05, observed_control: { throttle: 0, brake: .95, steer: -.1, reverse: false, hand_brake: false }, next_ego: { x: sequence * .2, y: 0, yaw: 0, speed_mps: Math.max(0, 3.9 - sequence * .1) }, application_status: "next_frame_observed" });
    if (partial && sequence === 1) Object.assign(tick, { status: "failed", control_acknowledged: true, next_world_frame: null, next_time_s: null, observed_control: null, next_ego: null, application_status: "acknowledged_without_next_observation", error: "Authored next physics timeout" });
    return tick;
  });
  const trace: any = { schema_version: "carla-live-stack-trace/1", execution_id, input_hash, context_hash: context.context_hash, status: partial ? "partial" : "completed", expected_intervals: 40, ticks, scope: "Authored browser diagnostic fixture; no CARLA or dynamics acceptance" };
  const run: any = { id: execution_id, scenario_id: scenario.id, scenario_name: `${scenario.name} ${suffix}`, scenario_revision: scenario.revision, adapter_id: "carla", status: partial ? "error" : "completed", verdict: partial ? "error" : "failed", created_at: "2026-09-13T12:00:00Z", duration_s: 2, execution_ms: 2000, metrics: [], provenance: { input_hash, output_hash: "2".repeat(64), seed: scenario.seed, adapter_version: "0.4.0", model_fidelity: "Authored browser diagnostic fixture; not a CARLA run" }, scenario_snapshot: scenario, execution_context: context, trajectory: [], events: [], artifacts: [] };
  const manifest: any = { ego_actor_id: 11, schema_version: "carla-live-stack-manifest/1", execution_id, input_hash, context_hash: context.context_hash, status: trace.status, execution_context: context, worker_adapter_version: "0.4.0", server_version: "authored-fixture", map_name: scenario.map_name, scenario_id: scenario.id, scenario_revision: scenario.revision, seed: scenario.seed, fixed_delta_seconds: .05, expected_intervals: 40, expected_observations: 41, observed_frames: partial ? 2 : 41, retained_source_observations: partial ? 2 : 41, acknowledged_commands: partial ? 2 : 40, next_frame_observed_commands: partial ? 1 : 40, failure_phase: partial ? "next_physics_observation" : null, error: partial ? "Authored partial diagnostic" : null, cleanup_errors: [], sensor_scope: "LiDAR and ego oracle; no RGB", limitations: ["Authored browser diagnostic fixture; not an actual CARLA run."], artifacts: [] };
  const files = new Map<string, Buffer>();
  const artifact = (name: string, kind: string, bytes: Buffer, media_type = "application/octet-stream") => { const id = `artifact_${suffix}_${files.size}`; files.set(name, bytes); return { id, run_id: execution_id, name, kind, media_type, size_bytes: bytes.length, sha256: digest(bytes), created_at: run.created_at, download_url: `/api/v1/artifacts/${id}/download`, source: "Authored browser diagnostic fixture" }; };
  const refresh = () => {
    files.clear();
    run.artifacts = [artifact("carla-stack-trace.json", "live_stack_trace", json(trace), "application/json"), artifact("carla-stack-points.bin", "live_stack_points", Buffer.concat(Array(partial ? 2 : 41).fill(xyz))), artifact("carla-stack-raw-xyzi.bin", "live_stack_raw_points", Buffer.concat(Array(partial ? 2 : 41).fill(raw))), artifact("carla-stack-sources.mcap", "sensor_recording", Buffer.from("Authored browser identity placeholder; no MCAP decoding in this test"), "application/mcap"), artifact("carla-stack-physics.json", "physics_trace", json({ scope: "authored fixture" }), "application/json")];
    manifest.artifacts = run.artifacts.map(({ name, kind, sha256, size_bytes }: any) => ({ name, kind, sha256, size_bytes }));
    run.artifacts.push(artifact("carla-stack-manifest.json", "live_stack_manifest", json(manifest), "application/json"));
  };
  refresh(); return { run, trace, manifest, files, refresh };
}
export type LiveFixture = ReturnType<typeof liveFixture>;
export async function installLive(page: Page, items: LiveFixture[]) {
  await page.route("**/api/v1/overview", async (route) => { const original = await (await route.fetch()).json(); await route.fulfill({ json: { ...original, recent_runs: items.map((item) => item.run) } }); });
  await page.route("**/api/v1/runs", (route) => route.fulfill({ json: items.map((item) => item.run) }));
  for (const item of items) {
    await page.route(`**/api/v1/runs/${item.run.id}`, (route) => route.fulfill({ json: item.run }));
    for (const artifact of item.run.artifacts) await page.route(`**${artifact.download_url}`, (route) => route.fulfill({ contentType: artifact.media_type, body: item.files.get(artifact.name)! }));
  }
  await page.goto("/#runs");
}
