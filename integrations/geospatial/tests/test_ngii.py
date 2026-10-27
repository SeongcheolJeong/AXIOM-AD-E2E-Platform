"""Generated semantic SHP fixtures ONLY; none is acquired/measured NGII data."""
import hashlib
import io
import json
import math
import zipfile
from xml.etree import ElementTree as ET

import pytest
import shapefile
from pyproj import CRS, Transformer

from axiom_geospatial import NgiiImportOptions, capabilities, inspect_ngii_zip


def make_zip(*, links=None, bounds=True, boundary_points=None, crs="EPSG:5186", prj=True, encoding="utf-8", cpg=True, z=True, reverse=False, node_gap=0, extra=None, prefix="synthetic_semantic_fixture/"):
    """400 m artificial Korean projected-coordinate fixture, not a real road."""
    if links is None:
        links = [dict(id="L1", start="N1", end="N2", points=[(0, 0), (100, 0), (200, 0)]), dict(id="L2", start="N2", end="N3", points=[(200, 0), (300, 0), (400, 0)])]
    node_points = {}
    for link in links:
        node_points[link["start"]] = link["points"][0]
        node_points[link["end"]] = link["points"][-1]
    output = io.BytesIO()
    members = {}

    def emit(name, shape_type, fields, records):
        streams = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
        writer = shapefile.Writer(**streams, shapeType=shape_type, encoding=encoding)
        for fieldname in fields:
            writer.field(fieldname, "C", size=80)
        for points, values in records:
            coords = [(200000 + p[0], 600000 + p[1], 12 + p[0] * .001) for p in points]
            if shape_type in (shapefile.POINT, shapefile.POINTZ):
                (writer.pointz if z else writer.point)(*(coords[0] if z else coords[0][:2]))
            else:
                (writer.linez if z else writer.line)([coords if z else [p[:2] for p in coords]])
            writer.record(*values)
        writer.close()
        for extension, stream in streams.items():
            members[prefix + name + "." + extension] = stream.getvalue()
        if prj:
            members[prefix + name + ".prj"] = CRS.from_user_input(crs).to_wkt(version="WKT1_ESRI").encode()
        if cpg:
            members[prefix + name + ".cpg"] = encoding.encode()

    emit("A1_NODE", shapefile.POINTZ if z else shapefile.POINT, ["ID"], [([(point[0] + (node_gap if key == "N2" else 0), point[1])], [key]) for key, point in node_points.items()])
    emit("A2_LINK", shapefile.POLYLINEZ if z else shapefile.POLYLINE, ["ID", "FromNodeID", "ToNodeID", "RoadName"], [(list(reversed(link["points"])) if reverse else link["points"], [link["id"], link["start"], link["end"], "합성 의미 검사용 도로"]) for link in links])
    if bounds:
        records = []
        for link in links:
            for side in (-1, 1):
                records.append((boundary_points(link, side) if boundary_points else [(x, y + side * 1.75) for x, y in link["points"]], [link["id"] + ("left" if side == 1 else "right"), link["id"] if side == -1 else "", link["id"] if side == 1 else ""]))
        emit("B2_SURFACELINEMARK", shapefile.POLYLINEZ if z else shapefile.POLYLINE, ["ID", "L_LinkID", "R_LinkID"], records)
    members.update(extra or {})
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members.items()):
            archive.writestr(name, payload)
    return output.getvalue()


def inspect(raw=None, **options):
    return inspect_ngii_zip(raw or make_zip(), source_name="SYNTHETIC SEMANTIC FIXTURE — not real NGII", options=options)


def error_code(result):
    return next(v["code"] for v in result["diagnostics"] if v["severity"] == "error")


def test_source_geometry_topology_width_elevation_and_determinism():
    raw = make_zip()
    a, b = inspect(raw), inspect(raw)
    assert a["ready"], a["diagnostics"]
    assert a["xodr_text"] == b["xodr_text"]
    assert a["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert a["native_qualified"] is False
    assert not a["inference_flags"]
    json.dumps(a, allow_nan=False)
    net = a["normalized_network"]
    assert net["total_lane_length_m"] == 400
    assert net["connections"] == [{"from_lane": "L1", "to_lane": "L2", "source_node_id": "N2", "provenance": "exact_shared_A1_node_id"}]
    assert net["lanes"][0]["centerline_local_m"][0] == [0, 0, 0]
    assert net["lanes"][0]["width_profile"][0]["width_m"] == 3.5
    root = ET.fromstring(a["xodr_text"])
    assert root.find("./header/offset") is None
    assert root.find("./header/geoReference") is None
    transform_metadata = next(v for v in root.findall("userData") if v.get("code") == "axiom-local-coordinate-transform")
    assert json.loads(transform_metadata.get("value"))["local_origin_target_m"] == [200000, 600000, 12]
    roads = root.findall("road")
    assert roads[0].find("./link/successor").attrib == {"elementType": "road", "elementId": "2", "contactPoint": "start"}
    assert roads[1].find("./link/predecessor").get("elementId") == "1"
    assert roads[0].find("./lanes/laneSection/right/lane/link/successor").get("id") == "-1"
    assert float(roads[0].find("./lanes/laneOffset").get("a")) == 1.75
    assert float(roads[0].find("./elevationProfile/elevation").get("b")) == pytest.approx(.001)
    # Independently reconstruct every written line endpoint; original XY remains exact.
    for road, lane in zip(roads, net["lanes"]):
        geoms = road.findall("./planView/geometry")
        for geom, p, q in zip(geoms, lane["centerline_local_m"], lane["centerline_local_m"][1:]):
            x, y, h, length = (float(geom.get(k)) for k in ("x", "y", "hdg", "length"))
            assert math.hypot(x - p[0], y - p[1]) < 1e-9
            assert math.hypot(x + length * math.cos(h) - q[0], y + length * math.sin(h) - q[1]) < 1e-9


def test_nonidentity_projection_and_reversible_origin():
    transform = Transformer.from_crs(5186, 5179, always_xy=True)
    x, y = transform.transform(200000, 600000)
    result = inspect(target_crs="EPSG:5179", local_origin=[x - 10, y - 20, 10])
    assert result["ready"], result["diagnostics"]
    assert result["normalized_network"]["lanes"][0]["centerline_local_m"][0] == pytest.approx([10, 20, 2])
    assert result["coordinate_transform"]["max_xy_roundtrip_error_source_units"] < 1e-6


def test_strict_korean_dbf_cp949():
    result = inspect(make_zip(encoding="cp949", cpg=False))
    assert result["ready"], result["diagnostics"]
    assert result["normalized_network"]["lanes"][0]["source_properties"]["RoadName"] == "합성 의미 검사용 도로"
    assert result["coordinate_transform"]["encodings"]["A2_LINK"] == "cp949"


@pytest.mark.parametrize("raw,options,code", [
    (b"not zip", {}, "invalid_zip"),
    (make_zip(prj=False), {}, "missing_crs"),
    (make_zip(), {"source_crs": "EPSG:5179"}, "crs_conflict"),
    (make_zip(), {"target_crs": "EPSG:4326"}, "metric_crs_required"),
    (make_zip(bounds=False), {}, "missing_boundary"),
    (make_zip(z=False), {}, "missing_elevation"),
    (make_zip(reverse=True), {}, "node_geometry_mismatch"),
    (make_zip(node_gap=.3), {}, "node_geometry_mismatch"),
    (make_zip(), {"selected_link_ids": ["NOPE"]}, "unknown_link"),
    (make_zip(), {"lane_width_overrides_m": {"L1": float("nan")}}, "invalid_options"),
    (make_zip(), {"lane_width_overrides_m": {"OUTSIDE": 3.5}}, "unknown_option_link"),
    (make_zip(extra={"../escape": b"x"}), {}, "unsafe_zip_member"),
    (make_zip(extra={"bomb": b"0" * 1_000_000}), {}, "zip_expansion_limit"),
    (make_zip(), {"allow_2d": "true"}, "invalid_options"),
    (make_zip(), {"encoding": "cp949"}, "encoding_conflict"),
    (make_zip(), {"unknown": True}, "invalid_options"),
])
def test_fail_closed(raw, options, code):
    result = inspect_ngii_zip(raw, options=options)
    assert result["ready"] is False
    assert result["xodr_text"] is None
    assert error_code(result) == code, result["diagnostics"]


def test_explicit_flat_and_width_authoring_is_not_measured():
    result = inspect(make_zip(bounds=False, z=False, prj=False), source_crs="EPSG:5186", allow_2d=True, lane_width_overrides_m={"L1": 3.5, "L2": 3.5})
    assert result["ready"], result["diagnostics"]
    assert {f["kind"] for f in result["inference_flags"]} == {"flat_elevation", "authored_width"}
    assert all(not f["measured"] for f in result["inference_flags"])


def test_explicit_source_selection_is_not_geometric_clipping():
    result = inspect(selected_link_ids=["L2"])
    assert result["ready"], result["diagnostics"]
    assert [l["source_id"] for l in result["normalized_network"]["lanes"]] == ["L2"]
    assert result["normalized_network"]["connections"] == []
    assert result["coordinate_transform"]["local_origin_target_m"] == [200200, 600000, 12.2]


def test_curve_retains_source_vertices_without_claiming_smoothness():
    links = [dict(id="CURVE", start="A", end="B", points=[(0, 0), (10, 1), (20, 4), (30, 9)])]
    result = inspect(make_zip(links=links, bounds=False), lane_width_overrides_m={"CURVE": 3.5})
    assert result["ready"], result["diagnostics"]
    assert result["normalized_network"]["geometry_policy"] == "piecewise_linear_source_vertices_no_smoothing"
    assert result["normalized_network"]["max_reference_line_source_error_m"] == 0


def test_branch_fails_until_explicit_connector_authoring():
    links = [dict(id="ENTRY", start="A", end="B", points=[(0, 0), (20, 0)]), dict(id="J1", start="B", end="C", points=[(20, 0), (30, 0)]), dict(id="J2", start="B", end="D", points=[(20, 0), (30, 10)]), dict(id="OUT1", start="C", end="E", points=[(30, 0), (50, 0)]), dict(id="OUT2", start="D", end="F", points=[(30, 10), (50, 10)])]
    raw = make_zip(links=links, bounds=False)
    widths = {v["id"]: 3.5 for v in links}
    rejected = inspect(raw, lane_width_overrides_m=widths)
    assert error_code(rejected) == "ambiguous_branch"
    assert rejected["normalized_network"] is not None
    accepted = inspect(raw, lane_width_overrides_m=widths, junction_links={"JUNCTION_AUTHORING": ["J1", "J2"]})
    assert accepted["ready"], accepted["diagnostics"]
    root = ET.fromstring(accepted["xodr_text"])
    assert len(root.findall("./junction/connection")) == 2
    assert all(x.attrib == {"from": "-1", "to": "-1"} for x in root.findall("./junction/connection/laneLink"))
    assert any(f["kind"] == "authored_junction" and not f["measured"] for f in accepted["inference_flags"])


def test_options_class_and_capability_contract():
    assert NgiiImportOptions.model_validate({}).model_dump()["allow_2d"] is False
    assert capabilities()["available"]
    assert len(capabilities()["converter"]["source_sha256"]) == 64


def test_width_change_between_source_vertices_is_not_missed():
    links = [dict(id="L1", start="A", end="B", points=[(0, 0), (200, 0)])]
    raw = make_zip(links=links, boundary_points=lambda link, side: [(0, side * 1.75), (100, side * 5), (200, side * 1.75)])
    result = inspect(raw)
    assert error_code(result) == "invalid_lane_width"


def test_variable_width_profile_written_as_source_derived_polynomials():
    links = [dict(id="L1", start="A", end="B", points=[(0, 0), (100, 0), (200, 0)])]
    raw = make_zip(links=links, boundary_points=lambda link, side: [(x, y + side * (1.75 + x / 400)) for x, y in link["points"]])
    result = inspect(raw)
    assert result["ready"], result["diagnostics"]
    profile = result["normalized_network"]["lanes"][0]["width_profile"]
    assert max(b["s_m"] - a["s_m"] for a, b in zip(profile, profile[1:])) <= 1 + 1e-10
    assert profile[50]["width_m"] == pytest.approx(3.75)
    root = ET.fromstring(result["xodr_text"])
    widths = root.findall("./road/lanes/laneSection/right/lane/width")
    segment = max((w for w in widths if float(w.get("sOffset")) <= 50.5), key=lambda w: float(w.get("sOffset")))
    ds = 50.5 - float(segment.get("sOffset"))
    assert float(segment.get("a")) + float(segment.get("b")) * ds == pytest.approx(3.7525)


def mutate_zip(raw, fn):
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        members = {i.filename: source.read(i) for i in source.infolist()}
    fn(members)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


@pytest.mark.parametrize("mutate,code", [
    (lambda members: members.pop("synthetic_semantic_fixture/A2_LINK.shx"), "missing_sidecar"),
    (lambda members: members.update({"synthetic_semantic_fixture/a2_link.SHP": members["synthetic_semantic_fixture/A2_LINK.shp"]}), "unsafe_zip_member"),
    (lambda members: members.update({"second/A2_LINK.shp": members["synthetic_semantic_fixture/A2_LINK.shp"]}), "ambiguous_layer"),
    (lambda members: members.update({"synthetic_semantic_fixture/A2_LINK.prj": b"not a CRS"}), "conversion_failed"),
])
def test_bad_package_components_do_not_produce_output(mutate, code):
    result = inspect(mutate_zip(make_zip(), mutate))
    assert error_code(result) == code
    assert result["xodr_text"] is None


def test_optional_dependency_failure_is_reported(monkeypatch):
    import axiom_geospatial.ngii as module
    original = module.importlib.util.find_spec
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None if name == "pyproj" else original(name))
    result = inspect()
    assert error_code(result) == "missing_optional_dependency"
    assert result["source_members"]
