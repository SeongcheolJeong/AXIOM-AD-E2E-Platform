"""Metadata and process boundary; no numpy or pycocotools import in the host."""
from importlib.metadata import PackageNotFoundError, version
import sys

from ade2e.evaluator_runtime import (EvaluationCancelled, EvaluationExecutionError,
                                   EvaluationInput, EvaluationInputError, EvaluationReport,
                                   EvaluatorMetadata)
from ade2e.process_transport import ManagedJsonProcess

VERSION = "0.1.0"
POLICY_VERSION = "coco-bbox-stored/1"
PACKAGES = {"pycocotools": "2.0.11", "numpy": "2.5.3"}
TRANSPORT_LIMIT = 32 * 1024 * 1024


class CocoBboxEvaluator:
    def metadata(self):
        installed = {}
        for package in PACKAGES:
            try:
                installed[package] = version(package)
            except PackageNotFoundError:
                pass
        status = "not_installed" if len(installed) != len(PACKAGES) else "available" if installed == PACKAGES else "unavailable"
        return EvaluatorMetadata(id="coco-bbox", name="COCO bbox on stored predictions", version=VERSION,
            policy_version=POLICY_VERSION, status=status, implementation=installed,
            limits={"images": 256, "labels": 256, "annotations_per_image": 100,
                    "predictions_per_image": 300, "image_pixels": 2_000_000,
                    "request_bytes": TRANSPORT_LIMIT, "response_bytes": TRANSPORT_LIMIT,
                    "operation_timeout_seconds": 120},
            limitations=["Metrics describe saved predictions after their original threshold, NMS and limit.",
                         "Incomplete coverage withholds aggregate AP, PR curves and operating-point metrics.",
                         "Crowd boxes follow COCO ignore semantics; arbitrary ignore flags are unsupported.",
                         "Area strata use bounding-box area, not segmentation-mask area.",
                         "No official benchmark, annotation certification or real-world accuracy claim is implied."])

    def evaluate(self, data: EvaluationInput, should_cancel) -> EvaluationReport:
        try:
            data = EvaluationInput.model_validate(data)
        except ValueError as error:
            raise EvaluationInputError("Input violates the typed evaluation contract") from error
        if self.metadata().status != "available":
            raise EvaluationInputError("Pinned pycocotools 2.0.11 and numpy 2.5.3 are required")
        with ManagedJsonProcess([sys.executable, "-m", "axiom_coco.child"], {}, should_cancel,
                timeout=120, max_request_bytes=TRANSPORT_LIMIT, max_response_bytes=TRANSPORT_LIMIT,
                input_error=EvaluationInputError, execution_error=EvaluationExecutionError,
                cancel_error=EvaluationCancelled) as child:
            reply = child.request(data.model_dump(mode="json"), should_cancel)
            if reply.get("type") != "evaluation":
                raise EvaluationExecutionError("Unexpected evaluation child response type")
            try:
                return EvaluationReport.model_validate(reply["report"])
            except (ValueError, KeyError, TypeError) as error:
                raise EvaluationExecutionError("Child output violates the typed evaluation report") from error
