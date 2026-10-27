"""Accepted profile/failure paths; these are contract checks, not native sensor tests."""
import copy,unittest
from ade2e.sensor_capture_schemas import CaptureSensor,SensorCaptureSpec,capture_template

class SensorCaptureContractTests(unittest.TestCase):
    def test_accepted_profile_and_explicit_rotation_units(self):
        template=capture_template();value=SensorCaptureSpec.model_validate(template)
        self.assertEqual(len(value.sensors),4)
        self.assertEqual({s.rotation_order for s in value.sensors},{'pitch_yaw_roll_right_hand_rule'})
        self.assertEqual(value.coordinate_convention,'right_handed_x_forward_y_left_z_up')
        self.assertEqual(CaptureSensor(key='front-left',kind='rgb').native_parameters()['image_size_x'],'640')

    def test_unsupported_outputs_and_incomplete_or_unbounded_inputs_fail(self):
        for parameters in [{'image_size_x':'1920'},{'sensor_tick':'.2'},{'fov':'nan'},{'fov':'180'},{'noise_seed':'42'}]:
            with self.subTest(parameters=parameters),self.assertRaises(ValueError):CaptureSensor(key='front',kind='rgb',parameters=parameters)
        for parameters in [{'points_per_second':'99999999'},{'channels':'64'},{'upper_fov':'90'},{'dropoff_intensity_limit':'0'},{'rotation_frequency':'20'}]:
            with self.subTest(parameters=parameters),self.assertRaises(ValueError):CaptureSensor(key='lidar',kind='lidar',parameters=parameters)
        for parameters in [{'points_per_second':'1501'},{'horizontal_fov':'91'},{'range':'101'}]:
            with self.subTest(parameters=parameters),self.assertRaises(ValueError):CaptureSensor(key='radar',kind='radar',parameters=parameters)
        for kind in ('raw','depth','semantic'):
            with self.assertRaises(ValueError):CaptureSensor(key='sensor',kind=kind)
        value=capture_template();value['sensors'][1]['key']='front'
        with self.assertRaises(ValueError):SensorCaptureSpec.model_validate(value)
        value=capture_template();value['duration_seconds']=31
        with self.assertRaises(ValueError):SensorCaptureSpec.model_validate(value)
        value=capture_template();value['sensors'][0]['translation_m']=[float('nan'),0,0]
        with self.assertRaises(ValueError):SensorCaptureSpec.model_validate(value)

if __name__=='__main__':unittest.main()
