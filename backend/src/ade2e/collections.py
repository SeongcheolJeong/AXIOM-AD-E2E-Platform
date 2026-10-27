"""Pinned multi-source curation → ordinary datasets, without copying originals.

Collections retain ALL selected source messages, including uncaptured/non-image
ones. Dataset publication requires an explicit include/exclude decision for each.
Annotations are copied only from a chosen immutable dataset member, never from
detector outputs or from the fact that a Case has been marked resolved.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import func, select

from . import jobs
from .case_schemas import Identity
from .collection_models import CollectionRow, CollectionRevisionRow, CollectionDatasetRow
from .collection_schemas import (CollectionDefinition, CollectionCreate, CollectionRevise,
    CollectionDatasetSelection, CollectionDatasetCreate)
from .dataset_models import DatasetImageRow, DatasetRow
from .dataset_schemas import DatasetCreate, DatasetMember
from .datasets import (get_dataset_image, get_dataset, dataset_image_bytes, _insert_revision, _stats_and_bounds)
from .recording_models import RecordingMessageRow
from .recordings import _get_recording, read_message_bytes
from .service import canonical_bytes, digest, uid, utcnow

VERSION = 'axiom-data-collection/1'
MAX_ROWS, MAX_REFERENCES = 256, 1024
MAX_MANIFEST, MAX_PAYLOAD_BYTES = 8*1024*1024, 128*1024*1024
SOURCE_FIELDS = ('recording_id', 'recording_sha256', 'channel_id', 'ordinal', 'message_sha256',
                 'log_time_ns', 'publish_time_ns', 'message_size_bytes')
LIMITATIONS = [
    'A fixed, manually selected collection, not a live query, independent-scene census or representative benchmark.',
    'Exact original-message duplicates are merged with all selection reasons retained. Equal PNGs from different messages remain visible.',
    'Only already captured dataset images can enter a detection dataset. Uncaptured or non-image sources are never silently dropped.',
    'Labels are optional, explicit selections from pinned dataset revisions. No labels means unreviewed, NOT a negative image.',
    'Scene conditions and source rights remain declarations. No automatic tagging, annotation approval, training or model execution.',
    'Metadata snapshots do not continuously revalidate source files. Publication checks selected original messages and PNG bytes.',
]


def _source(value):
    return {key: value[key] for key in SOURCE_FIELDS}


def _source_key(value):
    return digest({key: value[key] for key in ('recording_sha256', 'channel_id', 'ordinal', 'message_sha256')})


def _sealed(row):
    raw = row.manifest
    try:
        if not 0 < len(raw) <= MAX_MANIFEST or len(raw) != row.manifest_size_bytes or hashlib.sha256(raw).hexdigest() != row.manifest_hash:
            raise ValueError('Size/hash differs')
        value = json.loads(raw)
        if canonical_bytes(value) != raw:
            raise ValueError('Noncanonical manifest')
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Collection evidence failed its integrity check') from error


def _seal(value):
    raw = canonical_bytes(value)
    if len(raw) > MAX_MANIFEST:
        raise HTTPException(413, 'Collection evidence exceeds 8 MiB; narrow the selection')
    return dict(manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest(), manifest_size_bytes=len(raw))


def _describe(value, row, path):
    return {**value, 'manifest_hash': row.manifest_hash, 'manifest_size_bytes': row.manifest_size_bytes,
            'download_url': path}


def resolve_selection(session, body):
    """Read-only exact expansion, with pinned revisions rather than latest aliases."""
    from .cases import _get as get_case
    rows, refs, reference_count = {}, [], 0

    def add(source, ref_index, assets=(), label=None):
        nonlocal reference_count
        reference_count += 1
        if reference_count > MAX_REFERENCES:
            raise HTTPException(413, 'Collection exceeds 1024 expanded references; no partial selection returned')
        key = _source_key(source)
        row = rows.setdefault(key, {'key': key, 'source': source, 'source_indices': [], 'images': [], 'label_options': []})
        if len(rows) > MAX_ROWS:
            raise HTTPException(413, 'Collection exceeds 256 unique messages; no partial selection returned')
        if row['source'] != source:
            raise HTTPException(409, 'Two selected sources disagree on original message identity or clocks')
        row['source_indices'].append(ref_index)
        for asset in assets:
            value = asset.model_dump(mode='json')
            if _source(value['input']) != source:
                raise HTTPException(409, 'Image is not the exact selected source')
            if not any(a['id'] == value['id'] for a in row['images']):
                row['images'].append(value)
        if label and not any(o['id'] == label['id'] for o in row['label_options']):
            row['label_options'].append(label)

    for index, selection in enumerate(body.sources):
        ref = {'index': index, 'selection': selection.model_dump(mode='json')}
        if selection.kind == 'case':
            case = get_case(session, selection.case_id, selection.revision)
            ref.update(name=case['title'], manifest_hash=case['manifest_hash'], conditions=case.get('conditions'),
                       case_state=case['state'], segment_hash=case['segment_hash'])
            s = case['segment']
            if len(s['samples']) + reference_count > MAX_REFERENCES:
                raise HTTPException(413, 'Case interval exceeds collection reference limit; choose a smaller interval')
            for sample in s['samples']:
                source = dict(recording_id=s['recording_id'], recording_sha256=s['recording_sha256'],
                    channel_id=s['channel_id'], ordinal=sample['ordinal'], message_sha256=sample['sha256'],
                    log_time_ns=sample['log_time_ns'], publish_time_ns=sample['publish_time_ns'], message_size_bytes=sample['size_bytes'])
                candidates = list(session.scalars(select(DatasetImageRow.id).where(
                    DatasetImageRow.recording_id == s['recording_id'], DatasetImageRow.ordinal == sample['ordinal']).order_by(DatasetImageRow.id).limit(17)))
                if len(candidates) > 16:
                    raise HTTPException(413, 'Too many captured interpretations of one message; select an explicit image source')
                images = [get_dataset_image(session, i) for i in candidates]
                images = [a for a in images if _source(a.input.model_dump(mode='json')) == source]
                add(source, index, images)
        elif selection.kind == 'dataset':
            dataset = get_dataset(session, selection.dataset_id, selection.revision)
            ref.update(name=dataset.name, manifest_hash=dataset.manifest_hash, labels=dataset.labels,
                       license=dataset.license, source=dataset.source)
            assets = {a.id: a for a in dataset.image_assets}
            members = [m for m in dataset.images if selection.split == 'all' or m.split == selection.split]
            if not members:
                raise HTTPException(422, 'Selected dataset split is empty')
            for member in members:
                asset = assets[member.image_id]
                option = {'dataset_id': dataset.id, 'dataset_revision': dataset.revision,
                          'dataset_manifest_hash': dataset.manifest_hash, 'dataset_name': dataset.name,
                          'labels': dataset.labels, 'member': member.model_dump(mode='json')}
                option['id'] = digest(option)
                add(_source(asset.input.model_dump(mode='json')), index, [asset], option)
        else:
            asset = get_dataset_image(session, selection.image_id)
            ref.update(name=asset.id, manifest_hash=digest(asset.model_dump(mode='json')))
            add(_source(asset.input.model_dump(mode='json')), index, [asset])
        refs.append(ref)

    duplicates = defaultdict(set)
    for row in rows.values():
        row['images'].sort(key=lambda a: a['id'])
        row['label_options'].sort(key=lambda o: o['id'])
        row['image_state'] = 'image_not_captured' if not row['images'] else 'ready' if len(row['images']) == 1 else 'choose_image_interpretation'
        for asset in row['images']:
            duplicates[asset['image']['sha256']].add(row['key'])
    duplicate_groups = [{'png_sha256': sha, 'source_keys': sorted(keys)} for sha, keys in sorted(duplicates.items()) if len(keys) > 1]
    value = {'schema_version': 'collection-selection/1', 'definition': body.model_dump(mode='json'),
        'sources': refs, 'rows': list(rows.values()), 'duplicate_png_groups': duplicate_groups,
        'summary': {'source_selections': len(refs), 'expanded_references': reference_count,
            'unique_messages': len(rows), 'repeated_message_references': reference_count - len(rows),
            'recordings': len({r['source']['recording_sha256'] for r in rows.values()}),
            'with_captured_image': sum(bool(r['images']) for r in rows.values()),
            'without_captured_image': sum(not r['images'] for r in rows.values()),
            'image_interpretation_choices': sum(len(r['images']) > 1 for r in rows.values()),
            'with_reviewed_label_option': sum(any(o['member']['review_status'] == 'reviewed' for o in r['label_options']) for r in rows.values()),
            'duplicate_png_groups': len(duplicate_groups)}, 'limitations': LIMITATIONS}
    _seal(value)
    return value


def get_collection(session, collection_id, revision=None):
    head = session.get(CollectionRow, collection_id)
    if head is None:
        raise HTTPException(404, 'Collection not found')
    latest = session.scalar(select(func.max(CollectionRevisionRow.revision)).where(CollectionRevisionRow.collection_id == collection_id))
    if head.revision != latest:
        raise HTTPException(409, 'Collection head differs from revision history')
    row = session.get(CollectionRevisionRow, (collection_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, 'Collection revision not found')
    value = _sealed(row)
    if value.get('schema_version') != VERSION or value.get('id') != collection_id or value.get('revision') != row.revision:
        raise HTTPException(409, 'Collection identity differs from sealed snapshot')
    if row.revision > 1:
        previous = session.get(CollectionRevisionRow, (collection_id, row.revision-1))
        if previous is None or value['previous_manifest_hash'] != previous.manifest_hash:
            raise HTTPException(409, 'Collection revision chain differs')
    elif value['previous_manifest_hash'] is not None:
        raise HTTPException(409, 'First collection revision has a predecessor')
    return _describe(value, row, f'/api/v1/collections/{collection_id}/export?revision={row.revision}')


def _request_key(operation, identifier, key):
    return 'collection:' + digest({'operation': operation, 'id': identifier, 'key': key})


def save_collection(db, body, key, identifier=None):
    with jobs._write(db) as session:
        return save_collection_in_session(session, body, key, identifier)


def save_collection_in_session(session, body, key, identifier=None):
    """Compose a collection and its caller's provenance receipt atomically."""
    definition = CollectionDefinition.model_validate(body.model_dump(include=set(CollectionDefinition.model_fields)))
    request_key, request_hash = _request_key('save', identifier, key), digest(body.model_dump(mode='json'))
    old = session.scalar(select(CollectionRevisionRow).where(CollectionRevisionRow.request_key == request_key))
    if old:
        if old.request_hash != request_hash:
            raise HTTPException(409, 'Idempotency-Key identifies a different collection request')
        return get_collection(session, old.collection_id, old.revision)
    prior = get_collection(session, identifier) if identifier else None
    if prior and prior['revision'] != body.expected_revision:
        raise HTTPException(409, 'Collection changed; reload before revising')
    selection = resolve_selection(session, definition)
    if digest(selection) != body.expected_preview_hash:
        raise HTTPException(409, 'Selection changed since preview; review the full source set again')
    revision = prior['revision'] + 1 if prior else 1
    if revision > 2**31-1:
        raise HTTPException(409, 'Collection revision limit reached')
    if not identifier:
        identifier = uid('collection')
        session.add(CollectionRow(id=identifier, revision=revision, created_at=utcnow()))
        session.flush()
    else:
        session.get(CollectionRow, identifier).revision = revision
    value = {'schema_version': VERSION, 'id': identifier, 'revision': revision, 'created_at': utcnow(),
             'previous_manifest_hash': prior['manifest_hash'] if prior else None, 'selection': selection}
    session.add(CollectionRevisionRow(collection_id=identifier, revision=revision, request_key=request_key,
                                      request_hash=request_hash, **_seal(value)))
    session.flush()
    return get_collection(session, identifier, revision)


def _verify_selected_bytes(db, session, assets):
    total = sum(a.image.size_bytes + a.input.message_size_bytes for a in assets)
    if total > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, 'Selected original messages and PNGs exceed 128 MiB; no partial dataset')
    for asset in assets:
        if get_dataset_image(session, asset.id) != asset:
            raise HTTPException(409, 'Captured image metadata changed after collection snapshot')
        recording = _get_recording(session, asset.input.recording_id)
        m = session.get(RecordingMessageRow, (recording.id, asset.input.ordinal))
        if (m is None or recording.sha256 != asset.input.recording_sha256 or m.channel_id != asset.input.channel_id
                or m.sha256 != asset.input.message_sha256 or m.payload_length != asset.input.message_size_bytes
                or str(int(m.log_time_key)) != asset.input.log_time_ns or str(int(m.publish_time_key)) != asset.input.publish_time_ns):
            raise HTTPException(409, 'Original source index differs from collection image identity')
        read_message_bytes(db, recording, m)
        dataset_image_bytes(db, asset)


def dataset_proposal(db, session, identifier, body):
    from .dataset_schemas import DatasetImage
    collection = get_collection(session, identifier, body.collection_revision)
    rows = collection['selection']['rows']
    choices = {d.key: d for d in body.decisions}
    if set(choices) != {r['key'] for r in rows}:
        raise HTTPException(422, 'Decide include/exclude for EVERY original message; no hidden or extra rows')
    members, assets, exclusions, included_keys = [], [], [], []
    recording_splits = defaultdict(set)
    for row in rows:
        choice = choices[row['key']]
        if choice.action == 'exclude':
            exclusions.append({'key': row['key'], 'reason': choice.reason})
            continue
        image = next((a for a in row['images'] if a['id'] == choice.image_id), None)
        if image is None:
            raise HTTPException(422, 'Included image must be a captured interpretation pinned in this collection; uncaptured sources need explicit exclusion')
        asset = DatasetImage.model_validate(image)
        if choice.label_option_id is None:
            member = DatasetMember(image_id=asset.id, split=choice.split)
        else:
            option = next((o for o in row['label_options'] if o['id'] == choice.label_option_id), None)
            if option is None or option['member']['image_id'] != asset.id:
                raise HTTPException(422, 'Selected label source does not belong to the selected image')
            dataset = get_dataset(session, option['dataset_id'], option['dataset_revision'])
            source_member = next((m for m in dataset.images if m.image_id == asset.id), None)
            if (dataset.manifest_hash != option['dataset_manifest_hash'] or source_member is None
                    or source_member.model_dump(mode='json') != option['member'] or dataset.labels != option['labels']):
                raise HTTPException(409, 'Pinned annotation source differs from its original dataset revision')
            if set(option['labels']) != set(body.labels):
                raise HTTPException(422, 'Chosen annotations require the same complete label vocabulary; adding classes does not make them reviewed negatives. Use unreviewed images and review a new taxonomy explicitly.')
            member = source_member.model_copy(update={'split': choice.split})
        if choice.split != 'unspecified':
            recording_splits[asset.input.recording_sha256].add(choice.split)
        members.append(member); assets.append(asset); included_keys.append(row['key'])
    if not members:
        raise HTTPException(422, 'A dataset needs at least one explicitly included captured image')
    if any(len(splits) > 1 for splits in recording_splits.values()):
        raise HTTPException(422, 'Keep each recording in one train/validation/test partition to avoid adjacent-frame leakage; unspecified remains unassigned')
    try:
        definition = DatasetCreate(name=body.name, note=body.note, labels=body.labels, images=members,
            source=f'AXIOM collection {identifier}', source_revision=f"{body.collection_revision}:{collection['manifest_hash']}",
            license='unknown; review original source rights')
        stats = _stats_and_bounds(definition, assets)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    _verify_selected_bytes(db, session, assets)
    selection = CollectionDatasetSelection.model_validate(body.model_dump(include=set(CollectionDatasetSelection.model_fields)))
    value = {'schema_version': 'collection-dataset-proposal/1', 'collection_id': identifier,
        'collection_revision': body.collection_revision, 'collection_manifest_hash': collection['manifest_hash'],
        'request': selection.model_dump(mode='json'), 'dataset_definition': definition.model_dump(mode='json'),
        'stats': stats.model_dump(mode='json'), 'included_keys': included_keys, 'exclusions': exclusions,
        'denominator': {'collection_messages': len(rows), 'included': len(members), 'excluded': len(exclusions)},
        'source_bytes_checked': True, 'jobs_created': 0,
        'limitations': ['No prediction is a ground-truth label. Unreviewed empty annotations are NOT negatives.',
            'Same recording and identical PNGs cannot cross assigned splits; this does not prove independence across drives or near-duplicate scenes.',
            'Annotation review is copied as a historical declaration, not a new authenticated approval. Source rights are not verified.']}
    _seal(value)
    return value, definition, assets


def publication_get(session, publication_id):
    row = session.get(CollectionDatasetRow, publication_id)
    if row is None:
        raise HTTPException(404, 'Collection dataset publication not found')
    value = _sealed(row)
    collection = get_collection(session, row.collection_id, row.collection_revision)
    dataset = get_dataset(session, row.dataset_id, row.dataset_revision)
    if (value.get('schema_version') != 'collection-dataset-publication/1' or value.get('id') != row.id
            or value['proposal']['collection_id'] != row.collection_id
            or value['proposal']['collection_revision'] != row.collection_revision
            or value['proposal']['collection_manifest_hash'] != collection['manifest_hash']
            or value['dataset'] != {'id': dataset.id, 'revision': dataset.revision, 'manifest_hash': dataset.manifest_hash}
            or digest(value['proposal']) != value['proposal_hash']
            or any(dataset.model_dump(mode='json')[k] != v for k, v in value['proposal']['dataset_definition'].items())):
        raise HTTPException(409, 'Dataset publication differs from its pinned collection or dataset revision')
    return _describe(value, row, f'/api/v1/collection-datasets/{row.id}/export')


def publish_dataset(db, identifier, body, key):
    request_key = _request_key('dataset', identifier, key)
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        prior = session.scalar(select(CollectionDatasetRow).where(CollectionDatasetRow.request_key == request_key))
        if prior:
            if prior.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies a different dataset publication')
            return publication_get(session, prior.id)
        proposal, definition, assets = dataset_proposal(db, session, identifier, body)
        if digest(proposal) != body.expected_preview_hash:
            raise HTTPException(409, 'Dataset preview changed; review all decisions before publication')
        dataset_id, publication_id = uid('dataset'), uid('collection_dataset')
        session.add(DatasetRow(id=dataset_id, revision=1, created_at=utcnow())); session.flush()
        dataset = _insert_revision(session, dataset_id, 1, definition, assets)
        receipt = {'schema_version': 'collection-dataset-publication/1', 'id': publication_id,
            'created_at': utcnow(), 'proposal': proposal, 'proposal_hash': digest(proposal),
            'dataset': {'id': dataset.id, 'revision': dataset.revision, 'manifest_hash': dataset.manifest_hash}}
        session.add(CollectionDatasetRow(id=publication_id, collection_id=identifier,
            collection_revision=body.collection_revision, dataset_id=dataset.id, dataset_revision=1,
            request_key=request_key, request_hash=request_hash, **_seal(receipt)))
        session.flush()
        return publication_get(session, publication_id)


def _download(raw, name):
    return Response(raw, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{name}.json"',
        'X-Content-Type-Options': 'nosniff', 'X-Content-SHA256': hashlib.sha256(raw).hexdigest(), 'Cache-Control': 'no-store'})


def register_collection_routes(api, db):
    @api.post('/collections/preview', tags=['data collections'])
    def preview(body: CollectionDefinition):
        with db.read_session() as session:
            value = resolve_selection(session, body)
            return {**value, 'preview_hash': digest(value), 'jobs_created': 0, 'writes_created': 0}

    @api.post('/collections', status_code=201, tags=['data collections'])
    def create(body: CollectionCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return save_collection(db, body, idempotency_key)

    @api.get('/collections', tags=['data collections'])
    def listing(q: str = Query('', max_length=300), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        with db.read_session() as session:
            ids = list(session.scalars(select(CollectionRow.id).order_by(CollectionRow.created_at.desc(), CollectionRow.id).limit(1001)))
            if len(ids) > 1000:
                raise HTTPException(413, 'Collection library exceeds the current 1000-collection limit')
            items = []
            for identifier in ids:
                value = get_collection(session, identifier); d = value['selection']['definition']
                if q.strip().casefold() in (d['name']+' '+d['purpose']).casefold():
                    items.append({'id': identifier, 'revision': value['revision'], 'name': d['name'], 'purpose': d['purpose'],
                                  'manifest_hash': value['manifest_hash'], 'summary': value['selection']['summary']})
            return {'items': items[offset:offset+limit], 'total': len(items), 'next_offset': offset+limit if offset+limit < len(items) else None}

    @api.get('/collections/{collection_id}', tags=['data collections'])
    def read(collection_id: Identity, revision: int | None = Query(None, ge=1, le=2**31-1)):
        with db.read_session() as session:
            return get_collection(session, collection_id, revision)

    @api.post('/collections/{collection_id}/revisions', status_code=201, tags=['data collections'])
    def revise(collection_id: Identity, body: CollectionRevise, idempotency_key: str = Header(min_length=1, max_length=180)):
        return save_collection(db, body, idempotency_key, collection_id)

    @api.get('/collections/{collection_id}/export', tags=['data collections'])
    def export(collection_id: Identity, revision: int = Query(..., ge=1, le=2**31-1)):
        with db.read_session() as session:
            get_collection(session, collection_id, revision)
            return _download(session.get(CollectionRevisionRow, (collection_id, revision)).manifest, f'{collection_id}-r{revision}')

    @api.post('/collections/{collection_id}/datasets/preview', tags=['data collections'])
    def preview_dataset(collection_id: Identity, body: CollectionDatasetSelection):
        with db.read_session() as session:
            value, _, _ = dataset_proposal(db, session, collection_id, body)
            return {**value, 'preview_hash': digest(value)}

    @api.post('/collections/{collection_id}/datasets', status_code=201, tags=['data collections'])
    def create_dataset(collection_id: Identity, body: CollectionDatasetCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return publish_dataset(db, collection_id, body, idempotency_key)

    @api.get('/collection-datasets', tags=['data collections'])
    def dataset_links(collection_id: Identity | None = None, dataset_id: Identity | None = None):
        if collection_id is None and dataset_id is None:
            raise HTTPException(422, 'Scope publication receipts to a collection or dataset')
        with db.read_session() as session:
            query = select(CollectionDatasetRow.id).order_by(CollectionDatasetRow.id)
            if collection_id:
                get_collection(session, collection_id); query = query.where(CollectionDatasetRow.collection_id == collection_id)
            if dataset_id:
                get_dataset(session, dataset_id); query = query.where(CollectionDatasetRow.dataset_id == dataset_id)
            ids = list(session.scalars(query.limit(101)))
            if len(ids) > 100:
                raise HTTPException(413, 'Publication list exceeds 100; scope to one dataset')
            return {'items': [publication_get(session, i) for i in ids]}

    @api.get('/collection-datasets/{publication_id}', tags=['data collections'])
    def read_publication(publication_id: Identity):
        with db.read_session() as session:
            return publication_get(session, publication_id)

    @api.get('/collection-datasets/{publication_id}/export', tags=['data collections'])
    def export_publication(publication_id: Identity):
        with db.read_session() as session:
            publication_get(session, publication_id)
            return _download(session.get(CollectionDatasetRow, publication_id).manifest, publication_id)
