"""Recorded images -> actual ONNX worker -> immutable predictions and comparison.

The generated input-dependent pixel rule is an original contract fixture, not
trained semantic perception. An optional pinned OpenCV Zoo model demonstrates
real trained inference on the same authored images, not detector accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import uuid

from axiom_sdk import AxiomClient
from generate_perception_fixture import definition, make_model, make_recording
from fetch_opencv_yolox import definition as opencv_definition, MODEL_SHA256, MODEL_SIZE


def run(api, output, opencv_model=None):
    output.mkdir(parents=True, exist_ok=False)
    make_model(output / 'rule-a.onnx')
    make_model(output / 'rule-b.onnx', .65)
    channel = make_recording(output / 'authored-images.mcap')
    recording = api.import_recording(output / 'authored-images.mcap', name='Authored pixel-rule inference inputs',
                                     source_kind='synthetic', source='Original RGB pixel fixture; not a real camera or sensor simulation')
    supported = api.sensor_channels(recording['id'])['channels']
    selected = next(item for item in supported if item['channel_id'] == channel)
    # Decoder id/version is taken from advertised support, not inferred by topic name.
    decoder = selected['decoders'][0]
    a_artifact = api.upload_model_artifact(output / 'rule-a.onnx')
    a_definition = definition(a_artifact['id'])
    model = api.create_model(**a_definition)
    initial_manifest = api.export_model(model['id'], output / 'model-r1.json', revision=1).read_bytes()
    request = dict(label='Authored rule r1 · actual ONNX graph', model_id=model['id'], model_revision=1,
                   recording_id=recording['id'], channel_id=channel, ordinals=list(range(6)),
                   decoder_id=decoder['id'], decoder_version=decoder['version'], score_threshold=.6)
    key = str(uuid.uuid4())
    submitted = api.submit_perception_job(**request, idempotency_key=key)
    assert api.submit_perception_job(**request, idempotency_key=key)['id'] == submitted['id']
    b_artifact = api.upload_model_artifact(output / 'rule-b.onnx')
    revised = api.revise_model(model['id'], expected_revision=1,
                              **{**a_definition, 'artifact_id': b_artifact['id'], 'note': 'Lower score gain .65; authored rule, not a measured regression.'})
    assert revised['revision'] == 2
    first = api.wait_perception_job(submitted['id'], timeout=120)
    second = api.submit_perception_job(**{**request, 'label': 'Authored rule r2 · same exact image inputs', 'model_revision': 2}, idempotency_key=str(uuid.uuid4()))
    second = api.wait_perception_job(second['id'], timeout=120)
    for job in (first, second):
        assert job['status'] == 'completed', job
        assert job['counts']['completed'] == 6 and job['counts']['failed'] == 0
        api.export_perception_job(job['id'], output / f"{job['id']}.json")
    assert [len(frame['detections']) for frame in first['frames']] == [1, 1, 0, 1, 1, 0]
    assert [len(frame['detections']) for frame in second['frames']] == [1, 0, 0, 1, 0, 0]
    for index in range(6):
        left, right = first['frames'][index], second['frames'][index]
        assert left['image']['sha256'] == right['image']['sha256']
        saved = api.export_perception_image(first['id'], index, output / f'input-{index}.png')
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == left['image']['sha256']
    assert first['model_snapshot']['revision'] == 1 and first['model_snapshot']['artifact']['id'] == a_artifact['id']
    assert api.export_model(model['id'], output / 'model-r1-after.json', revision=1).read_bytes() == initial_manifest
    comparison = api.capture_perception_comparison(first['id'], second['id'], name='Authored score-gain comparison',
        note='Same PNG bytes, different authored ONNX weights; prediction difference only, no accuracy verdict.', idempotency_key=str(uuid.uuid4()))
    comparison = api.get_perception_comparison(comparison['id'])
    assert comparison['counts']['same_input'] == 6 and comparison['counts']['not_evaluated'] == 0
    api.export_perception_comparison(comparison['id'], output / 'comparison.json')
    summary = dict(recording_id=recording['id'], channel_id=channel, model_id=model['id'], model_revisions=[1, 2],
                   jobs=[first['id'], second['id']], comparison_id=comparison['id'], comparison_hash=comparison['manifest_hash'],
                   same_input_rows=6, detection_counts=[[len(f['detections']) for f in j['frames']] for j in (first, second)],
                   original_model_manifest_unchanged=True, exact_png_downloads_verified=True,
                   fidelity='Actual CPU ONNX execution of an authored input-dependent pixel rule on synthetic images; no accuracy or sensor-validation claim')
    if opencv_model:
        assert opencv_model.stat().st_size == MODEL_SIZE and hashlib.sha256(opencv_model.read_bytes()).hexdigest() == MODEL_SHA256
        artifact = api.upload_model_artifact(opencv_model)
        trained = api.create_model(**opencv_definition(artifact['id']))
        task = api.submit_perception_job(**{**request, 'label': 'OpenCV Zoo trained YOLOX-S · authored image execution',
             'model_id': trained['id'], 'model_revision': 1, 'ordinals': [0, 1, 2], 'score_threshold': .35}, idempotency_key=str(uuid.uuid4()))
        task = api.wait_perception_job(task['id'], timeout=120)
        assert task['status'] == 'completed', task
        api.export_perception_job(task['id'], output / 'opencv-trained-job.json')
        summary['trained_model_execution'] = dict(model_id=trained['id'], job_id=task['id'], model_sha256=MODEL_SHA256,
            image_source='Authored fixture, not road sensor capture', detections=[len(f['detections']) for f in task['frames']],
            inference_ms=[f['runtime']['inference_ms'] for f in task['frames']],
            timing_scope='Native model execution per frame; excludes initialization, preprocessing, postprocessing, queue and HTTP')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--opencv-model', type=Path)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.getenv('ADE2E_API_TOKEN')) as api:
        print(json.dumps(run(api, args.output, args.opencv_model), indent=2))
