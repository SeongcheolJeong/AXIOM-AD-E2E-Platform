import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";

// Authored catalog and saved-declaration browser contracts. No component plugin,
// recorded replay, or CARLA simulation is executed by this test fixture.
async function authoringFixture(page: Page, withSaved = false) {
  const original = await (
    await page.request.get("/api/v1/stack-components")
  ).json();
  const v1 = ["lidar-corridor", "stopping-headway", "proportional-control"].map(
    (id) => {
      const matches = original.filter(
        (item: any) => item.id === id && item.version === "1",
      );
      expect(matches).toHaveLength(1);
      return structuredClone(matches[0]);
    },
  );
  const controller2 = {
    ...structuredClone(v1[2]),
    version: "2",
    name: "Authored alternative controller",
    parameter_schema: {
      type: "object",
      properties: {
        alternative_gain: {
          type: "number",
          title: "Alternative gain",
          minimum: 0,
          maximum: 1,
        },
      },
      required: ["alternative_gain"],
      additionalProperties: false,
    },
    default_parameters: { alternative_gain: 0.7 },
    implementation: {
      package: "authored-browser-fixture",
      algorithm: "No controller execution",
    },
  };
  let catalog = [controller2, ...v1]; // v2 first deliberately changes the default for new drafts only.
  const revisions: any[] = [];
  const submissions: { path: string; body: any }[] = [];
  function snapshot(body: any, revision: number) {
    const { expected_revision: _expected, ...definition } =
      structuredClone(body);
    const value = {
      ...definition,
      schema_version: "1",
      id: "stack_authored_component_versions",
      revision,
      created_at: "2026-09-13T00:00:00Z",
      kernel_version:
        definition.definition.schema_version === "2"
          ? "stack-kernel/2"
          : "stack-kernel/1",
      component_metadata: ["perception", "policy", "controller"].map((role) => {
        const selection = definition.definition[role];
        return structuredClone(
          catalog.find(
            (item: any) =>
              item.role === role &&
              item.id === selection.id &&
              item.version === selection.version,
          ),
        );
      }),
    };
    const bytes = Buffer.from(JSON.stringify(value));
    return {
      ...value,
      manifest_hash: createHash("sha256").update(bytes).digest("hex"),
      manifest_size_bytes: bytes.length,
      download_url: `/api/v1/stacks/${value.id}/export?revision=${revision}`,
    };
  }
  if (withSaved)
    revisions.push(
      snapshot(
        {
          name: "Authored old controller version",
          description: "Saved declaration only; no controller execution",
          note: "Version 2 remains pinned even when the installed catalog changes",
          definition: {
            schema_version: "1",
            reference_frame: "ego",
            ...Object.fromEntries(
              [v1[0], v1[1], controller2].map((item: any) => [
                item.role,
                {
                  id: item.id,
                  version: item.version,
                  parameters: structuredClone(item.default_parameters),
                },
              ]),
            ),
          },
        },
        1,
      ),
    );
  await page.route("**/api/v1/stack-components", (route) =>
    route.fulfill({ json: catalog }),
  );
  await page.route(
    /\/api\/v1\/stacks(?:\/[^/?]+(?:\/revisions)?)?(?:\?.*)?$/,
    async (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        submissions.push({ path: url.pathname, body });
        const saved = snapshot(body, revisions.length + 1);
        revisions.push(saved);
        return route.fulfill({ status: 201, json: saved });
      }
      if (url.pathname === "/api/v1/stacks")
        return route.fulfill({
          json: revisions.length ? [revisions[revisions.length - 1]] : [],
        });
      if (url.pathname.endsWith("/revisions"))
        return route.fulfill({ json: [...revisions].reverse() });
      const revision = Number(
        url.searchParams.get("revision") ?? revisions.length,
      );
      return route.fulfill({
        json: revisions.find((item) => item.revision === revision),
      });
    },
  );
  await page.goto("/#stacks");
  return {
    submissions,
    revisions,
    v1,
    controller2,
    setCatalog: (values: any[]) => {
      catalog = values;
    },
  };
}

test("kernel 2 is explicit and switching kernels resets all draft components without upgrading legacy revisions", async ({
  page,
}) => {
  const fixture = await authoringFixture(page);
  const v2 = fixture.v1.map((item: any) => ({
    ...structuredClone(item),
    version: "2",
    name: `Authored kernel 2 ${item.role}`,
    implementation: {
      package: "authored-browser-fixture",
      kernel: "stack-kernel/2",
    },
  }));
  fixture.setCatalog([...v2, ...fixture.v1]);
  await page.reload();
  await page.getByRole("button", { name: "New stack", exact: true }).click();
  const editor = page.getByRole("dialog", {
    name: "Create component stack",
    exact: true,
  });
  await expect(editor.getByLabel("Stack kernel", { exact: true })).toHaveValue(
    "stack-kernel/1",
  );
  for (const item of fixture.v1) {
    const selection = editor.getByLabel(`Stack ${item.role} component`, {
      exact: true,
    });
    await expect(selection).toHaveValue(`${item.id}\u00001`);
    await expect(selection.locator("option")).toHaveCount(1);
  }
  await editor
    .getByLabel("Stack policy time_headway_s", { exact: true })
    .fill("9");
  await editor
    .getByLabel("Stack kernel", { exact: true })
    .selectOption("stack-kernel/2");
  for (const item of v2)
    await expect(
      editor.getByLabel(`Stack ${item.role} component`, { exact: true }),
    ).toHaveValue(`${item.id}\u00002`);
  await expect(
    editor.getByLabel("Stack policy time_headway_s", { exact: true }),
  ).toHaveValue("1.5");
  await editor
    .getByLabel("Stack name", { exact: true })
    .fill("Authored explicit kernel 2");
  await editor
    .getByLabel("Stack revision note", { exact: true })
    .fill("Explicit kernel selection; no algorithm execution");
  await editor
    .getByRole("button", { name: "Publish stack", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  expect(fixture.submissions[0].body.definition.schema_version).toBe("2");
  expect(fixture.submissions[0].body.definition.kernel_version).toBe(
    "stack-kernel/2",
  );
  for (const role of ["perception", "policy", "controller"])
    expect(fixture.submissions[0].body.definition[role].version).toBe("2");
  const original = structuredClone(fixture.revisions[0]);
  await page
    .getByRole("button", { name: "Create stack revision", exact: true })
    .click();
  const revision = page.getByRole("dialog", {
    name: "Create stack revision",
    exact: true,
  });
  await expect(
    revision.getByLabel("Stack kernel", { exact: true }),
  ).toHaveValue("stack-kernel/2");
  await revision
    .getByLabel("Stack kernel", { exact: true })
    .selectOption("stack-kernel/1");
  await revision
    .getByRole("button", { name: "Publish stack revision", exact: true })
    .click();
  await expect(revision).not.toBeVisible();
  expect(fixture.submissions[1].body.definition.schema_version).toBe("1");
  expect(fixture.submissions[1].body.definition).not.toHaveProperty(
    "kernel_version",
  );
  expect(fixture.revisions[0]).toEqual(original);
  // A partial kernel catalog cannot authorize a new draft or the old saved kernel.
  fixture.setCatalog([
    ...fixture.v1,
    ...v2.filter((item: any) => item.role !== "policy"),
  ]);
  await page.getByRole("button", { name: "Refresh stacks workspace" }).click();
  await page
    .getByLabel("Component stack revision", { exact: true })
    .selectOption("1");
  await page
    .getByRole("button", { name: "Create stack revision", exact: true })
    .click();
  await expect(
    revision.getByLabel("Stack kernel", { exact: true }),
  ).toHaveValue("stack-kernel/2");
  await expect(
    revision
      .getByLabel("Stack kernel", { exact: true })
      .locator('option[value="stack-kernel/2"]'),
  ).toHaveJSProperty("disabled", true);
  await expect(
    revision.getByRole("button", {
      name: "Publish stack revision",
      exact: true,
    }),
  ).toBeDisabled();
  expect(fixture.submissions).toHaveLength(2);
});

test("same component ID versions keep separate schemas, defaults and exact authoring/revision bodies", async ({
  page,
}) => {
  const fixture = await authoringFixture(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "New stack", exact: true }).click();
  const editor = page.getByRole("dialog", {
    name: "Create component stack",
    exact: true,
  });
  const controller = editor.getByLabel("Stack controller component", {
    exact: true,
  });
  await expect(controller.locator("option")).toHaveCount(2);
  await expect(controller).toHaveValue("proportional-control\u00002");
  await controller.selectOption("proportional-control\u00001");
  await editor
    .getByLabel("Stack controller throttle_gain", { exact: true })
    .fill("0.44");
  await controller.selectOption("proportional-control\u00002");
  await expect(
    editor.getByLabel("Stack controller throttle_gain", { exact: true }),
  ).toHaveCount(0);
  await expect(
    editor.getByLabel("Stack controller alternative_gain", { exact: true }),
  ).toHaveValue("0.7");
  await controller.selectOption("proportional-control\u00001");
  await expect(
    editor.getByLabel("Stack controller alternative_gain", { exact: true }),
  ).toHaveCount(0);
  await expect(
    editor.getByLabel("Stack controller throttle_gain", { exact: true }),
  ).toHaveValue("0.2");
  await controller.selectOption("proportional-control\u00002");
  await editor
    .getByLabel("Stack controller alternative_gain", { exact: true })
    .fill("0.88");
  await editor
    .getByLabel("Stack name", { exact: true })
    .fill("Explicit authored version 2");
  await editor
    .getByLabel("Stack revision note", { exact: true })
    .fill("Authored selection contract; no algorithm is executed");
  await editor
    .getByRole("button", { name: "Publish stack", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  expect(fixture.submissions).toHaveLength(1);
  expect(fixture.submissions[0].body.definition.controller).toEqual({
    id: "proportional-control",
    version: "2",
    parameters: { alternative_gain: 0.88 },
  });
  for (const role of ["perception", "policy"])
    expect(fixture.submissions[0].body.definition[role].version).toBe("1");
  const savedR1 = structuredClone(fixture.revisions[0]);

  await page
    .getByRole("button", { name: "Create stack revision", exact: true })
    .click();
  const revision = page.getByRole("dialog", {
    name: "Create stack revision",
    exact: true,
  });
  await expect(
    revision.getByLabel("Stack controller component", { exact: true }),
  ).toHaveValue("proportional-control\u00002");
  await expect(
    revision.getByLabel("Stack controller alternative_gain", { exact: true }),
  ).toHaveValue("0.88");
  await revision
    .getByLabel("Stack controller component", { exact: true })
    .selectOption("proportional-control\u00001");
  await expect(
    revision.getByLabel("Stack controller throttle_gain", { exact: true }),
  ).toHaveValue("0.2");
  await revision
    .getByLabel("Stack controller throttle_gain", { exact: true })
    .fill("0.33");
  await revision
    .getByRole("button", { name: "Publish stack revision", exact: true })
    .click();
  await expect(revision).not.toBeVisible();
  expect(fixture.submissions).toHaveLength(2);
  expect(fixture.submissions[1].path).toBe(
    `/api/v1/stacks/${savedR1.id}/revisions`,
  );
  expect(fixture.submissions[1].body.expected_revision).toBe(1);
  expect(fixture.submissions[1].body.definition.controller).toEqual({
    id: "proportional-control",
    version: "1",
    parameters: { ...fixture.v1[2].default_parameters, throttle_gain: 0.33 },
  });
  fixture.setCatalog([...fixture.v1, fixture.controller2]);
  await page.getByRole("button", { name: "Refresh stacks workspace" }).click();
  await page
    .getByLabel("Component stack revision", { exact: true })
    .selectOption("1");
  const detail = page.getByTestId("stack-definition-detail");
  await expect(detail).toContainText("proportional-control/2");
  await expect(detail).toContainText("0.88");
  await expect(detail).toContainText(savedR1.manifest_hash);
  expect(fixture.revisions[0]).toEqual(savedR1);
  expect(errors).toEqual([]);
});

for (const condition of ["missing", "unavailable", "invalid-kernel"] as const) {
  test(`${condition} saved component version blocks publication without switching to another version`, async ({
    page,
  }) => {
    const fixture = await authoringFixture(page, true);
    fixture.setCatalog(
      condition === "missing"
        ? fixture.v1
        : [
            ...fixture.v1,
            condition === "unavailable"
              ? { ...fixture.controller2, status: "unavailable" }
              : {
                  ...fixture.controller2,
                  implementation: {
                    ...fixture.controller2.implementation,
                    kernel: null,
                  },
                },
          ],
    );
    await page
      .getByRole("button", { name: "Refresh stacks workspace" })
      .click();
    await page
      .getByRole("button", { name: "Create stack revision", exact: true })
      .click();
    const editor = page.getByRole("dialog", {
      name: "Create stack revision",
      exact: true,
    });
    const controller = editor.getByLabel("Stack controller component", {
      exact: true,
    });
    await expect(controller).toHaveValue("proportional-control\u00002");
    await expect(controller.locator("option:checked")).toContainText(
      "unavailable",
    );
    await expect(
      editor.getByLabel("Stack controller alternative_gain", { exact: true }),
    ).toHaveValue("0.7");
    await expect(
      editor.getByRole("button", {
        name: "Publish stack revision",
        exact: true,
      }),
    ).toBeDisabled();
    expect(fixture.submissions).toEqual([]);
    await controller.selectOption("proportional-control\u00001");
    await expect(
      editor.getByLabel("Stack controller alternative_gain", { exact: true }),
    ).toHaveCount(0);
    await expect(
      editor.getByLabel("Stack controller throttle_gain", { exact: true }),
    ).toHaveValue("0.2");
    await expect(
      editor.getByRole("button", {
        name: "Publish stack revision",
        exact: true,
      }),
    ).toBeEnabled();
    expect(fixture.submissions).toEqual([]);
    await editor.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("stack-definition-detail")).toContainText(
      "proportional-control/2",
    );
  });
}
