"""Explicit label-assist execution; drafts are not ground truth."""
from pathlib import Path
from typing import Any
from .perception import _identity
from .collections import _revision, _key


class AnnotationClientMixin:
    def preview_annotation_task(self, dataset_id: str, **selection: Any) -> dict[str, Any]:
        _revision(selection.get('expected_revision'))
        return self._request('POST', f'/datasets/{_identity(dataset_id)}/annotation-tasks/preview', json=selection).json()

    def create_annotation_task(self, dataset_id: str, *, idempotency_key: str, **selection: Any) -> dict[str, Any]:
        _revision(selection.get('expected_revision'))
        if selection.get('confirm_trusted_model') is not True:
            raise ValueError('Explicit trusted-model consent is required')
        return self._request('POST', f'/datasets/{_identity(dataset_id)}/annotation-tasks', json=selection, headers=_key(idempotency_key)).json()

    def annotation_tasks(self, dataset_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Invalid annotation page bounds')
        return self._request('GET', f'/datasets/{_identity(dataset_id)}/annotation-tasks', params={'limit': limit, 'offset': offset}).json()

    def annotation_task(self, task_id: str) -> dict[str, Any]:
        value = self._request('GET', f'/annotation-tasks/{_identity(task_id)}').json()
        if value.get('id') != task_id: raise ValueError('Annotation task response identity differs')
        return value

    def publish_annotation_drafts(self, task_id: str, *, idempotency_key: str, **request: Any) -> dict[str, Any]:
        _revision(request.get('expected_revision'))
        return self._request('POST', f'/annotation-tasks/{_identity(task_id)}/publish', json=request, headers=_key(idempotency_key)).json()

    def annotation_lineage(self, dataset_id: str, *, revision: int | None = None) -> dict[str, Any]:
        if revision is not None: _revision(revision)
        value = self._request('GET', f'/datasets/{_identity(dataset_id)}/annotation-lineage', params={'revision': revision} if revision is not None else {}).json()
        if value.get('dataset',{}).get('id') != dataset_id or revision is not None and value['dataset']['revision'] != revision:
            raise ValueError('Annotation lineage response identity differs')
        return value

    def export_annotation_report(self, task_id: str, destination: str | Path) -> Path:
        value = self.annotation_task(task_id)
        return self._download_perception(f'/annotation-tasks/{_identity(task_id)}/export', destination,
            params={'expected_hash': value['manifest_hash']}, sha256=value['manifest_hash'],
            size_bytes=value['manifest_size_bytes'], maximum=16*1024*1024)
