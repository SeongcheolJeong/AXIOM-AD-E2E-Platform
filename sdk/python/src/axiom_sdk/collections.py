"""Fixed multi-source collections and explicit, non-executing dataset publication."""
from pathlib import Path
from typing import Any

from .perception import _identity


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**31-1:
        raise ValueError('Revision must be a positive integer')


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
    return {'Idempotency-Key': value}


class CollectionClientMixin:
    def collections(self, *, q: str = '', limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not isinstance(q, str) or len(q) > 300 or type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Invalid collection search bounds')
        return self._request('GET', '/collections', params={'q':q,'limit':limit,'offset':offset}).json()

    def preview_collection(self, **definition: Any) -> dict[str, Any]:
        return self._request('POST', '/collections/preview', json=definition).json()

    def create_collection(self, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        return self._request('POST', '/collections', json=definition, headers=_key(idempotency_key)).json()

    def revise_collection(self, collection_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        _revision(definition.get('expected_revision'))
        return self._request('POST', f'/collections/{_identity(collection_id)}/revisions', json=definition, headers=_key(idempotency_key)).json()

    def get_collection(self, collection_id: str, *, revision: int | None = None) -> dict[str, Any]:
        if revision is not None: _revision(revision)
        value = self._request('GET', f'/collections/{_identity(collection_id)}', params={'revision':revision} if revision is not None else {}).json()
        if value.get('id') != collection_id or revision is not None and value.get('revision') != revision:
            raise ValueError('Collection response identity mismatch')
        return value

    def preview_collection_dataset(self, collection_id: str, **definition: Any) -> dict[str, Any]:
        _revision(definition.get('collection_revision'))
        return self._request('POST', f'/collections/{_identity(collection_id)}/datasets/preview', json=definition).json()

    def publish_collection_dataset(self, collection_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        _revision(definition.get('collection_revision'))
        return self._request('POST', f'/collections/{_identity(collection_id)}/datasets', json=definition, headers=_key(idempotency_key)).json()

    def collection_dataset_publications(self, *, collection_id: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
        if collection_id is None and dataset_id is None: raise ValueError('Scope publications to a collection or dataset')
        params = {key:_identity(value) for key,value in (('collection_id',collection_id),('dataset_id',dataset_id)) if value is not None}
        return self._request('GET', '/collection-datasets', params=params).json()

    def export_collection(self, collection_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        value = self.get_collection(collection_id, revision=revision)
        return self._download_perception(f'/collections/{_identity(collection_id)}/export', destination,
            params={'revision':value['revision']}, sha256=value['manifest_hash'], size_bytes=value['manifest_size_bytes'], maximum=8*1024*1024)

    def export_collection_dataset_receipt(self, publication_id: str, destination: str | Path) -> Path:
        path = f'/collection-datasets/{_identity(publication_id)}'
        value = self._request('GET', path).json()
        if value.get('id') != publication_id: raise ValueError('Publication response identity mismatch')
        return self._download_perception(path+'/export', destination, sha256=value['manifest_hash'],
            size_bytes=value['manifest_size_bytes'], maximum=8*1024*1024)
