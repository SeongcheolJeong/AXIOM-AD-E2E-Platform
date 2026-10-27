#!/usr/bin/env python3
"""Preserve a public OSM extract and build a bounded, explicit road selection.

The optional convert subcommand requires CARLA's matching Python API. It does
not connect to or mutate a simulator. These are community-map-derived roads,
not NGII measurements, surveyed lane geometry, or a complete Korean city.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from xml.etree import ElementTree as ET


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, raw: bytes):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f'Preserve existing revision; choose a new output directory: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def select_roads(raw: bytes, way_ids: list[str]):
    if len(raw) > 32 * 1024**2 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unsupported OSM XML size or external entity declaration')
    root = ET.fromstring(raw)
    if root.tag != 'osm' or not way_ids or len(way_ids) != len(set(way_ids)):
        raise ValueError('Select distinct explicit OSM way IDs from an OSM document')
    nodes = {n.get('id'): n for n in root.findall('node')}
    ways = {w.get('id'): w for w in root.findall('way')}
    if set(way_ids) - ways.keys():
        raise ValueError('Selected road is absent from the preserved source')
    ids, selected, stats = set(), [], []
    for identity in way_ids:
        way = ways[identity]
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        if tags.get('highway') not in {'primary', 'secondary', 'tertiary', 'residential', 'unclassified',
                                       'primary_link', 'secondary_link', 'tertiary_link'}:
            raise ValueError('Only bounded ordinary driving road ways are supported')
        if tags.get('layer', '0') != '0' or tags.get('bridge', 'no') != 'no' or tags.get('tunnel', 'no') != 'no':
            raise ValueError(f'Grade-separated source road {identity} requires an elevation-aware follow-up')
        refs = [n.get('ref') for n in way.findall('nd')]
        if len(refs) < 2 or set(refs) - nodes.keys():
            raise ValueError('Incomplete original road geometry')
        coords = [(float(nodes[n].get('lon')), float(nodes[n].get('lat'))) for n in refs]
        if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in coords):
            raise ValueError('Invalid geographic coordinates')
        length = sum(math.hypot(math.radians(b[0]-a[0])*6371008.8*math.cos(math.radians((a[1]+b[1])/2)),
                               math.radians(b[1]-a[1])*6371008.8) for a, b in zip(coords, coords[1:]))
        stats.append({'id': identity, 'name': tags.get('name'), 'tags': tags,
                      'approximate_centerline_length_m': length, 'original_node_ids': refs})
        ids.update(refs)
        selected.append(way)
    # Do not silently connect disconnected roads or generate an arbitrary ROI.
    components = [set(s['original_node_ids']) for s in stats]
    while True:
        pair = next(((i, j) for i, a in enumerate(components)
                     for j in range(i+1, len(components)) if a & components[j]), None)
        if pair is None:
            break
        i, j = pair
        components[i].update(components.pop(j))
    if len(components) != 1:
        raise ValueError('Selected OSM ways must form one connected component')
    length = sum(s['approximate_centerline_length_m'] for s in stats)
    if not 100 <= length <= 3000:
        raise ValueError('Pilot selection must contain 100..3000 m of source centerlines')
    output = ET.Element('osm', version='0.6', generator='AXIOM explicit source selection/1')
    for identity in sorted(ids, key=int):
        output.append(nodes[identity])
    for way in selected:
        output.append(way)
    # Preserve restriction relations only when their complete referenced ways are
    # present. Record dropped relations; never claim full traffic-rule fidelity.
    relations = root.findall('relation')
    retained = []
    for relation in relations:
        members = relation.findall('member')
        if members and all((m.get('type') == 'way' and m.get('ref') in way_ids)
                           or (m.get('type') == 'node' and m.get('ref') in ids) for m in members):
            output.append(relation)
            retained.append(relation.get('id'))
    raw_selected = ET.tostring(output, encoding='utf-8', xml_declaration=True)
    summary = {'schema_version': 'osm-road-selection/1', 'source_sha256': sha(raw),
        'selected_sha256': sha(raw_selected), 'selection_policy': 'explicit_way_ids_no_coordinate_edit',
        'ways': stats, 'way_count': len(stats), 'node_count': len(ids),
        'approximate_centerline_length_m': length, 'retained_relation_ids': retained,
        'source_relation_count': len(relations), 'fidelity': 'community_road_centerlines',
        'inference_flags': ['lane_width_not_measured', 'elevation_not_measured',
            'junction_geometry_generated', 'traffic_control_not_verified',
            'missing_lane_counts_use_converter_defaults'],
        'attribution': '© OpenStreetMap contributors', 'license': 'ODbL-1.0',
        'license_url': 'https://www.openstreetmap.org/copyright'}
    return raw_selected, summary


def convert(selected: bytes, origin_lon: float, origin_lat: float):
    import carla
    if not (-180 <= origin_lon <= 180 and -90 <= origin_lat <= 90):
        raise ValueError('Finite geographic origin required')
    settings = carla.Osm2OdrSettings()
    settings.use_offsets = False
    settings.center_map = False
    settings.proj_string = f'+proj=tmerc +lat_0={origin_lat} +lon_0={origin_lon} +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'
    settings.default_lane_width = 3.5
    settings.generate_traffic_lights = False
    settings.all_junctions_with_traffic_lights = False
    settings.set_osm_way_types(['primary', 'secondary', 'tertiary', 'residential', 'unclassified',
                                'primary_link', 'secondary_link', 'tertiary_link'])
    content = carla.Osm2Odr.convert(selected.decode('utf-8'), settings)
    root = ET.fromstring(content)
    if root.tag != 'OpenDRIVE' or not root.findall('road'):
        raise ValueError('CARLA OSM converter did not produce a road network')
    # Numeric references retain Korean names without the known native UTF-8
    # transport roundtrip problem. Original OSM bytes remain untouched.
    # Wall-clock export dates are not geometry and must not change build hashes.
    stable = re.sub(r'(<header\b[^>]*\bdate=")[^"]*(")', r'\g<1>1970-01-01T00:00:00Z\2', content, count=1)
    transport = stable.encode('ascii', 'xmlcharrefreplace')
    try:
        api_version = version('carla')
    except PackageNotFoundError:
        api_version = 'metadata_unavailable'
    manifest = {'schema_version': 'osm-opendrive-conversion/1', 'input_sha256': sha(selected),
        'candidate_sha256': sha(transport), 'converter': 'carla.Osm2Odr', 'carla_python_version': api_version,
        'converter_script_sha256': sha(Path(__file__).read_bytes()),
        'normalizations': ['fixed_header_date', 'ascii_xml_numeric_references_preserve_unicode'],
        'settings': {'proj_string': settings.proj_string, 'use_offsets': False, 'center_map': False,
            'default_lane_width_m': 3.5, 'generate_traffic_lights': False},
        'coordinate_transform': {'source_crs': 'EPSG:4326', 'target_proj': settings.proj_string,
            'origin_lon': origin_lon, 'origin_lat': origin_lat,
            'native_axis_mapping': 'OpenDRIVE x/y right-handed to CARLA x/-y, applied by engine'},
        'roads': len(root.findall('road')), 'junctions': len(root.findall('junction')),
        'fidelity': 'osm_derived_procedural_roads_not_surveyed_hd_map',
        'inference_flags': ['default_lane_width_3.5m', 'missing_lane_counts_inferred_by_converter',
                            'junction_geometry_generated', 'source_elevation_not_available',
                            'traffic_light_programs_not_supplied'],
        'physical_validation': 'not_established'}
    return transport, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    selection = sub.add_parser('select')
    selection.add_argument('--input', type=Path, required=True)
    selection.add_argument('--output', type=Path, required=True)
    selection.add_argument('--way-id', action='append', required=True)
    conversion = sub.add_parser('convert')
    conversion.add_argument('--input', type=Path, required=True)
    conversion.add_argument('--output', type=Path, required=True)
    conversion.add_argument('--origin-lon', type=float, required=True)
    conversion.add_argument('--origin-lat', type=float, required=True)
    args = parser.parse_args()
    if args.action == 'select':
        raw, report = select_roads(args.input.read_bytes(), args.way_id)
        save(args.output / 'selected.osm', raw)
        save(args.output / 'selection.json', encoded(report))
    else:
        raw, report = convert(args.input.read_bytes(), args.origin_lon, args.origin_lat)
        save(args.output / 'roads.xodr', raw)
        save(args.output / 'conversion.json', encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k != 'ways'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
