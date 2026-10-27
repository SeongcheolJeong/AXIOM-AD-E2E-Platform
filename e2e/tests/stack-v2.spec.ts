import { test, expect } from "@playwright/test";
import { installLive } from "./carla-live-fixture";
import { v2Fixture } from "./stack-v2-fixture";

// Actual pure kernel calculations over authored samples, wrapped in authored
// native delivery records. These are UI/reader checks, never actual CARLA trials.
test("kernel 2 preserves interior unknown cells, far hits and positive surface without inventing free space", async ({
  page,
}, testInfo) => {
  const fixture = await v2Fixture(page.request, "gap", "v2_gap");
  await installLive(page, [fixture]);
  const panel = page.getByTestId("carla-live-verified");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("stack-v2-stages")).toBeVisible();
  await expect(panel.getByTestId("stack-coverage-grid")).toContainText(
    "1 / 39 cells qualified",
  );
  await expect(panel.getByTestId("stack-v2-qualified-prefix")).toHaveText(
    "0 m",
  );
  await expect(panel.getByTestId("stack-observed-distance")).toHaveText(
    "2.5 m",
  );
  await expect(panel.getByTestId("stack-nearest-point")).toHaveText(
    "5, 0, 0.7 m",
  );
  await expect(panel.getByTestId("stack-policy-mode")).toHaveText(
    "unknown stop",
  );
  await expect(panel.getByTestId("stack-control-brake")).toHaveText("1");
  const gap = panel.getByRole("button", {
    name: "Grid cell 0/0, unknown, 0 ground hits",
    exact: true,
  });
  await gap.focus();
  await gap.press("Enter");
  await expect(panel.getByTestId("stack-selected-grid-cell")).toContainText(
    "No retained hit extents",
  );
  await expect(panel.getByTestId("stack-unknown-region")).toHaveCount(38);
  await panel
    .getByRole("button", {
      name: "Grid cell 9/2, qualified, 2 ground hits",
      exact: true,
    })
    .click();
  await expect(panel.getByTestId("stack-selected-grid-cell")).toContainText(
    "12.125, 0.7, 0.01 m",
  );
  await expect(panel.getByTestId("stack-selected-grid-cell")).toContainText(
    "11.5 → 12.5 m",
  );
  await expect(panel.getByTestId("carla-live-points")).toContainText(
    "3 full points",
  );
  await expect(panel.getByTestId("stack-plan-state")).toContainText(
    "ready → unknown stop",
  );
  await panel
    .getByRole("button", {
      name: "Inspect plan state at sequence 39",
      exact: true,
    })
    .click();
  await expect(panel.getByTestId("stack-plan-state")).toContainText(
    "unknown stop → unknown stop",
  );
  await expect(
    panel.getByLabel("Live command interval", { exact: true }),
  ).toHaveValue("39");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await expect
    .poll(() =>
      page
        .locator(".sidebar")
        .evaluate((element) => element.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  await panel.getByTestId("stack-coverage-grid").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("kernel2-unknown-grid-mobile.png"),
  });
});

test("stored S-curve plan shows phases, envelope, state and missing acceleration as a gap", async ({
  page,
}, testInfo) => {
  const fixture = await v2Fixture(page.request, "plan", "v2_plan");
  await installLive(page, [fixture]);
  const panel = page.getByTestId("carla-live-verified");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("stack-policy-mode")).toHaveText(
    "controlled stop",
  );
  await expect(panel.getByTestId("stack-stop-plan")).toContainText(
    "Ramp back to zero 1 s",
  );
  await expect(panel.getByTestId("stack-stop-plan")).toContainText(
    "not a measured value",
  );
  await expect(panel.getByTestId("stack-stopping-envelope")).toContainText(
    "Available after reserve",
  );
  await expect(panel.getByTestId("stack-stopping-bound-model")).toContainText(
    "releases it under the jerk limit",
  );
  await expect(
    panel.getByRole("img", {
      name: "Assumed stop-plan acceleration phases",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel.locator('[data-series="Observed speed difference / dt"]'),
  ).toHaveAttribute("data-valid-points", "39");
  await panel.getByLabel("Live command interval", { exact: true }).fill("25");
  await expect(panel.getByTestId("stack-plan-state")).toContainText(
    "controlled stop → controlled stop",
  );
  await expect(panel.getByTestId("stack-stop-plan")).toContainText(
    "elapsed 1.25 s",
  );
  await expect(panel.getByTestId("stack-control-application")).toContainText(
    "KERNEL REQUEST",
  );
  await panel
    .getByTestId("stack-v2-stages")
    .screenshot({ path: testInfo.outputPath("kernel2-plan-desktop.png") });
});

test("unknown brake response retains valid sample geometry while denying motion", async ({
  page,
}) => {
  const fixture = await v2Fixture(
    page.request,
    "unconfigured-brake",
    "v2_no_brake_assumption",
  );
  await installLive(page, [fixture]);
  const panel = page.getByTestId("carla-live-verified");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("stack-observation-status")).toHaveText(
    "sampled · valid input",
  );
  await expect(panel.getByTestId("stack-coverage-grid")).toContainText(
    "39 / 39 cells qualified",
  );
  await expect(panel.getByTestId("stack-nearest-point")).toHaveText(
    "7.5, 0, 0.7 m",
  );
  await expect(panel.getByTestId("stack-policy-mode")).toHaveText(
    "unknown stop",
  );
  await expect(panel.getByTestId("stack-control-brake")).toHaveText("1");
  await expect(panel.getByTestId("stack-target-speed")).toHaveText("0 m/s");
  await expect(panel.getByTestId("stack-plan-state")).toContainText(
    "ready → unknown stop",
  );
});

for (const violation of [
  "kernel",
  "gap",
  "old-adapter",
  "unknown-regions",
  "point-extents",
  "state-prefix",
] as const) {
  test(`saved kernel 2 rejects ${violation} mismatch before displaying geometry`, async ({
    page,
  }) => {
    const fixture = await v2Fixture(
      page.request,
      "gap",
      `v2_reject_${violation}`,
    );
    if (violation === "kernel")
      fixture.trace.ticks[0].output.kernel_version = "stack-kernel/1";
    if (violation === "gap")
      fixture.trace.ticks[0].output.observation.coverage.qualified_prefix_m = 10;
    if (violation === "old-adapter")
      fixture.run.provenance.adapter_version =
        fixture.manifest.worker_adapter_version = "0.6.0";
    if (violation === "unknown-regions")
      fixture.trace.ticks[0].output.observation.coverage.unknown_regions.pop();
    if (violation === "point-extents")
      fixture.trace.ticks[0].output.observation.coverage.cells[29].point_max_xyz_m[0] = 100;
    if (violation === "state-prefix") {
      const output = fixture.trace.ticks[1].output;
      output.state_before.previous_speed_mps = 1;
      output.decision.observed_speed_acceleration_mps2 = 20;
    }
    fixture.refresh();
    await installLive(page, [fixture]);
    await expect(page.getByTestId("carla-live-inspector")).toContainText(
      "Live evidence rejected",
    );
    await expect(page.getByTestId("stack-coverage-grid")).toHaveCount(0);
    await expect(page.getByTestId("carla-live-verified")).toHaveCount(0);
  });
}
