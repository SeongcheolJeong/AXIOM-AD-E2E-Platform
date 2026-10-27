"""Relational entity keys plus versioned JSON documents and managed artifacts."""

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, create_engine, event, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

SCHEMA_VERSION = 20


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("ADE2E_DATA_DIR", ".data")).resolve())
    database_url: str | None = field(default_factory=lambda: os.environ.get("ADE2E_DATABASE_URL"))
    api_token: str | None = field(default_factory=lambda: os.environ.get("ADE2E_API_TOKEN"))
    team_mode: bool = field(default_factory=lambda: os.environ.get('ADE2E_TEAM_MODE', '0').lower() in {'1', 'true', 'yes'})
    seed_examples: bool = field(default_factory=lambda: os.environ.get("ADE2E_SEED_EXAMPLES", "1").lower() not in {"0", "false", "no"})
    cors_origins: list[str] = field(default_factory=lambda: os.environ.get("ADE2E_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(","))

    def __post_init__(self):
        if self.team_mode and not self.api_token:
            raise ValueError('Team mode requires an operator bootstrap ADE2E_API_TOKEN')
        self.data_dir = Path(self.data_dir).resolve()
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'platform.db'}"


class Base(DeclarativeBase):
    pass


class SchemaVersionRow(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class ScenarioRow(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RevisionRow(Base):
    __tablename__ = "scenario_revisions"
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    verdict: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RequirementRow(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RequirementLinkRow(Base):
    __tablename__ = "requirement_scenarios"
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), primary_key=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))


class Database:
    def __init__(self, settings: Settings):
        # Native user RTL is intentionally unavailable in shared team workers.
        self.team_mode = settings.team_mode
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = settings.data_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.recording_dir = settings.data_dir / "recordings"
        self.recording_dir.mkdir(parents=True, exist_ok=True)
        self.recording_staging_dir = settings.data_dir / "recording-staging"
        self.recording_staging_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir = settings.data_dir / "models"
        self.model_staging_dir = settings.data_dir / "model-staging"
        self.perception_dir = settings.data_dir / "perception"
        self.perception_staging_dir = settings.data_dir / "perception-staging"
        self.dataset_image_dir = settings.data_dir / "dataset-images"
        self.dataset_staging_dir = settings.data_dir / "dataset-staging"
        self.recovery_dir = settings.data_dir / "remote-recovery"
        self.recovery_staging_dir = settings.data_dir / "remote-recovery-staging"
        self.stack_replay_dir = settings.data_dir / "stack-replay"
        self.stack_replay_staging_dir = settings.data_dir / "stack-replay-staging"
        self.experiment_dir = settings.data_dir / 'experiments'
        for directory in (self.model_dir, self.model_staging_dir, self.perception_dir, self.perception_staging_dir,
                          self.dataset_image_dir, self.dataset_staging_dir, self.stack_replay_dir, self.stack_replay_staging_dir, self.recovery_dir, self.recovery_staging_dir, self.experiment_dir):
            directory.mkdir(parents=True, exist_ok=True)
        url = make_url(settings.database_url)
        options = {}
        if url.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if url.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
            else:
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.database_url, **options)
        if url.get_backend_name() == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def read_session(self):
        """Pin a compound read to one database snapshot.

        Python sqlite3 legacy mode does not begin a physical transaction for
        SELECT. SQLAlchemy Session autobegin alone therefore cannot prevent
        mixing old parent rows with newly committed child evidence.
        """
        with self.session() as session:
            if self.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN"))
            yield session

    def initialize(self):
        # Concurrent first boots can race while switching an empty database to
        # WAL, before busy_timeout takes effect for the schema writer lock.
        deadline = time.monotonic() + 30
        while True:
            try:
                self._initialize_once()
                return
            except OperationalError as error:
                code = getattr(error.orig, "sqlite_errorcode", 0)
                if (self.engine.dialect.name != "sqlite"
                        or code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                        or time.monotonic() >= deadline):
                    raise
                time.sleep(0.05)

    def _initialize_once(self):
        # Register additive recording/queue tables, then inspect the version
        # before creating or altering anything in an existing database.
        from . import recording_models  # noqa: F401
        from . import job_models  # noqa: F401
        from . import suite_models  # noqa: F401
        from . import comparison_models  # noqa: F401
        from . import sensor_models  # noqa: F401
        from . import run_recording_models  # noqa: F401
        from . import model_models  # noqa: F401
        from . import perception_models  # noqa: F401
        from . import dataset_models  # noqa: F401
        from . import detection_evaluation_models  # noqa: F401
        from . import recovery_models  # noqa: F401
        from . import simulation_sensor_models  # noqa: F401
        from . import stack_models, stack_replay_models  # noqa: F401
        from . import case_models  # noqa: F401
        from . import collection_models  # noqa: F401
        from . import annotation_models  # noqa: F401
        from . import scene_models, verification_models  # noqa: F401
        from . import team_models, experiment_models  # noqa: F401
        from . import project_recording_models  # noqa: F401
        from . import scene_sequence_models, replay_spec_models  # noqa: F401
        from . import scenario_v2_models  # noqa: F401
        from . import simulation_catalog_models  # noqa: F401
        from . import experiment_verification_models  # noqa: F401
        from . import driving_sequence_models  # noqa: F401

        with self.engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            try:
                if sqlite:
                    connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                    # SQLite requires this before BEGIN to rebuild a referenced
                    # parent without rewriting or dropping its child relations.
                    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                    connection.commit()
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                existing = set(inspect(connection).get_table_names())
                if not existing:
                    versions = set()
                elif "schema_versions" not in existing:
                    raise RuntimeError("Unversioned database. Refusing schema writes; back up and migrate explicitly.")
                else:
                    versions = set(connection.execute(select(SchemaVersionRow.version)).scalars())
                    if versions not in ({1}, {2}, {3}, {4}, {5}, {6}, {7}, {8}, {9}, {10}, {11}, {12}, {13}, {14}, {15}, {16}, {17}, {18}, {19}, {20}):
                        raise RuntimeError("Unsupported database schema. Refusing schema writes; back up and migrate explicitly.")
                if versions and versions not in ({9}, {10}, {11}, {12}, {13}, {14}, {15}, {16}, {17}, {18}, {19}, {20}) and "execution_jobs" in existing:
                    if not sqlite:
                        raise RuntimeError("Schema 9 queue migration currently supports SQLite only")
                    self._migrate_queue_v9(connection)
                if 'execution_jobs' in existing and 'resource_key' not in {
                        column['name'] for column in inspect(connection).get_columns('execution_jobs')}:
                    if not sqlite:
                        raise RuntimeError('Schema 17 queue migration currently supports SQLite only')
                    self._migrate_queue_v9(connection)
                # Schema 11 adds one nullable input-plan column. Existing queue
                # and campaign parent tables retain their rows and IDs exactly.
                if 'test_campaigns' in existing and 'execution_plan' not in {
                        column['name'] for column in inspect(connection).get_columns('test_campaigns')}:
                    if not sqlite:
                        raise RuntimeError('Schema 11 campaign migration currently supports SQLite only')
                    connection.exec_driver_sql('ALTER TABLE test_campaigns ADD COLUMN execution_plan JSON')
                Base.metadata.create_all(connection)
                # Existing unscoped resources remain in the operator profile.
                # The explicit default project scopes only the new domains.
                from .team_models import ProjectRow
                from datetime import datetime, timezone
                if connection.execute(select(ProjectRow.id).where(ProjectRow.id == 'default')).first() is None:
                    connection.execute(ProjectRow.__table__.insert().values(id='default', name='Default workspace',
                                       created_at=datetime.now(timezone.utc).isoformat(timespec='microseconds')))
                if not versions:
                    connection.execute(SchemaVersionRow.__table__.insert().values(version=SCHEMA_VERSION))
                elif versions != {SCHEMA_VERSION}:
                    connection.execute(update(SchemaVersionRow).values(version=SCHEMA_VERSION))
                if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    raise RuntimeError("Database foreign-key integrity failed; schema migration rolled back")
                connection.commit()
            except BaseException:
                connection.rollback()
                if sqlite:
                    # A failing SQLAlchemy commit hook can deactivate its
                    # transaction before SQLite received COMMIT or ROLLBACK.
                    # Do not let the PRAGMA restoration commit that open DDL.
                    connection.connection.dbapi_connection.rollback()
                raise
            finally:
                if sqlite:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()

    @staticmethod
    def _migrate_queue_v9(connection):
        """Rebuild just the queue parent, preserving exact IDs and sequence.

        All DDL/data/version publication is one writer transaction. Existing
        child FKs continue naming execution_jobs; no old parent is renamed.
        """
        from .job_models import JobRow

        old_columns = [column["name"] for column in inspect(connection).get_columns("execution_jobs")]
        old_sequence = connection.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='execution_jobs'").scalar() or 0
        count = connection.exec_driver_sql("SELECT count(*) FROM execution_jobs").scalar()
        ddl = str(CreateTable(JobRow.__table__).compile(connection)).replace("CREATE TABLE execution_jobs", "CREATE TABLE execution_jobs_v9", 1)
        connection.exec_driver_sql(ddl)
        names = ", ".join(f'"{name}"' for name in old_columns)
        connection.exec_driver_sql(f"INSERT INTO execution_jobs_v9 ({names}) SELECT {names} FROM execution_jobs")
        if connection.exec_driver_sql("SELECT count(*) FROM execution_jobs_v9").scalar() != count:
            raise RuntimeError("Queue migration row count differs from the original")
        connection.exec_driver_sql("DROP TABLE execution_jobs")
        connection.exec_driver_sql("ALTER TABLE execution_jobs_v9 RENAME TO execution_jobs")
        for index in JobRow.__table__.indexes:
            index.create(connection)
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=max(seq, ?) WHERE name='execution_jobs'", (old_sequence,))
