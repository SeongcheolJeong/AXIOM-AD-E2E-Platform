"""Original input-dependent ONNX contract fixture and synthetic MCAP images.

This computes one bounding union of red pixels, not learned object perception.
It is deliberately separate from the downloaded trained OpenCV Zoo detector.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from PIL import Image, ImageDraw
from mcap.writer import Writer, CompressionType

BASE_NS = 1700000010000000123
SIZE = 64
LABELS = ["red region (authored pixel rule)"]


def make_model(destination: Path, gain: float = 1.0):
    if not 0 < gain <= 1:
        raise ValueError("Fixture score gain must be within (0,1]")
    initializers, nodes = [], []

    def constant(name, value, dtype=np.float32):
        initializers.append(numpy_helper.from_array(np.asarray(value, dtype=dtype), name))
        return name

    def node(op, inputs, output, **attributes):
        nodes.append(helper.make_node(op, inputs, [output], **attributes))
        return output

    for index, channel in enumerate(("red", "green", "blue")):
        node("Gather", ["image", constant(channel + "_index", [index], np.int64)], channel, axis=1)
    constant("minimum_red", .6); constant("dominance", 1.5)
    node("Greater", ["red", "minimum_red"], "bright_red")
    node("Mul", ["green", "dominance"], "green_limit")
    node("Mul", ["blue", "dominance"], "blue_limit")
    node("Greater", ["red", "green_limit"], "red_over_green")
    node("Greater", ["red", "blue_limit"], "red_over_blue")
    node("And", ["bright_red", "red_over_green"], "first_mask")
    node("And", ["first_mask", "red_over_blue"], "mask")
    xx, yy = np.meshgrid(np.arange(SIZE), np.arange(SIZE))
    constant("xs", xx.reshape(1, 1, SIZE, SIZE)); constant("ys", yy.reshape(1, 1, SIZE, SIZE))
    constant("zero", 0); constant("one", 1); constant("outside", SIZE); constant("gain", gain)
    for axis in ("x", "y"):
        node("Where", ["mask", axis + "s", "outside"], axis + "_min_grid")
        node("Where", ["mask", axis + "s", "zero"], axis + "_max_grid")
        node("ReduceMin", [axis + "_min_grid"], axis + "1", axes=[0, 1, 2, 3], keepdims=0)
        node("ReduceMax", [axis + "_max_grid"], axis + "_max", axes=[0, 1, 2, 3], keepdims=0)
        node("Add", [axis + "_max", "one"], axis + "2")
    node("Cast", ["mask"], "float_mask", to=TensorProto.FLOAT)
    node("Mul", ["float_mask", "red"], "masked_red")
    node("ReduceMax", ["masked_red"], "red_score", axes=[0, 1, 2, 3], keepdims=0)
    node("Mul", ["red_score", "gain"], "score")
    constant("vector_shape", [1], np.int64)
    for scalar in ("x1", "y1", "x2", "y2", "score", "zero"):
        node("Reshape", [scalar, "vector_shape"], scalar + "_vector")
    node("Concat", [name + "_vector" for name in ("x1", "y1", "x2", "y2", "score", "zero")], "detection", axis=0)
    constant("output_shape", [1, 1, 6], np.int64)
    node("Reshape", ["detection", "output_shape"], "detections")
    graph = helper.make_graph(nodes, "original-red-region-fixture", [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, SIZE, SIZE])],
                              [helper.make_tensor_value_info("detections", TensorProto.FLOAT, [1, 1, 6])], initializers)
    model = helper.make_model(graph, producer_name="AXIOM original contract fixture", opset_imports=[helper.make_opsetid("", 13)], ir_version=10)
    model.doc_string = "Authored pixel rule; no learned perception, object semantics or accuracy claim."
    onnx.checker.check_model(model)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(model.SerializeToString())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def image_bytes(index: int, *, width=320, height=160):
    image = Image.new("RGB", (width, height), (25, 35, 45))
    draw = ImageDraw.Draw(image)
    draw.text((10, 8), "AUTHORED PIXEL-RULE FIXTURE", fill=(220, 220, 220))
    if index % 3 != 2:
        left = 24 + (index % 5) * 26
        draw.rectangle((left, 48, left + 72, 119), fill=(242 if index % 3 == 0 else 184, 35, 25))
    stream = io.BytesIO(); image.save(stream, format="PNG")
    return stream.getvalue()


def make_recording(destination: Path, frames=6):
    if not 1 <= frames <= 64:
        raise ValueError("Fixture frames must be 1..64")
    schema = {"type": "object", "properties": {"timestamp": {"type": "object"}, "frame_id": {"type": "string"},
               "format": {"type": "string"}, "data": {"type": "string", "contentEncoding": "base64"}}}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        writer = Writer(stream, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="json", library="AXIOM original perception contract fixture")
        sid = writer.register_schema("foxglove.CompressedImage", "jsonschema", json.dumps(schema).encode())
        channel = writer.register_channel("/camera/authored_rule/image", "json", sid)
        writer.add_metadata("axiom.source", {"source_kind": "synthetic", "scope": "Authored red-pixel fixture, not sensor simulation or trained perception"})
        for index in range(frames):
            timestamp = BASE_NS + index * 100_000_000
            payload = {"timestamp": {"sec": timestamp // 1_000_000_000, "nsec": timestamp % 1_000_000_000},
                       "frame_id": "fixture_camera_optical", "format": "png", "data": base64.b64encode(image_bytes(index)).decode()}
            writer.add_message(channel, timestamp + 3_000_000, json.dumps(payload, separators=(",", ":")).encode(), timestamp + 1_000_000, index)
        writer.finish()
    return channel


def definition(artifact_id: str, *, name="Authored red-pixel ONNX fixture"):
    return {"name": name, "artifact_id": artifact_id, "profile": "boxes_xyxy_v1", "input_width": SIZE, "input_height": SIZE,
            "labels": LABELS, "source": "Original examples/generate_perception_fixture.py", "source_revision": "1",
            "weights_license": "Original project fixture; distribution license not selected", "code_license": "Project code",
            "training_data_note": "Not trained; threshold/Gather/Reduce/Where operations over the actual RGB input",
            "note": "Contract demonstration only. Finds a bounding union of red pixels, not semantic objects or sensor accuracy."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summary = {"model_a_sha256": make_model(args.output / "red-rule.onnx"),
               "model_b_sha256": make_model(args.output / "red-rule-lower-score.onnx", .65),
               "channel_id": make_recording(args.output / "images.mcap"), "ordinals": list(range(6)),
               "fidelity": "Original authored pixel-rule ONNX and synthetic images; not a trained detector"}
    (args.output / "definition.json").write_text(json.dumps(definition("REPLACE_WITH_ARTIFACT_ID"), indent=2) + "\n")
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
