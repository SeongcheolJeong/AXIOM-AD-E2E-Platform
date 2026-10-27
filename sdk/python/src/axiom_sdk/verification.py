"""Explicit, revision-pinned verification workflow on the shared public API."""
from typing import Any
import hashlib
from pathlib import Path
from .perception import _identity
from .collections import _key, _revision

class VerificationClientMixin:
    def verification_plans(self):
        return self._request('GET','/verification-plans').json()

    def verification_plan(self, plan_id: str, *, revision: int | None=None):
        if revision is not None: _revision(revision)
        return self._request('GET',f'/verification-plans/{_identity(plan_id)}',params={'revision':revision} if revision is not None else {}).json()

    def create_verification_plan(self, *, idempotency_key: str, **definition: Any):
        return self._request('POST','/verification-plans',json=definition,headers=_key(idempotency_key)).json()

    def revise_verification_plan(self, plan_id: str, *, idempotency_key: str, **definition: Any):
        _revision(definition.get('expected_revision'))
        return self._request('POST',f'/verification-plans/{_identity(plan_id)}/revisions',json=definition,headers=_key(idempotency_key)).json()

    def preview_verification(self, plan_id: str, *, revision: int, retry_of: str | None=None):
        _revision(revision)
        return self._request('POST',f'/verification-plans/{_identity(plan_id)}/preview',json={'revision':revision, **({'retry_of':_identity(retry_of)} if retry_of else {})}).json()

    def submit_verification(self, plan_id: str, *, revision: int, expected_preview_hash: str,
                            confirm_trusted_models: bool, idempotency_key: str, retry_of: str | None=None):
        if confirm_trusted_models is not True:
            raise ValueError('Confirm trusted models and the previewed workload explicitly')
        _revision(revision)
        value={'revision':revision,'expected_preview_hash':expected_preview_hash,'confirm_trusted_models':True,'retry_of':retry_of}
        return self._request('POST',f'/verification-plans/{_identity(plan_id)}/attempts',json=value,headers=_key(idempotency_key)).json()

    def verification_attempt(self, attempt_id: str):
        return self._request('GET',f'/verification-attempts/{_identity(attempt_id)}').json()

    def verification_attempts(self, plan_id: str):
        return self._request('GET',f'/verification-plans/{_identity(plan_id)}/attempts').json()

    def cancel_verification(self, attempt_id: str):
        return self._request('POST',f'/verification-attempts/{_identity(attempt_id)}/cancel').json()

    def export_verification_plan(self, plan_id: str, destination: str | Path, *, revision: int):
        value=self.verification_plan(plan_id,revision=revision)
        return self._save_verification_export(f'/verification-plans/{_identity(plan_id)}/export',destination,value['manifest_hash'],{'revision':revision})

    def export_verification_attempt(self, attempt_id: str, destination: str | Path):
        value=self.verification_attempt(attempt_id)
        if not value.get('manifest_hash'): raise ValueError('Attempt is not terminal and sealed')
        return self._save_verification_export(f'/verification-attempts/{_identity(attempt_id)}/export',destination,value['manifest_hash'],{})

    def _save_verification_export(self, path: str, destination: str | Path, expected_hash: str, params: dict):
        raw=self._request('GET',path,params=params).content
        if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=expected_hash:
            raise ValueError('Verification export size/hash mismatch')
        target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        return target
