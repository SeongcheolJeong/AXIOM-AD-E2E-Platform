"""Preparation recovery on authored metadata; no native or human beta claim."""
from pathlib import Path
import hashlib
import json
import sys

import httpx
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_log_sim_beta import PACK_IDS, participant_labels, prepare, retained_sources


def sources():
    return [{'id': pack, 'raw': pack.encode(), 'mcap_sha256': hashlib.sha256(pack.encode()).hexdigest(),
        'ego_channel_id': 1, 'objects_channel_id': 2,
        'provenance': {'execution_id': 'job_' + pack, 'selected_frame_count': 12, 'original_frame_count': 20}}
        for pack in PACK_IDS]


class API:
    def __init__(self):
        self.projects, self.tokens, self.bindings, self.calls = [], {}, {}, []
        self.missing_pack = None

    def reply(self, request):
        path = request.url.path.removeprefix('/api/v1')
        method = request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.headers.get('content-type') == 'application/json' else {}
        if path == '/projects':
            if method == 'POST':
                result = {'id': 'project_' + str(len(self.projects) + 1), 'name': body['name']}
                self.projects.append(result)
            else:
                result = {'items': self.projects}
        elif path.endswith('/tokens'):
            project = path.split('/')[2]
            tokens = self.tokens.setdefault(project, [])
            if method == 'POST':
                result = body | {'id': 'token_' + project, 'token': 'private_' + project, 'project_id': project, 'revoked_at': None}
                tokens.append(result)
            else:
                result = {'items': tokens}
        elif path == '/team/whoami':
            token = next(token for tokens in self.tokens.values() for token in tokens
                if request.headers['Authorization'] == 'Bearer ' + token['token'])
            result = token | {'mode': 'project'}
        elif path == '/log-sim/uploads':
            project, pack = request.url.params['project_id'], request.url.params['name'].split(' ')[0]
            result = {'id': project + '_' + pack}
        elif path == '/log-sim/inputs/inspect':
            result = {'preview_hash': 'a' * 64}
        elif path == '/log-sim/inputs':
            result = {'id': 'source_' + body['recording_id']}
        elif path.startswith('/log-sim/sources/'):
            result = {'source_hash': hashlib.sha256(path.encode()).hexdigest()}
        elif path.endswith('/binding'):
            pack = path.split('/')[-2]
            self.bindings[body['project_id'], pack] = body
            result = body
        elif path == '/log-sim/packs':
            project = request.url.params['project_id']
            result = {'items': [{'id': pack, 'status': 'ready' if pack != self.missing_pack else 'unbound',
                'binding': self.bindings.get((project, pack))} for pack in PACK_IDS]
                + [{'id': 'input-errors', 'status': 'input_example', 'binding': None}]}
        else:
            raise AssertionError((method, path))
        return httpx.Response(200, json=result)

    def client(self):
        return httpx.Client(base_url='http://127.0.0.1:8072/api/v1', transport=httpx.MockTransport(self.reply))


def test_three_isolated_projects_resume_without_remint_or_upload(tmp_path):
    api = API()
    with api.client() as client:
        receipt = prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
        original = list(api.calls)
        resumed = prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
    assert receipt['ready'] and resumed['ready']
    assert len({entry['project_id'] for entry in resumed['participants']}) == 3
    assert len(api.projects) == 3 and all(len(tokens) == 1 for tokens in api.tokens.values())
    assert all(method == 'GET' for method, _ in api.calls[len(original):])
    assert sum(method == 'POST' and path == '/log-sim/uploads' for method, path in original) == 15
    for entry in receipt['participants']:
        token = tmp_path / entry['token_file']
        assert token.stat().st_mode & 0o777 == 0o600
        assert token.read_text() not in (tmp_path / 'participants.json').read_text()
        assert len(entry['packs']) == 5 and entry['ready']


def test_original_single_project_receipt_remains_resumable(tmp_path):
    api = API()
    with api.client() as client:
        saved = prepare(tmp_path, 'http://127.0.0.1:8072', sources(), client=client)
        old = {key: saved[key] for key in ('project_id', 'target_api', 'packs')}
        (tmp_path / 'prepared.json').write_text(json.dumps(old))
        count = len(api.calls)
        result = prepare(tmp_path, 'http://127.0.0.1:8072', sources(), client=client)
    assert result['project_id'] == saved['project_id'] and result['ready']
    assert result['token_file'] == 'access/tester-token'
    assert all(method == 'GET' for method, _ in api.calls[count:])


def test_missing_live_pack_clears_readiness_and_does_not_claim_complete(tmp_path):
    api = API()
    with api.client() as client:
        prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
        api.missing_pack = 'following-gap'
        with pytest.raises(ValueError, match='not bound'):
            prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
    receipt = json.loads((tmp_path / 'participants.json').read_text())
    assert not receipt['ready'] and not any(entry['ready'] for entry in receipt['participants'])


def test_lost_private_token_file_does_not_mint_another_live_token(tmp_path):
    api = API()
    with api.client() as client:
        receipt = prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
        (tmp_path / receipt['participants'][0]['token_file']).unlink()
        count = len(api.calls)
        with pytest.raises(ValueError, match='private file is missing'):
            prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
    assert all(method == 'GET' for method, _ in api.calls[count:])
    assert all(len(tokens) == 1 for tokens in api.tokens.values())


def test_missing_or_changed_source_fails_before_project_creation(tmp_path):
    api = API()
    with api.client() as client:
        with pytest.raises(ValueError, match='All five'):
            prepare(tmp_path, 'http://127.0.0.1:8072', sources()[:-1], participants=3, client=client)
        altered = sources()
        altered[0]['raw'] = b'changed'
        with pytest.raises(ValueError, match='bytes changed'):
            prepare(tmp_path, 'http://127.0.0.1:8072', altered, participants=3, client=client)
    assert not api.calls and not (tmp_path / 'participants.json').exists()
    with pytest.raises(ValueError, match='database is missing'):
        retained_sources(tmp_path / 'absent-workspace', {pack: 'job_a' for pack in PACK_IDS})
    assert not (tmp_path / 'absent-workspace').exists()


def test_receipt_cannot_move_to_another_api_or_replace_labels(tmp_path):
    api = API()
    with api.client() as client:
        prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, client=client)
        count = len(api.calls)
        with pytest.raises(ValueError, match='another source or API'):
            prepare(tmp_path, 'http://127.0.0.1:8073', sources(), participants=3, client=client)
        with pytest.raises(ValueError, match='labels differ'):
            prepare(tmp_path, 'http://127.0.0.1:8072', sources(), participants=3, labels=['A', 'B', 'C'], client=client)
    assert len(api.calls) == count


@pytest.mark.parametrize('count,labels', [(2, None), (3, ['A', 'A', 'B']), (4, ['A', 'B', 'C']), (None, ['A'])])
def test_participant_input_limits(count, labels):
    with pytest.raises(ValueError):
        participant_labels(count, labels)
