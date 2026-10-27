import unittest
from types import SimpleNamespace as N
from unittest.mock import patch
from axiom_autoware.contract import EpisodeClock
from axiom_autoware.native import Runtime


def point(speed=3., quaternion=None):
    return N(pose=N(position=N(x=0.,y=0.,z=0.),orientation=quaternion or N(x=0.,y=0.,z=0.,w=1.)),
             longitudinal_velocity_mps=speed,acceleration_mps2=0.)


def runtime(points, stamp=1.):
    r=Runtime.__new__(Runtime);r.launch=N(poll=lambda:None);r.clock=EpisodeClock(.05);r.clock.observe(0,.95)
    r.publish_observation=lambda _:1.;r.rclpy=N(spin_once=lambda *a,**kw:None);r.node=None;r.maybe_route=lambda:None
    r.route_result={'success':True};r.route_state=2;r.signal_mapping={};r.engaged=True;r.offset=1.4
    r.trajectory=N(header=N(stamp=N(sec=int(stamp),nanosec=int((stamp-int(stamp))*1e9))),points=points)
    r.command=N(stamp=N(sec=1,nanosec=0),longitudinal=N(is_defined_acceleration=True,acceleration=1.,velocity=3.),lateral=N(steering_tire_angle=0.))
    r.converter=N(convert=lambda *a:{'throttle':.2,'brake':0.,'steer':0.});r.pid_binary_sha256='unused';r.started=0
    return r


class NativeCommandGateTests(unittest.TestCase):
    def step(self,r):
        with patch('axiom_autoware.native.time.monotonic',side_effect=[0.,1.,1.]):
            return r.step({'frame':1,'sim_time_s':1.})

    def test_rejects_cached_control_when_current_path_has_under_three_points(self):
        for count in (0,1,2):
            r=runtime([point() for _ in range(count)]);out=self.step(r)
            self.assertEqual(out['status'],'command_rejected');self.assertIsNone(out['control']);self.assertFalse(r.engaged)

    def test_rejects_stale_or_invalid_current_path(self):
        for r in (runtime([point()]*3,.1),runtime([point(float('nan'))]*3),runtime([point(quaternion=N(x=0.,y=0.,z=0.,w=0.))]*3)):
            out=self.step(r);self.assertEqual(out['status'],'command_rejected');self.assertIsNone(out['control']);self.assertFalse(r.engaged)

    def test_accepts_fresh_three_point_path_and_fresh_command(self):
        out=self.step(runtime([point()]*3));self.assertEqual(out['status'],'driving');self.assertEqual(out['control']['throttle'],.2)

if __name__=='__main__':unittest.main()
