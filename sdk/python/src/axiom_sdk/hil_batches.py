"""Server-coordinated sequential Software DUT batches on the shared HIL API."""
from __future__ import annotations

from .experiments import _object_map_identity, _object_map_revision
from .hil_beta import _hash, _headers, _page, _ref, _text


def _tests(values):
    if not isinstance(values, list) or not 3 <= len(values) <= 5:
        raise ValueError('A HIL batch requires a list of 3..5 exact saved test references')
    references = [_ref(value) for value in values]
    identities = {(item['id'], item['revision'], item['sha256']) for item in references}
    if len(identities) != len(references):
        raise ValueError('A HIL batch cannot repeat an exact test reference')
    return references


class HilBatchClientMixin:
    def create_hil_sim_batch(self, *, name, tests, idempotency_key, project_id='default', note=''):
        """Save 3..5 pinned tests; creation does not start any engine execution.

        Reuse the same key and body after a lost response. Project ownership and
        the content of each saved revision are verified by the server.
        """
        body = dict(project_id=_object_map_identity(project_id), name=_text(name, 180),
                    note=_text(note, 4000, empty=True), tests=_tests(tests))
        return self._request('POST', '/hil-sim/batches', json=body, headers=_headers(idempotency_key)).json()

    def hil_sim_batches(self, *, project_id='default', limit=100, offset=0):
        return self._request('GET', '/hil-sim/batches', params=_page(project_id, limit, offset)).json()

    def hil_sim_batch(self, batch_id, *, project_id='default'):
        return self._request('GET', f'/hil-sim/batches/{_object_map_identity(batch_id)}',
                             params=dict(project_id=_object_map_identity(project_id))).json()

    def preview_hil_sim_batch(self, batch_id, *, expected_revision, project_id='default'):
        """Preflight the exact batch revision without submitting native jobs."""
        _object_map_revision(expected_revision)
        return self._request('POST', f'/hil-sim/batches/{_object_map_identity(batch_id)}/preview',
            json=dict(project_id=_object_map_identity(project_id), expected_revision=expected_revision)).json()

    def _hil_batch_action(self, batch_id, action, *, expected_revision, idempotency_key, project_id,
                          preview_hash=None):
        _object_map_revision(expected_revision)
        body = dict(project_id=_object_map_identity(project_id), expected_revision=expected_revision)
        if action in ('start', 'resume'):
            body['preview_hash'] = _hash(preview_hash)
        return self._request('POST', f'/hil-sim/batches/{_object_map_identity(batch_id)}/{action}',
                             json=body, headers=_headers(idempotency_key)).json()

    def start_hil_sim_batch(self, batch_id, *, expected_revision, preview_hash, idempotency_key,
                            project_id='default'):
        """Accept a reviewed batch for server-side sequential execution.

        The server owns child submission. This method makes one request and
        does not poll, fan out, or retry if the response is lost.
        """
        return self._hil_batch_action(batch_id, 'start', expected_revision=expected_revision,
            preview_hash=preview_hash, idempotency_key=idempotency_key, project_id=project_id)

    def cancel_hil_sim_batch(self, batch_id, *, expected_revision, idempotency_key, project_id='default'):
        """Request stop; read the retained batch until its current execution settles."""
        return self._hil_batch_action(batch_id, 'cancel', expected_revision=expected_revision,
                                     idempotency_key=idempotency_key, project_id=project_id)

    def resume_hil_sim_batch(self, batch_id, *, expected_revision, preview_hash, idempotency_key,
                             project_id='default'):
        """Explicitly resume reviewed unfinished work; prior attempts remain retained.

        Uncertain outcomes or unreconciled resources remain server errors.
        Reuse this exact action key/body to recover a lost response.
        """
        return self._hil_batch_action(batch_id, 'resume', expected_revision=expected_revision,
            preview_hash=preview_hash, idempotency_key=idempotency_key, project_id=project_id)

    def export_hil_sim_batch(self, batch_id, *, project_id='default'):
        """Read the server's batch evidence export; does not download camera archives."""
        return self._request('GET', f'/hil-sim/batches/{_object_map_identity(batch_id)}/export',
                             params=dict(project_id=_object_map_identity(project_id))).json()
