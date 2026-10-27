"""Authenticated MCAP import/catalog and indexed payload seek."""

import hashlib
import json
import math
import uuid
from pathlib import Path
from threading import RLock
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import mcap_ingest
from .artifact_transactions import track_created_artifact
from .database import Database
from .recording_models import RecordingChannelRow, RecordingMessageRow, RecordingRow, RecordingSchemaRow
from .recording_schemas import RecordingDetail, RecordingImportMetadata, RecordingSample, RecordingSamples, RecordingSummary, SourceKind
from .service import digest, utcnow

INDEX_VERSION = "mcap-payload-index/1"
MAX_DECODE_BYTES = 64 * 1024
PREVIEW_BYTES = 64
MAX_UINT64 = 2**64 - 1
MAX_SAFE_JSON_INTEGER = 2**53 - 1


def _write_exact(stream, content: bytes):
    if stream.write(content) != len(content):
        raise OSError("Recording storage returned a partial write")


def _managed_path(root: Path, storage_key: str) -> Path:
    path = (root / storage_key).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise HTTPException(404, "Managed recording object is unavailable")
    return path


def _get_recording(session: Session, recording_id: str) -> RecordingRow:
    row = session.get(RecordingRow, recording_id)
    if row is None:
        raise HTTPException(404, "Recording not found")
    return row


def select_message_at(session: Session, recording_id: str, channel_id: int, timestamp: int) -> tuple[RecordingMessageRow | None, RecordingMessageRow | None]:
    """Select a predecessor and optional next message without a future fallback.

    Internal timestamps may be outside uint64 after applying a declared clock
    offset. The stored ordering remains unsigned log time then original ordinal.
    """
    query = select(RecordingMessageRow).where(RecordingMessageRow.recording_id == recording_id,
                                             RecordingMessageRow.channel_id == channel_id)
    preceding = None if timestamp < 0 else session.scalar(query.where(RecordingMessageRow.log_time_key <= f"{min(timestamp, MAX_UINT64):020d}")
                                                         .order_by(RecordingMessageRow.log_time_key.desc(), RecordingMessageRow.ordinal.desc()).limit(1))
    following = None if timestamp >= MAX_UINT64 else session.scalar(query.where(RecordingMessageRow.log_time_key > f"{timestamp:020d}")
                                                                  .order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal).limit(1))
    return preceding, following


def read_message_bytes(db: Database, recording: RecordingRow, entry: RecordingMessageRow) -> bytes:
    """Read only the indexed message after managed-path, size and SHA checks."""
    if entry.recording_id != recording.id:
        raise HTTPException(409, "Recording message belongs to a different source")
    path = _managed_path(db.recording_dir, recording.index_storage_key)
    expected_size = recording.payload["index"]["size_bytes"]
    if not isinstance(expected_size, int) or expected_size < 0 or expected_size > mcap_ingest.MAX_EXPANDED_BYTES or path.stat().st_size != expected_size:
        raise HTTPException(409, "Recording payload sidecar size integrity check failed")
    if (entry.payload_offset < 0 or entry.payload_length < 0 or entry.payload_length > mcap_ingest.MAX_RECORD_BYTES
            or entry.payload_offset + entry.payload_length > expected_size):
        raise HTTPException(409, "Recording message index points outside its bounded payload sidecar")
    with path.open("rb") as stream:
        stream.seek(entry.payload_offset)
        content = stream.read(entry.payload_length)
    if len(content) != entry.payload_length or hashlib.sha256(content).hexdigest() != entry.sha256:
        raise HTTPException(409, "Recording sample integrity check failed")
    return content


def recording_detail(session: Session, row: RecordingRow) -> RecordingDetail:
    channels = [entry.payload for entry in session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id == row.id).order_by(RecordingChannelRow.channel_id))]
    schemas = [entry.payload for entry in session.scalars(select(RecordingSchemaRow).where(RecordingSchemaRow.recording_id == row.id).order_by(RecordingSchemaRow.schema_id))]
    return RecordingDetail.model_validate({**row.payload, "channels": channels, "schemas": schemas})


def _copy_original(source: Path, destination: Path, session: Session, expected_sha: str, expected_size: int):
    stream = destination.open("xb")
    track_created_artifact(session, destination)
    actual_hash, size = hashlib.sha256(), 0
    with stream, source.open("rb") as original:
        for chunk in iter(lambda: original.read(1024 * 1024), b""):
            _write_exact(stream, chunk)
            actual_hash.update(chunk)
            size += len(chunk)
            if size > mcap_ingest.MAX_UPLOAD_BYTES:
                raise mcap_ingest.RecordingLimitError("Recording changed or exceeded the upload bound during publication")
    if size != expected_size or actual_hash.hexdigest() != expected_sha:
        raise mcap_ingest.RecordingValidationError("Recording changed between upload and publication")


def import_recording(db: Database, write_lock: RLock, source_path: Path, metadata: RecordingImportMetadata, sha256: str, size_bytes: int) -> tuple[RecordingDetail, bool]:
    """CPU/file parsing and SQL publication run in a worker thread."""
    import_key = digest({"sha256": sha256, "metadata": metadata.model_dump(mode="json")})
    with write_lock, db.session.begin() as session:
        existing = session.scalar(select(RecordingRow).where(RecordingRow.import_key == import_key))
        if existing is not None:
            return recording_detail(session, existing), False
        recording_id = f"rec_{uuid.uuid4().hex}"
        original_key, payload_key = f"{recording_id}.mcap", f"{recording_id}.payloads"
        payload_path = db.recording_dir / payload_key
        payload_stream = payload_path.open("xb")
        track_created_artifact(session, payload_path)
        with payload_stream:
            parsed = mcap_ingest.parse_mcap(source_path, payload_stream)
        _copy_original(source_path, db.recording_dir / original_key, session, sha256, size_bytes)
        start = min((message["log_time_key"] for message in parsed.messages), default=None)
        end = max((message["log_time_key"] for message in parsed.messages), default=None)
        summary = RecordingSummary(
            id=recording_id, **metadata.model_dump(), sha256=sha256, size_bytes=size_bytes,
            created_at=utcnow(), profile=parsed.profile, message_count=len(parsed.messages), channel_count=len(parsed.channels),
            start_time_ns=str(int(start)) if start is not None else None, end_time_ns=str(int(end)) if end is not None else None,
            duration_s=(int(end) - int(start)) / 1e9 if start is not None and end is not None else 0,
            download_url=f"/api/v1/recordings/{recording_id}/download", warnings=parsed.warnings,
            index={"version": INDEX_VERSION, "sha256": parsed.payload_sha256, "size_bytes": parsed.payload_size, "source_sha256": sha256},
        )
        row = RecordingRow(id=recording_id, import_key=import_key, created_at=summary.created_at, sha256=sha256,
                           storage_key=original_key, index_storage_key=payload_key, payload={**summary.model_dump(mode="json"), "metadata": parsed.metadata})
        session.add(row)
        session.flush()
        for schema_id, schema in parsed.schemas.items():
            session.add(RecordingSchemaRow(recording_id=recording_id, schema_id=schema_id, payload=schema))
        session.flush()
        for channel_id, channel in parsed.channels.items():
            session.add(RecordingChannelRow(recording_id=recording_id, channel_id=channel_id, schema_ref=channel["schema_id"] or None, payload=channel))
        session.flush()
        for offset in range(0, len(parsed.messages), 1000):
            batch = [{"recording_id": recording_id, **message} for message in parsed.messages[offset:offset + 1000]]
            session.execute(RecordingMessageRow.__table__.insert(), batch)
        session.flush()
        return recording_detail(session, row), True


def _strict_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON numbers are not supported")
    return result


def _reject_json_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _contains_unsafe_integer(value) -> bool:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        return abs(value) > MAX_SAFE_JSON_INTEGER
    if isinstance(value, list):
        return any(_contains_unsafe_integer(item) for item in value)
    if isinstance(value, dict):
        for key in value:
            key.encode("utf-8", errors="strict")
        return any(_contains_unsafe_integer(item) for item in value.values())
    return False


def _sample(entry: RecordingMessageRow, data: bytes, decoder: str, warnings: list[str]) -> RecordingSample:
    status, decoded, decoded_text = "opaque", None, None
    truncated = len(data) > PREVIEW_BYTES
    if decoder == "json":
        if len(data) > MAX_DECODE_BYTES:
            status = "too_large"
        else:
            try:
                text = data.decode("utf-8")
                decoded = json.loads(text, parse_constant=_reject_json_constant, parse_float=_strict_float)
                status, decoded_text, truncated = "decoded", text, False
                if _contains_unsafe_integer(decoded):
                    decoded = None
                    warnings.append(f"Sample {entry.ordinal} contains integers outside JavaScript's exact range; use decoded_text for exact JSON. Structured decoded is omitted.")
            except (UnicodeError, ValueError, RecursionError):
                status, decoded, decoded_text = "invalid_json", None, None
    return RecordingSample(ordinal=entry.ordinal, log_time_ns=str(int(entry.log_time_key)), publish_time_ns=str(int(entry.publish_time_key)),
                           sequence=entry.sequence, size_bytes=len(data), sha256=entry.sha256, decoded=decoded, decoded_text=decoded_text,
                           decode_status=status, preview_hex=data[:PREVIEW_BYTES].hex(), truncated=truncated)


def recording_samples(session: Session, db: Database, recording_id: str, channel_id: int, at_ns: str | None, limit: int,
                      *, from_start: bool = False, after_ordinal: int | None = None, from_ordinal: int | None = None) -> RecordingSamples:
    if sum((at_ns is not None, from_start, after_ordinal is not None, from_ordinal is not None)) > 1:
        raise HTTPException(422, "Use exactly one selector: at_ns, from_start=true, after_ordinal, or from_ordinal")
    recording = _get_recording(session, recording_id)
    channel = session.get(RecordingChannelRow, (recording_id, channel_id))
    if channel is None:
        raise HTTPException(404, "Channel not found in this recording")
    requested = None if from_start or after_ordinal is not None or from_ordinal is not None else str(int(at_ns)) if at_ns is not None else recording.payload["start_time_ns"]
    if requested is not None and not 0 <= int(requested) <= MAX_UINT64:
        raise HTTPException(422, "at_ns must be an unsigned 64-bit decimal timestamp")
    warnings = []
    query = select(RecordingMessageRow).where(RecordingMessageRow.recording_id == recording_id, RecordingMessageRow.channel_id == channel_id)
    anchor = None
    if from_ordinal is not None:
        anchor = session.get(RecordingMessageRow, (recording_id, from_ordinal))
        if anchor is None or anchor.channel_id != channel_id:
            raise HTTPException(404, "from_ordinal does not identify a sample in the selected recording/channel")
    elif after_ordinal is not None:
        anchor = session.get(RecordingMessageRow, (recording_id, after_ordinal))
        if anchor is None or anchor.channel_id != channel_id:
            raise HTTPException(404, "after_ordinal does not identify a sample in the selected recording/channel")
        query = query.where(or_(RecordingMessageRow.log_time_key > anchor.log_time_key, and_(RecordingMessageRow.log_time_key == anchor.log_time_key, RecordingMessageRow.ordinal > anchor.ordinal)))
    elif requested is not None:
        anchor, _ = select_message_at(session, recording_id, channel_id, int(requested))
    if anchor is not None and after_ordinal is None:
        query = query.where(or_(RecordingMessageRow.log_time_key > anchor.log_time_key, and_(RecordingMessageRow.log_time_key == anchor.log_time_key, RecordingMessageRow.ordinal >= anchor.ordinal)))
    selected = list(session.scalars(query.order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal).limit(limit + 1)))
    has_more = len(selected) > limit
    entries = selected[:limit]
    if not entries:
        warnings.append("No following samples remain after this ordinal." if after_ordinal is not None else "Selected channel contains no samples.")
    elif anchor is None and requested is not None:
        warnings.append("No sample exists at or before the requested time; the first following sample is returned.")
    path = _managed_path(db.recording_dir, recording.index_storage_key)
    expected_size = recording.payload["index"]["size_bytes"]
    if path.stat().st_size != expected_size:
        raise HTTPException(409, "Recording payload sidecar size integrity check failed")
    samples = [_sample(entry, read_message_bytes(db, recording, entry), channel.payload["decoder"], warnings) for entry in entries]
    return RecordingSamples(recording_id=recording_id, channel_id=channel_id, requested_time_ns=requested, samples=samples,
                            warnings=warnings, next_after_ordinal=entries[-1].ordinal if has_more else None)


def register_recording_routes(api: APIRouter, db: Database, write_lock: RLock):
    @api.post("/recordings", response_model=RecordingDetail, status_code=201, tags=["recordings"],
              openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def post_recording(request: Request, name: Annotated[str, Query(min_length=1, max_length=180)],
                             source_kind: SourceKind = "unknown", source: str = Query(default="", max_length=1000)):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "Upload MCAP as application/octet-stream, not JSON or multipart")
        try:
            metadata = RecordingImportMetadata(name=name, source_kind=source_kind, source=source)
        except ValidationError as exc:
            raise HTTPException(422, detail=exc.errors(include_context=False)) from exc
        declared_size = request.headers.get("content-length")
        if declared_size is not None:
            if not declared_size.isascii() or not declared_size.isdigit():
                raise HTTPException(422, "Content-Length must be a nonnegative decimal integer")
            declared_size = declared_size.lstrip("0") or "0"
            if len(declared_size) > len(str(mcap_ingest.MAX_UPLOAD_BYTES)) or int(declared_size) > mcap_ingest.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"MCAP upload exceeds {mcap_ingest.MAX_UPLOAD_BYTES} bytes")
        temporary = db.recording_staging_dir / f"upload_{uuid.uuid4().hex}.part"
        created = False
        try:
            stream = temporary.open("xb")
            created = True
            total, sha = 0, hashlib.sha256()
            with stream:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > mcap_ingest.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, f"MCAP upload exceeds {mcap_ingest.MAX_UPLOAD_BYTES} bytes")
                    sha.update(chunk)
                    await run_in_threadpool(_write_exact, stream, chunk)
            if declared_size is not None and int(declared_size) != total:
                raise HTTPException(422, "Uploaded size differs from Content-Length")
            recording, is_new = await run_in_threadpool(import_recording, db, write_lock, temporary, metadata, sha.hexdigest(), total)
            return JSONResponse(recording.model_dump(mode="json"), status_code=201 if is_new else 200)
        except mcap_ingest.RecordingValidationError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        finally:
            if created:
                temporary.unlink(missing_ok=True)

    @api.get("/recordings", response_model=list[RecordingSummary], tags=["recordings"])
    def list_recordings(q: str = Query(default="", max_length=300)):
        with db.session() as session:
            rows = session.scalars(select(RecordingRow).order_by(RecordingRow.created_at.desc(), RecordingRow.id.desc()))
            needle = q.casefold()
            return [RecordingSummary.model_validate({key: value for key, value in row.payload.items() if key in RecordingSummary.model_fields}) for row in rows
                    if not needle or needle in f"{row.payload['name']} {row.payload['source']} {row.payload['source_kind']} {row.payload['profile']}".casefold()]

    @api.get("/recordings/{recording_id}", response_model=RecordingDetail, tags=["recordings"])
    def get_recording(recording_id: str):
        with db.session() as session:
            return recording_detail(session, _get_recording(session, recording_id))

    @api.get("/recordings/{recording_id}/download", response_class=FileResponse, tags=["recordings"])
    def download_recording(recording_id: str):
        with db.session() as session:
            recording = _get_recording(session, recording_id)
            path = _managed_path(db.recording_dir, recording.storage_key)
            if path.stat().st_size != recording.payload["size_bytes"]:
                raise HTTPException(409, "Original recording size integrity check failed")
            sha = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
            if sha.hexdigest() != recording.sha256:
                raise HTTPException(409, "Original recording SHA-256 integrity check failed")
            return FileResponse(path, media_type="application/octet-stream", filename=f"{recording.id}.mcap", headers={"X-Content-Type-Options": "nosniff"})

    @api.get("/recordings/{recording_id}/samples", response_model=RecordingSamples, tags=["recordings"])
    def get_samples(recording_id: str, channel_id: int = Query(ge=0, le=65535), at_ns: str | None = Query(default=None, pattern=r"^[0-9]{1,20}$"), limit: int = Query(default=1, ge=1, le=100),
                    from_start: bool = False, after_ordinal: int | None = Query(default=None, ge=0, le=mcap_ingest.MAX_MESSAGES - 1),
                    from_ordinal: int | None = Query(default=None, ge=0, le=mcap_ingest.MAX_MESSAGES - 1)):
        with db.session() as session:
            return recording_samples(session, db, recording_id, channel_id, at_ns, limit, from_start=from_start, after_ordinal=after_ordinal, from_ordinal=from_ordinal)
