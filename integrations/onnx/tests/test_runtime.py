from __future__ import annotations

import hashlib
import io
from pathlib import Path
import subprocess
import sys

import numpy as np
import onnx
from onnx import helper, TensorProto
from PIL import Image
import pytest

from ade2e.inference_runtime import InferenceCancelled, InferenceExecutionError, InferenceInputError
from axiom_onnx.engine import postprocess, preprocess, validate_model
from axiom_onnx.runtime import ChildSession, OnnxCpuRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'examples'))
from generate_perception_fixture import definition, image_bytes, make_model


@pytest.fixture
def model(tmp_path):
    path = tmp_path / 'input-dependent.onnx'
    sha = make_model(path)
    return path, definition('fixture'), sha


def image(color, size=(320, 160), format='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format=format)
    return stream.getvalue()


def configuration(profile='boxes_xyxy_v1', labels=None):
    return dict(profile=profile, input_width=64, input_height=64, labels=labels or ['red', 'blue'])


def transform():
    return preprocess(image((0, 0, 0)), 320, 160, configuration())[1]


def infer(session, png, **overrides):
    options = dict(width=320, height=160, score_threshold=.35, iou_threshold=.45,
                   max_detections=100, should_cancel=lambda: False)
    options.update(overrides)
    return session.infer(png, **options)


def test_yolox_uses_rgb_raw_and_top_left_114_padding():
    tensor, mapping = preprocess(image((230, 60, 15)), 320, 160, configuration('opencv_yolox_rgb_v1'))
    np.testing.assert_array_equal(tensor[0, :, 10, 10], [230, 60, 15])
    np.testing.assert_array_equal(tensor[0, :, 32, 0], [114, 114, 114])
    assert tensor.shape == (1, 3, 64, 64) and tensor.dtype == np.float32 and tensor.flags.c_contiguous
    assert mapping['pad_bottom'] == 32 and mapping['scale_x'] == mapping['scale_y'] == .2
    assert mapping['value_scale'] == 1 and mapping['color_order'] == 'RGB'


def test_direct_rgb_normalization_and_anisotropic_inverse():
    tensor, mapping = preprocess(image((255, 128, 0)), 320, 160, configuration())
    np.testing.assert_allclose(tensor[0, :, 1, 1], [1, 128 / 255, 0])
    boxes, _ = postprocess(np.array([[[4, 8, 20, 40, .9, 0]]], np.float32), configuration(), mapping, .5, .5, 100)
    assert boxes[0].bbox_xyxy == (20, 20, 100, 100)


def test_rounded_letterbox_inverse_uses_actual_resized_dimensions():
    config = configuration('opencv_yolox_rgb_v1')
    _, mapping = preprocess(image((255, 0, 0), (333, 101)), 333, 101, config)
    assert mapping['resized_height'] == 19
    assert mapping['scale_y'] == 19 / 101 and mapping['scale_x'] == 64 / 333
    boxes, _ = postprocess(np.array([[[0, 0, 64, 19, .9, 0]]], np.float32), configuration(), mapping, .5, .5, 100)
    assert boxes[0].bbox_xyxy == (0, 0, 333, 101)


@pytest.mark.parametrize('png,width,height', [(b'bad', 320, 160), (image((1, 2, 3)), 321, 160),
    (image((1, 2, 3), format='JPEG'), 320, 160), (b'x', 2001, 1000), (b'x', True, 2)])
def test_invalid_image_is_error_not_empty(png, width, height):
    with pytest.raises(InferenceInputError):
        preprocess(png, width, height, configuration())


def test_nms_is_class_aware_stable_and_counts_clipping_empty_and_truncation():
    output = np.array([[[0, 0, 20, 20, .9, 0], [1, 1, 21, 21, .9, 0],
                        [0, 0, 20, 20, .9, 1], [-5, 30, 10, 50, .8, 0],
                        [70, 70, 80, 80, .7, 0], [40, 40, 50, 50, .6, 0], [1, 1, 5, 5, .1, 0]]], np.float32)
    boxes, counts = postprocess(output, configuration(), transform(), .2, .4, 3)
    assert [box.id for box in boxes] == ['d0', 'd2', 'd3']
    assert boxes[2].bbox_xyxy == (0, 75, 50, 125)
    assert counts == dict(source_candidates=7, threshold_candidates=6, suppressed_candidates=1,
                          clipped_detections=1, discarded_empty_boxes=1, max_detections_truncated=1)


def test_yolox_grid_decode_and_objectness_class_product():
    config = configuration('opencv_yolox_rgb_v1')
    output = np.zeros((1, 84, 7), np.float32)
    output[0, 9] = [0, 0, 0, 0, .8, .2, .9]
    _, mapping = preprocess(image((0, 0, 0)), 320, 160, config)
    boxes, counts = postprocess(output, config, mapping, .5, .45, 100)
    assert len(boxes) == 1 and boxes[0].label == 'blue' and boxes[0].score == pytest.approx(.72)
    assert boxes[0].bbox_xyxy == (20, 20, 60, 60)
    assert counts['source_candidates'] == 84


@pytest.mark.parametrize('field,value', [(4, 1.1), (4, -.1), (5, .5), (5, 2), (5, -1), (0, np.nan), (2, np.inf)])
def test_invalid_output_values_rejected(field, value):
    values = np.array([[[0, 0, 10, 10, .5, 0]]], np.float32)
    values[0, 0, field] = value
    with pytest.raises(InferenceInputError):
        postprocess(values, configuration(), transform(), .2, .4, 100)


@pytest.mark.parametrize('output', [np.zeros((1, 1, 6), np.float64), np.zeros((1, 6), np.float32),
                                   np.zeros((1, 1, 5), np.float32)])
def test_invalid_output_signature(output):
    with pytest.raises(InferenceInputError):
        postprocess(output, configuration(), transform(), .2, .4, 100)


def test_excessive_threshold_candidates_fail_closed():
    values = np.tile(np.array([0, 0, 10, 10, .9, 0], np.float32), (1, 4097, 1))
    with pytest.raises(InferenceInputError, match='4,096'):
        postprocess(values, configuration(), transform(), .2, .4, 100)


@pytest.mark.parametrize('option,value', [('score', np.nan), ('score', True), ('iou', -1), ('limit', 301), ('limit', True)])
def test_invalid_configuration(option, value):
    opts = dict(score=.2, iou=.4, limit=100); opts[option] = value
    with pytest.raises(InferenceInputError):
        postprocess(np.empty((1, 0, 6), np.float32), configuration(), transform(), opts['score'], opts['iou'], opts['limit'])


@pytest.mark.parametrize('mutation,match', [('external', 'external'), ('domain', 'Custom'), ('random', 'random'),
    ('nested', 'Nested'), ('shape', 'dimensions'), ('profile', 'profile'), ('labels', 'label')])
def test_unsupported_models_refused_before_native_load(model, mutation, match):
    path, config, _ = model
    graph = onnx.load_model_from_string(path.read_bytes())
    if mutation == 'external':
        graph.graph.initializer[0].data_location = TensorProto.EXTERNAL
    elif mutation == 'domain':
        graph.graph.node[0].domain = 'vendor.custom'
    elif mutation == 'random':
        graph.graph.node[0].op_type = 'RandomUniform'
    elif mutation == 'nested':
        subgraph = helper.make_graph([], 'unsupported', [], [])
        graph.graph.node[0].attribute.append(helper.make_attribute('body', subgraph))
    elif mutation == 'shape':
        graph.graph.input[0].type.tensor_type.shape.dim[3].dim_value = 128
    elif mutation == 'profile':
        config['profile'] = 'unknown'
    else:
        config['labels'] = ['duplicate', 'duplicate']
    with pytest.raises(InferenceInputError, match=match):
        validate_model(graph.SerializeToString(), config)


def test_actual_onnx_child_changes_predictions_with_pixels_and_preserves_hashes(model):
    path, config, sha = model
    with OnnxCpuRuntime().open(path, config, sha, 42, lambda: False) as session:
        a, b, empty = [infer(session, image_bytes(index)) for index in range(3)]
        repeated = infer(session, image_bytes(0))
        assert a.detections[0].bbox_xyxy == (25, 47.5, 95, 120)
        assert b.detections[0].bbox_xyxy == (50, 47.5, 125, 120)
        assert a.detections[0].score > b.detections[0].score
        assert empty.detections == [] and empty.evidence.threshold_candidates == 0
        assert repeated.detections == a.detections
        assert a.evidence.image_sha256 == hashlib.sha256(image_bytes(0)).hexdigest()
        tensor, _ = preprocess(image_bytes(0), 320, 160, config)
        assert a.evidence.tensor_sha256 == hashlib.sha256(tensor.tobytes()).hexdigest()
        assert a.evidence.model_sha256 == sha and a.evidence.provider == 'CPUExecutionProvider'
        assert a.evidence.library_versions['opencv']
        process = session.process
    assert process.poll() is not None and session.closed


def test_weight_revision_changes_output_from_same_png(model, tmp_path):
    path, config, sha = model
    other = tmp_path / 'lower.onnx'; other_sha = make_model(other, .65)
    with OnnxCpuRuntime().open(path, config, sha, 42, lambda: False) as first:
        a = infer(first, image_bytes(0))
    with OnnxCpuRuntime().open(other, config, other_sha, 42, lambda: False) as second:
        b = infer(second, image_bytes(0))
    assert a.evidence.image_sha256 == b.evidence.image_sha256
    assert a.evidence.model_sha256 != b.evidence.model_sha256
    assert b.detections[0].score == pytest.approx(a.detections[0].score * .65)


def test_bad_frame_does_not_become_empty_or_break_next_frame(model):
    with OnnxCpuRuntime().open(*model, 42, lambda: False) as session:
        with pytest.raises(InferenceInputError):
            infer(session, b'corrupt PNG')
        assert len(infer(session, image_bytes(0)).detections) == 1


def test_pinned_hash_mismatch_cannot_initialize(model):
    path, config, _ = model
    session = ChildSession(path, config, '0' * 64, 42, lambda: False)
    with pytest.raises(InferenceInputError, match='SHA-256'):
        with session:
            pytest.fail('Model must not initialize')
    assert session.closed and session.process.poll() is not None


def test_cancel_acknowledges_only_after_child_exit(model):
    with OnnxCpuRuntime().open(*model, 42, lambda: False) as session:
        with pytest.raises(InferenceCancelled):
            infer(session, image_bytes(0), should_cancel=lambda: True)
        assert session.process.poll() is not None and session.closed


def test_operation_deadline_stops_native_child(model, monkeypatch):
    import ade2e.process_transport as runtime
    original = subprocess.Popen
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda args, **kw: original([sys.executable, '-c', 'import time; time.sleep(5)'], **kw))
    session = ChildSession(*model, 42, lambda: False, timeout=.1)
    with pytest.raises(InferenceExecutionError, match='deadline'):
        with session:
            pytest.fail('Unresponsive child initialized')
    assert session.process.poll() is not None and session.closed


def test_lost_lease_callback_terminates_child(model):
    class LostLease(RuntimeError):
        pass
    with OnnxCpuRuntime().open(*model, 42, lambda: False) as session:
        def lost():
            raise LostLease('ownership replaced')
        with pytest.raises(LostLease):
            session._receive(lost)
        assert session.closed and session.process.poll() is not None


def test_runtime_metadata_does_not_import_native_libraries():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from ade2e.inference_runtime import RuntimeRegistry; assert RuntimeRegistry().get('onnx-cpu').metadata().status == 'available'; assert not {'onnx','onnxruntime','cv2','numpy'}.intersection(sys.modules)"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('reason', ['deadline', 'cancel', 'lease'])
def test_blocked_large_input_transfer_remains_cancellable_and_bounded(model, monkeypatch, reason):
    import os
    import time
    import ade2e.process_transport as runtime
    original = subprocess.Popen
    # It acknowledges initialization but never consumes the large frame input.
    command = "import sys,time; sys.stdin.readline(); print('{\"type\":\"ready\"}',flush=True); time.sleep(5)"
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda args, **kw: original([sys.executable, '-c', command], **kw))
    session = ChildSession(*model, 42, lambda: False, timeout=2)
    class LostLease(RuntimeError):
        pass
    with session:
        started = time.monotonic()
        session.timeout = .15 if reason == 'deadline' else 2
        def stop():
            if time.monotonic() - started < .1:
                return False
            if reason == 'lease':
                raise LostLease('Worker ownership lost during transfer')
            return reason == 'cancel'
        expected = {'deadline': InferenceExecutionError, 'cancel': InferenceCancelled, 'lease': LostLease}[reason]
        with pytest.raises(expected):
            infer(session, os.urandom(512 * 1024), should_cancel=stop)
        assert time.monotonic() - started < 2
        assert session.closed and session.process.poll() is not None
        assert not session.writer.is_alive() and not session.reader.is_alive()
