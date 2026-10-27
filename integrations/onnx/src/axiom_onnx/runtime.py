"""Optional CPU inference metadata and profile-specific child session."""
from __future__ import annotations

from pathlib import Path
import sys
from importlib.metadata import PackageNotFoundError, version

from ade2e.inference_runtime import (InferenceCancelled, InferenceExecutionError, InferenceInputError,
                                   InferencePrediction, RuntimeMetadata)
from ade2e.process_transport import ManagedJsonProcess

VERSION = "0.1.0"


class OnnxCpuRuntime:
    def metadata(self):
        available = True
        for package in ("onnx", "onnxruntime", "numpy", "opencv-python-headless"):
            try:
                version(package)
            except PackageNotFoundError:
                available = False
        return RuntimeMetadata(id="onnx-cpu", name="ONNX Runtime CPU", version=VERSION,
                               status="available" if available else "not_installed",
                               profiles=["opencv_yolox_rgb_v1", "boxes_xyxy_v1"],
                               limits={"model_bytes": 128 * 1024 * 1024, "image_pixels": 2_000_000,
                                       "input_side": 1280, "detections": 300, "threshold_candidates": 4096, "frame_timeout_seconds": 30},
                               limitations=["Single local CPU child; no GPU or closed-loop control.",
                                            "Model files must be locally trusted. A subprocess is not a security sandbox.",
                                            "Score is a model output, not calibrated probability or accuracy."])

    def open(self, model_path: Path, model_definition: dict, model_sha256: str, seed: int, should_cancel):
        return ChildSession(model_path, model_definition, model_sha256, seed, should_cancel)


class ChildSession(ManagedJsonProcess):
    def __init__(self, model_path, definition, sha256, seed, should_cancel, *, timeout=30):
        super().__init__([sys.executable, "-m", "axiom_onnx.child", str(model_path)],
                         {"definition": definition, "sha256": sha256, "seed": seed}, should_cancel,
                         timeout=timeout, input_error=InferenceInputError,
                         execution_error=InferenceExecutionError, cancel_error=InferenceCancelled)

    def infer(self, png, *, width, height, score_threshold, iou_threshold, max_detections, should_cancel):
        import base64
        if should_cancel():
            self.close()
            raise InferenceCancelled("Inference child stopped before the next image")
        if len(png) > 8 * 1024 * 1024:
            raise InferenceInputError("Decoded PNG exceeds the 8 MiB runtime input bound")
        reply = self.request({"png": base64.b64encode(png).decode(), "width": width, "height": height,
                    "score_threshold": score_threshold, "iou_threshold": iou_threshold, "max_detections": max_detections}, should_cancel)
        if reply.get("type") != "prediction":
            raise InferenceExecutionError("Unexpected inference response type")
        try:
            return InferencePrediction.model_validate(reply["prediction"])
        except (ValueError, KeyError, TypeError) as exc:
            raise InferenceExecutionError("Inference child output violates the typed prediction contract") from exc
