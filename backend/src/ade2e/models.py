"""Streamed model storage and sealed authoring revisions; no graph execution."""

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from . import jobs
from .artifact_transactions import track_created_artifact
from .database import Database
from .model_models import ModelArtifactRow, ModelRevisionRow, ModelRow
from .model_schemas import MAX_MODEL_BYTES, ModelArtifact, ModelCreate, ModelDetail, ModelRevisionCreate, ModelSnapshot
from .schemas import Name
from .service import canonical_bytes, digest, uid, utcnow

MAX_MANIFEST_BYTES = 256 * 1024
STREAM_CHUNK_BYTES = 1024 * 1024
_ARTIFACT_ID = re.compile(r"model_artifact_[0-9a-f]{32}\Z")


def _upload_name(name: str) -> str:
    try:
        name = TypeAdapter(Name).validate_python(name)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_context=False)) from exc
    if any(ord(char) < 32 or ord(char) == 127 for char in name) or "/" in name or "\\" in name:
        raise HTTPException(422, "Model filename must not contain path separators or control characters")
    return name


def get_model_artifact(session, artifact_id: str) -> ModelArtifact:
    row = session.get(ModelArtifactRow, artifact_id)
    if row is None:
        raise HTTPException(404, "Model artifact not found")
    try:
        if not _ARTIFACT_ID.fullmatch(row.id) or row.storage_key != f"{row.id}.onnx" or _upload_name(row.name) != row.name:
            raise ValueError("Invalid managed artifact metadata")
        return ModelArtifact(id=row.id, name=row.name, sha256=row.sha256, size_bytes=row.size_bytes,
                             created_at=row.created_at, download_url=f"/api/v1/perception/model-artifacts/{row.id}/download")
    except (ValidationError, ValueError, TypeError, HTTPException) as exc:
        raise HTTPException(409, "Model artifact metadata failed its integrity check") from exc


def _verified_stream(db: Database, artifact: ModelArtifact) -> tuple[Path, BinaryIO]:
    """Verify through one bounded, non-symlink file handle before exposing bytes."""
    path = db.model_dir / f"{artifact.id}.onnx"
    stream = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        stream = os.fdopen(descriptor, "rb")
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != artifact.size_bytes or not 0 < info.st_size <= MAX_MODEL_BYTES:
            raise ValueError("Model artifact size/type mismatch")
        sha, total = hashlib.sha256(), 0
        for chunk in iter(lambda: stream.read(STREAM_CHUNK_BYTES), b""):
            total += len(chunk)
            if total > artifact.size_bytes:
                raise ValueError("Model artifact grew during verification")
            sha.update(chunk)
        if total != artifact.size_bytes or sha.hexdigest() != artifact.sha256:
            raise ValueError("Model artifact SHA-256 mismatch")
        stream.seek(0)
        return path, stream
    except (OSError, ValueError) as exc:
        if stream is not None:
            stream.close()
        raise HTTPException(409, "Model artifact bytes failed their size/SHA-256 integrity check") from exc


def verified_model_path(db: Database, artifact_id: str) -> Path:
    """Return a verified managed path. Callers also compare the pinned artifact SHA.

    This is a local trusted-file boundary, not a sandbox against an administrator
    modifying the file after this call. Runtime loading must preserve its own
    model-byte identity and reject external data or unsupported graphs.
    """
    with db.session() as session:
        artifact = get_model_artifact(session, artifact_id)
    path, stream = _verified_stream(db, artifact)
    stream.close()
    return path


def _revision_row(session, model_id: str, revision: int | None):
    head = session.get(ModelRow, model_id)
    if head is None:
        raise HTTPException(404, "Model not found")
    latest = session.scalar(select(func.max(ModelRevisionRow.revision)).where(ModelRevisionRow.model_id == model_id))
    if head.revision != latest or not isinstance(head.revision, int) or head.revision < 1:
        raise HTTPException(409, "Model authoring head differs from its revision history")
    row = session.get(ModelRevisionRow, (model_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, "Model revision not found")
    return row


def get_model(session, model_id: str, revision: int | None = None) -> ModelDetail:
    row = _revision_row(session, model_id, revision)
    try:
        if (len(row.manifest) != row.manifest_size_bytes or not 0 < len(row.manifest) <= MAX_MANIFEST_BYTES
                or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash):
            raise ValueError("Model manifest bytes differ from their size/digest")
        snapshot = ModelSnapshot.model_validate_json(row.manifest)
        if canonical_bytes(snapshot.model_dump(mode="json")) != row.manifest:
            raise ValueError("Model manifest is not canonical")
        artifact = get_model_artifact(session, row.artifact_id)
        if (snapshot.id != row.model_id or snapshot.revision != row.revision or snapshot.created_at != row.created_at
                or snapshot.artifact_id != row.artifact_id or snapshot.artifact != artifact
                or snapshot.label_map_hash != digest(snapshot.labels)):
            raise ValueError("Model metadata differs from its sealed definition")
        return ModelDetail(**snapshot.model_dump(), manifest_hash=row.manifest_hash, manifest_size_bytes=row.manifest_size_bytes,
                           download_url=f"/api/v1/perception/models/{model_id}/export?revision={row.revision}")
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Model declaration manifest failed its integrity check") from exc


def _manifest_values(snapshot: ModelSnapshot) -> dict:
    content = canonical_bytes(snapshot.model_dump(mode="json"))
    if len(content) > MAX_MANIFEST_BYTES:
        raise HTTPException(413, "Model declaration exceeds the 256 KiB manifest limit")
    return {"manifest": content, "manifest_hash": hashlib.sha256(content).hexdigest(), "manifest_size_bytes": len(content)}


def create_model(db: Database, body: ModelCreate) -> ModelDetail:
    # File hashing precedes the short SQLite writer transaction.
    verified_model_path(db, body.artifact_id)
    with jobs._write(db) as session:
        artifact = get_model_artifact(session, body.artifact_id)
        model_id, now = uid("model"), utcnow()
        snapshot = ModelSnapshot(**body.model_dump(), id=model_id, revision=1, created_at=now,
                                 artifact=artifact, label_map_hash=digest(body.labels))
        session.add(ModelRow(id=model_id, revision=1, created_at=now))
        session.flush()
        session.add(ModelRevisionRow(model_id=model_id, revision=1, created_at=now, artifact_id=body.artifact_id,
                                     **_manifest_values(snapshot)))
        session.flush()
        return get_model(session, model_id, 1)


def revise_model(db: Database, model_id: str, body: ModelRevisionCreate) -> ModelDetail:
    verified_model_path(db, body.artifact_id)
    with jobs._write(db) as session:
        current = get_model(session, model_id)
        if body.expected_revision != current.revision:
            raise HTTPException(409, "Model revision changed; reload before revising")
        if current.revision >= 2**31 - 1:
            raise HTTPException(409, "Model revision limit reached")
        definition = ModelCreate.model_validate(body.model_dump(exclude={"expected_revision"}))
        snapshot = ModelSnapshot(**definition.model_dump(), id=model_id, revision=current.revision + 1, created_at=utcnow(),
                                 artifact=get_model_artifact(session, body.artifact_id), label_map_hash=digest(body.labels))
        session.get(ModelRow, model_id).revision = snapshot.revision
        session.add(ModelRevisionRow(model_id=model_id, revision=snapshot.revision, created_at=snapshot.created_at,
                                     artifact_id=body.artifact_id, **_manifest_values(snapshot)))
        session.flush()
        return get_model(session, model_id, snapshot.revision)


def _write_exact(stream: BinaryIO, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = stream.write(view)
        if written is None or written <= 0 or written > len(view):
            raise OSError("Model file write did not make valid progress")
        view = view[written:]


def _flush_upload(stream: BinaryIO) -> None:
    stream.flush()
    os.fsync(stream.fileno())


def _publish_upload(db: Database, temporary: Path, name: str, sha256: str, size_bytes: int) -> tuple[ModelArtifact, bool]:
    with jobs._write(db) as session:
        existing = session.scalar(select(ModelArtifactRow).where(ModelArtifactRow.name == name,
                                  ModelArtifactRow.sha256 == sha256, ModelArtifactRow.size_bytes == size_bytes))
        if existing is not None:
            artifact = get_model_artifact(session, existing.id)
            return artifact, False
        artifact_id = uid("model_artifact")
        path = db.model_dir / f"{artifact_id}.onnx"
        # The staged upload and final directory share the data filesystem.
        # Hard-link publication is exclusive and avoids a 128 MiB copy under lock.
        os.link(temporary, path)
        track_created_artifact(session, path)
        directory_fd = os.open(db.model_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        session.add(ModelArtifactRow(id=artifact_id, name=name, sha256=sha256, size_bytes=size_bytes,
                                     created_at=utcnow(), storage_key=path.name))
        session.flush()
        return get_model_artifact(session, artifact_id), True


def _download_chunks(stream: BinaryIO):
    try:
        yield from iter(lambda: stream.read(STREAM_CHUNK_BYTES), b"")
    finally:
        stream.close()


def register_model_routes(api: APIRouter, db: Database):
    @api.post("/perception/model-artifacts", response_model=ModelArtifact, status_code=201, tags=["perception models"],
              openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def upload_model_artifact(request: Request, name: Annotated[str, Query(min_length=1, max_length=180)]):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "Upload model bytes as application/octet-stream, not JSON or multipart")
        name = _upload_name(name)
        declared_size = request.headers.get("content-length")
        if declared_size is not None:
            if not declared_size.isascii() or not declared_size.isdigit():
                raise HTTPException(422, "Content-Length must be a nonnegative decimal integer")
            declared_size = declared_size.lstrip("0") or "0"
            if len(declared_size) > len(str(MAX_MODEL_BYTES)) or int(declared_size) > MAX_MODEL_BYTES:
                raise HTTPException(413, "Model upload exceeds 128 MiB")
        temporary = db.model_staging_dir / f"upload_{uuid.uuid4().hex}.part"
        created = False
        try:
            stream = temporary.open("xb")
            created = True
            total, sha = 0, hashlib.sha256()
            with stream:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES:
                        raise HTTPException(413, "Model upload exceeds 128 MiB")
                    sha.update(chunk)
                    await run_in_threadpool(_write_exact, stream, chunk)
                await run_in_threadpool(_flush_upload, stream)
            if total == 0:
                raise HTTPException(422, "Model upload must not be empty")
            if declared_size is not None and int(declared_size) != total:
                raise HTTPException(422, "Uploaded size differs from Content-Length")
            artifact, is_new = await run_in_threadpool(_publish_upload, db, temporary, name, sha.hexdigest(), total)
            if not is_new:
                # A matching catalog row does not prove that its stored bytes
                # remain intact. Hash outside the SQLite writer transaction.
                await run_in_threadpool(verified_model_path, db, artifact.id)
            return JSONResponse(artifact.model_dump(mode="json"), status_code=201 if is_new else 200)
        finally:
            if created:
                temporary.unlink(missing_ok=True)

    @api.get("/perception/model-artifacts/{artifact_id}/download", response_class=StreamingResponse, tags=["perception models"])
    def download_model_artifact(artifact_id: str):
        with db.session() as session:
            artifact = get_model_artifact(session, artifact_id)
        _, stream = _verified_stream(db, artifact)
        return StreamingResponse(_download_chunks(stream), media_type="application/octet-stream", background=BackgroundTask(stream.close), headers={
            "Content-Length": str(artifact.size_bytes), "Content-Disposition": f'attachment; filename="{artifact.id}.onnx"',
            "X-Content-Type-Options": "nosniff", "ETag": f'"{artifact.sha256}"', "X-Content-SHA256": artifact.sha256})

    @api.post("/perception/models", response_model=ModelDetail, status_code=201, tags=["perception models"])
    def post_model(body: ModelCreate):
        return create_model(db, body)

    @api.get("/perception/models", response_model=list[ModelDetail], tags=["perception models"])
    def list_models():
        with db.session() as session:
            ids = list(session.scalars(select(ModelRow.id).order_by(ModelRow.created_at.desc(), ModelRow.id.desc())))
            return [get_model(session, model_id) for model_id in ids]

    @api.get("/perception/models/{model_id}", response_model=ModelDetail, tags=["perception models"])
    def model_detail(model_id: str, revision: int | None = Query(default=None, ge=1, le=2**31 - 1)):
        with db.session() as session:
            return get_model(session, model_id, revision)

    @api.get("/perception/models/{model_id}/revisions", response_model=list[ModelDetail], tags=["perception models"])
    def model_revisions(model_id: str):
        with db.session() as session:
            get_model(session, model_id)
            revisions = list(session.scalars(select(ModelRevisionRow.revision).where(ModelRevisionRow.model_id == model_id)
                                            .order_by(ModelRevisionRow.revision.desc())))
            return [get_model(session, model_id, revision) for revision in revisions]

    @api.post("/perception/models/{model_id}/revisions", response_model=ModelDetail, status_code=201, tags=["perception models"])
    def post_model_revision(model_id: str, body: ModelRevisionCreate):
        return revise_model(db, model_id, body)

    @api.get("/perception/models/{model_id}/export", response_class=Response, tags=["perception models"])
    def export_model(model_id: str, revision: int | None = Query(default=None, ge=1, le=2**31 - 1)):
        with db.session() as session:
            detail = get_model(session, model_id, revision)
            content = session.get(ModelRevisionRow, (model_id, detail.revision)).manifest
        return Response(content, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{model_id}_revision_{detail.revision}.json"',
            "X-Content-Type-Options": "nosniff", "ETag": f'"{detail.manifest_hash}"', "X-Content-SHA256": detail.manifest_hash})
