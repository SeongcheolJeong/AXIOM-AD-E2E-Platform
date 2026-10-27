from pathlib import Path
import tempfile
import unittest

from axiom_autoware.contract import ContractError, EpisodeClock, PriusConverter, carla_to_ros_pose, ros_to_carla_pose, acceleration_semantics, LEGACY_PID_SHA256
from axiom_autoware.launcher import docker_command


class ContractTests(unittest.TestCase):
    def test_acceleration_compatibility_is_exact_binary_only(self):
        self.assertEqual(acceleration_semantics(True,"other"),"explicit_message_flag")
        self.assertEqual(acceleration_semantics(False,LEGACY_PID_SHA256),"pinned_pid_0.50_legacy_acceleration_field")
        with self.assertRaises(ContractError):acceleration_semantics(False,"other")

    def test_center_rear_axle_roundtrip_and_handedness(self):
        import math
        for yaw in (0., math.pi/2, -math.pi/2, math.pi):
            pose={"x":12.,"y":5.,"z":.4,"yaw_rad":yaw}
            transformed=carla_to_ros_pose(pose,1.2)
            result=ros_to_carla_pose(transformed,1.2)
            for key in pose:self.assertAlmostEqual(result[key],pose[key])
        self.assertEqual(carla_to_ros_pose({"x":10.,"y":5.,"yaw_rad":0.},1.2),{"x":8.8,"y":-5.,"z":0.,"yaw_rad":-0.})

    def test_clock_rejects_duplicate_tick_future_stale_and_multiple_tick_owner(self):
        clock=EpisodeClock(.05)
        clock.observe(700,42.)
        self.assertAlmostEqual(clock.command_age(41.8),.2)
        for stamp in (41.7,42.01,float("nan")):
            with self.assertRaises(ContractError):clock.command_age(stamp)
        for frame,time in ((700,42.05),(701,42.),(702,42.1)):
            with self.assertRaises(ContractError):clock.observe(frame,time)
        clock.observe(701,42.05)

    def test_control_clamped_sign_stop_and_reverse_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            # Contract fixtures exercise inversion, not an actual CARLA calibration.
            Path(folder,"accel_map.csv").write_text("default,0,10\n0,0,-.5\n.2,1,.5\n.4,3,2\n")
            Path(folder,"brake_map.csv").write_text("default,0,10\n0,0,-.5\n.4,-2,-3\n.8,-4,-5\n")
            converter=PriusConverter(folder,1.)
            forward=converter.convert(1.,0.,.2,3.)
            self.assertEqual(set(forward),{"throttle","brake","steer"})
            self.assertEqual(forward["throttle"],.2)
            self.assertEqual(forward["steer"],-.2)
            self.assertEqual(converter.convert(0.,0.,0.,0.)["brake"],.8)
            self.assertEqual(converter.convert(100.,1.,5.,3.)["throttle"],.4)
            with self.assertRaises(ContractError):converter.convert(0.,0.,0.,-1.)

    def test_cpu_isolation_and_map_required(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,"lanelet2_map.osm").write_text("<osm/>")
            Path(folder,"map_projector_info.yaml").write_text("projector_type: Local")
            cmd=docker_command(folder,Path(folder,"out"),"job_123")
            self.assertNotIn("--gpus",cmd)
            self.assertIn("none",cmd)
            self.assertIn("ROS_DOMAIN_ID=78",cmd)
            self.assertTrue(any("@sha256:" in item for item in cmd))
            with self.assertRaises(ValueError):docker_command(folder,folder,"episode",42)
            with self.assertRaises(ValueError):docker_command(folder,folder,"../../escape")


if __name__ == "__main__": unittest.main()
