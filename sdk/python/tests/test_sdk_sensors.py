import base64
import hashlib
import json
import struct

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_bundle_retains_exact_clock_repeated_channels_and_does_not_adopt_a_binding():
    calls = []

    def transport(request):
        assert request.headers['Authorization'] == 'Bearer fixture-token'
        calls.append(request)
        return httpx.Response(200, json={'binding_revision': None, 'entries': [{'freshness': 'unknown'}]})

    with AxiomClient(token='fixture-token', transport=httpx.MockTransport(transport)) as api:
        result = api.sensor_bundle('rec_one', at_ns=2**64 - 1, channel_ids=[4, 8], max_age_ns=123)
    assert result['entries'][0]['freshness'] == 'unknown'
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert calls[0].url.params['at_ns'] == '18446744073709551615'
    assert calls[0].url.params.get_list('channel_id') == ['4', '8']
    assert 'binding_revision' not in calls[0].url.params


def test_rig_and_binding_edits_keep_explicit_revision_guards():
    requests = []

    def transport(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={'revision': 3})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        api.revise_sensor_rig('rig_one', expected_revision=2, name='Revised rig')
        api.bind_recording_sensors('rec_one', expected_revision=0, rig_id='rig_one', rig_revision=3, channels=[])
    assert requests == [('/api/v1/sensor-rigs/rig_one/revisions', {'name': 'Revised rig', 'expected_revision': 2}),
                        ('/api/v1/recordings/rec_one/sensor-bindings', {'rig_id': 'rig_one', 'rig_revision': 3, 'channels': [], 'expected_revision': 0})]


def preview_frame(content, component='camera'):
    preview = {'data_base64': base64.b64encode(content).decode(), 'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    decoded = {'camera': preview} if component == 'camera' else {'point_cloud': preview if component == 'points' else {'intensity': preview}}
    return {'recording_id': 'rec_one', 'channel_id': 2, 'message': {'ordinal': 7}, 'status': 'decoded', 'decoded': decoded}


@pytest.mark.parametrize('component', ['camera', 'points', 'intensity'])
def test_preview_export_verifies_exact_selected_message_and_does_not_overwrite(tmp_path, component):
    data = b'actual-png-bytes' if component == 'camera' else struct.pack('<fff', 1, 2, 3)
    frame = preview_frame(data, component)
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json=frame)

    destination = tmp_path / 'preview.bin'
    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        api.export_sensor_preview('rec_one', 2, 7, destination, component=component, max_points=50)
        with pytest.raises(FileExistsError):
            api.export_sensor_preview('rec_one', 2, 7, destination, component=component)
    assert destination.read_bytes() == data
    assert requests[0].url.params['ordinal'] == '7'
    assert requests[0].url.params['max_points'] == '50'


@pytest.mark.parametrize('fault', ['wrong_identity', 'wrong_hash', 'wrong_size', 'invalid_base64', 'decode_failure'])
def test_bad_sensor_preview_never_creates_a_file(tmp_path, fault):
    frame = preview_frame(b'captured pixels')
    if fault == 'wrong_identity': frame['message']['ordinal'] = 8
    elif fault == 'decode_failure': frame['decoded'] = None
    elif fault == 'invalid_base64': frame['decoded']['camera']['data_base64'] = '!!!'
    elif fault == 'wrong_size': frame['decoded']['camera']['size_bytes'] += 1
    else: frame['decoded']['camera']['sha256'] = '0' * 64
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=frame))) as api:
        with pytest.raises(ValueError):
            api.export_sensor_preview('rec_one', 2, 7, tmp_path / 'bad.bin')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('binding', [False, True])
def test_manifest_download_pins_observed_revision_and_ignores_external_urls(tmp_path, binding):
    content = b'{"revision":4,"transform":null}'
    requests = []

    def transport(request):
        requests.append(request)
        if request.url.path.endswith('/export'):
            assert request.url.params['revision'] == '4'
            return httpx.Response(200, content=content)
        return httpx.Response(200, json={'revision': 4, 'manifest_hash': hashlib.sha256(content).hexdigest(),
                                        'manifest_size_bytes': len(content), 'download_url': 'https://invalid.example/collect'})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        method = api.export_sensor_binding if binding else api.export_sensor_rig
        path = method('identity', tmp_path / 'manifest.json')
    assert path.read_bytes() == content
    assert all(request.url.host == '127.0.0.1' for request in requests)


def test_origin_import_lost_response_retries_artifact_import_without_reexecuting():
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path, request.content))
        if len(requests) == 1:
            raise httpx.ReadError('Import response lost')
        return httpx.Response(200, json={'id': 'rec_original'})

    with AxiomClient(transport=httpx.MockTransport(transport)) as api:
        with pytest.raises(httpx.ReadError): api.import_run_recording('run_one', 'artifact_one')
        assert api.import_run_recording('run_one', 'artifact_one')['id'] == 'rec_original'
    assert requests[0] == requests[1]
    assert requests[0][1] == '/api/v1/runs/run_one/recordings'


def test_cli_binding_json_preserves_separate_revision_guard(tmp_path, monkeypatch, capsys):
    from axiom_sdk import cli
    definition = tmp_path / 'binding.json'
    definition.write_text(json.dumps({'rig_id': 'rig_one', 'rig_revision': 2, 'channels': []}))
    calls = []
    monkeypatch.setattr(AxiomClient, 'bind_recording_sensors', lambda self, rid, **body: calls.append((rid, body)) or {'revision': 1})
    monkeypatch.setattr('sys.argv', ['axiom', 'bind-recording-sensors', 'rec_one', str(definition), '--expected-revision', '0'])
    cli.main()
    assert calls == [('rec_one', {'expected_revision': 0, 'rig_id': 'rig_one', 'rig_revision': 2, 'channels': []})]
    assert json.loads(capsys.readouterr().out)['revision'] == 1
