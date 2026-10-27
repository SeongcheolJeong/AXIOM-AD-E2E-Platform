"""Local shape validation; topology and native spawn remain server/engine checks."""
import math
import re
from .perception import _identity, _digest_metadata


def _number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def _scenario_ref(value):
    if not isinstance(value, dict) or set(value) != {'id', 'revision', 'configuration_hash', 'manifest_hash'}:
        raise ValueError('Scenario reference requires id, revision, configuration_hash and manifest_hash')
    _identity(value['id'])
    if type(value['revision']) is not int or value['revision'] < 1:
        raise ValueError('Scenario revision must be a positive integer')
    for key in ('configuration_hash', 'manifest_hash'):
        _digest_metadata(value[key], 1, 1)
    return value


def _actor_edits(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('Actor edits must be an object with at most 32 entries')
    for identity, edit in value.items():
        _identity(identity)
        if not isinstance(edit, dict) or set(edit) - {'speed_mps', 'pose', 'destination', 'actions'}:
            raise ValueError('Actor edits support speed_mps, pose, destination and actions')
        for field, item in edit.items():
            if field == 'speed_mps' and not _number(item, 0, 12):
                raise ValueError('Actor speed must be finite and between 0 and 12 m/s')
            if field in {'pose', 'destination'}:
                if not isinstance(item, dict) or set(item) != {'x', 'y', 'z', 'heading_rad'} or any(
                        not _number(v, -6.284 if k == 'heading_rad' else -10000,
                                       6.284 if k == 'heading_rad' else 10000) for k, v in item.items()):
                    raise ValueError('Actor pose requires finite bounded x, y, z and heading_rad')
            if field == 'actions':
                if not isinstance(item, list) or len(item) > 16:
                    raise ValueError('Actor actions must be a list with at most 16 entries')
                for action in item:
                    if not isinstance(action, dict) or not isinstance(action.get('id'), str) or not isinstance(action.get('kind'), str):
                        raise ValueError('Actions require id and kind; behavior semantics are checked by the API')
                    for key, maximum in [('speed_mps', 12), ('duration_s', 120), ('timeout_s', 120)]:
                        if action.get(key) is not None and not _number(action[key], 0, maximum):
                            raise ValueError('Behavior numeric field is outside the supported range')
    return value


def _observation(value):
    _scenario_ref(value['scenario_ref'])
    if not _number(value['time_s'], 0, 120):
        raise ValueError('Observation time must be finite and between 0 and 120 seconds')
    for field, maximum in [('title', 180), ('note', 4000)]:
        text = value[field]
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise ValueError('Observation title and note must be nonempty bounded text')
        if re.search(r'(?i)(?:\bbearer\s+\S+|\baxiom_[A-Za-z0-9_-]{16,}|\b(?:api[_ -]?token|access[_ -]?token|password|authorization)\s*[:=]\s*\S+)', text):
            raise ValueError('Remove credentials from the observation')
    if value['kind'] not in {'observation', 'issue'} or value['behavior_exercised'] not in {'observed', 'not_observed', 'uncertain'} or value['ego_outcome'] not in {'arrived', 'stopped', 'collision', 'other', 'uncertain'}:
        raise ValueError('Unsupported observation classification')
    return value
