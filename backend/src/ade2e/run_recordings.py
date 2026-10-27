"""Import existing MCAP artifacts without losing their run provenance."""
import hashlib
from threading import RLock

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy import select

from .mcap_ingest import RecordingLimitError, RecordingValidationError
from .database import ArtifactRow, Database
from .recording_models import RecordingRow
from .recording_schemas import RecordingDetail, RecordingImportMetadata
from .recordings import _managed_path, import_recording
from .run_recording_models import RecordingOriginRow
from .schemas import Artifact, StrictModel
from .service import completed_run_integrity_error, digest, run_get, utcnow
from .simulation_context import validate_prepared_context


class RunRecordingImport(StrictModel):
    artifact_id: str = Field(min_length=1, max_length=64)


class RecordingOrigin(StrictModel):
    recording_id: str
    run_id: str
    artifact_id: str
    created_at: str
    definition_hash: str
    snapshot: dict


def _origin(row: RecordingOriginRow, recording: RecordingRow) -> RecordingOrigin:
    snapshot = row.payload
    if isinstance(snapshot, dict) and ('execution_context' in snapshot or 'execution_context_hash' in snapshot):
        try:
            context = validate_prepared_context(snapshot['execution_context'])
            if context.context_hash != snapshot.get('execution_context_hash'):
                raise ValueError('Recording origin control context hash differs')
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(409, 'Recording origin control context failed its integrity check') from error
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("artifact"), dict) or recording is None
            or digest(snapshot) != row.definition_hash or snapshot.get("recording_id") != row.recording_id
            or snapshot.get("run_id") != row.run_id or snapshot.get("artifact", {}).get("id") != row.artifact_id
            or snapshot.get("created_at") != row.created_at or snapshot.get("recording_sha256") != recording.sha256
            or snapshot.get("artifact", {}).get("sha256") != recording.sha256):
        raise HTTPException(409, "Recording origin failed its integrity check")
    return RecordingOrigin(recording_id=row.recording_id, run_id=row.run_id, artifact_id=row.artifact_id,
                           created_at=row.created_at, definition_hash=row.definition_hash, snapshot=snapshot)


def import_run_recording(db: Database, write_lock: RLock, run_id: str, body: RunRecordingImport) -> tuple[RecordingDetail, bool]:
    with write_lock:
        with db.session() as session:
            try:
                run = run_get(session, run_id)
            except ValidationError as exc:
                raise HTTPException(409, "Run content is invalid") from exc
            error = completed_run_integrity_error(run)
            if error:
                raise HTTPException(409, error)
            row = session.get(ArtifactRow, body.artifact_id)
            if row is None or row.run_id != run_id:
                raise HTTPException(404, "Artifact does not belong to the selected run")
            try:
                artifact = Artifact.model_validate(row.payload)
            except ValidationError as exc:
                raise HTTPException(409, "Artifact metadata is invalid") from exc
            if (artifact.id != row.id or artifact.run_id != run_id
                    or not any(entry == artifact for entry in run.artifacts)):
                raise HTTPException(409, "Artifact metadata differs from its recorded run evidence")
            if artifact.media_type != "application/mcap":
                raise HTTPException(422, "Select an application/mcap run artifact")
            path = _managed_path(db.artifact_dir, row.storage_key)
            if path.stat().st_size != artifact.size_bytes:
                raise HTTPException(409, "Artifact size integrity check failed")
            sha = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
            if sha.hexdigest() != artifact.sha256:
                raise HTTPException(409, "Artifact SHA-256 integrity check failed")
            # Deterministic metadata makes a repeated import resolve to the same
            # recording. Source kind remains unknown: engine output alone does
            # not establish whether its underlying sensor data was measured.
            metadata = RecordingImportMetadata(name=f"Run {run.id} · sensor recording", source_kind="unknown",
                                               source=f"Run artifact {artifact.id}; adapter {run.adapter_id}; {artifact.sha256}")
            snapshot = {"run_id": run.id, "scenario_id": run.scenario_id, "scenario_revision": run.scenario_revision,
                        "seed": run.scenario_snapshot.seed, "adapter_id": run.adapter_id,
                        "adapter_version": run.provenance.adapter_version, "input_hash": run.provenance.input_hash,
                        "output_hash": run.provenance.output_hash, "model_fidelity": run.provenance.model_fidelity,
                        "artifact": artifact.model_dump(mode="json")}
            if run.execution_context is not None:
                snapshot['execution_context'] = run.execution_context.model_dump(mode='json')
                snapshot['execution_context_hash'] = run.execution_context.context_hash
            if run.recovery is not None:
                snapshot['recovery'] = run.recovery.model_dump(mode='json')
                snapshot['source_execution_id'] = run.recovery.source_execution_id
        # import_recording verifies the copied bytes again and owns its atomic
        # file/DB publication. If origin publication fails, retry repairs the link
        # for the same deduplicated recording; no linked-origin claim is returned.
        try:
            recording, created = import_recording(db, write_lock, path, metadata, artifact.sha256, artifact.size_bytes)
        except RecordingLimitError as exc:
            raise HTTPException(413, str(exc)) from exc
        except RecordingValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        with db.session.begin() as session:
            existing = session.scalar(select(RecordingOriginRow).where(RecordingOriginRow.artifact_id == artifact.id))
            recording_row = session.get(RecordingRow, recording.id)
            if existing:
                if existing.recording_id != recording.id:
                    raise HTTPException(409, "Artifact already identifies a different recording origin")
                _origin(existing, recording_row)
            else:
                timestamp = utcnow()
                snapshot.update(recording_id=recording.id, recording_sha256=recording.sha256, created_at=timestamp)
                session.add(RecordingOriginRow(recording_id=recording.id, run_id=run_id, artifact_id=artifact.id,
                                               created_at=timestamp, definition_hash=digest(snapshot), payload=snapshot))
        return recording, created


def register_run_recording_routes(api: APIRouter, db: Database, write_lock: RLock):
    @api.post("/runs/{run_id}/recordings", response_model=RecordingDetail, tags=["recordings"])
    def post_run_recording(run_id: str, body: RunRecordingImport):
        recording, created = import_run_recording(db, write_lock, run_id, body)
        return JSONResponse(recording.model_dump(mode="json"), status_code=201 if created else 200)

    @api.get("/recordings/{recording_id}/origin", response_model=RecordingOrigin | None, tags=["recordings"])
    def get_recording_origin(recording_id: str):
        with db.session() as session:
            recording = session.get(RecordingRow, recording_id)
            if recording is None:
                raise HTTPException(404, "Recording not found")
            row = session.get(RecordingOriginRow, recording_id)
            return _origin(row, recording) if row else None
