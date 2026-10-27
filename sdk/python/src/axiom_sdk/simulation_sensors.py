"""Immutable physical sensor configuration authoring; no engine execution."""
from pathlib import Path
from typing import Any

from .perception import _identity, _integer


class SimulationSensorClientMixin:
    def simulation_sensor_capabilities(self, *, adapter_id: str | None = None) -> list[dict[str, Any]]:
        return self._request('GET', '/simulation-sensor-capabilities',
            params={} if adapter_id is None else {'adapter_id': _identity(adapter_id)}).json()

    def simulation_sensor_configurations(self) -> list[dict[str, Any]]:
        return self._request('GET', '/simulation-sensor-configurations').json()

    def create_simulation_sensor_configuration(self, *, idempotency_key: str, **definition: Any) -> dict[str, Any]:
        return self._request('POST', '/simulation-sensor-configurations', json=definition,
            headers=self._intent_headers(idempotency_key)).json()

    def get_simulation_sensor_configuration(self, configuration_id: str, *, revision: int | None = None) -> dict[str, Any]:
        configuration_id = _identity(configuration_id)
        result = self._request('GET', f'/simulation-sensor-configurations/{configuration_id}',
            params={} if revision is None else {'revision': _integer(revision, 'revision', 1, 2**31-1)}).json()
        if not isinstance(result, dict) or result.get('id') != configuration_id or (revision is not None and result.get('revision') != revision):
            raise ValueError('Sensor configuration identity or revision mismatch')
        return result

    def simulation_sensor_configuration_revisions(self, configuration_id: str) -> list[dict[str, Any]]:
        return self._request('GET', f'/simulation-sensor-configurations/{_identity(configuration_id)}/revisions').json()

    def revise_simulation_sensor_configuration(self, configuration_id: str, *, expected_revision: int,
                                              idempotency_key: str, **definition: Any) -> dict[str, Any]:
        return self._request('POST', f'/simulation-sensor-configurations/{_identity(configuration_id)}/revisions',
            json={**definition, 'expected_revision': _integer(expected_revision, 'expected_revision', 1, 2**31-1)},
            headers=self._intent_headers(idempotency_key)).json()

    def export_simulation_sensor_configuration(self, configuration_id: str, destination: str | Path,
                                              *, revision: int | None = None) -> Path:
        configuration_id = _identity(configuration_id)
        metadata = self.get_simulation_sensor_configuration(configuration_id, revision=revision)
        return self._export_sensor_manifest(f'/simulation-sensor-configurations/{configuration_id}/export', metadata, destination)
