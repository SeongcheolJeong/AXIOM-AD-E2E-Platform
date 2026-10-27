# Browser workflow checks

These tests launch an isolated API on 8010, a separate `ade2e.worker` queue
process, and a Vite app on 5174. All test services use
`e2e/.runtime/workflow-db`, not the normal workspace's `.data` directory. The
worker has no additional HTTP port. Keep ports 8010 and 5174 free before running
the suite; Playwright manages these service lifetimes.

```bash
npm --prefix e2e ci
npm --prefix e2e exec -- playwright install chromium
npm --prefix e2e test
```

For an installed Google Chrome, skip the browser installation and use
`PLAYWRIGHT_CHANNEL=chrome npm --prefix e2e test`.

The primary flow creates a scenario through the UI, submits an execution job,
follows **Execution queue → Open result**, verifies playback movement from the
actual reference adapter, downloads evidence, edits the scenario, confirms
historical input immutability, and creates a requirement whose old execution
must no longer count as current evidence. Page coverage includes Execution queue,
read-only adapter connection checks, navigation, and overflow at a 390 px
viewport. A failed selected-run lookup must hide the previous replay and export.

The queue retry workflow lets the API accept a real job, drops its first response
in the browser, retries the launch with the same `Idempotency-Key`, and verifies
that the original job and actual run are opened. It also checks the worker panel
and narrow-screen navigation. This is response-loss injection around the real
API and separate reference worker, not proof of remote-engine recovery. Backend
process tests cover ownership expiry and the distinct cancellation paths;
release evidence belongs in [status](../docs/status.md), not an assumed test count.

The two tests in [suites.spec.ts](tests/suites.spec.ts) cover the Test suites UI:

- The keyboard regression types consecutive characters into separate dialog
  fields, verifies that focus stays in the selected field, checks reverse-tab
  wrapping, and restores focus to **New suite** after Escape closes the dialog.
- The workflow authors a suite with two seed cases, lets the API accept a real
  campaign, drops the response, and verifies resubmission uses the same key and
  original campaign. It captures a baseline, publishes a changed suite revision,
  runs another campaign, and distinguishes one changed-condition case from the
  unchanged case. It saves and downloads a comparison, checks its SHA-256 and the
  unchanged baseline bytes, and checks the 390 px layout. Selecting a different
  saved comparison whose lookup fails must hide both the previous saved detail
  and comparison report rather than displaying stale evidence for the new ID.

The campaign response-loss injection surrounds real API acceptance and separate
reference-worker execution. See [the suite workflow guide](../docs/test-suites.md) for the
distinction between live comparison and captured comparison evidence.

The recording workflows generate original MCAP fixtures locally, upload them
through the UI, verify exact nanosecond seeks, numeric/opaque previews, previous
and next windows, byte-identical downloads and narrow-screen/reload behavior.
A 150-message zero-timestamp fixture ensures duplicate rows remain accessible
across pages instead of disappearing behind the latest-at-cursor sample.

The checks use unique names and preserve their own local database between runs.
They never delete an existing user scenario or require a real CARLA installation.
GPU rendering, physical model fidelity and CARLA sensor behavior require separate
engine acceptance tests. Reports and failure traces are local git-ignored outputs.

For manual development, `.venv/bin/python scripts/dev.py` starts the normal API,
queue worker, and UI together. If running the API separately, start
`.venv/bin/python -m ade2e.worker` from the repository root with the same data and
adapter configuration. Existing `POST /runs` and SDK `run()` remain waiting
compatibility calls; the browser launcher submits `POST /jobs` instead.

Queue `completed` is distinct from a passing metric verdict. `cancel_requested`
does not establish an external stop, an uncooperative adapter may complete late,
and an `interrupted` or uncertain outcome is not automatically retried. The
[queue contract](../docs/jobs-contract.md) describes cleanup confirmation and
reconciliation. The current schema 7 retains suite/campaign/sensor/model/inference evidence and
adds immutable dataset revisions and saved detection evaluations. The tests do
not imply commercial readiness or verified live CARLA operation.


The dataset workflow in [datasets.spec.ts](tests/datasets.spec.ts) captures and
labels exact authored source images before running the model. It checks pointer
to original-pixel coordinates, precise box edits, crowd and explicit-negative
declarations, automatic review reset after annotation edits, immutable revision
exports, real ONNX inference and actual COCO child evaluation. Incomplete
coverage withholds aggregate AP/PR; completed review exposes the full denominator,
101-point PR and source-linked match overlays. The test also exercises a lost
evaluation submission response, exact same-key recovery, PNG failure/retry,
selection-error stale-evidence suppression, keyboard focus and390px layout.
These authored inputs establish UI/pipeline behavior, not road accuracy.
