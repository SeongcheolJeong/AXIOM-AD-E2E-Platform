#!/usr/bin/env python3
"""Plan/import/cook original Ramp assets with a pinned, separately installed UE4.

The plan action only verifies files and writes a new plan. Import and cook are
explicit commands, never automatic installation into a running CARLA server.
Even successful cooking leaves native collision/controller readiness unverified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from export_stunt_ramp_fbx import CARLA_COMMIT, COUNTS, canonical, sha

ENGINE_REMOTE = "github.com/CarlaUnreal/UnrealEngine"
GAME_MODE = "/Game/Carla/Blueprints/Game/CarlaGameMode.CarlaGameMode_C"
BUILD_SCRIPTS = ("stunt_ramp_native_assets.py", "import_stunt_ramp_ue4.py", "export_stunt_ramp_fbx.py")


def read_json(path):
    return json.loads(Path(path).read_text())


def seal(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected regular artifact: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return dict(sha256=digest.hexdigest(), size_bytes=path.stat().st_size)


def check_bounds(value):
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise ValueError("Bounds must contain min/max vectors")
    for vector in value.values():
        if (not isinstance(vector, list) or len(vector) != 3
                or any(type(item) not in (int, float) or not math.isfinite(item) for item in vector)):
            raise ValueError("Bounds must contain three finite numeric coordinates")
    if any(low > high for low, high in zip(value["min"], value["max"])):
        raise ValueError("Bounds are reversed")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=15).strip()


def verify_export(bundle):
    bundle = Path(bundle).resolve()
    manifest = read_json(bundle / "export-manifest.json")
    if (manifest.get("schema_version") != "stunt-ramp-fbx/1"
            or manifest.get("required_carla_commit") != CARLA_COMMIT
            or manifest.get("geometry_units") != "cm"
            or manifest.get("geometry_roundtrip_verified") is not True
            or manifest.get("native_map_available") is not False):
        raise ValueError("Unsupported, unverified or incorrectly promoted FBX manifest")
    settings = dict(auto_generate_collision=False, one_convex_hull_per_ucx=True,
        combine_meshes=False, convert_scene=False, convert_scene_unit=False, import_uniform_scale=1.0,
        collision_trace_flag="CTF_USE_SIMPLE_AND_COMPLEX")
    if manifest.get("import_settings") != settings:
        raise ValueError("FBX units/collision import settings differ from the exporter contract")
    exports = manifest.get("exports", [])
    if len(exports) != 3 or {item["name"] for item in exports} != set(COUNTS):
        raise ValueError("Expected exactly three per-mesh FBX exports")
    for item in exports:
        check_bounds(item["bounds_cm"])
        if (item["file"] != item["name"] + ".fbx" or type(item["convex_hulls"]) is not int
                or item["convex_hulls"] != COUNTS[item["name"]]):
            raise ValueError("Invalid FBX name or hull count")
        if seal(bundle / item["file"]) != {key: item[key] for key in ("sha256", "size_bytes")}:
            raise ValueError("FBX artifact hash/size mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("source_manifest_sha256", "")):
        raise ValueError("Missing source manifest provenance")
    return manifest


def toolchain(ue_root, carla_root):
    ue_root, carla_root = Path(ue_root).resolve(), Path(carla_root).resolve()
    editor = ue_root / "Engine/Binaries/Linux/UE4Editor"
    project = carla_root / "Unreal/CarlaUE4/CarlaUE4.uproject"
    engine_build = read_json(ue_root / "Engine/Build/Build.version")
    if (engine_build.get("MajorVersion"), engine_build.get("MinorVersion")) != (4, 26):
        raise ValueError("The installed CARLA source requires its Unreal 4.26 fork")
    # Do not accept a stock engine just because its version number matches.
    remote = git(ue_root, "config", "--get", "remote.origin.url")
    if ENGINE_REMOTE not in remote.replace(":CarlaUnreal/", "/CarlaUnreal/"):
        raise ValueError("Engine origin is not the CARLA Unreal fork")
    engine_commit = git(ue_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", engine_commit) or git(ue_root, "diff", "HEAD", "--name-only"):
        raise ValueError("Engine tracked source must have a recorded clean commit")
    if git(carla_root, "rev-parse", "HEAD") != CARLA_COMMIT or git(carla_root, "diff", "HEAD", "--name-only"):
        raise ValueError("CARLA tracked source must match the installed server's exact clean commit")
    if not editor.is_file() or read_json(project).get("EngineAssociation") != "4.26":
        raise ValueError("Compatible built UE4Editor/CarlaUE4 project is missing")
    config = project.parent / "Config/DefaultEngine.ini"
    if "GlobalDefaultGameMode=" + GAME_MODE not in config.read_text():
        raise ValueError("Expected CARLA game mode is not configured")
    return dict(ue_root=str(ue_root), carla_root=str(carla_root), editor=str(editor), project=str(project),
        carla_commit=CARLA_COMMIT, engine_commit=engine_commit, engine_build_version=engine_build,
        engine_origin=ENGINE_REMOTE, editor_artifact=seal(editor), project_artifact=seal(project),
        default_engine_config=seal(config))


def create_plan(bundle, ue_root, carla_root, output):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    exported = verify_export(bundle)
    # Every actual import/cook action repeats this preflight; a plan alone grants no readiness.
    checked = toolchain(ue_root, carla_root)
    if output.exists():
        raise ValueError("Plan directory must be new")
    package = "AxiomStuntRampV1_" + exported["source_manifest_sha256"][:12]
    plan = dict(schema_version="stunt-ramp-import-plan/1", bundle=str(bundle),
        export_manifest=seal(bundle / "export-manifest.json"), toolchain=checked,
        builder_scripts={name: seal(Path(__file__).with_name(name)) for name in BUILD_SCRIPTS},
        package=package, asset_root="/Game/" + package + "/Meshes",
        map_path="/Game/" + package + "/Maps/StuntRampV1", map_name="StuntRampV1",
        game_mode=GAME_MODE, import_settings=exported["import_settings"],
        source_manifest_sha256=exported["source_manifest_sha256"], exports=exported["exports"],
        map_scope="bounded_original_static_stunt_geometry_no_traffic_network",
        spawn_transform_m=dict(x=-8.0, y=0.0, z=1.0, pitch=0.0, yaw=0.0, roll=0.0),
        editor_import_verified=False, native_map_available=False, physical_validation="not_established",
        required_native_checks=["loaded_map_and_server_build_identity", "native_ray_hit_positions_and_plane_fit_normal_estimates",
            "approach_ramp_seam_tire_contact", "wall_and_overhang_contact", "dropped_rotated_vehicle_contact",
            "suction_force_contact_and_budget_limits", "safe_cleanup_and_world_restoration"])
    output.mkdir(parents=True)
    plan_path = output / "import-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return plan_path


def verify_plan(path):
    plan = read_json(path)
    if plan.get("schema_version") != "stunt-ramp-import-plan/1":
        raise ValueError("Unsupported import plan")
    if plan.get("builder_scripts") != {name: seal(Path(__file__).with_name(name)) for name in BUILD_SCRIPTS}:
        raise ValueError("Asset builder scripts changed after planning")
    bundle = Path(plan["bundle"])
    exported = verify_export(bundle)
    if seal(bundle / "export-manifest.json") != plan["export_manifest"]:
        raise ValueError("Export manifest changed after planning")
    if plan["exports"] != exported["exports"] or plan["import_settings"] != exported["import_settings"]:
        raise ValueError("Import plan changed the export contract")
    package = "AxiomStuntRampV1_" + exported["source_manifest_sha256"][:12]
    if (plan["package"] != package or plan["asset_root"] != "/Game/" + package + "/Meshes"
            or plan["map_path"] != "/Game/" + package + "/Maps/StuntRampV1"
            or plan["map_name"] != "StuntRampV1" or plan["game_mode"] != GAME_MODE):
        raise ValueError("Import destination differs from the fixed new package")
    checked = toolchain(plan["toolchain"]["ue_root"], plan["toolchain"]["carla_root"])
    if checked != plan["toolchain"]:
        raise ValueError("Toolchain changed after planning")
    return plan


def run_import(path):
    path = Path(path).resolve()
    plan = verify_plan(path)
    receipt = path.parent / "editor-import-receipt.json"
    if receipt.exists():
        raise ValueError("Preserve existing editor import receipts")
    import os
    environment = dict(os.environ, AXIOM_RAMP_IMPORT_PLAN=str(path))
    script = Path(__file__).with_name("import_stunt_ramp_ue4.py")
    command = [plan["toolchain"]["editor"], plan["toolchain"]["project"], "-unattended", "-nop4",
               "-nosplash", "-NullRHI", "-run=pythonscript", "-script=" + str(script)]
    with (path.parent / "editor-import.log").open("xb") as log:
        subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    verify_editor_receipt(path, plan)
    return dict(editor_import_verified=True, receipt=str(receipt), native_map_available=False)


def verify_editor_receipt(path, plan):
    receipt_path = Path(path).parent / "editor-import-receipt.json"
    receipt = read_json(receipt_path)
    if (receipt.get("schema_version") != "stunt-ramp-editor-import/1"
            or receipt.get("import_plan_sha256") != sha(Path(path).read_bytes())
            or receipt.get("editor_import_verified") is not True
            or receipt.get("native_map_available") is not False):
        raise ValueError("Missing/changed editor import proof")
    expected = {item["name"]: item for item in plan["exports"]}
    if {item["name"] for item in receipt["meshes"]} != set(expected) or len(receipt["meshes"]) != 3:
        raise ValueError("Editor receipt lacks the three imported meshes")
    for mesh in receipt["meshes"]:
        target = expected[mesh["name"]]
        check_bounds(mesh["bounds_cm"])
        if (type(mesh["convex_hulls"]) is not int or type(mesh["simple_shapes"]) is not int
                or mesh["convex_hulls"] != target["convex_hulls"] or mesh["simple_shapes"] != target["convex_hulls"]
                or mesh["collision_trace_flag"] != "CTF_USE_SIMPLE_AND_COMPLEX"):
            raise ValueError("Imported collision differs from the supplied UCX hulls")
        if (mesh.get("actor_transform_cm") != dict(location=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])
                or mesh.get("mobility") != "STATIC" or mesh.get("collision_profile") != "BlockAll"
                or mesh.get("collision_enabled") != "QUERY_AND_PHYSICS"):
            raise ValueError("Imported actors differ from fixed static identity transforms")
        for side in ("min", "max"):
            if any(abs(actual - wanted) > 0.01 for actual, wanted in zip(mesh["bounds_cm"][side], target["bounds_cm"][side])):
                raise ValueError("Imported bounds differ from source centimeters")
    project = Path(plan["toolchain"]["project"]).parent
    for relative, expected_seal in receipt["uncooked_files"].items():
        entry = Path(relative)
        if entry.is_absolute() or ".." in entry.parts or not relative.startswith("Content/" + plan["package"] + "/"):
            raise ValueError("Editor receipt contains an unrelated asset")
        if seal(project / entry) != expected_seal:
            raise ValueError("Uncooked asset changed after editor verification")
    for expected_file in ["Maps/StuntRampV1.umap", *["Meshes/" + name + ".uasset" for name in COUNTS]]:
        if "Content/" + plan["package"] + "/" + expected_file not in receipt["uncooked_files"]:
            raise ValueError("Editor receipt is missing a required map/mesh artifact")
    return receipt


def cook(path, output):
    path, output = Path(path).resolve(), Path(output).resolve()
    plan = verify_plan(path)
    editor_receipt = verify_editor_receipt(path, plan)
    if output.exists():
        raise ValueError("Cook output must be a new directory")
    output.mkdir(parents=True)
    command = [plan["toolchain"]["editor"], plan["toolchain"]["project"], "-run=cook",
        "-map=" + plan["map_path"], "-cooksinglepackage", "-targetplatform=LinuxNoEditor",
        "-OutputDir=" + str(output), "-unattended", "-nop4"]
    with (output / "cook.log").open("xb") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    # Fresh output only, no -iterate: uncooked source presence cannot pass this check.
    content = output / "CarlaUE4/Content" / plan["package"]
    for relative in ["Maps/StuntRampV1.umap", *["Meshes/" + name + ".uasset" for name in COUNTS]]:
        seal(content / relative)
    # Unreal's cook command does not stage raw CARLA map/config metadata.
    # Copy only already sealed original metadata, preserving package-relative paths.
    project = Path(plan["toolchain"]["project"]).parent
    for relative, expected_seal in editor_receipt["uncooked_files"].items():
        if relative.endswith((".xodr", ".Package.json")):
            source = project / relative
            if seal(source) != expected_seal:
                raise ValueError("Raw map metadata changed during cooking")
            destination = output / "CarlaUE4" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    files = {str(item.relative_to(output)): seal(item) for item in sorted(output.rglob("*")) if item.is_file()}
    receipt = dict(schema_version="stunt-ramp-cook/1", import_plan_sha256=sha(path.read_bytes()),
        editor_receipt=seal(path.parent / "editor-import-receipt.json"), toolchain=plan["toolchain"],
        map_path=plan["map_path"], target_platform="LinuxNoEditor", files=files,
        cooked_artifacts_present=True, native_map_available=False, native_collision_verified=False,
        physical_validation="not_established")
    (output / "cook-manifest.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    build = actions.add_parser("plan")
    for name in ("bundle", "ue-root", "carla-root", "output"):
        build.add_argument("--" + name, type=Path, required=True)
    ingest = actions.add_parser("import")
    ingest.add_argument("--plan", type=Path, required=True)
    cooking = actions.add_parser("cook")
    cooking.add_argument("--plan", type=Path, required=True)
    cooking.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "plan":
        result = {"plan": str(create_plan(args.bundle, args.ue_root, args.carla_root, args.output)), "native_map_available": False}
    elif args.action == "import":
        result = run_import(args.plan)
    else:
        result = cook(args.plan, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
