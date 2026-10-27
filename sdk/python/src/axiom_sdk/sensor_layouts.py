"""Offline sensor placement revisions and explicit normal-queue execution."""
from __future__ import annotations
import copy
import math
from .experiments import _object_map_identity, _object_map_revision
from .hil_beta import _ref, _hash, _headers, _text, _page


def _rig(value):
    if not isinstance(value, dict) or value.get('schema_version') != 'carla-driving-multisensor-spec/1':
        raise ValueError('rig_b must be a full driving multisensor specification')
    if (value.get('duration_seconds') != 20 or value.get('sample_hz') != 10 or value.get('spawn_index') != 0
            or value.get('expected_map') != 'Town10HD_Opt' or value.get('rig_revision') != 1
            or value.get('controller') != dict(id='native-map-lane-follow/1', target_speed_mps=3, lookahead_m=6, route_length_m=150)):
        raise ValueError('Placement beta fixes the 20 second Town10HD spawn 0 driving profile')
    sensors = value.get('sensors')
    if not isinstance(sensors, list) or len(sensors) != 4 or [item.get('key') for item in sensors if isinstance(item, dict)] != ['front', 'rear', 'lidar', 'radar']:
        raise ValueError('Placement beta preserves the four standard sensors')
    for sensor, kind in zip(sensors, ('rgb', 'rgb', 'lidar', 'radar')):
        if sensor.get('kind') != kind or sensor.get('parameters') != {}:
            raise ValueError('Only mounts can change; sensor kinds and parameters are fixed')
        for field, maximum, step in (('translation_m', 10, .05), ('rotation_deg', 180, 1)):
            values = sensor.get(field)
            if not isinstance(values, (list, tuple)) or len(values) != 3 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or abs(v) > maximum
                    or not math.isclose(v / step, round(v / step), rel_tol=0, abs_tol=1e-8) for v in values):
                raise ValueError('Mounts need three finite bounded values at 0.05 meter and 1 degree steps')
    return copy.deepcopy(value)


def _query(project_id, revision):
    params = {'project_id': _object_map_identity(project_id)}
    if revision is not None:
        _object_map_revision(revision)
        params['revision'] = revision
    return params


class SensorLayoutsClientMixin:
    def sensor_sim_layout_templates(self, *, project_id='default'):
        return self._request('GET', '/sensor-sim/layouts/templates', params=_query(project_id, None)).json()

    def sensor_sim_layouts(self, *, project_id='default', limit=100, offset=0):
        return self._request('GET', '/sensor-sim/layouts', params=_page(project_id, limit, offset)).json()

    def sensor_sim_layout(self, layout_id, *, project_id='default', revision=None):
        return self._request('GET', f'/sensor-sim/layouts/{_object_map_identity(layout_id)}', params=_query(project_id, revision)).json()

    def sensor_sim_layout_revisions(self, layout_id, *, project_id='default', limit=100, offset=0):
        return self._request('GET', f'/sensor-sim/layouts/{_object_map_identity(layout_id)}/revisions', params=_page(project_id, limit, offset)).json()

    def save_sensor_sim_layout(self, *, name, rig_b, idempotency_key, project_id='default', layout_id=None,
                               expected_revision=0, note='', copied_from=None):
        """Save immutable mounts offline. No native preview or execution is requested."""
        _object_map_revision(expected_revision, minimum=0)
        body = dict(project_id=_object_map_identity(project_id), name=_text(name, 180), note=_text(note, 4000, empty=True),
                    rig_b=_rig(rig_b), expected_revision=expected_revision)
        if layout_id is not None:
            body['id'] = _object_map_identity(layout_id)
        if copied_from is not None:
            body['copied_from'] = _ref(copied_from)
        return self._request('POST', '/sensor-sim/layouts', json=body, headers=_headers(idempotency_key)).json()

    def clone_sensor_sim_layout(self, layout_ref, *, name, idempotency_key, project_id='default'):
        """Clone the explicit revision, retaining its exact source reference."""
        reference = _ref(layout_ref)
        original = self.sensor_sim_layout(reference['id'], project_id=project_id, revision=reference['revision'])
        if {key: original.get(key) for key in ('id', 'revision', 'sha256')} != reference:
            raise ValueError('Loaded layout differs from the requested revision/hash')
        return self.save_sensor_sim_layout(name=name, note=original['note'], rig_b=original['rig_b'],
            copied_from=reference, project_id=project_id, idempotency_key=idempotency_key)

    def preview_sensor_sim_layout(self, layout_ref, *, project_id='default'):
        reference = _ref(layout_ref)
        return self._request('POST', f'/sensor-sim/layouts/{reference["id"]}/preview',
            json=dict(project_id=_object_map_identity(project_id), layout_ref=reference)).json()

    def execute_sensor_sim_layout(self, layout_ref, *, preview_hash, idempotency_key, project_id='default'):
        """Atomically create, queue and link the reviewed layout; retry with the same key."""
        reference = _ref(layout_ref)
        return self._request('POST', f'/sensor-sim/layouts/{reference["id"]}/executions',
            json=dict(project_id=_object_map_identity(project_id), layout_ref=reference, preview_hash=_hash(preview_hash)),
            headers=_headers(idempotency_key)).json()

    def sensor_sim_layout_executions(self, layout_id, *, project_id='default', revision=None, limit=100, offset=0):
        params = {**_page(project_id, limit, offset), **_query(project_id, revision)}
        return self._request('GET', f'/sensor-sim/layouts/{_object_map_identity(layout_id)}/executions', params=params).json()

    def create_sensor_sim_layout_observation(self, *, layout_ref, execution_id, sensor_key, time_s, title, note,
                                             idempotency_key, project_id='default', side='both', kind='observation'):
        if sensor_key not in {'front', 'rear', 'lidar', 'radar'} or side not in {'rig_a', 'rig_b', 'both'} or kind not in {'observation', 'issue'}:
            raise ValueError('Unsupported placement sensor, side or observation kind')
        if type(time_s) not in (int, float) or not math.isfinite(time_s) or not 0 <= time_s <= 19.9:
            raise ValueError('Observation time must be within the retained 0..19.9 second timeline')
        return self._request('POST', '/sensor-sim/layout-observations', headers=_headers(idempotency_key),
            json=dict(project_id=_object_map_identity(project_id), layout_ref=_ref(layout_ref),
                execution_id=_object_map_identity(execution_id), sensor_key=sensor_key, side=side, time_s=time_s,
                title=_text(title, 180), note=_text(note, 4000), kind=kind)).json()

    def sensor_sim_layout_observations(self, *, project_id='default', execution_id=None, layout_id=None,
                                       revision=None, limit=100, offset=0):
        params = _page(project_id, limit, offset, maximum=200)
        for key, value in (('execution_id', execution_id), ('layout_id', layout_id)):
            if value is not None:
                params[key] = _object_map_identity(value)
        if revision is not None:
            if layout_id is None:
                raise ValueError('Observation revision filtering requires layout_id')
            _object_map_revision(revision)
            params['revision'] = revision
        return self._request('GET', '/sensor-sim/layout-observations', params=params).json()

    def export_sensor_sim_layout(self, layout_id, *, project_id='default', revision=None):
        return self._request('GET', f'/sensor-sim/layouts/{_object_map_identity(layout_id)}/export', params=_query(project_id, revision)).json()
