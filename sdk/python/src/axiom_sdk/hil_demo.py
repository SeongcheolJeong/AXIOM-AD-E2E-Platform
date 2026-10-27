"""Software-DUT HIL Workbench on the same API used by the browser and agents."""
from __future__ import annotations
import math
import re
from .experiments import _object_map_identity, _object_map_revision

_PARAMETERS = {'input_repeat': ('duration_s', .5, 4, .5), 'input_delay': ('delay_ms', 50, 1000, 50), 'input_dropout': ('duration_s', .5, 4, .5)}


def _preset(identity):
    if identity not in _PARAMETERS:
        raise ValueError('Unknown HIL Workbench preset')
    return identity


def _validated_parameters(identity, parameters, *, require_primary=False):
    _preset(identity)
    key, lower, upper, step = _PARAMETERS[identity]
    if (not isinstance(parameters, dict) or set(parameters) - {key, 'start_s', 'duration_s'}
            or require_primary and key not in parameters):
        raise ValueError('Specify the supported HIL fault parameter and optional timing')
    limits = {key: (lower, upper, step), 'start_s': (1, 17, .5), 'duration_s': (.5, 4, .5)}
    result = {}
    for field, value in parameters.items():
        low, high, increment = limits[field]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('HIL parameter must follow its limits and step')
        # Match API timing exactly; 0.5-second grid values need no tolerance.
        on_grid = (value % .5 == 0 if field in {'start_s', 'duration_s'} else
                   math.isclose((value - low) / increment, round((value - low) / increment), abs_tol=1e-9))
        if not on_grid:
            raise ValueError('HIL parameter must follow its limits and step')
        result[field] = int(value) if float(value).is_integer() else value
    start = result.get('start_s', 3 if identity == 'input_delay' else 3.5)
    duration = result.get('duration_s', 4 if identity == 'input_delay' else 1)
    if start + duration > 17.5:
        raise ValueError('HIL fault must end by17.5 seconds to leave a recovery interval')
    return result


def _scenario_ref(value):
    if not isinstance(value, dict) or set(value) != {'id', 'revision', 'configuration_hash', 'manifest_hash'}:
        raise ValueError('Scenario reference requires its identity, revision and both content hashes')
    _object_map_identity(value['id'])
    _object_map_revision(value['revision'])
    if any(not isinstance(value[key], str) or re.fullmatch('[a-f0-9]{64}', value[key]) is None
           for key in ('configuration_hash', 'manifest_hash')):
        raise ValueError('Scenario hashes must be lowercase SHA-256 values')
    return value


class HilDemoClientMixin:
    def hil_sim_scenes(self, *, project_id: str = 'default'):
        """List fixed lead-motion overlays for the existing pinned base scene."""
        return self._request('GET', '/hil-sim/scenes', params={'project_id': _object_map_identity(project_id)}).json()

    def hil_sim_presets(self, *, project_id: str = 'default'):
        """List pinned scenarios and explicitly selected saved software-DUT results."""
        return self._request('GET', '/hil-sim/presets', params={'project_id': _object_map_identity(project_id)}).json()

    def bind_hil_sim_preset(self, preset_id: str, *, scenario_ref: dict, project_id: str = 'default',
                            expected_revision: int = 0, seed: int = 42, saved_execution_id: str | None = None):
        """Pin an immutable scene and optionally a completed default A/B result."""
        _preset(preset_id)
        _object_map_revision(expected_revision, minimum=0)
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise ValueError('Seed must be an integer within 0..2147483647')
        if saved_execution_id is not None:
            _object_map_identity(saved_execution_id)
        return self._request('POST', f'/hil-sim/presets/{preset_id}/bindings', json=dict(
            project_id=_object_map_identity(project_id), expected_revision=expected_revision,
            scenario_ref=_scenario_ref(scenario_ref), seed=seed, saved_execution_id=saved_execution_id)).json()

    def preview_hil_sim_preset(self, preset_id: str, *, expected_binding_revision: int,
                               project_id: str = 'default', parameters: dict | None = None, scene_profile: dict | None = None):
        """Preflight and return a draft; this does not submit or execute it."""
        _preset(preset_id)
        _object_map_revision(expected_binding_revision)
        parameters = {} if parameters is None else parameters
        parameters = _validated_parameters(preset_id, parameters)
        body = dict(
            project_id=_object_map_identity(project_id), expected_binding_revision=expected_binding_revision,
            parameters=parameters)
        if scene_profile is not None:
            from .hil_beta import _scene_ref
            body['scene_profile'] = _scene_ref(scene_profile)
        return self._request('POST', f'/hil-sim/presets/{preset_id}/preview', json=body).json()

    def execute_hil_sim_preview(self, preview: dict, *, idempotency_key: str):
        """Save and submit the exact reviewed preview through the normal queue."""
        if not isinstance(preview, dict) or preview.get('schema_version') not in {'hil-sim-preset-preview/1','hil-sim-test-preview/1'}:
            raise ValueError('A HIL Workbench preset preview is required')
        test_ref = None
        if preview['schema_version'] == 'hil-sim-test-preview/1':
            from .hil_beta import _ref
            test_ref = _ref(preview.get('test_ref'))
            _object_map_identity(preview['experiment']['project_id'])
        created = self.create_experiment(idempotency_key=idempotency_key, **preview['experiment'])
        if created['preview_hash'] != preview['preview']['preview_hash']:
            raise ValueError('Native source changed after preview; no execution was submitted. Preview again and use a new idempotency key.')
        execution = self.submit_experiment(created['id'], preview_hash=created['preview_hash'], idempotency_key=idempotency_key)
        if test_ref is not None:
            from .hil_beta import HilExecutionLinkError
            try:
                self.attach_hil_sim_test_execution(test_ref, execution['id'],
                    project_id=preview['experiment']['project_id'], idempotency_key=idempotency_key)
            except Exception as exc:
                raise HilExecutionLinkError(execution, test_ref) from exc
        return execution
