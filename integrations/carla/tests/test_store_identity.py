"""First-store publication over real files/processes; no CARLA connection."""
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

import pytest

from axiom_carla import spool
from axiom_carla.spool import Store


@pytest.mark.parametrize('boundary', ['partial_temporary', 'before_publish', 'after_publish'])
def test_sigkill_never_publishes_partial_store_identity(tmp_path, boundary):
    program = '''
import os, signal, sys
from axiom_carla import spool
boundary = sys.argv[2]
original_open, original_link = spool.os.fdopen, spool.os.link
if boundary == 'partial_temporary':
    def die_during_write(fd, *args, **kwargs):
        os.write(fd, b'partial-uuid')
        os.kill(os.getpid(), signal.SIGKILL)
    spool.os.fdopen = die_during_write
else:
    def publication(source, target):
        if boundary == 'before_publish':
            os.kill(os.getpid(), signal.SIGKILL)
        original_link(source, target)
        os.kill(os.getpid(), signal.SIGKILL)
    spool.os.link = publication
spool.Store(sys.argv[1])
'''
    root = tmp_path/'store'
    process = subprocess.run([sys.executable, '-c', program, str(root), boundary], capture_output=True, timeout=15)
    assert process.returncode == -signal.SIGKILL, process.stderr.decode()
    identity = root/'store-id'
    published = identity.read_bytes() if identity.exists() else None
    if boundary == 'after_publish':
        assert published is not None and str(uuid.UUID(published.decode())).encode() == published
    else:
        assert published is None
    # An abandoned temp is diagnostic residue, never an identity candidate.
    leftovers = list(root.glob('.identity-*'))
    assert len(leftovers) == 1
    if boundary == 'partial_temporary':
        assert leftovers[0].read_bytes() == b'partial-uuid'
    restored = Store(root)
    assert Store(root).id == restored.id
    assert identity.read_bytes() == restored.id.encode()
    if published is not None:
        assert identity.read_bytes() == published
    assert identity.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('boundary', ['file_fsync', 'publication', 'directory_fsync'])
def test_identity_io_failure_preserves_absent_or_complete_identity(tmp_path, monkeypatch, boundary):
    root = tmp_path/'store'
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'Injected identity publication failure')
    with monkeypatch.context() as patch:
        if boundary == 'file_fsync':
            patch.setattr(spool.os, 'fsync', fail)
        elif boundary == 'publication':
            patch.setattr(spool.os, 'link', fail)
        else:
            patch.setattr(spool, '_fsync_dir', fail)
        with pytest.raises(OSError, match='Injected identity publication failure'):
            Store(root)
    identity = root/'store-id'
    published = identity.read_bytes() if identity.exists() else None
    assert bool(published) == (boundary == 'directory_fsync')
    assert not list(root.glob('.identity-*'))
    recovered = Store(root)
    if published is not None:
        assert recovered.id.encode() == published
    assert Store(root).id == recovered.id


def test_existing_identity_is_never_republished_or_rotated(tmp_path, monkeypatch):
    store = Store(tmp_path/'store')
    original = (store.root/'store-id').read_bytes()
    monkeypatch.setattr(spool.tempfile, 'mkstemp', lambda *a, **k: pytest.fail('Existing identity was republished'))
    assert Store(store.root).id == store.id
    assert (store.root/'store-id').read_bytes() == original


def test_exclusive_publication_cannot_overwrite_an_unexpected_identity(tmp_path, monkeypatch):
    original_link = os.link
    other_identity = str(uuid.uuid4()).encode()
    root = tmp_path/'store'
    def publish_after_external_creation(source, target):
        Path(target).write_bytes(other_identity)
        original_link(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(spool.os, 'link', publish_after_external_creation)
        with pytest.raises(FileExistsError):
            Store(root)
    assert (root/'store-id').read_bytes() == other_identity
    assert Store(root).id.encode() == other_identity
    assert not list(root.glob('.identity-*'))


def test_existing_invalid_identity_is_rejected_without_silent_reset(tmp_path):
    root = tmp_path/'store'
    root.mkdir()
    identity = root/'store-id'
    identity.write_bytes(b'previously-truncated')
    with pytest.raises(ValueError):
        Store(root)
    assert identity.read_bytes() == b'previously-truncated'
    assert not list(root.glob('.identity-*'))
