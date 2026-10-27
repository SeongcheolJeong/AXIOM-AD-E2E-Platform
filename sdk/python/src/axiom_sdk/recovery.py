"""Read-only remote recovery; source execution is never resubmitted."""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any

from .perception import TERMINAL, _identity, _integer
from .datasets import STATUSES


class RecoveryClientMixin:
    def inspect_remote_execution(self, job_id: str) -> dict[str, Any]:
        job_id = _identity(job_id)
        result = self._request('GET', f'/jobs/{job_id}/remote-execution').json()
        if not isinstance(result, dict) or result.get('source_job_id') != job_id:
            raise ValueError('Remote inspection source identity mismatch')
        return result

    def submit_execution_recovery(self, source_job_id: str, *, note: str, idempotency_key: str) -> dict[str, Any]:
        source_job_id = _identity(source_job_id)
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise ValueError('Recovery note must contain 1..1000 characters')
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError('Recovery idempotency_key must not be blank')
        return self._request('POST', '/execution-recoveries',
            json={'source_job_id': source_job_id, 'note': note}, headers=self._intent_headers(idempotency_key)).json()

    def execution_recoveries(self, *, source_job_id: str | None = None, limit: int = 100,
                            before_seq: int | None = None) -> dict[str, Any]:
        params = {'limit': _integer(limit, 'limit', 1, 100)}
        if source_job_id is not None:
            params['source_job_id'] = _identity(source_job_id)
        if before_seq is not None:
            params['before_seq'] = _integer(before_seq, 'before_seq', 1, 2**63-1)
        return self._request('GET', '/execution-recoveries', params=params).json()

    def get_execution_recovery(self, recovery_id: str) -> dict[str, Any]:
        recovery_id = _identity(recovery_id)
        result = self._request('GET', f'/execution-recoveries/{recovery_id}').json()
        if not isinstance(result, dict) or result.get('id') != recovery_id:
            raise ValueError('Recovery operation identity mismatch')
        return result

    def cancel_execution_recovery(self, recovery_id: str) -> dict[str, Any]:
        """Cancel only retrieval, never assume the remote simulator stopped."""
        recovery_id = _identity(recovery_id)
        result = self._request('POST', f'/execution-recoveries/{recovery_id}/cancel').json()
        if not isinstance(result, dict) or result.get('id') != recovery_id:
            raise ValueError('Recovery operation identity mismatch')
        return result

    def wait_execution_recovery(self, recovery_id: str, *, timeout: float = 180,
                                poll_interval: float = .5) -> dict[str, Any]:
        _identity(recovery_id)
        for name, value in [('timeout', timeout), ('poll_interval', poll_interval)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (name == 'poll_interval' and value == 0):
                raise ValueError('timeout must be finite/nonnegative and poll_interval finite/positive')
        deadline = time.monotonic()+timeout
        while True:
            result = self.get_execution_recovery(recovery_id)
            if result.get('status') not in STATUSES:
                raise ValueError('Unknown recovery lifecycle status')
            if result['status'] in TERMINAL:
                return result
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Recovery {recovery_id} remains {result["status"]}; no cancellation or resimulation was requested')
            time.sleep(min(remaining, poll_interval))

    def export_execution_recovery(self, recovery_id: str, destination: str | Path) -> Path:
        result = self.get_execution_recovery(recovery_id)
        if result.get('status') not in TERMINAL:
            raise ValueError('Recovery export requires terminal sealed evidence')
        return self._download_perception(f'/execution-recoveries/{_identity(recovery_id)}/export', destination,
            sha256=result.get('manifest_hash'), size_bytes=result.get('manifest_size_bytes'), maximum=32*1024*1024)

    def export_remote_execution_archive(self, recovery_id: str, destination: str | Path) -> Path:
        result = self.get_execution_recovery(recovery_id)
        if result.get('status') not in TERMINAL or result.get('archive_hash') is None:
            raise ValueError('This recovery has no verified terminal remote archive')
        return self._download_perception(f'/execution-recoveries/{_identity(recovery_id)}/archive', destination,
            sha256=result.get('archive_hash'), size_bytes=result.get('archive_size_bytes'), maximum=128*1024*1024)
