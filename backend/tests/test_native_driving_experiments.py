"""Authored native-command boundary fixtures; actual GPU acceptance is separate."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ade2e.driving_contract import canonical
from ade2e.experiment_schemas import ExperimentCreate
from test_driving_policies import driving_input,original,receipt

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'integrations/vad/src'))
from axiom_vad.experiment import NativeVADExperimentHandler,load_configuration


def configuration(tmp_path,monkeypatch):
    value={'schema_version':'vad-native-experiment-config/1','ssh_destination':'tester@fixture.invalid',
        'remote_workspace':'/home/tester/research','docker_image':'sha256:'+'f'*64,'source_subdir':'VAD',
        'data_subdir':'nuscenes','checkpoint_subpath':'weights/VAD_tiny.pth','checkpoint_sha256':'b'*64,
        'source_files_hash':'a'*64,'config_sha256':'c'*64,'resolved_config_hash':'d'*64,'native_adapter_source_hash':'e'*64,
        'weights_rights':'Authored unit-test fixture; no native GPU execution','confirm_trusted_native_code':True,
        'use_scope':'research_isolated','timeout_seconds':30}
    path=tmp_path/'configuration.json'
    path.write_text(json.dumps(value))
    monkeypatch.setenv('AXIOM_VAD_NATIVE_CONFIG',str(path))
    return value,path


def source(client,value):
    response=client.post('/api/v1/driving-sequences',json={'name':'Authored input contract',
        'research_isolated':True,'rights_note':'Authored fixture, no road/GPU acceptance','manifest':value})
    assert response.status_code==201,response.text
    return response.json()


def spec(sequence,**values):
    return ExperimentCreate(name='Authored native boundary',kind='driving_open_loop',adapter_id='vad-tiny-native-research',
        input={'driving_sequence_id':sequence['id'],'confirm_noncommercial_research':True},**values)


def test_original_input_json_import_and_research_scope(client,driving_input,tmp_path,monkeypatch):
    raw=json.dumps(driving_input,indent=3).encode()+b'\n'
    (tmp_path/'original-input.json').write_bytes(raw)
    monkeypatch.setenv('AXIOM_DRIVING_INPUT_ROOT',str(tmp_path))
    response=client.post('/api/v1/driving-sequences/import',json={'name':'Exact source format',
        'research_isolated':True,'rights_note':'Authored original bytes','relative_path':'original-input.json'})
    assert response.status_code==201,response.text
    value=response.json()
    assert value['native_file_identity_status']=='requires_native_file_check'
    assert value['commercial_use'] is False
    assert client.get(value['download_url']).content==raw
    body={'name':'Unsupported commercial scope','research_isolated':False,'rights_note':'Fixture','manifest':driving_input}
    assert client.post('/api/v1/driving-sequences',json=body).status_code==422


@pytest.mark.parametrize('path',['../outside.json','/etc/private.json','nested\\secret.json','input.txt'])
def test_managed_input_import_path_rejected(client,tmp_path,monkeypatch,path):
    monkeypatch.setenv('AXIOM_DRIVING_INPUT_ROOT',str(tmp_path))
    response=client.post('/api/v1/driving-sequences/import',json={'name':'Bad source','research_isolated':True,
        'rights_note':'fixture','relative_path':path})
    assert response.status_code==422


def test_import_symlink_and_missing_config_refused(client,driving_input,tmp_path,monkeypatch):
    path=tmp_path/'original.json'
    path.write_bytes(canonical(driving_input))
    (tmp_path/'link.json').symlink_to(path)
    monkeypatch.setenv('AXIOM_DRIVING_INPUT_ROOT',str(tmp_path))
    body={'name':'Input','research_isolated':True,'rights_note':'Fixture','relative_path':'link.json'}
    assert client.post('/api/v1/driving-sequences/import',json=body).status_code==422
    monkeypatch.delenv('AXIOM_DRIVING_INPUT_ROOT',raising=False)
    body['relative_path']='original.json'
    assert client.post('/api/v1/driving-sequences/import',json=body).status_code==422


def test_vad_not_configured_is_not_an_execution_fallback(monkeypatch):
    monkeypatch.delenv('AXIOM_VAD_NATIVE_CONFIG',raising=False)
    assert NativeVADExperimentHandler().metadata().status=='not_configured'
    with pytest.raises(ValueError,match='not configured'):
        load_configuration()


@pytest.mark.parametrize('change',{'mutable_image':('docker_image','profile:latest'),
    'host_command':('ssh_destination','-oProxyCommand=bad'),
    'checkpoint_escape':('checkpoint_subpath','../outside.pth'),
    'commercial':('use_scope','commercial'),
    'untrusted':('confirm_trusted_native_code',False),
    'hash':('checkpoint_sha256','invalid')}.values())
def test_operator_native_config_requires_complete_pins(tmp_path,monkeypatch,change):
    value,path=configuration(tmp_path,monkeypatch)
    value[change[0]]=change[1]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_configuration()


def test_single_checkpoint_and_explicit_noncommercial_only(client,driving_input,tmp_path,monkeypatch):
    configuration(tmp_path,monkeypatch)
    sequence=source(client,driving_input)
    handler=NativeVADExperimentHandler(database=client.app.state.db)
    prepared=handler.prepare(spec(sequence))
    assert base64.b64decode(prepared['input_base64'])==canonical(driving_input)
    with pytest.raises(ValueError,match='A/B'):
        handler.prepare(spec(sequence,comparison_kind='same_input_model_ab'))
    with pytest.raises(ValueError,match='single pinned'):
        handler.prepare(spec(sequence,model={'checkpoint_sha256':'c'*64}))
    with pytest.raises(ValueError,match='safety/GT'):
        handler.prepare(spec(sequence,evaluator={'id':'safety-pass'}))
    wrong=spec(sequence)
    wrong.input['confirm_noncommercial_research']=False
    with pytest.raises(ValueError,match='noncommercial'):
        handler.prepare(wrong)


@pytest.mark.parametrize('mode',['accepted','wrong_checkpoint','native_failure'])
def test_native_subprocess_receipt_boundary_never_reuses_old_output(client,driving_input,tmp_path,monkeypatch,mode):
    config,_=configuration(tmp_path,monkeypatch)
    sequence=source(client,driving_input)
    handler=NativeVADExperimentHandler(database=client.app.state.db)
    prepared=handler.prepare(spec(sequence))
    predicted=receipt(driving_input)
    predicted['model']['adapter_version']='axiom-vad-native/2'
    predicted['model']['weights_rights']=config['weights_rights']
    if mode=='wrong_checkpoint':
        predicted['model']['checkpoint_sha256']='9'*64
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        assert command[0]=='ssh' and '-o' in command
        if 'docker run' in command[-1]:
            kwargs['stdout'].write(b'Authored native-command test log\n')
            return SimpleNamespace(returncode=1 if mode=='native_failure' else 0)
        if 'tempfile.mkdtemp' in command[-1]:
            return SimpleNamespace(returncode=0,stdout=canonical({'remote_directory':config['remote_workspace']+'/evidence/queued-experiments/vad-unit',
                'input_sha256':prepared['manifest_sha256']}),stderr=b'')
        return SimpleNamespace(returncode=0,stdout=canonical(predicted),stderr=b'')
    monkeypatch.setattr(subprocess,'run',run)
    work=tmp_path/mode
    if mode=='accepted':
        output=handler.execute(prepared,work,lambda:False)
        assert output.summary['frame_count']==3 and output.summary['commercial_use'] is False
        assert output.summary['verification_verdict']=='not_evaluated_open_loop_research'
        assert output.summary['driving_outputs']==predicted['outputs']
        assert json.loads((work/'external-dispatch.json').read_bytes())['status']=='native_completed_receipt_validated'
    else:
        with pytest.raises((ValueError,RuntimeError)):
            handler.execute(prepared,work,lambda:False)
        assert not (work/'driving-result.json').exists()
        assert (work/'external-dispatch.json').exists()
    assert '--rm' in calls[1][-1] and ':ro' in calls[1][-1] and ':rw' in calls[1][-1]

