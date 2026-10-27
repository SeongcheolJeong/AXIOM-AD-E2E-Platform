"""Plan conditions are human declarations, never automatically inferred ODD."""
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel
from .detection_evaluation_schemas import ClassMapping, Split

class PlanModel(StrictModel):
    model_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    class_mapping: list[ClassMapping] = Field(min_length=1, max_length=256)

class Criterion(StrictModel):
    metric: Literal['ap','ap50','ap75','ap_small','ap_medium','ap_large','precision','recall','f1'] = 'ap'
    minimum_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    maximum_drop: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    minimum_images: int | None = Field(default=None, ge=1, le=256)
    minimum_objects: int | None = Field(default=None, ge=0, le=25600)
    minimum_recordings: int | None = Field(default=None, ge=1, le=256)

class ConditionAxis(StrictModel):
    cardinality: Literal['single','multiple'] = 'single'
    name: str = Field(min_length=1, max_length=80)
    values: list[str] = Field(min_length=1, max_length=32)

class PlanCell(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=180)
    conditions: dict[str, str] = Field(default_factory=dict)
    image_ids: list[str] = Field(default_factory=list, max_length=256)
    labels: list[str] = Field(default_factory=list, max_length=256)
    review_status: Literal['draft','reviewed'] = 'draft'
    reviewer: str = Field(default='', max_length=180)
    review_note: str = Field(default='', max_length=1500)
    source_scope: Literal['image'] = 'image'
    source_taxonomy: str = Field(default='plan-manual', max_length=180)
    source_revision: str = Field(default='1', max_length=80)
    mapping_note: str = Field(default='', max_length=1500)
    criterion: Criterion = Field(default_factory=Criterion)

class EvidenceRef(StrictModel):
    kind: Literal['collection','dataset','case','evaluation','run','suite','campaign']
    id: str = Field(min_length=1, max_length=64)
    revision: int | None = Field(default=None, ge=1)
    role: Literal['context','diagnostic'] = 'context'

class VerificationPlanCreate(StrictModel):
    verification_type: Literal['detection_ab'] = 'detection_ab'
    allowed_evidence: Literal['reviewed_dataset_predictions'] = 'reviewed_dataset_predictions'
    name: str = Field(min_length=1, max_length=180)
    purpose: str = Field(min_length=1, max_length=4000)
    author: str = Field(min_length=1, max_length=180)
    change_reason: str = Field(min_length=1, max_length=1500)
    dataset_id: str = Field(min_length=1, max_length=64)
    dataset_revision: int = Field(ge=1)
    split: Split = 'test'
    models: list[PlanModel] = Field(min_length=2, max_length=2)
    runtime_id: str = Field(default='onnx-cpu', min_length=1, max_length=180)
    evaluator_id: str = Field(default='coco-bbox', min_length=1, max_length=180)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    score_threshold: float = Field(default=.35, ge=0, le=1, allow_inf_nan=False)
    iou_threshold: float = Field(default=.45, ge=0, le=1, allow_inf_nan=False)
    max_detections: int = Field(default=100, ge=1, le=300)
    axes: list[ConditionAxis] = Field(default_factory=list, max_length=8)
    cells: list[PlanCell] = Field(default_factory=list, max_length=32)
    criterion: Criterion = Field(default_factory=Criterion)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=32)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def validate_conditions(self):
        if any(not getattr(self, key).strip() for key in ('name','purpose','author','change_reason')):
            raise ValueError('Plan descriptions and declarations must be nonblank')
        if any(not a.name.strip() or any(not v.strip() for v in a.values) for a in self.axes):
            raise ValueError('Condition axes/values must be nonblank')
        if any(cell.id == '__overall__' for cell in self.cells):
            raise ValueError('The overall cell ID is reserved')
        axes = {axis.name: set(axis.values) for axis in self.axes}
        if len(axes) != len(self.axes) or any(len(a.values) != len(set(a.values)) for a in self.axes):
            raise ValueError('Condition axes/values must be unique')
        if len({cell.id for cell in self.cells}) != len(self.cells):
            raise ValueError('Condition cell IDs must be unique')
        for cell in self.cells:
            if len(set(cell.image_ids)) != len(cell.image_ids) or len(set(cell.labels)) != len(cell.labels):
                raise ValueError('Cell images and labels must be unique')
            if set(cell.conditions) != set(axes) or any(v not in axes[k] for k,v in cell.conditions.items()):
                raise ValueError('Every cell must select one declared value per axis')
            if cell.review_status == 'reviewed' and (not cell.reviewer.strip() or not cell.review_note.strip()):
                raise ValueError('Reviewed image conditions require a reviewer and review note')
            if cell.source_taxonomy != 'plan-manual' and not cell.mapping_note.strip():
                raise ValueError('External taxonomy interpretations require an explicit mapping note')
        return self

class PlanAttemptCreate(StrictModel):
    revision: int = Field(ge=1)
    expected_preview_hash: str = Field(min_length=64, max_length=64)
    confirm_trusted_models: bool = Field(strict=True)
    retry_of: str | None = Field(default=None, max_length=64)
