import type { RunDetail } from "../../types";
import { runExecutionId } from "../jobs/RunEvidenceIdentity";
import { validateLiveContext } from "./CarlaLiveData";
export type Matrix = number[][];
export interface MountAnalysis {
  relative_native_matrix: Matrix;
  relative_rh_matrix: Matrix;
  translation_residual_m: number;
  rotation_residual: number;
  translation_tolerance_m: number;
  rotation_tolerance: number;
  api_translation_residual_m: number;
  api_rotation_residual: number;
  measurement_translation_residual_m: number | null;
  measurement_rotation_residual: number | null;
  qualified: boolean;
  reasons: string[];
}
export interface MountRow {
  world_frame: number;
  source_time_s: number;
  bracket_before_frame: number;
  bracket_after_frame: number;
  sensor_actor_id: number;
  ego_actor_id: number;
  parent_actor_id: number | null;
  sensor_type_id: string;
  ego_world_matrix: Matrix;
  sensor_world_matrix: Matrix;
  sensor_api_world_matrix: Matrix;
  measurement_world_matrix: Matrix | null;
  measurement_frame: number | null;
  measurement_time_s: number | null;
  analysis: MountAnalysis;
}
type AttributeMap = Record<string, { type: "int" | "float32"; value: number }>;
export interface SensorMountEvidence {
  schema_version: "carla-sensor-configuration/1";
  execution_id: string;
  input_hash: string;
  context_hash: string;
  status: "completed" | "partial";
  failure_phase: string | null;
  error: string | null;
  sensor_frame: string;
  configuration_manifest_hash: string;
  rig_manifest_hash: string;
  sensor_profile_hash: string;
  policy: string;
  requested: {
    attributes: Record<string, string>;
    mount: {
      translation_m: [number, number, number];
      rotation_xyzw: [number, number, number, number];
    };
  };
  spawn: {
    policy: string;
    declared_rh_matrix: Matrix;
    requested_native_matrix: Matrix;
    native_spawn_matrix: Matrix;
    native_location_m: number[];
    native_rotation_deg: { pitch: number; yaw: number; roll: number };
  } | null;
  detached_attributes: AttributeMap;
  actor_attributes: {
    raw: Record<string, string>;
    typed: AttributeMap;
    qualified: boolean;
  } | null;
  startup_mount: MountRow | null;
  observations: MountRow[];
  callback_drain: {
    accepting: boolean;
    started: number;
    finished: number;
    active: number;
    rejected_after_stop: number;
    drained: boolean;
  };
}
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
const integer = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) >= 0;
function check(v: unknown, text: string): asserts v {
  if (!v) throw new Error(text);
}
const stable = (v: unknown): string =>
  Array.isArray(v)
    ? `[${v.map(stable).join(",")}]`
    : v && typeof v === "object"
      ? `{${Object.entries(v)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([k, item]) => `${JSON.stringify(k)}:${stable(item)}`)
          .join(",")}}`
      : JSON.stringify(v);
function keys(v: object, expected: string[]) {
  check(
    v && stable(Object.keys(v).sort()) === stable([...expected].sort()),
    "Unsupported sensor readback fields.",
  );
}
function matrix(v: Matrix) {
  check(
    Array.isArray(v) &&
      v.length === 4 &&
      v.every(
        (row) => Array.isArray(row) && row.length === 4 && row.every(finite),
      ) &&
      stable(v[3]) === "[0,0,0,1]",
    "Invalid sensor transform matrix.",
  );
  const gram = Math.max(
    ...[0, 1, 2].flatMap((i) =>
      [0, 1, 2].map((j) =>
        Math.abs(
          v.reduce((sum, row, k) => (k < 3 ? sum + row[i] * row[j] : sum), 0) -
            (i === j ? 1 : 0),
        ),
      ),
    ),
  );
  const [a, b, c] = v;
  const det =
    a[0] * (b[1] * c[2] - b[2] * c[1]) -
    a[1] * (b[0] * c[2] - b[2] * c[0]) +
    a[2] * (b[0] * c[1] - b[1] * c[0]);
  check(
    gram <= 2e-5 &&
      Math.abs(det - 1) <= 4e-5 &&
      v.slice(0, 3).every((row) => Math.abs(row[3]) <= 1e6),
    "Sensor transform is not a supported rigid pose.",
  );
  return v;
}
const multiply = (a: Matrix, b: Matrix) =>
  a.map((row) =>
    b[0].map((_, j) => row.reduce((sum, value, k) => sum + value * b[k][j], 0)),
  );
const inverse = (a: Matrix) =>
  [0, 1, 2]
    .map((i) => [
      ...[0, 1, 2].map((j) => a[j][i]),
      -[0, 1, 2].reduce((sum, k) => sum + a[k][i] * a[k][3], 0),
    ])
    .concat([[0, 0, 0, 1]]);
const reflect = (a: Matrix) =>
  a.map((row, i) => row.map((n, j) => n * [1, -1, 1, 1][i] * [1, -1, 1, 1][j]));
const residual = (a: Matrix, b: Matrix) => [
  Math.max(...[0, 1, 2].map((i) => Math.abs(a[i][3] - b[i][3]))),
  Math.max(
    ...[0, 1, 2].flatMap((i) =>
      [0, 1, 2].map((j) => Math.abs(a[i][j] - b[i][j])),
    ),
  ),
];
function declared(m: SensorMountEvidence["requested"]["mount"]) {
  const [x, y, z, w] = m.rotation_xyzw,
    [dx, dy, dz] = m.translation_m;
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), dx],
    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), dy],
    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), dz],
    [0, 0, 0, 1],
  ];
}
const close = (a: unknown, b: number) => finite(a) && Math.abs(a - b) <= 1e-9;
function attributes(a: Record<string, string>): AttributeMap {
  check(
    a && typeof a === "object" && !Array.isArray(a),
    "Sensor attribute map missing.",
  );
  return Object.fromEntries(
    Object.entries(a).map(([key, v]) => {
      check(
        typeof v === "string" && v.length < 256 && finite(Number(v)),
        "Sensor attribute value invalid.",
      );
      const int = ["channels", "points_per_second", "noise_seed"].includes(key);
      const value = int ? Number(v) : Math.fround(Number(v));
      check(
        finite(value) && (!int || integer(value)),
        "Sensor attribute type invalid.",
      );
      return [key, { type: int ? "int" : "float32", value }];
    }),
  );
}
function rowAnalysis(
  row: MountRow,
  mount: SensorMountEvidence["requested"]["mount"],
) {
  keys(row, [
    "world_frame",
    "source_time_s",
    "bracket_before_frame",
    "bracket_after_frame",
    "sensor_actor_id",
    "ego_actor_id",
    "parent_actor_id",
    "sensor_type_id",
    "ego_world_matrix",
    "sensor_world_matrix",
    "sensor_api_world_matrix",
    "measurement_world_matrix",
    "measurement_frame",
    "measurement_time_s",
    "analysis",
  ]);
  check(
    finite(row.source_time_s) && row.source_time_s >= 0,
    "Sensor clock is invalid.",
  );
  const ego = matrix(row.ego_world_matrix),
    sensor = matrix(row.sensor_world_matrix),
    api = matrix(row.sensor_api_world_matrix);
  const native = multiply(inverse(ego), sensor),
    rh = reflect(native),
    [dt, dr] = residual(rh, declared(mount)),
    [at, ar] = residual(api, sensor);
  const tolerance =
    2e-5 +
    16 *
      2 ** -23 *
      Math.max(
        1,
        ...[ego, sensor].map((m) =>
          m.slice(0, 3).reduce((sum, r) => sum + Math.abs(r[3]), 0),
        ),
      );
  const measured =
    row.measurement_world_matrix === null
      ? null
      : residual(matrix(row.measurement_world_matrix), sensor);
  const reasons = [];
  if (
    !integer(row.world_frame) ||
    row.bracket_before_frame !== row.world_frame ||
    row.bracket_after_frame !== row.world_frame
  )
    reasons.push("world_frame_bracket_mismatch");
  if (
    !integer(row.sensor_actor_id) ||
    row.sensor_actor_id === 0 ||
    !integer(row.ego_actor_id) ||
    row.ego_actor_id === 0 ||
    row.sensor_actor_id === row.ego_actor_id ||
    row.parent_actor_id !== row.ego_actor_id ||
    row.sensor_type_id !== "sensor.lidar.ray_cast"
  )
    reasons.push("sensor_identity_or_parent_mismatch");
  if (dt > tolerance || dr > 2e-5) reasons.push("declared_mount_mismatch");
  if (at > tolerance || ar > 2e-5)
    reasons.push("sensor_api_snapshot_pose_mismatch");
  if (
    measured &&
    (row.measurement_frame !== row.world_frame ||
      row.measurement_time_s !== row.source_time_s ||
      measured[0] > tolerance ||
      measured[1] > 2e-5)
  )
    reasons.push("measurement_snapshot_pose_or_time_mismatch");
  if (
    !measured &&
    (row.measurement_frame !== null || row.measurement_time_s !== null)
  )
    reasons.push("incomplete_measurement_pose");
  const a = row.analysis;
  keys(a, [
    "relative_native_matrix",
    "relative_rh_matrix",
    "translation_residual_m",
    "rotation_residual",
    "translation_tolerance_m",
    "rotation_tolerance",
    "api_translation_residual_m",
    "api_rotation_residual",
    "measurement_translation_residual_m",
    "measurement_rotation_residual",
    "qualified",
    "reasons",
  ]);
  check(
    residual(matrix(a.relative_native_matrix), native).every(
      (n) => n <= 1e-9,
    ) &&
      residual(matrix(a.relative_rh_matrix), rh).every((n) => n <= 1e-9) &&
      close(a.translation_residual_m, dt) &&
      close(a.rotation_residual, dr) &&
      close(a.translation_tolerance_m, tolerance) &&
      a.rotation_tolerance === 2e-5 &&
      close(a.api_translation_residual_m, at) &&
      close(a.api_rotation_residual, ar) &&
      (measured
        ? close(a.measurement_translation_residual_m, measured[0]) &&
          close(a.measurement_rotation_residual, measured[1])
        : a.measurement_translation_residual_m === null &&
          a.measurement_rotation_residual === null) &&
      a.qualified === !reasons.length &&
      stable(a.reasons) === stable(reasons),
    "Sensor mount qualification contradicts the original frame/parent/matrix observations.",
  );
}
export function validateSensorMountEvidence(
  value: unknown,
  run: RunDetail,
): SensorMountEvidence {
  const context = run.execution_context;
  check(
    context?.schema_version === "simulation-control-context/2" &&
      run.adapter_id === "carla" &&
      run.provenance.adapter_version === "0.8.0",
    "Configured sensor evidence requires the selected 0.8.0 context/2 run.",
  );
  validateLiveContext(context);
  const v = value as SensorMountEvidence;
  keys(v, [
    "schema_version",
    "execution_id",
    "input_hash",
    "context_hash",
    "status",
    "failure_phase",
    "error",
    "sensor_frame",
    "configuration_manifest_hash",
    "rig_manifest_hash",
    "sensor_profile_hash",
    "policy",
    "requested",
    "spawn",
    "detached_attributes",
    "actor_attributes",
    "startup_mount",
    "observations",
    "callback_drain",
  ]);
  check(
    v.schema_version === "carla-sensor-configuration/1" &&
      v.execution_id === runExecutionId(run) &&
      v.input_hash === run.provenance.input_hash &&
      v.context_hash === context.context_hash &&
      v.configuration_manifest_hash ===
        context.sensor_configuration_snapshot.manifest_hash &&
      v.rig_manifest_hash === context.rig_snapshot.manifest_hash &&
      v.sensor_profile_hash === context.sensor_profile.profile_hash &&
      v.sensor_frame === context.sensor_profile.sensor_frame &&
      v.policy === "carla-rigid-mount-readback/1" &&
      ["completed", "partial"].includes(v.status),
    "Sensor readback differs from the selected run or immutable configuration.",
  );
  check(
    (v.error === null || typeof v.error === "string") &&
      (v.failure_phase === null || typeof v.failure_phase === "string"),
    "Invalid sensor failure metadata.",
  );
  check(
    stable(v.requested) ===
      stable({
        attributes: context.sensor_profile.attributes,
        mount: context.sensor_profile.mount,
      }),
    "Sensor requested values differ from saved configuration.",
  );
  const wanted = attributes(v.requested.attributes);
  check(
    v.detached_attributes &&
      Object.entries(v.detached_attributes).every(
        ([k, item]) => k in wanted && stable(item) === stable(wanted[k]),
      ),
    "Detached blueprint readback differs from request.",
  );
  if (v.actor_attributes !== null) {
    keys(v.actor_attributes, ["raw", "typed", "qualified"]);
    const a = v.actor_attributes;
    check(
      stable(Object.keys(a.raw).sort()) ===
        stable(Object.keys(wanted).sort()) &&
        stable(a.typed) === stable(attributes(a.raw)) &&
        a.qualified === (stable(a.typed) === stable(wanted)),
      "Spawned actor attribute qualification contradicts its raw readback.",
    );
  }
  if (v.spawn !== null) {
    const s = v.spawn;
    keys(s, [
      "policy",
      "declared_rh_matrix",
      "requested_native_matrix",
      "native_spawn_matrix",
      "native_location_m",
      "native_rotation_deg",
    ]);
    const expected = declared(v.requested.mount);
    check(
      s.policy === "carla_rh_rig_to_lh_native_matrix/1" &&
        residual(matrix(s.declared_rh_matrix), expected).every(
          (n) => n <= 1e-12,
        ) &&
        residual(matrix(s.requested_native_matrix), reflect(expected)).every(
          (n) => n <= 1e-12,
        ),
      "Requested native mount conversion differs.",
    );
    const r = residual(
      matrix(s.native_spawn_matrix),
      s.requested_native_matrix,
    );
    check(
      r[0] <= 1e-6 &&
        r[1] <= 2e-6 &&
        stable(s.native_location_m) ===
          stable(s.native_spawn_matrix.slice(0, 3).map((row) => row[3])),
      "Native spawn transform differs.",
    );
    keys(s.native_rotation_deg, ["pitch", "yaw", "roll"]);
    check(
      Object.values(s.native_rotation_deg).every(finite),
      "Invalid native angles.",
    );
  }
  check(
    Array.isArray(v.observations) && v.observations.length <= 81,
    "Sensor mount observation bound exceeded.",
  );
  if (v.startup_mount !== null) {
    rowAnalysis(v.startup_mount, v.requested.mount);
    check(
      v.startup_mount.measurement_world_matrix === null,
      "Startup readback incorrectly claims a measurement.",
    );
  }
  for (const [i, row] of v.observations.entries()) {
    rowAnalysis(row, v.requested.mount);
    if (i)
      check(
        row.world_frame > v.observations[i - 1].world_frame,
        "Sensor frames must increase uniquely.",
      );
    if (v.startup_mount)
      check(
        row.sensor_actor_id === v.startup_mount.sensor_actor_id &&
          row.ego_actor_id === v.startup_mount.ego_actor_id &&
          row.world_frame > v.startup_mount.world_frame,
        "Sensor identity changed after startup.",
      );
  }
  const d = v.callback_drain;
  keys(d, [
    "accepting",
    "started",
    "finished",
    "active",
    "rejected_after_stop",
    "drained",
  ]);
  check(
    [d.started, d.finished, d.active, d.rejected_after_stop].every(integer) &&
      d.active === d.started - d.finished &&
      typeof d.accepting === "boolean" &&
      typeof d.drained === "boolean" &&
      (!d.drained || (!d.accepting && d.active === 0)),
    "Invalid callback completion evidence.",
  );
  if (v.status === "completed")
    check(
      v.failure_phase === null &&
        v.error === null &&
        d.drained &&
        v.spawn &&
        stable(v.detached_attributes) === stable(wanted) &&
        v.actor_attributes?.qualified &&
        v.startup_mount?.analysis.qualified &&
        v.observations.length ===
          Math.round(run.scenario_snapshot.duration_s / 0.05) + 1 &&
        v.observations.every(
          (row) =>
            row.analysis.qualified && row.measurement_world_matrix !== null,
        ),
      "Completed sensor evidence lacks all required readbacks.",
    );
  return v;
}
