"""Collection → actual image features → reviewed scene declarations → search.

Descriptors are numeric appearance baselines, not learned semantic embeddings.
Every source remains in the denominator, including unindexed and unavailable
images. No detector, training, simulator, external endpoint or vehicle is called.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query
from sqlalchemy import func, select

from . import jobs, scene_features, collections
from .case_schemas import Identity
from .collection_schemas import CollectionDefinition, CollectionCreate
from .dataset_schemas import DatasetImage
from .datasets import dataset_image_bytes
from .scene_models import SceneIndexRow, SceneReviewRow, SceneSelectionRow
from .scene_schemas import SceneIndexSelection, SceneIndexCreate, SceneTagReview, SceneQuery, SceneCollectionCreate
from .service import canonical_bytes, digest, uid, utcnow

MAX_TOTAL_PIXELS = 64_000_000
MAX_INPUT_BYTES = 128*1024*1024
LIMITATIONS = [
    'Appearance descriptors and pixel-rule proposals are not semantic embeddings or measured environmental conditions.',
    'Dark image does not imply night; bright pixels do not establish sensor clipping or glare. Weather/time-of-day are human declarations only.',
    'Similarity is not a probability, same event, same failure cause, independent scene or safety/ODD coverage.',
    'All source messages remain visible; ambiguous, uncaptured and unavailable images have no descriptor or fabricated score.',
    'Existing label/condition declarations are context, not fresh model predictions or automatically approved tags.',
    'Source bytes are verified during indexing and selected-output publication, not continuously by metadata queries.',
    'No new inference, model download, training, simulator execution or public network access is performed.',
]


def _key(path, key):
    if not isinstance(key, str) or not 1 <= len(key) <= 180:
        raise HTTPException(422, 'A stable Idempotency-Key of 1..180 characters is required')
    return 'scene:' + digest({'path': path, 'key': key})


def _describe(value, row, path):
    return {**value, 'manifest_hash': row.manifest_hash, 'manifest_size_bytes': row.manifest_size_bytes,
            'download_url': path}


def proposal(session, body):
    selection = SceneIndexSelection.model_validate(body.model_dump(include=set(SceneIndexSelection.model_fields)))
    collection = collections.get_collection(session, body.collection_id, body.collection_revision)
    rows, pixels, byte_count = [], 0, 0
    for source in collection['selection']['rows']:
        asset = source['images'][0] if len(source['images']) == 1 else None
        if asset:
            pixels += asset['image']['width']*asset['image']['height']
            byte_count += asset['image']['size_bytes'] + asset['input']['message_size_bytes']
        declarations = [{'dataset_id': o['dataset_id'], 'dataset_revision': o['dataset_revision'],
            'dataset_manifest_hash': o['dataset_manifest_hash'], 'review_status': o['member']['review_status'],
            'annotation_source': o['member']['annotation_source'],
            'label_counts': dict(sorted(Counter(a['label'] for a in o['member']['annotations']).items()))}
            for o in source['label_options']]
        rows.append({'key': source['key'], 'source': source['source'], 'image': asset,
            'source_state': source['image_state'], 'label_declarations': declarations})
    if not rows or len(rows) > 256 or pixels > MAX_TOTAL_PIXELS or byte_count > MAX_INPUT_BYTES:
        raise HTTPException(413, 'Discovery supports 1..256 messages, 64 million decoded pixels and 128 MiB source/PNG bytes; no truncation')
    if not any(r['image'] for r in rows):
        raise HTTPException(422, 'No unambiguous captured image. Capture images or select an explicit interpretation first')
    return {'schema_version': 'scene-index-proposal/1', 'request': selection.model_dump(mode='json'),
        'collection': {'id': collection['id'], 'revision': collection['revision'], 'manifest_hash': collection['manifest_hash']},
        'recipe': scene_features.recipe(), 'rows': rows,
        'workload': {'source_messages': len(rows), 'candidate_images': sum(r['image'] is not None for r in rows),
            'unindexed_sources': sum(r['image'] is None for r in rows), 'total_pixels': pixels, 'source_and_png_bytes': byte_count},
        'limitations': LIMITATIONS}


def index_get(session, index_id):
    row = session.get(SceneIndexRow, index_id)
    if row is None:
        raise HTTPException(404, 'Scene index not found')
    data = collections._sealed(row)
    try:
        c = collections.get_collection(session, row.collection_id, row.collection_revision)
        p = data['proposal']
        if (data['schema_version'] != 'scene-index/1' or data['id'] != row.id
                or p['collection'] != {'id': c['id'], 'revision': c['revision'], 'manifest_hash': c['manifest_hash']}
                or data['proposal_hash'] != digest(p) or p['recipe']['id'] != scene_features.VERSION
                or [r['key'] for r in data['rows']] != [r['key'] for r in p['rows']]
                or len({r['key'] for r in data['rows']}) != len(data['rows'])
                or data['counts'] != dict(Counter(r['status'] for r in data['rows']))):
            raise ValueError('Index identity or denominator changed')
        for item, original in zip(data['rows'], p['rows']):
            if any(item[k] != v for k,v in original.items()):
                raise ValueError('Index source differs from the fixed proposal')
            if item['status'] == 'indexed':
                scene_features.validate(item['features'])
                if item['features_hash'] != digest(item['features']) or not item['image']:
                    raise ValueError('Descriptor identity differs')
            elif item['status'] not in ('unavailable', 'not_captured', 'ambiguous') or item['features'] is not None:
                raise ValueError('Unavailable source acquired a descriptor')
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(409, 'Scene index source/feature contract failed') from error
    return _describe(data, row, f'/api/v1/scene-indexes/{index_id}/export')


def create_index(db, body, key):
    request_key, request_hash = _key('index', key), digest(body.model_dump(mode='json'))
    with db.read_session() as session:
        existing = session.scalar(select(SceneIndexRow).where(SceneIndexRow.request_key == request_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies another index request')
            return index_get(session, existing.id)
        p = proposal(session, body)
        if digest(p) != body.expected_preview_hash:
            raise HTTPException(409, 'Index workload changed; inspect it again')
        results = []
        for original in p['rows']:
            item = {**deepcopy(original), 'status': 'not_captured' if original['source_state'] == 'image_not_captured' else 'ambiguous',
                    'features': None, 'features_hash': None, 'reason': 'No unique captured interpretation; source retained without analysis'}
            if original['image']:
                try:
                    asset = DatasetImage.model_validate(original['image'])
                    collections._verify_selected_bytes(db, session, [asset])
                    features = scene_features.analyze(dataset_image_bytes(db, asset))
                    item.update(status='indexed', features=features, features_hash=digest(features), reason=None)
                except HTTPException as error:
                    if error.status_code not in (404, 409, 422):
                        raise
                    item.update(status='unavailable', reason=str(error.detail)[:500])
                except (ValueError, OSError) as error:
                    item.update(status='unavailable', reason=f'Image analysis unavailable: {str(error)[:400]}')
            results.append(item)
    # Numeric work runs outside the writer transaction. Compare the fixed source
    # proposal again before publishing; concurrent retry publishes only one index.
    with jobs._write(db) as session:
        existing = session.scalar(select(SceneIndexRow).where(SceneIndexRow.request_key == request_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies another index request')
            return index_get(session, existing.id)
        if proposal(session, body) != p:
            raise HTTPException(409, 'Source definition changed during analysis')
        identifier, now = uid('scene_index'), utcnow()
        data = {'schema_version': 'scene-index/1', 'id': identifier, 'created_at': now,
            'proposal': p, 'proposal_hash': digest(p), 'rows': results,
            'counts': dict(Counter(r['status'] for r in results)), 'environment_verified': False}
        session.add(SceneIndexRow(id=identifier, collection_id=body.collection_id, collection_revision=body.collection_revision,
            created_at=now, request_key=request_key, request_hash=request_hash, **collections._seal(data)))
        session.flush()
        return index_get(session, identifier)


def review_get(session, index, revision=None):
    latest = session.scalar(select(func.max(SceneReviewRow.revision)).where(SceneReviewRow.index_id == index['id'])) or 0
    revision = latest if revision is None else revision
    if revision == 0:
        data = {'schema_version': 'scene-tag-review/1', 'index_id': index['id'], 'revision': 0,
            'index_manifest_hash': index['manifest_hash'], 'previous_manifest_hash': None, 'declarations': {}}
        return {**data, 'manifest_hash': digest(data)}
    row = session.get(SceneReviewRow, (index['id'], revision))
    if row is None:
        raise HTTPException(404, 'Scene tag-review revision not found')
    data = collections._sealed(row)
    try:
        if (data['index_id'] != index['id'] or data['revision'] != revision or data['index_manifest_hash'] != index['manifest_hash']):
            raise ValueError('Review identity mismatch')
        previous = session.get(SceneReviewRow, (index['id'], revision-1)) if revision > 1 else None
        if data['previous_manifest_hash'] != (previous.manifest_hash if previous else None) or (revision > 1 and previous is None):
            raise ValueError('Review history differs')
        by_key = {r['key']: r for r in index['rows']}
        for source_key, declaration in data['declarations'].items():
            source = by_key[source_key]
            check = SceneTagReview(expected_review_revision=0, **declaration['review'])
            if (check.source_key != source_key or source['status'] != 'indexed'
                    or declaration['image_sha256'] != source['image']['image']['sha256']
                    or declaration['reviewer_is_authenticated'] is not False
                    or not set(check.accepted_feature_tags) <= {p['tag'] for p in source['features']['proposals']}):
                raise ValueError('Review does not belong to an indexed image')
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(409, 'Scene review declaration contract failed') from error
    return {**data, 'manifest_hash': row.manifest_hash}


def save_review(db, index_id, body, key):
    storage, request_hash = _key(index_id+'/reviews', key), digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        index = index_get(session, index_id)
        existing = session.scalar(select(SceneReviewRow).where(SceneReviewRow.request_key == storage))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies another review')
            return review_get(session, index, existing.revision)
        current = review_get(session, index)
        if current['revision'] != body.expected_review_revision:
            raise HTTPException(409, 'Tag review changed; reload before publishing')
        source = next((r for r in index['rows'] if r['key'] == body.source_key), None)
        if source is None or source['status'] != 'indexed':
            raise HTTPException(422, 'Review a successfully indexed captured image')
        if not set(body.accepted_feature_tags) <= {p['tag'] for p in source['features']['proposals']}:
            raise HTTPException(422, 'Accept only proposed numeric-feature tags; declare lighting/weather separately')
        declarations = deepcopy(current['declarations'])
        declarations[body.source_key] = {'review': body.model_dump(mode='json', exclude={'expected_review_revision'}),
            'image_sha256': source['image']['image']['sha256'], 'declared_at': utcnow(), 'reviewer_is_authenticated': False}
        revision = current['revision']+1
        data = {'schema_version': 'scene-tag-review/1', 'index_id': index_id, 'index_manifest_hash': index['manifest_hash'],
            'revision': revision, 'previous_manifest_hash': current['manifest_hash'] if current['revision'] else None,
            'declarations': declarations}
        session.add(SceneReviewRow(index_id=index_id, revision=revision, request_key=storage,
            request_hash=request_hash, **collections._seal(data))); session.flush()
        return review_get(session, index, revision)


def search(session, index_id, query):
    index = index_get(session, index_id)
    if query.anchor_key and index['proposal']['recipe'] != scene_features.recipe():
        raise HTTPException(409, 'The recorded visual-distance implementation or dependencies are not active; rebuild an index explicitly instead of mixing descriptor versions')
    reviews = review_get(session, index, query.review_revision)
    normalized = query.model_copy(update={'review_revision': reviews['revision']})
    anchor = next((r for r in index['rows'] if r['key'] == query.anchor_key), None)
    if query.anchor_key and (anchor is None or anchor['status'] != 'indexed'):
        raise HTTPException(422, 'Query image must be an indexed member of this exact source snapshot')
    results = []
    for source in index['rows']:
        review = reviews['declarations'].get(source['key'])
        declaration = review['review'] if review else None
        status = declaration['status'] if declaration else 'pending'
        verified = declaration is not None and status == 'reviewed'
        effective = {k: declaration[k] if verified else 'unknown' for k in ('lighting','weather')}
        proposals = source['features']['proposals'] if source['features'] else []
        tags = {p['tag'] for p in proposals} if query.feature_scope == 'proposed' else set(declaration['accepted_feature_tags'] if verified else [])
        excluded = []
        if query.feature_tag and query.feature_tag not in tags: excluded.append('feature_filter')
        for key in ('lighting', 'weather'):
            if getattr(query, key) != 'all' and getattr(query, key) != effective[key]: excluded.append(key+'_filter')
        if query.review_status != 'all' and query.review_status != status: excluded.append('review_filter')
        distance, identical_pixels = None, False
        if anchor:
            if source['key'] == query.anchor_key: excluded.append('query_image')
            if source['status'] != 'indexed': excluded.append('unindexed')
            else:
                distance = scene_features.distance(anchor['features'], source['features'])
                identical_pixels = anchor['features']['pixel_identity'] == source['features']['pixel_identity']
                if query.exclude_exact_pixels and identical_pixels: excluded.append('identical_pixels')
            if query.exclude_same_recording and source['source']['recording_sha256'] == anchor['source']['recording_sha256']:
                excluded.append('same_recording')
        results.append({**source, 'review': review, 'review_status': status, 'effective_conditions': effective,
            'distance': distance, 'identical_pixels_to_query': identical_pixels,
            'matched': not excluded, 'filter_reasons': excluded})
    if anchor:
        results.sort(key=lambda r: (r['distance'] is None, r['distance'] or 0., r['key']))
    duplicates = defaultdict(list)
    for row in index['rows']:
        if row['features']: duplicates[row['features']['pixel_identity']].append(row['key'])
    return {'schema_version': 'scene-search/1', 'index_id': index_id, 'index_manifest_hash': index['manifest_hash'],
        'source_collection': index['proposal']['collection'], 'recipe': index['proposal']['recipe'],
        'query': normalized.model_dump(mode='json'), 'review_snapshot': reviews,
        'rows': results, 'duplicate_pixel_groups': [keys for keys in duplicates.values() if len(keys) > 1],
        'denominator': {'all_sources': len(results), 'indexed': index['counts'].get('indexed', 0),
            'unindexed': len(results)-index['counts'].get('indexed',0), 'matched': sum(r['matched'] for r in results),
            'filtered_out': sum(not r['matched'] for r in results), 'reviewed': sum(r['review_status']=='reviewed' for r in results),
            'pending_or_draft': sum(r['review_status']!='reviewed' for r in results)},
        'limitations': LIMITATIONS}


def selection_get(session, selection_id):
    row = session.get(SceneSelectionRow, selection_id)
    if row is None: raise HTTPException(404, 'Scene selection receipt not found')
    value = collections._sealed(row)
    target = collections.get_collection(session, row.collection_id, 1)
    index = index_get(session, row.index_id)
    if (value['index_id'] != row.index_id or value['id'] != row.id
            or value['search']['index_manifest_hash'] != index['manifest_hash']
            or digest(value['search']) != value['search_hash']
            or value['collection'] != {'id': target['id'], 'revision': 1, 'manifest_hash': target['manifest_hash']}):
        raise HTTPException(409, 'Selection receipt differs from its source/collection')
    return _describe(value, row, f'/api/v1/scene-selections/{row.id}/export')


def save_selection(db, index_id, body, key):
    storage, request_hash = _key(index_id+'/collections', key), digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        old = session.scalar(select(SceneSelectionRow).where(SceneSelectionRow.request_key == storage))
        if old:
            if old.request_hash != request_hash: raise HTTPException(409, 'Idempotency-Key identifies another selection')
            return selection_get(session, old.id)
        result = search(session, index_id, body.query)
        if digest(result) != body.expected_search_hash:
            raise HTTPException(409, 'Search snapshot changed; review the whole result again')
        rows = {r['key']: r for r in result['rows']}
        chosen = [rows[k] for k in body.selected_keys if k in rows]
        if len(chosen) != len(body.selected_keys) or any(not r['matched'] or r['status'] != 'indexed' for r in chosen):
            raise HTTPException(422, 'Choose only indexed matching results from this pinned search')
        assets = [DatasetImage.model_validate(r['image']) for r in chosen]
        collections._verify_selected_bytes(db, session, assets)
        definition = CollectionDefinition(name=body.name, purpose=body.purpose, sources=[
            {'kind': 'image', 'image_id': r['image']['id'], 'reason': f'Scene discovery {index_id}; review r{body.query.review_revision}; distance={r["distance"]}. {body.purpose}'[:1500]}
            for r in chosen])
        expanded = collections.resolve_selection(session, definition)
        target = collections.save_collection_in_session(session,
            CollectionCreate(**definition.model_dump(), expected_preview_hash=digest(expanded)), storage)
        identifier = uid('scene_selection')
        receipt = {'schema_version': 'scene-collection-selection/1', 'id': identifier, 'index_id': index_id,
            'created_at': utcnow(), 'search': result, 'search_hash': digest(result), 'selected_keys': body.selected_keys,
            'not_selected_keys': [r['key'] for r in result['rows'] if r['key'] not in body.selected_keys],
            'purpose': body.purpose, 'labels_copied': False, 'conditions_verified': False,
            'collection': {'id': target['id'], 'revision': 1, 'manifest_hash': target['manifest_hash']}}
        session.add(SceneSelectionRow(id=identifier, index_id=index_id, collection_id=target['id'],
            request_key=storage, request_hash=request_hash, **collections._seal(receipt))); session.flush()
        return selection_get(session, identifier)


def _search_descriptor(data):
    raw = canonical_bytes(data)
    if len(raw) > collections.MAX_MANIFEST:
        raise HTTPException(413, 'Full scene search exceeds the 8 MiB report limit; no rows were dropped')
    query = urlencode({'query': canonical_bytes(data['query']).decode(), 'expected_hash': digest(data)})
    return {**data, 'manifest_hash': digest(data), 'manifest_size_bytes': len(raw),
        'download_url': f'/api/v1/scene-indexes/{data["index_id"]}/search/export?{query}'}


def register_scene_routes(api, db):
    @api.get('/scene-discovery-capabilities', tags=['scene discovery'])
    def capabilities():
        return {'recipe': scene_features.recipe(), 'limits': {'messages':256, 'pixels':MAX_TOTAL_PIXELS, 'input_bytes':MAX_INPUT_BYTES, 'selected_for_collection':32},
                'limitations': LIMITATIONS}

    @api.post('/scene-indexes/preview', tags=['scene discovery'])
    def preview(body: SceneIndexSelection):
        with db.read_session() as session:
            data = proposal(session, body)
            return {**data, 'preview_hash': digest(data), 'computed_images': 0, 'writes_created': 0}

    @api.post('/scene-indexes', status_code=201, tags=['scene discovery'])
    def create(body: SceneIndexCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return create_index(db, body, idempotency_key)

    @api.get('/scene-indexes', tags=['scene discovery'])
    def listing(collection_id: Identity | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        with db.read_session() as session:
            query = select(SceneIndexRow).order_by(SceneIndexRow.created_at.desc(), SceneIndexRow.id)
            if collection_id: query = query.where(SceneIndexRow.collection_id==collection_id)
            rows = list(session.scalars(query.limit(limit+1).offset(offset)))
            items = []
            for row in rows[:limit]:
                d = index_get(session, row.id)
                items.append({'id': row.id, 'name': d['proposal']['request']['name'], 'collection': d['proposal']['collection'], 'counts': d['counts']})
            return {'items': items, 'next_offset': offset+limit if len(rows)>limit else None}

    @api.get('/scene-indexes/{index_id}', tags=['scene discovery'])
    def read(index_id: Identity):
        with db.read_session() as session: return index_get(session, index_id)

    @api.get('/scene-indexes/{index_id}/export', tags=['scene discovery'])
    def export(index_id: Identity):
        with db.read_session() as session:
            index_get(session,index_id)
            return collections._download(session.get(SceneIndexRow,index_id).manifest,index_id)

    @api.get('/scene-indexes/{index_id}/reviews', tags=['scene discovery'])
    def reviews(index_id: Identity, revision: int | None = Query(None,ge=0,le=2**31-1)):
        with db.read_session() as session: return review_get(session,index_get(session,index_id),revision)

    @api.post('/scene-indexes/{index_id}/reviews', status_code=201, tags=['scene discovery'])
    def review(index_id: Identity, body: SceneTagReview, idempotency_key: str = Header(min_length=1,max_length=180)):
        return save_review(db,index_id,body,idempotency_key)

    @api.post('/scene-indexes/{index_id}/search', tags=['scene discovery'])
    def query(index_id: Identity, body: SceneQuery):
        with db.read_session() as session: return _search_descriptor(search(session,index_id,body))

    @api.get('/scene-indexes/{index_id}/search/export', tags=['scene discovery'])
    def export_search(index_id: Identity, query: str = Query(...,max_length=2500), expected_hash: str = Query(...,pattern=r'^[a-f0-9]{64}$')):
        try: body = SceneQuery.model_validate_json(query)
        except ValueError as error: raise HTTPException(422,'Invalid bounded scene search query') from error
        if body.review_revision is None: raise HTTPException(422,'Pin a review revision for export')
        with db.read_session() as session:
            data = search(session,index_id,body)
            if digest(data)!=expected_hash: raise HTTPException(409,'Search export differs from the inspected snapshot')
            return collections._download(canonical_bytes(data),index_id+'-search')

    @api.post('/scene-indexes/{index_id}/collections', status_code=201, tags=['scene discovery'])
    def collect(index_id: Identity, body: SceneCollectionCreate, idempotency_key: str = Header(min_length=1,max_length=180)):
        return save_selection(db,index_id,body,idempotency_key)

    @api.get('/scene-selections', tags=['scene discovery'])
    def selection_list(collection_id: Identity):
        with db.read_session() as session:
            ids = list(session.scalars(select(SceneSelectionRow.id).where(SceneSelectionRow.collection_id==collection_id).limit(101)))
            if len(ids)>100: raise HTTPException(413,'Too many selection receipts')
            return {'items':[selection_get(session,i) for i in ids]}

    @api.get('/scene-selections/{selection_id}', tags=['scene discovery'])
    def selection(selection_id: Identity):
        with db.read_session() as session: return selection_get(session,selection_id)

    @api.get('/scene-selections/{selection_id}/export', tags=['scene discovery'])
    def selection_export(selection_id: Identity):
        with db.read_session() as session:
            selection_get(session,selection_id)
            return collections._download(session.get(SceneSelectionRow,selection_id).manifest,selection_id)
