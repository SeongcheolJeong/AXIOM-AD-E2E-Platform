"""Neural Sim authoring on the same versioned API as the browser and agent tools."""
import math
from .experiments import _object_map_identity, _object_map_revision

PRESETS = {'left', 'right', 'raised', 'sweep'}


def _preset(identity):
    if identity not in PRESETS:
        raise ValueError('Unknown Neural Sim preset')
    return identity


def _seed(seed):
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError('Seed must be an integer within 0..2147483647')
    return seed


class NeuralDemoClientMixin:
    def neural_sim_sources(self, *, project_id='default'):
        """List completed original sources, trained scenes and renders for explicit selection."""
        return self._request('GET', '/neural-sim/sources', params={'project_id': _object_map_identity(project_id)}).json()

    def neural_sim_presets(self, *, project_id='default'):
        return self._request('GET', '/neural-sim/presets', params={'project_id': _object_map_identity(project_id)}).json()

    def bind_neural_sim_preset(self, preset_id, *, source_execution_id, reconstruction_execution_id=None,
                               saved_execution_id=None, project_id='default', expected_revision=0, seed=42):
        """Pin an exact source, optional trained scene and completed default render."""
        _preset(preset_id)
        for identity in (source_execution_id, reconstruction_execution_id, saved_execution_id):
            if identity is not None:
                _object_map_identity(identity)
        return self._request('POST', f'/neural-sim/presets/{preset_id}/bindings', json=dict(
            project_id=_object_map_identity(project_id), expected_revision=_object_map_revision(expected_revision, minimum=0),
            source_execution_id=_object_map_identity(source_execution_id), reconstruction_execution_id=reconstruction_execution_id,
            saved_execution_id=saved_execution_id, seed=_seed(seed))).json()

    def preview_neural_sim_preset(self, preset_id, *, expected_binding_revision, project_id='default',
                                  parameters=None, mode=None, frame_index=None):
        """Preflight one frame or the complete shifted camera sequence without execution."""
        _preset(preset_id)
        mode = ('camera_sweep' if preset_id == 'sweep' else 'sequence') if mode is None else mode
        parameters = {} if parameters is None else parameters
        if not isinstance(parameters, dict) or set(parameters) - {'lateral_m', 'height_m'}:
            raise ValueError('Unsupported Neural Sim view parameters')
        for key, value in parameters.items():
            bound = (2 if mode == 'camera_sweep' else .5) if key == 'lateral_m' else .3
            if (type(value) not in (int, float) or not math.isfinite(value) or abs(value) > bound
                    or key == 'lateral_m' and mode == 'camera_sweep' and value <= 0):
                raise ValueError('Neural Sim offsets must be finite numbers within the demo bounds')
        if mode not in ('frame', 'sequence', 'camera_sweep') or (frame_index is not None and (type(frame_index) is not int or not 0 <= frame_index <= 199)):
            raise ValueError('Neural Sim requires frame/sequence/camera_sweep mode and a source frame index within 0..199')
        return self._request('POST', f'/neural-sim/presets/{preset_id}/preview', json=dict(
            project_id=_object_map_identity(project_id), expected_binding_revision=_object_map_revision(expected_binding_revision),
            parameters=parameters, mode=mode, frame_index=frame_index)).json()

    def preview_neural_reconstruction(self, source_execution_id, *, project_id='default', seed=42,
                                      name='Neural Sim · source scene reconstruction'):
        """Prepare the fixed front-camera source reconstruction through Experiment."""
        return self.preview_experiment(project_id=_object_map_identity(project_id), name=name,
            kind='neural_reconstruction', adapter_id='neural-splatfacto', adapter_version='1.0.0',
            input=dict(source_execution_id=_object_map_identity(source_execution_id), rig='rig_a', camera='front'), seed=_seed(seed))

    def execute_neural_sim_preview(self, preview, *, idempotency_key):
        """Save and submit the reviewed render draft with the normal drift guard."""
        if not isinstance(preview, dict) or preview.get('schema_version') not in ('neural-sim-preset-preview/1', 'neural-sim-perception-preview/1'):
            raise ValueError('A Neural Sim preset preview is required')
        created = self.create_experiment(idempotency_key=idempotency_key, **preview['experiment'])
        if created['preview_hash'] != preview['preview']['preview_hash']:
            raise ValueError('Neural source changed after preview; no execution was submitted. Preview again with a new idempotency key.')
        return self.submit_experiment(created['id'], preview_hash=created['preview_hash'], idempotency_key=idempotency_key)

    def preview_neural_source_import(self, source_id, *, project_id='default', seed=42,
                                     name='Neural Sim · recorded real-road source'):
        """Publish one operator-configured original source through the normal queue."""
        return self.preview_experiment(project_id=_object_map_identity(project_id), name=name,
            kind='neural_source_import', adapter_id='neural-splatfacto', adapter_version='1.0.0',
            input={'source_id': _object_map_identity(source_id)}, seed=_seed(seed))

    def preview_neural_perception(self, render_execution_id, *, model_id, model_revision, project_id='default',
                                  frame_indices=None, score_threshold=.35, iou_threshold=.45, max_detections=100, seed=42):
        """Review actual detector inference over 3–5 original/baseline/novel triplets."""
        indices = [0, 15, 30, 45, 60] if frame_indices is None else frame_indices
        if (not isinstance(indices, list) or not 3 <= len(indices) <= 5
                or any(type(value) is not int or not 0 <= value <= 199 for value in indices)
                or indices != sorted(set(indices))):
            raise ValueError('Select 3–5 distinct ordered render frame indices within 0..199')
        for threshold in (score_threshold, iou_threshold):
            if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
                raise ValueError('Inference thresholds must be finite numbers within 0..1')
        if type(max_detections) is not int or not 1 <= max_detections <= 300:
            raise ValueError('max_detections must be within 1..300')
        return self._request('POST', '/neural-sim/perception/preview', json=dict(
            project_id=_object_map_identity(project_id), render_execution_id=_object_map_identity(render_execution_id),
            model_id=_object_map_identity(model_id), model_revision=_object_map_revision(model_revision), frame_indices=indices,
            score_threshold=score_threshold, iou_threshold=iou_threshold, max_detections=max_detections, seed=_seed(seed))).json()

    def neural_perception_result(self, execution_id, *, project_id='default'):
        return self._request('GET', f'/neural-sim/perception/{_object_map_identity(execution_id)}',
            params={'project_id': _object_map_identity(project_id)}).json()

    def capture_neural_perception_case(self, execution_id, *, frame_index, title, note, idempotency_key,
                                       project_id='default', selected_variant='novel', assignee=''):
        if type(frame_index) is not int or not 0 <= frame_index <= 199:
            raise ValueError('frame_index must be within 0..199')
        if selected_variant not in ('original', 'baseline', 'novel'):
            raise ValueError('Unknown Neural image variant')
        return self._request('POST', f'/neural-sim/perception/{_object_map_identity(execution_id)}/case',
            json=dict(project_id=_object_map_identity(project_id), frame_index=frame_index, title=title, note=note,
                selected_variant=selected_variant, assignee=assignee), headers={'Idempotency-Key': idempotency_key}).json()
