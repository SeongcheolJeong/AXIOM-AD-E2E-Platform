"""Pure mechanics for the bounded four-nozzle stunt model; no engine imports.

Vectors use CARLA world coordinates. Rotation matrices are proper matrices from
CARLA Transform.get_matrix(), so r x F and quaternions share the engine's basis.
The signed nozzle forces are body Z, at [+-1.5, +-.75, 0] metres.
"""
from __future__ import annotations

import math

VERSION = 'kickflip-controller/1'
THRUSTERS = ((1.5, -.75, 0.), (1.5, .75, 0.), (-1.5, -.75, 0.), (-1.5, .75, 0.))
GRAVITY = 9.81


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm(a):
    return math.sqrt(dot(a, a))


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def rotate(matrix, value):
    return [dot(row[:3], value) for row in matrix[:3]]


def unrotate(matrix, value):
    return [sum(matrix[j][i]*value[j] for j in range(3)) for i in range(3)]


def clamp(value, low, high):
    return max(low, min(high, value))


def quaternion(matrix):
    """Proper 3x3 rotation matrix -> normalized xyzw, including 180 degrees."""
    m = matrix
    trace = m[0][0]+m[1][1]+m[2][2]
    if trace > 0:
        s = math.sqrt(trace+1)*2
        q = [(m[2][1]-m[1][2])/s, (m[0][2]-m[2][0])/s, (m[1][0]-m[0][1])/s, .25*s]
    else:
        i = max(range(3), key=lambda j: m[j][j])
        j, k = (i+1) % 3, (i+2) % 3
        s = math.sqrt(max(0., 1+m[i][i]-m[j][j]-m[k][k]))*2
        q = [0., 0., 0., 0.]
        q[i] = .25*s
        q[j] = (m[j][i]+m[i][j])/s
        q[k] = (m[k][i]+m[i][k])/s
        q[3] = (m[k][j]-m[j][k])/s
    length = norm(q)
    return [v/length for v in q]


def multiply_quaternion(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz]


class RollAccumulator:
    """Accumulate local-X twist from successive quaternions, not wrapped Euler angles."""
    def __init__(self):
        self.previous = None
        self.angle = 0.

    def observe(self, q):
        if self.previous is not None:
            if dot(self.previous, q) < 0:
                q = [-v for v in q]
            p = self.previous
            delta = multiply_quaternion([-p[0], -p[1], -p[2], p[3]], q)
            twist = 2*math.atan2(delta[0], delta[3])
            self.angle += math.atan2(math.sin(twist), math.cos(twist))
        self.previous = list(q)
        return self.angle


def allocate(force_z_n, roll_nm, pitch_nm, matrix, *, limit_n, remaining_ns, dt_s):
    """Least-norm nozzle allocation, followed by a common aggregate saturation.

    A common scale preserves the requested wrench direction. Both signed force
    cancellation and pure torque consume absolute nozzle impulse. No yaw nozzle
    is invented; nozzle geometry cannot produce a body-Z moment.
    """
    if not (dt_s > 0 and limit_n >= 0 and remaining_ns >= 0):
        raise ValueError('Invalid allocation bounds')
    values = [force_z_n/4+roll_nm*p[1]/2.25-pitch_nm*p[0]/9. for p in THRUSTERS]
    magnitude = sum(abs(v) for v in values)
    allowed = min(limit_n, remaining_ns/dt_s)
    scale = min(1., allowed/magnitude, (limit_n/4)/max(abs(v) for v in values)) if magnitude else 1.
    values = [v*scale for v in values]
    force = [0., 0., sum(values)]
    torque = [sum(cross(p, [0., 0., f])[axis] for p, f in zip(THRUSTERS, values)) for axis in range(3)]
    consumed = sum(abs(v) for v in values)*dt_s
    return {'thruster_forces_n': values, 'force_n': rotate(matrix, force),
            'torque_nm': rotate(matrix, torque), 'remaining_impulse_ns': max(0., remaining_ns-consumed),
            'consumed_impulse_ns': consumed, 'allocation_scale': scale}


class KickflipController:
    def __init__(self, config):
        self.config = config
        self.phase = 'ascent'
        self.remaining = config['impulse_budget_ns']
        self.accumulator = RollAccumulator()
        self.roll = 0.
        self.rotation_complete = False
        self.airborne = False

    def step(self, state):
        c = self.config
        self.roll = self.accumulator.observe(state['quaternion_xyzw'])
        h, vz = state['height_m'], state['velocity_mps'][2]
        omega = unrotate(state['matrix'], state['angular_velocity_rad_s'])
        mass = c['mass_kg']
        self.airborne = self.airborne or h > .5
        if self.phase == 'ascent' and h+max(0., vz)**2/(2*GRAVITY) >= c['target_height_m']:
            self.phase = 'rotation'
        if self.phase == 'rotation' and self.roll > 2*math.pi-.035 and abs(omega[0]) < .35:
            self.phase = 'descent'
            self.rotation_complete = True
        if state.get('contact_confirmed') and self.airborne:
            self.phase = 'settle'
        # Inertia is a controller gain only; CARLA retains the actual mass tensor.
        inertia = mass*.6
        pitch_angle = math.asin(clamp(-state['matrix'][2][0], -1., 1.))
        pitch = inertia*(-8*pitch_angle-4*omega[1])
        if self.phase == 'ascent':
            force = c['max_thrust_n']*c['thrust_scale']
            torque = inertia*(-8*self.roll-4*omega[0])
        elif self.phase == 'rotation':
            remaining = max(0., 2*math.pi-self.roll)
            desired_rate = min(6., math.sqrt(2*18.*remaining))
            torque = inertia*clamp(12*(desired_rate-omega[0]), -22., 22.)
            # Ballistic roll: do not apply translation along a sideways nozzle.
            force = 0.
        elif self.phase == 'descent':
            torque = inertia*(12*(2*math.pi-self.roll)-5*omega[0])
            desired_vz = -clamp(math.sqrt(max(0., 2*1.5*h)), .35, 2.)
            world_up = state['matrix'][2][2]
            force = mass*max(0., GRAVITY+5*(desired_vz-vz))/max(.5, world_up)
            # Close to the settled body height, remove lift to allow real contact.
            if h < .10 and vz > -.8:
                force = 0.
        else:
            force = torque = pitch = 0.
        command = allocate(force, torque, pitch, state['matrix'],
            limit_n=c['max_thrust_n']*c['thrust_scale'], remaining_ns=self.remaining, dt_s=c['dt_s'])
        self.remaining = command['remaining_impulse_ns']
        return {**command, 'phase': self.phase, 'accumulated_roll_rad': self.roll,
                'rotation_complete': self.rotation_complete, 'brake': self.phase == 'settle'}


def landing_summary(frames, *, contact, rotation_complete):
    """Explicit 1-second settled window and independently observed contact required."""
    stable_since = None
    settled_s = 0.
    for row in frames:
        stable = (contact is not None and row['time_s'] >= contact['time_s']
                  and abs(row['rotation_deg'][0]) <= 10 and abs(row['rotation_deg'][1]) <= 10
                  and norm(row['velocity_mps']) <= .5)
        if not stable:
            stable_since = None
            settled_s = 0.
        elif stable_since is None:
            stable_since = row['time_s']
        if stable_since is not None:
            settled_s = row['time_s']-stable_since
    impact = contact['pre_contact_vertical_speed_mps'] if contact else None
    passed = bool(rotation_complete and contact and impact <= 2 and settled_s >= 1.-1e-6)
    return {'demo_landing_passed': passed, 'rotation_complete': rotation_complete,
            'contact': contact, 'settled_window_s': settled_s, 'impact_vertical_speed_mps': impact,
            'reason': 'demo_criteria_met' if passed else 'contact_unconfirmed' if contact is None
                else 'rotation_incomplete' if not rotation_complete else 'impact_speed_exceeded' if impact > 2
                else 'settled_window_not_met', 'physical_validation': 'not_established'}
