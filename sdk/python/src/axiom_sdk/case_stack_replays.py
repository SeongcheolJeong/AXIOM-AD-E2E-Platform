"""Explicit recorded stack execution and same-server verified report download."""
from pathlib import Path
from typing import Any

from .perception import _identity


def _revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('A positive integer expected_revision is required')


class CaseStackReplayClientMixin:
    def preview_case_stack_replay(self, case_id: str, **definition: Any) -> dict[str, Any]:
        _revision(definition.get('expected_revision'))
        return self._request('POST', f'/cases/{_identity(case_id)}/stack-replays/preview', json=definition).json()

    def _case_stack_write(self, case_id, suffix, key, definition):
        _revision(definition.get('expected_revision'))
        if not isinstance(key, str) or not 1 <= len(key) <= 180:
            raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
        return self._request('POST', f'/cases/{_identity(case_id)}/stack-replays{suffix}', json=definition,
            headers={'Idempotency-Key': key}).json()

    def submit_case_stack_replay(self, case_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        if definition.get('confirm_open_loop') is not True:
            raise ValueError('Explicit full-prefix open-loop execution consent is required')
        return self._case_stack_write(case_id, '', idempotency_key, definition)

    def capture_case_stack_replay(self, case_id: str, plan_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        return self._case_stack_write(case_id, f'/{_identity(plan_id)}/capture', idempotency_key, definition)

    def case_stack_replays(self, case_id: str, *, revision: int | None = None) -> dict[str, Any]:
        if revision is not None:
            _revision(revision)
        value = self._request('GET', f'/cases/{_identity(case_id)}/stack-replays', params={'revision': revision} if revision is not None else {}).json()
        if value.get('case_id') != case_id or revision is not None and value.get('case_revision') != revision:
            raise ValueError('Stack replay response case identity differs')
        return value

    def export_case_stack_replays(self, case_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        report = self.case_stack_replays(case_id, revision=revision)
        return self._download_perception(f'/cases/{_identity(case_id)}/stack-replays/export', destination,
            params={'revision': report['case_revision'], 'expected_hash': report['manifest_hash']},
            sha256=report['manifest_hash'], size_bytes=report['manifest_size_bytes'], maximum=16*1024*1024)
