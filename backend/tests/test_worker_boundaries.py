"""Race boundaries between durable intent, actual dispatch and thread teardown."""

import asyncio
import threading

import pytest

from ade2e import jobs
from ade2e.adapters import Registry
from ade2e.database import Database
from ade2e.job_models import WorkerRow
from ade2e.main import create_app
from ade2e.reference import ReferenceAdapter
from ade2e.schemas import RunCreate, ScenarioCreate
from ade2e.service import execute_run, latest_run_details, scenario_create
from ade2e.worker import run_once


def test_cancel_after_intent_before_adapter_call_preserves_prior_pass(settings, monkeypatch):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    with db.session.begin() as session:
        scenario = scenario_create(session, ScenarioCreate(name="Core cancellation boundary", category="lane_follow"))
        body = RunCreate(scenario_id=scenario.id)
        old = execute_run(session, db, registry, body)
    assert old.verdict == "passed"

    class Counting(ReferenceAdapter):
        calls = 0

        def execute(self, scenario):
            self.calls += 1
            return super().execute(scenario)

    adapter = Counting()
    registry.register(adapter, replace=True)
    queued = jobs.submit_job(db, registry, body, "pre-call-cancel")
    original_mark = jobs.mark_dispatched

    def cancel_after_intent(db, claim):
        marked = original_mark(db, claim)
        jobs.cancel_job(db, claim.job.id)
        return marked

    monkeypatch.setattr(jobs, "mark_dispatched", cancel_after_intent)
    try:
        result = run_once(db, registry, "worker", job_id=queued.id)
        assert adapter.calls == 0
        assert result.status == "cancelled" and result.run_id is None and not result.outcome_uncertain
        assert result.dispatched_at is not None  # Preserve the durable intent history.
        assert "before the adapter call" in result.events[-1].message
        assert not any("Adapter acknowledged cancellation" in item.message for item in result.events)
        with db.session() as session:
            assert latest_run_details(session)[scenario.id].id == old.id
            assert jobs.latest_job_evidence(session) == {}
        assert jobs.list_resources(db)[0]["state"] == "idle"
    finally:
        db.engine.dispose()


def test_late_heartbeat_cannot_resurrect_finished_worker(settings, monkeypatch):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    class AwaitHeartbeat(ReferenceAdapter):
        def execute(self, scenario):
            assert entered.wait(3)
            return super().execute(scenario)

    registry.register(AwaitHeartbeat(), replace=True)
    with db.session.begin() as session:
        scenario = scenario_create(session, ScenarioCreate(name="Late worker heartbeat", category="lane_follow"))
    job = jobs.submit_job(db, registry, RunCreate(scenario_id=scenario.id), "late-heartbeat")
    original_heartbeat = jobs.heartbeat_job

    def delayed_heartbeat(*args):
        # Lease renewal completed while the job was active. Simulate the
        # thread being descheduled before its caller receives the result.
        renewed = original_heartbeat(*args)
        entered.set()
        try:
            assert release.wait(5)
            return renewed
        finally:
            exited.set()

    monkeypatch.setattr(jobs, "heartbeat_job", delayed_heartbeat)
    try:
        result = run_once(db, registry, "worker", lease_seconds=1)
        assert result.status == "completed"
        assert jobs.list_workers(db)[0]["state"] == "idle"
        jobs.touch_worker(db, "worker", "stopped")  # CLI final transition.
        release.set()
        assert exited.wait(2)
        for thread in threading.enumerate():
            if thread.name == "heartbeat-" + job.id:
                thread.join(2)
                assert not thread.is_alive()
        worker = jobs.list_workers(db)[0]
        assert worker["state"] == "stopped" and worker["job_id"] is None and not worker["alive"]
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "heartbeat-" + job.id:
                thread.join(2)
        db.engine.dispose()


def test_heartbeat_updates_only_current_running_worker_without_state_changes(settings):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    with db.session.begin() as session:
        scenario = scenario_create(session, ScenarioCreate(name="Conditional worker heartbeat"))
    job = jobs.submit_job(db, registry, RunCreate(scenario_id=scenario.id), "heartbeat")
    claim = jobs.claim_job(db, "worker", job_id=job.id)
    try:
        jobs.touch_worker(db, "worker", "running", job.id)
        with db.session.begin() as session:
            session.get(WorkerRow, "worker").heartbeat_at = "2000-01-01T00:00:00.000000+00:00"
        assert jobs.heartbeat_job(db, claim)
        assert jobs.list_workers(db)[0]["heartbeat_at"] != "2000-01-01T00:00:00.000000+00:00"
        jobs.touch_worker(db, "worker", "idle")
        idle = jobs.list_workers(db)[0]
        assert jobs.heartbeat_job(db, claim)
        assert jobs.list_workers(db)[0] == idle
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("cancel_lifespan", [False, True])
def test_shutdown_drains_active_lease_maintenance_before_disposing_engine(settings, monkeypatch, cancel_lifespan):
    app = create_app(settings, Registry(load_plugins=False))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real_expire, real_dispose = jobs.expire_jobs, app.state.db.engine.dispose
    disposed_while_active = []

    def slow_expire(db):
        entered.set()
        assert release.wait(5)
        try:
            return real_expire(db)
        finally:
            finished.set()

    def observe_dispose(*args, **kwargs):
        disposed_while_active.append(not finished.is_set())
        return real_dispose(*args, **kwargs)

    monkeypatch.setattr(jobs, "expire_jobs", slow_expire)
    monkeypatch.setattr(app.state.db.engine, "dispose", observe_dispose)

    async def check():
        exit_requested, shutdown_started = asyncio.Event(), asyncio.Event()

        async def lifespan():
            async with app.router.lifespan_context(app):
                await exit_requested.wait()
                shutdown_started.set()

        task = asyncio.create_task(lifespan())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            exit_requested.set()
            await shutdown_started.wait()
            # Yield once so the context manager enters its drain await. The
            # Event-held thread provides the synchronization, not this delay.
            await asyncio.sleep(0)
            assert not task.done()
            assert disposed_while_active == []
            if cancel_lifespan:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()  # Repeated cancellation must also preserve drain.
                await asyncio.sleep(0)
                assert not task.done()
                assert disposed_while_active == []
            release.set()
            if cancel_lifespan:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
            assert finished.is_set()
            assert disposed_while_active == [False]
        finally:
            release.set()
            if not task.done():
                try:
                    await asyncio.wait_for(task, 2)
                except asyncio.CancelledError:
                    pass

    asyncio.run(check())
