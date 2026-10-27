"""Author-only tests with prescribed evidence; no CARLA or physical trial."""
from copy import deepcopy
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BASE_SOURCES = ('carla_brake_protocol.py', 'diagnose_carla_brake_response.py', 'diagnose_carla_self_hit.py')
NEW_SOURCES = (*BASE_SOURCES, 'carla_throttle_isolation_protocol.py', 'diagnose_carla_throttle_isolation.py')


def imported_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def author(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    original_import = builtins.__import__

    def no_engine_import(name, *args, **kwargs):
        assert name.split('.')[0] not in {'carla', 'axiom_carla', 'ade2e'}, 'Author imported an engine'
        return original_import(name, *args, **kwargs)

    def no_engine_io(*args, **kwargs):
        pytest.fail('Author attempted network or process I/O')

    monkeypatch.setattr(builtins, '__import__', no_engine_import)
    monkeypatch.setattr(socket, 'socket', no_engine_io)
    monkeypatch.setattr(subprocess, 'Popen', no_engine_io)
    return imported_file('tested_throttle_isolation_author', ROOT / 'scripts/prepare_carla_throttle_isolation_intent.py')


@pytest.fixture(scope='module')
def prescribed_reference(tmp_path_factory):
    """Seal a fake 225-step source using the real offline validation contract.

    Positions, speeds and rotating fields are authored independently of a plant;
    they test admission and provenance only, never vehicle response validity.
    """
    sys.path.insert(0, str(ROOT / 'scripts'))
    helpers = imported_file('isolation_author_evidence_helpers', ROOT / 'scripts/tests/test_analyze_carla_brake_response.py')
    from carla_brake_protocol import (SETTLED_VERSION, VERSION, canonical, make_protocol,
        measurement_sequence, sha, settled_preparation_gate, settled_preparation_step, spatial_check)
    root = tmp_path_factory.mktemp('authored-isolation-reference')
    directory, sources, original, intent, report = helpers.fixture(root, initialization='throttle', protocol_version=VERSION)
    protocol = make_protocol('throttle', version=SETTLED_VERSION)
    intent.update(protocol=protocol, protocol_sha256=sha(canonical(protocol)),
        sequence_sha256=sha(canonical(measurement_sequence(protocol))))
    report.update(protocol_sha256=intent['protocol_sha256'], sequence_sha256=intent['sequence_sha256'],
        intent_sha256=sha(canonical(intent)), status='failed',
        error={'type': 'RuntimeError', 'message': 'First entry-plane crossing failed the fixed settled admission gate'})
    report['installed_module_hashes'] = {name: 'b' * 64 for name in (
        'axiom_carla.startup', 'axiom_carla.vehicle_response', 'axiom_carla.native_journal',
        'axiom_carla.spool', 'axiom_carla.version')}
    report['inventory_refresh_ticks'] = 0
    spawn = intent['spawn_transform']
    role = report['spawn']['ego']['returned_identity']['role_name']
    first = deepcopy(report['rest']['observations'][-1])
    first.update(index=0, phase='preparation')
    first['bounds'] = spatial_check(first['ego']['position_m'], first['ego']['rotation_deg'],
        first['ego']['velocity_mps'], spawn, phase='preparation', protocol=protocol)
    prep = {'status': 'running', 'mode': 'throttle', 'velocity_request': None, 'commands': [],
        'observations': [first], 'pi_steps': [], 'pi_state': None,
        'entry_trace': [settled_preparation_gate(protocol, [first], spawn)]}
    for i in range(225):
        proposed = settled_preparation_step(protocol, prep['observations'][-1], prep['pi_state'])
        speed = min(1.9, (i + 1) * .02)
        acceleration = (speed - prep['observations'][-1]['signed_heading_speed_mps']) / .05
        row = helpers.observation(i + 1, 'preparation', first['world_frame'] + i + 1,
            speed, proposed['control'], spawn, role, acceleration=acceleration, protocol=protocol)
        position = 12.077 * ((i + 1) / 225) ** 2
        row['ego']['position_m']['x'] = position
        row['ego']['snapshot_transform']['position_m']['x'] = position
        row['ego']['snapshot_transform']['matrix_native'][0][3] = position
        row['bounds'] = spatial_check(row['ego']['position_m'], row['ego']['rotation_deg'],
            row['ego']['velocity_mps'], spawn, phase='preparation', protocol=protocol)
        row['reported_control']['data']['gear'] = 1
        row['telemetry']['scalars']['engine_rpm'] = helpers.available(500.)
        row['telemetry']['wheels'] = [{'index': w, 'fields': {'omega': helpers.available(6.)}} for w in range(4)]
        prep['commands'].append(helpers.command(proposed['control'], i, i * .05, prep['observations'][-1], row))
        prep['observations'].append(row)
        prep['pi_steps'].append(proposed['trace'])
        prep['pi_state'] = proposed['state']
        prep['entry_trace'].append(settled_preparation_gate(protocol, prep['observations'], spawn))
    prep['entry_gate'] = deepcopy(prep['entry_trace'][-1])
    assert prep['entry_gate']['first_crossing_index'] == 225 and prep['entry_gate']['admitted'] is False
    report['preparation'] = prep
    report['measurement'] = {'expected_commands': 80, 'expected_observations': 81, 'observations': [],
        'commands': [helpers.command(s['control'], s['step'], s['time_s']) for s in measurement_sequence(protocol)]}
    report['cleanup']['observed'] = deepcopy(report['original'])
    helpers.seal(directory, intent, report)
    helpers.analysis.load_trial(directory, sources, original)
    return root, directory, sources, original, helpers


@pytest.fixture
def inputs(tmp_path, prescribed_reference):
    root, directory, sources, original, helpers = prescribed_reference
    reference = tmp_path / 'authored-reference'
    shutil.copytree(root, reference)
    args = SimpleNamespace(reference_capture=reference / directory.relative_to(root),
        reference_source_dir=reference / sources.relative_to(root), source_evidence=reference / original.relative_to(root),
        source_dir=tmp_path / 'new-captured-sources', output=tmp_path / 'intent.json', branch='continue_pi')
    args.source_dir.mkdir()
    for name in NEW_SOURCES:
        (args.source_dir / name).write_bytes(('# New authored source descriptor, never executed: ' + name).encode())
    return args, helpers


def reseal(args, helpers, mutation):
    intent = json.loads((args.reference_capture / 'source-intent.json').read_bytes())
    report = json.loads((args.reference_capture / 'report.json').read_bytes())
    mutation(report, intent)
    helpers.seal(args.reference_capture, intent, report)


def file_snapshot(directory):
    return {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}


@pytest.mark.parametrize('branch', ['continue_pi', 'hold_throttle'])
def test_author_exact_source_preservation_new_identity_and_private_exclusive_output(author, inputs, branch):
    args, _ = inputs
    args.branch = branch
    before = file_snapshot(args.reference_capture.parent) | file_snapshot(args.source_dir)
    source_intent = json.loads((args.reference_capture / 'source-intent.json').read_bytes())
    result = author.prepare(args)
    raw = args.output.read_bytes()
    intent = json.loads(raw)
    assert intent['schema_version'] == 'carla-throttle-isolation-intent/1'
    assert intent['experiment_id'] != source_intent['experiment_id']
    for key in ('expected_world', 'expected_store_id', 'engine_build', 'adapter_version',
                'spawn_transform', 'requested_weather', 'expected_physics'):
        assert intent[key] == source_intent[key]
    assert intent['source_evidence'] == {'path': str(args.source_evidence.resolve()),
        'sha256': hashlib.sha256(args.source_evidence.read_bytes()).hexdigest()}
    assert intent['source_files'] == {name: hashlib.sha256((args.source_dir / name).read_bytes()).hexdigest() for name in NEW_SOURCES}
    assert result['engine_actions'] == 0 and result['intent_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['reference_experiment_id'] == source_intent['experiment_id']
    assert result['reference_artifact_hashes'] == {name: hashlib.sha256((args.reference_capture / name).read_bytes()).hexdigest()
        for name in ('source-intent.json', 'report.json', 'events.jsonl', 'reservation-release.json')}
    assert stat.S_IMODE(args.output.stat().st_mode) == 0o600
    assert before == file_snapshot(args.reference_capture.parent) | file_snapshot(args.source_dir)


def test_existing_output_is_not_overwritten_even_if_valid_reference_exists(author, inputs):
    args, _ = inputs
    args.output.write_bytes(b'original pinned intent\x00\n')
    before = (args.output.read_bytes(), args.output.stat().st_ino, args.output.stat().st_mtime_ns)
    with pytest.raises(FileExistsError):
        author.prepare(args)
    assert before == (args.output.read_bytes(), args.output.stat().st_ino, args.output.stat().st_mtime_ns)


@pytest.mark.parametrize('mutation,match', [
    (lambda r, i: r['physics_configuration']['scalars']['mass'].update(value=1846.), 'post-rest physics'),
    (lambda r, i: r['original'].update(world_id='123'), 'original world'),
    (lambda r, i: r['cleanup'].update(observed={}), 'cleanup'),
    (lambda r, i: r['cleanup']['errors'].append('destroy unconfirmed'), 'cleanup'),
    (lambda r, i: r['callback_shutdown'].update(active=1, drained=False), 'callback'),
    (lambda r, i: r['callback_shutdown'].update(started=1, finished=0), 'callback'),
    (lambda r, i: r.update(collision_callbacks=[{'authored': 'queued contact'}]), 'contact'),
])
def test_resealed_failed_capture_is_not_admissible_merely_because_hashes_verify(author, inputs, mutation, match):
    args, helpers = inputs
    reseal(args, helpers, mutation)
    with pytest.raises(ValueError, match=match):
        author.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize('kind', ['missing_receipt', 'wrong_source', 'wrong_physics_file', 'changed_capture'])
def test_incomplete_or_modified_reference_never_falls_back(author, inputs, kind):
    args, _ = inputs
    if kind == 'missing_receipt':
        (args.reference_capture / 'reservation-release.json').unlink()
    elif kind == 'wrong_source':
        (args.reference_source_dir / BASE_SOURCES[0]).write_bytes(b'# different source')
    elif kind == 'wrong_physics_file':
        args.source_evidence.write_bytes(args.source_evidence.read_bytes() + b' ')
    else:
        report = args.reference_capture / 'report.json'
        data = json.loads(report.read_bytes()); data['preparation']['commands'][100]['control']['throttle'] = .3
        report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        author.prepare(args)
    assert not args.output.exists()


def test_short_but_internally_verified_failure_is_not_the_declared_225_step_reference(author, inputs):
    args, helpers = inputs

    def shorten(report, intent):
        prep = report['preparation']
        prep['commands'] = prep['commands'][:-1]
        prep['observations'] = prep['observations'][:-1]
        prep['pi_steps'] = prep['pi_steps'][:-1]
        prep['pi_state'] = deepcopy(prep['pi_steps'][-1]['state_after'])
        prep['entry_trace'] = prep['entry_trace'][:-1]
        prep['entry_gate'] = deepcopy(prep['entry_trace'][-1])
        report['error']['message'] = 'Authored interrupted preparation prefix'

    reseal(args, helpers, shorten)
    # A valid old partial failure is readable, but is not an isolation input.
    result, _ = helpers.analysis.load_trial(args.reference_capture, args.reference_source_dir, args.source_evidence)
    assert result['integrity_status'] == 'verified'
    with pytest.raises(ValueError, match='225 commands and 226 observations'):
        author.prepare(args)
    assert not args.output.exists()


def test_resealed_wrong_ack_actor_is_rejected_before_output(author, inputs):
    args, helpers = inputs
    reseal(args, helpers, lambda report, intent:
        report['preparation']['commands'][100]['rpc_response'].update(actor_id=102))
    with pytest.raises(ValueError, match='ACK actor'):
        author.prepare(args)
    assert not args.output.exists()


def test_valid_legacy_capture_remains_readable_but_cannot_author_this_experiment(author, inputs, tmp_path):
    args, helpers = inputs
    legacy = tmp_path / 'authored-legacy'
    legacy.mkdir()
    args.reference_capture, args.reference_source_dir, args.source_evidence, _, _ = helpers.fixture(legacy)
    with pytest.raises(ValueError, match='protocol3 throttle coast'):
        author.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize('raw', [b'{"schema_version": "x", "schema_version": "y"}', b'{"value": NaN}'])
def test_ambiguous_reference_json_is_rejected_before_output(author, inputs, raw):
    args, _ = inputs
    (args.reference_capture / 'source-intent.json').write_bytes(raw)
    with pytest.raises(ValueError, match='Duplicate JSON key|Nonfinite JSON'):
        author.prepare(args)
    assert not args.output.exists()


def test_repeated_authoring_preserves_same_inputs_with_distinct_experiment_ids(author, inputs):
    args, _ = inputs
    author.prepare(args)
    first_bytes = args.output.read_bytes()
    first_path = args.output
    first = json.loads(first_bytes)
    args.output = args.output.with_name('second-intent.json')
    author.prepare(args)
    second = json.loads(args.output.read_bytes())
    assert first.pop('experiment_id') != second.pop('experiment_id')
    assert first == second and first_path.read_bytes() == first_bytes


@pytest.mark.parametrize('name', NEW_SOURCES)
def test_all_five_new_captured_sources_are_required_without_checkout_fallback(author, inputs, name):
    args, _ = inputs
    (args.source_dir / name).unlink()
    with pytest.raises(ValueError):
        author.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize('target', ['reference', 'new_source'])
def test_symlink_sources_are_rejected(author, inputs, target):
    args, _ = inputs
    path = args.reference_capture / 'report.json' if target == 'reference' else args.source_dir / NEW_SOURCES[-1]
    original = path.with_suffix('.original')
    path.rename(original); path.symlink_to(original)
    with pytest.raises(ValueError):
        author.prepare(args)
    assert original.exists() and not args.output.exists()


def test_cli_requires_explicit_branch_and_returns_zero_engine_actions(author, inputs, monkeypatch, capsys):
    args, _ = inputs
    cli = ['prepare_carla_throttle_isolation_intent.py', '--reference-capture', str(args.reference_capture),
        '--reference-source-dir', str(args.reference_source_dir), '--source-evidence', str(args.source_evidence),
        '--source-dir', str(args.source_dir), '--output', str(args.output)]
    monkeypatch.setattr(sys, 'argv', cli)
    with pytest.raises(SystemExit) as caught:
        author.main()
    assert caught.value.code == 2 and not args.output.exists()
    monkeypatch.setattr(sys, 'argv', [*cli, '--branch', 'hold_throttle'])
    author.main()
    assert json.loads(capsys.readouterr().out)['engine_actions'] == 0


def test_new_source_byte_budget_checked_before_output(author, inputs):
    args, _ = inputs
    (args.source_dir / NEW_SOURCES[-1]).write_bytes(b'#' * (256 * 1024 + 1))
    with pytest.raises(ValueError, match='bounded size'):
        author.prepare(args)
    assert not args.output.exists()
