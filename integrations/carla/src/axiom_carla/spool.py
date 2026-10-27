"""Private, bounded durable worker attempts with OS ownership fencing.

SQLite FULL commits retain request identity, native intent/observation journal,
and the latest complete checkpoint. Final archives are sealed before responding.
An abandoned OS lock proves only the worker stopped, never engine cleanup.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from .version import ADAPTER_VERSION, require_durable_version
from .wire import (WIRE_BYTES_LIMIT, ARCHIVE_SCHEMA, ARTIFACT_BYTES_LIMIT,
                   ARTIFACT_TOTAL_LIMIT, ARTIFACT_COUNT_LIMIT, encode_failure,
                   decode_failure, parse_json)

JOURNAL_BYTES_LIMIT = 40 * 1024 * 1024
JOURNAL_EVENTS_LIMIT = 12000
STORE_BYTES_LIMIT = 4 * 1024**3
ATTEMPTS_LIMIT = 1000
CHECKPOINT_MAGIC = b'AXIOMCHK2\n'
CHECKPOINT_REFS_SCHEMA = 'carla-checkpoint-refs/1'
CHECKPOINT_REFS_LIMIT = 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def identity(request):
    value = request.model_dump(mode='json') if hasattr(request, 'model_dump') else request
    return {'execution_id': value['execution_id'], 'input_hash': value['input_hash'],
            'context_hash': value['context']['context_hash'] if value.get('context') else None,
            'request_hash': sha(canonical(value)), 'worker_adapter_version': value['adapter_version']}


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StoreConflict(ValueError):
    pass


class StoreUnavailable(ValueError):
    pass


def _checkpoint_header(meta, phase):
    from ade2e.execution_attempt import ExecutionAttemptRequest
    if not isinstance(phase, str):
        raise ValueError('Invalid checkpoint phase')
    request = meta['request']
    pinned = ExecutionAttemptRequest.model_validate(request)
    context = pinned.simulation_request() if pinned.context is not None else None
    version = require_durable_version(request['adapter_version'])
    if meta['worker_adapter_version'] != version:
        raise StoreUnavailable('Checkpoint worker version differs from its original request')
    header = encode_failure('Execution interrupted; retained committed checkpoint at ' + phase,
                            request=context, adapter_version=version)
    del header['artifact_payloads']
    return header, context


def _checkpoint_refs(payloads, header):
    """Bound the exact public wire size without repeatedly encoding blob bytes."""
    from ade2e.schemas import ArtifactPayload
    if not isinstance(payloads, (list, tuple)) or len(payloads) > ARTIFACT_COUNT_LIMIT:
        raise ValueError('Too many worker artifacts')
    refs, blobs, names, total = [], {}, set(), 0
    for payload in payloads:
        artifact = ArtifactPayload.model_validate(payload.model_dump() if isinstance(payload, ArtifactPayload) else payload)
        content = artifact.content
        total += len(content)
        if len(content) > ARTIFACT_BYTES_LIMIT or total > ARTIFACT_TOTAL_LIMIT:
            raise ValueError('Worker artifacts exceed their total byte bound')
        if artifact.name in names:
            raise ValueError('Checkpoint artifact names must be unique')
        names.add(artifact.name)
        digest = sha(content)
        if digest in blobs and blobs[digest] != content:
            raise StoreUnavailable('Checkpoint content hash collision')
        blobs[digest] = content
        refs.append({'name': artifact.name, 'kind': artifact.kind, 'media_type': artifact.media_type,
                     'size_bytes': len(content), 'sha256': digest})
    skeleton = {**header, 'artifact_payloads': [
        {key: ref[key] for key in ('name', 'kind', 'media_type')} | {'content_base64': ''} for ref in refs]}
    # Base64 uses only unescaped JSON characters. This is exactly the canonical
    # public failure length, including UTF-8 metadata and correlation fields.
    wire_size = len(canonical(skeleton)) + sum(4 * ((ref['size_bytes'] + 2) // 3) for ref in refs)
    if wire_size > WIRE_BYTES_LIMIT:
        raise StoreUnavailable('Checkpoint exceeds wire capacity')
    encoded = CHECKPOINT_MAGIC + canonical({'schema_version': CHECKPOINT_REFS_SCHEMA,
        'envelope': header, 'artifacts': refs, 'wire_size_bytes': wire_size})
    if len(encoded) > CHECKPOINT_REFS_LIMIT:
        raise StoreUnavailable('Checkpoint descriptor exceeds its bound')
    return encoded, blobs


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _blob_index(db):
    rows = db.execute('SELECT sha256,size_bytes,length(content) FROM checkpoint_blobs LIMIT ?',
                      (ARTIFACT_COUNT_LIMIT + 1,)).fetchall()
    if len(rows) > ARTIFACT_COUNT_LIMIT:
        raise StoreUnavailable('Checkpoint blob count exceeds its bound')
    result, total = {}, 0
    for digest, size, stored_size in rows:
        if (not _digest(digest) or digest in result or type(size) is not int
                or not 0 <= size <= ARTIFACT_BYTES_LIMIT or size != stored_size):
            raise StoreUnavailable('Checkpoint blob metadata mismatch')
        result[digest] = size
        total += size
    if total > ARTIFACT_TOTAL_LIMIT:
        raise StoreUnavailable('Checkpoint blob total exceeds its bound')
    return result


def _load_checkpoint(db, meta):
    """Verify both private formats, materializing original wire only on recovery."""
    from ade2e.schemas import ArtifactPayload
    length = db.execute('SELECT length(content) FROM checkpoint WHERE id=1').fetchone()
    if length is None:
        return None
    if type(length[0]) is not int or not 0 < length[0] <= WIRE_BYTES_LIMIT:
        raise StoreUnavailable('Checkpoint exceeds wire capacity')
    encoded, digest, phase = db.execute('SELECT content,sha256,phase FROM checkpoint WHERE id=1').fetchone()
    if (not isinstance(encoded, bytes) or sha(encoded) != digest or phase != meta.get('checkpoint_phase')):
        raise StoreUnavailable('Committed checkpoint hash or phase mismatch')
    header, context = _checkpoint_header(meta, phase)
    try:
        if not encoded.startswith(CHECKPOINT_MAGIC):
            # Old checkpoints remain readable. An unknown binary format is not
            # JSON and fails here, before any archive can be sealed.
            payload = parse_json(encoded)
            detail, artifacts = decode_failure(payload, request=context, adapter_version=header['worker_adapter_version'])
            if ({key: value for key, value in payload.items() if key != 'artifact_payloads'} != header
                    or payload != encode_failure(detail, artifacts, request=context, adapter_version=header['worker_adapter_version'])):
                raise ValueError('Legacy checkpoint envelope mismatch')
            return payload
        if len(encoded) > CHECKPOINT_REFS_LIMIT:
            raise ValueError('Checkpoint descriptor exceeds its bound')
        value = parse_json(encoded[len(CHECKPOINT_MAGIC):])
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'envelope', 'artifacts', 'wire_size_bytes'}
                or value['schema_version'] != CHECKPOINT_REFS_SCHEMA or value['envelope'] != header
                or type(value['wire_size_bytes']) is not int or not 0 < value['wire_size_bytes'] <= WIRE_BYTES_LIMIT
                or not isinstance(value['artifacts'], list) or len(value['artifacts']) > ARTIFACT_COUNT_LIMIT):
            raise ValueError('Invalid checkpoint descriptor')
        refs, names, total = value['artifacts'], set(), 0
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {'name', 'kind', 'media_type', 'size_bytes', 'sha256'}
                    or type(ref['size_bytes']) is not int or not 0 <= ref['size_bytes'] <= ARTIFACT_BYTES_LIMIT
                    or not _digest(ref['sha256'])):
                raise ValueError('Invalid checkpoint artifact reference')
            fields = {key: ref[key] for key in ('name', 'kind', 'media_type')}
            parsed = ArtifactPayload.model_validate(fields | {'content': b''})
            if fields != parsed.model_dump(exclude={'content'}) or parsed.name in names:
                raise ValueError('Invalid or duplicate checkpoint artifact metadata')
            names.add(parsed.name)
            total += ref['size_bytes']
        if total > ARTIFACT_TOTAL_LIMIT:
            raise ValueError('Checkpoint artifacts exceed their total byte bound')
        index = _blob_index(db)
        if set(index) != {ref['sha256'] for ref in refs}:
            raise ValueError('Checkpoint blob references differ from stored content')
        blobs = {}
        for digest, size in index.items():
            content = db.execute('SELECT content FROM checkpoint_blobs WHERE sha256=?', (digest,)).fetchone()[0]
            if not isinstance(content, bytes) or len(content) != size or sha(content) != digest:
                raise ValueError('Checkpoint blob content hash mismatch')
            blobs[digest] = content
        artifacts = []
        for ref in refs:
            if len(blobs[ref['sha256']]) != ref['size_bytes']:
                raise ValueError('Checkpoint reference size mismatch')
            artifacts.append(ArtifactPayload(**{key: ref[key] for key in ('name', 'kind', 'media_type')},
                                             content=blobs[ref['sha256']]))
        rebuilt, _ = _checkpoint_refs(artifacts, header)
        if rebuilt != encoded:
            raise ValueError('Checkpoint descriptor is not canonical or has an incorrect wire bound')
        payload = encode_failure(header['detail'], artifacts, request=context, adapter_version=header['worker_adapter_version'])
        if len(canonical(payload)) != value['wire_size_bytes']:
            raise ValueError('Checkpoint wire length mismatch')
        return payload
    except (ValueError, TypeError, KeyError, sqlite3.DatabaseError) as error:
        raise StoreUnavailable('Committed checkpoint integrity mismatch: ' + str(error)) from error


@contextmanager
def file_lock(path):
    if path.is_symlink():
        raise StoreUnavailable('Durable lock cannot be a symlink')
    stream = path.open('a+b')
    os.chmod(path, 0o600)
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StoreConflict('An execution owner still holds the worker lock') from error
        yield
    finally:
        stream.close()


class Attempt:
    def __init__(self, store, path):
        self.store, self.path = store, path
        if path.is_symlink() or (path / 'attempt.db').is_symlink():
            raise StoreUnavailable('Durable attempt cannot be a symlink')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path / 'attempt.db', timeout=5)
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def meta(self):
        with self.db() as db:
            return json.loads(db.execute('SELECT value FROM meta WHERE id=1').fetchone()[0])

    def verified_meta(self):
        # Inspection/recovery performs this full check; the hot native journal
        # path keeps its original bounded transaction cost.
        from ade2e.execution_attempt import ExecutionAttemptRequest
        from ade2e.simulation_context import validate_simulation_adapter_context
        meta = self.meta()
        try:
            request = ExecutionAttemptRequest.model_validate(meta['request'])
            require_durable_version(request.adapter_version)
            if request.context:
                validate_simulation_adapter_context(request.context, request.adapter_version)
            expected = identity(request)
            if (request.adapter_id != 'carla' or meta.get('store_id') != self.store.id
                    or any(meta.get(key) != value for key, value in expected.items())):
                raise ValueError('Stored attempt identity differs from its original request')
        except (ValueError, TypeError, KeyError) as error:
            raise StoreUnavailable('Stored attempt identity/version could not be verified') from error
        return meta

    def _write_meta(self, db, value):
        db.execute('UPDATE meta SET value=? WHERE id=1', (canonical(value),))

    def role_name(self, role):
        if not isinstance(role, str) or len(role) > 50:
            raise ValueError('Invalid native actor role')
        return 'axiom_' + self.path.name[:24] + '_' + role

    def event(self, kind, data, *, content=None):
        if not isinstance(kind, str) or not 1 <= len(kind) <= 100 or not isinstance(data, dict):
            raise ValueError('Invalid durable event')
        encoded = canonical(data)
        if len(encoded) > 128*1024 or (content is not None and (not isinstance(content, bytes) or len(content) > 2*1024*1024)):
            raise ValueError('Durable observation exceeds its bound')
        content = content or b''
        with self.db() as db:
            meta = json.loads(db.execute('SELECT value FROM meta WHERE id=1').fetchone()[0])
            if meta['state'] != 'running':
                raise StoreConflict('Sealed attempt cannot accept native events')
            seq = meta['journal_count']
            size = meta['journal_bytes'] + len(content) + len(encoded)
            if seq >= JOURNAL_EVENTS_LIMIT or size > JOURNAL_BYTES_LIMIT:
                raise StoreUnavailable('Native journal capacity exhausted before next mutation')
            at = now()
            row = {'sequence': seq, 'at': at, 'kind': kind, 'data': data,
                   'blob_sha256': sha(content), 'blob_size_bytes': len(content), 'previous_sha256': meta['journal_sha256']}
            digest = sha(canonical(row))
            db.execute('INSERT INTO journal VALUES(?,?,?,?,?)', (seq, canonical(row), content, digest, len(content)+len(encoded)))
            meta.update(updated_at=at, phase=kind, journal_count=seq+1, journal_bytes=size, journal_sha256=digest)
            if kind == 'world_mutation_intent':
                meta.update(mutations_started=True, cleanup_status='unknown')
            if kind == 'cleanup_finished':
                meta['cleanup_status'] = 'failed' if data.get('errors') else 'confirmed'
            # Native code can report ACKs, but no journal entry releases the
            # engine reservation. The sealed outcome and operator reconciliation
            # are separate state transitions.
            self._write_meta(db, meta)

    def checkpoint(self, payloads, phase):
        meta = self.meta()
        header, _ = _checkpoint_header(meta, phase)
        encoded, blobs = _checkpoint_refs(payloads, header)
        with self.db() as db:
            # DDL is lazy for pre-existing attempts, but belongs to the same
            # rollback boundary as the replacement checkpoint and its blobs.
            db.execute('BEGIN IMMEDIATE')
            meta = json.loads(db.execute('SELECT value FROM meta WHERE id=1').fetchone()[0])
            if meta['state'] != 'running':
                raise StoreConflict('Sealed attempt cannot accept checkpoints')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoint_blobs '
                       '(sha256 TEXT PRIMARY KEY,size_bytes INTEGER NOT NULL,content BLOB NOT NULL)')
            index = _blob_index(db)
            for digest, content in blobs.items():
                if digest in index:
                    stored = db.execute('SELECT content FROM checkpoint_blobs WHERE sha256=?', (digest,)).fetchone()[0]
                    if (not isinstance(stored, bytes) or index[digest] != len(content)
                            or sha(stored) != digest or stored != content):
                        raise StoreUnavailable('Existing checkpoint blob hash mismatch')
                else:
                    db.execute('INSERT INTO checkpoint_blobs VALUES(?,?,?)', (digest, len(content), content))
            db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)', (encoded, sha(encoded), phase))
            if blobs:
                db.execute('DELETE FROM checkpoint_blobs WHERE sha256 NOT IN (' + ','.join('?' for _ in blobs) + ')', tuple(blobs))
            else:
                db.execute('DELETE FROM checkpoint_blobs')
            meta.update(updated_at=now(), checkpoint_phase=phase, checkpoint_journal_count=meta['journal_count'])
            self._write_meta(db, meta)

    def seal(self, outcome, payload, *, cleanup_status=None, execution_ms=None):
        if outcome not in {'completed', 'failed', 'interrupted'}:
            raise ValueError('Invalid terminal outcome')
        with self.db() as db:
            meta = json.loads(db.execute('SELECT value FROM meta WHERE id=1').fetchone()[0])
            if meta['state'] != 'running':
                return
            version = require_durable_version(meta['request']['adapter_version'])
            if meta['worker_adapter_version'] != version or not isinstance(payload, dict) or payload.get('worker_adapter_version') != version:
                raise StoreUnavailable('Archive payload version differs from its original execution')
            archive = {'schema_version': ARCHIVE_SCHEMA, 'store_id': self.store.id,
                       **{key: meta[key] for key in identity(meta['request'])}, 'outcome': outcome, 'payload': payload, 'execution_ms': execution_ms}
            encoded = canonical(archive)
            if len(encoded) > WIRE_BYTES_LIMIT:
                raise StoreUnavailable('Sealed archive exceeds wire capacity')
            db.execute('INSERT OR REPLACE INTO archive VALUES(1,?,?)', (encoded, sha(encoded)))
            db.execute('DELETE FROM checkpoint')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoint_blobs'").fetchone():
                db.execute('DELETE FROM checkpoint_blobs')
            meta.update(state=outcome, updated_at=now(), archive_sha256=sha(encoded), archive_size_bytes=len(encoded),
                        cleanup_status=cleanup_status or meta['cleanup_status'])
            self._write_meta(db, meta)

    def recover_abandoned(self):
        meta = self.verified_meta()
        if meta['state'] != 'running':
            return
        try:
            with file_lock(self.path / 'owner.lock'):
                # Re-read under the owner fence. An active/retried dispatcher
                # can never be mistaken for a stopped worker by a stale PID.
                meta = self.verified_meta()
                if meta['state'] != 'running':
                    return
                with self.db() as db:
                    payload = _load_checkpoint(db, meta)
                if payload is None:
                    request = meta['request']
                    from ade2e.execution_attempt import ExecutionAttemptRequest
                    from .wire import initial_sensor_configuration_payload
                    pinned = ExecutionAttemptRequest.model_validate(request)
                    context = pinned.simulation_request() if pinned.context is not None else None
                    payloads = ()
                    if context is not None and context.context.schema_version == 'simulation-control-context/2':
                        if meta['mutations_started']:
                            raise StoreUnavailable('Context/2 native mutation lacks the required initial evidence checkpoint')
                        payloads = [initial_sensor_configuration_payload(context)]
                    payload = encode_failure('Worker owner stopped before a complete checkpoint. Native journal may retain earlier intent; cleanup is unknown.',
                        payloads, request=context, adapter_version=request['adapter_version'])
                self.seal('interrupted', payload)
        except StoreConflict:
            return

    def archive(self):
        self.verified_meta()
        with self.db() as db:
            row = db.execute('SELECT content,sha256 FROM archive WHERE id=1').fetchone()
        if row is None:
            raise StoreConflict('Execution has no sealed archive yet')
        if sha(row[0]) != row[1]:
            raise StoreUnavailable('Archive hash mismatch')
        return bytes(row[0])

    def journal(self, *, include_blobs=False):
        self.verified_meta()
        # This is a diagnostic export, never a reconstructed trajectory or a
        # resume program. Verify every committed blob and hash-chain entry.
        with self.db() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT sequence,event,blob,sha256 FROM journal ORDER BY sequence').fetchall()
            meta = json.loads(db.execute('SELECT value FROM meta WHERE id=1').fetchone()[0])
        entries, previous = [], '0'*64
        for index, (sequence, encoded, content, digest) in enumerate(rows):
            event = json.loads(encoded)
            if (sequence != index or event['sequence'] != index or event['previous_sha256'] != previous
                    or sha(canonical(event)) != digest or sha(content) != event['blob_sha256'] or len(content) != event['blob_size_bytes']):
                raise StoreUnavailable('Native journal integrity mismatch')
            entries.append({**event, 'sha256': digest})
            if include_blobs and content:
                import base64
                entries[-1]['blob_base64'] = base64.b64encode(content).decode('ascii')
            previous = digest
        if previous != meta['journal_sha256'] or len(entries) != meta['journal_count']:
            raise StoreUnavailable('Native journal head mismatch')
        return {'schema_version': 'carla-native-journal/1', 'store_id': self.store.id,
                **{key: meta[key] for key in identity(meta['request'])}, 'entries': entries,
                'journal_sha256': previous, 'scope': 'Committed native intent and observations; absent ACKs and cleanup remain unknown.'}


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser()
        if self.root.is_symlink():
            raise StoreUnavailable('Durable store cannot be a symlink')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        with file_lock(self.root / 'identity.lock'):
            identity_path = self.root / 'store-id'
            if identity_path.is_symlink():
                raise StoreUnavailable('Invalid store identity path')
            if not identity_path.exists():
                # Publish only a complete, fsynced identity. A killed first
                # initializer may leave an inert temporary file, never a partial
                # public UUID that blocks every subsequent store inspection.
                fd, temporary_name = tempfile.mkstemp(prefix='.identity-', dir=self.root)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        content = str(uuid.uuid4()).encode('ascii')
                        if stream.write(content) != len(content):
                            raise OSError('Incomplete store identity write')
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Exclusive link cannot rotate/overwrite an existing store.
                    os.link(temporary, identity_path)
                    _fsync_dir(self.root)
                finally:
                    temporary.unlink(missing_ok=True)
            self.id = str(uuid.UUID(identity_path.read_text()))

    def find(self, execution_id):
        if not isinstance(execution_id, str) or not 1 <= len(execution_id) <= 200:
            raise ValueError('Invalid execution identity')
        path = self.root / sha(execution_id.encode('utf-8'))
        if not path.exists():
            return None
        if not path.is_dir() or path.is_symlink() or not (path / 'attempt.db').is_file():
            raise StoreUnavailable('Incomplete durable attempt directory; manual inspection required')
        attempt = Attempt(self, path)
        if attempt.meta()['execution_id'] != execution_id:
            raise StoreUnavailable('Durable identity mismatch')
        return attempt

    def _capacity(self):
        children = list(self.root.iterdir())
        if sum(p.is_dir() for p in children) >= ATTEMPTS_LIMIT:
            raise StoreUnavailable('Durable attempt limit reached; export and manage retention explicitly')
        size = sum(p.stat().st_size for folder in children if folder.is_dir() for p in folder.iterdir() if p.is_file())
        if size >= STORE_BYTES_LIMIT or shutil.disk_usage(self.root).free < 512*1024**2:
            raise StoreUnavailable('Durable store capacity unavailable; no execution started')

    @contextmanager
    def begin(self, request):
        value = request.model_dump(mode='json')
        expected = identity(value)
        with file_lock(self.root / 'engine.lock'):
            existing = self.find(expected['execution_id'])
            if existing:
                if existing.meta()['request_hash'] != expected['request_hash']:
                    raise StoreConflict('Execution ID already belongs to another immutable request')
                existing.recover_abandoned()
                # A duplicate is never permission to simulate again.
                raise StoreConflict('Execution ID already exists; inspect and retrieve its recorded outcome')
            quarantine = self.root / 'quarantine.json'
            if quarantine.exists():
                raise StoreConflict('A prior execution has unresolved engine cleanup; operator reconciliation is required')
            self._capacity()
            path = self.root / sha(expected['execution_id'].encode('utf-8'))
            path.mkdir(mode=0o700)
            attempt = Attempt(self, path)
            with file_lock(path / 'owner.lock'):
                with attempt.db() as db:
                    db.executescript('CREATE TABLE meta(id INTEGER PRIMARY KEY,value BLOB NOT NULL);'
                        'CREATE TABLE journal(sequence INTEGER PRIMARY KEY,event BLOB NOT NULL,blob BLOB NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL);'
                        'CREATE TABLE checkpoint(id INTEGER PRIMARY KEY,content BLOB NOT NULL,sha256 TEXT NOT NULL,phase TEXT NOT NULL);'
                        'CREATE TABLE archive(id INTEGER PRIMARY KEY,content BLOB NOT NULL,sha256 TEXT NOT NULL);')
                    timestamp = now()
                    db.execute('INSERT INTO meta VALUES(1,?)', (canonical({**expected, 'store_id': self.id,
                        'request': value, 'state': 'running', 'created_at': timestamp, 'updated_at': timestamp,
                        'phase': 'accepted', 'checkpoint_phase': None, 'checkpoint_journal_count': 0,
                        'journal_count': 0, 'journal_bytes': 0, 'journal_sha256': '0'*64,
                        'archive_sha256': None, 'archive_size_bytes': None, 'mutations_started': False, 'cleanup_status': 'not_started'}),))
                os.chmod(path / 'attempt.db', 0o600)
                _fsync_dir(path)
                _fsync_dir(self.root)
                # Publish only a complete fsynced reservation. A kill before
                # publication may leave an inert temporary file, never malformed
                # ownership JSON that prevents explicit recovery.
                fd, temporary_name = tempfile.mkstemp(prefix='.reservation-', dir=self.root)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(canonical({'execution_id': expected['execution_id'], 'request_hash': expected['request_hash'], 'store_id': self.id}))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, quarantine)
                    _fsync_dir(self.root)
                finally:
                    temporary.unlink(missing_ok=True)
                try:
                    yield attempt
                finally:
                    # Only a successfully sealed clean outcome releases the
                    # reservation. Process death leaves the durable quarantine.
                    meta = attempt.meta()
                    if meta['state'] in {'completed', 'failed'} and meta['cleanup_status'] in {'confirmed', 'not_started'}:
                        quarantine.unlink()
                        _fsync_dir(self.root)

    def inspect(self, request):
        from ade2e.execution_attempt import RemoteExecutionStatus
        from ade2e.simulation_context import validate_simulation_adapter_context
        require_durable_version(request.adapter_version)
        if request.context:
            validate_simulation_adapter_context(request.context, request.adapter_version)
        if request.adapter_id != 'carla':
            raise StoreUnavailable('Unsupported durable adapter identity')
        attempt = self.find(request.execution_id)
        common = dict(store_id=self.id, execution_id=request.execution_id, request_hash=request.request_hash,
            input_hash=request.input_hash, context_hash=request.context_hash, adapter_id=request.adapter_id,
            adapter_version=request.adapter_version, observed_at=now())
        if attempt is None:
            return RemoteExecutionStatus(**common, state='not_found', mutations_started=False, cleanup_status='not_started',
                error='No record in this store; this does not prove the simulator was never changed.')
        meta = attempt.meta()
        if meta['request_hash'] != request.request_hash:
            raise StoreConflict('Execution ID belongs to a different immutable request')
        attempt.recover_abandoned()
        meta = attempt.meta()
        return RemoteExecutionStatus(**common, state=meta['state'], mutations_started=meta['mutations_started'],
            cleanup_status=meta['cleanup_status'], archive_kind=('complete' if meta['state'] == 'completed' else 'partial') if meta['archive_sha256'] else None,
            archive_sha256=meta['archive_sha256'], archive_size_bytes=meta['archive_size_bytes'],
            error='Worker stopped; only its committed checkpoint is recoverable. No remaining commands were resumed.' if meta['state'] == 'interrupted' else None)
