"""Create original RGB drawings for the Scene discovery workflow.

These are authored images, not photographs, learned-model results, measured
illumination or independent driving scenes. All writes use the selected API.
"""
import argparse
import base64
import io
import json
import os
from pathlib import Path
from uuid import uuid4

from mcap.writer import CompressionType, Writer
from PIL import Image, ImageDraw
from axiom_sdk import AxiomClient

BASE = 1700000000000000123


def image_bytes(kind):
    picture = Image.new('RGB', (160, 96), (150, 195, 235))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 45, 159, 95), fill=(65, 72, 80))
    draw.line((45, 95, 75, 45), fill=(220, 215, 185), width=3)
    draw.line((115, 95, 85, 45), fill=(220, 215, 185), width=3)
    shift = 2 if kind == 'near' else 0
    draw.rectangle((69+shift, 39, 89+shift, 63), fill=(210, 45, 32))
    if kind == 'dark':
        draw.rectangle((0, 0, 159, 95), fill=(8, 12, 24))
        draw.rectangle((30, 40, 95, 80), fill=(35, 20, 25))
    if kind == 'green':
        draw.rectangle((0, 0, 159, 95), fill=(20, 155, 75))
    if kind == 'bright':
        draw.rectangle((0, 0, 159, 95), fill=(250, 252, 254))
    stream = io.BytesIO()
    picture.save(stream, format='PNG')
    return stream.getvalue()


def write_recording(path, roles, start):
    schema = {'type': 'object', 'properties': {'timestamp': {'type': 'object'},
        'frame_id': {'type': 'string'}, 'format': {'type': 'string'},
        'data': {'type': 'string', 'contentEncoding': 'base64'}}}
    with path.open('xb') as stream:
        writer = Writer(stream, compression=CompressionType.ZSTD, enable_crcs=True)
        writer.start(profile='json', library='axiom-original-scene-fixture/1')
        sid = writer.register_schema('foxglove.CompressedImage', 'jsonschema', json.dumps(schema).encode())
        channel = writer.register_channel('/camera/appearance', 'json', sid)
        writer.add_metadata('axiom.fixture', {'instance': uuid4().hex, 'kind': 'original drawings'})
        for ordinal, kind in enumerate(roles):
            stamp = start + ordinal*100000000
            value = {'timestamp': {'sec': stamp//1000000000, 'nsec': stamp%1000000000},
                'frame_id': 'appearance_camera', 'format': 'png',
                'data': base64.b64encode(image_bytes(kind)).decode()}
            writer.add_message(channel, stamp, json.dumps(value).encode(), stamp, ordinal)
        writer.finish()
    return channel


def prepare(api, output):
    output.mkdir(parents=True, exist_ok=False)
    sources, identities, recordings = [], {}, []
    for group, roles in enumerate([['day', 'near', 'dark', 'uncaptured'], ['duplicate', 'green', 'bright']]):
        path = output/f'authored-{group}.mcap'
        start = BASE + group*10000000000
        channel = write_recording(path, roles, start)
        recording = api.import_recording(path, name=f'Appearance fixture {group}', source_kind='synthetic',
            source='Original RGB drawings; no sensor or environmental validation')
        recordings.append(recording['id'])
        members = []
        for ordinal, kind in enumerate(roles):
            identities[kind] = {'recording_id': recording['id'], 'channel_id': channel, 'ordinal': ordinal}
            if kind == 'uncaptured':
                stamp = str(start+ordinal*100000000)
                case = api.create_case(title='Uncaptured appearance input', note='Retain this source in the full denominator',
                    source={'kind': 'recording', 'recording_id': recording['id'], 'channel_id': channel,
                        'start_time_ns': stamp, 'end_time_ns': stamp}, idempotency_key=uuid4().hex)
                sources.append({'kind': 'case', 'case_id': case['id'], 'revision': case['revision'], 'reason': 'Keep uncaptured input'})
            else:
                asset = api.capture_dataset_image(recording['id'], channel, ordinal, decoder_id='foxglove-json', decoder_version='1')
                members.append({'image_id': asset['id'], 'split': 'unspecified'})
        dataset = api.create_dataset(name=f'Appearance images {group}', labels=['authored-region'], images=members,
            note='Unreviewed source images without object or weather labels')
        sources.append({'kind': 'dataset', 'dataset_id': dataset['id'], 'revision': 1, 'reason': 'All captured appearances'})
    definition = {'name': 'Scene discovery authored collection', 'purpose': 'Numeric features and declared scene reviews', 'sources': sources}
    preview = api.preview_collection(**definition)
    collection = api.create_collection(**definition, expected_preview_hash=preview['preview_hash'], idempotency_key=uuid4().hex)
    keys = {kind: next(r['key'] for r in collection['selection']['rows'] if all(r['source'][k] == v for k, v in identity.items()))
        for kind, identity in identities.items()}
    summary = {'collection_id': collection['id'], 'collection_hash': collection['manifest_hash'],
        'recording_ids': recordings, 'key_by_role': keys, 'source_messages': 7, 'captured_images': 6}
    (output/'summary.json').write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.environ.get('ADE2E_API_TOKEN')) as api:
        print(json.dumps(prepare(api, args.output), indent=2))
