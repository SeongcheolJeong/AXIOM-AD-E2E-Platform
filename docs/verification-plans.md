# Verification Plan v1

Validation → **Plans** connects a fixed dataset/model decision to the existing inference queue,
COCO evaluation, comparisons and investigation screens. Requirements remain in their original tab.
This version evaluates **detection A/B**. It does not approve a driving policy, physical sensor,
closed-loop collision avoidance or a commercial release.

## Normal product workflow

1. Create a plan. Select an exact dataset revision/split, two model revisions and explicit class mappings.
   Set inference seed/score/NMS/limit, a purpose, declared author and change reason.
2. Define overall and required-cell criteria: metric, minimum candidate score, maximum A→B score drop,
   minimum images, non-crowd objects and unique source recordings. Unset criteria cannot pass.
3. Edit plan axes/values and add required condition combinations. Select exact images/classes, review
   their conditions and record the reviewer/note. New assignments and taxonomy edits revert to draft.
4. Link existing Collection/Dataset/Case/Evaluation/Run/Suite/terminal Campaign as context, with revisions
   where applicable. Context is preserved and browsable, but cannot independently pass detection criteria.
5. Preview counts and job scope; explicitly confirm trusted models/workload, then submit once.
6. The worker submits A/B inference, then full and eligible-cell evaluations and seals the report.
   Investigate A/B images, review labels, select a collection or find scene data. A return link restores
   the selected plan. New evidence/condition changes require a new plan revision and execution.
7. Retry explicitly. The preview shows new/reused inference jobs. Only sealed successful jobs with
   identical plan/task/capability snapshots are reused; evaluations are recomputed. Previous receipts remain unchanged.

Author/reviewer names are declarations. This local pilot has no individual user roles or project isolation.

## Conditions and honest gaps

The built-in lighting suggestions keep `daylight`, `night`, `low_light`, `twilight`, `unknown` distinct.
There is no automatic Case↔Scene conversion. Imported interpretations require source taxonomy/revision
and a mapping note plus an explicit image review. A Case interval is not automatically applied to each image.
Scene appearance proposals are not weather or illumination measurements.

Axes declare whether one value per image or multiple coexisting values are allowed. Contradictory reviewed
assignments on a single-value axis remain `condition_conflict`; unknown/unclassified or draft assignments
remain `condition_review_needed`. Those cells cannot pass or silently drop their conflicting images.
Reviewed labels are evaluated separately from reviewed conditions.

`data_missing`, `label_review_needed`, `data_insufficient`, `criteria_unconfigured`, `not_evaluated`,
`not_comparable`, `passed`, `degraded` are distinct. Overall plan status is passed only if every required
cell and overall criterion passes. A completed attempt means orchestration ended, not criteria passed.
The report separately records inference/evaluation terminal outcomes and incomplete denominators.

Overlapping image memberships and unclassified split images remain visible. Unique recordings are a
source count, **not independent events**. Adjacent frames/seeds cannot prove scene diversity.

## Evaluator and execution limits

- One pinned dataset split, exactly two models, at most 256 images/32 cells/8 axes and 16 inference jobs per model.
- Model A/B uses the same original input identities, reviewed labels and inference/evaluation policies.
  Different mappings/runtime policies may make the pair non-comparable; names alone never prove equivalence.
- `DetectionEvaluationCreate.selection` optionally pins image IDs and dataset classes using
  `image_class_cohort_v1`. The adapter receives the actual selected rows/classes and recomputes COCO AP.
  Without selection, serialized request bytes retain the legacy shape. Existing full-split behavior is unchanged.
- Full AP and condition AP are separate. TP/FP/FN aggregation and averaging cohort AP are not overall AP.
- `ap_small`, `ap_medium`, `ap_large` use existing native COCO bbox-area ranges including their boundary semantics;
  **arbitrary size bins are not supported**. Class filtering preserves selected-class annotations and crowd semantics.
- Missing/failed predictions remain in the original selected denominator and suppress incomplete aggregate metrics.
- Known label-generating model self-evaluation is rejected before submission. Human review does not erase origin.
- Submission and child publication are transactional/idempotent. GET/polling does not advance work. Worker restart
  continues the already submitted attempt; it never automatically reruns a failed engine stage.
- Cancellation requests active children to stop, prevents later stages and seals only after children are terminal.
- No generic workflow editor, automatic condition inference, temporal event grouping or simulator-generated gap filling.

## Public API and SDK

All paths below are under `/api/v1`; authentication is the existing private bearer-token boundary.

| Action | Interface |
|---|---|
| Create/list/get | `POST/GET /verification-plans`, `GET /verification-plans/{id}?revision=N` |
| Immutable revision | `POST /verification-plans/{id}/revisions` with expected_revision and Idempotency-Key |
| Preview | `POST /verification-plans/{id}/preview` with revision and optional retry_of; creates zero jobs |
| Submit | `POST /verification-plans/{id}/attempts` with revision, expected_preview_hash, confirm_trusted_models, optional retry_of and Idempotency-Key |
| History/status | `GET /verification-plans/{id}/attempts`, `GET /verification-attempts/{id}` |
| Cancel | `POST /verification-attempts/{id}/cancel` |
| Sealed export | `GET /verification-plans/{id}/export?revision=N`, `GET /verification-attempts/{id}/export` |

`AxiomClient` provides `create_verification_plan`, `revise_verification_plan`, `verification_plan(s)`,
`preview_verification`, `submit_verification`, `verification_attempt(s)`, `cancel_verification`,
`export_verification_plan`, `export_verification_attempt`. SDK export checks the exact manifest SHA-256
and uses the same configured server. UI/SDK/agents use the same endpoints; no hidden agent execution path.

## Schema, installation and evidence

Schema **16** adds plan heads, immutable revision manifests and execution receipts. Existing rows and
source artifacts are not rewritten. Use the existing cold backup/restore and frozen local-pilot build
workflow; unfinished plan attempts also block cold backup even between child-job stages.
Old schema15 binaries cannot open schema16. Back up before starting new binaries against an existing workspace.
The current implementation does not automatically restart, deploy, commit or migrate the user's active workspace.

Acceptance: backend API/queue/failure/retry/manifest and migration rollback/concurrent-start checks;
actual native pycocotools image/class cohort evaluation; SDK checks; browser creation→actual CPU ONNX/COCO
A/B→gaps/degradation→comparison→return→revision/export, plus existing requirement workflows and mobile layouts.
ONNX browser inputs/weights/labels are **authored fixtures**, not learned-model road accuracy evidence.

## Next connections selected, not implemented here

1. Research-isolated VAD-Tiny with nuScenes mini: actual temporal multi-camera inference and trajectories,
   native input/state/coordinate contract, no future-GT inference leakage; licenses and remote 8GB feasibility
   must be checked before execution. Neither driving-model inference nor closed-loop safety is delivered by v1.
2. CameraE2E preserved simulated L1-S1 RAW → default ISP vs white-balance `[1.3,1.0,0.75]` → identical
   OpenCV YOLOX-S. Separate sensor/ISP experiment contract; original RAW/labels/geometric correspondence preserved.
   Existing RAW is generated from a spectral proxy, not measured RAW or physically qualified sensor evidence.
3. Approved internal log ingestion/preflight, stable selected-workspace launch and user permissions before sharing;
   learned semantic retrieval and temporal events remain separate strengthening tasks.
