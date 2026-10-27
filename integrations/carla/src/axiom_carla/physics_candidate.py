"""Explicit, bounded world-physics candidate; never a production default.

The original request hash identifies the scenario/stack only. The separate
candidate_input_hash also binds the world-physics profile. A standalone caller
must additionally preserve its source bundle and must not publish this as an
ordinary 0.8.0 platform execution.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ade2e.adapters import AdapterInputError
from ade2e.schemas import ArtifactPayload
from .native_journal import SETTINGS, fields

VERSION = 'carla-world-physics-candidate/1'
PRECISION_VERSION = 'carla-world-physics-candidate/2'
FIELDS = ('synchronous_mode', 'fixed_delta_seconds', 'substepping',
          'max_substep_delta_time', 'max_substeps')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def profile(substep_s):
    if type(substep_s) not in (int, float) or substep_s not in (.01, .005, .001, .0005):
        raise AdapterInputError('Candidate substep must be explicitly 0.01, 0.005, 0.001 or 0.0005 seconds')
    # Version1 bytes/defaults stay unchanged. Version2 is an explicit bounded
    # convergence candidate, not permission to change normal-worker defaults.
    precise = substep_s in (.001, .0005)
    return {'schema_version': PRECISION_VERSION if precise else VERSION, 'synchronous_mode': True,
            'fixed_delta_seconds': .05, 'substepping': True,
            'max_substep_delta_time': float(substep_s),
            'max_substeps': round(.05 / substep_s) if precise else 10}


class CandidateEvidence:
    def __init__(self, value, request, dt):
        if type(value) is not dict or canonical(value) != canonical(profile(value.get('max_substep_delta_time'))):
            raise AdapterInputError('Candidate profile differs from its bounded versioned contract')
        if dt != .05 or request.scenario.duration_s != 4.0:
            raise AdapterInputError('Candidate commissioning requires the original 4-second / 20Hz trial')
        self.profile = deepcopy(value)
        bound = {'schema_version': 'carla-physics-candidate-input/1',
                 'base_input_hash': request.input_hash, 'physics_profile': self.profile}
        self.data = {**bound, 'candidate_input_hash': hashlib.sha256(canonical(bound)).hexdigest(),
            'execution_id': request.execution_id, 'application_stage': 'before_actor_spawn_and_rest',
            'initialization': 'unchanged_production_rest_then_target_velocity_request',
            'status': 'not_applied', 'target_settings': None, 'readbacks': [],
            'scope': 'Standalone candidate, not an ordinary production run. Substep cap is not a measured solver step count.'}

    def configure(self, settings):
        for name in FIELDS:
            setattr(settings, name, self.profile[name])
        self.data['target_settings'] = fields(settings, SETTINGS)
        self.data['status'] = 'requested'

    def observe(self, world, snapshot, phase):
        observed = fields(world.get_settings(), SETTINGS)
        row = {'phase': phase, 'world_frame': snapshot.frame,
               'source_time_s': float(snapshot.timestamp.elapsed_seconds),
               'settings': observed, 'matched': observed == self.data['target_settings']}
        self.data['readbacks'].append(row)
        if len(self.data['readbacks']) > 84:
            raise RuntimeError('Candidate settings observation bound exceeded')
        self.data['status'] = 'applied_and_read_back' if row['matched'] else 'readback_failed'
        if not row['matched']:
            raise RuntimeError('Candidate world physics settings readback differs')

    def artifact(self):
        return ArtifactPayload(name='carla-physics-candidate.json', kind='execution_diagnostic',
                               media_type='application/json', content=canonical(self.data))
