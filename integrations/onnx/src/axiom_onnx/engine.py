"""Explicit ONNX image profiles. Imported only inside the inference child/tests."""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import time

import cv2
import numpy as np
import onnx
import onnxruntime as ort
from PIL import Image, __version__ as pillow_version

from ade2e.inference_runtime import Detection, InferenceInputError, InferencePrediction, RuntimeEvidence
from .runtime import VERSION

PROFILES = {"opencv_yolox_rgb_v1", "boxes_xyxy_v1"}


def validate_model(raw: bytes, definition: dict):
    if not 0 < len(raw) <= 128 * 1024 * 1024:
        raise InferenceInputError("ONNX file exceeds the 128 MiB bound or is empty")
    profile = definition.get("profile")
    if profile not in PROFILES:
        raise InferenceInputError("This runtime does not support the declared model profile")
    width, height = definition.get("input_width"), definition.get("input_height")
    if any(type(value) is not int or not 32 <= value <= 1280 for value in (width, height)):
        raise InferenceInputError("Declared input dimensions must be 32..1280")
    if profile == "opencv_yolox_rgb_v1" and (width % 32 or height % 32):
        raise InferenceInputError("YOLOX dimensions must be divisible by 32")
    labels = definition.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 256 or any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
        raise InferenceInputError("An explicit unique ordered label map is required")
    try:
        model = onnx.load_model_from_string(raw)
    except Exception as exc:
        raise InferenceInputError("Invalid ONNX protobuf") from exc
    if model.functions or model.training_info or model.graph.sparse_initializer:
        raise InferenceInputError("Local functions, training graphs and sparse initializers are unsupported")
    if any(op.domain not in ("", "ai.onnx") for op in model.opset_import):
        raise InferenceInputError("Custom operator domains are unsupported")
    if not 1 <= len(model.graph.node) <= 10000:
        raise InferenceInputError("Model graph node count is outside the supported bound")
    tensors = list(model.graph.initializer)
    forbidden = {"RandomNormal", "RandomNormalLike", "RandomUniform", "RandomUniformLike", "Multinomial", "Bernoulli"}
    for node in model.graph.node:
        if node.domain not in ("", "ai.onnx") or node.op_type in forbidden:
            raise InferenceInputError("Custom or random operators are unsupported by these deterministic profiles")
        for attribute in node.attribute:
            if attribute.type in (onnx.AttributeProto.GRAPH, onnx.AttributeProto.GRAPHS,
                                  onnx.AttributeProto.SPARSE_TENSOR, onnx.AttributeProto.SPARSE_TENSORS):
                raise InferenceInputError("Nested graphs and sparse tensor attributes are unsupported")
            if attribute.type == onnx.AttributeProto.TENSOR:
                tensors.append(attribute.t)
            if attribute.type == onnx.AttributeProto.TENSORS:
                tensors.extend(attribute.tensors)
    if any(tensor.data_location == onnx.TensorProto.EXTERNAL or tensor.external_data for tensor in tensors):
        raise InferenceInputError("ONNX external data is forbidden; provide one self-contained model")
    if len(model.graph.input) != 1 or len(model.graph.output) != 1:
        raise InferenceInputError("Profiles require exactly one image input and one detection output")
    image_type = model.graph.input[0].type.tensor_type
    dimensions = image_type.shape.dim
    if image_type.elem_type != onnx.TensorProto.FLOAT or len(dimensions) != 4:
        raise InferenceInputError("Image input must be float32 NCHW")
    for declared, expected in zip(dimensions, (1, 3, height, width)):
        if declared.HasField("dim_value") and declared.dim_value != expected:
            raise InferenceInputError("Declared model dimensions differ from the ONNX input signature")
    try:
        onnx.checker.check_model(model)
    except Exception as exc:
        raise InferenceInputError("ONNX structural validation failed") from exc
    return model


def preprocess(png: bytes, width: int, height: int, definition: dict):
    if type(width) is not int or type(height) is not int or width < 1 or height < 1 or width * height > 2_000_000:
        raise InferenceInputError("Source image dimensions exceed the runtime bound")
    if not 0 < len(png) <= 8 * 1024 * 1024:
        raise InferenceInputError("PNG input size is outside the runtime bound")
    try:
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or image.size != (width, height) or getattr(image, "n_frames", 1) != 1:
                raise InferenceInputError("PNG dimensions or format differ from the supplied input identity")
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    except InferenceInputError:
        raise
    except Exception as exc:
        raise InferenceInputError("Cannot decode the exact input PNG") from exc
    target_w, target_h = definition["input_width"], definition["input_height"]
    yolox = definition["profile"] == "opencv_yolox_rgb_v1"
    if yolox:
        ratio = min(target_w / width, target_h / height)
        resized_w, resized_h = int(width * ratio), int(height * ratio)
    else:
        resized_w, resized_h = target_w, target_h
    if min(resized_w, resized_h) < 1:
        raise InferenceInputError("Image aspect ratio collapses a resized dimension")
    resized = cv2.resize(rgb, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    padded = np.full((target_h, target_w, 3), 114 if yolox else 0, dtype=np.uint8)
    padded[:resized_h, :resized_w] = resized
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype="<f4")
    if not yolox:
        tensor /= np.float32(255)
    transform = {"source_width": width, "source_height": height, "input_width": target_w, "input_height": target_h,
                 "resized_width": resized_w, "resized_height": resized_h, "scale_x": resized_w / width,
                 "scale_y": resized_h / height, "pad_left": 0, "pad_top": 0, "pad_right": target_w - resized_w,
                 "pad_bottom": target_h - resized_h, "resize": "opencv_inter_linear", "color_order": "RGB",
                 "layout": "NCHW", "value_scale": 1.0 if yolox else 1 / 255, "padding_value": 114.0 if yolox else 0.0,
                 "box_inverse": "xyxy_divide_scale_then_clip", "nms": "class_aware_continuous_iou_v1"}
    return tensor, transform


def postprocess(output, definition, transform, score_threshold, iou_threshold, max_detections):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
           for v in (score_threshold, iou_threshold)) or type(max_detections) is not int or not 1 <= max_detections <= 300:
        raise InferenceInputError("Invalid detection threshold/NMS/limit configuration")
    values = np.asarray(output)
    if values.dtype != np.float32 or values.ndim != 3 or values.shape[0] != 1 or values.size > 10_000_000 or not np.isfinite(values).all():
        raise InferenceInputError("Detection output must be bounded finite float32 [1,N,C]")
    labels = definition["labels"]
    rows = values[0].astype(np.float64)
    if definition["profile"] == "opencv_yolox_rgb_v1":
        width, height = definition["input_width"], definition["input_height"]
        anchors = sum((width // stride) * (height // stride) for stride in (8, 16, 32))
        if rows.shape != (anchors, 5 + len(labels)):
            raise InferenceInputError("Output shape differs from the pinned YOLOX raw grid profile")
        if np.any((rows[:, 4:] < 0) | (rows[:, 4:] > 1)):
            raise InferenceInputError("YOLOX objectness/class outputs are outside [0,1]")
        grids, strides = [], []
        for stride in (8, 16, 32):
            xx, yy = np.meshgrid(np.arange(width // stride), np.arange(height // stride))
            grid = np.stack((xx, yy), axis=-1).reshape(-1, 2)
            grids.append(grid)
            strides.append(np.full((len(grid), 1), stride))
        grid, stride = np.concatenate(grids), np.concatenate(strides)
        if np.any(np.abs(rows[:, 2:4]) > 80):
            raise InferenceInputError("YOLOX box exponent is outside the bounded decode range")
        center = (rows[:, :2] + grid) * stride
        extent = np.exp(rows[:, 2:4]) * stride
        boxes = np.concatenate((center - extent / 2, center + extent / 2), axis=1)
        class_ids = np.argmax(rows[:, 5:], axis=1)
        scores = rows[:, 4] * rows[np.arange(len(rows)), class_ids + 5]
    else:
        if rows.shape[1] != 6 or len(rows) > 50000:
            raise InferenceInputError("Direct detection profile requires [1,N,6], at most 50,000 candidates")
        boxes, scores = rows[:, :4], rows[:, 4]
        if np.any((scores < 0) | (scores > 1)) or np.any(rows[:, 5] != np.floor(rows[:, 5])) or np.any((rows[:, 5] < 0) | (rows[:, 5] >= len(labels))):
            raise InferenceInputError("Scores or class indices violate the pinned label map")
        class_ids = rows[:, 5].astype(np.int64)
    candidates = np.flatnonzero(scores >= score_threshold)
    if len(candidates) > 4096:
        raise InferenceInputError("More than 4,096 candidates pass the score threshold; increase the threshold")
    ordered = sorted(candidates.tolist(), key=lambda index: (-float(scores[index]), index))
    kept, suppressed = [], 0
    while ordered:
        index, ordered = ordered[0], ordered[1:]
        kept.append(index)
        if not ordered:
            break
        others = np.asarray(ordered)
        intersect_extent = np.maximum(0, np.minimum(boxes[index, 2:], boxes[others, 2:]) - np.maximum(boxes[index, :2], boxes[others, :2]))
        intersection = intersect_extent[:, 0] * intersect_extent[:, 1]
        extent = np.maximum(0, boxes[others, 2:] - boxes[others, :2])
        area_i = np.prod(np.maximum(0, boxes[index, 2:] - boxes[index, :2]))
        union = area_i + extent[:, 0] * extent[:, 1] - intersection
        iou = np.divide(intersection, union, out=np.zeros_like(union), where=union > 0)
        drop = (class_ids[others] == class_ids[index]) & (iou > iou_threshold)
        suppressed += int(drop.sum())
        ordered = others[~drop].tolist()
    detections, clipped, empty, truncated = [], 0, 0, 0
    for index in kept:
        bbox = boxes[index] / np.array([transform["scale_x"], transform["scale_y"]] * 2)
        bounded = np.clip(bbox, [0, 0, 0, 0], [transform["source_width"], transform["source_height"]] * 2)
        if bounded[2] <= bounded[0] or bounded[3] <= bounded[1]:
            empty += 1
            continue
        if len(detections) >= max_detections:
            truncated += 1
            continue
        clipped += int(not np.array_equal(bbox, bounded))
        class_id = int(class_ids[index])
        detections.append(Detection(id=f"d{index}", class_id=class_id, label=labels[class_id], score=float(scores[index]),
                                    bbox_xyxy=tuple(float(value) for value in bounded)))
    return detections, dict(source_candidates=len(rows), threshold_candidates=len(candidates), suppressed_candidates=suppressed,
                            clipped_detections=clipped, discarded_empty_boxes=empty, max_detections_truncated=truncated)


class Engine:
    def __init__(self, path: Path, definition: dict, sha256: str, seed: int):
        self.definition, self.sha256, self.seed = definition, sha256, seed
        if path.stat().st_size > 128 * 1024 * 1024:
            raise InferenceInputError("Model exceeds the supported size")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise InferenceInputError("Model bytes differ from the pinned SHA-256")
        model = validate_model(raw, definition)
        ort.set_seed(seed)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        options.add_session_config_entry("session.use_deterministic_compute", "1")
        self.session = ort.InferenceSession(model.SerializeToString(), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise InferenceInputError("Runtime did not bind exclusively to CPUExecutionProvider")

    def infer(self, png: bytes, *, width: int, height: int, score_threshold: float, iou_threshold: float, max_detections: int):
        started = time.perf_counter()
        tensor, transform = preprocess(png, width, height, self.definition)
        before = time.perf_counter()
        output = self.session.run(None, {self.input_name: tensor})[0]
        after = time.perf_counter()
        detections, counts = postprocess(output, self.definition, transform, score_threshold, iou_threshold, max_detections)
        finished = time.perf_counter()
        return InferencePrediction(detections=detections, evidence=RuntimeEvidence(
            runtime_id="onnx-cpu", runtime_version=VERSION, engine="onnxruntime", engine_version=ort.__version__, provider="CPUExecutionProvider",
            library_versions={"onnx": onnx.__version__, "numpy": np.__version__, "opencv": cv2.__version__, "Pillow": pillow_version},
            model_sha256=self.sha256, profile=self.definition["profile"], preprocessing_version="opencv_inter_linear_v1",
            postprocessing_version="class_aware_xyxy_v1", image_sha256=hashlib.sha256(png).hexdigest(),
            tensor_sha256=hashlib.sha256(tensor.tobytes()).hexdigest(), tensor_shape=list(tensor.shape), transform=transform,
            **counts, preprocessing_ms=(before-started)*1000, inference_ms=(after-before)*1000,
            postprocessing_ms=(finished-after)*1000, seed=self.seed,
            warnings=["Model scores and detections are predictions, not ground truth or calibrated probabilities.",
                      "Reported durations describe this single local operation, not deployment throughput."]))
