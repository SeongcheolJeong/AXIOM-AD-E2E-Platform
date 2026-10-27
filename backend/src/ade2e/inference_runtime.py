"""Replaceable image inference contracts; engine imports stay in optional packages."""
from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Callable, ContextManager, Literal, Protocol

from pydantic import Field

from .adapters import AdapterCancelled
from .schemas import StrictModel


class RuntimeMetadata(StrictModel):
    id: str
    name: str
    version: str
    status: Literal["available", "not_installed", "unavailable"]
    profiles: list[str]
    limits: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class Detection(StrictModel):
    id: str
    class_id: int = Field(ge=0)
    label: str
    score: float = Field(ge=0, le=1)
    bbox_xyxy: tuple[float, float, float, float]


class RuntimeEvidence(StrictModel):
    runtime_id: str
    runtime_version: str
    engine: str
    engine_version: str
    library_versions: dict[str, str] = Field(default_factory=dict)
    provider: str
    model_sha256: str
    profile: str
    preprocessing_version: str
    postprocessing_version: str
    image_sha256: str
    tensor_sha256: str
    tensor_shape: list[int]
    transform: dict
    source_candidates: int = Field(ge=0)
    threshold_candidates: int = Field(ge=0)
    suppressed_candidates: int = Field(ge=0)
    clipped_detections: int = Field(ge=0)
    discarded_empty_boxes: int = Field(ge=0)
    max_detections_truncated: int = Field(ge=0)
    inference_ms: float = Field(ge=0)
    preprocessing_ms: float = Field(ge=0)
    postprocessing_ms: float = Field(ge=0)
    seed: int
    warnings: list[str] = Field(default_factory=list)


class InferencePrediction(StrictModel):
    detections: list[Detection]
    evidence: RuntimeEvidence


class InferenceInputError(ValueError):
    """Unsupported model/image/profile; no valid prediction was produced."""


class InferenceExecutionError(RuntimeError):
    """Native inference failed; this is not a successful empty detection list."""


class InferenceCancelled(AdapterCancelled):
    """The inference child has stopped and cancellation is acknowledged."""


class InferenceSession(Protocol):
    def infer(self, png: bytes, *, width: int, height: int, score_threshold: float,
              iou_threshold: float, max_detections: int,
              should_cancel: Callable[[], bool]) -> InferencePrediction: ...


class InferenceRuntime(Protocol):
    def metadata(self) -> RuntimeMetadata: ...

    def open(self, model_path: Path, model_definition: dict, model_sha256: str,
             seed: int, should_cancel: Callable[[], bool]) -> ContextManager[InferenceSession]: ...


class UnavailableRuntime:
    def metadata(self):
        return RuntimeMetadata(id="onnx-cpu", name="ONNX Runtime CPU", version="0.1.0", status="not_installed",
                               profiles=["opencv_yolox_rgb_v1", "boxes_xyxy_v1"],
                               limitations=["Install the optional axiom-onnx-adapter package on API and worker hosts.",
                                            "Model registration is not compatibility or accuracy validation."])

    def open(self, *_args, **_kwargs):
        raise InferenceInputError("The optional ONNX inference runtime is not installed")


class RuntimeRegistry:
    def __init__(self, load_plugins: bool = True):
        self._runtimes: dict[str, InferenceRuntime] = {}
        self.register(UnavailableRuntime())
        if load_plugins:
            for entry in entry_points(group="ade2e.inference_runtimes"):
                factory = entry.load()
                self.register(factory() if callable(factory) else factory, replace=True)

    def register(self, runtime: InferenceRuntime, *, replace: bool = False):
        metadata = RuntimeMetadata.model_validate(runtime.metadata())
        if not callable(getattr(runtime, "open", None)):
            raise TypeError("An inference runtime must implement open(model_path, definition, sha256, seed, should_cancel)")
        if metadata.id in self._runtimes and not replace:
            raise ValueError("Runtime is already registered; use replace=True explicitly")
        self._runtimes[metadata.id] = runtime

    def get(self, runtime_id: str) -> InferenceRuntime | None:
        return self._runtimes.get(runtime_id)

    def list(self) -> list[RuntimeMetadata]:
        return [RuntimeMetadata.model_validate(runtime.metadata()) for runtime in self._runtimes.values()]
