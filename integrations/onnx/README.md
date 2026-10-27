# Optional ONNX CPU inference runtime

This package executes recorded camera PNGs through an actual ONNX Runtime CPU
session. Install it on both the API and worker hosts so capability discovery and
claim routing agree. Native imports occur only in the managed inference child;
model registration and API startup do not parse or execute the graph.

```bash
# Repository root; Python 3.12+ for this pinned NumPy/runtime combination
.venv/bin/python -m pip install -c requirements.lock -e integrations/onnx
.venv/bin/python -m pytest integrations/onnx/tests -q
```

`ade2e.inference_runtimes` advertises `onnx-cpu` version `0.1.0`. Each job opens
one model session, processes 1..64 selected images in order, and closes the child.
The shared worker maintains lease/cancellation checks and commits each frame
separately. Cancellation is acknowledged after the child has stopped. An
unresponsive initialization or frame is stopped after 30 seconds. A failed or
interrupted frame is distinct from a successful zero-detection result.

Supported profiles are explicit, versioned image/output contracts:

| Profile | Input | Output |
| --- | --- | --- |
| `opencv_yolox_rgb_v1` | RGB 0..255 float32 NCHW; aspect-preserving OpenCV linear resize, top-left 114 padding | YOLOX raw stride 8/16/32 grid, objectness × class score |
| `boxes_xyxy_v1` | RGB /255 float32 NCHW; OpenCV linear stretch resize | `[1,N,6]` pixel xyxy, score, zero-based ordered class ID |

Both use deterministic class-aware continuous-IoU NMS, then inverse mapping by
actual rounded resize dimensions and clipping to original image bounds. The
saved transform, versions, tensor/image/model SHA-256, candidate accounting and
timings describe the exact operation. Input, preprocessing and postprocessing
identities are separate. A runtime-reported tensor digest alone is not proof
that a plugin used those pixels; behavioral tests exercise actual input changes.

Limits: 128 MiB self-contained model; one float32 NCHW input and one output;
32..1280 input sides (YOLOX multiples of 32); 2,000,000 source pixels; 8 MiB PNG;
256 labels; 4,096 threshold-passing candidates; 300 retained detections. External
tensor files, custom domains, local functions, random operators, nested/training
graphs and sparse initializers are refused. Only CPUExecutionProvider is enabled.
A subprocess and these format restrictions are **not an OS security sandbox**;
this local milestone accepts administrator-trusted models. Remote adversarial
model execution requires a separate isolation/resource policy before deployment.

The original pixel-rule ONNX fixture exercises actual input-dependent graph
execution; it is not a trained object detector. The optional trained model fetch
is explicit and SHA-pinned; setup does not download weights:

```bash
.venv/bin/python examples/fetch_opencv_yolox.py .runtime/opencv-model
.venv/bin/python examples/perception_workflow.py \
  --output .runtime/perception-example \
  --opencv-model .runtime/opencv-model/opencv-yolox-s.onnx
```

Start the API and worker first. This example uses authored image pixels. It does
not measure road perception accuracy, calibrate scores, or connect predictions
to planning/control. See [model provenance](../../docs/perception-model-notes.md),
[shared contract](../../docs/perception-contract.md) and
[workflow](../../docs/perception.md).
