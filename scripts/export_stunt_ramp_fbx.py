#!/usr/bin/env python3
"""Export sealed original Ramp OBJ geometry with Blender; never cook/install it.

Run with env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONHASHSEED=0
blender --python-use-system-env --background --factory-startup --python-exit-code 1
--python export_stunt_ramp_fbx.py -- --source <source-v1> --output <NEW directory>.
Each FBX contains one visible mesh plus its UCX hulls. A Blender round trip checks
geometry, units and outward triangle winding; Unreal import remains unverified.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

CARLA_COMMIT = "1ae535667d2c75ef92ba44d0e7506419b2e577cb"
COUNTS = {"StuntRamp": 55, "StuntApproach": 1, "StuntLanding": 1}
EXPORT_OPTIONS = dict(use_selection=True, global_scale=1.0, apply_unit_scale=True,
    apply_scale_options="FBX_SCALE_NONE", axis_forward="Y", axis_up="Z",
    use_space_transform=False, bake_space_transform=False, object_types={"MESH"},
    use_mesh_modifiers=False, mesh_smooth_type="FACE", use_triangles=True,
    bake_anim=False, add_leaf_bones=False, use_custom_props=False, path_mode="STRIP")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def read_obj(content):
    """Accept the bounded source generator's OBJ subset, preserving exact names."""
    vertices, objects, current = [], {}, None
    for line in content.decode("utf-8").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0] == "o" and len(words) == 2:
            name = words[1]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in objects:
                raise ValueError("Invalid or repeated OBJ object name")
            current = objects[name] = {"name": name, "vertices_m": [], "faces": [], "offset": len(vertices)}
        elif words[0] == "v" and len(words) == 4 and current is not None:
            point = tuple(float(value) for value in words[1:])
            if not all(math.isfinite(value) and abs(value) <= 100 for value in point):
                raise ValueError("OBJ vertex outside finite 100 m source bounds")
            vertices.append(point)
            current["vertices_m"].append(point)
        elif words[0] == "f" and len(words) == 4 and current is not None:
            indices = tuple(int(value) - 1 - current["offset"] for value in words[1:])
            if len(set(indices)) != 3 or not all(0 <= value < len(current["vertices_m"]) for value in indices):
                raise ValueError("OBJ face references outside its object or is degenerate")
            current["faces"].append(indices)
        else:
            raise ValueError("Unsupported OBJ record")
    for item in objects.values():
        item.pop("offset")
        if not item["vertices_m"] or not item["faces"]:
            raise ValueError("Empty OBJ object")
    return objects


def load_source(source):
    source = Path(source)
    raw_manifest = (source / "manifest.json").read_bytes()
    manifest = json.loads(raw_manifest)
    if (manifest.get("schema_version") != "stunt-ramp-source/1" or manifest.get("units") != "m"
            or manifest.get("axes") != {"x": "forward", "y": "right", "z": "up"}
            or manifest.get("native_map_available") is not False):
        raise ValueError("Unsupported or incorrectly promoted source manifest")
    for name in ("visual.obj", "collision.obj", "README.md"):
        content = (source / name).read_bytes()
        expected = manifest["files"][name]
        if sha(content) != expected["sha256"] or len(content) != expected["size_bytes"]:
            raise ValueError("Source hash/size mismatch: " + name)
    visual = read_obj((source / "visual.obj").read_bytes())
    collision = read_obj((source / "collision.obj").read_bytes())
    if set(visual) != set(COUNTS):
        raise ValueError("Expected the three fixed visible meshes")
    expected_hulls = {f"UCX_{name}_{index:03d}" for name, count in COUNTS.items() for index in range(count)}
    if set(collision) != expected_hulls:
        raise ValueError("UCX association/count differs from the fixed source contract")
    objects = {**visual, **collision}
    return manifest, sha(raw_manifest), objects


def bounds_cm(mesh):
    points = mesh["vertices_m"]
    return {"min": [100 * min(point[axis] for point in points) for axis in range(3)],
            "max": [100 * max(point[axis] for point in points) for axis in range(3)]}


def cycle(face):
    return min(tuple(face[index:]) + tuple(face[:index]) for index in range(3))


def check_geometry(expected, actual_vertices_cm, actual_faces):
    """Check full vertex/triangle topology and winding after export + import."""
    points = [tuple(100 * value for value in point) for point in expected["vertices_m"]]
    if len(points) != len(actual_vertices_cm) or len(expected["faces"]) != len(actual_faces):
        raise ValueError("Round-trip vertex/triangle count differs: " + expected["name"])
    mapping, unmatched = {}, set(range(len(points)))
    max_error = 0.0
    for index, actual in enumerate(actual_vertices_cm):
        if len(actual) != 3 or not all(math.isfinite(value) for value in actual):
            raise ValueError("Nonfinite round-trip vertex")
        candidate = min(unmatched, key=lambda i: sum((points[i][axis] - actual[axis]) ** 2 for axis in range(3)))
        for value, target in zip(actual, points[candidate]):
            error = abs(value - target)
            # Four float32 rounding steps, only for DCC geometry serialization.
            if error > 4 * 2**-23 * max(1.0, abs(target)):
                raise ValueError("Round-trip coordinate/unit mismatch: " + expected["name"])
            max_error = max(max_error, error)
        mapping[index] = candidate
        unmatched.remove(candidate)
    expected_faces = sorted(cycle(face) for face in expected["faces"])
    actual = sorted(cycle(tuple(mapping[index] for index in face)) for face in actual_faces)
    if actual != expected_faces:
        raise ValueError("Round-trip triangle winding/topology differs: " + expected["name"])
    return max_error


def export(source, output):
    import bpy
    import io_scene_fbx.export_fbx_bin as exporter
    if (os.environ.get("PYTHONHASHSEED") != "0" or sys.flags.ignore_environment
            or sys.flags.hash_randomization):
        raise ValueError("Use PYTHONHASHSEED=0 and --python-use-system-env; Blender otherwise ignores the seed")
    manifest, manifest_sha, objects = load_source(source)
    output = Path(output)
    if output.exists():
        raise ValueError("Output must be a new directory; preserve prior exports")
    output.mkdir(parents=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 0.01
    # Only serialization metadata is normalized; no geometry/exporter source is modified.
    original_header = exporter.fbx_header_elements
    def fixed_header(root, scene_data, time=None):
        return original_header(root, scene_data, time=datetime.datetime(2000, 1, 1))
    exporter.fbx_header_elements = fixed_header
    results = []
    try:
        for name, hull_count in COUNTS.items():
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            selected = [objects[name]] + [objects[f"UCX_{name}_{i:03d}"] for i in range(hull_count)]
            for mesh in selected:
                data = bpy.data.meshes.new(mesh["name"])
                data.from_pydata([tuple(100 * value for value in point) for point in mesh["vertices_m"]], [], mesh["faces"])
                data.update()
                obj = bpy.data.objects.new(mesh["name"], data)
                bpy.context.collection.objects.link(obj)
                obj.select_set(True)
            target = output / (name + ".fbx")
            if bpy.ops.export_scene.fbx(filepath=str(target.resolve()), **EXPORT_OPTIONS) != {"FINISHED"}:
                raise RuntimeError("Blender FBX export failed")
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            if bpy.ops.import_scene.fbx(filepath=str(target.resolve()), use_anim=False, use_image_search=False) != {"FINISHED"}:
                raise RuntimeError("Blender FBX round-trip import failed")
            imported = {obj.name: obj for obj in bpy.context.scene.objects if obj.type == "MESH"}
            if set(imported) != {mesh["name"] for mesh in selected}:
                raise ValueError("FBX round-trip changed visible/UCX object names")
            errors = {}
            for mesh in selected:
                obj = imported[mesh["name"]]
                errors[mesh["name"]] = check_geometry(mesh,
                    [tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices],
                    [tuple(polygon.vertices) for polygon in obj.data.polygons])
            payload = target.read_bytes()
            results.append(dict(name=name, file=target.name, sha256=sha(payload), size_bytes=len(payload),
                convex_hulls=hull_count, bounds_cm=bounds_cm(objects[name]),
                roundtrip_objects=len(selected), maximum_roundtrip_error_cm=max(errors.values())))
    finally:
        exporter.fbx_header_elements = original_header
    receipt = dict(schema_version="stunt-ramp-fbx/1", source_manifest_sha256=manifest_sha,
        source_geometry_sha256=sha(canonical(objects)), source_files=manifest["files"],
        exporter_sha256=sha(Path(__file__).read_bytes()), blender_version=bpy.app.version_string,
        blender_build_hash=bpy.app.build_hash.decode(), fbx_timestamp_normalized_to="2000-01-01T00:00:00",
        geometry_units="cm", source_to_geometry_scale=100, scene_scale_length_m=0.01,
        export_axis_forward="Y", export_axis_up="Z", geometry_roundtrip_verified=True,
        required_carla_commit=CARLA_COMMIT, required_engine_fork="CarlaUnreal/UnrealEngine:carla",
        required_engine_major_minor="4.26", editor_import_verified=False,
        native_map_available=False, native_collision_verified=False, physical_validation="not_established",
        import_settings=dict(auto_generate_collision=False, one_convex_hull_per_ucx=True,
            combine_meshes=False, convert_scene=False, convert_scene_unit=False, import_uniform_scale=1.0,
            collision_trace_flag="CTF_USE_SIMPLE_AND_COMPLEX"), exports=results)
    (output / "export-manifest.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    print(json.dumps(export(args.source, args.output), indent=2))


if __name__ == "__main__":
    main()
