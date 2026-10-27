"""Worker startup/poll/log boundaries; no retained workspace or simulator is used."""
import logging
from types import SimpleNamespace

import pytest

from ade2e import experiment_defaults, experiment_runtime, worker
from ade2e.worker_supervision import PrivateRotatingFileHandler, configure_worker_logging, run_polling_loop


class Stop:
    def __init__(self, *, after_waits=None):
        self.stopped = False
        self.waits = []
        self.after_waits = after_waits
        self.now = 0.0

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        if self.after_waits is not None and len(self.waits) >= self.after_waits:
            self.set()
        return self.stopped


@pytest.fixture
def isolated_main(monkeypatch):
    state = SimpleNamespace(disposals=0, initialized=0, touches=[])

    def dispose():
        state.disposals += 1

    def initialize():
        state.initialized += 1

    state.db = SimpleNamespace(initialize=initialize, engine=SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(worker, 'Database', lambda _: state.db)
    monkeypatch.setattr(worker, 'Settings', lambda: object())
    for name in ('Registry', 'RuntimeRegistry', 'SensorDecoderRegistry', 'EvaluatorRegistry', 'StackRegistry'):
        monkeypatch.setattr(worker, name, lambda: object())
    monkeypatch.setattr(worker, 'configure_worker_logging', lambda *args, **kwargs: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(worker.jobs, 'touch_worker', lambda db, identity, status: state.touches.append(status))
    monkeypatch.setattr('sys.argv', ['ade2e.worker'])
    return state


def test_missing_installed_plugin_fails_before_ready_or_claim(isolated_main, monkeypatch, caplog, capsys):
    calls = []

    class MissingEditablePlugin:
        def load(self):
            calls.append('load')
            raise ModuleNotFoundError("No module named 'axiom_engineering'", name='axiom_engineering')

    monkeypatch.setattr(experiment_runtime, 'entry_points', lambda **_: [MissingEditablePlugin()])
    monkeypatch.setattr(worker, 'run_once', lambda *args, **kwargs: pytest.fail('Must not claim a job'))
    assert worker.main() == 1
    assert calls == ['load']
    assert isolated_main.touches == [] and isolated_main.disposals == 1
    assert 'ready' not in capsys.readouterr().out
    assert 'axiom_engineering' in caplog.text and 'restart the worker' in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_cli_constructs_handler_inventory_once_and_reuses_it(isolated_main, monkeypatch, capsys):
    inventory, loaded, observed = object(), [], []
    stop = Stop(after_waits=3)
    monkeypatch.setattr(worker.threading, 'Event', lambda: stop)
    monkeypatch.setattr(experiment_defaults, 'default_experiment_registry', lambda db: loaded.append(db) or inventory)
    monkeypatch.setattr(worker, 'run_once', lambda *args, **kwargs: observed.append(kwargs['experiment_registry']))
    assert worker.main() == 0
    assert loaded == [isolated_main.db] and observed == [inventory] * 3
    assert isolated_main.touches == ['idle', 'stopped'] and isolated_main.disposals == 1
    assert 'AXIOM worker ready:' in capsys.readouterr().out


@pytest.mark.parametrize('fails,expected', [(False, 0), (True, 1)])
def test_once_returns_nonzero_only_for_poll_failure(isolated_main, monkeypatch, fails, expected):
    monkeypatch.setattr('sys.argv', ['ade2e.worker', '--once'])
    monkeypatch.setattr(experiment_defaults, 'default_experiment_registry', lambda db: object())
    calls = []

    def poll(*args, **kwargs):
        calls.append(True)
        if fails:
            raise RuntimeError('temporary database failure')

    monkeypatch.setattr(worker, 'run_once', poll)
    assert worker.main() == expected
    assert calls == [True]
    assert isolated_main.touches == ['idle', 'stopped'] and isolated_main.disposals == 1


def test_poll_backoff_caps_suppresses_tracebacks_and_stops(caplog):
    stop = Stop(after_waits=12)
    calls = []

    def poll():
        calls.append(True)
        raise RuntimeError('database busy')

    with caplog.at_level(logging.INFO):
        assert run_polling_loop(poll, stop, poll_interval=.5, logger=logging.getLogger('test.poll'),
                                clock=lambda: stop.now) == 0
    assert stop.waits == [.5, 1, 2, 4, 8, 16, 32, 60, 60, 60, 60, 60]
    assert len(calls) == 12
    assert sum(bool(record.exc_info) for record in caplog.records) == 1
    assert 'repeated tracebacks suppressed' in caplog.text
    assert len(caplog.records) < len(calls)


def test_success_resets_backoff_and_reports_recovery(caplog):
    stop = Stop(after_waits=5)
    outcomes = iter([RuntimeError('busy'), RuntimeError('busy'), None, RuntimeError('busy'), None])

    def poll():
        outcome = next(outcomes)
        if outcome:
            raise outcome

    with caplog.at_level(logging.INFO):
        assert run_polling_loop(poll, stop, poll_interval=.5, logger=logging.getLogger('test.poll')) == 0
    assert stop.waits == [.5, 1, .5, .5, .5]
    assert 'recovered after 2 consecutive failures' in caplog.text
    assert 'recovered after 1 consecutive failures' in caplog.text
    assert sum(bool(record.exc_info) for record in caplog.records) == 2


def test_import_failure_is_permanent_and_never_waits(caplog):
    stop = Stop()

    def poll():
        raise ModuleNotFoundError("No module named 'broken_plugin'")

    assert run_polling_loop(poll, stop, poll_interval=.5, logger=logging.getLogger('test.poll')) == 1
    assert stop.waits == [] and 'no automatic retry' in caplog.text


def test_already_stopped_never_polls_or_waits():
    stop = Stop()
    stop.set()
    assert run_polling_loop(lambda: pytest.fail('Must not poll'), stop, poll_interval=.5,
                            logger=logging.getLogger('test.poll')) == 0
    assert stop.waits == []


def test_rotating_log_retains_only_bounded_private_files(tmp_path):
    target = tmp_path / 'worker.log'
    handler = PrivateRotatingFileHandler(target, maxBytes=1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    try:
        for index in range(100):
            handler.emit(logging.LogRecord('test.log', logging.ERROR, __file__, 1, '%03d %s',
                                           (index, 'x' * 200), None))
    finally:
        handler.close()
    files = sorted(tmp_path.iterdir())
    assert [path.name for path in files] == ['worker.log', 'worker.log.1', 'worker.log.2', 'worker.log.3']
    assert all(path.stat().st_size <= 1024 for path in files)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert '099 ' in target.read_text() and '000 ' not in ''.join(path.read_text() for path in files)


def test_worker_log_refuses_symlink_and_nonprivate_file(tmp_path):
    target = tmp_path / 'public.log'
    target.write_text('preserve')
    target.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'):
        PrivateRotatingFileHandler(target, maxBytes=1024, backupCount=3)
    link = tmp_path / 'linked.log'
    link.symlink_to(target)
    with pytest.raises(OSError):
        PrivateRotatingFileHandler(link, maxBytes=1024, backupCount=3)
    assert target.read_text() == 'preserve'


def test_oversized_unicode_record_obeys_byte_limit(tmp_path):
    target = tmp_path / 'worker.log'
    handler = PrivateRotatingFileHandler(target, maxBytes=1024, backupCount=3, encoding='utf-8')
    try:
        for _ in range(6):
            handler.emit(logging.LogRecord('test.log', logging.ERROR, __file__, 1, '오류' * 2000, (), None))
    finally:
        handler.close()
    assert len(list(tmp_path.iterdir())) == 4
    assert all(path.stat().st_size <= 1024 for path in tmp_path.iterdir())
    assert '[log record truncated]' in target.read_text()


@pytest.mark.parametrize('interval', [0, -.5, float('nan'), float('inf'), 61])
def test_poll_loop_rejects_invalid_intervals(interval):
    with pytest.raises(ValueError, match='poll_interval'):
        run_polling_loop(lambda: None, Stop(), poll_interval=interval, logger=logging.getLogger('test.poll'))


@pytest.mark.parametrize('max_bytes,backups', [(0, 3), (1024, 0), (1024, 21)])
def test_log_configuration_cannot_disable_bounds(tmp_path, max_bytes, backups):
    with pytest.raises(ValueError, match='max_bytes'):
        configure_worker_logging(tmp_path / 'worker.log', max_bytes=max_bytes, backups=backups)
    assert list(tmp_path.iterdir()) == []
