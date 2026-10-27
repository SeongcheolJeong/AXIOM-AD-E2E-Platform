import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const digest = (value: Buffer) =>
  createHash("sha256").update(value).digest("hex");

/** HTTP lifecycle records are authored; artifact bytes come from the real native
 * bridge over its deterministic mocked world. No remote CARLA is contacted. */
async function fixture(info: TestInfo, name: string, partial = false) {
  const directory = info.outputPath(`recovery-${name}`);
  execFileSync(
    process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [
      path.join(root, "e2e/fixtures/recovery_fixture.py"),
      directory,
      name,
      ...(partial ? ["partial"] : []),
    ],
    { cwd: root, timeout: 30000 },
  );
  const native = JSON.parse(
    await readFile(path.join(directory, "fixture.json"), "utf8"),
  );
  const files = new Map<string, Buffer>();
  for (const artifact of native.run.artifacts)
    files.set(
      artifact.name,
      await readFile(path.join(directory, artifact.name)),
    );
  const original = {
    ...structuredClone(native.run),
    status: "error",
    verdict: "error",
    error: "Original response was lost; remote outcome uncertain",
    metrics: [],
    trajectory: [],
    artifacts: [],
    events: [
      {
        time_s: 0,
        type: "execution_error",
        message: "Original response was lost; remote outcome uncertain",
      },
    ],
  };
  const job: any = {
    id: `job-recovery-${name}`,
    submission_seq: 42,
    scenario_id: original.scenario_id,
    scenario_name: original.scenario_name,
    scenario_revision: 1,
    adapter_id: "carla",
    adapter_version: original.provenance.adapter_version,
    status: "failed",
    created_at: "2026-09-13T00:00:00Z",
    updated_at: "2026-09-13T00:01:00Z",
    started_at: "2026-09-13T00:00:01Z",
    finished_at: "2026-09-13T00:01:00Z",
    dispatched_at: "2026-09-13T00:00:02Z",
    run_id: original.id,
    error: original.error,
    input_hash: original.provenance.input_hash,
    worker_id: "lost-dispatcher",
    lease_expires_at: null,
    cancel_requested_at: null,
    cancellation_supported: false,
    outcome_uncertain: true,
    execution_context: original.execution_context,
    scenario_snapshot: original.scenario_snapshot,
    request: {
      scenario_id: original.scenario_id,
      adapter_id: "carla",
      seed: original.provenance.seed,
    },
    events: [
      { at: "2026-09-13T00:01:00Z", type: "failed", message: original.error },
    ],
    events_total: 1,
  };
  const binding = {
    store_id: `store-${name}`,
    attempt: native.attempt,
    request_hash: native.request_hash,
  };
  const archive = Buffer.from(
    JSON.stringify({
      scope:
        "Authored browser recovery archive, not a real remote worker response",
      execution_id: original.id,
      artifacts: native.run.artifacts.map(
        ({ name, sha256, size_bytes }: any) => ({ name, sha256, size_bytes }),
      ),
    }),
  );
  const status: any = {
    schema_version: "remote-execution-status/1",
    store_id: binding.store_id,
    execution_id: original.id,
    request_hash: binding.request_hash,
    input_hash: job.input_hash,
    context_hash: job.execution_context.context_hash,
    adapter_id: "carla",
    adapter_version: job.adapter_version,
    state: partial ? "interrupted" : "completed",
    observed_at: "2026-09-13T00:02:00Z",
    archive_kind: partial ? "partial" : "complete",
    archive_sha256: digest(archive),
    archive_size_bytes: archive.length,
    mutations_started: true,
    cleanup_status: "unknown",
    error: partial ? "Injected readback failure" : null,
  };
  const recovered = {
    ...structuredClone(native.run),
    id: `run-recovered-${name}`,
    created_at: "2026-09-13T00:03:00Z",
    recovery: {
      verification_version: "remote-recovery/1",
      recovery_id: `recovery-${name}`,
      source_job_id: job.id,
      source_run_id: original.id,
      source_execution_id: original.id,
      source_submission_seq: job.submission_seq,
      store_id: binding.store_id,
      request_hash: binding.request_hash,
      archive_sha256: digest(archive),
      archive_kind: status.archive_kind,
      recovered_at: "2026-09-13T00:03:00Z",
    },
  };
  recovered.artifacts.forEach((item: any) => (item.run_id = recovered.id));
  const inspection: any = {
    source_job_id: job.id,
    source_run_id: original.id,
    source_execution_id: original.id,
    supported: true,
    can_inspect: true,
    can_retrieve: true,
    reason: null,
    binding,
    status,
    recovered_run_id: null,
  };
  const operation: any = {
    id: `recovery-${name}`,
    submission_seq: 43,
    source_job_id: job.id,
    source_run_id: original.id,
    source_execution_id: original.id,
    status: "completed",
    outcome: partial ? "diagnostics_only" : "recovered",
    created_at: "2026-09-13T00:02:01Z",
    updated_at: "2026-09-13T00:03:00Z",
    finished_at: "2026-09-13T00:03:00Z",
    note: "Retrieve the immutable original execution evidence",
    run_id: recovered.id,
    remote_status: status,
    error: null,
    binding,
    events: [
      {
        at: "2026-09-13T00:03:00Z",
        type: "completed",
        message: "Supplemental evidence published; original history preserved",
      },
    ],
    events_total: 1,
    cancellation_supported: true,
    worker_id: "retrieval-worker",
    lease_expires_at: null,
    cancel_requested_at: null,
    manifest_hash: null,
    manifest_size_bytes: null,
    download_url: `/api/v1/execution-recoveries/recovery-${name}/export`,
    archive_hash: digest(archive),
    archive_size_bytes: archive.length,
    archive_download_url: `/api/v1/execution-recoveries/recovery-${name}/archive`,
  };
  const manifest = Buffer.from(
    JSON.stringify({
      scope: "Authored local recovery manifest",
      source_job_id: job.id,
      source_execution_id: original.id,
      recovered_run_id: recovered.id,
      archive_hash: operation.archive_hash,
    }),
  );
  operation.manifest_hash = digest(manifest);
  operation.manifest_size_bytes = manifest.length;
  return {
    original,
    recovered,
    files,
    job,
    inspection,
    operation,
    archive,
    manifest,
  };
}
type Fixture = Awaited<ReturnType<typeof fixture>>;

async function install(
  page: Page,
  item: Fixture,
  options: {
    published?: boolean;
    extraJobs?: any[];
    loseFirstResponse?: boolean;
  } = {},
) {
  const overview = await (await page.request.get("/api/v1/overview")).json();
  const state = {
    published: options.published ?? false,
    inspectionCalls: 0,
    submissionKeys: [] as string[],
    submissions: [] as any[],
    mutations: [] as string[],
    evidenceAuth: [] as string[],
    corruptArchive: false,
    inspect: async (_jobId: string) => item.inspection,
  };
  const jobs = [item.job, ...(options.extraJobs ?? [])];
  page.on("request", (request) => {
    if (request.method() !== "GET" && request.url().includes("/api/v1/"))
      state.mutations.push(new URL(request.url()).pathname);
    if (
      /\/api\/v1\/(?:artifacts\/.*\/download|execution-recoveries\/.*\/(?:archive|export))$/.test(
        request.url(),
      )
    )
      state.evidenceAuth.push(request.headers()["authorization"] ?? "");
  });
  await page.route("**/api/v1/overview", async (route) => {
    await route.fulfill({
      json: {
        ...overview,
        recent_runs:
          state.published && item.operation.run_id
            ? [item.recovered, item.original]
            : [item.original],
      },
    });
  });
  await page.route(/\/api\/v1\/runs(?:\?.*)?$/, (route) =>
    route.fulfill({
      json:
        state.published && item.operation.run_id
          ? [item.recovered, item.original]
          : [item.original],
    }),
  );
  for (const run of [item.original, item.recovered])
    await page.route(`**/api/v1/runs/${run.id}`, (route) =>
      route.fulfill({ json: run }),
    );
  for (const artifact of item.recovered.artifacts)
    await page.route(`**${artifact.download_url}`, (route) =>
      route.fulfill({
        contentType: artifact.media_type,
        body: item.files.get(artifact.name)!,
      }),
    );
  await page.route(/\/api\/v1\/jobs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { items: jobs, next_before_seq: null } }),
  );
  for (const job of jobs) {
    await page.route(`**/api/v1/jobs/${job.id}`, (route) =>
      route.fulfill({ json: job }),
    );
    await page.route(
      `**/api/v1/jobs/${job.id}/remote-execution`,
      async (route) => {
        state.inspectionCalls++;
        const value = await state.inspect(job.id);
        try {
          await route.fulfill(
            value?.httpError
              ? { status: value.httpError, json: { detail: value.detail } }
              : { json: value },
          );
        } catch {
          /* Selecting another job aborts the original request intentionally. */
        }
      },
    );
  }
  await page.route("**/api/v1/execution-resources", (route) =>
    route.fulfill({
      json: [
        {
          adapter_id: "carla",
          state: "reconcile_required",
          job_id: item.job.id,
          updated_at: item.job.finished_at,
          note: "Original dispatcher stop and engine cleanup require independent confirmation",
        },
      ],
    }),
  );
  await page.route(
    /\/api\/v1\/execution-recoveries(?:\?.*)?$/,
    async (route) => {
      if (route.request().method() === "GET") {
        const source = new URL(route.request().url()).searchParams.get(
          "source_job_id",
        );
        return route.fulfill({
          json: {
            items:
              state.published && source === item.job.id ? [item.operation] : [],
            next_before_seq: null,
          },
        });
      }
      state.submissionKeys.push(route.request().headers()["idempotency-key"]);
      state.submissions.push(route.request().postDataJSON());
      state.published = true;
      if (options.loseFirstResponse && state.submissionKeys.length === 1)
        return route.abort("failed");
      return route.fulfill({ status: 202, json: item.operation });
    },
  );
  await page.route(
    `**/api/v1/execution-recoveries/${item.operation.id}`,
    (route) => route.fulfill({ json: item.operation }),
  );
  await page.route(
    `**/api/v1/execution-recoveries/${item.operation.id}/cancel`,
    (route) => {
      Object.assign(item.operation, {
        status: "cancelled",
        outcome: "not_available",
        run_id: null,
        finished_at: "2026-09-13T00:03:00Z",
      });
      return route.fulfill({ json: item.operation });
    },
  );
  await page.route(`**${item.operation.download_url}`, (route) =>
    route.fulfill({ contentType: "application/json", body: item.manifest }),
  );
  await page.route(`**${item.operation.archive_download_url}`, (route) => {
    const bytes = Buffer.from(item.archive);
    if (state.corruptArchive) bytes[bytes.length - 1] ^= 1;
    return route.fulfill({ contentType: "application/json", body: bytes });
  });
  return state;
}

test("recovery retries one logical retrieval, preserves the failed run and opens original native artifacts under a new storage ID", async ({
  page,
}, info) => {
  const item = await fixture(info, "complete"),
    originalSnapshot = JSON.stringify(item.original),
    errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("axiom.apiToken", "browser-contract-token"),
  );
  const state = await install(page, item, { loseFirstResponse: true });
  await page.goto("/#jobs");
  const panel = page.getByTestId("remote-outcome-panel");
  await expect(panel).toBeVisible();
  expect(state.inspectionCalls).toBe(0);
  await panel
    .getByRole("button", { name: "Inspect remote execution", exact: true })
    .click();
  await expect(panel.getByTestId("remote-execution-status")).toContainText(
    item.original.id,
  );
  await panel
    .getByLabel("Recovery note", { exact: true })
    .fill(item.operation.note);
  await panel
    .getByRole("button", { name: "Retrieve evidence", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toContainText(
    "same note reuses the original request key",
  );
  await panel
    .getByRole("button", { name: "Retry evidence retrieval", exact: true })
    .click();
  const recovery = panel.getByTestId("recovery-detail");
  await expect(recovery.getByTestId("recovery-outcome")).toHaveText(
    "Evidence outcome · recovered",
  );
  expect(state.submissionKeys).toHaveLength(2);
  expect(state.submissionKeys[0]).toBeTruthy();
  expect(state.submissionKeys[1]).toBe(state.submissionKeys[0]);
  expect(state.submissions).toEqual([
    { source_job_id: item.job.id, note: item.operation.note },
    { source_job_id: item.job.id, note: item.operation.note },
  ]);
  await expect(
    page
      .getByTestId("job-detail")
      .locator(".job-detail-header")
      .getByTestId("job-status"),
  ).toHaveText("Failed");
  await expect(
    page.getByRole("region", { name: "Cleanup required for carla" }),
  ).toBeVisible();
  expect(state.inspectionCalls).toBe(1);
  await recovery
    .locator("summary", { hasText: "Sealed files and checksums" })
    .click();
  const archiveDownload = page.waitForEvent("download");
  await recovery
    .getByRole("link", {
      name: "Download original remote archive",
      exact: true,
    })
    .click();
  expect(await readFile((await (await archiveDownload).path())!)).toEqual(
    item.archive,
  );
  await recovery
    .getByRole("button", { name: "Open recovered evidence", exact: true })
    .click();
  await expect(
    page.getByLabel("Select execution", { exact: true }),
  ).toHaveValue(item.recovered.id);
  await expect(page.getByTestId("recovered-local-run-id")).toHaveText(
    item.recovered.id,
  );
  await expect(page.getByTestId("recovered-source-execution-id")).toHaveText(
    item.original.id,
  );
  await expect(page.getByTestId("carla-response-verified")).toContainText(
    "81 / 81 measured observations",
  );
  await expect(page.getByTestId("carla-live-verified")).toBeVisible();
  const originalBytes = page.waitForEvent("download");
  await page
    .getByRole("link", {
      name: "Download verified vehicle response JSON",
      exact: true,
    })
    .click();
  const responseBytes = await readFile((await (await originalBytes).path())!);
  expect(responseBytes).toEqual(item.files.get("carla-vehicle-response.json"));
  expect(JSON.parse(responseBytes.toString()).execution_id).toBe(
    item.original.id,
  );
  expect(state.evidenceAuth.length).toBeGreaterThan(0);
  expect(
    state.evidenceAuth.every(
      (header) => header === "Bearer browser-contract-token",
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page
    .getByTestId("recovered-run-lineage")
    .screenshot({ path: info.outputPath("recovery-lineage-mobile.png") });
  await page
    .getByRole("button", { name: "Open original result", exact: true })
    .click();
  await expect(
    page.getByLabel("Select execution", { exact: true }),
  ).toHaveValue(item.original.id);
  await expect(page.getByTestId("recovered-run-lineage")).not.toBeVisible();
  expect(JSON.stringify(item.original)).toBe(originalSnapshot);
  expect(state.mutations).toEqual([
    "/api/v1/execution-recoveries",
    "/api/v1/execution-recoveries",
  ]);
  expect(errors).toEqual([]);
});

test("unknown remote states, failed refresh and late responses never become a different job's evidence", async ({
  page,
}, info) => {
  const item = await fixture(info, "unknown"),
    otherJob = {
      ...structuredClone(item.job),
      id: "job-recovery-other",
      run_id: null,
      scenario_name: "Other source submission",
    };
  const state = await install(page, item, { extraJobs: [otherJob] });
  await page.goto("/#jobs");
  const panel = page.getByTestId("remote-outcome-panel");
  for (const remoteState of ["accepted", "running", "not_found"]) {
    state.inspect = async () => ({
      ...item.inspection,
      can_retrieve: false,
      status: {
        ...item.inspection.status,
        state: remoteState,
        archive_kind: null,
        archive_sha256: null,
        archive_size_bytes: null,
      },
    });
    await panel
      .getByRole("button", {
        name: state.inspectionCalls
          ? "Refresh remote status"
          : "Inspect remote execution",
        exact: true,
      })
      .click();
    await expect(panel.getByTestId("remote-execution-status")).toContainText(
      `Remote · ${remoteState.replaceAll("_", " ")}`,
    );
    await expect(
      panel.getByRole("button", { name: "Retrieve evidence", exact: true }),
    ).not.toBeVisible();
  }
  await expect(panel).toContainText(
    "does not prove that an earlier execution never ran",
  );
  state.inspect = async () => item.inspection;
  await panel
    .getByRole("button", { name: "Refresh remote status", exact: true })
    .click();
  await panel
    .getByLabel("Recovery note", { exact: true })
    .fill("Keep original identity");
  state.inspect = async () => ({
    httpError: 503,
    detail: "Remote store unavailable",
  });
  await panel
    .getByRole("button", { name: "Refresh remote status", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toContainText(
    "last successful remote observation below is stale",
  );
  await expect(
    panel.getByRole("button", { name: "Retrieve evidence", exact: true }),
  ).toBeDisabled();
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => (release = resolve));
  state.inspect = async (id) => {
    if (id === item.job.id) {
      await delayed;
      return item.inspection;
    }
    return {
      source_job_id: otherJob.id,
      source_run_id: null,
      source_execution_id: null,
      supported: false,
      can_inspect: false,
      can_retrieve: false,
      reason: "No pinned durable remote binding",
      binding: null,
      status: null,
      recovered_run_id: null,
    };
  };
  await panel
    .getByRole("button", { name: "Refresh remote status", exact: true })
    .click();
  await page
    .getByRole("button", { name: `Inspect job ${otherJob.id}`, exact: true })
    .click();
  await expect(page.getByTestId("job-id")).toHaveText(otherJob.id);
  release();
  await expect(panel.getByTestId("remote-execution-status")).not.toBeVisible();
  await expect(
    panel.getByLabel("Recovery note", { exact: true }),
  ).not.toBeVisible();
  await panel
    .getByRole("button", { name: "Inspect remote execution", exact: true })
    .click();
  await expect(panel).toContainText("No pinned durable remote binding");
  await expect(panel).not.toContainText(item.original.id);
  expect(state.mutations).toEqual([]);
});

test("partial native evidence remains diagnostics and archive or lineage tampering is rejected", async ({
  page,
}, info) => {
  const item = await fixture(info, "partial", true);
  const state = await install(page, item, { published: true });
  await page.goto("/#jobs");
  const recovery = page.getByTestId("recovery-detail");
  await expect(recovery.getByTestId("recovery-outcome")).toHaveText(
    "Evidence outcome · diagnostics only",
  );
  await recovery
    .locator("summary", { hasText: "Sealed files and checksums" })
    .click();
  state.corruptArchive = true;
  let downloads = 0;
  page.on("download", () => downloads++);
  await recovery
    .getByRole("link", {
      name: "Download original remote archive",
      exact: true,
    })
    .click();
  await expect(recovery.getByRole("alert")).toContainText("checksum mismatch");
  expect(downloads).toBe(0);
  await recovery
    .getByRole("button", { name: "Open recovered evidence", exact: true })
    .click();
  await expect(page.getByTestId("recovered-run-lineage")).toContainText(
    "Recovered partial diagnostics",
  );
  await expect(page.getByTestId("carla-response-verified")).toContainText(
    "Partial response sequence",
  );
  await expect(page.getByTestId("carla-live-verified")).toBeVisible();
  expect(item.recovered.trajectory).toEqual([]);
  expect(item.recovered.metrics).toEqual([]);
  const wrong = {
    ...structuredClone(item.recovered),
    id: "run-recovered-invalid-lineage",
  };
  wrong.recovery.source_execution_id = "different-original-execution";
  wrong.artifacts.forEach((artifact: any) => (artifact.run_id = wrong.id));
  await page.route(/\/api\/v1\/runs(?:\?.*)?$/, (route) =>
    route.fulfill({ json: [item.recovered, wrong, item.original] }),
  );
  await page.route(`**/api/v1/runs/${wrong.id}`, (route) =>
    route.fulfill({ json: wrong }),
  );
  await page.reload();
  await page
    .getByLabel("Select execution", { exact: true })
    .selectOption(wrong.id);
  await expect(
    page.getByTestId("carla-response-inspector").getByRole("alert"),
  ).toContainText("rejected");
  await expect(page.getByTestId("carla-response-verified")).not.toBeVisible();
  await expect(page.getByTestId("carla-live-verified")).not.toBeVisible();
  expect(state.mutations).toEqual([]);
});

test("cancelling retrieval affects only that operation and agent cURL preserves required header metadata", async ({
  page,
  context,
}, info) => {
  const item = await fixture(info, "cancel");
  Object.assign(item.operation, {
    status: "running",
    outcome: "pending",
    run_id: null,
    finished_at: null,
    manifest_hash: null,
    manifest_size_bytes: null,
    download_url: null,
    archive_hash: null,
    archive_size_bytes: null,
    archive_download_url: null,
  });
  const state = await install(page, item, { published: true });
  await page.goto("/#jobs");
  const recovery = page.getByTestId("recovery-detail");
  await expect(recovery).toContainText(
    "It does not cancel the original remote execution",
  );
  await recovery
    .getByRole("button", { name: "Cancel retrieval", exact: true })
    .click();
  await expect(recovery.getByTestId("job-status")).toHaveText("Cancelled");
  await expect(
    page
      .getByTestId("job-detail")
      .locator(".job-detail-header")
      .getByTestId("job-status"),
  ).toHaveText("Failed");
  await expect(
    page.getByRole("region", { name: "Cleanup required for carla" }),
  ).toBeVisible();
  expect(state.mutations).toEqual([
    `/api/v1/execution-recoveries/${item.operation.id}/cancel`,
  ]);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/#agents");
  await page.getByRole("button", { name: /retrieve_remote_execution/ }).click();
  const tool = page
    .locator(".tool-item")
    .filter({
      has: page.getByRole("button", { name: /retrieve_remote_execution/ }),
    });
  await expect(tool).toContainText("Idempotency-Key");
  await expect(tool).toContainText("Retry scope");
  await tool
    .getByRole("button", { name: "Copy cURL template", exact: true })
    .click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("/api/v1/execution-recoveries");
  expect(copied).toContain(
    "-H 'Idempotency-Key: <stable-logical-request-key>'",
  );
  expect(copied).not.toContain("/simulate");
});
