"""Optional product-queue dispatch to the existing isolated native VAD runner.

Only operator configuration selects SSH host, immutable Docker image and trusted
source/checkpoint. User-authored experiment JSON cannot select commands or paths.
No Torch/MMCV/nuScenes imports enter the core/API process.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time

from ade2e.driving_contract import PROFILE, SOURCE_COMMIT, canonical, content_hash, validate_result

ADAPTER_ID='vad-tiny-native-research'
ADAPTER_VERSION='axiom-vad-experiment/1'
CONFIG_VERSION='vad-native-experiment-config/1'
MAX_RESULT_BYTES=256*1024
CONFIG_FIELDS={'schema_version','ssh_destination','remote_workspace','docker_image','source_subdir','data_subdir',
    'checkpoint_subpath','checkpoint_sha256','source_files_hash','config_sha256','resolved_config_hash',
    'native_adapter_source_hash','weights_rights','confirm_trusted_native_code','use_scope','timeout_seconds'}


def _sha(value):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None


def _fragment(value):
    return (isinstance(value,str) and bool(re.fullmatch('[A-Za-z0-9_./-]+',value))
        and not PurePosixPath(value).is_absolute() and '..' not in PurePosixPath(value).parts)


def load_configuration():
    selected=os.environ.get('AXIOM_VAD_NATIVE_CONFIG','').strip()
    if not selected:
        raise ValueError('Operator AXIOM_VAD_NATIVE_CONFIG is not configured')
    path=Path(selected)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024:
        raise ValueError('Native VAD configuration must be a bounded regular JSON file')
    value=json.loads(path.read_bytes())
    if not isinstance(value,dict) or set(value)!=CONFIG_FIELDS or value['schema_version']!=CONFIG_VERSION:
        raise ValueError('Unsupported native VAD configuration fields/version')
    if not isinstance(value['ssh_destination'],str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*',value['ssh_destination']):
        raise ValueError('Native SSH destination must be a fixed operator user@host')
    workspace=value['remote_workspace']
    if not isinstance(workspace,str) or not re.fullmatch('/[A-Za-z0-9_./-]+',workspace) or '..' in PurePosixPath(workspace).parts:
        raise ValueError('Native remote workspace must be an explicit safe absolute path')
    if not isinstance(value['docker_image'],str) or not re.fullmatch('sha256:[a-f0-9]{64}',value['docker_image']):
        raise ValueError('Native container must use an immutable image digest')
    for name in ('source_subdir','data_subdir','checkpoint_subpath'):
        if not _fragment(value[name]):
            raise ValueError('Native source/data/checkpoint must stay inside the configured workspace')
    for name in ('checkpoint_sha256','source_files_hash','config_sha256','resolved_config_hash','native_adapter_source_hash'):
        if not _sha(value[name]):
            raise ValueError('Native model/source/config requires pinned SHA-256 identities')
    if (value['confirm_trusted_native_code'] is not True or value['use_scope']!='research_isolated'
            or not isinstance(value['weights_rights'],str) or not value['weights_rights'].strip()
            or len(value['weights_rights'])>2048 or type(value['timeout_seconds']) is not int
            or not 30<=value['timeout_seconds']<=600):
        raise ValueError('Native trust, research scope, weights rights and bounded timeout must be explicit')
    return value


def _ssh(configuration,command):
    return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',configuration['ssh_destination'],shlex.join(command)]


CREATE_REMOTE_SCRIPT='''import base64,hashlib,json,os,pathlib,sys,tempfile
workspace=pathlib.Path(sys.argv[1]).resolve()
value=json.loads(sys.stdin.buffer.read(512*1024))
raw=base64.b64decode(value["input_base64"],validate=True)
if len(raw)>256*1024 or hashlib.sha256(raw).hexdigest()!=value["manifest_sha256"]:
 raise ValueError("Retained input hash mismatch")
root=workspace/"evidence"/"queued-experiments"
if root.is_symlink():raise ValueError("Symlink execution store rejected")
root.mkdir(parents=True,exist_ok=True)
directory=pathlib.Path(tempfile.mkdtemp(prefix="vad-",dir=str(root)))
os.chmod(str(directory),0o700)
(directory/"driving-input.json").write_bytes(raw)
print(json.dumps({"remote_directory":str(directory),"input_sha256":hashlib.sha256(raw).hexdigest()}))
'''

READ_REMOTE_SCRIPT='''import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve()/"evidence"/"queued-experiments"
directory=pathlib.Path(sys.argv[2])
if directory.is_symlink():raise ValueError("Symlink execution result rejected")
directory.resolve().relative_to(root)
path=directory/"driving-result.json"
if path.is_symlink() or not path.is_file() or path.stat().st_size>256*1024:raise ValueError("Native receipt missing or oversized")
raw=path.read_bytes()
sys.stdout.buffer.write(raw)
'''


class NativeVADExperimentHandler:
    def __init__(self,database=None):
        self.db=database

    def metadata(self):
        from ade2e.experiment_runtime import ExperimentHandlerMetadata
        try:
            load_configuration()
            status='available'
            description='Configured SSH/container native VAD-Tiny open-loop research dispatch; exact file checks run at the native data root'
        except (ValueError,OSError,TypeError):
            status='not_configured'
            description='Configure the isolated native VAD SSH/container profile; no native output fallback'
        return ExperimentHandlerMetadata(id=ADAPTER_ID,version=ADAPTER_VERSION,kinds=['driving_open_loop'],
            status=status,fidelity='learned_camera_temporal_open_loop_research',resource_id='carla',
            cancellation_supported=False,description=description)

    def prepare(self,spec):
        from ade2e.driving_sequences import driving_sequence_get
        configuration=load_configuration()
        if self.db is None:
            raise ValueError('Native VAD handler requires the core managed database')
        if spec.project_id!='default':
            raise ValueError('Native nuScenes research assets are restricted to the default isolated workspace')
        if spec.kind!='driving_open_loop' or spec.comparison_kind!='single':
            raise ValueError('Only a single pinned VAD checkpoint is configured; policy A/B and closed loop are unavailable')
        if set(spec.input)!={'driving_sequence_id','confirm_noncommercial_research'} or spec.input['confirm_noncommercial_research'] is not True:
            raise ValueError('Select driving_sequence_id and explicitly confirm isolated noncommercial research')
        if spec.sensor or spec.evaluator:
            raise ValueError('Native VAD does not execute sensor substitutions or driving safety/GT evaluators')
        if spec.model and spec.model!={'profile':PROFILE,'checkpoint_sha256':configuration['checkpoint_sha256']}:
            raise ValueError('Model selection differs from the single pinned native VAD checkpoint/profile')
        with self.db.read_session() as session:
            row,sequence=driving_sequence_get(session,spec.input['driving_sequence_id'])
            raw=row.manifest
        return {'schema_version':'vad-native-experiment-input/1','driving_sequence_id':sequence['id'],
            'manifest_sha256':sequence['manifest_sha256'],'input_hash':sequence['input_hash'],
            'input_base64':base64.b64encode(raw).decode('ascii'),'configuration':configuration,
            'configuration_hash':content_hash(configuration),'source_commit':SOURCE_COMMIT,'seed':spec.seed,
            'research_isolated':True,'commercial_use':False,
            'preflight':sequence['preflight'],'native_file_identity_status':'requires_native_file_check'}

    def execute(self,snapshot,work_dir,should_cancel):
        from ade2e.adapters import AdapterCancelledBeforeDispatch
        from ade2e.driving_sequences import decode_manifest,driving_sequence_get
        from ade2e.experiment_runtime import ExperimentExecution,ExperimentOutputFile
        configuration=load_configuration()
        if (configuration!=snapshot['configuration'] or content_hash(configuration)!=snapshot['configuration_hash']
                or snapshot['source_commit']!=SOURCE_COMMIT or snapshot['research_isolated'] is not True or snapshot['commercial_use'] is not False):
            raise ValueError('Pinned native VAD operator configuration or research scope changed after submission')
        raw=base64.b64decode(snapshot['input_base64'],validate=True)
        value=decode_manifest(raw)
        if hashlib.sha256(raw).hexdigest()!=snapshot['manifest_sha256'] or content_hash(value)!=snapshot['input_hash']:
            raise ValueError('Prepared driving input differs from its retained original bytes/hash')
        with self.db.read_session() as session:
            current,sequence=driving_sequence_get(session,snapshot['driving_sequence_id'])
            if current.manifest!=raw or sequence['input_hash']!=snapshot['input_hash']:
                raise ValueError('Managed driving sequence changed after submission')
        if should_cancel():
            raise AdapterCancelledBeforeDispatch('Cancelled before remote native VAD dispatch')
        work_dir=Path(work_dir)
        work_dir.mkdir(parents=True,exist_ok=True)
        (work_dir/'driving-input.json').write_bytes(raw)
        started=time.monotonic()
        create=subprocess.run(_ssh(configuration,['python3','-c',CREATE_REMOTE_SCRIPT,configuration['remote_workspace']]),
            input=canonical({'input_base64':snapshot['input_base64'],'manifest_sha256':snapshot['manifest_sha256']}),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
        if create.returncode or len(create.stdout)>4096:
            (work_dir/'native-setup.stderr').write_bytes(create.stderr[-1024*1024:])
            raise RuntimeError('Native VAD source-transfer setup failed; inspect the owned execution diagnostics')
        location=json.loads(create.stdout)
        remote=location['remote_directory']
        if (not isinstance(remote,str) or not re.fullmatch(re.escape(configuration['remote_workspace'])+'/evidence/queued-experiments/vad-[A-Za-z0-9_-]+',remote)
                or location['input_sha256']!=snapshot['manifest_sha256']):
            raise ValueError('Remote native execution store returned an unexpected source identity/path')
        command=['docker','run','--rm','--gpus','all','--shm-size','512m',
            '-v',configuration['remote_workspace']+':/work:ro','-v',remote+':/output:rw',
            '-e','PYTHONPATH=/work/backend/src:/work/integrations/vad/src','--workdir','/work',configuration['docker_image'],
            'python','-m','axiom_vad','run','--source','/work/'+configuration['source_subdir'],
            '--data-root','/work/'+configuration['data_subdir'],'--checkpoint','/work/'+configuration['checkpoint_subpath'],
            '--input','/output/driving-input.json','--output','/output/driving-result.json','--seed',str(snapshot['seed']),
            '--weights-rights',configuration['weights_rights'],'--confirm-trusted-native-code']
        dispatch={'schema_version':'native-vad-dispatch/1','adapter_id':ADAPTER_ID,'adapter_version':ADAPTER_VERSION,
            'remote_directory':remote,'configuration_hash':snapshot['configuration_hash'],'input_hash':snapshot['input_hash'],
            'original_manifest_sha256':snapshot['manifest_sha256'],'command_sha256':content_hash(command),
            'status':'dispatch_requested','remote_cleanup_confirmed':False,'research_isolated':True,'commercial_use':False}
        (work_dir/'external-dispatch.json').write_bytes(canonical(dispatch))
        with (work_dir/'native-run.stdout').open('xb') as stdout,(work_dir/'native-run.stderr').open('xb') as stderr:
            try:
                result=subprocess.run(_ssh(configuration,command),stdout=stdout,stderr=stderr,
                    timeout=configuration['timeout_seconds'],check=False)
            except subprocess.TimeoutExpired:
                dispatch['status']='outcome_unknown_native_timeout'
                (work_dir/'external-dispatch.json').write_bytes(canonical(dispatch))
                raise RuntimeError('Native VAD dispatch timed out; remote outcome is unknown and no retry was performed')
        if result.returncode:
            dispatch['status']='native_command_failed'
            dispatch['returncode']=result.returncode
            (work_dir/'external-dispatch.json').write_bytes(canonical(dispatch))
            raise RuntimeError('Native VAD command failed; no synthetic or previous receipt was substituted')
        receipt=subprocess.run(_ssh(configuration,['python3','-c',READ_REMOTE_SCRIPT,configuration['remote_workspace'],remote]),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
        if receipt.returncode or len(receipt.stdout)>MAX_RESULT_BYTES:
            raise RuntimeError('Native VAD receipt retrieval failed; remote evidence is retained and outcome remains uncertain')
        predicted=json.loads(receipt.stdout)
        validate_result(predicted)
        model=predicted['model']
        expected={'source_commit':SOURCE_COMMIT,'source_files_hash':configuration['source_files_hash'],
            'adapter_source_hash':configuration['native_adapter_source_hash'],'checkpoint_sha256':configuration['checkpoint_sha256'],
            'config_sha256':configuration['config_sha256'],'resolved_config_hash':configuration['resolved_config_hash'],
            'adapter_version':'axiom-vad-native/2','profile':PROFILE,'weights_rights':configuration['weights_rights']}
        if (model!=expected or predicted['input']!=value or predicted['input_hash']!=snapshot['input_hash']
                or predicted['execution']['seed']!=snapshot['seed']):
            raise ValueError('Native model/config/checkpoint/input/seed receipt differs from the approved experiment')
        # Lease ownership is checked before publication; an expired worker cannot
        # turn the externally retained receipt into an accepted product result.
        should_cancel()
        (work_dir/'driving-result.json').write_bytes(receipt.stdout)
        dispatch['status']='native_completed_receipt_validated'
        dispatch['receipt_sha256']=hashlib.sha256(receipt.stdout).hexdigest()
        dispatch['remote_cleanup_confirmed']=True
        dispatch['cleanup_scope']='docker --rm process returned; remote evidence directory intentionally retained'
        (work_dir/'external-dispatch.json').write_bytes(canonical(dispatch))
        summary={'schema_version':'vad-native-experiment-result/1','profile':PROFILE,'research_isolated':True,
            'commercial_use':False,'input_hash':snapshot['input_hash'],'original_manifest_sha256':snapshot['manifest_sha256'],
            'receipt_sha256':dispatch['receipt_sha256'],'frame_count':len(predicted['outputs']),
            'camera_observations':6*len(predicted['outputs']),'model':model,'native_execution':predicted['execution'],
            'dispatch_elapsed_ms':(time.monotonic()-started)*1000,'configuration_hash':snapshot['configuration_hash'],
            'origin_verification':'platform_dispatched_pinned_native_runner_over_authenticated_ssh',
            'native_file_identity_status':'verified_at_native_data_root','verification_verdict':'not_evaluated_open_loop_research',
            'driving_outputs':predicted['outputs'],'limitations':predicted['limitations']+
                ['One approved checkpoint; no policy A/B evaluator or closed-loop camera feedback is configured.',
                 'Native processing time excludes model loading; dispatch time includes transfer/container/model loading. Neither is a real-time acceptance.']}
        return ExperimentExecution(summary=summary,fidelity='learned_camera_temporal_open_loop_research',
            artifacts=[ExperimentOutputFile(path=work_dir/name,name=name,media_type='application/json' if name.endswith('.json') else 'text/plain')
                for name in ('driving-input.json','driving-result.json','external-dispatch.json','native-run.stdout','native-run.stderr')])

