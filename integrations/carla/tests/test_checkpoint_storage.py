"""Private checkpoint storage/crash tests; no simulator or remote worker."""
from contextlib import contextmanager
import json
import signal
import sqlite3
import subprocess
import sys

import pytest

from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import ArtifactPayload
from ade2e.simulation_context import simulation_input_hash
from axiom_carla import spool
from axiom_carla.spool import Attempt, Store, StoreUnavailable, canonical, sha
from axiom_carla.version import ADAPTER_VERSION
from axiom_carla.wire import encode_failure
from test_live_stack import live_request


@pytest.fixture(params=['legacy', 'context'])
def request_value(carla_scenario, request):
    if request.param == 'context':
        value = live_request(carla_scenario)
        return ExecutionAttemptRequest(**value.model_dump(exclude={'schema_version'}), adapter_id='carla', adapter_version=ADAPTER_VERSION)
    return ExecutionAttemptRequest(execution_id='checkpoint-storage-test', adapter_id='carla', adapter_version=ADAPTER_VERSION,
        scenario=carla_scenario, input_hash=simulation_input_hash(carla_scenario, 'carla', ADAPTER_VERSION))


def artifact(name='data.bin', content=b'\x00\xffsource'):
    return ArtifactPayload(name=name, kind='execution_diagnostic', media_type='application/octet-stream', content=content)


def public_payload(attempt, payloads, phase):
    header, context = spool._checkpoint_header(attempt.meta(), phase)
    return encode_failure(header['detail'], payloads, request=context)


def saved(attempt):
    with attempt.db() as db:
        checkpoint = db.execute('SELECT * FROM checkpoint').fetchall()
        meta = db.execute('SELECT value FROM meta').fetchone()[0]
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoint_blobs'").fetchone()
        blobs = db.execute('SELECT rowid,sha256,size_bytes,content FROM checkpoint_blobs ORDER BY sha256').fetchall() if exists else None
    return checkpoint, meta, blobs


def descriptor(attempt):
    raw = saved(attempt)[0][0][1]
    assert raw.startswith(spool.CHECKPOINT_MAGIC)
    return json.loads(raw[len(spool.CHECKPOINT_MAGIC):])


def rewrite_descriptor(attempt, mutate):
    value = descriptor(attempt)
    mutate(value)
    raw = spool.CHECKPOINT_MAGIC + canonical(value)
    with attempt.db() as db:
        db.execute('UPDATE checkpoint SET content=?,sha256=? WHERE id=1', (raw, sha(raw)))


def assert_no_archive(attempt):
    assert attempt.meta()['state'] == 'running'
    with attempt.db() as db:
        assert db.execute('SELECT count(*) FROM archive').fetchone()[0] == 0


def test_recovery_materializes_exact_prior_public_wire_and_leaves_journal(request_value, tmp_path):
    store = Store(tmp_path / 'store')
    payloads = [artifact('한글 "escape\\.bin', b'\x00\xffbinary'), artifact('empty.bin', b''),
        ArtifactPayload(name='duplicate-bytes.bin', kind='separate-role', media_type='application/x-original', content=b'\x00\xffbinary')]
    with store.begin(request_value) as attempt:
        attempt.event('source_observed', {'frame': 4}, content=b'raw-source')
        journal = attempt.journal(include_blobs=True)
        expected = public_payload(attempt, payloads, '관측 "before\\send')
        attempt.checkpoint(payloads, '관측 "before\\send')
        value = descriptor(attempt)
        assert value['wire_size_bytes'] == len(canonical(expected))
        assert len(saved(attempt)[2]) == 2
        assert attempt.meta()['checkpoint_journal_count'] == 1
        with attempt.db() as db:
            assert db.execute('PRAGMA synchronous').fetchone()[0] == 2
            assert db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal'
    status = store.inspect(request_value)
    assert status.state == 'interrupted'
    archive = json.loads(attempt.archive())
    assert canonical(archive['payload']) == canonical(expected)
    assert archive['store_id'] == store.id
    assert archive['execution_id'] == request_value.execution_id
    assert archive['request_hash'] == sha(canonical(request_value.model_dump(mode='json')))
    assert attempt.journal(include_blobs=True) == journal
    assert saved(attempt)[0] == [] and saved(attempt)[2] == []


def test_unchanged_blobs_reused_and_only_latest_unique_content_retained(request_value, tmp_path):
    with Store(tmp_path).begin(request_value) as attempt:
        unchanged = artifact('stable.bin', b'stable' * 10000)
        attempt.checkpoint([unchanged, artifact(content=b'old')], 'one')
        old_id = next(row[0] for row in saved(attempt)[2] if row[1] == sha(unchanged.content))
        for index in range(12):
            attempt.checkpoint([unchanged, artifact(content=str(index).encode()), artifact('alias.bin', unchanged.content)], 'two')
            rows = saved(attempt)[2]
            assert len(rows) == 2
            assert next(row[0] for row in rows if row[1] == sha(unchanged.content)) == old_id
            assert {row[1] for row in rows} == {sha(unchanged.content), sha(str(index).encode())}
        attempt.checkpoint([], 'empty')
        assert saved(attempt)[2] == []


@pytest.mark.parametrize('size', [0, 1, 2, 3, 4, 17, 1024])
def test_exact_wire_limit_checked_before_any_checkpoint_commit(request_value, tmp_path, monkeypatch, size):
    with Store(tmp_path).begin(request_value) as attempt:
        payloads = [artifact('é "한글\\.bin', b'x' * size)]
        expected_size = len(canonical(public_payload(attempt, payloads, 'same')))
        monkeypatch.setattr(spool, 'WIRE_BYTES_LIMIT', expected_size)
        attempt.checkpoint(payloads, 'same')
        assert descriptor(attempt)['wire_size_bytes'] == expected_size
        before = saved(attempt)
        monkeypatch.setattr(spool, 'WIRE_BYTES_LIMIT', expected_size - 1)
        with pytest.raises(StoreUnavailable, match='wire capacity'):
            attempt.checkpoint(payloads, 'same')
        assert saved(attempt) == before


@pytest.mark.parametrize('invalid', ['duplicate_names', 'count', 'individual_size', 'total_size', 'mutated_metadata'])
def test_invalid_payload_rejected_without_replacing_committed_checkpoint(request_value, tmp_path, invalid):
    with Store(tmp_path).begin(request_value) as attempt:
        attempt.checkpoint([artifact()], 'valid')
        before = saved(attempt)
        if invalid == 'duplicate_names':
            payloads = [artifact(), artifact()]
        elif invalid == 'count':
            payloads = [artifact(str(index)) for index in range(101)]
        elif invalid == 'individual_size':
            item = artifact()
            item.content = b'x' * (spool.ARTIFACT_BYTES_LIMIT + 1)
            payloads = [item]
        elif invalid == 'total_size':
            payloads = [artifact('a', b'x' * (17 * 1024 * 1024)), artifact('b', b'y' * (17 * 1024 * 1024))]
        else:
            item = artifact()
            item.name = ''
            payloads = [item]
        with pytest.raises(ValueError):
            attempt.checkpoint(payloads, 'invalid')
        assert saved(attempt) == before


@pytest.mark.parametrize('damage', ['missing_blob', 'extra_blob', 'blob_content', 'blob_size', 'blob_type',
    'ref_size', 'ref_bool', 'ref_hash', 'ref_name', 'duplicate_ref', 'envelope', 'wire_size', 'extra_field', 'magic', 'descriptor_hash', 'phase'])
def test_corrupt_private_checkpoint_never_seals_a_partial_archive(request_value, tmp_path, damage):
    store = Store(tmp_path)
    with store.begin(request_value) as attempt:
        attempt.checkpoint([artifact()], 'valid')
        with attempt.db() as db:
            if damage == 'missing_blob':
                db.execute('DELETE FROM checkpoint_blobs')
            elif damage == 'extra_blob':
                db.execute('INSERT INTO checkpoint_blobs VALUES(?,?,?)', (sha(b'extra'), 5, b'extra'))
            elif damage == 'blob_content':
                db.execute('UPDATE checkpoint_blobs SET content=?', (b'XXXXXXXX',))
            elif damage == 'blob_size':
                db.execute('UPDATE checkpoint_blobs SET size_bytes=size_bytes+1')
            elif damage == 'blob_type':
                db.execute('UPDATE checkpoint_blobs SET content=?,size_bytes=8', ('XXXXXXXX',))
            elif damage == 'descriptor_hash':
                db.execute('UPDATE checkpoint SET sha256=?', ('f' * 64,))
            elif damage == 'phase':
                db.execute("UPDATE checkpoint SET phase='other'")
            elif damage == 'magic':
                content = b'AXIOMCHK999\n{}'
                db.execute('UPDATE checkpoint SET content=?,sha256=?', (content, sha(content)))
        changes = {
            'ref_size': lambda d: d['artifacts'][0].update(size_bytes=999),
            'ref_bool': lambda d: d['artifacts'][0].update(size_bytes=True),
            'ref_hash': lambda d: d['artifacts'][0].update(sha256='a' * 64),
            'ref_name': lambda d: d['artifacts'][0].update(name=' padded '),
            'duplicate_ref': lambda d: d['artifacts'].append(dict(d['artifacts'][0])),
            'envelope': lambda d: d['envelope'].update(worker_adapter_version='0.0.0'),
            'wire_size': lambda d: d.update(wire_size_bytes=d['wire_size_bytes'] + 1),
            'extra_field': lambda d: d.update(untrusted=True),
        }
        if damage in changes:
            rewrite_descriptor(attempt, changes[damage])
    with pytest.raises(StoreUnavailable):
        store.inspect(request_value)
    assert_no_archive(attempt)


def test_existing_blob_must_be_verified_before_reuse(request_value, tmp_path):
    with Store(tmp_path).begin(request_value) as attempt:
        attempt.checkpoint([artifact()], 'old')
        with attempt.db() as db:
            db.execute('UPDATE checkpoint_blobs SET content=?', (b'XXXXXXXX',))
        before = saved(attempt)
        with pytest.raises(StoreUnavailable, match='Existing checkpoint blob hash'):
            attempt.checkpoint([artifact()], 'new')
        assert saved(attempt) == before


def test_old_reader_rejects_new_binary_format_before_its_seal_call(request_value, tmp_path):
    store = Store(tmp_path)
    with store.begin(request_value) as attempt:
        attempt.checkpoint([artifact()], 'current')
        expected = public_payload(attempt, [artifact()], 'current')
    # Exact old recover_abandoned sequence: hash -> json.loads -> seal.
    with attempt.db() as db:
        content, digest = db.execute('SELECT content,sha256 FROM checkpoint WHERE id=1').fetchone()
    assert sha(content) == digest
    with pytest.raises(ValueError):
        payload = json.loads(content)
        attempt.seal('interrupted', payload)
    assert_no_archive(attempt)
    store.inspect(request_value)
    assert json.loads(attempt.archive())['payload'] == expected


def write_legacy_checkpoint(attempt, payloads, phase):
    expected = public_payload(attempt, payloads, phase)
    content = canonical(expected)
    with attempt.db() as db:
        db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)', (content, sha(content), phase))
        meta = attempt.meta()
        meta.update(checkpoint_phase=phase, checkpoint_journal_count=meta['journal_count'])
        attempt._write_meta(db, meta)
    return expected


@pytest.mark.parametrize('upgrade', [False, True])
def test_old_wire_checkpoint_reads_or_upgrades_without_store_or_journal_change(request_value, tmp_path, upgrade):
    store = Store(tmp_path)
    with store.begin(request_value) as attempt:
        attempt.event('source_observed', {'frame': 10}, content=b'original')
        journal = attempt.journal(include_blobs=True)
        expected = write_legacy_checkpoint(attempt, [artifact(content=b'old')], 'old')
        assert saved(attempt)[2] is None
        if upgrade:
            attempt.checkpoint([artifact(content=b'new')], 'new')
            expected = public_payload(attempt, [artifact(content=b'new')], 'new')
    prior_store_id = store.id
    recreated = Store(tmp_path)
    assert recreated.id == prior_store_id
    recreated.inspect(request_value)
    assert json.loads(attempt.archive())['payload'] == expected
    assert attempt.journal(include_blobs=True) == journal


@pytest.mark.parametrize('boundary', ['after_table_create', 'after_blob_insert', 'after_checkpoint_replace', 'before_commit'])
def test_failed_checkpoint_transaction_restores_previous_wire_and_lazy_schema(request_value, tmp_path, monkeypatch, boundary):
    with Store(tmp_path).begin(request_value) as attempt:
        write_legacy_checkpoint(attempt, [artifact(content=b'old')], 'old')
        before = saved(attempt)
        original_db = Attempt.db

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, *args):
                value = self.connection.execute(sql, *args)
                matches = {'after_table_create': 'CREATE TABLE IF NOT EXISTS checkpoint_blobs',
                    'after_blob_insert': 'INSERT INTO checkpoint_blobs', 'after_checkpoint_replace': 'INSERT OR REPLACE INTO checkpoint VALUES'}
                if boundary in matches and sql.startswith(matches[boundary]):
                    raise OSError('authored checkpoint write failure')
                return value

        @contextmanager
        def failing_db(self):
            with original_db(self) as connection:
                yield ConnectionProxy(connection)
                if boundary == 'before_commit' and connection.in_transaction:
                    raise OSError('authored checkpoint commit failure')

        with monkeypatch.context() as scoped:
            scoped.setattr(Attempt, 'db', failing_db)
            with pytest.raises(OSError, match='authored checkpoint'):
                attempt.checkpoint([artifact(content=b'new')], 'new')
        assert saved(attempt) == before


@pytest.mark.parametrize('boundary', ['after_table_create', 'after_blob_insert', 'after_checkpoint_replace', 'before_commit', 'after_commit'])
@pytest.mark.parametrize('prior_format', ['legacy', 'refs'])
def test_real_process_kill_observes_whole_previous_or_whole_new_checkpoint(request_value, tmp_path, boundary, prior_format):
    request_path = tmp_path / 'request.json'
    request_path.write_bytes(canonical(request_value.model_dump(mode='json')))
    program = r'''
import json, os, signal, sqlite3, sys
from pathlib import Path
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import ArtifactPayload
from axiom_carla import spool
from axiom_carla.spool import Store, canonical, sha
from axiom_carla.wire import encode_failure
request = ExecutionAttemptRequest.model_validate_json(Path(sys.argv[2]).read_bytes())
boundary = sys.argv[3]
prior_format = sys.argv[4]
with Store(sys.argv[1]).begin(request) as attempt:
    attempt.event('source_observed', {'frame': 1}, content=b'raw-original')
    old = ArtifactPayload(name='data.bin', kind='execution_diagnostic', media_type='application/octet-stream', content=b'old')
    if prior_format == 'refs':
        attempt.checkpoint([old], 'old')
    else:
        header, context = spool._checkpoint_header(attempt.meta(), 'old')
        encoded = canonical(encode_failure(header['detail'], [old], request=context))
        with attempt.db() as db:
            meta = attempt.meta()
            db.execute('INSERT INTO checkpoint VALUES(1,?,?,?)', (encoded, sha(encoded), 'old'))
            meta.update(checkpoint_phase='old', checkpoint_journal_count=1)
            attempt._write_meta(db, meta)
    original = sqlite3.connect
    class KillConnection(sqlite3.Connection):
        checkpoint_transaction = False
        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if sql.startswith('BEGIN IMMEDIATE'):
                self.checkpoint_transaction = True
            matches = {'after_table_create': 'CREATE TABLE IF NOT EXISTS checkpoint_blobs',
                'after_blob_insert': 'INSERT INTO checkpoint_blobs',
                'after_checkpoint_replace': 'INSERT OR REPLACE INTO checkpoint VALUES'}
            if boundary in matches and sql.startswith(matches[boundary]):
                os.kill(os.getpid(), signal.SIGKILL)
            return result
        def commit(self):
            if self.checkpoint_transaction and boundary == 'before_commit':
                os.kill(os.getpid(), signal.SIGKILL)
            super().commit()
            if self.checkpoint_transaction and boundary == 'after_commit':
                os.kill(os.getpid(), signal.SIGKILL)
    sqlite3.connect = lambda *args, **kwargs: original(*args, factory=KillConnection, **kwargs)
    new = old.model_copy(update={'content': b'new'})
    attempt.checkpoint([new], 'new')
raise AssertionError('Fault did not trigger')
'''
    process = subprocess.run([sys.executable, '-c', program, str(tmp_path / 'store'), str(request_path), boundary, prior_format],
                             capture_output=True, timeout=15)
    assert process.returncode == -signal.SIGKILL, process.stderr.decode()
    store = Store(tmp_path / 'store')
    attempt = store.find(request_value.execution_id)
    expected_phase = 'new' if boundary == 'after_commit' else 'old'
    assert attempt.meta()['checkpoint_phase'] == expected_phase
    assert attempt.meta()['checkpoint_journal_count'] == 1
    assert (saved(attempt)[2] is not None) == (boundary == 'after_commit' or prior_format == 'refs')
    if saved(attempt)[2] is not None:
        assert {row[1] for row in saved(attempt)[2]} == {sha(expected_phase.encode())}
    expected = public_payload(attempt, [artifact(content=expected_phase.encode())], expected_phase)
    journal = attempt.journal(include_blobs=True)
    store.inspect(request_value)
    assert canonical(json.loads(attempt.archive())['payload']) == canonical(expected)
    assert attempt.journal(include_blobs=True) == journal


def test_sealing_public_payload_remains_exact_and_discards_private_checkpoint(request_value, tmp_path):
    store = Store(tmp_path)
    with store.begin(request_value) as attempt:
        attempt.checkpoint([artifact(content=b'private')], 'old')
        payload = public_payload(attempt, [artifact(content=b'final')], 'final')
        expected = {'schema_version': spool.ARCHIVE_SCHEMA, 'store_id': store.id,
            **spool.identity(request_value), 'outcome': 'failed', 'payload': payload, 'execution_ms': 12.5}
        attempt.seal('failed', payload, execution_ms=12.5)
    assert attempt.archive() == canonical(expected)
    assert saved(attempt)[0] == [] and saved(attempt)[2] == []
