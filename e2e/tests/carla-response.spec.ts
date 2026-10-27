import { test, expect, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { digest, installLive } from "./carla-live-fixture";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
async function nativeFixture(info: TestInfo, name: string) {
  const directory = info.outputPath(`native-response-${name}`);
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"), [path.join(root, "e2e/fixtures/native_live_fixture.py"), directory, name], { cwd: root, timeout: 30000 });
  const run = JSON.parse(await readFile(path.join(directory, "browser-run.json"), "utf8"));
  const files = new Map<string, Buffer>();
  for (const artifact of run.artifacts) files.set(artifact.name, await readFile(path.join(directory, artifact.name)));
  const response = JSON.parse(files.get("carla-vehicle-response.json")!.toString());
  const trace = JSON.parse(files.get("carla-stack-trace.json")!.toString());
  const physics = JSON.parse(files.get("carla-stack-physics.json")!.toString());
  const manifest = JSON.parse(files.get("carla-stack-manifest.json")!.toString());
  const update = (name: string, object: unknown) => {
    const bytes = Buffer.from(JSON.stringify(object)); files.set(name, bytes);
    Object.assign(run.artifacts.find((item: any) => item.name === name), { size_bytes: bytes.length, sha256: digest(bytes) });
  };
  const refresh = () => {
    update("carla-vehicle-response.json", response); update("carla-stack-trace.json", trace); update("carla-stack-physics.json", physics);
    manifest.artifacts = run.artifacts.filter((item: any) => item.name !== "carla-stack-manifest.json").map(({ name, kind, sha256, size_bytes }: any) => ({ name, kind, sha256, size_bytes }));
    update("carla-stack-manifest.json", manifest);
  };
  return { run, files, response, trace, physics, manifest, refresh };
}

test("native bridge over a mocked world preserves response boundaries, all frame samples, undefined derivatives and exact downloads", async ({ page }, info) => {
  const fixture = await nativeFixture(info, "response-base"), errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installLive(page, [fixture] as any);
  const panel = page.getByTestId("carla-response-verified");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("81 / 81 measured observations");
  await expect(panel.getByTestId("carla-response-friction")).toContainText("ego matched / lead matched");
  await expect(panel.getByTestId("carla-response-boundaries")).toContainText("ACK unknown");
  await expect(panel.locator('[data-series="Existing 3D speed norm"]')).toHaveAttribute("data-valid-points", "81");
  await expect(panel.locator('[data-series="Finite difference of signed heading speed"]')).toHaveAttribute("data-valid-points", "80");
  await panel.getByLabel("Vehicle response observation", { exact: true }).fill("80");
  await expect(panel.getByTestId("carla-response-command")).toContainText("Final observation · no outgoing command scheduled");
  await expect(panel.getByTestId("carla-response-selected")).toContainText(`Frame ${fixture.response.observations[80].world_frame}`);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download verified vehicle response JSON" }).click();
  expect(await readFile((await (await download).path())!)).toEqual(fixture.files.get("carla-vehicle-response.json"));
  await panel.screenshot({ path: info.outputPath("native-mocked-response-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await panel.getByLabel("Vehicle response observation", { exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(panel.getByLabel("Vehicle response observation", { exact: true })).toHaveValue("79");
  await expect(panel.getByTestId("carla-response-command")).toContainText("next frame observed");
  await panel.getByLabel("Vehicle response actor", { exact: true }).selectOption("lead");
  await expect(panel.getByTestId("carla-response-selected")).toContainText("lead actor");
  await panel.screenshot({ path: info.outputPath("native-mocked-response-mobile.png") });
  expect(errors).toEqual([]);
});

test("authored signed motion and nonfinite wheel fields remain separate from norms and genuine zero observations", async ({ page }, info) => {
  const fixture = await nativeFixture(info, "response-signed"), row = fixture.response.observations[1], speed = Math.sqrt(5);
  // Deliberately authored chart inputs layered on a native mocked-world fixture.
  // This test is a display/identity contract, never a claim of CARLA dynamics.
  row.actors.ego.velocity_mps = { x: -2, y: 0, z: 1 };
  row.actors.ego.acceleration_mps2 = { x: -3, y: 0, z: 4 };
  fixture.physics.observations[1].frame.ego.speed_mps = speed;
  fixture.trace.ticks[1].packet.ego.speed_mps = speed;
  fixture.trace.ticks[0].next_ego.speed_mps = speed;
  const available = (value: number) => ({ status: "available", value, reason: null });
  row.actors.ego.telemetry = { source_api: "vehicle.get_telemetry_data", interpretation: "raw_api_fields_units_unverified", before_world_frame: row.world_frame, after_world_frame: row.world_frame, status: "available", error: null, scalars: { speed: available(-2), engine_rpm: available(120), gear: available(0) }, wheels: [0, 1, 2, 3].map((index) => ({ index, fields: { omega: index === 0 ? { status: "nonfinite", value: null, reason: "Authored nonfinite API observation" } : available(0), long_slip: available(0), lat_slip: available(0), tire_friction: available(.8) } })) };
  fixture.refresh();
  await installLive(page, [fixture] as any);
  const panel = page.getByTestId("carla-response-verified");
  await expect(panel).toBeVisible();
  await panel.getByLabel("Vehicle response observation", { exact: true }).fill("1");
  await expect(panel.getByTestId("carla-response-selected")).toContainText("-2.000 m/s");
  await expect(panel.getByTestId("carla-response-selected")).toContainText("2.236 m/s");
  await panel.locator("summary", { hasText: "Wheel and drivetrain API observations" }).click();
  await expect(panel).toContainText("nonfinite · Authored nonfinite API observation");
  await expect(panel.locator('[data-series="Wheel 0"]')).toHaveAttribute("data-valid-points", "0");
  await expect(panel.locator('[data-series="Wheel 1"]')).toHaveAttribute("data-valid-points", "1");
  await expect(panel.locator('[data-series="engine_rpm"]')).toHaveAttribute("data-valid-points", "1");
});

test("response hash, context, friction gate, RPC frame and raw physics mismatch fail closed", async ({ page }, info) => {
  const fixtures = [];
  for (const kind of ["hash", "context", "friction", "rpc", "control", "raw", "rest"]) {
    const fixture = await nativeFixture(info, `response-bad-${kind}`);
    if (kind === "context") fixture.response.context_hash = "f".repeat(64);
    if (kind === "friction") fixture.response.physics_configuration.ego.wheels[0].fields.tire_friction.value = 3.5;
    if (kind === "raw") fixture.response.observations[1].actors.ego.position_m.x += 1;
    if (kind === "rest") fixture.response.after_rest.actors.ego.position_m.z += 1;
    if (kind === "rpc") fixture.response.observations[1].actors.ego.reported_control = { status: "available", before_world_frame: 0, after_world_frame: 0, error: null, data: { throttle: 0, brake: 1, steer: 0, hand_brake: false, reverse: false, manual_gear_shift: false, gear: 0 } };
    if (kind === "control") { const frame = fixture.response.observations[1].world_frame; fixture.response.observations[1].actors.ego.reported_control = { status: "available", before_world_frame: frame, after_world_frame: frame, error: null, data: { throttle: 0, brake: 1, steer: 0, hand_brake: false, reverse: false, manual_gear_shift: false, gear: 0 } }; }
    fixture.refresh();
    if (kind === "hash") fixture.run.artifacts.find((item: any) => item.kind === "vehicle_response").sha256 = "f".repeat(64);
    fixtures.push(fixture);
  }
  await installLive(page, fixtures as any);
  for (const fixture of fixtures) {
    await page.getByLabel("Select execution", { exact: true }).selectOption(fixture.run.id);
    await expect(page.getByTestId("carla-response-inspector").getByRole("alert")).toContainText("Vehicle response evidence rejected");
    await expect(page.getByTestId("carla-response-verified")).not.toBeVisible();
  }
});

test("older response-unrecorded runs and slow or failed selections never inherit another run's response", async ({ page }, info) => {
  const good = await nativeFixture(info, "response-good"), slow = await nativeFixture(info, "response-slow"), failed = await nativeFixture(info, "response-failed"), older = await nativeFixture(info, "response-older");
  older.run.artifacts = older.run.artifacts.filter((item: any) => item.kind !== "vehicle_response");
  older.run.provenance.adapter_version = "0.4.0";
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await installLive(page, [good, slow, failed, older] as any);
  const panel = page.getByTestId("carla-response-verified"), select = page.getByLabel("Select execution", { exact: true });
  await expect(panel).toBeVisible();
  await select.selectOption(older.run.id);
  await expect(page.getByTestId("carla-response-unrecorded")).toContainText("Unrecorded for this run");
  await expect(panel).not.toBeVisible();
  await select.selectOption(good.run.id); await expect(panel).toBeVisible();
  let release = () => {}; const barrier = new Promise<void>((resolve) => { release = resolve; });
  const artifact = slow.run.artifacts.find((item: any) => item.kind === "vehicle_response");
  await page.route(`**${artifact.download_url}`, async (route) => { await barrier; await route.fulfill({ contentType: "application/json", body: slow.files.get(artifact.name)! }).catch(() => {}); });
  await select.selectOption(slow.run.id); await expect(panel).not.toBeVisible();
  await page.route(`**/api/v1/runs/${failed.run.id}`, (route) => route.fulfill({ status: 503, json: { detail: "Authored lookup failure" } }));
  await select.selectOption(failed.run.id); release();
  await expect(page.getByText("Execution unavailable", { exact: true })).toBeVisible();
  await expect(page.getByTestId("carla-response-inspector")).not.toBeVisible();
  await select.selectOption(good.run.id); await expect(panel).toBeVisible();
  await expect(page.getByTestId("carla-response-inspector")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("an authored partial friction mismatch is inspectable with zero measured observations and no injected motion", async ({ page }, info) => {
  const fixture = await nativeFixture(info, "response-friction-stop");
  fixture.run.status = "error"; fixture.run.verdict = "error"; fixture.run.trajectory = [];
  const message = "Authored ego friction readback mismatch; no velocity injection";
  Object.assign(fixture.response, { status: "partial", error: message, observations: [], initial_velocity_requests: [] });
  fixture.response.physics_configuration.ego.friction_status = "mismatch";
  fixture.response.physics_configuration.ego.wheels[0].fields.tire_friction.value = 3.5;
  Object.assign(fixture.physics, { status: "partial", observations: [] });
  fixture.trace.status = "partial";
  fixture.trace.ticks = fixture.trace.ticks.map((tick: any) => ({ schema_version: tick.schema_version, sequence: tick.sequence, status: "not_attempted", source_world_frame: null, source_time_s: null, packet: null, output: null, source_refs: { lidar: null, ego: null }, points: null, requested_carla_control: null, control_acknowledged: false, control_rpc_response: null, next_world_frame: null, next_time_s: null, observed_control: null, next_ego: null, application_status: "not_sent", error: null }));
  Object.assign(fixture.manifest, { status: "partial", error: message, failure_phase: "post_rest_physics_readback", observed_frames: 0, retained_source_observations: 0, acknowledged_commands: 0, next_frame_observed_commands: 0 });
  fixture.refresh();
  await installLive(page, [fixture] as any);
  const panel = page.getByTestId("carla-response-verified");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("0 / 81 measured observations");
  await expect(panel.getByTestId("carla-response-friction")).toContainText("ego mismatch / lead matched");
  await expect(panel).toContainText("No request retained");
  await expect(panel.getByRole("img", { name: "Velocity components by simulation time" })).not.toBeVisible();
  await expect(panel).toContainText(message);
});

test("negative acceleration axis labels retain the minus sign inside the SVG at desktop and mobile widths", async ({ page }, info) => {
  const fixtures = [];
  for (const [name, acceleration] of [["actual-sized", -39.159], ["scientific", -9.999e100]] as const) {
    const fixture = await nativeFixture(info, `response-axis-${name}`);
    // Authored display regression at the actual observed label width, plus a
    // longer scientific label. These are not new CARLA physics measurements.
    fixture.response.observations[1].actors.ego.acceleration_mps2.x = acceleration;
    fixture.refresh();
    fixtures.push(fixture);
  }
  await installLive(page, fixtures as any);
  for (const [index, fixture] of fixtures.entries()) {
    await page.getByLabel("Select execution", { exact: true }).selectOption(fixture.run.id);
    const chart = page.getByTestId("carla-response-verified").getByRole("img", { name: "Acceleration sources by simulation time", exact: true });
    const lower = chart.locator('[data-axis="y-min"]');
    await expect(lower).toHaveText(index === 0 ? "-39.159" : "-9.999e+100");
    await page.evaluate(() => document.fonts.ready);
    for (const viewport of [{ width: 1440, height: 1080 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      const bounds = await lower.evaluate((element) => {
        const text = element.getBoundingClientRect(), svg = element.closest("svg")!.getBoundingClientRect();
        return { labelLeft: text.left, labelRight: text.right, svgLeft: svg.left, svgRight: svg.right };
      });
      expect(bounds.labelLeft).toBeGreaterThanOrEqual(bounds.svgLeft);
      expect(bounds.labelRight).toBeLessThanOrEqual(bounds.svgRight);
    }
  }
});

test("vehicle response supports explicit 0.4.1 through 0.8.0 contracts and rejects unknown or mismatched versions", async ({ page }, info) => {
  const fixtures = [];
  for (const version of ["0.4.1", "0.5.0", "0.6.0", "0.7.0", "0.8.0", "0.9.0", "mismatch"]) {
    const fixture = await nativeFixture(info, `response-version-${version}`);
    // Version labels are authored over fixed native/mock-world bytes, not
    // evidence that multiple actual CARLA workers were executed here.
    const runVersion = version === "mismatch" ? "0.6.0" : version;
    fixture.run.provenance.adapter_version = runVersion;
    fixture.manifest.worker_adapter_version = runVersion;
    fixture.response.worker_adapter_version = version === "mismatch" ? "0.5.0" : version;
    const startup = JSON.parse(fixture.files.get("carla-startup.json")!.toString());
    startup.context.adapter_version = runVersion;
    const bytes = Buffer.from(JSON.stringify(startup));
    fixture.files.set("carla-startup.json", bytes);
    Object.assign(fixture.run.artifacts.find((item: any) => item.name === "carla-startup.json"), { size_bytes: bytes.length, sha256: digest(bytes) });
    fixture.refresh(); fixtures.push(fixture);
  }
  await installLive(page, fixtures as any);
  for (const [index, fixture] of fixtures.entries()) {
    await page.getByLabel("Select execution", { exact: true }).selectOption(fixture.run.id);
    if (index < 5) {
      await expect(page.getByTestId("carla-response-verified")).toContainText("81 / 81 measured observations");
      await expect(page.getByTestId("carla-response-verified")).toContainText(fixture.run.provenance.adapter_version);
    } else {
      await expect(page.getByTestId("carla-response-inspector").getByRole("alert")).toContainText("Vehicle response evidence rejected");
      await expect(page.getByTestId("carla-response-verified")).not.toBeVisible();
    }
  }
});
