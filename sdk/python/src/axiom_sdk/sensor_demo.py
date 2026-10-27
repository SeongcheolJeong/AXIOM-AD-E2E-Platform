"""Guided Sensor Sim authoring on the same versioned API as the UI."""
from __future__ import annotations
import math
from .experiments import _object_map_identity, _object_map_revision

_PARAMETERS = {
    'driving_camera_fov': ('front_fov_deg', 50, 110, 1, 90),
    'camera_fov': ('front_fov_deg', 50, 110, 1, 90),
    'lidar_density': ('lidar_points_per_second', 8000, 32000, 1000, 32000),
    'radar_range': ('radar_range_m', 10, 100, 1, 80),
}


def _preset(identity):
    if identity not in _PARAMETERS:
        raise ValueError('Unknown Sensor Sim preset')
    return identity


class SensorDemoClientMixin:
    def sensor_sim_presets(self, *, project_id: str = 'default'):
        """List pinned inputs and explicit saved results; this does not probe CARLA."""
        return self._request('GET', '/sensor-sim/presets', params={
            'project_id': _object_map_identity(project_id)}).json()

    def bind_sensor_sim_preset(self, preset_id: str, *, project_id: str = 'default',
                               expected_revision: int = 0, capture_spec: dict | None = None,
                               seed: int = 42, saved_execution_id: str | None = None):
        """Pin the fixed demo rig and an optional exact default A/B native result."""
        _preset(preset_id)
        _object_map_revision(expected_revision, minimum=0)
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise ValueError('Seed must be an integer within 0..2147483647')
        if capture_spec is not None and not isinstance(capture_spec, dict):
            raise ValueError('Capture specification must be an object')
        if saved_execution_id is not None:
            _object_map_identity(saved_execution_id)
        body = dict(project_id=_object_map_identity(project_id), expected_revision=expected_revision,
                    seed=seed, saved_execution_id=saved_execution_id)
        if capture_spec is not None:
            body['capture_spec'] = capture_spec
        return self._request('POST', f'/sensor-sim/presets/{preset_id}/bindings', json=body).json()

    def preview_sensor_sim_preset(self, preset_id: str, *, expected_binding_revision: int,
                                  project_id: str = 'default', parameters: dict | None = None):
        """Run native preflight and return an exact draft; nothing is submitted."""
        _preset(preset_id)
        _object_map_revision(expected_binding_revision)
        parameters = {} if parameters is None else parameters
        key, lower, upper, step, baseline = _PARAMETERS[preset_id]
        if not isinstance(parameters, dict) or set(parameters) - {key}:
            raise ValueError('Unsupported parameter for this Sensor Sim preset')
        for value in parameters.values():
            if (type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper
                    or not float(value).is_integer() or (value - lower) % step != 0 or value == baseline):
                raise ValueError('Sensor Sim parameter must follow its limits and step and differ from baseline')
        return self._request('POST', f'/sensor-sim/presets/{preset_id}/preview', json={
            'project_id': _object_map_identity(project_id), 'expected_binding_revision': expected_binding_revision,
            'parameters': parameters}).json()

    def execute_sensor_sim_preview(self, preview: dict, *, idempotency_key: str):
        """Explicitly save and submit a reviewed preview using the normal queue.

        Source/preflight drift creates no execution. A retry must reuse this
        exact preview and idempotency key; no automatic simulator retry occurs.
        """
        if not isinstance(preview, dict) or preview.get('schema_version') != 'sensor-sim-preset-preview/1':
            raise ValueError('A Sensor Sim preset preview is required')
        created = self.create_experiment(idempotency_key=idempotency_key, **preview['experiment'])
        if created['preview_hash'] != preview['preview']['preview_hash']:
            raise ValueError('Native source changed after preview; no execution was submitted. Preview again and use a new idempotency key.')
        return self.submit_experiment(created['id'], preview_hash=created['preview_hash'], idempotency_key=idempotency_key)
