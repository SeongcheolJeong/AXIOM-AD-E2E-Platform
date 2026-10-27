"""Optional experiment handlers. Core never imports simulator/sensor libraries."""
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Callable, Protocol
from pydantic import BaseModel, ConfigDict, Field
from .experiment_schemas import ExperimentCreate, ExperimentKind

class ExperimentHandlerMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=180)
    version: str = Field(min_length=1, max_length=180)
    kinds: list[ExperimentKind]
    status: str = Field(pattern='^(available|not_configured)$')
    fidelity: str
    resource_id: str | None = Field(default=None, max_length=180)
    cancellation_supported: bool = False
    description: str = ''

@dataclass(frozen=True)
class ExperimentOutputFile:
    path: Path
    name: str
    media_type: str = 'application/octet-stream'

@dataclass(frozen=True)
class ExperimentExecution:
    summary: dict
    artifacts: list[ExperimentOutputFile] = field(default_factory=list)
    fidelity: str = 'unclassified'

class ExperimentHandler(Protocol):
    def metadata(self) -> ExperimentHandlerMetadata: ...
    def prepare(self, spec: ExperimentCreate) -> dict: ...
    def execute(self, snapshot: dict, work_dir: Path, should_cancel: Callable[[], bool]) -> ExperimentExecution: ...

class ExperimentRegistry:
    def __init__(self, handlers=None, *, load_plugins: bool = True, database=None):
        self._handlers = {}
        for handler in handlers or []:
            self.register(handler)
        if load_plugins:
            for entry in entry_points(group='ade2e.experiment_handlers'):
                self.register(entry.load()(database=database))

    def register(self, handler, *, replace: bool = False):
        metadata = ExperimentHandlerMetadata.model_validate(handler.metadata())
        if not callable(getattr(handler, 'prepare', None)) or not callable(getattr(handler, 'execute', None)):
            raise TypeError('Experiment handlers implement metadata(), prepare(spec), execute(snapshot, work_dir, should_cancel)')
        if metadata.id in self._handlers and not replace:
            raise ValueError('Experiment handler already registered; replace explicitly')
        self._handlers[metadata.id] = handler

    def get(self, identity):
        return self._handlers.get(identity)

    def list(self):
        return [ExperimentHandlerMetadata.model_validate(handler.metadata()) for handler in self._handlers.values()]
