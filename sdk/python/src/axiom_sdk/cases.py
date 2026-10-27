"""Explicit investigation writes; no automatic inference or source reconstruction."""
from pathlib import Path
from typing import Any

from .perception import _identity


class CaseClientMixin:
    def case_retest_templates(self) -> dict[str, Any]:
        return self._request('GET', '/case-retest-templates').json()

    def _write_case_retest(self, path: str, definition: dict, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not 1 <= len(key) <= 180:
            raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
        if type(definition.get('expected_revision')) is not int or definition['expected_revision'] < 1:
            raise ValueError('expected_revision must be a positive integer')
        return self._request('POST', path, json=definition, headers={'Idempotency-Key': key}).json()

    def create_case_retest(self, case_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        """Author and link a plan atomically. Does not execute."""
        return self._write_case_retest(f'/cases/{_identity(case_id)}/retests', definition, idempotency_key)

    def submit_case_retest(self, case_id: str, plan_id: str, *, expected_revision: int,
                           plan_hash: str, idempotency_key: str) -> dict[str, Any]:
        """Explicit compute request; never automatically resubmit failed replies."""
        return self._write_case_retest(f'/cases/{_identity(case_id)}/retests/{_identity(plan_id)}/campaigns',
            {'expected_revision': expected_revision, 'plan_hash': plan_hash}, idempotency_key)

    def capture_case_retest(self, case_id: str, plan_id: str, campaign_id: str, *, expected_revision: int,
                            plan_hash: str, idempotency_key: str) -> dict[str, Any]:
        return self._write_case_retest(f'/cases/{_identity(case_id)}/retests/{_identity(plan_id)}/campaigns/{_identity(campaign_id)}/capture',
            {'expected_revision': expected_revision, 'plan_hash': plan_hash}, idempotency_key)

    def case_retests(self, case_id: str, *, revision: int | None = None) -> dict[str, Any]:
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('revision must be a positive integer')
        value = self._request('GET', f'/cases/{_identity(case_id)}/retests',
            params={'revision': revision} if revision is not None else {}).json()
        if value.get('case_id') != case_id or revision is not None and value.get('case_revision') != revision:
            raise ValueError('Retest report identity mismatch')
        return value

    def export_case_retests(self, case_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        value = self.case_retests(case_id, revision=revision)
        return self._download_perception(f'/cases/{_identity(case_id)}/retests/export', destination,
            params={'revision': value['case_revision'], 'expected_hash': value['manifest_hash']},
            sha256=value['manifest_hash'], size_bytes=value['manifest_size_bytes'], maximum=16*1024*1024)

    def case_retest_activity(self, *, recording_id: str | None = None) -> dict[str, Any]:
        return self._request('GET', '/case-retest-activity',
            params={'recording_id': _identity(recording_id)} if recording_id is not None else {}).json()

    def export_case_retest_activity(self, destination: str | Path, *, recording_id: str | None = None) -> Path:
        value = self.case_retest_activity(recording_id=recording_id)
        return self._download_perception('/case-retest-activity/export', destination,
            params={'expected_hash': value['manifest_hash'], **({'recording_id': _identity(recording_id)} if recording_id is not None else {})},
            sha256=value['manifest_hash'], size_bytes=value['manifest_size_bytes'], maximum=16*1024*1024)

    def create_case(self, *, title: str, note: str, source: dict[str, Any], idempotency_key: str, assignee: str = '') -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 180:
            raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
        return self._request('POST', '/cases', json=dict(title=title, note=note, assignee=assignee, source=source),
            headers={'Idempotency-Key': idempotency_key}).json()

    def get_case(self, case_id: str, *, revision: int | None = None) -> dict[str, Any]:
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('revision must be a positive integer')
        result = self._request('GET', f'/cases/{_identity(case_id)}', params={'revision': revision} if revision is not None else {}).json()
        if result.get('id') != case_id or revision is not None and result.get('revision') != revision:
            raise ValueError('Case response identity mismatch')
        return result

    def cases(self, *, recording_id: str | None = None, evaluation_id: str | None = None, image_id: str | None = None,
              state: str | None = None, limit: int = 50, offset: int = 0, q: str = '',
              conditions: list[str] | None = None, review: str = 'all', diagnostic: str | None = None) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid case page bounds')
        if state not in (None, 'open', 'investigating', 'resolved', 'dismissed'):
            raise ValueError('Unknown case workflow state')
        params = {'limit': limit, 'offset': offset, **({ 'state': state } if state else {})}
        for key, value in [('recording_id', recording_id), ('evaluation_id', evaluation_id), ('image_id', image_id)]:
            if value is not None: params[key] = _identity(value)
        if review not in ('all', 'reviewed', 'draft', 'unclassified') or not isinstance(q, str) or len(q) > 300:
            raise ValueError('Invalid condition search or review status')
        if conditions is not None and (not isinstance(conditions, list) or len(conditions) > 3 or any(not isinstance(c, str) or len(c) > 100 or c.count(':') != 1 for c in conditions)):
            raise ValueError('conditions must be at most three axis:value strings')
        if diagnostic not in (None, 'has_false_negative', 'has_false_positive', 'incomplete_evidence', 'no_linked_evaluation'):
            raise ValueError('Unknown diagnostic tag')
        params.update(q=q, review=review)
        if conditions: params['condition'] = conditions
        if diagnostic: params['diagnostic'] = diagnostic
        return self._request('GET', '/cases', params=params).json()

    def case_condition_taxonomy(self) -> dict[str, Any]:
        return self._request('GET', '/case-condition-taxonomy').json()

    def classify_case(self, case_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 180:
            raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
        if type(definition.get('expected_revision')) is not int or definition['expected_revision'] < 1:
            raise ValueError('expected_revision is required')
        return self._request('POST', f'/cases/{_identity(case_id)}/conditions', json=definition,
            headers={'Idempotency-Key': idempotency_key}).json()

    def case_condition_coverage(self, *, recording_id: str | None = None) -> dict[str, Any]:
        params = {'recording_id': _identity(recording_id)} if recording_id is not None else {}
        return self._request('GET', '/case-condition-coverage', params=params).json()

    def export_case_condition_coverage(self, destination: str | Path, *, recording_id: str | None = None) -> Path:
        value = self.case_condition_coverage(recording_id=recording_id)
        params = {'expected_hash': value['manifest_hash']}
        if recording_id is not None: params['recording_id'] = _identity(recording_id)
        return self._download_perception('/case-condition-coverage/export', destination, params=params,
            sha256=value['manifest_hash'], size_bytes=value['manifest_size_bytes'], maximum=32*1024*1024)

    def revise_case(self, case_id: str, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 180:
            raise ValueError('Supply a stable Idempotency-Key of 1..180 characters')
        if type(definition.get('expected_revision')) is not int or definition['expected_revision'] < 1:
            raise ValueError('expected_revision is required')
        return self._request('POST', f'/cases/{_identity(case_id)}/revisions', json=definition,
            headers={'Idempotency-Key': idempotency_key}).json()

    def case_samples(self, case_id: str, *, limit: int = 25, offset: int = 0) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid sample page bounds')
        return self._request('GET', f'/cases/{_identity(case_id)}/samples', params={'limit': limit, 'offset': offset}).json()

    def export_case(self, case_id: str, destination: str | Path, *, revision: int) -> Path:
        value = self.get_case(case_id, revision=revision)
        return self._download_perception(f'/cases/{_identity(case_id)}/export', destination,
            params={'revision': revision}, sha256=value['manifest_hash'], size_bytes=value['manifest_size_bytes'], maximum=2*1024*1024)
