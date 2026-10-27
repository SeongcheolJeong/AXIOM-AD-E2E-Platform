"""Replaceable simulation engines. Plugins are trusted installed Python code."""

from importlib.metadata import entry_points
from typing import Protocol, runtime_checkable

from .schemas import Adapter, AdapterFailureEvidence, ArtifactPayload, Scenario, SimulationResult


class AdapterInputError(ValueError):
    """The adapter cannot execute this scenario; API returns 422."""


class AdapterUnavailable(RuntimeError):
    """A configured dependency is not available; API returns 422."""


class AdapterExecutionError(RuntimeError):
    """Execution was attempted but failed or its outcome is unknown.

    Core persists an error run and its idempotency key. This must not inherit
    from AdapterUnavailable: a failed attempt invalidates older baseline evidence.
    """

    def __init__(self, message: str, *, artifact_payloads=()):
        super().__init__(message)
        # Diagnostics have the same managed-file ownership as successful output.
        # Copy and validate nested models; plugins may mutate them after creation.
        evidence = AdapterFailureEvidence.model_validate({"artifact_payloads": [
            item.model_dump() if isinstance(item, ArtifactPayload) else item for item in artifact_payloads
        ]})
        self.artifact_payloads = tuple(evidence.artifact_payloads)


class AdapterCancelled(RuntimeError):
    """Cooperative execution stopped; this is not a failed validation run."""


class AdapterCancelledBeforeDispatch(AdapterCancelled):
    """Core observed cancellation before calling the selected adapter."""


class AdapterOwnershipLost(RuntimeError):
    """The worker lost its execution lease; no result may be published."""


@runtime_checkable
class SimulationAdapter(Protocol):
    def metadata(self) -> Adapter: ...

    def execute(self, scenario: Scenario) -> SimulationResult: ...


def supports_cancellation(adapter: SimulationAdapter) -> bool:
    """An optional hook must not bypass a more recently overridden execute.

    Existing plugins subclassing a cancellable engine may only override
    execute. They retain that behavior until they explicitly supply a matching
    execute_cancellable(scenario, should_cancel) implementation.
    """
    if not callable(getattr(adapter, "execute_cancellable", None)):
        return False
    definitions = [getattr(adapter, "__dict__", {}), *(cls.__dict__ for cls in type(adapter).__mro__)]
    execute_owner = next((i for i, values in enumerate(definitions) if "execute" in values), len(definitions))
    hook_owner = next((i for i, values in enumerate(definitions) if "execute_cancellable" in values), len(definitions))
    return hook_owner <= execute_owner


def supports_execution_context(adapter: SimulationAdapter) -> bool:
    """Stack selection never falls back to a legacy scenario-only execute."""
    return callable(getattr(adapter, 'validate_execution_context', None)) and callable(getattr(adapter, 'execute_with_context', None))


class UnavailableAdapter:
    def __init__(self, metadata: Adapter):
        self._metadata = metadata

    def metadata(self) -> Adapter:
        return self._metadata

    def execute(self, scenario: Scenario) -> SimulationResult:
        raise AdapterUnavailable(self._metadata.description)


class Registry:
    def __init__(self, load_plugins: bool = True):
        from .reference import ReferenceAdapter

        self._adapters: dict[str, SimulationAdapter] = {}
        self.register(ReferenceAdapter())
        self.register(UnavailableAdapter(Adapter(
            id="carla", name="CARLA", version="unconfigured", kind="driving_and_sensors",
            status="not_configured", description="Install the CARLA adapter and configure a reachable CARLA server.",
            capabilities=["external driving simulation", "external camera and LiDAR sensors"],
            limitations=["No CARLA server is configured; no sensor evidence has been generated"],
            license="MIT (CARLA code); assets have separate terms", source_url="https://carla.org/",
        )))
        if load_plugins:
            for entry in entry_points(group="ade2e.adapters"):
                factory = entry.load()
                adapter = factory() if callable(factory) else factory
                self.register(adapter, replace=True)

    def register(self, adapter: SimulationAdapter, *, replace: bool = False) -> None:
        metadata = Adapter.model_validate(adapter.metadata())
        if not callable(getattr(adapter, "execute", None)):
            raise TypeError("Adapters must implement execute(scenario)")
        existing = self._adapters.get(metadata.id)
        if existing and not replace and existing.metadata().status == "available":
            raise ValueError(f"Adapter {metadata.id!r} is already registered; pass replace=True explicitly")
        self._adapters[metadata.id] = adapter

    def get(self, adapter_id: str) -> SimulationAdapter | None:
        return self._adapters.get(adapter_id)

    def list(self) -> list[Adapter]:
        return [Adapter.model_validate(adapter.metadata()) for adapter in self._adapters.values()]
