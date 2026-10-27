"""Pure Ramp geometry and contact-actuator contracts, not a native integration.

Nothing here establishes a collision hit, moves an engine actor, or enables the
Ramp preset. Geometry is authored prediction. Contact inputs must come from a
separately commissioned native query adapter with pinned collision identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math

try:
    from .stunt_math import GRAVITY, clamp, cross, dot, norm
except ImportError:
    from stunt_math import GRAVITY, clamp, cross, dot, norm

VERSION = 'stunt-ramp-contact/1'
PADS = ('front-left', 'front-right', 'rear-left', 'rear-right')


def finite_vector(value):
    if len(value) != 3 or any(type(v) not in (float, int) or not math.isfinite(v) for v in value):
        raise ValueError('Expected a finite three-vector')
    return list(value)


@dataclass(frozen=True)
class RampGeometry:
    """Matches source-v1's finite, piecewise-planar driving face in local metres."""
    radius_m: float = 6.
    width_m: float = 8.
    sweep_deg: float = 110.
    segments: int = 55

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.radius_m, self.width_m, self.sweep_deg))
                or self.radius_m <= 0 or self.width_m <= 0 or not 0 < self.sweep_deg < 180
                or type(self.segments) is not int or self.segments < 1):
            raise ValueError('Invalid finite ramp geometry')

    def point(self, angle_rad):
        if not 0 <= angle_rad <= math.radians(self.sweep_deg):
            raise ValueError('Ramp angle is outside the authored surface')
        return [self.radius_m*math.sin(angle_rad), 0., self.radius_m*(1-math.cos(angle_rad))]

    def facet(self, index):
        if type(index) is not int or not 0 <= index < self.segments:
            raise ValueError('Ramp facet is outside the authored surface')
        step = math.radians(self.sweep_deg)/self.segments
        a, b = self.point(index*step), self.point((index+1)*step)
        middle = (index+.5)*step
        return {'index': index, 'start_m': a, 'end_m': b,
                'tangent': [math.cos(middle), 0., math.sin(middle)],
                'normal_into_free_space': [-math.sin(middle), 0., math.cos(middle)]}

    def project(self, position_m):
        """Nearest authored top-face point; this is never native contact proof."""
        position = finite_vector(position_m)
        candidates = []
        for index in range(self.segments):
            face = self.facet(index)
            a, b = face['start_m'], face['end_m']
            ab = [v-u for u, v in zip(a, b)]
            fraction = clamp(dot([p-v for p, v in zip(position, a)], ab)/dot(ab, ab), 0., 1.)
            nearest = [v+fraction*d for v, d in zip(a, ab)]
            nearest[1] = clamp(position[1], -self.width_m/2, self.width_m/2)
            offset = [p-v for p, v in zip(position, nearest)]
            candidates.append({**face, 'point_m': nearest, 'distance_m': norm(offset),
                'signed_normal_gap_m': dot(offset, face['normal_into_free_space']),
                'angle_rad': math.radians(self.sweep_deg)*(index+fraction)/self.segments,
                'within_lateral_width': abs(position[1]) <= self.width_m/2,
                'evidence_kind': 'authored_geometry_prediction'})
        return min(candidates, key=lambda row: (row['distance_m'], row['index']))


def ideal_exit_estimate(geometry, entry_speed_mps, release_angle_deg, landing_z_m=0.):
    """Energy/ballistic estimate excluding drive, drag, thrust and collisions.

    Turning suction off does NOT imply detachment from a concave surface. An
    inward launch force or reaching the end of the collision surface is needed.
    This estimate assumes that detachment has already occurred.
    """
    if not all(math.isfinite(v) for v in (entry_speed_mps, release_angle_deg, landing_z_m)) or entry_speed_mps < 0:
        raise ValueError('Invalid theoretical release input')
    angle = math.radians(release_angle_deg)
    point = geometry.point(angle)
    speed_squared = entry_speed_mps**2-2*GRAVITY*point[2]
    result = {'evidence_kind': 'ideal_point_mass_estimate', 'native_validation': 'not_established',
              'release_position_m': point, 'minimum_energy_entry_mps': math.sqrt(2*GRAVITY*point[2]),
              'sufficient_entry_energy': speed_squared >= 0, 'requires_collision_clearance_check': True}
    if speed_squared < 0:
        return result
    speed = math.sqrt(speed_squared)
    vx, vz = speed*math.cos(angle), speed*math.sin(angle)
    discriminant = vz*vz+2*GRAVITY*(point[2]-landing_z_m)
    if discriminant < 0:
        return result | {'landing_plane_reachable': False}
    flight = (vz+math.sqrt(discriminant))/GRAVITY
    return result | {'landing_plane_reachable': True, 'release_speed_mps': speed,
        'release_velocity_mps': [vx, 0., vz], 'unobstructed_flight_time_s': flight,
        'unobstructed_landing_x_m': point[0]+vx*flight}


def fit_query_plane(points_m, toward_m, max_residual_m=.01):
    """Fit a plane from query points; do not call the result an engine hit normal."""
    points = [finite_vector(p) for p in points_m]
    toward = finite_vector(toward_m)
    if not 3 <= len(points) <= 16 or not math.isfinite(max_residual_m) or max_residual_m < 0:
        raise ValueError('A bounded native plane query needs 3 to 16 points')
    candidates = [cross([b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)])
                  for a, b, c in combinations(points, 3)]
    normal = max(candidates, key=norm)
    length = norm(normal)
    if length < 1e-8:
        raise ValueError('Native plane query points are collinear')
    normal = [v/length for v in normal]
    centre = [sum(p[i] for p in points)/len(points) for i in range(3)]
    if dot(normal, [a-b for a, b in zip(toward, centre)]) < 0:
        normal = [-v for v in normal]
    residual = max(abs(dot(normal, [a-b for a, b in zip(p, centre)])) for p in points)
    if residual > max_residual_m:
        raise ValueError('Native plane query points cross a nonplanar seam')
    return {'normal_into_free_space': normal, 'point_m': centre, 'max_residual_m': residual,
            'method': 'native_query_point_plane_fit/1'}


def _region_query_verified(hit, region, frame):
    """Alternative to unavailable component IDs in a commissioned isolated map."""
    if (not region or not isinstance(region.get('id'), str) or not region['id']
            or hit.get('surface_region_id') != region['id']):
        return False
    try:
        low, high = (finite_vector(region[k]) for k in ('bounds_min_m', 'bounds_max_m'))
        points = [finite_vector(p) for p in hit['native_query_points_m']]
        if (any(a >= b for a, b in zip(low, high))
                or hit['native_query_frames'] != [frame]*len(points)
                or any(not all(a <= p[i] <= b for i, (a, b) in enumerate(zip(low, high))) for p in points)):
            return False
        plane = fit_query_plane(points, hit['pad_position_m'], region['max_plane_residual_m'])
        hit_point = finite_vector(hit['hit_position_m'])
        return (hit.get('normal_method') == plane['method'] and
                min(norm([a-b for a, b in zip(hit_point, p)]) for p in points) <= 1e-4 and
                norm([a-b for a, b in zip(plane['normal_into_free_space'], finite_vector(hit['normal_into_free_space']))]) <= 1e-6
                and abs(dot(plane['normal_into_free_space'], [a-b for a, b in zip(hit_point, plane['point_m'])])) <= region['max_plane_residual_m'])
    except (KeyError, TypeError, ValueError):
        return False


def allocate_suction(hits, *, expected_collision_sha256, current_frame, centre_of_mass_m,
                     max_force_n, remaining_impulse_ns, dt_s, enabled,
                     max_gap_m=.25, max_tangential_error_m=.05, verified_region=None):
    """Bounded four-pad attraction at current native hit normals, with r cross F.

    Each pad gets at most one quarter of the total cap. Missing pads do not lend
    their capacity to other pads. This is a distinct assumed contact actuator;
    its finite impulse account must be separate from the existing jet budget.
    Normals point from the solid into free space; attraction is the opposite.
    """
    if (len(expected_collision_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_collision_sha256)
            or type(current_frame) is not int or type(enabled) is not bool
            or not all(math.isfinite(v) for v in (max_force_n, remaining_impulse_ns, dt_s, max_gap_m, max_tangential_error_m))
            or min(max_force_n, remaining_impulse_ns, max_gap_m, max_tangential_error_m) < 0 or dt_s <= 0):
        raise ValueError('Invalid suction identity or bounds')
    centre = finite_vector(centre_of_mass_m)
    qualified, rejected, seen = [], [], set()
    for hit in hits:
        pad = hit.get('pad_id')
        if pad in seen:
            raise ValueError('Duplicate suction pad observation')
        seen.add(pad)
        reason = None
        if pad not in PADS:
            reason = 'unknown_pad'
        elif hit.get('evidence_kind') != 'native_collision_query':
            reason = 'native_query_required'
        elif hit.get('collision_sha256') != expected_collision_sha256:
            reason = 'collision_identity_changed'
        elif type(hit.get('world_frame')) is not int or hit['world_frame'] != current_frame:
            reason = 'stale_or_future_frame'
        elif not hit.get('surface_component_id') and not _region_query_verified(hit, verified_region, current_frame):
            reason = 'surface_identity_unavailable'
        else:
            pad_point, hit_point, normal = (finite_vector(hit[k]) for k in
                ('pad_position_m', 'hit_position_m', 'normal_into_free_space'))
            offset = [a-b for a, b in zip(pad_point, hit_point)]
            if abs(norm(normal)-1.) > 1e-6:
                reason = 'nonunit_native_normal'
            else:
                length = norm(normal)
                normal = [v/length for v in normal]
                gap = dot(offset, normal)
                tangent_error = norm([v-gap*n for v, n in zip(offset, normal)])
                if not 0 <= gap <= max_gap_m or tangent_error > max_tangential_error_m:
                    reason = 'pad_outside_contact_range'
        if reason:
            rejected.append({'pad_id': pad, 'reason': reason})
        else:
            qualified.append({'pad_id': pad, 'pad_position_m': pad_point,
                'normal_into_free_space': normal, 'gap_m': gap,
                'surface_component_id': hit.get('surface_component_id'),
                'surface_region_id': hit.get('surface_region_id'),
                'normal_method': hit.get('normal_method', 'native_reported_normal'), 'world_frame': current_frame})
    per_pad = max_force_n/4 if enabled else 0.
    requested_total = per_pad*len(qualified)
    scale = min(1., remaining_impulse_ns/(dt_s*requested_total)) if requested_total else 1.
    force, torque, applied = [0., 0., 0.], [0., 0., 0.], []
    for hit in qualified:
        value = [-v*per_pad*scale for v in hit['normal_into_free_space']]
        offset = [p-c for p, c in zip(hit['pad_position_m'], centre)]
        moment = cross(offset, value)
        force = [a+b for a, b in zip(force, value)]
        torque = [a+b for a, b in zip(torque, moment)]
        applied.append({**hit, 'force_n': value, 'torque_nm': moment})
    consumed = requested_total*scale*dt_s
    return {'force_n': force, 'torque_nm': torque, 'pads': applied, 'rejected': rejected,
        'consumed_impulse_ns': consumed, 'remaining_impulse_ns': max(0., remaining_impulse_ns-consumed),
        'qualified_pad_ids': [row['pad_id'] for row in qualified],
        'physical_validation': 'not_established'}


class RampPhaseGate:
    """Phase eligibility only; native throttle and flight controllers remain separate.

    It does not interpret missing queries as airborne or a normal near zero as
    landing. The adapter must establish each support state from native evidence.
    """
    def __init__(self, release_angle_deg=85., release_mode='inward_pulse'):
        if not 45 <= release_angle_deg <= 100:
            raise ValueError('Release angle must precede the authored 110 degree tip')
        self.release_angle_deg = release_angle_deg
        if release_mode not in ('inward_pulse', 'surface_end'):
            raise ValueError('Unsupported ramp release mode')
        self.release_mode = release_mode
        self.phase = 'approach'
        self.failure_reason = None

    def observe(self, *, native_query_available, qualified_pad_ids, ramp_angle_deg,
                native_clearance_confirmed=False, native_landing_contact_confirmed=False,
                terminal_settled_confirmed=False, native_front_edge_exit_confirmed=False):
        if self.phase in ('aborted', 'complete'):
            return self.command()
        if native_query_available is not True:
            self.phase, self.failure_reason = 'aborted', 'native_contact_query_unavailable'
            return self.command()
        if not math.isfinite(ramp_angle_deg):
            raise ValueError('Ramp angle must be finite')
        support = (any(p in qualified_pad_ids for p in PADS[:2])
                   and any(p in qualified_pad_ids for p in PADS[2:]))
        if self.phase == 'approach' and ramp_angle_deg > 2 and support:
            self.phase = 'wallride'
        if self.phase == 'wallride':
            if (self.release_mode == 'surface_end' and native_front_edge_exit_confirmed is True
                    and any(p in qualified_pad_ids for p in PADS[2:])):
                self.phase = 'release'
            elif not support:
                self.phase, self.failure_reason = 'aborted', 'wallride_support_lost'
            elif ramp_angle_deg >= self.release_angle_deg:
                self.phase = 'release'
        elif self.phase == 'release' and native_clearance_confirmed is True:
            self.phase = 'airborne'
        elif self.phase == 'airborne' and native_landing_contact_confirmed is True:
            self.phase = 'settle'
        elif self.phase == 'settle' and terminal_settled_confirmed is True:
            self.phase = 'complete'
        return self.command()

    def command(self):
        return {'phase': self.phase, 'suction_enabled': self.phase == 'wallride',
                'wheel_drive_enabled': self.phase in ('approach', 'wallride'),
                'inward_release_requested': self.phase == 'release' and self.release_mode == 'inward_pulse',
                'flight_control_enabled': self.phase == 'airborne' or (self.phase == 'release' and self.release_mode == 'inward_pulse'),
                'failure_reason': self.failure_reason, 'native_integration': 'not_established'}
