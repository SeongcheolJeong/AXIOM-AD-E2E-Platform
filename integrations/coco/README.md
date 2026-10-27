# COCO bbox evaluator

`axiom-coco-evaluator` is an optional CPU adapter for evaluating saved detections
against a fixed, reviewed dataset revision. ID `coco-bbox`, adapter version
`0.1.0`, policy `coco-bbox-stored/1`. It registers through
`ade2e.detection_evaluators`; the UI and worker use the shared API contract.

Install in the same Python environment used by both the API and worker:

```sh
.venv/bin/python -m pip install -e integrations/coco
.venv/bin/python -m pytest integrations/coco/tests -q
```

Restart the API and worker after installing. Dependencies are pinned to
`pycocotools==2.0.11` and `numpy==2.5.3`, requiring Python 3.12 or newer.
Registration reads package metadata; native imports happen in an owned child
process. Missing or changed package versions fail closed. A cancellation request
terminates that child before acknowledgement; each transport/compute operation
has a 120-second deadline. The subprocess is not an operating-system sandbox.

Limits are 256 images, 256 dataset classes, 100 annotations and 300 saved mapped
predictions per image, 2,000,000 pixels per source image, and 32 MiB per request
or response. Geometry is finite, positive-area, continuous original-image pixel
`xyxy`; boxes outside the image are rejected. Segmentation/RLE and arbitrary
`ignore` attributes are unsupported. `iscrowd` uses actual COCO matching and
intersection-over-detection-area semantics.

AP uses the pinned Python evaluator's default IoU/recall/area grids and
`maxDets=[1,10,100]`. Its class-aware cap is per image/category. Separate
diagnostic matching includes all retained predictions up to 300 per image,
so extra false positives remain visible. Scale metrics use bounding-box area;
they are not COCO mask-area strata. Scores, NMS and limits applied during the
original inference are already present in the saved predictions. Additional
evaluation score filtering cannot recover detections removed earlier.

All selected images remain in the coverage denominator. Failed, missing,
unreviewed or changed-input rows have no diagnostic result. Incomplete coverage
withholds aggregate metrics, per-class AP and PR curves; partial row counts and
matches are explicitly labelled as covering evaluated rows. An evaluated empty
prediction list is a valid model result, distinct from unavailable evidence.
Absent ground-truth classes have undefined AP, even if their predictions produce
false positives; counts preserve those false positives.

These calculations do not establish annotation quality, training/test
independence, official benchmark performance, or real-world driving accuracy.
Arithmetic fixtures establish arithmetic behavior only.

See [the evaluation contract](../../docs/datasets-contract.md),
[source and metric notes](../../docs/detection-evaluation-notes.md), and
[the upstream license](third_party/pycocotools-license.txt).
