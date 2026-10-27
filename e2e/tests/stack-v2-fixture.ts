import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { APIRequestContext } from "@playwright/test";
import { authoredContext, digest, liveFixture } from "./carla-live-fixture";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

/** Pure kernel outputs plus authored native delivery envelope; no CARLA is run. */
export async function v2Fixture(
  request: APIRequestContext,
  kind: "gap" | "plan" | "unconfigured-brake",
  suffix: string,
) {
  const generated = JSON.parse(
    execFileSync(
      process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
      [path.join(root, "e2e/fixtures/stack_v2_fixture.py"), kind],
      { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
    ),
  );
  const input = await authoredContext(request);
  const stackResponse = await request.post("/api/v1/stacks", {
    data: {
      name: `Kernel 2 browser ${kind} ${suffix}`,
      description: generated.scope,
      note: "Authored browser inputs and native delivery envelope; no CARLA was run",
      definition: generated.definition,
    },
  });
  if (stackResponse.status() !== 201)
    throw new Error(await stackResponse.text());
  input.context.stack_snapshot = await stackResponse.json();
  const fixture = liveFixture(input, suffix);
  fixture.run.provenance.adapter_version = "0.7.0";
  fixture.manifest.worker_adapter_version = "0.7.0";
  const points = Buffer.from(generated.points_base64, "base64"),
    raw = Buffer.from(generated.raw_xyzi_base64, "base64");
  for (const [index, tick] of fixture.trace.ticks.entries()) {
    const sourceHash = tick.source_refs.lidar.message_sha256;
    tick.packet = generated.ticks[index].packet;
    tick.packet.lidar.source_message_sha256 = sourceHash;
    tick.output = generated.ticks[index].output;
    tick.points = {
      ...tick.points,
      offset_bytes: index * points.length,
      size_bytes: points.length,
      point_count: points.length / 12,
      sha256: digest(points),
    };
    tick.source_refs.lidar.raw_carla_xyzi_sha256 = digest(raw);
    tick.source_refs.lidar.raw_points = {
      ...tick.source_refs.lidar.raw_points,
      offset_bytes: index * raw.length,
      size_bytes: raw.length,
      sha256: digest(raw),
    };
    tick.requested_carla_control = {
      ...tick.output.control,
      steer: -tick.output.control.steer,
    };
    delete tick.requested_carla_control.steering_convention;
    tick.next_ego.speed_mps =
      generated.ticks[Math.min(index + 1, 39)].packet.ego.speed_mps;
  }
  const baseRefresh = fixture.refresh;
  fixture.refresh = () => {
    baseRefresh();
    for (const [name, bytes] of [
      ["carla-stack-points.bin", Buffer.concat(Array(41).fill(points))],
      ["carla-stack-raw-xyzi.bin", Buffer.concat(Array(41).fill(raw))],
    ] as const) {
      fixture.files.set(name, bytes);
      const descriptor = fixture.run.artifacts.find(
        (item: any) => item.name === name,
      );
      descriptor.sha256 = digest(bytes);
      descriptor.size_bytes = bytes.length;
    }
    fixture.manifest.artifacts = fixture.run.artifacts
      .filter((item: any) => item.name !== "carla-stack-manifest.json")
      .map(({ name, kind, sha256, size_bytes }: any) => ({
        name,
        kind,
        sha256,
        size_bytes,
      }));
    const bytes = Buffer.from(JSON.stringify(fixture.manifest));
    fixture.files.set("carla-stack-manifest.json", bytes);
    const descriptor = fixture.run.artifacts.find(
      (item: any) => item.name === "carla-stack-manifest.json",
    );
    descriptor.sha256 = digest(bytes);
    descriptor.size_bytes = bytes.length;
  };
  fixture.refresh();
  return fixture;
}
