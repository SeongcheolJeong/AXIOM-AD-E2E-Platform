"""Optional queued external OpenDRIVE load/readback using the owned city endpoint."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.map_sources import resolve_conversion, reference, NATIVE_FIDELITY, QUALIFIER
from ade2e.simulation_catalog_schemas import MapDefinition
from .city import CityExperimentHandler, canonical, sha, file_sha
from . import city_native

VERSION = 'carla-map-qualification/1.0.0'


class MapQualificationHandler(CityExperimentHandler):
    def metadata(self):
        configured = all((self.db, self.target, self.python, self.script, self.directory, self.spool, self.owner))
        return ExperimentHandlerMetadata(id=QUALIFIER, version=VERSION, kinds=['object_sim'],
            status='available' if configured else 'not_configured', fidelity=NATIVE_FIDELITY,
            resource_id='carla-city-' + str(self.port), cancellation_supported=True,
            description='External XODR native load/readback only; no driving or physical sensor validation.')

    def prepare(self, spec):
        self.config()
        if (spec.kind != 'object_sim' or spec.comparison_kind != 'single' or
                any(value is not None for value in (spec.model, spec.sensor, spec.evaluator)) or
                set(spec.input) != {'source_ref', 'conversion_ref', 'server_version'}):
            raise ValueError('Map qualification pins source, conversion and engine version only')
        if not isinstance(spec.input['server_version'],str) or not 1<=len(spec.input['server_version'])<=200:
            raise ValueError('Map qualification requires a bounded engine build string')
        source, conversion, _ = resolve_conversion(self.db, spec.project_id,
            spec.input['source_ref'], spec.input['conversion_ref'])
        source_identity = dict(project_id=spec.project_id, source_ref=reference(source),
            conversion_ref=reference(conversion), original_sha256=source['source_sha256'],
            candidate_sha256=conversion['candidate_sha256'])
        return dict(schema_version='carla-map-qualification-input/1', project_id=spec.project_id,
            source_ref=reference(source), conversion_ref=reference(conversion), source=source_identity,
            candidate_sha256=conversion['candidate_sha256'], server_version=spec.input['server_version'],
            native_script_sha256=file_sha(city_native.__file__), adapter_script_sha256=file_sha(__file__),
            operator_transport_hash=self.transport_hash(), execution_fidelity=NATIVE_FIDELITY)

    def execute(self, snapshot, work_dir, should_cancel):
        self.config()
        if (snapshot.get('schema_version') != 'carla-map-qualification-input/1' or
                snapshot.get('operator_transport_hash') != self.transport_hash() or
                snapshot.get('native_script_sha256') != file_sha(city_native.__file__) or
                snapshot.get('adapter_script_sha256') != file_sha(__file__)):
            raise ValueError('External map runner or operator transport changed; create a new qualification')
        _, conversion, candidate = resolve_conversion(self.db, snapshot['project_id'],
            snapshot['source_ref'], snapshot['conversion_ref'])
        if conversion['candidate_sha256'] != snapshot['candidate_sha256']:
            raise ValueError('Pinned map candidate changed')
        if should_cancel():
            raise AdapterCancelled('Map qualification cancelled before native dispatch')
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        external_id = 'map_' + uuid.uuid4().hex
        remote_dir = self.directory.rstrip('/') + '/' + external_id
        journal = dict(schema_version='external-map-dispatch/1', external_execution_id=external_id,
            status='dispatching', remote_directory=remote_dir, source=snapshot['source'], cleanup_status='unknown')

        def record():
            (work / 'external-dispatch.json').write_bytes(canonical(journal))

        record()
        with (work / 'native.stderr.log').open('wb') as log, (work / 'native.stdout.json').open('wb') as output:
            process = subprocess.Popen(self.command(['--output', remote_dir, '--qualify-map']),
                stdin=subprocess.PIPE, stdout=output, stderr=log)
            process.stdin.write(canonical(snapshot | {'opendrive': candidate.decode('utf-8')}))
            process.stdin.close()
            started = time.monotonic()
            cancel_sent = False
            while process.poll() is None:
                if should_cancel() and not cancel_sent:
                    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                        self.target, shlex.join(['touch', remote_dir + '/cancel.request'])], capture_output=True, timeout=20)
                    cancel_sent = result.returncode == 0
                if time.monotonic() - started > 300:
                    process.terminate()
                    process.wait(timeout=15)
                    journal['status'] = 'outcome_unknown'
                    record()
                    raise RuntimeError('Native map qualification timed out; inspect exclusive endpoint cleanup')
                time.sleep(.15)
        if process.returncode:
            journal['status'] = 'failed_or_cancelled'
            result = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', self.target + ':' + remote_dir + '/state.json',
                                     str(work / 'native-state.json')], capture_output=True, timeout=30)
            if result.returncode == 0:
                state = json.loads((work / 'native-state.json').read_bytes())
                (work / 'map-native-diagnostics.json').write_bytes(canonical(state))
                journal.update(cleanup_status=state.get('cleanup_status'), native_error=state.get('error'))
                record()
                if state.get('status') == 'cancelled' and state.get('cleanup_status') == 'confirmed':
                    raise AdapterCancelled('Native map qualification cancelled; endpoint has no dynamic actors')
            record()
            # Existing worker diagnostic collection recognizes this suffix.
            (work / 'map-native-stderr.log').write_bytes((work / 'native.stderr.log').read_bytes()[-65536:])
            raise RuntimeError('Native map qualification failed: ' + (work / 'native.stderr.log').read_text(errors='replace')[-2000:])
        receipt = json.loads((work / 'native.stdout.json').read_bytes())
        if (receipt.get('schema_version') != 'carla-map-native-receipt/1' or receipt.get('status') != 'completed'
                or receipt.get('cleanup_status') != 'confirmed' or receipt.get('external_execution_id') != external_id
                or receipt.get('source') != snapshot['source']):
            raise ValueError('Native map receipt identity or cleanup mismatch')
        expected = {'map.xodr', 'map-catalog.json', 'map-qualification.json'}
        entries = receipt.get('artifacts', [])
        if len(entries) != 3 or {entry.get('name') for entry in entries} != expected:
            raise ValueError('Native map evidence closure differs')
        outputs = []
        for entry in entries:
            if (type(entry.get('size_bytes')) is not int or not 0 < entry['size_bytes'] <= 32 * 1024 * 1024
                    or not re.fullmatch('[a-f0-9]{64}', entry.get('sha256', ''))):
                raise ValueError('Native map artifact bound/hash invalid')
            path = work / entry['name']
            result = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', self.target + ':' + remote_dir + '/' + entry['name'],
                                     str(path)], capture_output=True, timeout=180)
            if result.returncode or path.stat().st_size != entry['size_bytes'] or file_sha(path) != entry['sha256']:
                raise ValueError('Native map evidence transfer/hash mismatch')
            outputs.append(ExperimentOutputFile(path, entry['name'], entry['media_type']))
        report = json.loads((work / 'map-qualification.json').read_bytes())
        definition = MapDefinition.model_validate_json((work / 'map-catalog.json').read_bytes())
        if (report.get('source_ref') != snapshot['source_ref'] or report.get('conversion_ref') != snapshot['conversion_ref']
                or report.get('input_opendrive_sha256') != snapshot['candidate_sha256']
                or report.get('native_opendrive_sha256') != snapshot['candidate_sha256']
                or report.get('native_runner_sha256') != snapshot['native_script_sha256']
                or report.get('map_catalog_sha256') != file_sha(work / 'map-catalog.json')
                or report.get('physical_validation') is not False or report.get('driving_evaluated') is not False
                or definition.server_version != snapshot['server_version'] or definition.opendrive.content.encode() != candidate
                or (work / 'map.xodr').read_bytes() != candidate):
            raise ValueError('Native map readback differs from pinned source or candidate')
        journal.update(status='completed', cleanup_status='confirmed')
        record()
        return ExperimentExecution(summary=report | {'source': snapshot['source'], 'native_ready': True,
            'editable': False, 'qualification': {'ready': True, 'scope': 'native_map_load_readback_not_driving'}},
            artifacts=outputs, fidelity=NATIVE_FIDELITY)


def create_map_qualification_handler(*, database=None):
    return MapQualificationHandler(database=database)
