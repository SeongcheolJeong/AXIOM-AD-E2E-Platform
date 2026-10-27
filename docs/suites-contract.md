# Versioned suites, campaigns and immutable baselines

Authoritative response models are `backend/src/ade2e/suite_schemas.py` and OpenAPI.
All routes below use `/api/v1` and existing bearer authentication. Suites and
baselines are evidence organization, not approval or certification claims.

## Authoring

- `POST /suites` (`201`), `GET /suites` (array of `SuiteSummary`).
- `GET /suites/{id}` returns latest `SuiteDetail`.
- `POST /suites/{id}/revisions` (`201`) accepts a complete replacement definition
  plus `expected_revision`; a stale optimistic guard returns `409`.
- `GET /suites/{id}/revisions` returns immutable `SuiteDetail[]`, newest first.

`SuiteCreate = {name, description:"", cases:[...]}`; 1–100 cases. Each case is
`{key, name, scenario_id, scenario_revision, seed?:int, parameter_overrides:{},
requirement_ids:[...]}`. A stable case key is unique within a suite, 1–64 ASCII
letters/digits/underscore/dot/hyphen, starting with a letter or digit. Multiple
cases may use the same scenario with different seeds/overrides. Each case needs
1–32 unique requirement IDs. Editing may preserve historical scenario revisions;
the API resolves the actual `RevisionRow`, never silently upgrades a selection.
Requirement scope must include the selected scenario ID, or (when no explicit
IDs exist) match the selected scenario revision's category. No-scope requirements
are rejected. Requirement IDs are selected per case, not per suite.

SuiteDetail exposes `id,name,description,revision,case_count,created_at,updated_at,
definition_hash,evaluator_version,cases`. Each returned case retains its authoring
fields and adds `scenario_snapshot` (effective overrides/seed already applied),
`effective_input_hash`, `requirements:[{id,definition,definition_hash}]` and
`criteria_hash`. Each requirement snapshot also pins its expected SI `unit`. Revisions pin actual requirement definitions, not current
requirement coverage. Original suite revisions never change.

## Execution and evaluation

- `POST /campaigns` (`202`), required `Idempotency-Key`, body
  `{suite_id,suite_revision,adapter_id:"reference",label,control?:selection}`.
- `GET /campaigns?limit=100&before_seq=N&suite_id=...` returns `{items,next_before_seq}`;
  newest submission first, limit 1–100; optional suite_id filters before pagination. `GET /campaigns/{id}` returns detail.
- `POST /campaigns/{id}/cancel` atomically requests cancellation for every
  member, preserving already terminal results. Remote work may still finish.
- `GET /campaigns/{id}/export` returns the current canonical report JSON.

An accepted campaign and all its member jobs commit in one SQLite writer
transaction. Invalid saved inputs/requirements or known unsupported adapter
categories reject the entire submission with zero new jobs. Jobs are executed by
the existing worker, leases and resource ownership. No engine call occurs during
submission. Unknown runtime support remains an execution outcome; submission is
not a sensor/engine validation. Idempotency is scoped to `/campaigns`; identical
body/key replays the original campaign before re-resolving mutable state, and a
changed body returns `409`.

`control` accepts the same versioned selection as individual simulation jobs:
exact stack/rig revisions and either a fixed profile (selection/1) or sensor
configuration revision (selection/2). It requires CARLA. All assets are resolved
once within the campaign transaction; each effective case seed derives its own
prepared context. Missing revisions or any unsupported case reject the whole
submission. [Control workflow](campaign-control.md).

A controlled detail includes `execution_plan` with
`schema_version:"campaign-execution-plan/1"`, the selection, full pinned
`context_template`, `metric_contract_version`, ordered `members` and `plan_hash`. Members independently pin
`key,job_id,input_hash,context_hash,request_hash`; each case exposes that entry as
`expected_execution`. Reporting verifies the plan against the original campaign
request and rederives every expected context from its snapshots before comparing
actual jobs/runs. Missing members stay in the denominator; changed job inputs
remain unevaluated. A corrupt/missing plan or an extra member returns `409`.
Cancellation also checks case-to-job identity before touching any member.

New controlled plans pin `carla-observed-contact-count/1`: the explicitly
supported CARLA adapter versions may evaluate a nonnegative integer
`collision_count` in raw `observed contacts` against a `count` criterion without
rewriting its value or unit. The check exposes `measurement_contract` only when
that contract applies. It counts distinct callback-reported contact actors in
the measured interval; zero does not establish stream completeness or collision
absence. Unknown units and invalid counts remain unevaluated. Legacy reports do
not receive this interpretation retroactively. A change of measurement contract
also makes a baseline comparison `not_comparable`.

Schema11 adds one nullable JSON column. Legacy requests/details and baseline
manifests omit the new optional fields rather than adding null keys, preserving
their existing hashes and bytes. A missing plan is accepted as legacy only when
the original request hash matches the corresponding no-control request.

Campaign summary fields: `id,submission_seq,label,suite_id,suite_revision,
suite_name,adapter_id,adapter_version,created_at,status,verdict,counts`.
`status = queued|running|finished`; finished means no member remains active,
including unsuccessful or missing outcomes. `verdict = pending|passed|failed|
incomplete`. A known failed criterion yields failed even if another member is
pending. Passed requires every case and every pinned check to pass; incomplete
means terminal missing/unevaluable evidence without a demonstrated criterion
failure. A completed job can have a failed case verdict.

`counts = {total,pending,completed,failed,cancelled,interrupted,missing,
passed_cases,failed_cases,not_evaluated_cases,checks_total,checks_passed,
checks_failed,checks_not_evaluated}`. `evaluation_complete` on campaign and each case is true only when every pinned check is evaluated. A case can be failed and incomplete at once. The lifecycle buckets from pending through
missing sum to total. Checks always use the full pinned denominator; pending and
missing checks count as not evaluated. Cases pending execution have verdict
`pending`; other case verdicts are `passed|failed|not_evaluated`.

Detail adds `evaluated_at,evaluator_version,suite_snapshot,adapter_snapshot,cases`.
Each case includes key/name/scenario identity, effective/criteria hashes,
`job_id,run_id,job_status,verdict,checks,metrics,execution_context,reason`.
`job_status` is the actual job status or `missing`. Each check has requirement
identity/name/metric/predicate/threshold, `status:passed|failed|not_evaluated`,
`value,unit,expected_unit,unbounded,reason`. Null TTC with a present metric means unbounded
constant-velocity TTC; missing metrics are not evaluated. Evaluation uses the
suite's pinned definitions and fixed evaluator contract, never current coverage.
Execution context retains adapter ID/version, fidelity, result schema version
and input/output hashes from actual run evidence.

## Baselines and comparisons

- `POST /baselines` (`201`), required `Idempotency-Key`, body
  `{campaign_id,name,note}`; note must be nonempty. Only a finished campaign may
  be captured. Failed and incomplete captures are allowed and remain labelled.
- `GET /baselines?suite_id=...` (optional filter) returns `BaselineSummary[]`; `GET /baselines/{id}` returns
  summary plus `manifest`. `GET /baselines/{id}/export` returns exact stored bytes.

Summary fields: `id,name,note,campaign_id,suite_id,suite_revision,created_at,
manifest_hash,manifest_size_bytes,total_cases,verdict,download_url`. The immutable
manifest is `{schema_version:"1",baseline_id,name,note,captured_at,report}` where
report is a complete CampaignDetail captured once. SHA-256 covers the exact
canonical export bytes. Reads verify integrity; later definitions/runs do not
rewrite a baseline. Baseline creation keys are scoped to `/baselines` and replay
before capture validation, including a retry after a lost response.

`GET /campaigns/{id}/compare?baseline_id=...` requires the same suite ID (different
suite revisions are allowed). Response has `campaign_id,baseline_id,
baseline_manifest_hash,suite_id,compared_at,counts,rows`. Case keys use the union
of baseline/current definitions; removed and added cases remain in total.

Each row has `key,name,status,reasons,baseline_verdict,current_verdict,
baseline_job_id,current_job_id,baseline_run_id,current_run_id,
baseline_adapter_version,current_adapter_version,adapter_version_changed,
metrics:[{metric_name,unit,baseline_value,current_value,delta}]`.
Status is `added|removed|not_evaluated|not_comparable|regressed|improved|
unchanged_pass|unchanged_fail`; counts contain total and each exact status.
Paired cases require identical effective scenario/revision, requirement criteria,
evaluator version, adapter ID, metric units, fidelity and result schema. Different
adapter versions or control context hashes also yield `not_comparable`, with the change reported.
A future independent measurement contract would be needed to permit cross-version
comparison; matching names/units alone do not establish measurement equivalence.
Unknown/missing evidence is never regression or improvement. Delta is
current-minus-baseline only for finite values in equal units; null TTC has no
numeric delta. No safety/acceptance claim follows from these classifications.

Live compare is a current observation and may change while jobs are active. To
preserve a reviewable regression report, `POST /comparisons` captures a finished
candidate, the exact source baseline and the comparison policy into immutable
canonical bytes; see [comparison evidence](comparison-evidence.md).

Suite snapshots are capped at 8 MiB; baseline manifests at 16 MiB. Completed-run
JSON content is checked against its recorded input/output hashes before criteria
evaluation. This does not re-read or certify separate artifact file bytes. Those
files retain their independent hash-verified download boundary.
