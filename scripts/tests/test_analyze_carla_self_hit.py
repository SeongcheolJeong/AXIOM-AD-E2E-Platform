"""Authored offline counterexamples; no simulator, real labels, or network calls."""
import importlib.util
from pathlib import Path


def test_offline_identity_and_evidence_counterexamples():
    path = Path(__file__).resolve().parents[1] / 'analyze_carla_self_hit.py'
    spec = importlib.util.spec_from_file_location('self_hit_analyzer_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.self_test()
    assert set(result['checked']) == {
        'competing_ids', 'channel_mismatch', 'signed_zero', 'approximate_only',
        'truncated_record', 'complete', 'frame', 'parent', 'pose', 'empty',
        'unsealed_callback_prefix', 'event_hash_tamper', 'raw_truncation_or_size_tamper',
    }
