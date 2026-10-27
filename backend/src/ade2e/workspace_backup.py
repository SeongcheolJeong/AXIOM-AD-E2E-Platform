"""Cold SQLite workspace backup/verify/restore for the single-host pilot.

Stop API and every worker first; this command NEVER stops services for you.
Source files are never opened by SQLite: DB/WAL are copied, then consolidated
in the private copy with SQLite's backup API. Secrets outside the data directory
and remote simulator stores are NOT part of this unencrypted local backup.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys

VERSION = "axiom-cold-workspace-backup/1"
DATABASE = "platform.db"
SIDECARS = {DATABASE + suffix for suffix in ("-wal", "-shm", "-journal")}
SUPPORTED_SCHEMAS = (11, 12, 13, 14, 15, 16, 17, 18)
MAX_FILES = 100_000
MAX_MANIFEST_BYTES = 32 * 1024 * 1024


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def fingerprint(path: Path) -> dict:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("File changed while reading: " + str(path))
    return {"size_bytes": after.st_size, "sha256": digest.hexdigest()}


def inventory(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a real directory, not a symlink")
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("Symlinks are not accepted in a workspace backup")
        for name in files:
            path = Path(directory) / name
            if not path.is_file():
                raise ValueError("Only regular workspace files are supported")
            key = path.relative_to(root).as_posix()
            safe_key(key)
            result[key] = fingerprint(path)
            if len(result) > MAX_FILES:
                raise ValueError("Workspace exceeds the pilot file-count bound")
    return dict(sorted(result.items()))


def safe_key(key: str) -> None:
    if (not isinstance(key, str) or not key or "\\" in key or "\x00" in key
            or PurePosixPath(key).is_absolute() or any(p in {"", ".", ".."} for p in key.split("/"))):
        raise ValueError("Unsafe backup path")


def assert_offline(source: Path, *, offline: bool) -> None:
    if not offline:
        raise ValueError("Stop API and all workers, then explicitly pass --offline")
    tool = shutil.which("lsof")
    if tool is None:
        raise ValueError("Cold backup requires lsof on macOS/Linux to check open DB handles")
    paths = [source / DATABASE, *[source / s for s in sorted(SIDECARS) if (source / s).exists()]]
    result = subprocess.run([tool, "-t", *map(str, paths)], capture_output=True, text=True, timeout=15)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise ValueError("Could not establish offline database state")
    if result.stdout.strip():
        raise ValueError("Database is open by a process; stop this workspace's API and workers first")


def database_info(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database contains broken foreign keys")
        versions = connection.execute("SELECT version FROM schema_versions").fetchall()
        if len(versions) != 1 or versions[0][0] not in SUPPORTED_SCHEMAS:
            raise ValueError("This backup release supports schema 11 through 18; no migration is attempted")
        # Queued work cannot silently resume when the restored server is started.
        active = connection.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('queued','running','cancel_requested')").fetchone()[0]
        resources = connection.execute("SELECT COUNT(*) FROM execution_resources WHERE state != 'idle'").fetchone()[0]
        has_plans=connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='verification_attempts'").fetchone()[0]
        active_plans=connection.execute('SELECT COUNT(*) FROM verification_attempts WHERE manifest IS NULL').fetchone()[0] if has_plans else 0
        if active or resources or active_plans:
            raise ValueError("Unfinished jobs or reserved/quarantined engines require reconciliation before backup/restore")
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        return {"schema_version": versions[0][0], "table_counts": counts,
                "integrity": "ok", "foreign_key_errors": 0, "active_jobs": active, "nonidle_resources": resources}


def _fresh_destination(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; restore/backup never overwrites a workspace")
    if destination.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(destination.resolve()):
        raise ValueError("Source and destination must not contain each other")
    if not destination.parent.is_dir():
        raise ValueError("Create the destination's parent directory first")


def backup(source: Path, destination: Path, *, offline: bool = False) -> dict:
    source, destination = Path(source).absolute(), Path(destination).absolute()
    _fresh_destination(source, destination)
    if not (source / DATABASE).is_file():
        raise ValueError("No platform.db exists in the selected data directory")
    assert_offline(source, offline=offline)
    original = inventory(source)
    if DATABASE not in original:
        raise ValueError("Missing workspace database")
    destination.mkdir(mode=0o700)
    try:
        payload = destination / "payload"
        payload.mkdir(mode=0o700)
        for name in original:
            target = payload / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            target.chmod(0o600)
        if inventory(payload) != original:
            raise ValueError("Copied files do not match the offline source")
        # Recover/merge a retained WAL in the PRIVATE COPY, never in source.
        normalized = destination / "consolidated.sqlite"
        with closing(sqlite3.connect(str(payload / DATABASE), timeout=5)) as incoming:
            with closing(sqlite3.connect(str(normalized))) as outgoing:
                incoming.backup(outgoing, pages=256, sleep=.01)
                outgoing.execute("PRAGMA journal_mode=DELETE")
        os.replace(normalized, payload / DATABASE)
        for name in SIDECARS:
            (payload / name).unlink(missing_ok=True)
        info = database_info(payload / DATABASE)
        assert_offline(source, offline=offline)
        if inventory(source) != original:
            raise ValueError("Source changed during backup; no snapshot is published")
        manifest = {"schema_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "database": info, "files": inventory(payload), "source_inventory_sha256": hashlib.sha256(canonical(original)).hexdigest(),
            "scope": "Cold local data directory, schema11/12/13/14/15/16/17/18 without migration; no remote engine store, credentials, runtime scripts or application source.",
            "encrypted": False, "authenticity": "Checksums detect corruption; they are not signatures or access control."}
        raw = canonical(manifest)
        with (destination / "manifest.json").open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        (destination / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
        return verify(destination)
    except BaseException:
        # This directory was created exclusively by this call, never preexisting.
        shutil.rmtree(destination)
        raise


def _parse_manifest(raw: bytes) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate manifest key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


def verify(bundle: Path) -> dict:
    bundle = Path(bundle).absolute()
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("Backup must be a real directory")
    if {p.name for p in bundle.iterdir()} != {"payload", "manifest.json", "manifest.sha256"}:
        raise ValueError("Incomplete backup or unexpected files at the bundle root")
    for name in ("manifest.json", "manifest.sha256"):
        if (bundle / name).is_symlink():
            raise ValueError("Symlinked manifests are forbidden")
    if (bundle / "manifest.json").stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Backup manifest exceeds the size bound")
    raw = (bundle / "manifest.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (bundle / "manifest.sha256").read_text().strip() != digest:
        raise ValueError("Backup manifest checksum mismatch")
    manifest = _parse_manifest(raw)
    if manifest.get("schema_version") != VERSION or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported backup manifest")
    if len(manifest["files"]) > MAX_FILES or DATABASE not in manifest["files"]:
        raise ValueError("Invalid backup file inventory")
    for name in manifest["files"]:
        safe_key(name)
        if name in SIDECARS:
            raise ValueError("Backup database must be consolidated, without WAL sidecars")
    if inventory(bundle / "payload") != manifest["files"]:
        raise ValueError("Backup payload checksum, size or file inventory mismatch")
    info = database_info(bundle / "payload" / DATABASE)
    if info != manifest["database"]:
        raise ValueError("Backup database does not match its manifest")
    return {"status": "verified", "manifest_sha256": digest, "file_count": len(manifest["files"]),
            "total_bytes": sum(x["size_bytes"] for x in manifest["files"].values()), "database": info,
            "encrypted": False, "scope": manifest["scope"]}


def restore(bundle: Path, destination: Path) -> dict:
    bundle, destination = Path(bundle).absolute(), Path(destination).absolute()
    _fresh_destination(bundle, destination)
    verified = verify(bundle)
    manifest = _parse_manifest((bundle / "manifest.json").read_bytes())
    destination.mkdir(mode=0o700)
    try:
        for name in manifest["files"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / "payload" / name, target)
            target.chmod(0o600)
        if inventory(destination) != manifest["files"] or database_info(destination / DATABASE) != verified["database"]:
            raise ValueError("Restored workspace failed verification")
        if verify(bundle)["manifest_sha256"] != verified["manifest_sha256"]:
            raise ValueError("Backup changed during restore")
        return {**verified, "status": "restored", "services_started": False}
    except BaseException:
        shutil.rmtree(destination)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    command = actions.add_parser("backup")
    command.add_argument("--source", type=Path, required=True)
    command.add_argument("--destination", type=Path, required=True)
    command.add_argument("--offline", action="store_true")
    command = actions.add_parser("verify")
    command.add_argument("bundle", type=Path)
    command = actions.add_parser("restore")
    command.add_argument("bundle", type=Path)
    command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "backup": result = backup(args.source, args.destination, offline=args.offline)
        elif args.action == "restore": result = restore(args.bundle, args.destination)
        else: result = verify(args.bundle)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
