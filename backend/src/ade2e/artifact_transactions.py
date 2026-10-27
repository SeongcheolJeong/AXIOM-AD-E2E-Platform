"""Keep newly created local artifact files owned by their DB transaction.

These hooks handle ordinary rollback, commit errors and session close. They do
not make the filesystem and database crash-atomic across process/host failure.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)
_STATE_KEY = "ade2e.artifact_transactions"


@dataclass
class _PendingArtifacts:
    paths: set[Path] = field(default_factory=set)
    committed: bool = False


def track_created_artifact(session: Session, path: Path) -> None:
    """Track a path only after exclusive creation succeeded, before writing.

    A savepoint's successful release transfers ownership to its parent, since
    an outer rollback must still remove the uncommitted artifact bytes.
    """
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        raise RuntimeError("Artifact creation requires an active database transaction")
    if _STATE_KEY not in session.info:
        session.info[_STATE_KEY] = {}
        event.listen(session, "after_commit", _mark_committed)
        event.listen(session, "after_transaction_end", _finish_transaction)
    pending: dict[SessionTransaction, _PendingArtifacts] = session.info[_STATE_KEY]
    pending.setdefault(transaction, _PendingArtifacts()).paths.add(path)


def _mark_committed(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending = session.info[_STATE_KEY].get(transaction)
    if pending is not None:
        pending.committed = True


def _finish_transaction(session: Session, transaction: SessionTransaction) -> None:
    transactions: dict[SessionTransaction, _PendingArtifacts] = session.info[_STATE_KEY]
    pending = transactions.pop(transaction, None)
    if pending is None:
        return
    if pending.committed:
        if transaction.parent is not None:
            transactions.setdefault(transaction.parent, _PendingArtifacts()).paths.update(pending.paths)
        return
    for path in pending.paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Preserve the original storage/commit exception; do not hide it
            # behind a second cleanup error. This condition needs operator care.
            logger.exception("Could not remove artifact from rolled-back transaction: %s", path)
