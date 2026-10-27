"""Original RGB labels -> immutable dataset -> ONNX predictions -> actual COCO bbox.

Ground truth below comes from the original image construction, independently of
the model outputs. This is a reproducible pipeline exercise on authored pixels;
its numbers are not road accuracy or learned object-detector performance.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

from axiom_sdk import AxiomClient
from generate_perception_fixture import LABELS, definition, make_model, make_recording


def original_annotations(index):
    # Pillow rectangle endpoints are inclusive; our continuous xyxy right/bottom
    # lie just beyond the last painted pixel. No inference output is read here.
    if index % 3 == 2:
        return []
    left = 24 + (index % 5) * 26
    return [dict(id=f'red-{index}', label=LABELS[0], bbox_xyxy=[left, 48, left + 73, 120], iscrowd=False)]


def run(api, output):
    output.mkdir(parents=True, exist_ok=False)
    channel = make_recording(output / 'original-images.mcap')
    recording = api.import_recording(output / 'original-images.mcap', name='Dataset evaluation · original authored images',
        source_kind='synthetic', source='Original generate_perception_fixture.py pixels; not sensor simulation')
    supported = api.sensor_channels(recording['id'])['channels']
    decoder = next(item for item in supported if item['channel_id'] == channel)['decoders'][0]
    images = [api.capture_dataset_image(recording['id'], channel, index,
        decoder_id=decoder['id'], decoder_version=decoder['version']) for index in range(6)]
    members = [dict(image_id=image['id'], split='test', review_status='reviewed' if index < 5 else 'unreviewed',
        reviewer='Original fixture declaration' if index < 5 else '',
        review_note='Checked against the original rectangle construction, independent of inference.' if index < 5 else '',
        annotation_source='synthetic', annotations=original_annotations(index)) for index, image in enumerate(images)]
    body = dict(name='Authored red-region labels · six exact source images', labels=LABELS, images=members,
        description='Four rectangles and two explicitly reviewable empty images from original construction.',
        source='examples/generate_perception_fixture.py:image_bytes', source_revision='1',
        license='Original project fixture; distribution license not selected',
        note='Synthetic label declarations; no independent human review or real-world accuracy claim.')
    dataset = api.create_dataset(**body)
    first_manifest = api.export_dataset(dataset['id'], output / 'dataset-r1.json', revision=1).read_bytes()
    assert dataset['stats']['reviewed'] == 5 and dataset['stats']['negative_images'] == 1
    for index, image in enumerate(images):
        saved = api.export_dataset_image(image['id'], output / f'original-{index}.png')
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == image['image']['sha256']

    # Dataset capture and labeling are complete before the model is registered.
    jobs = []
    for label, gain in [('A', 1.0), ('B', .65)]:
        make_model(output / f'rule-{label}.onnx', gain)
        artifact = api.upload_model_artifact(output / f'rule-{label}.onnx')
        model = api.create_model(**definition(artifact['id'], name=f'Original pixel rule {label} · gain {gain}'))
        task = api.submit_perception_job(label=f'Authored rule {label} for labeled dataset', model_id=model['id'],
            model_revision=1, recording_id=recording['id'], channel_id=channel, ordinals=list(range(6)),
            decoder_id=decoder['id'], decoder_version=decoder['version'], score_threshold=.6,
            idempotency_key=str(uuid.uuid4()))
        task = api.wait_perception_job(task['id'], timeout=180)
        assert task['status'] == 'completed', task
        api.export_perception_job(task['id'], output / f'predictions-{label}.json')
        jobs.append(task)

    request = dict(note='Actual COCO bbox computation on stored authored-rule predictions; source score floor .6.',
        dataset_id=dataset['id'], dataset_revision=1, split='test', inference_job_ids=[jobs[0]['id']],
        class_mapping=[dict(model_label=LABELS[0], dataset_label=LABELS[0])], score_threshold=.6, diagnostic_iou=.5)

    def evaluate(name, **changes):
        key = str(uuid.uuid4())
        submitted = api.submit_detection_evaluation(name=name, **{**request, **changes}, idempotency_key=key)
        assert api.submit_detection_evaluation(name=name, **{**request, **changes}, idempotency_key=key)['id'] == submitted['id']
        result = api.wait_detection_evaluation(submitted['id'], timeout=180)
        assert result['status'] == 'completed', result
        api.export_detection_evaluation(result['id'], output / f"{result['id']}.json")
        return result

    incomplete = evaluate('Draft labels · full denominator retained')
    assert incomplete['report']['coverage']['total'] == 6
    assert incomplete['report']['coverage']['unreviewed'] == 1
    assert incomplete['report']['metrics'] is None
    assert all(item['ap'] is None and item['pr_curve'] is None for item in incomplete['report']['classes'])
    first_evidence = api.export_detection_evaluation(incomplete['id'], output / 'incomplete-before.json').read_bytes()
    revised = deepcopy(body)
    revised['images'][-1].update(review_status='reviewed', reviewer='Original fixture declaration',
        review_note='Original frame 5 paints no red region; explicitly declared a negative image.')
    second = api.revise_dataset(dataset['id'], expected_revision=1, **revised)
    assert second['revision'] == 2 and second['stats']['negative_images'] == 2
    assert api.export_dataset(dataset['id'], output / 'dataset-r1-after.json', revision=1).read_bytes() == first_manifest
    api.export_dataset(dataset['id'], output / 'dataset-r2.json', revision=2)
    a = evaluate('Reviewed labels · rule A', dataset_revision=2)
    b = evaluate('Reviewed labels · rule B', dataset_revision=2, inference_job_ids=[jobs[1]['id']])
    for result, expected in [(a, (4, 0, 0)), (b, (2, 0, 2))]:
        report = result['report']
        assert report['coverage']['complete'] and report['coverage']['evaluated'] == 6
        cls = report['classes'][0]
        assert (cls['tp'], cls['fp'], cls['fn']) == expected, cls
    assert a['report']['metrics']['ap'] > b['report']['metrics']['ap']
    assert api.export_detection_evaluation(incomplete['id'], output / 'incomplete-after.json').read_bytes() == first_evidence
    comparison = api.compare_detection_evaluations(a['id'], b['id'])
    assert comparison['status'] == 'comparable' and comparison['row_count'] == 6
    assert next(m for m in comparison['metrics'] if m['name'] == 'ap')['delta'] < 0
    api.export_evaluation_comparison(a['id'], b['id'], output / 'comparison.json')
    api.export_evaluation_comparison(a['id'], b['id'], output / 'comparison.html', format='html')
    summary = dict(recording_id=recording['id'], channel_id=channel, dataset_id=dataset['id'], dataset_revisions=[1, 2],
        inference_job_ids=[job['id'] for job in jobs], evaluation_ids=[item['id'] for item in (incomplete, a, b)],
        incomplete_coverage=incomplete['report']['coverage'], incomplete_metrics=incomplete['report']['metrics'],
        results=[dict(evaluation_id=item['id'], coverage=item['report']['coverage'], metrics=item['report']['metrics'],
                      counts={key:item['report']['classes'][0][key] for key in ['gt','tp','fp','fn']},
                      implementation=item['report']['implementation'], manifest_hash=item['manifest_hash']) for item in (a, b)],
        comparison_status=comparison['status'], comparison_rows=comparison['row_count'],
        comparison_exports_verified=True,
        original_dataset_and_evaluation_unchanged=True, exact_png_downloads_verified=True,
        fidelity='Actual CPU ONNX and pycocotools on original authored pixels/labels; pipeline arithmetic, not road accuracy.')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.getenv('ADE2E_API_TOKEN')) as api:
        print(json.dumps(run(api, args.output), indent=2))
