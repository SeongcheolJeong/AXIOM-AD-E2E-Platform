"""UE4 editor commandlet entry point. Called only by stunt_ramp_native_assets.py.

This source is prepared against official UE4 editor APIs, but is not native
import/collision evidence until the receipt is produced by the compatible editor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stunt_ramp_native_assets import seal, sha, verify_plan, verify_editor_receipt


# Only the flat approach is an OpenDRIVE road. The wall/overhang remains static
# collision geometry, never a fictitious traversable driving-network surface.
APPROACH_XODR = '''<?xml version="1.0" encoding="UTF-8"?>
<OpenDRIVE>
  <header revMajor="1" revMinor="4" name="StuntRampV1" version="1.00"
          date="2000-01-01" north="15" south="-15" east="30" west="-12"/>
  <road name="OriginalFlatApproach" length="12" id="1" junction="-1">
    <link/>
    <planView><geometry s="0" x="-12" y="0" hdg="0" length="12"><line/></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0"/></elevationProfile>
    <lateralProfile/>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving" level="false"><link/>
        <width sOffset="0" a="4" b="0" c="0" d="0"/></lane></left>
      <center><lane id="0" type="none" level="false"><link/></lane></center>
      <right><lane id="-1" type="driving" level="false"><link/>
        <width sOffset="0" a="4" b="0" c="0" d="0"/></lane></right>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
'''


def vector(value):
    return [value.x, value.y, value.z]


def actor_evidence(actor, component, unreal):
    rotation = actor.get_actor_rotation()
    transform = dict(location=vector(actor.get_actor_location()),
        rotation=[rotation.pitch, rotation.yaw, rotation.roll], scale=vector(actor.get_actor_scale3d()))
    mobility = component.get_editor_property("mobility")
    profile = str(component.get_collision_profile_name())
    enabled = component.get_collision_enabled()
    if (transform != dict(location=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])
            or mobility != unreal.ComponentMobility.STATIC or profile != "BlockAll"
            or enabled != unreal.CollisionEnabled.QUERY_AND_PHYSICS):
        raise ValueError("Actual actor/component readback differs from fixed static collision contract")
    # Enum labels are normalized only after comparing the actual editor values.
    return dict(actor_transform_cm=transform, mobility="STATIC", collision_profile=profile,
                collision_enabled="QUERY_AND_PHYSICS")


def main():
    import unreal
    path = Path(os.environ["AXIOM_RAMP_IMPORT_PLAN"]).resolve()
    plan = verify_plan(path)
    version = unreal.SystemLibrary.get_engine_version()
    if not version.startswith("4.26."):
        raise ValueError("Running editor differs from the required UE4.26")
    project = Path(plan["toolchain"]["project"]).parent
    if Path(unreal.Paths.project_dir()).resolve() != project.resolve():
        raise ValueError("Running editor project differs from the pinned plan")
    asset_library = unreal.EditorAssetLibrary
    level_library = unreal.EditorLevelLibrary
    mesh_library = unreal.EditorStaticMeshLibrary
    target = project / "Content" / plan["package"]
    if target.exists() or asset_library.does_directory_exist("/Game/" + plan["package"]):
        raise ValueError("Target package already exists; preserve imported/failed evidence")
    game_mode = unreal.load_class(None, plan["game_mode"])
    if game_mode is None:
        raise ValueError("CARLA game mode dependency is missing")
    # Check required capability before creating an asset. Missing APIs fail closed.
    for name in ("get_convex_collision_count", "get_simple_collision_count", "get_collision_complexity"):
        if not callable(getattr(mesh_library, name, None)):
            raise ValueError("Editor collision inspection capability is unavailable: " + name)
    if not level_library.new_level(plan["map_path"]):
        raise RuntimeError("Could not create the dedicated empty map")
    level_library.get_editor_world().get_world_settings().set_editor_property("default_game_mode", game_mode)
    meshes = []
    for item in plan["exports"]:
        options = unreal.FbxImportUI()
        options.set_editor_property("import_mesh", True)
        options.set_editor_property("import_as_skeletal", False)
        options.set_editor_property("import_materials", False)
        options.set_editor_property("import_textures", False)
        options.set_editor_property("automated_import_should_detect_type", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
        static = options.get_editor_property("static_mesh_import_data")
        for key, value in plan["import_settings"].items():
            if key != "collision_trace_flag":
                static.set_editor_property(key, value)
        static.set_editor_property("generate_lightmap_u_vs", False)
        task = unreal.AssetImportTask()
        for key, value in dict(filename=str(Path(plan["bundle"]) / item["file"]),
                destination_path=plan["asset_root"], destination_name=item["name"],
                automated=True, replace_existing=False, save=False, options=options).items():
            task.set_editor_property(key, value)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        asset_path = plan["asset_root"] + "/" + item["name"]
        mesh = asset_library.load_asset(asset_path)
        if not isinstance(mesh, unreal.StaticMesh):
            raise ValueError("Expected static mesh was not imported: " + asset_path)
        if len(task.get_editor_property("imported_object_paths")) != 1:
            raise ValueError("FBX imported unexpected extra assets")
        body = mesh.get_editor_property("body_setup")
        body.set_editor_property("collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX)
        body.set_editor_property("double_sided_geometry", False)
        hulls = mesh_library.get_convex_collision_count(mesh)
        simple = mesh_library.get_simple_collision_count(mesh)
        if hulls != item["convex_hulls"] or simple != hulls:
            raise ValueError("Unreal collision count differs from supplied UCX hulls")
        if mesh_library.get_collision_complexity(mesh) != unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX:
            raise ValueError("Unreal replaced the requested collision representation")
        bounds = mesh.get_bounding_box()
        measured = dict(min=vector(bounds.min), max=vector(bounds.max))
        if any(abs(measured[side][axis] - item["bounds_cm"][side][axis]) > 0.01
               for side in ("min", "max") for axis in range(3)):
            raise ValueError("Unreal axis/unit/bounds differ from source geometry")
        actor = level_library.spawn_actor_from_object(mesh, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
        if actor is None:
            raise ValueError("Failed to place imported mesh")
        actor.set_actor_label(item["name"])
        actor.set_actor_scale3d(unreal.Vector(1, 1, 1))
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        component.set_mobility(unreal.ComponentMobility.STATIC)
        component.set_collision_profile_name("BlockAll")
        component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
        observed_actor = actor_evidence(actor, component, unreal)
        if not asset_library.save_loaded_asset(mesh, only_if_is_dirty=False):
            raise RuntimeError("Could not save imported mesh")
        meshes.append(dict(name=item["name"], asset_path=asset_path, convex_hulls=hulls,
            simple_shapes=simple, bounds_cm=measured, collision_trace_flag="CTF_USE_SIMPLE_AND_COMPLEX",
            **observed_actor))
    # Native light actors, no copied external textures or proprietary scene assets.
    sun = level_library.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1500), unreal.Rotator(-45, -25, 0))
    sun.get_component_by_class(unreal.DirectionalLightComponent).set_mobility(unreal.ComponentMobility.MOVABLE)
    sky = level_library.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 1500))
    sky.get_component_by_class(unreal.SkyLightComponent).set_mobility(unreal.ComponentMobility.MOVABLE)
    level_library.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(-800, 0, 100))
    if not level_library.save_current_level():
        raise RuntimeError("Could not save the dedicated map")
    xodr = target / "Maps/OpenDrive/StuntRampV1.xodr"
    xodr.parent.mkdir(parents=True, exist_ok=True)
    xodr.write_text(APPROACH_XODR)
    config = target / "Config" / (plan["package"] + ".Package.json")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(dict(maps=[dict(name=plan["map_name"],
        path=plan["map_path"].rsplit("/", 1)[0], use_carla_materials=False)], props=[]), indent=2) + "\n")
    receipt = dict(schema_version="stunt-ramp-editor-import/1", import_plan_sha256=sha(path.read_bytes()),
        editor_version=version, map_path=plan["map_path"], meshes=meshes,
        uncooked_files={str(item.relative_to(project)): seal(item) for item in sorted(target.rglob("*")) if item.is_file()},
        editor_import_verified=True, native_map_available=False, native_collision_verified=False,
        physical_validation="not_established")
    receipt_path = path.parent / "editor-import-receipt.json"
    with receipt_path.open("x") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    verify_editor_receipt(path, plan)


if __name__ == "__main__":
    main()
