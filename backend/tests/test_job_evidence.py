"""Durable dispatch order controls evidence even when completion order differs."""

import pytest
from sqlalchemy import select

from ade2e import jobs
from ade2e.adapters import Registry
from ade2e.database import Database
from ade2e.job_models import JobRow
from ade2e.schemas import RequirementCreate, RunCreate, ScenarioCreate, ScenarioPatch
from ade2e.service import (compute_run, latest_run_details, overview, prepare_run, publish_run, requirement_create,
                           requirements_evaluated, scenario_create, scenario_patch)


@pytest.fixture
def evidence(settings):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    with db.session.begin() as session:
        scenario = scenario_create(session, ScenarioCreate(name="Evidence ordering", category="lane_follow"))
        requirement_create(session, RequirementCreate(name="No observed collision", metric_name="collision_count", operator="eq",
                                                       threshold=0, scenario_ids=[scenario.id]))
    yield db, registry, RunCreate(scenario_id=scenario.id)
    db.engine.dispose()


def _finish(db, registry, job):
    claim = jobs.claim_job(db, f"worker-{job.id}", job_id=job.id)
    assert claim is not None
    assert jobs.mark_dispatched(db, claim)
    computed = compute_run(claim.prepared, registry.get(job.adapter_id), run_id=claim.reserved_run_id, created_at=job.created_at)
    return jobs.finish_job(db, claim, computed)


def _assert_evidence(db, body, expected_run_id):
    with db.session() as session:
        latest = latest_run_details(session).get(body.scenario_id)
        requirement = requirements_evaluated(session)[0]
        cells = overview(session)["coverage"]
    assert (latest.id if latest else None) == expected_run_id
    assert requirement.status == ("passed" if expected_run_id else "untested")
    assert requirement.evidence_count == (1 if expected_run_id else 0)
    assert sum(cell["tested"] for cell in cells) == (1 if expected_run_id else 0)
    assert sum(cell["untested"] for cell in cells) == (0 if expected_run_id else 1)


def test_older_queued_job_finishing_last_never_overrides_newer_submission(evidence):
    db, registry, body = evidence
    older = jobs.submit_job(db, registry, body, "older")
    newer = jobs.submit_job(db, registry, body, "newer")
    assert older.submission_seq < newer.submission_seq
    completed_newer = _finish(db, registry, newer)
    _assert_evidence(db, body, completed_newer.run_id)
    completed_older = _finish(db, registry, older)
    assert completed_older.run_id != completed_newer.run_id
    _assert_evidence(db, body, completed_newer.run_id)


def test_latest_running_then_interrupted_job_blocks_older_pass(evidence):
    db, registry, body = evidence
    completed = _finish(db, registry, jobs.submit_job(db, registry, body, "passed"))
    _assert_evidence(db, body, completed.run_id)
    latest = jobs.submit_job(db, registry, body, "interrupted")
    claim = jobs.claim_job(db, "will-expire", job_id=latest.id)
    assert jobs.mark_dispatched(db, claim)
    _assert_evidence(db, body, None)
    with db.session.begin() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == latest.id))
        row.lease_expires_at = "2000-01-01T00:00:00.000000+00:00"
    assert jobs.expire_jobs(db) == 1
    assert jobs.job_get(db, latest.id).status == "interrupted"
    _assert_evidence(db, body, None)


@pytest.mark.parametrize("undispatched_outcome", ["queued", "queued_cancel", "claimed_cancel", "preflight_rejected"])
def test_undispatched_or_preflight_rejected_job_preserves_legacy_evidence(evidence, undispatched_outcome):
    db, registry, body = evidence
    # Represents a pre-schema-3 run with no corresponding queue job.
    with db.session() as session:
        prepared = prepare_run(session, registry, body)
    computed = compute_run(prepared, registry.get(body.adapter_id))
    with db.session.begin() as session:
        legacy = publish_run(session, db, prepared, computed)
    _assert_evidence(db, body, legacy.id)
    new = jobs.submit_job(db, registry, body, "not-executed")
    if undispatched_outcome == "queued_cancel":
        assert jobs.cancel_job(db, new.id).status == "cancelled"
    elif undispatched_outcome == "claimed_cancel":
        claim = jobs.claim_job(db, "cancel-before-dispatch", job_id=new.id)
        jobs.cancel_job(db, new.id)
        assert not jobs.mark_dispatched(db, claim)
    elif undispatched_outcome == "preflight_rejected":
        claim = jobs.claim_job(db, "unsupported", job_id=new.id)
        assert jobs.mark_dispatched(db, claim)
        jobs.fail_job(db, claim, "Category unsupported; no engine execution", failure_http_status=422)
    _assert_evidence(db, body, legacy.id)


def test_cancelled_after_dispatch_blocks_previous_pass(evidence):
    db, registry, body = evidence
    completed = _finish(db, registry, jobs.submit_job(db, registry, body, "passed"))
    _assert_evidence(db, body, completed.run_id)
    newer = jobs.submit_job(db, registry, body, "cancelled")
    claim = jobs.claim_job(db, "cancel-during-execution", job_id=newer.id)
    assert jobs.mark_dispatched(db, claim)
    jobs.cancel_job(db, newer.id)
    jobs.fail_job(db, claim, "Adapter acknowledged cancellation", status="cancelled")
    _assert_evidence(db, body, None)


def test_completed_job_still_requires_current_scenario_snapshot(evidence):
    db, registry, body = evidence
    queued = jobs.submit_job(db, registry, body, "old-snapshot")
    with db.session.begin() as session:
        scenario_patch(session, body.scenario_id, ScenarioPatch(parameters={"initial_gap_m": 90}))
    completed = _finish(db, registry, queued)
    with db.session() as session:
        latest = latest_run_details(session)[body.scenario_id]
        assert latest.id == completed.run_id and latest.scenario_revision == 1
        assert requirements_evaluated(session)[0].status == "untested"
        assert sum(cell["untested"] for cell in overview(session)["coverage"]) == 1
