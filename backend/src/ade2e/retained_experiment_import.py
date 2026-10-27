"""Explicit operator retention of sealed experiment evidence between workspaces.

No API, adapter, worker, migration, project or permission is created here. Only
the selected completed experiment closures are copied. Existing receipts remain
byte-identical; import provenance lives in a separate private operator receipt.
Ordinary failures roll back rows and owned files. Host/process failure between
filesystem publication and SQLite commit is not crash-atomic: orphan files or a
partial closure cause a later attempt to fail closed for operator investigation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import experiment_jobs
from .experiment_models import ExperimentExecutionRow
from .job_models import JobRow
from .service import canonical_bytes, digest, utcnow


class RetainedImportError(ValueError):
    """Bounded validation or publication failed; no new execution was submitted."""


@dataclass(frozen=True)
class RetainedExecutionSelection:
    job_id: str
    manifest_sha256: str

    def __post_init__(self):
        if not re.fullmatch(r'job_[0-9a-f]{32}', self.job_id):
            raise RetainedImportError('Selection requires an exact managed execution ID')
        if not re.fullmatch(r'[0-9a-f]{64}', self.manifest_sha256):
            raise RetainedImportError('Selection requires the original execution receipt SHA-256')


TABLES = ('experiments', 'execution_jobs', 'experiment_executions',
          'experiment_artifacts', 'execution_job_events')
MAX_EXECUTIONS = 16
MAX_BYTES = 8 * 1024 ** 3
_STORAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,179}')


def _workspace(value: Path) -> Path:
    value = Path(value).absolute()
    if value.is_symlink() or not value.is_dir():
        raise RetainedImportError('Workspace must be an existing directory without a symlink')
    root = value.resolve(strict=True)
    for path in (root / 'platform.db', root / 'experiments', root / 'experiments/artifacts'):
        if path.is_symlink():
            raise RetainedImportError('Managed workspace paths must not be symlinks')
    if not (root / 'platform.db').is_file():
        raise RetainedImportError('Existing platform.db is required; initialization is prohibited')
    return root


def _engine(root: Path, *, readonly: bool):
    uri = 'file:' + quote(str(root / 'platform.db'), safe='/') + ('?mode=ro' if readonly else '?mode=rw')

    def connect():
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    return create_engine('sqlite://', creator=connect, poolclass=NullPool)


def _rows(connection, table, where, values):
    return [dict(row) for row in connection.exec_driver_sql(
        f'SELECT * FROM "{table}" WHERE {where}', values).mappings()]


def _one(connection, table, key, value):
    rows = _rows(connection, table, f'"{key}"=?', (value,))
    if len(rows) != 1:
        raise RetainedImportError(f'Selected {table} identity is missing or duplicated')
    return rows[0]


def _schema(source, target):
    source_versions = {row[0] for row in source.exec_driver_sql('SELECT version FROM schema_versions')}
    target_versions = {row[0] for row in target.exec_driver_sql('SELECT version FROM schema_versions')}
    if source_versions not in ({17}, {18}, {19}) or target_versions not in ({18}, {19}):
        raise RetainedImportError('Only an existing schema 17/18/19 source and schema 18/19 target are supported')
    for table in TABLES:
        source_columns = list(source.exec_driver_sql(f'PRAGMA table_info("{table}")'))
        target_columns = list(target.exec_driver_sql(f'PRAGMA table_info("{table}")'))
        source_fks = list(source.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")'))
        target_fks = list(target.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")'))
        if not source_columns or source_columns != target_columns or source_fks != target_fks:
            raise RetainedImportError(f'Selected table contract differs: {table}; no migration is attempted')
    return next(iter(source_versions)), next(iter(target_versions))


def _artifact_path(root, artifact):
    key = artifact['storage_key']
    if not isinstance(key, str) or not _STORAGE.fullmatch(key):
        raise RetainedImportError('Artifact storage key must name a managed file')
    if not isinstance(artifact['size_bytes'], int) or not 0 <= artifact['size_bytes'] <= experiment_jobs.ARTIFACT_LIMIT:
        raise RetainedImportError('Artifact exceeds the supported native file capacity')
    if not re.fullmatch(r'[0-9a-f]{64}', artifact['sha256']):
        raise RetainedImportError('Artifact requires a content SHA-256')
    return root / 'experiments/artifacts' / key


def _read_verified(path, artifact, destination=None):
    """Check both the open inode and path identity; never follow an artifact link."""
    checksum, size = hashlib.sha256(), 0
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode):
                raise RetainedImportError('Artifact must be a regular managed file')
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                if size > artifact['size_bytes']:
                    raise RetainedImportError('Artifact grew beyond its sealed size')
                checksum.update(chunk)
                if destination is not None and destination.write(chunk) != len(chunk):
                    raise RetainedImportError('Artifact staging write was incomplete')
            final = os.fstat(stream.fileno())
        at_path = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(at_path.st_mode) or (initial.st_dev, initial.st_ino) != (at_path.st_dev, at_path.st_ino)
                or initial.st_size != final.st_size or initial.st_mtime_ns != final.st_mtime_ns
                or size != artifact['size_bytes'] or checksum.hexdigest() != artifact['sha256']):
            raise RetainedImportError('Artifact bytes or identity differ from their sealed evidence')
    except OSError as exc:
        raise RetainedImportError('Artifact could not be read without following a symlink') from exc


def _validate_execution(session, identity, pin):
    row = session.scalar(select(JobRow).where(JobRow.id == identity))
    if row is None or row.kind != 'experiment' or row.status != 'completed':
        raise RetainedImportError('Only selected completed experiment executions may be retained')
    if any(getattr(row, key) is not None for key in ('scenario_id', 'scenario_name', 'scenario_revision', 'run_id',
                                                   'owner_token', 'lease_expires_at')):
        raise RetainedImportError('Retained execution has an unsupported subject or active ownership')
    try:
        detail = experiment_jobs.detail(session, row)
    except (HTTPException, ValueError, TypeError, KeyError) as exc:
        raise RetainedImportError('Execution failed the existing immutable input/receipt checks') from exc
    if detail.manifest_hash != pin:
        raise RetainedImportError('Selected execution receipt differs from the explicitly pinned SHA-256')
    return detail


def _snapshot(source, selections, project):
    closure = {table: [] for table in TABLES}
    public = []
    parents = set()
    with Session(bind=source) as session:
        for selection in selections:
            detail = _validate_execution(session, selection.job_id, selection.manifest_sha256)
            if detail.project_id != project:
                raise RetainedImportError('Selection belongs to a different project; project remapping is prohibited')
            job = _one(source, 'execution_jobs', 'id', selection.job_id)
            subject = _one(source, 'experiment_executions', 'job_id', selection.job_id)
            parent = _one(source, 'experiments', 'id', subject['experiment_id'])
            if subject['manifest'] is None or hashlib.sha256(subject['manifest']).hexdigest() != selection.manifest_sha256:
                raise RetainedImportError('Original sealed execution bytes differ from the selected SHA-256')
            if parent['id'] not in parents:
                closure['experiments'].append(parent)
                parents.add(parent['id'])
            closure['execution_jobs'].append(job)
            closure['experiment_executions'].append(subject)
            artifacts = _rows(source, 'experiment_artifacts', 'job_id=? ORDER BY id', (selection.job_id,))
            if len(artifacts) > experiment_jobs.MAX_NATIVE_FILES:
                raise RetainedImportError('Selected execution exceeds the native file count contract')
            closure['experiment_artifacts'].extend(artifacts)
            closure['execution_job_events'].extend(_rows(source, 'execution_job_events',
                                                        'job_id=? ORDER BY sequence', (selection.job_id,)))
            public.append({'id': detail.id, 'experiment_id': detail.experiment_id, 'name': parent['name'],
                           'project_id': project, 'adapter_id': detail.adapter_id, 'adapter_version': detail.adapter_version,
                           'created_at': detail.created_at, 'status': detail.status, 'fidelity': detail.fidelity,
                           'input_hash': detail.input_hash, 'manifest_hash': detail.manifest_hash,
                           'experiment_manifest_hash': parent['manifest_hash'],
                           'artifact_count': len(artifacts), 'artifact_bytes': sum(row['size_bytes'] for row in artifacts)})
    total = sum(row['size_bytes'] for row in closure['experiment_artifacts'])
    if total > MAX_BYTES:
        raise RetainedImportError('Selected evidence exceeds the bounded 8 GiB retention capacity')
    return closure, public, total


def _without_sequence(row, table):
    return {key: value for key, value in row.items()
            if key != ('submission_seq' if table == 'execution_jobs' else 'sequence' if table == 'execution_job_events' else '')}


def _target_state(target, closure, target_root):
    """Return all-new or exactly-existing; partial publication never gets merged."""
    exists = []
    for table in TABLES[:-1]:
        key = 'job_id' if table == 'experiment_executions' else 'id'
        unique = {'experiments': 'request_key', 'execution_jobs': 'idempotency_key',
                  'experiment_artifacts': 'storage_key'}.get(table)
        for expected in closure[table]:
            where, values = f'"{key}"=?', (expected[key],)
            if unique:
                where, values = where + f' OR "{unique}"=?', values + (expected[unique],)
            actual = _rows(target, table, where, values)
            if len(actual) > 1 or (actual and _without_sequence(actual[0], table) != _without_sequence(expected, table)):
                raise RetainedImportError(f'Target contains conflicting retained evidence: {table}')
            exists.append(bool(actual))
    if any(exists) and not all(exists):
        raise RetainedImportError('Target contains a partial selected closure; no rows or files were changed')
    retained = bool(exists) and all(exists)
    for subject in closure['experiment_executions']:
        identity = subject['job_id']
        expected = [_without_sequence(row, 'execution_job_events') for row in closure['execution_job_events'] if row['job_id'] == identity]
        actual = [_without_sequence(row, 'execution_job_events') for row in _rows(
            target, 'execution_job_events', 'job_id=? ORDER BY sequence', (identity,))]
        actual_artifacts = _rows(target, 'experiment_artifacts', 'job_id=? ORDER BY id', (identity,))
        expected_artifacts = sorted([row for row in closure['experiment_artifacts'] if row['job_id'] == identity], key=lambda row: row['id'])
        if (retained and (actual != expected or actual_artifacts != expected_artifacts)) or (not retained and (actual or actual_artifacts)):
            raise RetainedImportError('Target event/artifact closure differs from the original sealed evidence')
    for artifact in closure['experiment_artifacts']:
        path = _artifact_path(target_root, artifact)
        if retained:
            _read_verified(path, artifact)
        elif os.path.lexists(path):
            raise RetainedImportError('Target contains an unowned artifact file; no file is overwritten or adopted')
    return 'already_retained' if retained else 'new'


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_file(source, target, owned):
    # Staging shares the target filesystem. link() publishes fully fsynced bytes
    # exclusively; an existing destination cannot be overwritten by rename().
    os.link(source, target, follow_symlinks=False)
    info = target.stat(follow_symlinks=False)
    owned.append((target, info.st_dev, info.st_ino))


def _remove_owned(owned):
    for path, device, inode in reversed(owned):
        try:
            current = path.stat(follow_symlinks=False)
            if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (device, inode):
                path.unlink()
        except FileNotFoundError:
            pass


def _insert(connection, table, row):
    value = _without_sequence(row, table)
    columns = ','.join('"' + key + '"' for key in value)
    placeholders = ','.join('?' for _ in value)
    connection.exec_driver_sql(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', tuple(value.values()))


def _after_failed_commit(engine, closure, target_root, selections):
    """Distinguish rolled-back publication from a lost COMMIT acknowledgement.

    Never remove files while committed row ownership is possible. A fresh
    connection observes durable state after the original connection is closed.
    """
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('BEGIN')
            present = [bool(_rows(connection, 'execution_jobs', 'id=?', (selection.job_id,)))
                       for selection in selections]
            if not any(present):
                return 'rolled_back'
            if not all(present) or _target_state(connection, closure, target_root) != 'already_retained':
                return 'uncertain'
            with Session(bind=connection) as session:
                for selection in selections:
                    _validate_execution(session, selection.job_id, selection.manifest_sha256)
            return 'retained'
    except (OSError, RetainedImportError, SQLAlchemyError):
        return 'uncertain'


def retain_experiment_executions(*, source_data_dir: Path, target_data_dir: Path, project_id: str,
                                selections: list[RetainedExecutionSelection], source_label: str,
                                receipt_path: Path | None = None, apply: bool = False) -> dict:
    """Preview by default; ``apply=True`` explicitly publishes only these IDs.

    The private receipt has a public-safe ``public`` section for UI provenance.
    The receipt is not attached to an original execution or added to its seal.
    An identical retry verifies all existing bytes and returns already_retained.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', project_id):
        raise RetainedImportError('Explicit managed project ID is required')
    if not 1 <= len(selections) <= MAX_EXECUTIONS or len({item.job_id for item in selections}) != len(selections):
        raise RetainedImportError('Select 1..16 unique completed execution IDs')
    if not isinstance(source_label, str) or not source_label.strip() or len(source_label) > 180 or '\n' in source_label:
        raise RetainedImportError('Source label must be a bounded public description')
    source_root, target_root = _workspace(source_data_dir), _workspace(target_data_dir)
    if source_root == target_root or os.path.samefile(source_root / 'platform.db', target_root / 'platform.db'):
        raise RetainedImportError('Source and target must be distinct existing workspaces')
    if apply and receipt_path is None:
        raise RetainedImportError('Explicit private import receipt path is required for publication')
    if receipt_path is not None:
        receipt_path = Path(receipt_path).absolute()
        if not receipt_path.parent.is_dir() or receipt_path.parent.is_symlink() or os.path.lexists(receipt_path):
            raise RetainedImportError('Receipt needs an existing directory and a new exclusive filename')
        receipt_path = receipt_path.parent.resolve() / receipt_path.name
        if receipt_path.is_relative_to(source_root) or receipt_path.is_relative_to(target_root):
            raise RetainedImportError('Import provenance must be outside the managed source/target data')
    source_engine, target_engine = _engine(source_root, readonly=True), _engine(target_root, readonly=not apply)
    stage, owned, committed, commit_attempted, state = None, [], False, False, None
    try:
        with source_engine.connect() as source, target_engine.connect() as target:
            source.exec_driver_sql('BEGIN')
            target.exec_driver_sql('BEGIN IMMEDIATE' if apply else 'BEGIN')
            source_version, target_version = _schema(source, target)
            if not _rows(target, 'team_projects', 'id=?', (project_id,)):
                raise RetainedImportError('Target project must already exist; project creation is prohibited')
            closure, public, total = _snapshot(source, selections, project_id)
            for artifact in closure['experiment_artifacts']:
                _read_verified(_artifact_path(source_root, artifact), artifact)
            state = _target_state(target, closure, target_root)
            selection_hash = digest({'project_id': project_id,
                                     'selections': sorted([{'id': item.job_id, 'manifest_hash': item.manifest_sha256}
                                                           for item in selections], key=lambda item: item['id'])})
            report = {'schema_version': 'retained-execution-import/1', 'created_at': utcnow(),
                      'selection_sha256': selection_hash, 'operation': 'preview' if not apply else state,
                      'private': {'source_data_dir': str(source_root), 'target_data_dir': str(target_root),
                                  'source_schema_version': source_version, 'target_schema_version': target_version},
                      'public': {'source_label': source_label, 'project_id': project_id, 'executions': public,
                                 'execution_count': len(public), 'artifact_count': len(closure['experiment_artifacts']),
                                 'artifact_bytes': total, 'original_ids_and_receipts_preserved': True,
                                 'new_execution_submitted': False}}
            if not apply:
                target.rollback()
                return report
            if state == 'new':
                if shutil.disk_usage(target_root).free < total + 64 * 1024 ** 2:
                    raise RetainedImportError('Insufficient storage for the bounded retained evidence')
                experiment_dir = target_root / 'experiments'
                artifact_dir = experiment_dir / 'artifacts'
                artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                stage = Path(tempfile.mkdtemp(prefix='retained-import-', dir=experiment_dir))
                for artifact in closure['experiment_artifacts']:
                    staged = stage / artifact['storage_key']
                    with staged.open('xb') as stream:
                        os.chmod(staged, 0o600)
                        _read_verified(_artifact_path(source_root, artifact), artifact, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    _publish_file(staged, artifact_dir / artifact['storage_key'], owned)
                _fsync_directory(artifact_dir)
                for table in TABLES:
                    for row in closure[table]:
                        _insert(target, table, row)
            with Session(bind=target) as session:
                for selection in selections:
                    _validate_execution(session, selection.job_id, selection.manifest_sha256)
            if target.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
                raise RetainedImportError('Target foreign-key integrity failed; selected retention was rolled back')
            # Receipt publication follows the same ordinary-failure rollback
            # ownership as native files. Its mode prevents exposing private paths.
            with tempfile.NamedTemporaryFile(dir=receipt_path.parent, prefix='.retained-import-', delete=False) as stream:
                receipt_stage = Path(stream.name)
                try:
                    os.chmod(receipt_stage, 0o600)
                    stream.write(canonical_bytes(report))
                    stream.flush()
                    os.fsync(stream.fileno())
                    _publish_file(receipt_stage, receipt_path, owned)
                finally:
                    receipt_stage.unlink(missing_ok=True)
            _fsync_directory(receipt_path.parent)
            commit_attempted = True
            target.commit()
            committed = True
            return report
    except BaseException as exc:
        if commit_attempted and state == 'new':
            durable = _after_failed_commit(target_engine, closure, target_root, selections)
            if durable != 'rolled_back':
                # Keep the private provenance receipt as well as the owned
                # files. Deleting either after a successful COMMIT is unsafe.
                committed = True
                if durable == 'retained':
                    raise RetainedImportError('Commit acknowledgement failed; the exact selected closure is retained. '
                                              'Verify with a new receipt filename; no evidence was deleted') from exc
                raise RetainedImportError('Commit outcome could not be established; published files and provenance '
                                          'were preserved for operator investigation') from exc
        if isinstance(exc, (HTTPException, sqlite3.Error, SQLAlchemyError, OSError)):
            raise RetainedImportError('Retained evidence failed its transaction or integrity contract') from exc
        raise
    finally:
        if not committed:
            _remove_owned(owned)
        if stage is not None:
            shutil.rmtree(stage)
        source_engine.dispose()
        target_engine.dispose()
