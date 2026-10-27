import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { digest, type LiveFixture } from "./carla-live-fixture";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
/** Actual optional adapter code over an authored in-memory world, never a server. */
export function configuredSensorFixture(kind = "complete"): LiveFixture {
  const generated = JSON.parse(
    execFileSync(
      process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
      [path.join(root, "e2e/fixtures/configurable_sensor_fixture.py"), kind],
      { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 },
    ),
  );
  const r = generated.request;
  const files = new Map<string, Buffer>(
    generated.payloads.map((p: any) => [
      p.name,
      Buffer.from(p.base64, "base64"),
    ]),
  );
  const run: any = {
    id: r.execution_id,
    scenario_id: r.scenario.id,
    scenario_name: r.scenario.name,
    scenario_revision: r.scenario.revision,
    adapter_id: "carla",
    status: generated.error ? "error" : "completed",
    verdict: generated.error ? "error" : "failed",
    created_at: r.scenario.created_at,
    duration_s: 4,
    execution_ms: 1,
    metrics: generated.metrics,
    provenance: {
      input_hash: r.input_hash,
      output_hash: "d".repeat(64),
      seed: r.scenario.seed,
      adapter_version: "0.8.0",
      model_fidelity: "AUTHORED FAKE-WORLD BROWSER FIXTURE · not actual CARLA",
    },
    scenario_snapshot: r.scenario,
    execution_context: r.context,
    trajectory: generated.trajectory,
    events: [],
    artifacts: [],
  };
  const trace = JSON.parse(files.get("carla-stack-trace.json")!.toString()),
    manifest = JSON.parse(files.get("carla-stack-manifest.json")!.toString());
  function refresh() {
    run.artifacts = generated.payloads
      .filter((p: any) => p.name !== "carla-stack-manifest.json")
      .map((p: any, index: number) => ({
        id: `artifact_configured_${kind}_${index}`,
        run_id: run.id,
        name: p.name,
        kind: p.kind,
        media_type: p.media_type,
        size_bytes: files.get(p.name)!.length,
        sha256: digest(files.get(p.name)!),
        created_at: run.created_at,
        download_url: `/api/v1/artifacts/artifact_configured_${kind}_${index}/download`,
      }));
    manifest.artifacts = run.artifacts.map(
      ({ name, kind, sha256, size_bytes }: any) => ({
        name,
        kind,
        sha256,
        size_bytes,
      }),
    );
    const bytes = Buffer.from(JSON.stringify(manifest));
    files.set("carla-stack-manifest.json", bytes);
    run.artifacts.push({
      id: `artifact_configured_${kind}_manifest`,
      run_id: run.id,
      name: "carla-stack-manifest.json",
      kind: "live_stack_manifest",
      media_type: "application/json",
      size_bytes: bytes.length,
      sha256: digest(bytes),
      created_at: run.created_at,
      download_url: `/api/v1/artifacts/artifact_configured_${kind}_manifest/download`,
    });
  }
  refresh();
  return { run, files, trace, manifest, refresh };
}
