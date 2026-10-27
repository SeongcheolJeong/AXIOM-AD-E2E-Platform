"""CPU contract checks; these do not establish native GPU reconstruction quality."""
import hashlib
import io
import unittest
import zipfile
import numpy as np
from axiom_neural.data import SWEEP_FRAME_COUNT, camera_sweep_lateral_m, camera_pose, changed_pose, matrix, read_member


class CameraDataTests(unittest.TestCase):
    def test_native_forward_camera_maps_right_up_back(self):
        pose = camera_pose(np.eye(4), [0, 0, 0])
        np.testing.assert_allclose(pose[:3, 0], [0, -1, 0])
        np.testing.assert_allclose(pose[:3, 1], [0, 0, 1])
        np.testing.assert_allclose(pose[:3, 2], [-1, 0, 0])
        self.assertAlmostEqual(np.linalg.det(pose[:3, :3]), 1)

    def test_native_pose_center_scale_and_bounded_view_have_metric_meaning(self):
        native = np.eye(4); native[:3, 3] = [10, 20, 2]
        pose = camera_pose(native, [9, -20, 1])
        np.testing.assert_allclose(pose[:3, 3], [.05, 0, .05])
        edit = changed_pose(pose, .3, .2)
        np.testing.assert_allclose(edit[:3, 3] - pose[:3, 3], [0, -.015, .01])
        np.testing.assert_array_equal(edit[:3, :3], pose[:3, :3])

    def test_invalid_affine_and_improper_rotations_fail(self):
        for malformed in (np.zeros((4, 4)), np.diag([1, 1, -1, 1]), np.eye(3), np.full((4, 4), np.nan)):
            with self.assertRaises(ValueError): matrix(malformed)

    def test_camera_sweep_moves_metric_camera_at_a_frozen_pose(self):
        offsets = [camera_sweep_lateral_m(i, 1.5) for i in range(SWEEP_FRAME_COUNT)]
        self.assertEqual((offsets[0], offsets[30], offsets[-1]), (-1.5, 0, 1.5))
        self.assertTrue(all(a < b for a, b in zip(offsets, offsets[1:])))
        np.testing.assert_allclose(offsets, -np.asarray(offsets[::-1]), atol=1e-15)
        base = camera_pose(np.eye(4), [0, 0, 0])
        first, last = (changed_pose(base, offset, .2) for offset in (offsets[0], offsets[-1]))
        self.assertAlmostEqual(np.linalg.norm(last[:3, 3] - first[:3, 3]), 3 * .05)
        np.testing.assert_array_equal(first[:3, :3], last[:3, :3])
        for index, amplitude in [(-1, 1.5), (61, 1.5), (1.2, 1.5), (0, 0), (0, -1), (0, 2.1), (0, float('nan'))]:
            with self.assertRaises(ValueError): camera_sweep_lateral_m(index, amplitude)

    def test_source_member_hash_size_and_traversal_fail_closed(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive: archive.writestr('frames/0.raw', b'actual')
        with zipfile.ZipFile(stream) as archive:
            item = dict(name='frames/0.raw', size_bytes=6, sha256=hashlib.sha256(b'actual').hexdigest())
            self.assertEqual(read_member(archive, item, 6), b'actual')
            for edit in (dict(sha256='0'*64), dict(size_bytes=5), dict(name='../0.raw')):
                with self.assertRaises(ValueError): read_member(archive, item | edit, 6)
            with self.assertRaises(ValueError): read_member(archive, item, 5)


if __name__ == '__main__': unittest.main()
