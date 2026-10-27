"""Replaceable detection-evaluation contract, independent of native metric code."""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Annotated, Callable, Literal, Protocol

from pydantic import Field
from .dataset_schemas import DatasetAnnotation
from .adapters import AdapterCancelled
from .schemas import StrictModel

Value = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]
RowStatus = Literal['evaluated','unreviewed','missing_prediction','failed_prediction','input_changed']
DiagnosticIoU = Literal[.5,.55,.6,.65,.7,.75,.8,.85,.9,.95]


class EvaluationInputError(ValueError):
    """Unsupported or inconsistent evaluation input."""


class EvaluationExecutionError(RuntimeError):
    """Evaluation child failed without valid result evidence."""


class EvaluationCancelled(AdapterCancelled):
    """The evaluation child has stopped after cancellation."""


class EvaluatorMetadata(StrictModel):
    id: str
    name: str
    version: str
    policy_version: str
    status: Literal['available','not_installed','unavailable']
    implementation: dict[str,str] = Field(default_factory=dict)
    limits: dict[str,int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class EvaluationPrediction(StrictModel):
    id: str
    label: str
    score: Value
    bbox_xyxy: tuple[float,float,float,float]


class EvaluationInputRow(StrictModel):
    image_id: str
    status: RowStatus
    width: int = Field(ge=1, strict=True)
    height: int = Field(ge=1, strict=True)
    annotations: list[DatasetAnnotation] = Field(default_factory=list, max_length=100)
    predictions: list[EvaluationPrediction] = Field(default_factory=list, max_length=300)


class EvaluationInput(StrictModel):
    labels: list[str] = Field(min_length=1, max_length=256)
    score_threshold: Value
    diagnostic_iou: DiagnosticIoU = .5
    rows: list[EvaluationInputRow] = Field(min_length=1, max_length=256)


class EvaluationCoverage(StrictModel):
    total: Count
    evaluated: Count
    unreviewed: Count
    missing_prediction: Count
    failed_prediction: Count
    input_changed: Count
    complete: bool


class EvaluationMetrics(StrictModel):
    ap: Value | None
    ap50: Value | None
    ap75: Value | None
    ap_small: Value | None
    ap_medium: Value | None
    ap_large: Value | None
    ar1: Value | None
    ar10: Value | None
    ar100: Value | None
    ar_small: Value | None
    ar_medium: Value | None
    ar_large: Value | None
    precision: Value | None
    recall: Value | None
    f1: Value | None


class EvaluationPRCurve(StrictModel):
    recall: list[Value]
    precision: list[Value]


class EvaluationClass(StrictModel):
    label: str
    gt: Count
    crowd_gt: Count
    predictions: Count
    tp: Count
    fp: Count
    fn: Count
    crowd_ignored: Count
    ap: Value | None
    pr_curve: EvaluationPRCurve | None


class EvaluationMatch(StrictModel):
    outcome: Literal['true_positive','false_positive','crowd_ignored','false_negative']
    prediction_id: str | None
    annotation_id: str | None
    iou: Value | None


class EvaluationDiagnostics(StrictModel):
    gt: Count
    crowd_gt: Count
    predictions: Count
    score_filtered: Count
    tp: Count
    fp: Count
    fn: Count
    crowd_ignored: Count
    matches: list[EvaluationMatch]


class EvaluationReportRow(StrictModel):
    image_id: str
    status: RowStatus
    diagnostics: EvaluationDiagnostics | None


class EvaluationReport(StrictModel):
    policy_version: str
    implementation: dict[str,str]
    parameters: dict
    coverage: EvaluationCoverage
    metrics: EvaluationMetrics | None
    diagnostic_scope: Literal['evaluated_rows'] = 'evaluated_rows'
    classes: list[EvaluationClass]
    rows: list[EvaluationReportRow]
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)


class DetectionEvaluator(Protocol):
    def metadata(self) -> EvaluatorMetadata: ...
    def evaluate(self, data: EvaluationInput, should_cancel: Callable[[],bool]) -> EvaluationReport: ...


class UnavailableEvaluator:
    def metadata(self):
        return EvaluatorMetadata(id='coco-bbox', name='COCO bbox on stored predictions', version='0.1.0',
            policy_version='coco-bbox-stored/1', status='not_installed',
            limitations=['Install the optional axiom-coco-evaluator on API and worker hosts.',
                         'No official benchmark or real-world performance claim is implied.'])

    def evaluate(self, *_args, **_kwargs):
        raise ValueError('Optional COCO evaluation package is not installed')


class EvaluatorRegistry:
    def __init__(self, load_plugins=True):
        self._evaluators = {}
        self.register(UnavailableEvaluator())
        if load_plugins:
            for entry in entry_points(group='ade2e.detection_evaluators'):
                factory = entry.load()
                self.register(factory() if callable(factory) else factory, replace=True)

    def register(self, evaluator, *, replace=False):
        metadata = EvaluatorMetadata.model_validate(evaluator.metadata())
        if not callable(getattr(evaluator, 'evaluate', None)):
            raise TypeError('Evaluator must implement evaluate(data, should_cancel)')
        if metadata.id in self._evaluators and not replace:
            raise ValueError('Evaluator is already registered; use replace=True explicitly')
        self._evaluators[metadata.id] = evaluator

    def get(self, evaluator_id):
        return self._evaluators.get(evaluator_id)

    def list(self):
        return [EvaluatorMetadata.model_validate(item.metadata()) for item in self._evaluators.values()]
