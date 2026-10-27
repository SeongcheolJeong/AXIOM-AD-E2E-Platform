"""Physical mount and callback lifetime for configuration-based CARLA sensors.

Native conversion is checked against the installed client matrix before spawn.
See pinned CARLA 1ae5356 geom/Rotation.h and geom/Transform.h for its convention.
No simulator import is needed until the caller supplies its installed client.
"""
from __future__ import annotations

import copy
import math
import threading
import time

from ade2e.simulation_context import execution_correlation
from ade2e.simulation_sensor_pose import (POLICY, SPAWN_ROTATION_TOLERANCE,
    declared_matrix, f32, matrix, mount_row_analysis, reflect, residual, typed_attributes)


def configured(context):
    return context.schema_version == 'simulation-control-context/2'


def native_mount(carla, mount):
    requested = declared_matrix(mount)
    native = reflect(requested)
    pitch = math.atan2(native[2][0], math.hypot(native[0][0],native[1][0]))
    if math.hypot(native[0][0],native[1][0]) > 1e-10:
        yaw = math.atan2(native[1][0], native[0][0])
        roll = math.atan2(-native[2][1], native[2][2])
    else:
        yaw = math.atan2(-native[0][1], native[1][1])
        roll = 0.0
    angles = {key:f32(math.degrees(value)) for key,value in zip(('pitch','yaw','roll'),(pitch,yaw,roll))}
    xyz = [f32(native[i][3]) for i in range(3)]
    pose = carla.Transform(carla.Location(x=xyz[0],y=xyz[1],z=xyz[2]), carla.Rotation(**angles))
    actual = matrix(pose.get_matrix())
    translation, rotation = residual(actual,native)
    if translation > 1e-6 or rotation > SPAWN_ROTATION_TOLERANCE:
        raise ValueError('Installed native transform cannot represent the pinned rig mount')
    return pose, {'policy':'carla_rh_rig_to_lh_native_matrix/1', 'declared_rh_matrix':requested,
                  'requested_native_matrix':native, 'native_spawn_matrix':actual,
                  'native_location_m':[float(getattr(pose.location,key)) for key in ('x','y','z')],
                  'native_rotation_deg':{key:float(getattr(pose.rotation,key)) for key in angles}}


class CallbackGate:
    """Drain accepted Python callbacks; this does not certify transport closure."""
    def __init__(self):
        self.condition = threading.Condition()
        self.accepting = True
        self.started = self.finished = self.active = self.rejected_after_stop = 0
        self.drained = False

    def wrap(self, callback):
        def receive(value):
            with self.condition:
                if not self.accepting:
                    self.rejected_after_stop += 1
                    return
                self.started += 1
                self.active += 1
            try:
                return callback(value)
            finally:
                with self.condition:
                    self.active -= 1
                    self.finished += 1
                    self.condition.notify_all()
        return receive

    def stop_accepting(self):
        with self.condition:
            self.accepting = False

    def drain(self, timeout=2.0):
        self.stop_accepting()
        end = time.monotonic()+timeout
        with self.condition:
            while self.active:
                left = end-time.monotonic()
                if left <= 0:
                    raise ValueError('Accepted sensor callbacks did not finish within the drain bound')
                self.condition.wait(left)
            self.drained = True

    def snapshot(self):
        with self.condition:
            return {key:getattr(self,key) for key in ('accepting','started','finished','active','rejected_after_stop','drained')}


class SensorConfigurationEvidence:
    def __init__(self, request):
        self.request = request
        self.gate = CallbackGate()
        self.spawn = None
        self.detached_attributes = {}
        self.actor_attributes = None
        self.startup_mount = None
        self.observations = []

    def read_actor_attributes(self, sensor):
        requested = self.request.context.sensor_profile.attributes
        # These strings are the spawned actor's description, observed separately
        # from the detached blueprint. They do not expose the internal RNG state.
        raw = sensor.attributes
        if any(name not in raw for name in requested):
            raise ValueError('Spawned sensor does not expose every promised attribute')
        raw = {name:raw[name] for name in requested}
        actual = typed_attributes(raw)
        self.actor_attributes = {'raw':raw,'typed':actual,'qualified':actual == typed_attributes(requested)}
        if not self.actor_attributes['qualified']:
            raise ValueError('Spawned sensor attributes differ from the pinned configuration')

    def observe_mount(self, world, snapshot, sensor, ego, cloud=None):
        before = world.get_snapshot().frame
        observed_ego, observed_sensor = snapshot.find(ego.id), snapshot.find(sensor.id)
        if observed_ego is None or observed_sensor is None:
            raise ValueError('Coherent snapshot lacks the returned ego/sensor identity')
        parent = sensor.parent
        row = {'world_frame':snapshot.frame,'source_time_s':float(snapshot.timestamp.elapsed_seconds),
               'bracket_before_frame':before,'sensor_actor_id':sensor.id,'ego_actor_id':ego.id,
               'parent_actor_id':None if parent is None else parent.id,'sensor_type_id':sensor.type_id,
               'ego_world_matrix':matrix(observed_ego.get_transform().get_matrix()),
               'sensor_world_matrix':matrix(observed_sensor.get_transform().get_matrix()),
               'sensor_api_world_matrix':matrix(sensor.get_transform().get_matrix()),
               'measurement_world_matrix':None if cloud is None else matrix(cloud.transform.get_matrix()),
               'measurement_frame':None if cloud is None else cloud.frame,
               'measurement_time_s':None if cloud is None else float(cloud.timestamp),
               'bracket_after_frame':world.get_snapshot().frame}
        row['analysis'] = mount_row_analysis(row,self.request.context.sensor_profile.mount)
        if cloud is None:
            self.startup_mount = row
        else:
            if len(self.observations) >= 81:
                raise ValueError('Configured sensor pose observations exceed their bound')
            self.observations.append(row)
        if not row['analysis']['qualified']:
            raise ValueError('Sensor physical mount readback failed: '+', '.join(row['analysis']['reasons']))
        return row

    def payload(self, *, completed, phase, error):
        context = self.request.context
        profile = context.sensor_profile
        return {'schema_version':'carla-sensor-configuration/1',**execution_correlation(self.request),
                'status':'completed' if completed else 'partial','failure_phase':None if completed else phase,
                'error':None if completed else error,'sensor_frame':profile.sensor_frame,
                'configuration_manifest_hash':context.sensor_configuration_snapshot.manifest_hash,
                'rig_manifest_hash':context.rig_snapshot.manifest_hash,'sensor_profile_hash':profile.profile_hash,
                'policy':POLICY,'requested':{'attributes':dict(profile.attributes),'mount':profile.mount.model_dump(mode='json')},
                'spawn':copy.deepcopy(self.spawn),'detached_attributes':copy.deepcopy(self.detached_attributes),
                'actor_attributes':copy.deepcopy(self.actor_attributes),'startup_mount':copy.deepcopy(self.startup_mount),
                'observations':copy.deepcopy(self.observations),'callback_drain':self.gate.snapshot()}
