import math
import unittest
from axiom_autoware.contract import rear_axle_kinematics, bbox_corners_ros


class RigidBodyOriginTests(unittest.TestCase):
    def test_rear_axle_circular_motion_in_rotated_world(self):
        # Rear axle at 5 m/s with no lateral slip; center is 1.4 m ahead.
        # ROS yaw rate +0.3, acceleration +0.1: center body v=(5,0.42),
        # a=(-0.126,1.64), while rear body v=(5,0), a=(0,1.5).
        yaw = .7
        def native_world(x, y):
            return {'x': x*math.cos(yaw)+y*math.sin(yaw),
                    'y': x*math.sin(yaw)-y*math.cos(yaw), 'z': 0}
        ego = {'pose': {'yaw_rad': yaw}, 'velocity': native_world(5,.42),
               'acceleration': native_world(-.126,1.64), 'yaw_rate_rps': -.3}
        actual = rear_axle_kinematics(ego, 1.4, -.1)
        for key, expected in {'longitudinal_velocity':5, 'lateral_velocity':0,
                              'longitudinal_acceleration':0, 'lateral_acceleration':1.5,
                              'yaw_rate':.3}.items():
            self.assertAlmostEqual(actual[key], expected, places=10)

    def test_bbox_cloud_is_centered_with_native_dimensions(self):
        actor = {'pose': {'x':10,'y':20,'z':1,'yaw_rad':math.pi/2},
                 'dimensions': {'length':4,'width':2,'height':2}}
        points = bbox_corners_ros(actor)
        self.assertEqual(len(points),8)
        for axis, expected in enumerate([10,-20,1]):
            self.assertAlmostEqual(sum(point[axis] for point in points)/8,expected)
        self.assertEqual({point[2] for point in points},{0,2})
        self.assertAlmostEqual(max(p[0] for p in points)-min(p[0] for p in points),2)
        self.assertAlmostEqual(max(p[1] for p in points)-min(p[1] for p in points),4)

if __name__ == '__main__': unittest.main()
