import test from "node:test";
import assert from "node:assert/strict";
import { demoCaseCounts, demoCaseReady, demoCatalogError, filterDemoCases, scopedDemoLink } from "../src/components/demo-cases/demo-cases.ts";
import type { DemoCase, DemoCaseCatalog } from "../src/components/demo-cases/demo-cases.ts";

// Authored API fixtures check navigation/identity only; they are not simulator evidence.
function example(id = "object_lead_wait", execution = "job_a"): DemoCase {
  return {
    id, module: "object_sim", title: "Example", question: "Why did it wait?",
    change: { label: "Wait", before: 0, after: 6, unit: "s" }, observation: "Fixture observation", next_action: "Inspect", limits: ["Fixture"],
    ready: true, status: "ready", reason: null, source_family: { id: "family_a", label: "Example source", kind: "synthetic_carla" },
    executions: [{ role: "baseline", id: execution, project_id: "demo", input_hash: "a".repeat(64), manifest_hash: "b".repeat(64), configuration_hash: null, adapter_id: "example", adapter_version: "v1", fidelity: "fixture" }],
    primary_link: { label: "Compare", href: `#object-sim?project=demo&left=${execution}&demo_case=${id}` }, links: [], key_moments: [], binding: null,
  };
}
const catalog = (items = [example()]): DemoCaseCatalog => ({ schema_version: "demo-cases/1", project_id: "demo", collection: "carla_external", items, counts: { templates: items.length, ready: items.length, unavailable: 0, unique_executions: 1, source_families: 1 }, source_families: [], limits: [] });

test("a previous project's response or execution is rejected before rendering", () => {
  assert.equal(demoCatalogError(catalog(), "demo"), null);
  assert.match(demoCatalogError(catalog(), "other")!, /작업공간/);
  const mixed = example(); mixed.executions[0].project_id = "other";
  assert.match(demoCatalogError(catalog([mixed]), "demo")!, /작업공간/);
  assert.equal(demoCaseReady(mixed, "demo"), false);
  assert.match(demoCatalogError({ ...catalog(), schema_version: "demo-cases/2" } as unknown as DemoCaseCatalog, "demo")!, /버전/);
});

test("case navigation retains the exact project and key moment without allowing external or ambiguous links", () => {
  const href = "#hil-sim?project=demo&execution=job_a&time_s=3.0&demo_case=hil_delay_200";
  assert.equal(scopedDemoLink(href, "demo"), href);
  assert.equal(scopedDemoLink("#neural-sim?project=a%20b&demo_case=neural_frozen_sweep", "a b"), "#neural-sim?project=a%20b&demo_case=neural_frozen_sweep");
  for (const wrong of ["https://example.com/?project=demo", "javascript:alert(1)", "#hil-sim", "#hil-sim?project=other", "#hil-sim?project=demo&project=other", "#settings?project=demo", "#object-sim?project=demo#other"]) assert.equal(scopedDemoLink(wrong, "demo"), null);
});

test("ready filters require pinned evidence and a valid launch, not merely the server label", () => {
  const ready = example();
  const missing = { ...example("hil_delay_400"), ready: false, status: "unavailable", primary_link: null } as DemoCase;
  const wrongLink = { ...example("sensor_driving_fov"), primary_link: { label: "Compare", href: "#sensor-sim?project=other" } };
  const noManifest = example("hil_delay_200"); noManifest.executions[0].manifest_hash = "";
  assert.deepEqual(filterDemoCases([ready, missing, wrongLink, noManifest], "demo", "all", true), [ready]);
  assert.deepEqual(filterDemoCases([ready, missing], "demo", "respond", false), [missing]);
  assert.deepEqual(filterDemoCases([ready, missing], "demo", "respond", true), []);
});

test("comparison cases do not inflate unique source or execution counts", () => {
  const first = example();
  const reused = example("log_lead_wait");
  const secondExecution = example("object_gap_release", "job_b");
  const pending = { ...example("hil_delay_400", "job_missing"), ready: false, status: "unavailable" } as DemoCase;
  assert.deepEqual(demoCaseCounts([first, reused, secondExecution, pending], "demo"), { ready: 3, executions: 2, sourceFamilies: 1 });
  assert.deepEqual(demoCaseCounts([], "demo"), { ready: 0, executions: 0, sourceFamilies: 0 });
});
