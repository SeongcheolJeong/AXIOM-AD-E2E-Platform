#!/usr/bin/env python3
"""Bounded launcher for a dedicated GDB-child CARLA2300 and one observation.

Never attaches to an existing engine. Own child processes are stopped before
return; source/artifact files are preserved. No production pid is signalled.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from native_stop_observer_gdb import validate_owner, BINARY_SHA256
import hashlib


def launch(root, binary, *, sticky_damping=False, model_validation=False):
    root = root.resolve()
    model_selection = None
    if model_validation:
        if not sticky_damping:
            raise ValueError('Model validation requires explicit native damping selection')
        from carla_vehicle_model_validation import validate_selection_files
        model_selection = validate_selection_files(root)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError('Executable pin mismatch')
    for port in (2300, 2301, 2302):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
    if not (root / 'home').is_dir() or (root / 'observed').exists():
        raise ValueError('Fresh output and private user directory required')
    commands = [
        'set pagination off', 'set confirm off', 'set may-call-functions off',
        'set print thread-events off', 'set startup-with-shell off',
        'set inferior-tty /dev/null', 'handle SIGPIPE nostop noprint pass',
        'handle SIGTERM nostop noprint pass',
        'python import sys; sys.path.insert(0, ' + repr(str(root)) + ')',
        'source ' + str(root / 'native_stop_observer_gdb.py'),
        'source ' + str(root / 'native_physics_clock_gdb.py'),
        'python assert int(gdb.parse_and_eval("$axiom_stop_ready")) == 1 and int(gdb.parse_and_eval("$axiom_clock_ready")) == 1; gdb.execute("run")',
    ]
    intervention = None
    if sticky_damping:
        from sticky_damping_contract import validate_profile, canonical
        raw = (root / 'sticky-profile.json').read_bytes()
        intervention = validate_profile(json.loads(raw))
        if raw != canonical(intervention):
            raise ValueError('Noncanonical private intervention profile')
        commands[-1:] = [
            'source ' + str(root / 'native_sticky_damping_gdb.py'),
            'python assert int(gdb.parse_and_eval("$axiom_stop_ready")) == 1 and int(gdb.parse_and_eval("$axiom_clock_ready")) == 1 and int(gdb.parse_and_eval("$axiom_damping_ready")) == 1; gdb.execute("run")',
        ]
    cmd = ['gdb', '-q', '-nx', '-nh', '-batch', '-iex', 'set auto-load off']
    for command in commands:
        cmd += ['-ex', command]
    cmd += ['--args', str(binary), 'CarlaUE4', '-RenderOffScreen', '-nosound',
            '-quality-level=Low', '-ResX=480', '-ResY=270', '-carla-rpc-port=2300',
            '-userdir=' + str(root / 'home')]
    environment = {**os.environ, 'HOME': str(root / 'home'),
        'AXIOM_NATIVE_OBSERVER_ROOT': str(root), 'PYTHONDONTWRITEBYTECODE': '1'}
    outcome = {'schema_version': 'native-observer-launch/1', 'command': cmd,
               'status': 'starting', 'debugger_pid': None, 'child_pids': []}
    if intervention is not None:
        outcome['sticky_damping_intervention'] = intervention
    if model_selection is not None:
        outcome['model_validation'] = model_selection
    engine_children = []
    debugger = None
    with (root / 'gdb.log').open('xb') as log:
        try:
            debugger = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=environment)
            outcome['debugger_pid'] = debugger.pid
            (root / 'launcher-pid.json').write_text(json.dumps({'launcher': os.getpid(), 'debugger': debugger.pid}))
            deadline = time.monotonic() + 140
            ready = False
            while time.monotonic() < deadline and debugger.poll() is None:
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():
                        continue
                    try:
                        stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                        if int(stat[1]) == debugger.pid and b'-carla-rpc-port=2300' in (path / 'cmdline').read_bytes().split(b'\0'):
                            pid = int(path.name)
                            if pid not in engine_children:
                                validate_owner(pid, debugger.pid, root)
                                engine_children.append(pid)
                    except (OSError, ProcessLookupError):
                        continue
                try:
                    with socket.create_connection(('127.0.0.1', 2300), timeout=.3):
                        ready = True
                        break
                except OSError:
                    time.sleep(.5)
            if not ready:
                raise RuntimeError('Private engine failed to become ready; inspect gdb.log')
            print(json.dumps({'state': 'private_ready', 'debugger_pid': debugger.pid, 'children': engine_children}), flush=True)
            driver = [sys.executable, '-B', str(root / 'run_native_stop_observer_trial.py'),
                '--observer-root', str(root), '--debugger-pid', str(debugger.pid),
                '--label', 'observed', '--instrumented']
            if intervention is not None:
                driver.append('--sticky-damping')
            if model_selection is not None:
                driver.append('--model-validation')
            with (root / 'driver.log').open('xb') as output:
                result = subprocess.run(driver, stdout=output, stderr=subprocess.STDOUT,
                                        env=environment, timeout=290)
            outcome.update(status='completed' if result.returncode == 0 else 'trial_failed',
                           driver_returncode=result.returncode)
        except Exception as error:
            outcome.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        finally:
            outcome['child_pids'] = engine_children
            cleanup_errors = []
            for pid in engine_children:
                if Path('/proc', str(pid)).exists():
                    try:
                        validate_owner(pid, debugger.pid, root)
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except Exception as error:
                        # Never signal a pid whose identity can no longer be
                        # established. Still reap the debugger we launched.
                        cleanup_errors.append({'pid': pid, 'error': str(error)})
            if debugger is not None:
                try:
                    debugger.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    debugger.terminate()
                    try:
                        debugger.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        debugger.kill()
                        debugger.wait(timeout=5)
            # GDB can exit slightly before the child is reaped. Observe that
            # bounded interval; never signal a now-orphaned/unverified PID.
            reap_deadline = time.monotonic() + 5
            while any(Path('/proc', str(pid)).exists() for pid in engine_children) and time.monotonic() < reap_deadline:
                time.sleep(.1)
            remaining = [pid for pid in engine_children if Path('/proc', str(pid)).exists()]
            outcome['remaining_owned_pids'] = remaining
            outcome['cleanup_errors'] = cleanup_errors
            if remaining or cleanup_errors:
                outcome['status'] = 'cleanup_uncertain'
            with (root / 'launcher-result.json').open('x') as result_file:
                json.dump(outcome, result_file, indent=2)
    print(json.dumps(outcome), flush=True)
    return 0 if outcome['status'] == 'completed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--sticky-damping', action='store_true',
                        help='Explicit private-engine solver-row intervention, NOT read-only observation')
    parser.add_argument('--model-validation', action='store_true')
    args = parser.parse_args()
    raise SystemExit(launch(args.root, args.binary, sticky_damping=args.sticky_damping,
                           model_validation=args.model_validation))
