import importlib.util
import math
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('derive_signal_map', Path(__file__).parents[1] / 'scripts/derive_signal_map.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def stop(y, x=0, yaw=0):
    return {'pose': {'x': x, 'y': y, 'z': 0, 'yaw_rad': yaw}, 'width_m': 3.5}


class SharedSignalStopTests(unittest.TestCase):
    def test_preserves_adjacent_lane_native_endpoints(self):
        points, receipt = module.shared_stop_line([stop(0), stop(3.5, x=.09)])
        self.assertEqual(len(points), 4)
        self.assertEqual(receipt['lane_count'], 2)
        self.assertAlmostEqual(receipt['longitudinal_spread_m'], .09)
        self.assertEqual(receipt['boundary_lateral_gaps_m'], [0])
        self.assertEqual({round(point[0], 2) for point in points}, {0, .09})

    def test_rejects_median_opposite_lane_and_offset_stop(self):
        for other in (stop(7), stop(3.5, yaw=math.pi), stop(3.5, x=.3)):
            with self.assertRaises(ValueError):
                module.shared_stop_line([stop(0), other])


if __name__ == '__main__':
    unittest.main()
