"""Library selection contracts using authored frame metadata only."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_log_sim_library import deterministic_parts, valid_runs


def frame(index, *, excluded=False, at=None):
    return {'sample_start_index': index, 'sample_token': str(index),
            'timestamp_us': index * 500000 if at is None else at,
            'reasons': ['object_count_exceeds_128'] if excluded else []}


def test_excluded_original_frame_splits_without_bridging_or_losing_following_frames():
    rows = [frame(index, excluded=index in (0, 4, 5)) for index in range(10)]
    runs = valid_runs(rows)
    assert [[row['sample_start_index'] for row in run] for run in runs] == [[1, 2, 3], [6, 7, 8, 9]]
    assert sum(len(run) for run in runs) + sum(bool(row['reasons']) for row in rows) == len(rows)


def test_original_time_gap_and_index_gap_are_not_interpolated():
    assert [len(run) for run in valid_runs([frame(0), frame(1), frame(2, at=2_000_000)])] == [2, 1]
    assert [len(run) for run in valid_runs([frame(0), frame(2)])] == [1, 1]


def test_byte_bound_split_is_deterministic_complete_and_without_duplicate_boundaries():
    rows = [frame(index) for index in range(39)]
    left, right = deterministic_parts(rows)
    assert [len(left), len(right)] == [19, 20]
    assert left + right == rows
    assert len({row['sample_token'] for row in left + right}) == 39
