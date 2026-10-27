"""Bounded reference software DUT. JSON-lines only; no CARLA dependency."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import sys
try:
    from . import city_control
except ImportError:
    import city_control

VERSION = 'reference-traffic-dut/2'
MAX_MESSAGE = 256 * 1024
MAX_OBJECT_AGE_S = .25
RECOVERY_STEPS = 3


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_dut_profile(profile):
    """Validate a frozen catalog record without importing the platform or CARLA."""
    fields = {'schema_version', 'project_id', 'id', 'revision', 'name', 'note',
              'parameters', 'created_at', 'author', 'sha256'}
    if (not isinstance(profile, dict) or set(profile) != fields
            or profile['schema_version'] != 'hil-dut-profile/1'
            or type(profile['revision']) is not int or profile['revision'] < 1
            or any(not isinstance(profile[k], str) or not profile[k] for k in
                   ('project_id', 'id', 'name', 'created_at'))
            or not isinstance(profile['note'], str)):
        raise ValueError('Unsupported frozen DUT profile identity')
    parameters, author = profile['parameters'], profile['author']
    if (not isinstance(author, dict) or set(author) != {'user_label', 'role'}
            or any(not isinstance(v, str) or not v for v in author.values())):
        raise ValueError('Unsupported frozen DUT profile author')
    if (not isinstance(parameters, dict) or set(parameters) != {'max_object_age_ms', 'recovery_fresh_steps'}
            or any(type(value) is not int for value in parameters.values())
            or not 50 <= parameters['max_object_age_ms'] <= 1000 or parameters['max_object_age_ms'] % 50
            or not 1 <= parameters['recovery_fresh_steps'] <= 10):
        raise ValueError('DUT profile requires integer age50..1000ms step50 and recovery1..10')
    content = {key: value for key, value in profile.items() if key != 'sha256'}
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != profile['sha256']:
        raise ValueError('Frozen DUT profile hash mismatch')
    return copy.deepcopy(profile)


def traffic_headway(ego, objects, route, target_speed, headway_s=1.5):
    """Same bounded actor-origin headway rule as the native traffic controller."""
    origin = city_control.project(ego, route)['progress_m']
    ahead = []
    for obj in objects:
        projected = city_control.project(obj, route)
        gap = projected['progress_m'] - origin
        if 0 < gap < 60 and projected['error_m'] < 2.2:
            ahead.append((gap, obj['id']))
    if not ahead:
        return target_speed, None
    gap, identity = min(ahead)
    return min(target_speed, max(0., (gap - 6) / max(1., headway_s))), {'id': identity, 'origin_gap_m': gap}


def state_check(state):
    if not isinstance(state, dict) or not all(finite(state.get(k)) for k in ('x', 'y', 'z', 'heading_rad', 'speed_mps')):
        raise ValueError('DUT state is incomplete or nonfinite')
    if not isinstance(state.get('id'), str) or not 0 <= state['speed_mps'] <= 100:
        raise ValueError('DUT actor identity/speed invalid')


class ReferenceDut:
    def __init__(self, init):
        if init.get('schema_version') != 'hil-dut-init/1' or init.get('op') != 'init':
            raise ValueError('Unsupported DUT initialization')
        self.profile = validate_dut_profile(init['dut_profile']) if 'dut_profile' in init else None
        self.max_object_age_s = self.profile['parameters']['max_object_age_ms'] / 1000 if self.profile else MAX_OBJECT_AGE_S
        self.recovery_steps = self.profile['parameters']['recovery_fresh_steps'] if self.profile else RECOVERY_STEPS
        self.route = init['route']
        if not isinstance(self.route, list) or not 2 <= len(self.route) <= 1000:
            raise ValueError('DUT route exceeds bounded profile')
        for point in self.route:
            if not all(finite(point.get(k)) for k in ('x', 'y', 'z')):
                raise ValueError('DUT route contains invalid coordinates')
        city_control.lengths(self.route)
        self.speed = init['target_speed_mps']
        if not finite(self.speed) or not 0 <= self.speed <= 4:
            raise ValueError('DUT speed exceeds reference profile')
        self.physics = init['physics']
        self.sequence = -1
        self.object_sequence = -1
        self.packet_fingerprint = None
        self.duplicate_packets = 0
        self.missing_packets = 0
        self.previous = None
        self.previous_steering = None
        self.degraded = False
        self.fresh_steps = 0

    def step(self, message):
        sequence, now = message.get('sequence'), message.get('time_s')
        if (message.get('schema_version') != 'hil-dut-step/1' or message.get('op') != 'step'
                or type(sequence) is not int or sequence != self.sequence + 1
                or not finite(now) or abs(now - sequence * .05) > 1e-6):
            raise ValueError('DUT sequence or 20 Hz simulation clock mismatch')
        ego, packet = message['ego'], message['object_packet']
        state_check(ego)
        object_sequence, object_time = packet.get('sequence'), packet.get('time_s')
        if (type(object_sequence) is not int or not self.object_sequence <= object_sequence <= sequence
                or not finite(object_time) or abs(object_time - object_sequence * .05) > 1e-6
                or object_time > now + 1e-8 or not isinstance(packet.get('objects'), list) or len(packet['objects']) > 32):
            raise ValueError('DUT object packet identity/clock invalid')
        ids = set()
        for obj in packet['objects']:
            state_check(obj)
            if obj['id'] == ego['id'] or obj['id'] in ids:
                raise ValueError('DUT object identities duplicate ego or one another')
            ids.add(obj['id'])
        age = max(0., now - object_time)
        fresh = age <= self.max_object_age_s + 1e-8
        new_packet = object_sequence > self.object_sequence
        present = message.get('object_delivery_present', True)
        fingerprint = json.dumps(packet, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if type(present) is not bool or (not present and new_packet):
            raise ValueError('DUT delivery presence contradicts packet sequence')
        if not new_packet and fingerprint != self.packet_fingerprint:
            raise ValueError('DUT repeated sequence changed packet contents')
        duplicate = present and not new_packet
        self.duplicate_packets += int(duplicate)
        self.missing_packets += int(not present)
        self.packet_fingerprint = fingerprint
        if not fresh:
            self.degraded = True
            self.fresh_steps = 0
        elif self.degraded:
            self.fresh_steps = self.fresh_steps + 1 if new_packet else 0
            if self.fresh_steps >= self.recovery_steps:
                self.degraded = False
        desired, lead = traffic_headway(ego, packet['objects'], self.route, self.speed)
        command, plan = city_control.feedback(ego, self.route, desired,
            wheelbase_m=self.physics['wheelbase_m'], max_steer_rad=self.physics['max_steer_angle_rad'],
            rear_axle_offset_m=self.physics.get('rear_axle_offset_m', 0.),
            previous=self.previous, previous_steering=self.previous_steering, end_behavior='stop', stop=self.degraded)
        if self.degraded:
            command = {'throttle': 0., 'brake': 1., 'steer': command['steer']}
        state = 'degraded' if not fresh else 'recovering' if self.degraded else 'tracking'
        plan.update(hil_state=state, object_age_s=age, lead=lead)
        self.previous, self.previous_steering = plan['progress_m'], plan['steering_progress_m']
        self.sequence, self.object_sequence = sequence, object_sequence
        return {'schema_version': 'hil-dut-reply/1', 'op': 'step', 'sequence': sequence,
                'object_sequence': object_sequence, 'object_age_s': age, 'state': state,
                'duplicate_packet_detected': duplicate, 'duplicate_packet_count': self.duplicate_packets,
                'missing_packet_count': self.missing_packets,
                'fresh_recovery_steps': self.fresh_steps, 'control': command, 'plan': plan}


def main():
    dut = None
    while True:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not raw:
            return
        if len(raw) > MAX_MESSAGE or not raw.endswith(b'\n'):
            raise ValueError('DUT protocol message exceeds bound')
        message = json.loads(raw)
        if message.get('op') == 'init' and dut is None:
            dut = ReferenceDut(message)
            reply = {'schema_version': 'hil-dut-reply/1', 'op': 'init', 'status': 'ready',
                     'pid': os.getpid(), 'dut_version': VERSION}
            if dut.profile is not None:
                reply['dut_profile'] = dut.profile
        elif message.get('op') == 'step' and dut is not None:
            reply = dut.step(message)
        elif message.get('op') == 'close' and dut is not None:
            print(json.dumps({'schema_version': 'hil-dut-reply/1', 'op': 'close', 'status': 'closed'}), flush=True)
            return
        else:
            raise ValueError('DUT protocol state transition invalid')
        print(json.dumps(reply, allow_nan=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
