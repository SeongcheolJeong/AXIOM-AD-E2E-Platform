#!/usr/bin/env python3
"""One bounded native input-isolation diagnostic; not a brake calibration.

Replay the pinned preparation requests, validate fresh PI proposals, then run
one declared 40-command continuation. Importing this module performs no I/O.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path

import carla_throttle_isolation_protocol as protocol
from diagnose_carla_brake_response import (
    BrakeDiagnostic, Recorder, DT, MAX_INTENT_BYTES, MAX_REPORT_BYTES,
    MAX_JOURNAL_BYTES, command_row, require_control_readback,
)
from diagnose_carla_self_hit import canonical, sha, exclusive_write, require, utc, best_effort_event

REPORT_VERSION = 'carla-throttle-isolation-diagnostic/1'


def verify_reference(intent, directory):
    """Check the four pinned original files before optional native imports."""
    require(directory.is_dir() and not directory.is_symlink(), 'Reference capture must be an ordinary directory')
    limits = {'source-intent.json': MAX_INTENT_BYTES, 'report.json': MAX_REPORT_BYTES,
              'events.jsonl': MAX_JOURNAL_BYTES, 'reservation-release.json': 64 * 1024}
    hashes, blobs = {}, {}
    for name, limit in limits.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'Reference artifact is absent or a symlink: ' + name)
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        require(0 < len(raw) <= limit, 'Reference artifact byte bound exceeded: ' + name)
        hashes[name] = sha(raw)
        require(hashes[name] == intent['reference']['artifacts'][name], 'Reference artifact hash differs: ' + name)
        blobs[name] = raw
    reference = protocol.verify_reference_capture(blobs)
    require(canonical(reference) == canonical(intent['reference']), 'Reference reconstruction differs from the pinned intent')
    return reference


class ThrottleIsolationDiagnostic(BrakeDiagnostic):
    """Share actor ownership and teardown; keep this experiment's DTO separate."""
    def __init__(self, intent, **dependencies):
        super().__init__(intent, **dependencies)
        self.reference = intent['reference']
        count = self.protocol['prefix_steps']
        self.report.update(schema_version=protocol.report_version(self.protocol),
            isolation_protocol=deepcopy(self.protocol), reference=deepcopy(self.reference),
            scope='One input-isolation diagnostic; no settled-entry admission, brake-response calibration, or platform Run.')
        if self.protocol['schema_version'] == protocol.SUBSTEP_VERSION:
            self.report['continuation_physics'] = {'status': 'not_attempted',
                'requested': deepcopy(self.protocol['continuation_physics']), 'before': None,
                'target': None, 'after': None, 'handoff_frame': None, 'after_frame': None,
                'before_time_s': None, 'after_time_s': None, 'api_returned_frame': None}
        self.report['preparation'] = {
            'status': 'not_started', 'mode': 'throttle', 'recipe': 'captured_requests_with_fresh_pi_validation',
            'expected_commands': count, 'expected_observations': count + 1,
            'commands': [command_row(control, step=index, time_s=index * DT)
                         for index, control in enumerate(self.reference['controls'])],
            'observations': [], 'velocity_request': None, 'pi_steps': [], 'pi_state': None,
            'initial_gate': None, 'handoff_gate': None,
        }
        experiment_specific = (
            'This diagnostic applies fixed input sequences,',
            'Both preparation modes request',
            'Target velocity API return is',
        )
        self.report['limitations'] = [item for item in self.report['limitations']
            if not item.startswith(experiment_specific)]
        self.report['limitations'].extend([
            'The captured preparation requests are fixed; the declared continuation either computes fresh PI requests or holds one request.',
            'One physical throttle preparation prefix is used; no direct target-velocity initialization is permitted.',
            'The reference failed its original settled-entry gate; this protocol does not change that verdict.',
            'A matched handoff constrains observed state only, not hidden drivetrain state or tire forces.',
            'Hold uses the last original requested throttle, not a cached float32 readback.',
        ])

    def initial_measurement(self):
        count = self.protocol['continuation_steps']
        return {'expected_commands': count, 'expected_observations': count + 1,
                'commands': [command_row(None, step=index, time_s=index * DT) for index in range(count)],
                'observations': [], 'control_steps': [], 'pi_state': None,
                'handoff_gate': None, 'entry_observation_frame': None}

    def observation_bounds(self, position, rotation, velocity, phase):
        return protocol.spatial_check(position, rotation, velocity, self.intent['spawn_transform'],
                                      phase=phase, protocol=self.protocol)

    def handoff(self, observations):
        return protocol.handoff_gate(self.protocol, self.reference, observations,
            len(self.report['rest']['trace']), self.report['inventory_refresh_ticks'])

    def prepare(self, snapshot):
        prep = self.report['preparation']
        prep['status'] = 'running'
        initial = self.observe(snapshot, 'preparation', prep['observations'])
        # The pure initial-state comparison is supplied by the isolation
        # protocol, including its explicit rotating=False rest interpretation.
        initial_gate = protocol.compare_entry(self.protocol, initial, self.reference['initial_observation'], rotating=False)
        prep['initial_gate'] = deepcopy(initial_gate)
        self.recorder.event('isolation_initial_gate', initial_gate)
        require(initial_gate['admitted'] is True, 'Fresh preparation initial state differs from the pinned reference')
        for index, row in enumerate(prep['commands']):
            proposed = protocol.prefix_step(self.protocol, self.reference, index, prep['observations'][-1], prep['pi_state'])
            step = {'command_index': index, **deepcopy(proposed)}
            prep['pi_steps'].append(step)
            self.recorder.event('isolation_prefix_step', step)
            require(proposed['matched'] is True, 'Fresh PI proposal differs from the recorded float32 request')
            require(canonical(proposed['control']) == canonical(row['control']), 'Prefix request differs from original bytes')
            self.send_control(snapshot, row)
            snapshot = self.tick(snapshot, 'isolation_prefix')
            observed = self.observe(snapshot, 'preparation', prep['observations'])
            self.link_transition(row, observed)
            prep['pi_state'] = deepcopy(proposed['state'])
        gate = self.handoff(prep['observations'])
        prep['handoff_gate'] = deepcopy(gate)
        self.recorder.event('isolation_handoff', gate)
        require(gate['admitted'] is True, 'Fresh prefix handoff differs from the pinned reference')
        prep['status'] = 'passed'
        return snapshot

    def measure(self, snapshot):
        prep, measurement = self.report['preparation'], self.report['measurement']
        initial = self.observe(snapshot, 'measurement', measurement['observations'])
        require(prep['status'] == 'passed' and prep['observations'], 'Isolation continuation requires a matched completed prefix')
        previous = prep['observations'][-1]
        prior_gate = self.handoff(prep['observations'])
        require(prior_gate == prep['handoff_gate'] and prior_gate['admitted'] is True, 'Stored isolation handoff changed')
        for key in ('world_frame', 'source_time_s', 'delta_seconds', 'ego', 'signed_heading_speed_mps', 'bounds'):
            require(initial[key] == previous[key], 'Isolation F0 differs from the completed prefix: ' + key)
        require_control_readback(prep['commands'][-1]['control'], initial)
        gate = self.handoff([*prep['observations'][:-1], initial])
        measurement['handoff_gate'] = deepcopy(gate)
        measurement['entry_observation_frame'] = initial['world_frame']
        self.recorder.event('isolation_measurement_handoff', {'handoff_gate': gate, 'entry_observation_frame': initial['world_frame']})
        require(gate['admitted'] is True, 'Fresh isolation F0 no longer matches the pinned reference')
        measurement['pi_state'] = deepcopy(prep['pi_state'])
        for index, row in enumerate(measurement['commands']):
            proposed = protocol.extension_step(self.protocol, index, measurement['observations'][-1],
                measurement['pi_state'], self.reference['controls'][-1])
            row['control'] = deepcopy(proposed['control'])
            step = {'command_index': index, **deepcopy(proposed)}
            measurement['control_steps'].append(step)
            self.recorder.event('isolation_extension_step', step)
            self.send_control(snapshot, row)
            if index == 0 and self.protocol['schema_version'] == protocol.SUBSTEP_VERSION:
                snapshot = self.apply_continuation_physics(snapshot)
            else:
                snapshot = self.tick(snapshot, 'isolation_continuation')
            observed = self.observe(snapshot, 'measurement', measurement['observations'])
            self.link_transition(row, observed)
            measurement['pi_state'] = deepcopy(proposed['state'])
        require(len(measurement['observations']) == self.protocol['continuation_steps'] + 1,
                'Isolation continuation observation denominator differs')
        self.check_collisions()
        self.report['status'] = 'collected'

    def apply_continuation_physics(self, snapshot):
        """Account for ApplySettings' internal tick as the first ACKed interval.

        A 0.01s sham and 0.005s treatment both call the same native API. No
        implicit frame is discarded, and an unexpected gap is a failed trial.
        """
        evidence = self.report['continuation_physics']
        requested = self.protocol['continuation_physics']
        evidence['handoff_frame'] = snapshot.frame
        evidence['before_time_s'] = snapshot.timestamp.elapsed_seconds
        before = self.field_values(self.read(self.world.get_settings), self.native['SETTINGS'])
        evidence['before'] = deepcopy(before)
        values = before['values']
        require(values['synchronous_mode'] is True and abs(values['fixed_delta_seconds'] - DT) <= 1e-9
            and values['substepping'] is True and abs(values['max_substep_delta_time'] - .01) <= 1e-9
            and values['max_substeps'] == 10, 'Unexpected pre-override physics settings')
        settings = self.read(self.world.get_settings)
        settings.max_substep_delta_time = requested['max_substep_delta_time_s']
        settings.max_substeps = requested['max_substeps']
        target = deepcopy(before)
        target['values']['max_substep_delta_time'] = requested['max_substep_delta_time_s']
        require(self.field_values(settings, self.native['SETTINGS']) == target,
                'Continuation changes a setting other than the declared substep')
        evidence['target'] = target
        evidence['status'] = 'mutation_intent'
        # A failed RPC may have applied the exact target. Cleanup accepts only
        # pinned original/before/target values, never arbitrary fresh readback.
        self.report['requested_settings'] = deepcopy(target)
        try:
            evidence['api_returned_frame'] = self.event_call('world.apply_settings.continuation_substep',
                lambda: self.world.apply_settings(settings), {'handoff_frame': snapshot.frame, **deepcopy(requested)})
            current = self.read(self.world.get_snapshot)
            evidence.update(after_frame=current.frame, after_time_s=current.timestamp.elapsed_seconds,
                after=self.field_values(self.read(self.world.get_settings), self.native['SETTINGS']))
            require(evidence['after'] == target, 'Continuation physics readback differs')
            require(current.frame == snapshot.frame + requested['expected_advanced_frames']
                and abs(current.timestamp.elapsed_seconds - snapshot.timestamp.elapsed_seconds - DT) <= 1e-6,
                'Settings transition did not advance exactly one recorded interval')
            evidence['status'] = 'applied_and_read_back'
            self.recorder.event('isolation_continuation_physics', deepcopy(evidence))
            return current
        except Exception as error:
            evidence.update(status='failed_or_uncertain', error={'type': type(error).__name__, 'message': str(error)[:1000]})
            best_effort_event(self.recorder, self.report.setdefault('journal_errors', []),
                'isolation_continuation_physics_failed', deepcopy(evidence))
            raise

    def cleanup_settings_candidates(self):
        candidates = super().cleanup_settings_candidates()
        evidence = self.report.get('continuation_physics') or {}
        if evidence.get('target') is not None and evidence.get('before') is not None:
            candidates = (*candidates, evidence['before'], evidence['target'])
        return candidates


def run(args):
    require(args.host == '127.0.0.1' and args.port == 2000, 'Only the declared local native endpoint is allowed')
    require(args.intent.is_file() and not args.intent.is_symlink(), 'Intent must be an ordinary file')
    with args.intent.open('rb') as stream:
        raw = stream.read(MAX_INTENT_BYTES + 1)
    require(len(raw) <= MAX_INTENT_BYTES, 'Intent exceeds byte cap')
    intent = protocol.parse_intent(raw)
    require(raw == canonical(intent), 'Intent must use exact canonical protocol bytes')
    require(not Path(__file__).is_symlink(), 'Diagnostic entry point must not be a symlink')
    here = Path(__file__).resolve().parent
    for name, digest in intent['source_files'].items():
        source = here / name
        require(source.is_file() and not source.is_symlink() and sha(source.read_bytes()) == digest,
                'Diagnostic source hash differs or is a symlink: ' + name)
    verify_reference(intent, args.reference_capture)
    require(os.getenv('ADE2E_CARLA_EXCLUSIVE') == '1', 'Explicit dedicated exclusive native environment required')
    execution_root = args.runtime_dir / 'executions'
    require(execution_root.is_dir() and not execution_root.is_symlink(), 'Existing execution store required')
    store_path = execution_root / 'store-id'
    require(store_path.is_file() and not store_path.is_symlink()
            and store_path.read_text().strip() == intent['expected_store_id'], 'Store UUID differs')
    # No CARLA import or engine operation precedes the pinned input checks.
    import carla
    from axiom_carla.version import ADAPTER_VERSION
    from axiom_carla.spool import file_lock
    from axiom_carla.native_journal import fields, SETTINGS, WEATHER
    from axiom_carla.startup import StartupPolicy, _actor_state
    from axiom_carla.vehicle_response import _control, _telemetry, _physics
    require(ADAPTER_VERSION == '0.8.0', 'This diagnostic requires the pinned optional adapter0.8.0')
    require(set(intent['expected_world']['settings']['values']) == set(SETTINGS)
        and set(intent['expected_world']['weather']['values']) == set(WEATHER)
        and set(intent['requested_weather']['values']) == set(WEATHER), 'Pinned world field set differs')
    installed_hashes = {name: sha(Path(importlib.import_module(name).__file__).read_bytes()) for name in protocol.MODULES}
    require(installed_hashes == intent['reference']['installed_module_hashes'],
            'Installed native module hashes differ from the reference; no engine mutation allowed')
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    exclusive_write(args.output / 'source-intent.json', raw)
    recorder = Recorder(args.output)
    native = dict(fields=fields, SETTINGS=SETTINGS, WEATHER=WEATHER, StartupPolicy=StartupPolicy,
        _actor_state=_actor_state, _control=_control, _telemetry=_telemetry, _physics=_physics)
    diagnostic = ThrottleIsolationDiagnostic(intent, carla=carla, client=carla.Client(args.host, args.port),
        output=args.output, execution_root=execution_root, recorder=recorder, native=native)
    diagnostic.report['installed_module_hashes'] = installed_hashes
    recorder.event('diagnostic_started', {key: diagnostic.report[key] for key in
        ('experiment_id', 'intent_sha256', 'protocol_sha256', 'sequence_sha256', 'source_files')})
    result = 1
    try:
        with file_lock(execution_root / 'engine.lock'):
            try:
                require(not diagnostic.quarantine.exists() and not diagnostic.quarantine.is_symlink(), 'Existing quarantine forbids diagnostic')
                snapshot = diagnostic.preflight()
                snapshot = diagnostic.rest(snapshot)
                snapshot = diagnostic.prepare(snapshot)
                diagnostic.measure(snapshot)
            except Exception as error:
                diagnostic.report['error'] = {'type': type(error).__name__, 'message': str(error)[:1400]}
                best_effort_event(recorder, diagnostic.report.setdefault('journal_errors', []), 'diagnostic_error', diagnostic.report['error'])
            finally:
                diagnostic.cleanup()
            result = diagnostic.finalize()
    except Exception as error:
        if not diagnostic.changed:
            diagnostic.callback_gate.stop_accepting()
            diagnostic.callbacks_drained = diagnostic.callback_gate.drain(diagnostic.policy['callback_drain_seconds'])
        if not (args.output / 'report.json').exists():
            diagnostic.report.update(status='failed', error={'type': type(error).__name__, 'message': str(error)[:1400]},
                reservation_retained=diagnostic.reservation_owned, release_eligible=False, finished_at=utc(),
                event_chain={'entries': recorder.sequence, 'head_sha256': recorder.previous, 'sealed': False})
            content = canonical(diagnostic.report)
            require(len(content) <= MAX_REPORT_BYTES, 'Failure report exceeds byte cap')
            exclusive_write(args.output / 'report.json', content)
        raise
    finally:
        if diagnostic.callbacks_drained:
            recorder.stream.close()
    print(json.dumps({'status': diagnostic.report['status'], 'experiment_id': intent['experiment_id'],
        'observations': len(diagnostic.report['measurement']['observations']),
        'cleanup': diagnostic.report['cleanup']['status'], 'reservation_retained': diagnostic.reservation_owned,
        'output': str(args.output)}, sort_keys=True))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent', type=Path, required=True)
    parser.add_argument('--reference-capture', type=Path, required=True)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2000)
    return run(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
