"""Fetch one pinned OpenCV Zoo model and its license; no model is bundled in Git.

The manifest separates model weights, implementation and training-image rights.
Download verifies the official LFS SHA and expected size before publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import httpx

REVISION = '47534e27c9851bb1128ccc0102f1145e27f23f98'
MODEL_SHA256 = 'c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063'
MODEL_SIZE = 35_858_002
ROOT = f'https://github.com/opencv/opencv_zoo/blob/{REVISION}/models/object_detection_yolox'
MODEL_URL = f'https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/models/object_detection_yolox/object_detection_yolox_2022nov.onnx'
LICENSE_URL = f'https://raw.githubusercontent.com/opencv/opencv_zoo/{REVISION}/models/object_detection_yolox/LICENSE'
LICENSE_SHA256 = '0ec3668d3274bcf29e8a29e9576d5a2cd96fc78d3c5bec4387355a796e5d9088'
LABELS = ['person','bicycle','car','motorcycle','airplane','bus','train','truck','boat','traffic light','fire hydrant',
          'stop sign','parking meter','bench','bird','cat','dog','horse','sheep','cow','elephant','bear','zebra','giraffe',
          'backpack','umbrella','handbag','tie','suitcase','frisbee','skis','snowboard','sports ball','kite','baseball bat',
          'baseball glove','skateboard','surfboard','tennis racket','bottle','wine glass','cup','fork','knife','spoon','bowl',
          'banana','apple','sandwich','orange','broccoli','carrot','hot dog','pizza','donut','cake','chair','couch',
          'potted plant','bed','dining table','toilet','tv','laptop','mouse','remote','keyboard','cell phone','microwave',
          'oven','toaster','sink','refrigerator','book','clock','vase','scissors','teddy bear','hair drier','toothbrush']


def definition(artifact_id):
    return dict(name='OpenCV Zoo YOLOX-S FP32', description='Pinned trained detector; predictions require independent labeled evaluation.',
                artifact_id=artifact_id, profile='opencv_yolox_rgb_v1', input_width=640, input_height=640, labels=LABELS,
                source=ROOT + '/README.md', source_revision=REVISION, weights_license='Apache-2.0 (OpenCV model-directory declaration)',
                weights_license_url=ROOT + '/LICENSE', code_license='OpenCV Zoo Apache-2.0; ONNX Runtime MIT',
                training_data_note='COCO model. Individual training-image rights are separate from model/code licensing; no dataset bundled.',
                note='RGB raw 0..255 NCHW; top-left linear resize and 114 padding; raw YOLOX grid; class-aware continuous-IoU NMS.')


def fetch(url, destination, expected_sha, expected_size):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or destination.stat().st_size != expected_size or hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha:
            raise ValueError(f'Existing destination differs from pinned artifact: {destination}')
        return destination
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    try:
        digest, size = hashlib.sha256(), 0
        with os.fdopen(fd, 'wb') as stream, httpx.stream('GET', url, timeout=60, follow_redirects=False) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > expected_size:
                    raise ValueError('Download exceeds pinned artifact size')
                digest.update(chunk)
                if stream.write(chunk) != len(chunk):
                    raise OSError('Incomplete model download file write')
            if size != expected_size or digest.hexdigest() != expected_sha:
                raise ValueError('Download differs from pinned size/SHA-256')
            stream.flush(); os.fsync(stream.fileno())
        saved_digest, saved_size = hashlib.sha256(), 0
        with Path(temporary).open('rb') as saved:
            for chunk in iter(lambda: saved.read(1024 * 1024), b''):
                saved_size += len(chunk); saved_digest.update(chunk)
        if saved_size != expected_size or saved_digest.hexdigest() != expected_sha:
            raise ValueError('Saved model file differs from pinned size/SHA-256')
        # No overwrite if another process published a destination while fetching.
        os.link(temporary, destination)
        return destination
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    model = fetch(MODEL_URL, args.output / 'opencv-yolox-s.onnx', MODEL_SHA256, MODEL_SIZE)
    fetch(LICENSE_URL, args.output / 'OpenCV-YOLOX-LICENSE', LICENSE_SHA256, 11371)
    manifest = {'model_path': str(model.resolve()), 'sha256': MODEL_SHA256, 'size_bytes': MODEL_SIZE,
                'download_url': MODEL_URL, 'license_sha256': LICENSE_SHA256, 'definition': definition('REPLACE_WITH_ARTIFACT_ID')}
    (args.output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))
