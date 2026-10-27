#!/usr/bin/env python3
"""Build original deterministic Ramp source geometry, never a cooked CARLA map.

Standard-library only. The OBJ files are authoring inputs; a separate licensed
Unreal/CARLA toolchain must import, cook and verify collision before native use.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

VERSION = 'stunt-ramp-source/1'
PARAMETERS = dict(width_m=8.0, radius_m=6.0, thickness_m=0.3, sweep_deg=110.0,
                  segments=55, approach_length_m=12.0, landing_width_m=30.0, landing_length_m=30.0)


@dataclass
class Mesh:
    name: str
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, ...]]


def prism(name, polygon_xz, width):
    """Extrude a counterclockwise XZ polygon into a closed, outward-wound prism."""
    n = len(polygon_xz)
    vertices = [(x, y, z) for y in (-width / 2, width / 2) for x, z in polygon_xz]
    faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    faces += [(i, i + n, (i + 1) % n + n, (i + 1) % n) for i in range(n)]
    return Mesh(name, vertices, faces)


def point(angle, radius):
    return (radius * math.sin(angle), PARAMETERS['radius_m'] - radius * math.cos(angle))


def meshes():
    p = PARAMETERS
    angles = [math.radians(p['sweep_deg']) * i / p['segments'] for i in range(p['segments'] + 1)]
    top = [point(angle, p['radius_m']) for angle in angles]
    bottom = [point(angle, p['radius_m'] + p['thickness_m']) for angle in angles]
    # Segment side polygons preserve the curved outline without a concave n-gon.
    vertices = [(x, y, z) for y in (-p['width_m'] / 2, p['width_m'] / 2)
                for chain in (top, bottom) for x, z in chain]
    n = len(angles)
    faces = []
    for i in range(n - 1):
        j = i + 1
        faces.extend([(i, i + 2*n, j + 2*n, j),
                      (i + n, j + n, j + 3*n, i + 3*n),
                      (i, j, j + n, i + n),
                      (i + 2*n, i + 3*n, j + 3*n, j + 2*n)])
    faces += [(0, n, 3*n, 2*n), (n-1, 3*n-1, 4*n-1, 2*n-1)]
    # Winding above points inward; reverse once so every visible solid is outward.
    ramp = Mesh('StuntRamp', vertices, [tuple(reversed(face)) for face in faces])
    approach = prism('StuntApproach', [(-12, -.3), (0, -.3), (0, 0), (-12, 0)], 8)
    landing = prism('StuntLanding', [(0, -.6), (30, -.6), (30, -.3), (0, -.3)], 30)
    collision = [prism(f'UCX_StuntRamp_{i:03d}',
                       [bottom[i], bottom[i + 1], top[i + 1], top[i]], p['width_m'])
                 for i in range(n - 1)]
    collision += [Mesh('UCX_StuntApproach_000', approach.vertices, approach.faces),
                  Mesh('UCX_StuntLanding_000', landing.vertices, landing.faces)]
    return [ramp, approach, landing], collision


def obj_bytes(objects):
    lines = ['# Original AXIOM stunt ramp source geometry; meters; X forward, Y right, Z up.',
             '# Source only: native collision import, cooking and engine validation are not established.']
    offset = 1
    for mesh in objects:
        lines.append('o ' + mesh.name)
        lines.extend('v ' + ' '.join(f'{component:.9f}' for component in vertex) for vertex in mesh.vertices)
        # Fan triangulation is valid for these convex planar quads/end caps only.
        for face in mesh.faces:
            for index in range(1, len(face) - 1):
                lines.append('f ' + ' '.join(str(i + offset) for i in (face[0], face[index], face[index + 1])))
        offset += len(mesh.vertices)
    return ('\n'.join(lines) + '\n').encode()


def sha(content):
    return hashlib.sha256(content).hexdigest()


README = """# Stunt Ramp source v1

This is original project-authored geometry: an 8 m wide, 6 m radius, 110 degree
ramp with a 12 m approach and a separate 30 x 30 m landing slab. The ramp is
0.3 m thick; its curved face has 55 segments. The landing surface is z=-0.3 m,
below the approach's z=0 m surface. The top overhang ends about 8.05 m above
the approach. These are authored dimensions, not a Tesla test-site replica.

`visual.obj` contains the three visible watertight solids. `collision.obj`
contains a separate convex hull for each ramp strip and each slab. The names
follow the intended UCX mesh association; OBJ names alone do not install
collision in Unreal. `manifest.json` pins the generator and mesh hashes.

Coordinates are meters, X forward, Y right, Z up. Import all mesh objects in a
DCC tool with names/transforms preserved; export a compatible FBX containing
the visual meshes and their matching UCX hulls. Convert meters to Unreal
centimeters exactly once (factor 100). In a compatible CARLA source/editor
build, import each visual mesh with its matching hulls as simple collision,
place actors at the common origin with static mobility, and cook a dedicated
map/package. Do not enable automatic collision that replaces the supplied
hulls without recording it. Verify normals, the approach seam, overhang
collision, contact normals, tire contact and dropped/rotated vehicle response.
Record editor/server versions and cooked map/collision package hashes.

No editor import, FBX export, engine cooking or native collision verification
has happened merely because this bundle exists. Ramp execution must remain
blocked until that evidence and the suction/contact model are implemented.
`physical_validation: not_established` applies to this source and future
simulation. No claim is made about real vehicles or safe landings.

Rights: original project-authored geometry; no external meshes or textures.
The project's distribution license is undecided (repository README), so this
bundle does not grant a new external asset license. CARLA assets and Unreal
tools retain their separate license requirements.

Regenerate into a new directory with:
`python scripts/build_stunt_ramp_asset.py --output <new-directory>`
"""


def build(output: Path):
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory is not empty; preserve prior source bundles')
    visual, collision = meshes()
    payloads = {'visual.obj': obj_bytes(visual), 'collision.obj': obj_bytes(collision),
                'README.md': README.encode()}
    generator = Path(__file__).read_bytes()
    manifest = dict(schema_version=VERSION, parameters=PARAMETERS, units='m',
        axes=dict(x='forward', y='right', z='up'), engine_import_scale_cm_per_m=100,
        source_kind='original_authored_geometry', native_map_available=False,
        collision_status='source_hulls_only_not_imported_or_cooked', physical_validation='not_established',
        generator=dict(path='scripts/build_stunt_ramp_asset.py', sha256=sha(generator)),
        rights=dict(origin='original_project_authored', external_assets=[],
                    distribution_license='project_distribution_license_undecided'),
        visual_objects=len(visual), convex_collision_objects=len(collision),
        files={name: dict(sha256=sha(content), size_bytes=len(content)) for name, content in payloads.items()})
    output.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        (output / name).write_bytes(content)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps(dict(output=str(args.output), schema_version=result['schema_version'],
                         native_map_available=False, files=result['files']), indent=2))


if __name__ == '__main__':
    main()
