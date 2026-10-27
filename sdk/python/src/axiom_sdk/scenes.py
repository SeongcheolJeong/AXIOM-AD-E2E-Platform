"""Bounded local appearance indexing, declared reviews and exact search export."""
import json
from pathlib import Path
from typing import Any

from .perception import _identity
from .collections import _key, _revision


class SceneClientMixin:
    def scene_capabilities(self) -> dict[str, Any]:
        return self._request('GET','/scene-discovery-capabilities').json()

    def preview_scene_index(self, **selection: Any) -> dict[str, Any]:
        _identity(selection.get('collection_id')); _revision(selection.get('collection_revision'))
        return self._request('POST','/scene-indexes/preview',json=selection).json()

    def create_scene_index(self, *, idempotency_key: str, **selection: Any) -> dict[str, Any]:
        _identity(selection.get('collection_id')); _revision(selection.get('collection_revision'))
        if selection.get('confirm_local_analysis') is not True:
            raise ValueError('Explicit bounded local image analysis consent is required')
        return self._request('POST','/scene-indexes',json=selection,headers=_key(idempotency_key)).json()

    def scene_index(self, index_id: str) -> dict[str, Any]:
        value=self._request('GET',f'/scene-indexes/{_identity(index_id)}').json()
        if value.get('id')!=index_id: raise ValueError('Scene index response identity differs')
        return value

    def search_scenes(self, index_id: str, **query: Any) -> dict[str, Any]:
        value=self._request('POST',f'/scene-indexes/{_identity(index_id)}/search',json=query).json()
        if value.get('index_id')!=index_id: raise ValueError('Search response identity differs')
        return value

    def review_scene_tags(self, index_id: str, *, idempotency_key: str, **declaration: Any) -> dict[str, Any]:
        revision=declaration.get('expected_review_revision')
        if type(revision) is not int or not 0<=revision<=2**31-2:
            raise ValueError('Use the nonnegative observed review revision')
        return self._request('POST',f'/scene-indexes/{_identity(index_id)}/reviews',json=declaration,headers=_key(idempotency_key)).json()

    def create_scene_collection(self, index_id: str, *, idempotency_key: str, **selection: Any) -> dict[str, Any]:
        return self._request('POST',f'/scene-indexes/{_identity(index_id)}/collections',json=selection,headers=_key(idempotency_key)).json()

    def export_scene_index(self, index_id: str, destination: str | Path) -> Path:
        value=self.scene_index(index_id)
        return self._download_perception(f'/scene-indexes/{_identity(index_id)}/export',destination,
            sha256=value['manifest_hash'],size_bytes=value['manifest_size_bytes'],maximum=8*1024*1024)

    def export_scene_search(self, index_id: str, destination: str | Path, **query: Any) -> Path:
        value=self.search_scenes(index_id,**query)
        return self._download_perception(f'/scene-indexes/{_identity(index_id)}/search/export',destination,
            params={'query':json.dumps(value['query'],sort_keys=True,separators=(',',':')),'expected_hash':value['manifest_hash']},
            sha256=value['manifest_hash'],size_bytes=value['manifest_size_bytes'],maximum=8*1024*1024)

    def export_scene_selection(self, selection_id: str, destination: str | Path) -> Path:
        path=f'/scene-selections/{_identity(selection_id)}'
        value=self._request('GET',path).json()
        if value.get('id')!=selection_id: raise ValueError('Scene selection identity differs')
        return self._download_perception(path+'/export',destination,
            sha256=value['manifest_hash'],size_bytes=value['manifest_size_bytes'],maximum=8*1024*1024)
