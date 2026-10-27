"""Original mesh topology and deterministic source provenance; no native run."""
from collections import Counter
import hashlib
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_stunt_ramp_asset as asset


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def test_every_visual_and_collision_object_is_closed_and_outward_wound():
    visual, collision = asset.meshes()
    assert len(visual) == 3 and len(collision) == 57
    for mesh in visual + collision:
        edges = Counter()
        volume = 0
        for face in mesh.faces:
            for i, a in enumerate(face):
                edges[(a, face[(i+1) % len(face)])] += 1
            for i in range(1, len(face)-1):
                a, b, c = [mesh.vertices[index] for index in (face[0], face[i], face[i+1])]
                volume += dot(a, cross(b, c)) / 6
        assert all(count == 1 and edges[(b, a)] == 1 for (a, b), count in edges.items()), mesh.name
        assert volume > 0, (mesh.name, volume)


def test_collision_hulls_are_convex_and_cover_ramp_strips():
    _, collision = asset.meshes()
    for mesh in collision:
        for face in mesh.faces:
            a, b, c = [mesh.vertices[index] for index in face[:3]]
            normal = cross(tuple(x-y for x, y in zip(b, a)), tuple(x-y for x, y in zip(c, a)))
            assert all(dot(normal, tuple(x-y for x, y in zip(vertex, a))) <= 1e-8 for vertex in mesh.vertices), mesh.name
    top = asset.point(math.radians(110), 6)
    assert top[0] == pytest.approx(5.6381557247)
    assert top[1] == pytest.approx(8.05212086)


def test_bundle_is_reproducible_and_has_source_only_provenance(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    manifest = asset.build(a)
    assert manifest == asset.build(b)
    assert manifest['native_map_available'] is False
    assert manifest['collision_status'] == 'source_hulls_only_not_imported_or_cooked'
    assert manifest['physical_validation'] == 'not_established'
    for name, record in manifest['files'].items():
        content = (a/name).read_bytes()
        assert content == (b/name).read_bytes()
        assert len(content) == record['size_bytes']
        assert hashlib.sha256(content).hexdigest() == record['sha256']
        assert len(content) < 100000
    assert manifest['generator']['sha256'] == hashlib.sha256(Path(asset.__file__).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='preserve'):
        asset.build(a)
