"""Operator conversion entry point, separate from project authorization/dispatch."""
import argparse
import hashlib
from pathlib import Path

from .converter import (canonical, collect_source_bundle, convert_bundle,
                        file_hash, mcap_bytes, parse_json, MAX_BUNDLE_BYTES, MAX_EXPANDED_LINEAGE_BYTES)


def write_outputs(bundle, destination):
    frames, provenance = convert_bundle(bundle)
    raw, receipt = mcap_bytes(frames, provenance)
    destination = Path(destination)
    # A new directory is required. No original or existing receipt is overwritten.
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'planning-log.mcap').write_bytes(raw)
    (destination/'original-source-bundle.json').write_bytes(canonical(bundle))
    (destination/'conversion-lineage.json').write_bytes(canonical(provenance))
    proposal = {'schema_version': 'nuscenes-planning-import-proposal/1',
        'recording': {'name': 'nuScenes original annotation planning log · isolated research',
            'source_kind': 'measured', 'source': 'nuScenes original recorded GT annotations; explicitly authored ego footprint; user-approved isolated noncommercial research, not commercial or sensor-perception evidence'},
        'mcap': receipt,
        'source_provenance': {'use_scope': 'isolated_noncommercial_research', 'commercial_use': False,
            'source_bundle_sha256': hashlib.sha256(canonical(bundle)).hexdigest(),
            'lineage_sha256': hashlib.sha256(canonical(provenance)).hexdigest()},
        'sequence_input': {'name': 'nuScenes original GT objects and ego · research', 'source_type': 'recording',
            'world_frame': receipt['world_frame'], 'clock_domain': 'nuscenes-original-capture-unix-ns',
            'ego_channel_id': receipt['ego_channel_id'], 'object_channel_id': receipt['object_channel_id'],
            'ego_ordinals': receipt['ego_ordinals'], 'ego_log_time_offset_ns': '0', 'object_log_time_offset_ns': '0',
            'object_max_age_ns': '0', 'max_step_ns': '1000000000',
            'note': 'Original GT annotations, not sensor perception. Explicit reference ego footprint; unknown velocities remain null. Nonreactive surrounding objects; isolated noncommercial research.'},
        'limitations': provenance['limitations']}
    (destination/'import-proposal.json').write_bytes(canonical(proposal))
    return proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    export = sub.add_parser('export', help='Read existing original mini tables/archive and write a new immutable research conversion')
    export.add_argument('--data-root', required=True)
    export.add_argument('--archive', required=True)
    export.add_argument('--archive-sha256', required=True)
    export.add_argument('--driving-input', required=True)
    export.add_argument('--ego-length-m', type=float, required=True)
    export.add_argument('--ego-width-m', type=float, required=True)
    export.add_argument('--declare-zero-offset-reference-footprint', action='store_true', required=True)
    export.add_argument('--output', required=True)
    export.add_argument('--compact-lineage', action='store_true',
                        help='Retain original rows in a bounded lossless lineage envelope for longer selected clips')
    convert = sub.add_parser('convert-source', help='Write MCAP from a retained source bundle collected over authenticated SSH')
    convert.add_argument('--source-bundle', required=True)
    convert.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.operation == 'export':
        geometry = {'length_m': args.ego_length_m, 'width_m': args.ego_width_m, 'origin_offset_xy_m': [0., 0.],
                    'provenance': 'authored_reference_footprint', 'original_measured_geometry_available': False}
        bundle = collect_source_bundle(args.data_root, args.archive, Path(args.driving_input).read_bytes(),
            expected_archive_sha256=args.archive_sha256,
            converter_source_sha256=file_hash(Path(__file__).with_name('converter.py'))[0], ego_geometry=geometry,
            compact=args.compact_lineage)
    else:
        file_hash(args.source_bundle, maximum=MAX_EXPANDED_LINEAGE_BYTES)
        bundle = parse_json(Path(args.source_bundle).read_bytes())
        if not bundle.get('lineage_encoding') and len(canonical(bundle)) > MAX_BUNDLE_BYTES:
            raise ValueError('Uncompressed source bundle exceeds its original bound')
        if bundle['converter_source_sha256'] != file_hash(Path(__file__).with_name('converter.py'))[0]:
            raise ValueError('Source collection converter code differs from the local converter')
    print(canonical(write_outputs(bundle, args.output)).decode())


if __name__ == '__main__':
    main()
