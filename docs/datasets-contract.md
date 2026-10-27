# Detection datasets and evaluation implementation contract

Status: implemented local contract (2026-09-13); measured acceptance and remaining gates are recorded in [status](status.md).
All routes share `/api/v1`. Immutable source images are independent of inference
jobs: labels may be authored before any detector is run. Source/license/reviewer
fields are declarations, not authenticated review or rights certification.

## Image assets

POST `/dataset-images` captures exactly one supported camera message as an
immutable PNG. Body `{recording_id,channel_id,ordinal,decoder_id,decoder_version}`.
Response 201 new /200 exact dedup: `DatasetImage` `{id,created_at,input,image}`.
`input` is existing `PerceptionInput` minus `selection_index`: original recording
ID/SHA, channel/ordinal, exact log/publish ns strings, message SHA/size, decoder
ID/version. `image` matches `PerceptionImage` (SHA,size,width,height,download_url,
capture_time_ns,frame_id). Source recording/schema declarations are saved in
`recording_snapshot`. Camera PNG bound 8 MiB and 2,000,000 pixels. Original source
and decoded bytes are independently verified. ID dedup includes exact PNG bytes.
GET `/dataset-images/{id}` / GET `/{id}/image` verify metadata/file integrity.

## Dataset revisions

POST `/datasets`: `DatasetCreate` complete definition:
`{name,description='',labels:[unique ordered nonblank strings],source='unknown',
source_revision='unknown',license='unknown',note,images:[DatasetMember]}`.
Limits 1..256 images, 1..256 labels, 100 boxes/image. No duplicate image IDs.
`DatasetMember`:
`{image_id,split:'train'|'validation'|'test'|'unspecified',review_status:'unreviewed'|'reviewed',
reviewer:'',review_note:'',annotation_source:'human'|'synthetic'|'imported'|'unknown',
annotations:[{id,label,bbox_xyxy:[x1,y1,x2,y2],iscrowd:boolean(default false)}]}`.
Annotations are drafts while unreviewed. Reviewed requires nonblank reviewer and
review_note and annotation_source other than unknown. Reviewed+empty annotations
is an explicit negative image, never inferred from an empty/unreviewed list.
A revision changing taxonomy/annotations cannot carry the same prior reviewer and
review_note while remaining reviewed; renew the declaration or reset unreviewed.
Split-only edits preserve the annotation review. Review remains user-declared.
Stable annotation IDs are unique within each image; labels must belong to the
pinned dataset taxonomy; finite positive-area boxes must fit saved original PNG.
Repeated PNG SHA across train/validation/test partitions is rejected. Identical
bytes within one split remain distinct source observations and are disclosed in
statistics; unassigned membership is not a train/test guarantee. This check does
not detect near-duplicates or establish independence from a model's training set.

GET `/datasets` heads; GET `/{id}?revision=N` optional authoring head;
GET `/{id}/revisions`; POST `/{id}/revisions` complete create + expected_revision;
GET `/{id}/export?revision=N` exact canonical bytes.
`DatasetSnapshot` create + `{schema_version:'1',id,revision,created_at,label_map_hash,
image_assets:[DatasetImage],stats}`. Stats `{images,reviewed,unreviewed,annotations,
negative_images,duplicate_image_groups,splits:{train,validation,test,unspecified}}`.
`DatasetDetail` adds `{manifest_hash,manifest_size_bytes,download_url}`.
Dataset/model/image revisions never overwrite prior labels or exact PNG bytes.

## Saved evaluations (shared durable queue, kind=evaluation)

POST `/detection-evaluations` with stable endpoint-scoped Idempotency-Key:
`{name,note,dataset_id,dataset_revision,split,inference_job_ids:[1..16 terminal jobs],
evaluator_id:'coco-bbox',class_mapping:[{model_label,dataset_label:string|null}],
score_threshold:0.0,diagnostic_iou:0.5}`.
All source jobs must share model manifest and inference configuration/runtime
identity (including original threshold/NMS/limit/seed), plus observed provider, engine/library, preprocessing and postprocessing versions. Repeated input selection
across source jobs is rejected. Every source model label appears exactly once
in class_mapping; null deliberately excludes that model class from this task.
Mapped dataset classes may receive multiple explicitly declared source classes.
Unmapped dataset classes remain in the denominator (possible false negatives).

Root/source key is recording SHA + channel + ordinal + message SHA, then exact
PNG/decoder identity must agree. Every image in the selected split remains a
report row: `evaluated|unreviewed|missing_prediction|failed_prediction|input_changed`.
Sources outside the selected split are disclosed, not evaluated. Failed and
interrupted source jobs may contribute their already completed frames.

Summary fields: common Job lifecycle (`id,submission_seq,status,created_at,
updated_at,started_at,dispatched_at,finished_at,error,worker_id,lease_expires_at,
cancel_requested_at,cancellation_supported,outcome_uncertain,input_hash`),
`name,dataset_id,dataset_revision,dataset_name,evaluator_id,evaluator_version`.
Detail adds `request,dataset_snapshot,source_jobs,evaluator_snapshot,evaluation_input,source_context,report:null|object,
events,events_total,manifest_hash:null|string,manifest_size_bytes:null|int,
download_url:null|string`.
GET `/detection-evaluations?limit&before_seq&status` page, GET `/{id}`,
POST `/{id}/cancel`, GET `/{id}/export` immutable terminal manifest.
GET `/detection-evaluators` capabilities. Exact report/math contract is finalized
against the official evaluator source in detection-evaluation-notes.md.

Aggregate metrics are withheld (`metrics:null`) unless every selected image is
reviewed and has a valid matched prediction result. Partial per-image diagnostics
may be shown explicitly as a subset; no silent denominator shrink. Completed
zero predictions on a reviewed positive image are misses, while unattempted/
failed inference is unavailable evidence. COCO-style AP is on the stored,
post-NMS/thresholded/limited predictions; original preprocessing/threshold/limit
remain prominent and this is not an official benchmark submission.

The evaluator runs in a cancellable bounded child with versioned package/policy
identity. Current commercial goal, CARLA actual server and real-world validity
gates remain open. Authored fixtures exercise evaluator arithmetic and UI flows;
they do not measure road perception performance.

The authoritative typed metric contract is backend/src/ade2e/evaluator_runtime.py.
Diagnostics use all retained mapped predictions (up to300/image); COCO AP uses
maxDets100/image/category. Crowd matches use intersection/detection area per the
official evaluator. These policies are recorded independently.

The core caps the complete prepared snapshot at16MiB and evaluator report at12MiB;
terminal export is bounded at32MiB to reserve space for lifecycle/failure sealing.
Full original source job manifests remain pinned, even for source rows outside
the selected split. The native subprocess has its own32MiB request/response cap.

`evaluation_input` is the exact evaluator request: ordered dataset taxonomy,
score threshold, diagnostic IoU and all selected rows with source-pixel dimensions,
pinned annotations and explicitly mapped stored predictions. `source_context`
contains shared `inference_configuration`, nullable `observed_runtime_context`,
per-image lineage `rows` (status/reason/source job/selection/PNG hash/input identity/
excluded_class_predictions), `outside_split_sources`, and total
`excluded_class_predictions`. Neither omitted model classes nor out-of-split
sources disappear without disclosure. Diagnostic IoU is one of 0.50..0.95 in
steps of 0.05; the report records all AP and diagnostic parameters independently.

Submission pins at most 16 MiB of canonical input/source snapshots. Published
reports are bounded to 12 MiB; terminal exports are bounded to32 MiB, reserving
space to seal cancellation/failure metadata. Oversize requests fail explicitly.
The evaluator runs outside database transactions; report publication and the
terminal manifest share one lease-fenced transaction. Worker loss preserves the
full selected input and an interrupted outcome without auto-replay. Simulation,
perception and evaluation share queue sequence/lease machinery but retain typed
subjects; evaluations do not create scenario runs or driving validation evidence.
Schema7 rebuilds the SQLite queue parent constraints transactionally while keeping
old IDs, sequence high-watermarks, child relations and exact stored manifests.
