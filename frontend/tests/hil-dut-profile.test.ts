import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  hilProfileComparison,
  hilProfileError,
  hilProfileRef,
  hilProfileSelectionError,
  hilUpdateProfileSelection,
  parseHilDutProfile,
  verifyHilProfileHash,
} from "../src/components/hil-demo/dut-profile.ts";
import type { HilDutProfile } from "../src/components/hil-demo/dut-profile.ts";
import {
  hilDraftSignature,
  hilTestDraft,
  hilTestSaveBody,
} from "../src/components/hil-demo/beta-contract.ts";
import type { HilTest } from "../src/components/hil-demo/beta-contract.ts";
import {
  hilSideLabel,
  hilVerifyTracePair,
  parseHilTrace,
} from "../src/components/hil-demo/hil-trace.ts";
import type { HilEvidence } from "../src/components/hil-demo/hil-trace.ts";

// Authored profile and trace fixtures, not native execution evidence.
const sorted = (value: unknown): unknown =>
  Array.isArray(value)
    ? value.map(sorted)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.entries(value)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([key, item]) => [key, sorted(item)]),
        )
      : value;
function profile(
  id: string,
  age: number,
  steps: number,
  revision = 1,
): HilDutProfile {
  const body = {
    schema_version: "hil-dut-profile/1" as const,
    project_id: "project",
    id,
    revision,
    name: id === "a" ? "기준 정책" : "지연 허용 정책",
    note: "",
    parameters: { max_object_age_ms: age, recovery_fresh_steps: steps },
    author: { role: "Engineer", user_label: "Authored fixture" },
    created_at: "2026-10-11T00:00:00Z",
  };
  return {
    ...body,
    sha256: createHash("sha256")
      .update(JSON.stringify(sorted(body)))
      .digest("hex"),
  };
}
const profiles = {
  baseline: profile("a", 250, 3),
  faulted: profile("b", 500, 8, 2),
};
const fault = { kind: "delay", start_s: 2, duration_s: 3, delay_ms: 400 };
const context = () => ({
  summary: {
    comparison_kind: "same_fault_dut_ab",
    dut_profiles: profiles,
    sides: {
      baseline: { dut_profile: profiles.baseline },
      faulted: { dut_profile: profiles.faulted },
    },
  },
  source: { comparison_kind: "same_fault_dut_ab", dut_profiles: profiles },
  spec: {
    comparison_kind: "same_fault_dut_ab",
    input: { dut_profiles: profiles },
  },
});
const trace = (side: "baseline" | "faulted") => ({
  schema_version: "hil-demo-trace/1",
  execution_target: "software_dut",
  side,
  comparison_kind: "same_fault_dut_ab",
  dut_profile: profiles[side],
  fault_time_basis: "simulation_time",
  ipc_time_basis: "parent_monotonic_wall_clock",
  max_object_age_s: profiles[side].parameters.max_object_age_ms / 1000,
  recovery_fresh_steps: profiles[side].parameters.recovery_fresh_steps,
  fault,
  frames: [0, 1].map((sequence) => ({
    time_s: (sequence + 1) * 0.05,
    input_time_s: sequence * 0.05,
    sequence,
    input_age_ms: 0,
    dut_state: "tracking",
    ego_speed_mps: 3,
    lead_gap_m: 12,
    command: { throttle: 0.2, brake: 0, steer: 0 },
    ipc_wall_latency_ms: 1,
    object_sequence: sequence,
    response_frame: sequence + 100,
  })),
  events: [],
});

test("profile controls enforce exact integer policy bounds", () => {
  assert.equal(
    hilProfileError({ max_object_age_ms: 250, recovery_fresh_steps: 3 }),
    "",
  );
  for (const age of [NaN, 49, 51, 1001, 250.00000001])
    assert.ok(
      hilProfileError({ max_object_age_ms: age, recovery_fresh_steps: 3 }),
    );
  for (const steps of [0, 1.1, 11, Infinity])
    assert.ok(
      hilProfileError({ max_object_age_ms: 250, recovery_fresh_steps: steps }),
    );
  assert.ok(
    hilProfileSelectionError({
      baseline: hilProfileRef(profiles.baseline),
      faulted: null,
    }),
  );
});
test("profile edits pin references and preserve absent legacy defaults", () => {
  const base = {
    id: "test",
    revision: 3,
    sha256: "d".repeat(64),
    name: "test",
    note: "",
    preset_id: "input_delay",
    parameters: { delay_ms: 400, start_s: 2, duration_s: 3 },
    binding_ref: { id: "input_delay", revision: 1, sha256: "e".repeat(64) },
  } as HilTest;
  const legacy = hilTestDraft(base);
  assert.equal(
    Object.hasOwn(hilTestSaveBody("project", legacy, base), "dut_profiles"),
    false,
  );
  const selected = hilTestDraft({ ...base, dut_profiles: profiles });
  assert.deepEqual(selected.dut_profiles, {
    baseline: hilProfileRef(profiles.baseline),
    faulted: hilProfileRef(profiles.faulted),
  });
  assert.notEqual(hilDraftSignature(legacy), hilDraftSignature(selected));
  const newer = profile("b", 500, 3, 3);
  assert.equal(selected.dut_profiles!.faulted!.revision, 2);
  assert.notEqual(selected.dut_profiles!.faulted!.sha256, newer.sha256);
});
test("a late side selection merges the current opposite side and cannot restore removed profile mode", () => {
  const current = {
    baseline: hilProfileRef(profiles.baseline),
    faulted: hilProfileRef(profile("b", 500, 8, 3)),
  };
  const next = hilUpdateProfileSelection(
    current,
    "baseline",
    hilProfileRef(profile("a", 300, 3, 2)),
  )!;
  assert.equal(next.faulted!.revision, 3);
  assert.equal(next.baseline!.revision, 2);
  assert.equal(
    hilUpdateProfileSelection(
      undefined,
      "baseline",
      hilProfileRef(profiles.baseline),
    ),
    undefined,
  );
});
test("sealed profile hashes verify canonical Korean records and reject altered policy or name", async () => {
  await verifyHilProfileHash(profiles.baseline);
  await assert.rejects(
    verifyHilProfileHash({ ...profiles.baseline, name: "modified" }),
    /SHA-256/,
  );
  assert.throws(
    () => parseHilDutProfile(profiles.baseline, "other"),
    /프로젝트/,
  );
});
test("same-fault mode verifies every profile echo and allows faulted A with retained policy labels", () => {
  const { summary, source, spec } = context();
  const valid = hilProfileComparison(summary, source, "project", spec);
  const traces = {
    baseline: parseHilTrace(trace("baseline"), "baseline"),
    faulted: parseHilTrace(trace("faulted"), "faulted"),
  };
  hilVerifyTracePair(traces, fault, valid.comparisonKind, valid.profiles);
  assert.deepEqual(traces.baseline.fault, traces.faulted.fault);
  assert.equal(traces.baseline.policy.maxObjectAgeMs, 250);
  assert.equal(traces.faulted.policy.maxObjectAgeMs, 500);
  assert.equal(traces.faulted.policy.recoveryFreshSteps, 8);
  assert.equal(
    hilSideLabel({ traces } as HilEvidence, "baseline"),
    "기준 정책 · r1",
  );
  assert.equal(
    hilSideLabel({ traces } as HilEvidence, "faulted"),
    "지연 허용 정책 · r2",
  );
});
test("mode, profile, project, side policy and fault tampering fail closed", () => {
  const { summary, source, spec } = context();
  assert.throws(
    () =>
      hilProfileComparison(
        summary,
        {
          ...source,
          dut_profiles: { ...profiles, faulted: profile("b", 500, 3, 3) },
        },
        "project",
        spec,
      ),
    /고정 DUT/,
  );
  assert.throws(
    () => hilProfileComparison(summary, source, "other", spec),
    /프로젝트/,
  );
  assert.throws(
    () =>
      hilProfileComparison(summary, source, "project", {
        ...spec,
        comparison_kind: "same_scene_fault_ab",
      }),
    /비교 모드/,
  );
  assert.throws(
    () =>
      hilProfileComparison(
        {
          ...summary,
          sides: { baseline: {}, faulted: { dut_profile: profiles.faulted } },
        },
        source,
        "project",
        spec,
      ),
    /고정 DUT/,
  );
  assert.throws(
    () =>
      parseHilTrace({ ...trace("faulted"), max_object_age_s: 0.25 }, "faulted"),
    /DUT 정책/,
  );
  assert.throws(
    () =>
      parseHilTrace(
        { ...trace("baseline"), dut_profile: undefined },
        "baseline",
      ),
    /DUT profile/,
  );
  const traces = {
    baseline: parseHilTrace(trace("baseline"), "baseline"),
    faulted: parseHilTrace(trace("faulted"), "faulted"),
  };
  assert.throws(
    () =>
      hilVerifyTracePair(
        { ...traces, baseline: { ...traces.baseline, fault: null } },
        fault,
        "same_fault_dut_ab",
        profiles,
      ),
    /고장·정책/,
  );
  assert.throws(
    () =>
      hilVerifyTracePair(
        traces,
        { ...fault, delay_ms: 300 },
        "same_fault_dut_ab",
        profiles,
      ),
    /고장·정책/,
  );
});
