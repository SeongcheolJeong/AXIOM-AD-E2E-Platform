"""Normal product API for frozen experiments, execution and native downloads."""
from fastapi import Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow, ExperimentArtifactRow
from .experiment_schemas import ExperimentCreate, ExperimentSubmit
from .job_models import JobRow
from .team_access import project_authorize

def register_experiment_routes(api, db, registry):
    def access_experiment(request, identity, role='Viewer'):
        with db.read_session() as session:
            row = session.get(ExperimentRow, identity)
            if row is None:
                raise HTTPException(404, 'Experiment not found')
            project = row.project_id
        return project_authorize(request, db, project, role)

    def access_execution(request, identity, role='Viewer'):
        with db.read_session() as session:
            row = session.get(ExperimentExecutionRow, identity)
            if row is None:
                raise HTTPException(404, 'Experiment execution not found')
            project = row.project_id
        return project_authorize(request, db, project, role)

    @api.get('/experiments/capabilities', tags=['experiments'])
    def capabilities():
        return {'schema_version': 1, 'handlers': [item.model_dump(mode='json') for item in registry.list()],
                'comparison_types': ['single', 'same_input_model_ab', 'same_raw_isp_ab', 'same_scene_sensor_ab', 'same_scene_fault_ab', 'same_fault_dut_ab'],
                'limits': {'manifest_bytes': experiment_jobs.MANIFEST_LIMIT, 'native_files': experiment_jobs.MAX_NATIVE_FILES,
                           'input_snapshot_bytes': experiment_jobs.INPUT_MANIFEST_LIMIT,
                           'summary_bytes': experiment_jobs.SUMMARY_LIMIT,
                           'native_file_bytes': experiment_jobs.ARTIFACT_LIMIT, 'bundle_bytes': experiment_jobs.TOTAL_ARTIFACT_LIMIT},
                'execution_status_is_evaluation_verdict': False,
                'project_scope': 'new_experiment_scenario_sequence_and_replay_domains_only'}

    @api.post('/experiments/preview', tags=['experiments'])
    def preview(body: ExperimentCreate, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        prepared = experiment_jobs.prepare(registry, body)
        value = prepared.to_payload()
        return {'preview_hash': prepared.input_hash, 'prepared': value, 'job_count': 1,
                'resource_id': value['handler']['resource_id'], 'execution_fidelity': value['input'].get('execution_fidelity',value['handler']['fidelity']),
                'requires_explicit_submit': True, 'evaluation_status': 'not_evaluated'}

    @api.post('/experiments', status_code=201, tags=['experiments'])
    def create(body: ExperimentCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        return experiment_jobs.create(db, registry, body, idempotency_key)

    @api.get('/experiments', tags=['experiments'])
    def experiments(request: Request, project_id: str = 'default', limit: int = Query(default=50, ge=1, le=100)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(ExperimentRow).where(ExperimentRow.project_id == project_id)
                                   .order_by(ExperimentRow.created_at.desc(), ExperimentRow.id).limit(limit))
            return {'items': [{'id': row.id, 'project_id': row.project_id, 'name': row.name, 'kind': row.kind,
                              'created_at': row.created_at, 'manifest_hash': row.manifest_hash} for row in rows]}

    @api.get('/experiments/executions', tags=['experiments'])
    def execution_list(request: Request, project_id: str = 'default', limit: int = Query(default=50, ge=1, le=100),
                       before_seq: int | None = Query(default=None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            query = select(JobRow, ExperimentExecutionRow, ExperimentRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.job_id == JobRow.id).join(
                ExperimentRow, ExperimentRow.id == ExperimentExecutionRow.experiment_id).where(
                ExperimentExecutionRow.project_id == project_id, JobRow.kind == 'experiment').order_by(JobRow.submission_seq.desc())
            if before_seq is not None:
                query = query.where(JobRow.submission_seq < before_seq)
            rows = session.execute(query.limit(limit + 1)).all()
            return {'items': [{'id': job.id, 'experiment_id': subject.experiment_id, 'name': experiment.name,
                              'experiment_kind': experiment.kind, 'status': job.status, 'fidelity': subject.fidelity,
                              'input_hash': job.input_hash, 'manifest_hash': subject.manifest_hash, 'created_at': job.created_at}
                             for job, subject, experiment in rows[:limit]],
                    'next_before_seq': rows[limit - 1][0].submission_seq if len(rows) > limit else None}

    @api.get('/experiments/{experiment_id}', tags=['experiments'])
    def get(experiment_id: str, request: Request):
        access_experiment(request, experiment_id)
        with db.read_session() as session:
            row, value, _ = experiment_jobs.experiment_get(session, experiment_id)
            executions = list(session.scalars(select(ExperimentExecutionRow).where(
                ExperimentExecutionRow.experiment_id == experiment_id)))
            return value | {'manifest_hash': row.manifest_hash,
                            'executions': [experiment_jobs.detail(session, session.scalar(select(JobRow).where(
                                JobRow.id == execution.job_id))).model_dump(mode='json') for execution in executions]}

    @api.post('/experiments/{experiment_id}/executions', status_code=202, tags=['experiments'])
    def submit(experiment_id: str, body: ExperimentSubmit, request: Request,
               idempotency_key: str = Header(alias='Idempotency-Key')):
        access_experiment(request, experiment_id, 'Engineer')
        return experiment_jobs.submit(db, registry, experiment_id, body.preview_hash, idempotency_key)

    @api.get('/experiments/executions/{job_id}', tags=['experiments'])
    def execution(job_id: str, request: Request):
        access_execution(request, job_id)
        return jobs.job_get(db, job_id, kind='experiment')

    @api.post('/experiments/executions/{job_id}/cancel', tags=['experiments'])
    def cancel(job_id: str, request: Request):
        access_execution(request, job_id, 'Engineer')
        return jobs.cancel_job(db, job_id, kind='experiment')

    @api.get('/experiments/executions/{job_id}/export', tags=['experiments'])
    def export(job_id: str, request: Request):
        access_execution(request, job_id)
        with db.read_session() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == job_id))
            experiment_jobs.detail(session, row)
            subject = session.get(ExperimentExecutionRow, job_id)
            if subject.manifest is None:
                raise HTTPException(409, 'Execution is not terminal; no immutable receipt is available')
            return Response(subject.manifest, media_type='application/json',
                            headers={'Content-Disposition': f'attachment; filename="{job_id}.json"',
                                     'X-Content-SHA256': subject.manifest_hash})

    @api.get('/experiments/executions/{job_id}/artifacts/{artifact_id}', tags=['experiments'])
    def artifact(job_id: str, artifact_id: str, request: Request):
        access_execution(request, job_id)
        with db.read_session() as session:
            row = session.get(ExperimentArtifactRow, artifact_id)
            if row is None or row.job_id != job_id:
                raise HTTPException(404, 'Experiment artifact not found')
            path = experiment_jobs.artifact_path(db, row)
            return FileResponse(path, media_type=row.media_type, filename=row.name,
                                headers={'X-Content-SHA256': row.sha256})
