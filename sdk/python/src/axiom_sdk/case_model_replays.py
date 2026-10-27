"""Explicit recorded-model actions. No hidden retry, model download or scheduler."""
from pathlib import Path
from typing import Any

from .perception import _identity


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
    return value


def _revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('Expected revision must be a positive integer')
    return value


class CaseModelReplayClientMixin:
    def preview_case_model_replay(self, case_id: str, *, expected_revision: int,
                                  baseline_evaluation_id: str, model_id: str, model_revision: int, note: str) -> dict[str, Any]:
        return self._request('POST', f'/cases/{_identity(case_id)}/model-replays/preview', json={
            'expected_revision': _revision(expected_revision), 'baseline_evaluation_id': _identity(baseline_evaluation_id),
            'model_id': _identity(model_id), 'model_revision': _revision(model_revision), 'note': note}).json()

    def submit_case_model_replay(self, case_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        _revision(definition.get('expected_revision'))
        if definition.get('confirm_trusted_model') is not True:
            raise ValueError('Explicit trusted-model and full-split acknowledgment is required')
        return self._request('POST', f'/cases/{_identity(case_id)}/model-replays', json=definition,
            headers={'Idempotency-Key': _key(idempotency_key)}).json()

    def evaluate_case_model_replay(self, case_id: str, plan_id: str, *, expected_revision: int,
                                   plan_hash: str, idempotency_key: str) -> dict[str, Any]:
        return self._request('POST', f'/cases/{_identity(case_id)}/model-replays/{_identity(plan_id)}/evaluate',
            json={'expected_revision': _revision(expected_revision), 'plan_hash': plan_hash},
            headers={'Idempotency-Key': _key(idempotency_key)}).json()

    def capture_case_model_replay(self, case_id: str, plan_id: str, *, expected_revision: int,
                                  plan_hash: str, idempotency_key: str) -> dict[str, Any]:
        return self._request('POST', f'/cases/{_identity(case_id)}/model-replays/{_identity(plan_id)}/capture',
            json={'expected_revision': _revision(expected_revision), 'plan_hash': plan_hash},
            headers={'Idempotency-Key': _key(idempotency_key)}).json()

    def case_model_replays(self, case_id: str, *, revision: int | None = None) -> dict[str, Any]:
        params = {} if revision is None else {'revision': _revision(revision)}
        result = self._request('GET', f'/cases/{_identity(case_id)}/model-replays', params=params).json()
        if result.get('case_id') != case_id or revision is not None and result.get('case_revision') != revision:
            raise ValueError('Case/model-replay response identity mismatch')
        return result

    def export_case_model_replays(self, case_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        report = self.case_model_replays(case_id, revision=revision)
        return self._download_perception(f'/cases/{_identity(case_id)}/model-replays/export', destination,
            params={'revision': report['case_revision'], 'expected_hash': report['manifest_hash']},
            sha256=report['manifest_hash'], size_bytes=report['manifest_size_bytes'], maximum=16*1024*1024)
