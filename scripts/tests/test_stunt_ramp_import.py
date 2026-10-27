"""Pure/file tests; fake editor/cook receipts never count as native validation."""
import copy
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_stunt_ramp_fbx as exporter
import stunt_ramp_native_assets as native
import import_stunt_ramp_ue4 as editor

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "assets/stunt-ramp/source-v1"
FBX = ROOT / "assets/stunt-ramp/fbx-v1-blender-4.2.23"


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


def test_original_source_and_actual_fbx_preserve_all_meshes_and_pending_native_boundary():
    manifest, checksum, objects = exporter.load_source(SOURCE)
    assert len(objects) == 60 and len(checksum) == 64
    actual = native.verify_export(FBX)
    assert actual["source_manifest_sha256"] == checksum
    assert actual["source_geometry_sha256"] == exporter.sha(exporter.canonical(objects))
    assert [item["convex_hulls"] for item in actual["exports"]] == [55, 1, 1]
    assert actual["native_map_available"] is False and actual["editor_import_verified"] is False
    assert actual["native_collision_verified"] is False


def test_source_hash_tampering_rejected_before_export(tmp_path):
    target = tmp_path / "source"
    shutil.copytree(SOURCE, target)
    with (target / "visual.obj").open("a") as stream:
        stream.write("# changed\n")
    with pytest.raises(ValueError, match="hash/size mismatch"):
        exporter.load_source(target)


@pytest.mark.parametrize("content", [
    b"o Mesh\nv nan 0 0\n", b"o Mesh\nv 101 0 0\n", b"o Mesh\nv 0 0 0\nf 1 1 1\n",
    b"o Mesh\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n", b"o ../invalid\n",
])
def test_obj_parser_rejects_nonfinite_or_unbounded_geometry(content):
    with pytest.raises(ValueError):
        exporter.read_obj(content)


def test_roundtrip_verifier_detects_units_missing_vertex_and_reversed_normal():
    _, _, objects = exporter.load_source(SOURCE)
    mesh = objects["StuntApproach"]
    points = [[100 * value for value in point] for point in mesh["vertices_m"]]
    assert exporter.check_geometry(mesh, points, mesh["faces"]) == 0
    with pytest.raises(ValueError, match="coordinate/unit"):
        exporter.check_geometry(mesh, mesh["vertices_m"], mesh["faces"])
    with pytest.raises(ValueError, match="count"):
        exporter.check_geometry(mesh, points[:-1], mesh["faces"])
    faces = list(mesh["faces"])
    faces[0] = tuple(reversed(faces[0]))
    with pytest.raises(ValueError, match="winding/topology"):
        exporter.check_geometry(mesh, points, faces)


@pytest.fixture
def bundle(tmp_path):
    destination = tmp_path / "fbx"
    shutil.copytree(FBX, destination)
    return destination


def test_export_hash_and_single_mesh_association_are_required(bundle):
    (bundle / "StuntRamp.fbx").write_bytes(b"not the sealed FBX")
    with pytest.raises(ValueError, match="hash/size mismatch"):
        native.verify_export(bundle)


@pytest.mark.parametrize("key,value", [("auto_generate_collision", True),
    ("combine_meshes", True), ("convert_scene_unit", True), ("import_uniform_scale", 100)])
def test_export_import_contract_cannot_silently_rescale_or_replace_hulls(bundle, key, value):
    path = bundle / "export-manifest.json"
    data = native.read_json(path)
    data["import_settings"][key] = value
    write_json(path, data)
    with pytest.raises(ValueError, match="import settings"):
        native.verify_export(bundle)


@pytest.fixture
def plan(bundle, tmp_path, monkeypatch):
    project = tmp_path / "carla/Unreal/CarlaUE4/CarlaUE4.uproject"
    project.parent.mkdir(parents=True)
    checked = dict(ue_root=str(tmp_path / "ue"), carla_root=str(tmp_path / "carla"),
                   editor=str(tmp_path / "ue/UE4Editor"), project=str(project), carla_commit=native.CARLA_COMMIT)
    monkeypatch.setattr(native, "toolchain", lambda *args: copy.deepcopy(checked))
    path = native.create_plan(bundle, checked["ue_root"], checked["carla_root"], tmp_path / "plan")
    return path, native.read_json(path)


def mock_editor_receipt(path, plan):
    project = Path(plan["toolchain"]["project"]).parent
    package = "Content/" + plan["package"] + "/"
    files = {}
    for relative in ["Maps/StuntRampV1.umap", "Maps/OpenDrive/StuntRampV1.xodr",
                     "Config/" + plan["package"] + ".Package.json", *["Meshes/" + name + ".uasset" for name in exporter.COUNTS]]:
        target = project / (package + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"unit fixture only, not a cooked or native asset")
        files[package + relative] = native.seal(target)
    meshes = [dict(name=item["name"], bounds_cm=copy.deepcopy(item["bounds_cm"]), convex_hulls=item["convex_hulls"],
        simple_shapes=item["convex_hulls"], collision_trace_flag="CTF_USE_SIMPLE_AND_COMPLEX",
        actor_transform_cm=dict(location=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1]),
        mobility="STATIC", collision_profile="BlockAll", collision_enabled="QUERY_AND_PHYSICS") for item in plan["exports"]]
    receipt = dict(schema_version="stunt-ramp-editor-import/1", import_plan_sha256=exporter.sha(path.read_bytes()),
        editor_import_verified=True, native_map_available=False, meshes=meshes, uncooked_files=files)
    write_json(path.parent / "editor-import-receipt.json", receipt)
    return receipt


def test_plan_is_new_scoped_and_does_not_grant_native_readiness(plan):
    path, data = plan
    assert native.verify_plan(path) == data
    assert data["native_map_available"] is False
    assert data["map_path"].startswith("/Game/AxiomStuntRampV1_")
    assert len(data["required_native_checks"]) == 7
    with pytest.raises(ValueError, match="must be new"):
        native.create_plan(data["bundle"], "ignored", "ignored", path.parent)
    data["map_path"] = "/Game/Carla/Maps/Town10HD_Opt"
    write_json(path, data)
    with pytest.raises(ValueError, match="destination"):
        native.verify_plan(path)


@pytest.mark.parametrize("mutate", [
    lambda r: r["meshes"][0].update(convex_hulls=1),
    lambda r: r["meshes"][0].update(collision_trace_flag="CTF_USE_COMPLEX_AS_SIMPLE"),
    lambda r: r["meshes"][0]["bounds_cm"].update(min=[float("nan"), 0, 0]),
    lambda r: r["meshes"][0]["bounds_cm"].update(min=[]),
    lambda r: r["meshes"][0]["bounds_cm"]["max"].__setitem__(0, 63000),
    lambda r: r["meshes"][0].update(collision_profile="NoCollision"),
    lambda r: r["meshes"][0].update(collision_enabled="QUERY_ONLY"),
    lambda r: r.update(native_map_available=True),
])
def test_editor_proof_fails_closed_on_collision_units_or_false_promotion(plan, mutate):
    path, data = plan
    receipt = mock_editor_receipt(path, data)
    mutate(receipt)
    write_json(path.parent / "editor-import-receipt.json", receipt)
    with pytest.raises(ValueError):
        native.verify_editor_receipt(path, data)


def test_editor_proof_binds_map_and_mesh_files(plan):
    path, data = plan
    receipt = mock_editor_receipt(path, data)
    native.verify_editor_receipt(path, data)
    target = Path(data["toolchain"]["project"]).parent / next(iter(receipt["uncooked_files"]))
    target.write_bytes(b"changed after verification")
    with pytest.raises(ValueError, match="changed"):
        native.verify_editor_receipt(path, data)


def test_cook_exit_zero_without_cooked_assets_cannot_pass(plan, tmp_path, monkeypatch):
    path, data = plan
    mock_editor_receipt(path, data)
    run = Mock()
    monkeypatch.setattr(native.subprocess, "run", run)
    with pytest.raises(ValueError, match="regular artifact"):
        native.cook(path, tmp_path / "cook")
    assert not (tmp_path / "cook/cook-manifest.json").exists()
    args = run.call_args.args[0]
    assert "-targetplatform=LinuxNoEditor" in args and "-iterate" not in args


def test_mocked_cook_seals_new_files_but_does_not_establish_runtime_readiness(plan, tmp_path, monkeypatch):
    path, data = plan
    mock_editor_receipt(path, data)
    output = tmp_path / "cook"
    def fake_cook(command, **kwargs):
        content = output / "CarlaUE4/Content" / data["package"]
        for relative in ["Maps/StuntRampV1.umap", *["Meshes/" + name + ".uasset" for name in exporter.COUNTS]]:
            target = content / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fake cooked fixture; never native acceptance")
    monkeypatch.setattr(native.subprocess, "run", fake_cook)
    result = native.cook(path, output)
    assert result["cooked_artifacts_present"] is True
    assert result["native_map_available"] is False and result["native_collision_verified"] is False
    assert any(name.endswith(".xodr") for name in result["files"])
    with pytest.raises(ValueError, match="must be a new"):
        native.cook(path, output)


def test_wrong_or_stock_unreal_toolchain_rejected_before_import(tmp_path, monkeypatch):
    ue, carla = tmp_path / "ue", tmp_path / "carla"
    build = ue / "Engine/Build/Build.version"
    build.parent.mkdir(parents=True)
    write_json(build, dict(MajorVersion=5, MinorVersion=6))
    with pytest.raises(ValueError, match="Unreal 4.26"):
        native.toolchain(ue, carla)
    write_json(build, dict(MajorVersion=4, MinorVersion=26))
    monkeypatch.setattr(native, "git", lambda *args: "https://github.com/EpicGames/UnrealEngine.git")
    with pytest.raises(ValueError, match="not the CARLA Unreal fork"):
        native.toolchain(ue, carla)


def test_plan_rejects_importer_code_drift(plan, monkeypatch):
    path, data = plan
    original = native.seal
    def changed_script(item):
        result = original(item)
        if Path(item).name == "import_stunt_ramp_ue4.py":
            result["sha256"] = "0" * 64
        return result
    monkeypatch.setattr(native, "seal", changed_script)
    with pytest.raises(ValueError, match="scripts changed"):
        native.verify_plan(path)


@pytest.mark.parametrize("changed", [None, "position", "rotation", "scale", "mobility", "profile", "enabled"])
def test_editor_reads_actual_actor_component_values(changed):
    def point(x, y, z):
        return SimpleNamespace(x=x, y=y, z=z)
    actor = SimpleNamespace(
        get_actor_location=lambda: point(1 if changed == "position" else 0, 0, 0),
        get_actor_rotation=lambda: SimpleNamespace(pitch=0, yaw=2 if changed == "rotation" else 0, roll=0),
        get_actor_scale3d=lambda: point(2 if changed == "scale" else 1, 1, 1))
    component = SimpleNamespace(
        get_editor_property=lambda name: "movable" if changed == "mobility" else "static",
        get_collision_profile_name=lambda: "NoCollision" if changed == "profile" else "BlockAll",
        get_collision_enabled=lambda: "query_only" if changed == "enabled" else "query_physics")
    unreal = SimpleNamespace(ComponentMobility=SimpleNamespace(STATIC="static"),
                             CollisionEnabled=SimpleNamespace(QUERY_AND_PHYSICS="query_physics"))
    if changed:
        with pytest.raises(ValueError, match="readback"):
            editor.actor_evidence(actor, component, unreal)
    else:
        result = editor.actor_evidence(actor, component, unreal)
        assert result["actor_transform_cm"]["location"] == [0, 0, 0]
        assert result["collision_enabled"] == "QUERY_AND_PHYSICS"
