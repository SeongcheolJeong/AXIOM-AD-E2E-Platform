#!/usr/bin/env python3
"""One bounded, explicitly authorized CARLA plant-response experiment.

This optional-native diagnostic is not a production controller, a platform Run,
calibration, or an automatic retry. Importing this file performs no engine I/O.
Only run(args) imports CARLA. The same worker engine lock and quarantine apply.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import importlib
import json
import math
import os
from pathlib import Path
import queue
import struct
import sys
import tempfile
import threading
import time

from carla_brake_protocol import (DT, STEPS, SETTLED_VERSION, EXECUTABLE_VERSIONS,
    REPORT_VERSION, parse_intent, measurement_sequence, preparation_control,
    settled_preparation_step, settled_preparation_gate, speed_in_window, signed_heading, spatial_check)
from diagnose_carla_self_hit import (CallbackGate, canonical, sha, exclusive_write,
    fsync_dir, identity, identities, inventory_key, pose, vector, require, utc,
    best_effort_event, cleanup_call)

MAX_INTENT_BYTES = 128 * 1024
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_COLLISIONS = 64


class Recorder:
    """The audited hash-chain shape, with explicit entry and total byte caps."""
    def __init__(self, directory):
        self.stream = (directory / 'events.jsonl').open('xb')
        os.chmod(directory / 'events.jsonl', 0o600)
        self.lock = threading.RLock()
        self.sequence, self.previous, self.bytes_written = 0, '0' * 64, 0
        self.failed = False

    def event(self, kind, data):
        with self.lock:
            try:
                require(not self.failed, 'Diagnostic journal already failed; append refused')
                value = {'sequence': self.sequence, 'previous_sha256': self.previous,
                         'at': utc(), 'kind': kind, 'data': data}
                digest = sha(canonical(value))
                content = canonical({**value, 'sha256': digest}) + b'\n'
                require(self.sequence < 12000 and len(content) <= 512 * 1024
                        and self.bytes_written + len(content) <= MAX_JOURNAL_BYTES,
                        'Diagnostic journal budget exhausted')
                require(self.stream.write(content) == len(content), 'Incomplete diagnostic journal write')
                self.stream.flush(); os.fsync(self.stream.fileno())
                self.bytes_written += len(content)
                self.sequence += 1; self.previous = digest
            except Exception:
                self.failed = True
                raise


def command_row(control, *, step=None, time_s=None):
    return {'step': step, 'time_s': time_s, 'control': deepcopy(control),
        'application_status': 'not_attempted', 'world_frame': None,
        'next_world_frame': None, 'next_observation_index': None,
        'rpc_before_world_frame': None, 'rpc_after_world_frame': None,
        'rpc_response': None}


def report_projection(report):
    return {key: value for key, value in report.items() if key not in {'event_chain', 'finished_at'}}


def require_control_readback(control, observation):
    """Compare native float32 command fields; automatic gear stays raw evidence."""
    readback = observation.get('reported_control')
    frame = observation['world_frame']
    require(isinstance(readback, dict) and readback.get('status') == 'available'
        and readback.get('before_world_frame') == frame and readback.get('after_world_frame') == frame
        and isinstance(readback.get('data'), dict), 'Critical control readback unavailable or frame differs')
    data = readback['data']
    for key in ('throttle', 'brake', 'steer'):
        expected = struct.unpack('<f', struct.pack('<f', control[key]))[0]
        require(type(data.get(key)) in (int, float) and math.isfinite(data[key]) and data[key] == expected,
                'Critical control readback differs: ' + key)
    for key in ('hand_brake', 'reverse', 'manual_gear_shift'):
        require(type(data.get(key)) is bool and data[key] is control[key],
                'Critical control readback differs: ' + key)


class BrakeDiagnostic:
    """Single-ego runner; dependencies are explicit for isolated safety tests."""
    def __init__(self, intent, *, carla, client, output, execution_root, recorder, native):
        self.intent, self.protocol = intent, intent['protocol']
        self.policy = self.protocol['policy']
        self.carla, self.client, self.output = carla, client, output
        self.execution_root, self.recorder, self.native = execution_root, recorder, native
        self.nonce = intent['experiment_id'].replace('-', '')
        self.diagnostic_id = 'diagnostic_brake_' + self.nonce
        self.store_id = intent['expected_store_id']
        self.quarantine = execution_root / 'quarantine.json'
        self.reservation = {'execution_id': self.diagnostic_id, 'request_hash': sha(canonical(intent)),
                            'store_id': self.store_id}
        self.reservation_owned = self.changed = self.callbacks_drained = False
        self.world = self.original_settings = self.original_weather = None
        self.expected, self.returned = {}, {}
        self.callback_gate = CallbackGate(); self.callback_errors = queue.Queue(maxsize=4)
        self.deadline = time.monotonic() + self.policy['main_wall_seconds']
        self.report = {'schema_version': REPORT_VERSION, 'experiment_id': intent['experiment_id'],
            'diagnostic_id': self.diagnostic_id, 'store_id': self.store_id,
            'created_at': utc(), 'status': 'failed', 'error': None,
            'intent_sha256': sha(canonical(intent)), 'protocol_sha256': intent['protocol_sha256'],
            'sequence_sha256': intent['sequence_sha256'], 'source_files': intent['source_files'],
            'adapter_version': intent['adapter_version'], 'engine_build': intent['engine_build'],
            'coordinate_convention': 'carla_left_handed_world',
            'angular_velocity_basis': 'official_carla_0.9.16_degrees_per_second_installed_build_separate',
            'source_evidence': intent['source_evidence'], 'original': None,
            'requested_settings': None, 'requested_weather': intent['requested_weather'],
            'spawn': {}, 'physics_configuration': None,
            'rest': {'status': 'not_started', 'policy': None, 'trace': [], 'stable_streak': 0},
            'preparation': {'status': 'not_started', 'mode': self.protocol['initialization'],
                'commands': [], 'observations': [], 'velocity_request': None},
            'measurement': self.initial_measurement(),
            'collision_callbacks': [], 'callback_shutdown': None,
            'inventory_refresh_ticks': 0,
            'cleanup': {'status': 'not_started', 'errors': []},
            'reservation_retained': False, 'release_eligible': False,
            'limitations': [
                'A single Tesla and collision observer are explicit conditions; no lead or LiDAR is present.',
                'This diagnostic applies fixed input sequences, not a perception/policy/controller stack.',
                'Both preparation modes request identical automatic forward gear1/coast settings during rest and release; this differs from historical production initialization.',
                'Requested automatic gear1 is not proof of actual drivetrain gear1. Raw reported gear and telemetry remain separate.',
                'Snapshot acceleration and velocity differences are representations of the same simulator state, not independent sensors.',
                'Optional telemetry fields have unverified native API units; unavailable, nonfinite and null are never zero.',
                'Target velocity API return is not an actor ACK or proof of wheel/body convergence.',
                'No received contact callback does not establish collision-stream completeness or absence of collisions.',
                'Tire configuration readback is not a calibrated friction coefficient, force, torque or braking model.',
                'Callback drain covers accepted Python callback lifetimes, not all native C++ queues; observe process exit separately.',
                'Deadlines bound each RPC and are checked around it; an in-flight native RPC cannot be forcibly cancelled.',
                'Repeated protocol seeds label provenance and do not prove stochastic independence.'],
            'scope': 'One bounded plant-response diagnostic; no platform run, driving verdict or calibration claim.'}
        if self.protocol['schema_version'] == SETTLED_VERSION:
            self.report['preparation'].update(pi_steps=[], pi_state=None, entry_trace=[], entry_gate=None)

    def initial_measurement(self):
        return {'expected_commands': STEPS, 'expected_observations': STEPS + 1,
            'commands': [command_row(item['control'], step=item['step'], time_s=item['time_s'])
                         for item in measurement_sequence(self.protocol)], 'observations': []}

    def observation_bounds(self, position, rotation, velocity, phase):
        return spatial_check(position, rotation, velocity, self.intent['spawn_transform'],
                             phase=phase, protocol=self.protocol)

    def guard(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'Diagnostic wall deadline exceeded')
        identity_path = self.execution_root / 'store-id'
        require(identity_path.is_file() and not identity_path.is_symlink()
                and identity_path.read_text().strip() == self.store_id, 'Execution store identity changed')
        if self.reservation_owned:
            require(self.quarantine.is_file() and not self.quarantine.is_symlink()
                    and self.quarantine.read_bytes() == canonical(self.reservation),
                    'Diagnostic quarantine identity changed')
        self.client.set_timeout(min(self.policy['rpc_seconds'], remaining))
        return remaining

    def read(self, function):
        self.guard(); result = function(); self.guard(); return result

    def event_call(self, operation, function, data=None):
        self.guard()
        self.recorder.event('action_intent', {'operation': operation, **(data or {})})
        result = function()
        self.recorder.event('action_returned', {'operation': operation,
            'return_value': result if type(result) in (str, bool, int, float) or result is None else None,
            'meaning': 'API return only; explicit actor ACK and snapshot readback are separate'})
        self.guard(); return result

    def field_values(self, value, names):
        result = self.native['fields'](value, names)
        require(not result['invalid_fields'] and set(result['values']) == set(names), 'Incomplete world fields')
        return result

    def map_identity(self, world):
        road = self.read(world.get_map); content = self.read(road.to_opendrive).encode()
        require(0 < len(content) <= 20 * 1024 * 1024, 'OpenDRIVE identity exceeds bound')
        return {'name': road.name, 'opendrive_sha256': sha(content), 'opendrive_size_bytes': len(content)}

    def world_state(self, world):
        return {'world_id': str(world.id), 'map': self.map_identity(world),
            'actors': identities(list(self.read(world.get_actors))),
            'settings': self.field_values(self.read(world.get_settings), self.native['SETTINGS']),
            'weather': self.field_values(self.read(world.get_weather), self.native['WEATHER'])}

    def verify_active(self, phase, *, allow_missing=False):
        current = self.read(self.client.get_world)
        require(str(current.id) == self.intent['expected_world']['world_id'], 'World episode changed')
        require(self.field_values(self.read(current.get_settings), self.native['SETTINGS']) == self.report['requested_settings'],
                'Active world settings differ')
        require(self.field_values(self.read(current.get_weather), self.native['WEATHER']) == self.report['requested_weather'],
                'Active world weather differs')
        owned = []
        for role, actor in self.returned.items():
            pinned = self.expected[role]
            ident = {'actor_id': pinned['returned_actor_id'], 'type_id': pinned['type_id'],
                     'role_name': pinned['role_name']}
            require(identity(actor) == ident, 'Returned control target identity differs')
            parent = self.read(lambda a=actor: a.parent)
            require((parent.id if parent else None) == pinned['parent_actor_id'], 'Returned control target parent differs')
            owned.append(ident)
        expected = sorted([*self.intent['expected_world']['actors'], *owned], key=lambda a:a['actor_id'])
        visible = list(self.read(current.get_actors))
        observed = identities(visible)
        ek, ok = inventory_key(expected), inventory_key(observed)
        data = {'phase': phase, 'expected': expected, 'observed': observed,
            'missing': [x for x in expected if (x['actor_id'],x['type_id'],x['role_name']) not in ok],
            'foreign': [x for x in observed if (x['actor_id'],x['type_id'],x['role_name']) not in ek]}
        self.recorder.event('inventory_check', data)
        require(not data['foreign'], 'Foreign/conflicting actor observed')
        for role, actor in self.returned.items():
            for matched in (a for a in visible if a.id == actor.id):
                parent = self.read(lambda a=matched: a.parent)
                require((parent.id if parent else None) == self.expected[role]['parent_actor_id'],
                        'Visible control target parent differs')
        if allow_missing:
            require(inventory_key(data['missing']) <= inventory_key(owned),
                    'Missing actor is not a just-returned owned handle')
        else:
            require(not data['missing'], 'Owned or original actor missing from refreshed inventory')
        return data

    def spawn(self, blueprint, transform, role, parent=None):
        role_name = 'axiom_brake_' + self.nonce + '_' + role
        require(blueprint.has_attribute('role_name'), 'Spawn blueprint lacks role_name')
        blueprint.set_attribute('role_name', role_name)
        self.expected[role] = {'type_id': blueprint.id, 'role_name': role_name,
            'parent_actor_id': parent.id if parent else None, 'requested_transform': pose(transform)}
        self.recorder.event('spawn_identity_intent', {'role': role, **self.expected[role]})
        def issue():
            actor = self.world.spawn_actor(blueprint, transform,
                **({'attach_to': parent, 'attachment_type': self.carla.AttachmentType.Rigid} if parent else {}))
            if actor is not None:
                self.returned[role] = actor  # Before any fallible identity/evidence operation.
                self.expected[role]['returned_actor_id'] = actor.id
            return actor
        actor = self.event_call('world.spawn_actor', issue, {'role': role, **self.expected[role]})
        require(actor is not None and identity(actor) == {'actor_id': actor.id,
            'type_id': blueprint.id, 'role_name': role_name}, 'Returned spawn identity differs')
        actual_parent = self.read(lambda: actor.parent)
        require((actual_parent.id if actual_parent else None) == self.expected[role]['parent_actor_id'], 'Returned spawn parent differs')
        self.report['spawn'][role] = {**self.expected[role], 'returned_identity': identity(actor)}
        self.recorder.event('spawn_identity_returned', {'role': role, **self.report['spawn'][role]})
        return actor

    def collision_callback(self, event):
        if not self.callback_gate.begin(): return
        try:
            row = {'world_frame': int(event.frame), 'source_time_s': float(event.timestamp),
                'other_actor': identity(event.other_actor), 'normal_impulse_native': vector(event.normal_impulse)}
            require(math.isfinite(row['source_time_s']), 'Nonfinite collision timestamp')
            with self.recorder.lock:
                require(len(self.report['collision_callbacks']) < MAX_COLLISIONS, 'Collision callback budget exhausted')
                self.report['collision_callbacks'].append(row)
                self.recorder.event('collision_callback', row)
        except Exception as error:
            try:self.callback_errors.put_nowait(type(error).__name__ + ': ' + str(error)[:800])
            except queue.Full:pass
            best_effort_event(self.recorder, self.report.setdefault('journal_errors', []), 'callback_error', {'error': str(error)[:800]})
        finally:self.callback_gate.end()

    def check_collisions(self):
        require(self.callback_errors.empty(), 'Collision callback failed; retained prefix is incomplete')
        require(not self.report['collision_callbacks'], 'Observed contact aborts this diagnostic')

    def raw_observation(self, snapshot, phase, index):
        ego = self.returned['ego']; observed = snapshot.find(ego.id)
        require(observed is not None, 'Ego absent from coherent WorldSnapshot')
        transform = observed.get_transform()
        data = {'index': index, 'phase': phase, 'world_frame': snapshot.frame,
            'source_time_s': float(snapshot.timestamp.elapsed_seconds), 'delta_seconds': float(snapshot.timestamp.delta_seconds),
            'ego': {**identity(ego), 'position_m': vector(transform.location),
                'rotation_deg': {k:float(getattr(transform.rotation,k)) for k in ('pitch','yaw','roll')},
                'forward_vector': vector(transform.get_forward_vector()), 'snapshot_transform': pose(transform),
                'velocity_mps': vector(observed.get_velocity()), 'acceleration_mps2': vector(observed.get_acceleration()),
                'angular_velocity_deg_s': vector(observed.get_angular_velocity())},
            'reported_control': None, 'telemetry': None}
        require(type(data['world_frame']) is int and all(math.isfinite(data[k]) for k in ('source_time_s','delta_seconds')), 'Invalid snapshot clock')
        data['signed_heading_speed_mps'] = signed_heading(data['ego']['velocity_mps'],data['ego']['rotation_deg']['yaw'])
        data['bounds'] = self.observation_bounds(data['ego']['position_m'], data['ego']['rotation_deg'],
            data['ego']['velocity_mps'], phase)
        return data

    def observe(self, snapshot, phase, rows):
        row = self.raw_observation(snapshot, phase, len(rows))
        rows.append(row); self.recorder.event('observation_raw', row)  # Before auxiliary RPCs or a failed guard.
        require(row['bounds']['status'] == 'within_bounds', 'Spatial/speed bound exceeded: '+str(row['bounds']['failures']))
        outer = self
        class WorldView:
            def get_snapshot(self):return outer.read(outer.world.get_snapshot)
        class ActorView:
            def __getattr__(self, name):
                value = getattr(outer.returned['ego'], name)
                return (lambda *a, **k:outer.read(lambda:value(*a,**k))) if callable(value) else value
        row['reported_control'] = self.native['_control'](WorldView(), snapshot.frame, ActorView())
        row['telemetry'] = self.native['_telemetry'](WorldView(), snapshot.frame, ActorView())
        self.recorder.event('observation_complete', row)
        for name in ('reported_control','telemetry'):
            value = row[name]
            require(value['before_world_frame'] in (None,snapshot.frame) and value['after_world_frame'] in (None,snapshot.frame),
                    'Auxiliary RPC crossed snapshot frame')
        self.check_collisions()
        return row

    def tick(self, previous, phase, *, inventory=True):
        if inventory:self.verify_active('before_tick:'+phase)
        self.check_collisions()
        frame = self.event_call('world.tick.'+phase, lambda:self.world.tick(seconds=min(self.policy['rpc_seconds'],self.guard())))
        snapshot = self.read(self.world.get_snapshot)
        row = {'phase':phase,'returned_world_frame':frame,'world_frame':snapshot.frame,
            'source_time_s':float(snapshot.timestamp.elapsed_seconds),'delta_seconds':float(snapshot.timestamp.delta_seconds)}
        self.recorder.event('tick_observed', row)
        require(type(frame) is int and frame == snapshot.frame == previous.frame+1 and
            math.isfinite(row['source_time_s']) and math.isfinite(row['delta_seconds']) and
            abs(row['delta_seconds']-DT)<=1e-6 and abs(row['source_time_s']-previous.timestamp.elapsed_seconds-DT)<=1e-6,
            'World frame/time did not advance exactly one configured tick')
        return snapshot

    def send_control(self, snapshot, row, *, allow_just_spawned_missing=False):
        self.verify_active('before_control', allow_missing=allow_just_spawned_missing)
        before = self.read(self.world.get_snapshot)
        require(before.frame == snapshot.frame, 'World advanced before control dispatch')
        # Abort on contacts already received since the last observation. This
        # check and the later transport call are not an atomic callback barrier.
        self.check_collisions()
        row['world_frame'] = snapshot.frame; row['rpc_before_world_frame'] = before.frame
        self.recorder.event('control_intent', row)
        row['application_status'] = 'send_outcome_unknown'
        self.guard()
        responses = self.client.apply_batch_sync([self.carla.command.ApplyVehicleControl(
            self.returned['ego'].id, self.carla.VehicleControl(**row['control']))], False)
        require(type(responses) in (list,tuple) and len(responses)==1, 'Control response denominator differs')
        response = responses[0]
        if response.has_error():
            row['rpc_response'] = {'actor_id':None, 'error':str(response.error)}
            row['application_status'] = 'send_rejected'
        else:
            require(type(response.actor_id) is int, 'Control ACK actor ID is invalid')
            row['rpc_response'] = {'actor_id':response.actor_id,'error':None}
            if response.actor_id == self.returned['ego'].id:row['application_status'] = 'sent_acknowledged'
        self.recorder.event('control_response', row)  # Preserve parsed ACK before the next fallible RPC.
        after = self.read(self.world.get_snapshot); row['rpc_after_world_frame'] = after.frame
        self.recorder.event('control_ack', row)
        require(row['application_status']=='sent_acknowledged' and after.frame==snapshot.frame,
                'Control ACK identity/outcome/frame differs')

    def link_transition(self, command, observation):
        require(command['application_status']=='sent_acknowledged' and
            observation['world_frame']==command['world_frame']+1, 'Command/next observation identity differs')
        require_control_readback(command['control'], observation)
        command.update(application_status='observed_next_frame', next_world_frame=observation['world_frame'],
                       next_observation_index=observation['index'])
        self.recorder.event('control_transition', command)

    def publish_reservation(self):
        directory = self.execution_root / 'brake-diagnostics'
        if not directory.exists():directory.mkdir(mode=0o700); fsync_dir(self.execution_root)
        require(directory.is_dir() and not directory.is_symlink(), 'Unsafe diagnostic marker directory')
        exclusive_write(directory/(self.intent['experiment_id']+'.json'), canonical(self.reservation))
        descriptor, name = tempfile.mkstemp(prefix='.brake-reservation-',dir=self.execution_root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(canonical(self.reservation));stream.flush();os.fsync(stream.fileno())
            os.link(temporary,self.quarantine)
            self.reservation_owned = True  # Link exists even if fsync/evidence fails next.
            fsync_dir(self.execution_root)
        finally:temporary.unlink(missing_ok=True)
        self.report['reservation_retained'] = True
        self.recorder.event('diagnostic_reservation',self.reservation)

    def preflight(self):
        self.guard()
        require(self.read(self.client.get_server_version)==self.read(self.client.get_client_version)==self.intent['engine_build'], 'Pinned engine/client build differs')
        self.world = self.read(self.client.get_world)
        original = self.world_state(self.world)
        self.report['original'] = original;self.recorder.event('world_original',original)
        require(original==self.intent['expected_world'], 'Original world differs from pinned intent; no mutation allowed')
        self.original_settings,self.original_weather = self.read(self.world.get_settings),self.read(self.world.get_weather)
        requested = self.read(self.world.get_settings)
        requested.synchronous_mode,requested.fixed_delta_seconds = True,DT
        requested.substepping,requested.max_substep_delta_time,requested.max_substeps = True,.01,10
        self.report['requested_settings'] = self.field_values(requested,self.native['SETTINGS'])
        require(set(self.intent['requested_weather']['values'])==set(self.native['WEATHER']), 'Requested weather field set differs')
        weather = self.carla.WeatherParameters()
        for key,value in self.intent['requested_weather']['values'].items():setattr(weather,key,value)
        require(self.field_values(weather,self.native['WEATHER'])==self.intent['requested_weather'], 'Requested weather native representation differs')
        self.publish_reservation()
        self.changed = True  # A timed-out mutation may have taken effect.
        self.event_call('world.apply_settings',lambda:self.world.apply_settings(requested),self.report['requested_settings'])
        self.event_call('world.set_weather',lambda:self.world.set_weather(weather),self.report['requested_weather'])
        p=self.intent['spawn_transform']
        transform=self.carla.Transform(self.carla.Location(**p['position_m']),self.carla.Rotation(**p['rotation_deg']))
        library=self.read(self.world.get_blueprint_library)
        ego=self.spawn(library.find(self.policy['vehicle_blueprint']),transform,'ego')
        collision=self.spawn(library.find('sensor.other.collision'),self.carla.Transform(),'collision',ego)
        physics=self.read(ego.get_physics_control);wheels=list(physics.wheels)
        require(len(wheels)==4,'Exactly four wheel physics records required')
        for wheel in wheels:wheel.tire_friction=self.policy['tire_friction']
        physics.wheels=wheels
        self.event_call('ego.apply_physics_control',lambda:ego.apply_physics_control(physics),{'requested_tire_friction':self.policy['tire_friction']})
        self.event_call('collision.listen',lambda:collision.listen(self.collision_callback),{'actor_id':collision.id})
        snapshot=self.read(self.world.get_snapshot)
        rest_command=command_row(self.policy['rest_control'])
        self.report['rest']['initial_control']=rest_command
        self.send_control(snapshot,rest_command,allow_just_spawned_missing=True)
        inventory=self.verify_active('after_spawn',allow_missing=True)
        if inventory['missing']:
            self.report['inventory_refresh_ticks']=1
            snapshot=self.tick(snapshot,'inventory_refresh',inventory=False)
            refreshed=self.observe(snapshot,'inventory_refresh',self.report.setdefault('inventory_refresh_observations',[]))
            self.link_transition(rest_command,refreshed)
            self.verify_active('after_single_inventory_refresh')
        return snapshot

    def rest(self,snapshot):
        policy=self.native['StartupPolicy'](min_ticks=8,max_ticks=100,consecutive_stable_samples=5,
            max_linear_speed_mps=.03,max_acceleration_mps2=.1)
        rest=self.report['rest'];rest.update(status='running',policy=asdict(policy))
        previous=None;started=time.monotonic()
        for index in range(1,101):
            require(time.monotonic()-started<=policy.max_wall_time_s,'Rest phase wall deadline exceeded')
            snapshot=self.tick(snapshot,'rest')
            actor=self.native['_actor_state'](snapshot,self.returned['ego'].id,'ego',previous,policy)
            sample={'tick':index,'world_frame':snapshot.frame,'source_time_s':float(snapshot.timestamp.elapsed_seconds),
                'actor':actor,'stable_streak':rest['stable_streak']+1 if actor['stable'] else 0}
            rest['trace'].append(sample);rest['stable_streak']=sample['stable_streak']
            self.recorder.event('rest_sample',sample)
            require(not actor['invalid_fields'],'Invalid rest actor sample')
            observation=self.observe(snapshot,'rest',rest.setdefault('observations',[]))
            require_control_readback(self.policy['rest_control'], observation)
            if rest['initial_control']['application_status']=='sent_acknowledged':
                self.link_transition(rest['initial_control'],observation)
            previous=actor
            if index>=8 and rest['stable_streak']>=5:
                rest['status']='passed';rest['after_rest']=observation
                break
        require(rest['status']=='passed','Bounded rest preparation did not converge')
        outer=self
        class WorldView:
            def get_snapshot(self):return outer.read(outer.world.get_snapshot)
        class ActorView:
            id=self.returned['ego'].id
            def get_physics_control(self):return outer.read(outer.returned['ego'].get_physics_control)
        config=self.native['_physics'](WorldView(),snapshot.frame,ActorView(),self.policy['tire_friction'])
        self.report['physics_configuration']=config;self.recorder.event('post_rest_physics_readback',config)
        require(config['status']=='available' and config['friction_status']=='matched','Post-rest friction readback failed')
        require(canonical({k:config[k] for k in ('scalars','wheels')})==canonical(self.intent['expected_physics']),
                'Post-rest complete pinned physics readback differs')
        return snapshot

    def prepare(self,snapshot):
        preparation=self.report['preparation'];preparation['status']='running'
        self.observe(snapshot,'preparation',preparation['observations'])
        if self.protocol['schema_version']==SETTLED_VERSION:
            return self.prepare_settled(snapshot)
        if self.protocol['initialization']=='velocity_injection':
            row=command_row(self.policy['rest_control']);preparation['commands'].append(row)
            self.send_control(snapshot,row)
            forward=snapshot.find(self.returned['ego'].id).get_transform().get_forward_vector()
            direction=vector(forward)
            velocity={k:direction[k]*2. for k in ('x','y','z')}
            request={'actor_id':self.returned['ego'].id,'after_world_frame':snapshot.frame,
                'requested_speed_mps':2.,'velocity_mps':velocity,'operation':'actor.set_target_velocity',
                'ack_status':'unknown','returned_without_ack':False}
            preparation['velocity_request']=request;self.recorder.event('velocity_request',request)
            self.verify_active('before_initial_velocity')
            require(self.read(self.world.get_snapshot).frame == snapshot.frame, 'World advanced before initial velocity request')
            self.guard();self.returned['ego'].set_target_velocity(self.carla.Vector3D(**velocity))
            request['returned_without_ack']=True;self.recorder.event('velocity_return',request)
            snapshot=self.tick(snapshot,'velocity_injection')
            observed=self.observe(snapshot,'preparation',preparation['observations']);self.link_transition(row,observed)
            require(speed_in_window(observed['signed_heading_speed_mps']),'Injected initialization did not enter the fixed speed window')
        else:
            streak=0
            for index in range(self.policy['preparation_max_ticks']):
                speed=preparation['observations'][-1]['signed_heading_speed_mps']
                row=command_row(preparation_control(self.protocol,speed),step=index,time_s=index*DT)
                preparation['commands'].append(row);self.send_control(snapshot,row)
                snapshot=self.tick(snapshot,'throttle_preparation')
                observed=self.observe(snapshot,'preparation',preparation['observations']);self.link_transition(row,observed)
                streak=streak+1 if speed_in_window(observed['signed_heading_speed_mps']) else 0
                preparation['stable_streak']=streak
                if streak>=self.policy['preparation_stable_samples']:break
            require(streak>=self.policy['preparation_stable_samples'],'Throttle preparation did not converge; no velocity fallback')
        preparation['status']='passed';return snapshot

    def preparation_entry(self):
        preparation=self.report['preparation']
        gate=settled_preparation_gate(self.protocol,preparation['observations'],self.intent['spawn_transform'])
        preparation['entry_trace'].append(deepcopy(gate))
        preparation['entry_gate']=deepcopy(gate)
        self.recorder.event('preparation_entry',gate)
        return gate

    def prepare_settled(self,snapshot):
        """Fixed PI requests and first-crossing admission; never hunt past the plane."""
        preparation=self.report['preparation']
        gate=self.preparation_entry()
        require(not gate['plane_reached'],'Preparation already reached the entry plane before its first command')
        for index in range(self.policy['preparation_max_ticks']):
            proposed=settled_preparation_step(self.protocol,preparation['observations'][-1],preparation['pi_state'])
            row=command_row(proposed['control'],step=index,time_s=index*DT)
            preparation['commands'].append(row)
            preparation['pi_steps'].append(deepcopy(proposed['trace']))
            self.recorder.event('preparation_pi_step',{'command_index':index,**proposed['trace']})
            self.send_control(snapshot,row)
            snapshot=self.tick(snapshot,'throttle_preparation')
            observed=self.observe(snapshot,'preparation',preparation['observations'])
            self.link_transition(row,observed)
            # A proposed state is not a completed actuation. Keep the previous
            # committed state if ACK, clock, raw observation or readback fails.
            preparation['pi_state']=deepcopy(proposed['state'])
            gate=self.preparation_entry()
            if gate['plane_reached']:
                require(gate['admitted'],'First entry-plane crossing failed the fixed settled admission gate')
                preparation['status']='passed'
                return snapshot
        raise RuntimeError('Settled preparation did not reach an admitted entry plane; no velocity fallback')

    def measurement_entry(self,initial):
        preparation=self.report['preparation'];measurement=self.report['measurement']
        require(preparation['status']=='passed' and preparation['observations'],
                'Measurement requires completed settled preparation')
        previous=preparation['observations'][-1]
        gate=settled_preparation_gate(self.protocol,preparation['observations'],self.intent['spawn_transform'])
        require(gate==preparation['entry_gate'] and gate['admitted'],
                'Stored preparation entry gate differs or is not admitted')
        # Re-reading auxiliary RPCs does not advance the physics snapshot. Keep
        # both readings and require the current one to satisfy the same gate.
        for key in ('world_frame','source_time_s','delta_seconds','ego','signed_heading_speed_mps','bounds'):
            require(initial[key]==previous[key],'Measurement F0 differs from the admitted preparation snapshot: '+key)
        require_control_readback(preparation['commands'][-1]['control'],initial)
        current=settled_preparation_gate(self.protocol,[*preparation['observations'][:-1],initial],self.intent['spawn_transform'])
        measurement['entry_gate']=deepcopy(current)
        measurement['entry_observation_frame']=initial['world_frame']
        self.recorder.event('measurement_entry',{'entry_gate':current,'entry_observation_frame':initial['world_frame']})
        require(current['admitted'],'Measurement F0 no longer satisfies settled entry admission')

    def measure(self,snapshot):
        measurement=self.report['measurement']
        initial=self.observe(snapshot,'measurement',measurement['observations'])
        settled=self.protocol['schema_version']==SETTLED_VERSION
        if settled:self.measurement_entry(initial)
        else:require(speed_in_window(initial['signed_heading_speed_mps']), 'Measurement initial speed outside fixed window')
        for command in measurement['commands']:
            if not settled and self.protocol['measurement']=='pulse' and command['step']==self.policy['pulse_start_step']:
                require(speed_in_window(measurement['observations'][-1]['signed_heading_speed_mps']),
                        'Pulse entry speed outside fixed window; no corrective control is permitted')
            self.send_control(snapshot,command)
            snapshot=self.tick(snapshot,'measurement')
            observed=self.observe(snapshot,'measurement',measurement['observations'])
            self.link_transition(command,observed)
        require(len(measurement['observations'])==81,'Measurement observation denominator differs')
        self.check_collisions();self.report['status']='collected'

    def cleanup_actor_call(self, role, actor, pinned, operation):
        """Recheck before mutation, never read a successfully destroyed handle afterward."""
        current=self.read(self.client.get_world)
        require(str(current.id)==self.intent['expected_world']['world_id'], 'Cleanup episode changed before actor mutation')
        visible=list(self.read(current.get_actors))
        matches=[a for a in visible if a.id==pinned['actor_id']]
        require(len(matches)<=1, 'Cleanup target ID is ambiguous')
        for target in [actor,*matches]:
            require(identity(target)==pinned, 'Cleanup target identity changed before mutation')
            parent=self.read(lambda a=target:a.parent)
            require((parent.id if parent else None)==self.expected[role]['parent_actor_id'],
                    'Cleanup target parent changed before mutation')
        self.guard()
        return getattr(actor,operation)()

    def cleanup_settings_candidates(self):
        return (self.intent['expected_world']['settings'], self.report['requested_settings'])

    def cleanup(self):
        self.deadline=time.monotonic()+self.policy['cleanup_wall_seconds']
        result=self.report['cleanup'];errors=result['errors'];result['status']='checking'
        self.callback_gate.stop_accepting();drain_attempted=False
        if not self.changed:
            self.callbacks_drained=self.callback_gate.drain(self.policy['callback_drain_seconds'])
            result['status']='not_started' if self.callbacks_drained else 'uncertain'
            self.report['callback_shutdown']={**self.callback_gate.snapshot(),'drained':self.callbacks_drained}
            return
        try:
            current=self.read(self.client.get_world)
            require(str(current.id)==self.intent['expected_world']['world_id'],'Cleanup episode differs')
            require(self.map_identity(current)==self.intent['expected_world']['map'],'Cleanup map differs')
            visible=list(self.read(current.get_actors));original=self.intent['expected_world']
            require(inventory_key(original['actors'])<=inventory_key(identities(visible)),'An original actor identity disappeared')
            owned=[]
            for role,actor in self.returned.items():
                try:
                    expected=self.expected[role];actual=identity(actor)
                    require(actual=={'actor_id':expected['returned_actor_id'],'type_id':expected['type_id'],
                        'role_name':'axiom_brake_'+self.nonce+'_'+role} and actual['role_name']==expected['role_name'],
                        'Retained actor identity differs')
                    matches=[a for a in visible if a.id==actor.id]
                    require(len(matches)<=1,'Ambiguous visible actor ID')
                    for matched in [actor,*matches]:
                        require(identity(matched)==actual,'Visible identity conflicts with returned handle')
                        parent=self.read(lambda a=matched:a.parent)
                        require((parent.id if parent else None)==expected['parent_actor_id'],'Owned actor parent differs')
                    owned.append((role,actor,actual))  # Cache before destruction; never call destroyed handles again.
                except Exception as error:errors.append({'phase':'identity','role':role,'error':str(error)[:800]})
            for role in self.expected.keys()-self.returned.keys():
                errors.append({'phase':'identity','role':role,'error':'Spawn outcome has no returned ownership handle'})
            allowed=inventory_key(original['actors'])|inventory_key(identities(self.returned.values()))
            foreign=[identity(a) for a in visible if (a.id,a.type_id,dict(a.attributes).get('role_name')) not in allowed]
            if foreign:errors.append({'phase':'inventory','foreign_actors':foreign})
            destroyed=set()
            for role,actor,ident in owned:
                if not ident['type_id'].startswith('sensor.'):continue
                try:cleanup_call(self.recorder,errors,self.guard,'sensor.stop',
                    lambda r=role,a=actor,i=ident:self.cleanup_actor_call(r,a,i,'stop'),{'role':role,**ident})
                except Exception as error:errors.append({'phase':'sensor_stop','role':role,'error':str(error)[:800]})
                try:
                    ack=cleanup_call(self.recorder,errors,self.guard,'actor.destroy',
                        lambda r=role,a=actor,i=ident:self.cleanup_actor_call(r,a,i,'destroy'),{'role':role,**ident})
                    require(ack is True,'Sensor destruction lacks explicit ACK');destroyed.add(role)
                except Exception as error:errors.append({'phase':'destroy','role':role,'error':str(error)[:800]})
            drain_attempted=True
            self.callbacks_drained=self.callback_gate.drain(min(self.policy['callback_drain_seconds'],max(0.,self.deadline-time.monotonic())))
            require(self.callbacks_drained,'Callbacks still active; journal and parents retained')
            sensor_roles={r for r,e in self.expected.items() if e['type_id'].startswith('sensor.')}
            require(sensor_roles<=destroyed,'Sensor removal is uncertain; parent and synchronous world retained')
            for role,actor,ident in owned:
                if ident['type_id'].startswith('sensor.'):continue
                try:
                    ack=cleanup_call(self.recorder,errors,self.guard,'actor.destroy',
                        lambda r=role,a=actor,i=ident:self.cleanup_actor_call(r,a,i,'destroy'),{'role':role,**ident})
                    require(ack is True,'Vehicle destruction lacks explicit ACK');destroyed.add(role)
                except Exception as error:errors.append({'phase':'destroy','role':role,'error':str(error)[:800]})
            require(destroyed==set(self.expected),'Not all spawn outcomes have acknowledged removal')
            require(not foreign,'Foreign actors prevent automatic world restoration')
            require(self.field_values(self.read(current.get_settings),self.native['SETTINGS']) in self.cleanup_settings_candidates(), 'Cleanup settings changed externally')
            require(self.field_values(self.read(current.get_weather),self.native['WEATHER']) in (original['weather'],self.report['requested_weather']), 'Cleanup weather changed externally')
            cleanup_call(self.recorder,errors,self.guard,'world.restore_weather',lambda:current.set_weather(self.original_weather))
            cleanup_call(self.recorder,errors,self.guard,'world.restore_settings',lambda:current.apply_settings(self.original_settings))
            frame=self.read(current.get_snapshot).frame
            while self.read(current.get_snapshot).frame<=frame:time.sleep(min(.01,self.guard()))
            final=self.world_state(self.read(self.client.get_world));result['observed']=final
            require(final==original,'Final fresh snapshot/world/settings/weather/original inventory differs')
            require(not errors,'Cleanup had unresolved operations or journal failures')
            result.update(status='confirmed',owned_actors_remaining=0,original_actors=len(original['actors']))
        except Exception as error:
            errors.append({'phase':'verification','error':type(error).__name__+': '+str(error)[:1000]});result['status']='uncertain'
        finally:
            if not drain_attempted:self.callbacks_drained=self.callback_gate.drain(min(self.policy['callback_drain_seconds'],max(0.,self.deadline-time.monotonic())))
            self.report['callback_shutdown']={**self.callback_gate.snapshot(),'drained':self.callbacks_drained}
            if not self.callbacks_drained:
                result['status']='uncertain';errors.append({'phase':'callbacks','error':'Callback still active; journal must remain open'})
            elif not best_effort_event(self.recorder,errors,'cleanup_result',result):result['status']='uncertain'
        # Deliberately retain the marker until the final event and report are durable.

    def finalize(self):
        report=self.report
        if getattr(self.recorder,'failed',False):
            report.setdefault('journal_errors',[]).append({'phase':'journal',
                'error':'Recorder encountered a write/serialization failure; the journal is not sealed'})
        if report['cleanup']['status']=='uncertain' or self.callback_errors.qsize() or report['collision_callbacks']:
            report['status']='failed'
        report['reservation_retained']=self.reservation_owned
        report['release_eligible']=bool(self.reservation_owned and self.callbacks_drained and report['cleanup']['status']=='confirmed'
            and not report.get('journal_errors'))
        report['finished_at']=utc()
        final={'status':report['status'],'error':report['error'],'cleanup_status':report['cleanup']['status'],
            'reservation_retained':self.reservation_owned,'report_projection_sha256':sha(canonical(report_projection(report)))}
        if not self.callbacks_drained or getattr(self.recorder,'failed',False):
            report['event_chain']={'entries':self.recorder.sequence,'head_sha256':self.recorder.previous,'sealed':False}
        else:
            self.recorder.event('diagnostic_final',final)
            report['event_chain']={'entries':self.recorder.sequence,'head_sha256':self.recorder.previous,'sealed':True}
        content=canonical(report);require(len(content)<=MAX_REPORT_BYTES,'Final diagnostic report exceeds byte cap')
        exclusive_write(self.output/'report.json',content)
        if report['release_eligible']:
            self.guard()
            try:
                self.quarantine.unlink();fsync_dir(self.execution_root);self.reservation_owned=False
                exclusive_write(self.output/'reservation-release.json',canonical({'schema_version':'carla-brake-reservation-release/1',
                    'experiment_id':self.intent['experiment_id'],'diagnostic_id':self.diagnostic_id,'store_id':self.store_id,
                    'report_sha256':sha(content),'status':'released','released_at':utc()}))
            except Exception:
                # Receipt failure cannot manufacture a successful final release.
                if not self.quarantine.exists() and not self.quarantine.is_symlink():
                    exclusive_write(self.quarantine,canonical(self.reservation));self.reservation_owned=True
                raise
        return 0 if report['status']=='collected' and report['cleanup']['status']=='confirmed' and not self.reservation_owned else 1


def run(args):
    require(args.host=='127.0.0.1' and args.port==2000,'Only the declared local native endpoint is allowed')
    require(args.intent.is_file() and not args.intent.is_symlink(), 'Intent must be an ordinary file')
    with args.intent.open('rb') as stream:raw=stream.read(MAX_INTENT_BYTES+1)
    require(len(raw)<=MAX_INTENT_BYTES,'Intent exceeds byte cap')
    intent=parse_intent(raw)
    require(intent['protocol']['schema_version'] in EXECUTABLE_VERSIONS,
            'New diagnostic executions require protocol2 or protocol3; protocol1 is retained for historical reading only')
    require(raw==canonical(intent),'Intent must use exact canonical protocol bytes')
    require(not Path(__file__).is_symlink(), 'Diagnostic entry point must not be a symlink')
    here=Path(__file__).resolve().parent
    for name,digest in intent['source_files'].items():
        source=here/name
        require(source.is_file() and not source.is_symlink() and sha(source.read_bytes())==digest,
                'Diagnostic source hash differs or is a symlink: '+name)
    require(os.getenv('ADE2E_CARLA_EXCLUSIVE')=='1','Explicit dedicated exclusive native environment required')
    execution_root=args.runtime_dir/'executions'
    require(execution_root.is_dir() and not execution_root.is_symlink(),'Existing execution store required')
    store_path=execution_root/'store-id'
    require(store_path.is_file() and not store_path.is_symlink() and store_path.read_text().strip()==intent['expected_store_id'],'Store UUID differs')
    # Optional imports happen only after bounded/pinned input validation.
    import carla
    from axiom_carla.version import ADAPTER_VERSION
    from axiom_carla.spool import file_lock
    from axiom_carla.native_journal import fields, SETTINGS, WEATHER
    from axiom_carla.startup import StartupPolicy, _actor_state
    from axiom_carla.vehicle_response import _control, _telemetry, _physics
    require(ADAPTER_VERSION=='0.8.0','This diagnostic requires the pinned optional adapter0.8.0')
    require(set(intent['expected_world']['settings']['values'])==set(SETTINGS) and
            set(intent['expected_world']['weather']['values'])==set(WEATHER) and
            set(intent['requested_weather']['values'])==set(WEATHER),'Pinned world field set differs')
    args.output.mkdir(mode=0o700,parents=False,exist_ok=False)
    exclusive_write(args.output/'source-intent.json',raw)
    recorder=Recorder(args.output)
    native=dict(fields=fields,SETTINGS=SETTINGS,WEATHER=WEATHER,StartupPolicy=StartupPolicy,
        _actor_state=_actor_state,_control=_control,_telemetry=_telemetry,_physics=_physics)
    diagnostic=BrakeDiagnostic(intent,carla=carla,client=carla.Client(args.host,args.port),
        output=args.output,execution_root=execution_root,recorder=recorder,native=native)
    diagnostic.report['installed_module_hashes']={name:sha(Path(importlib.import_module(name).__file__).read_bytes())
        for name in ('axiom_carla.startup','axiom_carla.vehicle_response','axiom_carla.native_journal','axiom_carla.spool','axiom_carla.version')}
    recorder.event('diagnostic_started',{key:diagnostic.report[key] for key in ('experiment_id','intent_sha256','protocol_sha256','sequence_sha256','source_files')})
    result=1
    try:
        with file_lock(execution_root/'engine.lock'):
            try:
                require(not diagnostic.quarantine.exists() and not diagnostic.quarantine.is_symlink(),'Existing quarantine forbids diagnostic')
                snapshot=diagnostic.preflight()
                snapshot=diagnostic.rest(snapshot)
                snapshot=diagnostic.prepare(snapshot)
                diagnostic.measure(snapshot)
            except Exception as error:
                diagnostic.report['error']={'type':type(error).__name__,'message':str(error)[:1400]}
                best_effort_event(recorder,diagnostic.report.setdefault('journal_errors',[]),'diagnostic_error',diagnostic.report['error'])
            finally:diagnostic.cleanup()
            result=diagnostic.finalize()
    except Exception as error:
        # Lock or final-publication failure: no retry, no fabricated sealed report.
        if not diagnostic.changed:
            diagnostic.callback_gate.stop_accepting()
            diagnostic.callbacks_drained=diagnostic.callback_gate.drain(diagnostic.policy['callback_drain_seconds'])
        if not (args.output/'report.json').exists():
            diagnostic.report.update(status='failed',error={'type':type(error).__name__,'message':str(error)[:1400]},
                reservation_retained=diagnostic.reservation_owned,release_eligible=False,finished_at=utc(),
                event_chain={'entries':recorder.sequence,'head_sha256':recorder.previous,'sealed':False})
            content=canonical(diagnostic.report)
            require(len(content)<=MAX_REPORT_BYTES,'Failure report exceeds byte cap')
            exclusive_write(args.output/'report.json',content)
        raise
    finally:
        if diagnostic.callbacks_drained:recorder.stream.close()
    print(json.dumps({'status':diagnostic.report['status'],'experiment_id':intent['experiment_id'],
        'observations':len(diagnostic.report['measurement']['observations']),
        'cleanup':diagnostic.report['cleanup']['status'],'reservation_retained':diagnostic.reservation_owned,
        'output':str(args.output)},sort_keys=True))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent',type=Path,required=True)
    parser.add_argument('--runtime-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=2000)
    return run(parser.parse_args())


if __name__=='__main__':
    raise SystemExit(main())
