"""Exact camera image assets and immutable, explicitly reviewed dataset revisions."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import stat
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from fastapi import HTTPException, Query, Response
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select

from . import jobs
from .artifact_transactions import track_created_artifact
from .dataset_models import DatasetImageRow, DatasetMembershipRow, DatasetRevisionRow, DatasetRow
from .dataset_schemas import (DatasetCreate, DatasetDetail, DatasetImage, DatasetImageCreate,
                             DatasetImageInput, DatasetRevisionCreate, DatasetSnapshot, DatasetStats)
from .perception_schemas import PerceptionImage
from .recording_models import RecordingMessageRow
from .recordings import _get_recording, read_message_bytes
from .sensors import _channel, _resolve_decoder, sensor_frame
from .service import canonical_bytes, digest, uid, utcnow

IMAGE_LIMIT = 8 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
IMAGE_ID = re.compile(r'dataset_image_[0-9a-f]{32}\Z')


def _identity(asset):
    return digest({'input': asset.input.model_dump(mode='json'),
                   'image': asset.image.model_dump(mode='json', exclude={'download_url'}),
                   'recording_snapshot': asset.recording_snapshot})


def _manifest(value, limit=MANIFEST_LIMIT):
    content = canonical_bytes(value.model_dump(mode='json'))
    if len(content) > limit:
        raise HTTPException(413, 'Dataset manifest exceeds its byte limit')
    return dict(manifest=content, manifest_hash=hashlib.sha256(content).hexdigest(), manifest_size_bytes=len(content))


def _checked_manifest(row, cls, limit=MANIFEST_LIMIT):
    if not 0 < len(row.manifest) <= limit or len(row.manifest) != row.manifest_size_bytes or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
        raise ValueError('Manifest size or SHA-256 mismatch')
    value = cls.model_validate_json(row.manifest)
    if canonical_bytes(value.model_dump(mode='json')) != row.manifest:
        raise ValueError('Manifest is not canonical')
    return value


def get_dataset_image(session, image_id):
    row = session.get(DatasetImageRow, image_id)
    if row is None:
        raise HTTPException(404, 'Dataset image not found')
    try:
        value = _checked_manifest(row, DatasetImage, 256 * 1024)
        if (not IMAGE_ID.fullmatch(row.id) or row.storage_key != f'{row.id}.png' or value.id != row.id
                or value.created_at != row.created_at or value.input.recording_id != row.recording_id
                or value.input.ordinal != row.ordinal or _identity(value) != row.identity_hash
                or value.image.download_url != f'/api/v1/dataset-images/{row.id}/image'
                or value.recording_snapshot.get('id') != value.input.recording_id
                or value.recording_snapshot.get('sha256') != value.input.recording_sha256
                or value.recording_snapshot.get('channel', {}).get('id') != value.input.channel_id
                or min(value.image.width, value.image.height) < 1
                or value.image.width * value.image.height > 2_000_000
                or not 0 < value.image.size_bytes <= IMAGE_LIMIT):
            raise ValueError('Dataset image identity differs from stored relationships')
        return value
    except (ValueError, TypeError, KeyError, ValidationError) as error:
        raise HTTPException(409, 'Dataset image metadata failed its integrity check') from error


def _verify_png(png, image):
    if len(png) != image.size_bytes or not 0 < len(png) <= IMAGE_LIMIT or hashlib.sha256(png).hexdigest() != image.sha256:
        raise ValueError('Saved PNG bytes differ from captured image SHA/size')
    if not 1 <= image.width * image.height <= 2_000_000:
        raise ValueError('Dataset image exceeds the pixel bound')
    with Image.open(io.BytesIO(png)) as decoded:
        if decoded.format != 'PNG' or decoded.size != (image.width, image.height) or getattr(decoded, 'n_frames', 1) != 1:
            raise ValueError('PNG format/dimensions differ from the captured image')
        decoded.verify()


def dataset_image_bytes(db, asset):
    """Return verified bytes from one bounded non-symlink file handle."""
    try:
        if not IMAGE_ID.fullmatch(asset.id):
            raise ValueError('Invalid managed image ID')
        path = db.dataset_image_dir / f'{asset.id}.png'
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != asset.image.size_bytes or not 0 < info.st_size <= IMAGE_LIMIT:
                raise ValueError('Dataset image file type/size mismatch')
            png = stream.read(IMAGE_LIMIT + 1)
        _verify_png(png, asset.image)
        return png
    except (OSError, ValueError) as error:
        raise HTTPException(409, 'Dataset image bytes failed their integrity check') from error


def _source(session, decoders, body):
    recording = _get_recording(session, body.recording_id)
    channel, schema = _channel(session, body.recording_id, body.channel_id)
    decoder = _resolve_decoder(decoders, channel, schema, body.decoder_id, body.decoder_version)
    if decoder.kind_for(channel, schema) != 'camera':
        raise HTTPException(422, 'Dataset images require a supported camera decoder')
    entry = session.get(RecordingMessageRow, (body.recording_id, body.ordinal))
    if entry is None or entry.channel_id != body.channel_id:
        raise HTTPException(422, 'Ordinal does not belong to the selected camera channel')
    captured = DatasetImageInput(**body.model_dump(), recording_sha256=recording.sha256,
        message_sha256=entry.sha256, message_size_bytes=entry.payload_length,
        log_time_ns=str(int(entry.log_time_key)), publish_time_ns=str(int(entry.publish_time_key)))
    snapshot = {'id': recording.id, 'sha256': recording.sha256, 'name': recording.payload['name'],
        'source_kind': recording.payload.get('source_kind','unknown'), 'source': recording.payload.get('source',''),
        'channel': channel.model_dump(mode='json'), 'schema': schema.model_dump(mode='json') if schema else None}
    return recording, entry, captured, snapshot


def capture_dataset_image(db, decoders, body):
    with db.read_session() as session:
        recording, entry, captured, snapshot = _source(session, decoders, body)
        read_message_bytes(db, recording, entry)
        decoded = sensor_frame(session, db, decoders, body.recording_id, body.channel_id, body.ordinal, 1,
                               decoder_id=body.decoder_id, decoder_version=body.decoder_version)
        if decoded.status != 'decoded' or decoded.decoded is None or decoded.decoded.camera is None:
            raise HTTPException(422, 'Camera decode failed: ' + (decoded.reason or decoded.status))
        camera = decoded.decoded.camera
        png = base64.b64decode(camera.data_base64, validate=True)
        image_id, now = uid('dataset_image'), utcnow()
        image = PerceptionImage(sha256=camera.sha256, size_bytes=camera.size_bytes, width=camera.width, height=camera.height,
            download_url=f'/api/v1/dataset-images/{image_id}/image', capture_time_ns=decoded.decoded.capture_time_ns,
            frame_id=decoded.decoded.frame_id)
    try:
        _verify_png(png, image)
    except (ValueError, OSError) as error:
        raise HTTPException(422, 'Camera decoder returned invalid image evidence') from error
    value = DatasetImage(id=image_id, created_at=now, input=captured, image=image, recording_snapshot=snapshot)
    identity = _identity(value)
    fd, temporary_name = tempfile.mkstemp(prefix='image-', suffix='.part', dir=db.dataset_staging_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if stream.write(png) != len(png):
                raise OSError('Incomplete dataset image staging write')
            stream.flush(); os.fsync(stream.fileno())
        if temporary.stat().st_size != len(png) or hashlib.sha256(temporary.read_bytes()).hexdigest() != image.sha256:
            raise OSError('Staged dataset image differs from decoded bytes')
        with jobs._write(db) as session:
            _, _, current_input, current_snapshot = _source(session, decoders, body)
            if current_input != captured or current_snapshot != snapshot:
                raise HTTPException(409, 'Source camera identity changed during image capture')
            existing = session.scalar(select(DatasetImageRow).where(DatasetImageRow.identity_hash == identity))
            if existing is not None:
                result, is_new = get_dataset_image(session, existing.id), False
            else:
                destination = db.dataset_image_dir / f'{image_id}.png'
                os.link(temporary, destination)
                track_created_artifact(session, destination)
                session.add(DatasetImageRow(id=image_id, recording_id=captured.recording_id, ordinal=captured.ordinal,
                    identity_hash=identity, created_at=now, storage_key=destination.name, **_manifest(value, 256 * 1024)))
                session.flush()
                result, is_new = get_dataset_image(session, image_id), True
        dataset_image_bytes(db, result)
        return result, is_new
    finally:
        temporary.unlink(missing_ok=True)


def _stats_and_bounds(body, assets):
    if [asset.id for asset in assets] != [member.image_id for member in body.images]:
        raise ValueError('Dataset image assets do not match ordered membership')
    splits = dict.fromkeys(['train','validation','test','unspecified'], 0)
    image_splits, byte_counts = defaultdict(set), Counter()
    reviewed = negatives = annotations = 0
    for member, asset in zip(body.images, assets):
        splits[member.split] += 1
        byte_counts[asset.image.sha256] += 1
        if member.split != 'unspecified':
            image_splits[asset.image.sha256].add(member.split)
        if member.review_status == 'reviewed':
            reviewed += 1
            negatives += int(not member.annotations)
        annotations += len(member.annotations)
        for annotation in member.annotations:
            if annotation.bbox_xyxy[2] > asset.image.width or annotation.bbox_xyxy[3] > asset.image.height:
                raise ValueError('Annotation box exceeds original saved image bounds')
    if any(len(values) > 1 for values in image_splits.values()):
        raise ValueError('Identical PNG bytes must not cross train/validation/test partitions')
    return DatasetStats(images=len(assets), reviewed=reviewed, unreviewed=len(assets)-reviewed,
        annotations=annotations, negative_images=negatives, duplicate_image_groups=sum(count > 1 for count in byte_counts.values()), splits=splits)


def get_dataset(session, dataset_id, revision=None):
    head = session.get(DatasetRow, dataset_id)
    if head is None:
        raise HTTPException(404, 'Dataset not found')
    latest = session.scalar(select(func.max(DatasetRevisionRow.revision)).where(DatasetRevisionRow.dataset_id == dataset_id))
    if head.revision != latest or not isinstance(head.revision, int) or head.revision < 1:
        raise HTTPException(409, 'Dataset head differs from its revision history')
    row = session.get(DatasetRevisionRow, (dataset_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, 'Dataset revision not found')
    try:
        value = _checked_manifest(row, DatasetSnapshot)
        assets = [get_dataset_image(session, member.image_id) for member in value.images]
        members = list(session.scalars(select(DatasetMembershipRow).where(DatasetMembershipRow.dataset_id == dataset_id,
            DatasetMembershipRow.revision == row.revision).order_by(DatasetMembershipRow.position)))
        if (value.id != row.dataset_id or value.revision != row.revision or value.created_at != row.created_at
                or value.label_map_hash != digest(value.labels) or assets != value.image_assets
                or [(item.position,item.image_id) for item in members] != list(enumerate(item.image_id for item in value.images))
                or value.stats != _stats_and_bounds(value, assets)):
            raise ValueError('Dataset revision differs from its saved image relationships or statistics')
        return DatasetDetail(**value.model_dump(), manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
                             download_url=f'/api/v1/datasets/{dataset_id}/export?revision={row.revision}')
    except (ValueError, TypeError, KeyError, ValidationError) as error:
        raise HTTPException(409, 'Dataset revision failed its integrity check') from error


def _verify_assets(db, body):
    with db.read_session() as session:
        assets = [get_dataset_image(session, member.image_id) for member in body.images]
    for asset in assets:
        dataset_image_bytes(db, asset)
    try:
        _stats_and_bounds(body, assets)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return assets


def _insert_revision(session, dataset_id, revision, body, assets):
    if [get_dataset_image(session, item.id) for item in assets] != assets:
        raise HTTPException(409, 'Dataset image identity changed while publishing labels')
    snapshot = DatasetSnapshot(**body.model_dump(), id=dataset_id, revision=revision, created_at=utcnow(),
        label_map_hash=digest(body.labels), image_assets=assets, stats=_stats_and_bounds(body, assets))
    session.add(DatasetRevisionRow(dataset_id=dataset_id, revision=revision, created_at=snapshot.created_at, **_manifest(snapshot)))
    session.flush()
    for position, image in enumerate(body.images):
        session.add(DatasetMembershipRow(dataset_id=dataset_id, revision=revision, image_id=image.image_id, position=position))
    session.flush()
    return get_dataset(session, dataset_id, revision)


def create_dataset(db, body):
    assets = _verify_assets(db, body)
    with jobs._write(db) as session:
        dataset_id = uid('dataset')
        session.add(DatasetRow(id=dataset_id, revision=1, created_at=utcnow()))
        session.flush()
        return _insert_revision(session, dataset_id, 1, body, assets)


def revise_dataset(db, dataset_id, body):
    assets = _verify_assets(db, body)
    with jobs._write(db) as session:
        current = get_dataset(session, dataset_id)
        if current.revision != body.expected_revision:
            raise HTTPException(409, 'Dataset revision changed; reload before publishing')
        if current.revision >= 2**31 - 1:
            raise HTTPException(409, 'Dataset revision limit reached')
        previous = {item.image_id: item for item in current.images}
        for member in body.images:
            old = previous.get(member.image_id)
            changed = old is not None and (body.labels != current.labels or member.annotations != old.annotations)
            if (changed and old.review_status == member.review_status == 'reviewed'
                    and member.review_note.strip() == old.review_note.strip()
                    and member.reviewer.strip() == old.reviewer.strip()):
                raise HTTPException(422, 'Changed taxonomy or annotations require a renewed review declaration or unreviewed status')
        session.get(DatasetRow, dataset_id).revision += 1
        definition = DatasetCreate.model_validate(body.model_dump(exclude={'expected_revision'}))
        return _insert_revision(session, dataset_id, current.revision + 1, definition, assets)


def register_dataset_routes(api, db, decoders):
    @api.post('/dataset-images', response_model=DatasetImage, status_code=201, tags=['datasets'])
    def capture_image(body: DatasetImageCreate):
        value, is_new = capture_dataset_image(db, decoders, body)
        return JSONResponse(value.model_dump(mode='json'), status_code=201 if is_new else 200)

    @api.get('/dataset-images/{image_id}', response_model=DatasetImage, tags=['datasets'])
    def read_image(image_id: str):
        with db.read_session() as session:
            return get_dataset_image(session, image_id)

    @api.get('/dataset-images/{image_id}/image', response_class=Response, tags=['datasets'])
    def image_file(image_id: str):
        with db.read_session() as session:
            asset = get_dataset_image(session, image_id)
        return Response(dataset_image_bytes(db, asset), media_type='image/png', headers={
            'X-Content-Type-Options':'nosniff', 'X-Content-SHA256':asset.image.sha256, 'ETag':f'"{asset.image.sha256}"'})

    @api.post('/datasets', response_model=DatasetDetail, status_code=201, tags=['datasets'])
    def post_dataset(body: DatasetCreate):
        return create_dataset(db, body)

    @api.get('/datasets', response_model=list[DatasetDetail], tags=['datasets'])
    def list_datasets():
        with db.read_session() as session:
            return [get_dataset(session, key) for key in session.scalars(select(DatasetRow.id).order_by(DatasetRow.created_at.desc(), DatasetRow.id.desc()))]

    @api.get('/datasets/{dataset_id}', response_model=DatasetDetail, tags=['datasets'])
    def read_dataset(dataset_id: str, revision: int | None = Query(default=None, ge=1, le=2**31-1)):
        with db.read_session() as session:
            return get_dataset(session, dataset_id, revision)

    @api.get('/datasets/{dataset_id}/revisions', response_model=list[DatasetDetail], tags=['datasets'])
    def list_revisions(dataset_id: str):
        with db.read_session() as session:
            get_dataset(session, dataset_id)
            return [get_dataset(session, dataset_id, revision) for revision in session.scalars(select(DatasetRevisionRow.revision)
                .where(DatasetRevisionRow.dataset_id == dataset_id).order_by(DatasetRevisionRow.revision.desc()))]

    @api.post('/datasets/{dataset_id}/revisions', response_model=DatasetDetail, status_code=201, tags=['datasets'])
    def post_revision(dataset_id: str, body: DatasetRevisionCreate):
        return revise_dataset(db, dataset_id, body)

    @api.get('/datasets/{dataset_id}/export', response_class=Response, tags=['datasets'])
    def export(dataset_id: str, revision: int | None = Query(default=None, ge=1, le=2**31-1)):
        with db.read_session() as session:
            value = get_dataset(session, dataset_id, revision)
            content = session.get(DatasetRevisionRow, (dataset_id, value.revision)).manifest
        return Response(content, media_type='application/json', headers={'X-Content-Type-Options':'nosniff',
            'Content-Disposition':f'attachment; filename="{dataset_id}_revision_{value.revision}.json"',
            'ETag':f'"{value.manifest_hash}"', 'X-Content-SHA256':value.manifest_hash})
