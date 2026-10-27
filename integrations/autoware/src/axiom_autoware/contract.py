"""Pure contracts and coordinate transforms; no simulator/ROS imports."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path


class ContractError(ValueError):
    pass


LEGACY_PID_SHA256 = "20c25b86dc54c8a6a43fcd602a1b5e642f2b8f43ae9e5101aadaa3ff66465206"
LEGACY_PID_SOURCE_SHA256 = "d06f35b02f7da90da69f1ee73e1626d833855f8dbd566aaeae1a1874b3ba14f8"


def acceleration_semantics(is_defined, pid_binary_sha256):
    if is_defined:
        return "explicit_message_flag"
    if pid_binary_sha256 == LEGACY_PID_SHA256:
        # Upstream0.50 createCtrlCmdMsg assigns acceleration without setting the
        # newly introduced flag. Bind this compatibility to the exact binary.
        return "pinned_pid_0.50_legacy_acceleration_field"
    raise ContractError("native controller did not define acceleration")


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{name} must be a finite number")
    return float(value)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def carla_to_ros_pose(pose, rear_axle_offset_m=0.0):
    """CARLA actor center LH -> ROS rear axle RH. Angles are radians throughout."""
    x, y, z, yaw = (number(pose.get(k, 0.0), k) for k in ("x", "y", "z", "yaw_rad"))
    offset = number(rear_axle_offset_m, "rear_axle_offset_m")
    return {"x": x - offset * math.cos(yaw), "y": -(y - offset * math.sin(yaw)), "z": z, "yaw_rad": -yaw}


def ros_to_carla_pose(pose, rear_axle_offset_m=0.0):
    x, y, z, yaw = (number(pose.get(k, 0.0), k) for k in ("x", "y", "z", "yaw_rad"))
    offset = number(rear_axle_offset_m, "rear_axle_offset_m")
    return {"x": x + offset * math.cos(yaw), "y": -(y + offset * math.sin(yaw)), "z": z, "yaw_rad": -yaw}


def rear_axle_kinematics(ego, offset_m, yaw_acceleration_rps2):
    """Planar rigid-body velocity/acceleration at the ROS rear-axle origin.

    Native vectors are world-space at CARLA actor center. The angular derivative
    uses the same LH convention as native yaw rate; outputs are RH body vectors.
    """
    yaw = number(ego["pose"]["yaw_rad"], "yaw")
    offset = number(offset_m, "rear axle offset")
    omega = -number(ego["yaw_rate_rps"], "yaw rate")
    alpha = -number(yaw_acceleration_rps2, "yaw acceleration")
    def body(vector):
        x, y = number(vector["x"], "world x"), number(vector["y"], "world y")
        return (x * math.cos(yaw) + y * math.sin(yaw),
                x * math.sin(yaw) - y * math.cos(yaw))
    vx, vy = body(ego["velocity"])
    ax, ay = body(ego["acceleration"])
    # r = (-offset, 0): v_rear = v_center + omega x r;
    # a_rear = a_center + alpha x r + omega x (omega x r).
    return {"longitudinal_velocity": vx, "lateral_velocity": vy - omega * offset,
            "longitudinal_acceleration": ax + omega * omega * offset,
            "lateral_acceleration": ay - alpha * offset, "yaw_rate": omega}


def bbox_corners_ros(actor):
    """Eight GT corners, with the supplied pose explicitly at bbox center."""
    pose = carla_to_ros_pose(actor["pose"])
    half = [number(actor["dimensions"][key], key) / 2 for key in ("length", "width", "height")]
    if any(value <= 0 for value in half):
        raise ContractError("bounding-box dimensions must be positive")
    c, s = math.cos(pose["yaw_rad"]), math.sin(pose["yaw_rad"])
    return [(pose["x"] + x*c - y*s, pose["y"] + x*s + y*c, pose["z"] + z)
            for x in (-half[0], half[0]) for y in (-half[1], half[1]) for z in (-half[2], half[2])]


class EpisodeClock:
    """Reject duplicate/backwards frames and commands outside the observed clock."""
    def __init__(self, dt_s, max_command_age_s=0.25):
        self.dt = number(dt_s, "dt_s")
        if not 0.02 <= self.dt <= 0.1:
            raise ContractError("Autoware GT profile supports 0.02 <= dt_s <= 0.1")
        self.max_age = number(max_command_age_s, "max_command_age_s")
        if not self.dt <= self.max_age <= 0.5:
            raise ContractError("command age must be between dt_s and 0.5 seconds")
        self.frame = None
        self.time = None

    def observe(self, frame, sim_time_s):
        now = number(sim_time_s, "sim_time_s")
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0 or now < 0:
            raise ContractError("invalid frame/time")
        if self.frame is not None and (frame <= self.frame or now <= self.time):
            raise ContractError("episode frame and time must increase monotonically")
        if self.frame is not None and frame != self.frame + 1:
            raise ContractError("non-contiguous CARLA frame; another client may have ticked")
        if self.time is not None and abs((now-self.time)-self.dt) > max(1e-4, self.dt * .02):
            raise ContractError("clock step mismatch; AXIOM must remain the only tick owner")
        self.frame, self.time = frame, now

    def command_age(self, stamp_s):
        stamp = number(stamp_s, "command stamp")
        if self.time is None:
            raise ContractError("no observation")
        age = self.time-stamp
        if age < -1e-5:
            raise ContractError("future Autoware command")
        if age > self.max_age:
            raise ContractError("stale Autoware command")
        return max(0.0, age)


class PedalMap:
    """Invert the installed upstream CARLA CSV at a measured vehicle speed.

    This is an upstream simulation calibration, not physical vehicle calibration.
    Source CSV bytes are hashed and retained in the episode receipt.
    """
    def __init__(self, path, limit):
        self.path = Path(path)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.speeds = [float(v) for v in rows[0][1:]]
        self.rows = [(float(r[0]), [float(v) for v in r[1:]]) for r in rows[1:]]
        self.limit = limit
        if len(self.speeds) < 2 or any(b <= a for a,b in zip(self.speeds, self.speeds[1:])):
            raise ContractError("invalid upstream pedal map")

    def curve(self, speed):
        speed = min(max(number(speed, "speed"), self.speeds[0]), self.speeds[-1])
        j = next((j for j in range(len(self.speeds)-1) if speed <= self.speeds[j+1]), len(self.speeds)-2)
        t = (speed-self.speeds[j])/(self.speeds[j+1]-self.speeds[j])
        return [(pedal, values[j] + t*(values[j+1]-values[j])) for pedal,values in self.rows]

    def invert(self, acceleration, speed):
        curve = sorted(self.curve(speed), key=lambda p:p[1])
        target = number(acceleration, "acceleration")
        if target <= curve[0][1]: return min(self.limit, curve[0][0])
        if target >= curve[-1][1]: return min(self.limit, curve[-1][0])
        for (p0,a0),(p1,a1) in zip(curve, curve[1:]):
            if a0 <= target <= a1:
                return min(self.limit, max(0.0, p0+(target-a0)/(a1-a0)*(p1-p0)))
        raise ContractError("cannot invert upstream pedal map")


class PriusConverter:
    def __init__(self, map_dir, max_steer_angle_rad):
        self.max_steer = number(max_steer_angle_rad, "max_steer_angle_rad")
        if not 0.2 <= self.max_steer <= 1.6:
            raise ContractError("invalid measured Prius steering limit")
        self.accel = PedalMap(Path(map_dir)/"accel_map.csv", .4)
        self.brake = PedalMap(Path(map_dir)/"brake_map.csv", .8)

    @property
    def provenance(self):
        return {"version": "axiom-prius-upstream-csv/1", "accel_map_sha256":self.accel.sha256,
                "brake_map_sha256":self.brake.sha256, "max_steer_angle_rad":self.max_steer,
                "fidelity":"CARLA Prius upstream simulation calibration; not physical calibration"}

    def convert(self, acceleration, speed, steering_tire_angle_rad, target_speed_mps):
        acceleration = number(acceleration, "acceleration")
        speed = number(speed, "speed")
        target = number(target_speed_mps, "target speed")
        steer = number(steering_tire_angle_rad, "steering")
        if target < 0 or speed < -0.1:
            raise ContractError("reverse is not supported by the Prius profile")
        if target <= .01 and speed < .1:
            throttle, brake = 0.0, .8
        elif acceleration >= self.accel.curve(speed)[0][1]:
            throttle, brake = self.accel.invert(acceleration, speed), 0.0
        else:
            throttle, brake = 0.0, self.brake.invert(acceleration, speed)
        return {"throttle":throttle, "brake":brake, "steer":max(-1.0,min(1.0,-steer/self.max_steer))}
