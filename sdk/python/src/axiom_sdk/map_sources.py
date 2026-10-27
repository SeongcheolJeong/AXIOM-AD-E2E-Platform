"""External map source bytes and pinned conversion/native qualification workflow."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from .experiments import _object_map_identity, _object_map_revision
from .collections import _key


def _pin(revision: int, sha256: str) -> dict:
    _object_map_revision(revision)
    if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
        raise ValueError('A pinned lowercase SHA-256 is required')
    return {'revision': revision, 'sha256': sha256}


def _conversion(reference: dict) -> dict:
    if not isinstance(reference, dict) or set(reference) != {'id', 'revision', 'sha256'}:
        raise ValueError('conversion_ref requires id, revision and sha256')
    return {'id': _object_map_identity(reference['id']), **_pin(reference['revision'], reference['sha256'])}


class MapSourcesClientMixin:
    def map_source_capabilities(self, *, project_id: str = 'default'):
        return self._request('GET', '/map-sources/capabilities', params={
            'project_id': _object_map_identity(project_id)}).json()

    def map_sources(self, *, project_id: str = 'default', limit: int = 100, offset: int = 0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError('Pagination requires limit 1..1000 and offset >= 0')
        return self._request('GET', '/map-sources', params={
            'project_id': _object_map_identity(project_id), 'limit': limit, 'offset': offset}).json()

    def map_source(self, source_id: str, *, project_id: str = 'default', revision: int | None = None):
        params = {'project_id': _object_map_identity(project_id)}
        if revision is not None:
            _object_map_revision(revision)
            params['revision'] = revision
        return self._request('GET', f'/map-sources/{_object_map_identity(source_id)}', params=params).json()

    def create_map_source(self, *, name: str, source_kind: str, filename: str,
                          content: bytes, rights: dict, project_id: str = 'default',
                          source_id: str | None = None, expected_revision: int = 0,
                          metadata: dict | None = None, attachments: list[dict] | None = None):
        """Upload original bytes, without newline, encoding or GIS normalization.

        Optional attachments contain filename, role and content (bytes), preserving
        upstream sources such as the OSM input of a derived OpenDRIVE document.
        This saves a source only; it does not create a native world.
        """
        if source_kind not in {'opendrive', 'ngii_shp'}:
            raise ValueError('source_kind must be opendrive or ngii_shp')
        if not isinstance(content, bytes) or not 0 < len(content) <= 32 * 1024**2:
            raise ValueError('content must be original bytes, at most 32 MiB')
        if not isinstance(name, str) or not name.strip() or not isinstance(filename, str) or not filename.strip():
            raise ValueError('name and filename are required')
        _object_map_revision(expected_revision, minimum=0)
        body = {'project_id': _object_map_identity(project_id), 'expected_revision': expected_revision,
                'name': name, 'source_kind': source_kind, 'filename': filename,
                'content_base64': base64.b64encode(content).decode('ascii'), 'rights': rights,
                'metadata': {} if metadata is None else metadata}
        if source_id is not None:
            body['id'] = _object_map_identity(source_id)
        if attachments is not None:
            body['attachments'] = []
            for attachment in attachments:
                if set(attachment) != {'filename', 'role', 'content'} or not isinstance(attachment['content'], bytes):
                    raise ValueError('An attachment requires filename, role and original content bytes')
                body['attachments'].append({'filename': attachment['filename'], 'role': attachment['role'],
                    'content_base64': base64.b64encode(attachment['content']).decode('ascii')})
        return self._request('POST', '/map-sources', json=body).json()

    def upload_map_source(self, path: str | Path, *, name: str, source_kind: str, rights: dict, **kwargs):
        """Read a caller-selected local file as bytes, then register it."""
        path = Path(path)
        if path.stat().st_size > 32 * 1024**2:
            raise ValueError('Source exceeds the 32 MiB upload limit')
        return self.create_map_source(name=name, source_kind=source_kind, filename=path.name,
                                      content=path.read_bytes(), rights=rights, **kwargs)

    def convert_map_source(self, source_id: str, *, revision: int, sha256: str,
                           options: dict | None = None, project_id: str = 'default'):
        return self._request('POST', f'/map-sources/{_object_map_identity(source_id)}/convert', json={
            'project_id': _object_map_identity(project_id), **_pin(revision, sha256),
            'options': {} if options is None else options}).json()

    def qualify_map_source(self, source_id: str, *, revision: int, sha256: str,
                           conversion_ref: dict, idempotency_key: str, project_id: str = 'default',
                           server_version: str = '1ae5356'):
        """Queue a native road-generation check; no driving validation is implied."""
        return self._request('POST', f'/map-sources/{_object_map_identity(source_id)}/qualify', json={
            'project_id': _object_map_identity(project_id), **_pin(revision, sha256),
            'conversion_ref': _conversion(conversion_ref), 'server_version': server_version},
            headers=_key(idempotency_key)).json()

    def publish_map_source(self, source_id: str, *, revision: int, sha256: str,
                           conversion_ref: dict, execution_id: str, name: str | None = None,
                           project_id: str = 'default'):
        body = {'project_id': _object_map_identity(project_id), **_pin(revision, sha256),
                'conversion_ref': _conversion(conversion_ref),
                'execution_id': _object_map_identity(execution_id)}
        if name is not None:
            body['name'] = name
        return self._request('POST', f'/map-sources/{_object_map_identity(source_id)}/publish', json=body).json()

    def export_map_source(self, source_id: str, *, revision: int | None = None,
                          project_id: str = 'default', attachment: int | None = None) -> bytes:
        """Return verified original bytes, including ZIP and non-UTF-8 input."""
        params = {'project_id': _object_map_identity(project_id)}
        if revision is not None:
            _object_map_revision(revision)
            params['revision'] = revision
        if attachment is not None:
            if type(attachment) is not int or attachment < 0:
                raise ValueError('attachment must be a nonnegative index')
            params['attachment'] = attachment
        response = self._request('GET', f'/map-sources/{_object_map_identity(source_id)}/export', params=params)
        expected = response.headers.get('X-Content-SHA256')
        if not expected or hashlib.sha256(response.content).hexdigest() != expected:
            raise ValueError('Map source export hash mismatch')
        return response.content
