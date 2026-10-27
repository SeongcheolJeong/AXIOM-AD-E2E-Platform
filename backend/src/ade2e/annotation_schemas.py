"""Explicit trusted-model compute, never automatic ground-truth approval."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .base_schemas import StrictModel
from .case_schemas import Identity
from .detection_evaluation_schemas import ClassMapping
from .model_schemas import Sha256

Revision = Annotated[int, Field(strict=True, ge=1, le=2**31-1)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]


class AnnotationSelection(StrictModel):
    expected_revision: Revision
    model_id: Identity
    model_revision: Revision
    runtime_id: Identity = 'onnx-cpu'
    class_mapping: list[ClassMapping] = Field(min_length=1, max_length=256)
    score_threshold: float = Field(default=.35, ge=0, le=1, allow_inf_nan=False)
    iou_threshold: float = Field(default=.45, ge=0, le=1, allow_inf_nan=False)
    max_detections: int = Field(default=100, ge=1, le=100, strict=True)
    seed: int = Field(default=42, ge=0, le=2**31-1, strict=True)
    note: Note

    @model_validator(mode='after')
    def unique_mapping(self):
        if len({m.model_label for m in self.class_mapping}) != len(self.class_mapping):
            raise ValueError('Map every model class exactly once, using explicit null for exclusions')
        return self


class AnnotationCreate(AnnotationSelection):
    expected_preview_hash: Sha256
    confirm_trusted_model: bool = Field(strict=True)

    @model_validator(mode='after')
    def consent(self):
        if not self.confirm_trusted_model:
            raise ValueError('Explicit consent for trusted model execution is required')
        return self


class AnnotationPublish(StrictModel):
    expected_revision: Revision
    expected_report_hash: Sha256
    note: Note


class AnnotationOrigin(StrictModel):
    task_id: Identity
    model_sha256: Sha256
    model_id: Identity
    model_revision: Revision
    publication_dataset_id: Identity
    publication_dataset_revision: Revision
    publication_hash: Sha256


class AnnotationLineageSnapshot(StrictModel):
    schema_version: Literal['annotation-lineage/1'] = 'annotation-lineage/1'
    dataset_id: Identity
    dataset_revision: Revision
    dataset_manifest_hash: Sha256
    images: dict[Identity, list[AnnotationOrigin]]
    independence_verified: Literal[False] = False

    @model_validator(mode='after')
    def bounded(self):
        if not 1 <= len(self.images) <= 256 or any(not 1 <= len(v) <= 64 for v in self.images.values()):
            raise ValueError('Model-assisted lineage must retain 1..256 images and 1..64 sources per image')
        return self
