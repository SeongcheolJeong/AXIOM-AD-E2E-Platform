"""Bounded, fail-closed A1/A2/B2 reader; original ZIP retention belongs upstream.

No nearest-neighbour road connectivity, invented lane widths, smoothing, traffic
rules, or synthetic measurements. ``ready`` means conversion checks passed,
never CARLA, physical, data-rights, or release acceptance.
"""
from __future__ import annotations

import codecs
import datetime
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import stat
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = "0.1.0"
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_FEATURES = 100_000
MAX_VERTICES = 200_000
MAX_LANES = 256
MAX_LENGTH_M = 5_000
MAX_EXTENT_M = 5_000
DEPENDENCIES = {"pyproj": "pyproj", "pyshp": "shapefile", "shapely": "shapely", "scenariogeneration": "scenariogeneration"}


class ImportFailure(ValueError):
    def __init__(self, code: str, message: str, **context: Any):
        super().__init__(message)
        self.diagnostic = {"severity": "error", "code": code, "message": message, **context}


@dataclass
class NgiiImportOptions:
    selected_link_ids: list[str] | None = None
    source_crs: str | None = None
    target_crs: str | None = None
    local_origin: list[float] | None = None
    encoding: str | None = None
    allow_2d: bool = False
    lane_width_overrides_m: dict[str, float] = field(default_factory=dict)
    junction_links: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def model_validate(cls, value: Any) -> "NgiiImportOptions":
        if value is None:
            return cls()
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ImportFailure("invalid_options", "Unknown NGII option or options is not an object")
        try:
            result = cls(**value)
            for name in ("source_crs", "target_crs", "encoding"):
                v = getattr(result, name)
                if v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 16_384):
                    raise ValueError(name)
            if type(result.allow_2d) is not bool:
                raise ValueError("allow_2d")
            if result.selected_link_ids is not None and (not isinstance(result.selected_link_ids, list) or not result.selected_link_ids or len(result.selected_link_ids) > MAX_LANES or any(not isinstance(v, str) or not v for v in result.selected_link_ids) or len(set(result.selected_link_ids)) != len(result.selected_link_ids)):
                raise ValueError("selected_link_ids")
            if result.local_origin is not None and (not isinstance(result.local_origin, list) or len(result.local_origin) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in result.local_origin)):
                raise ValueError("local_origin")
            if not isinstance(result.lane_width_overrides_m, dict) or len(result.lane_width_overrides_m) > MAX_LANES:
                raise ValueError("lane_width_overrides_m")
            for key, width in result.lane_width_overrides_m.items():
                if not isinstance(key, str) or type(width) not in (int, float) or not math.isfinite(width) or not 1.5 <= width <= 8:
                    raise ValueError("lane_width_overrides_m: widths must be finite 1.5–8 m")
            if not isinstance(result.junction_links, dict) or len(result.junction_links) > 64:
                raise ValueError("junction_links")
            connectors = []
            for key, links in result.junction_links.items():
                if not isinstance(key, str) or not key or not isinstance(links, list) or not links or any(not isinstance(v, str) or not v for v in links):
                    raise ValueError("junction_links")
                connectors.extend(links)
            if len(connectors) > MAX_LANES or len(set(connectors)) != len(connectors):
                raise ValueError("junction_links contains duplicate connectors")
            return result
        except (ValueError, TypeError) as exc:
            raise ImportFailure("invalid_options", str(exc)) from exc

    def model_dump(self) -> dict:
        return asdict(self)


def _source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


_LOADED_SOURCE_SHA256 = _source_hash()


def capabilities() -> dict:
    dependencies = {}
    for distribution, module in DEPENDENCIES.items():
        available = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if available else None
        except importlib.metadata.PackageNotFoundError:
            version = None
        dependencies[distribution] = {"available": available, "version": version}
    return {"available": all(d["available"] for d in dependencies.values()), "converter": {"id": "axiom.ngii-shp.opendrive", "version": VERSION, "source_sha256": _LOADED_SOURCE_SHA256}, "dependencies": dependencies, "limits": {"zip_bytes": MAX_ZIP_BYTES, "expanded_bytes": MAX_EXPANDED_BYTES, "selected_lanes": MAX_LANES, "total_length_m": MAX_LENGTH_M}, "native_qualified": False}


def _key(value: str) -> str:
    return value.replace("_", "").upper()


def _prop(properties: dict, *names: str, required=False) -> str:
    normalized = {_key(k): v for k, v in properties.items()}
    matches = {str(normalized[_key(name)]).strip() for name in names if normalized.get(_key(name)) is not None and str(normalized[_key(name)]).strip()}
    if len(matches) > 1:
        raise ImportFailure("conflicting_field_aliases", f"Conflicting values for aliases of {names[0]}")
    for name in names:
        value = normalized.get(_key(name))
        if value is not None and str(value).strip():
            return str(value).strip()
    if required:
        raise ImportFailure("missing_field", f"Required field missing: {names[0]}")
    return ""


def _archive(raw: bytes) -> tuple[dict, list]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ZIP_BYTES:
        raise ImportFailure("zip_size", "ZIP must be nonempty bytes no larger than 32 MiB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
        if len(infos) > 512 or sum(v.file_size for v in infos) > MAX_EXPANDED_BYTES:
            raise ImportFailure("zip_expansion_limit", "ZIP contains too many members or exceeds 128 MiB expanded")
        files, manifest, names = {}, [], set()
        for item in infos:
            path = PurePosixPath(item.filename)
            key = item.filename.casefold()
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or key in names:
                raise ImportFailure("unsafe_zip_member", "Unsafe or duplicate ZIP member", member=item.filename)
            names.add(key)
            if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                raise ImportFailure("unsafe_zip_member", "Symlink and encrypted ZIP members are unsupported", member=item.filename)
            if item.file_size > 32 * 1024 * 1024 or item.file_size / max(1, item.compress_size) > 200:
                raise ImportFailure("zip_expansion_limit", "ZIP member size/compression ratio exceeds bound", member=item.filename)
            if item.is_dir():
                continue
            payload = archive.read(item)
            files[item.filename] = payload
            manifest.append({"path": item.filename, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        return files, sorted(manifest, key=lambda x: x["path"])
    except ImportFailure:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
        raise ImportFailure("invalid_zip", f"ZIP could not be safely read: {exc}") from exc


def _layers(files: dict) -> dict:
    layers = {}
    for name in files:
        if PurePosixPath(name).suffix.lower() != ".shp":
            continue
        stem = PurePosixPath(name).stem.upper()
        kind = next((v for v in ("A1_NODE", "A2_LINK", "B2_SURFACELINEMARK") if stem == v), None)
        if kind is None:
            continue
        if kind in layers:
            raise ImportFailure("ambiguous_layer", f"More than one {kind} layer; supply a single coherent tile/subset")
        base = name[:-4].casefold()
        members = {PurePosixPath(n).suffix.lower(): n for n in files if n[:-4].casefold() == base}
        for ext in (".shp", ".shx", ".dbf"):
            if ext not in members:
                raise ImportFailure("missing_sidecar", f"{kind} requires {ext}")
        layers[kind] = members
    for kind in ("A1_NODE", "A2_LINK"):
        if kind not in layers:
            raise ImportFailure("missing_layer", f"ZIP requires {kind}.shp/.shx/.dbf")
    return layers


def _encoding(raw: str) -> str:
    value = raw.strip().strip('"').lower()
    value = {"949": "cp949", "65001": "utf-8", "utf8": "utf-8"}.get(value, value)
    try:
        return codecs.lookup(value).name
    except LookupError as exc:
        raise ImportFailure("unsupported_encoding", f"Unknown DBF encoding: {raw}") from exc


def _read_layer(kind, members, files, options) -> tuple[list, str]:
    import shapefile
    declared = _encoding(files[members[".cpg"]].decode("ascii", "strict")) if ".cpg" in members else None
    requested = _encoding(options.encoding) if options.encoding else None
    if declared and requested and declared != requested:
        raise ImportFailure("encoding_conflict", f"Explicit encoding conflicts with {kind}.cpg")
    encodings = [declared or requested] if declared or requested else ["utf-8", "cp949"]
    last_error = None
    for encoding in encodings:
        try:
            reader = shapefile.Reader(shp=io.BytesIO(files[members[".shp"]]), shx=io.BytesIO(files[members[".shx"]]), dbf=io.BytesIO(files[members[".dbf"]]), encoding=encoding, encodingErrors="strict")
            if reader.numRecords > MAX_FEATURES:
                raise ImportFailure("feature_limit", f"{kind} exceeds {MAX_FEATURES} records")
            field_names = [f[0] for f in reader.fields[1:]]
            if len({_key(k) for k in field_names}) != len(field_names):
                raise ImportFailure("duplicate_field", f"{kind} contains ambiguous duplicate DBF field names")
            records, seen, vertices = [], set(), 0
            for shape_record in reader.iterShapeRecords():
                shape = shape_record.shape
                properties = {k: v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v for k, v in shape_record.record.as_dict().items()}
                if any(isinstance(v, float) and not math.isfinite(v) for v in properties.values()):
                    raise ImportFailure("invalid_attribute", "Non-finite numeric DBF field is unsupported")
                identity = _prop(properties, "ID", required=True)
                if identity in seen:
                    raise ImportFailure("duplicate_source_id", f"Duplicate {kind} source ID", source_id=identity)
                seen.add(identity)
                is_point = kind == "A1_NODE"
                allowed = (shapefile.POINT, shapefile.POINTZ) if is_point else (shapefile.POLYLINE, shapefile.POLYLINEZ)
                if shape.shapeType not in allowed or (not is_point and len(shape.parts) != 1):
                    raise ImportFailure("unsupported_geometry", "Only Point/PointZ nodes and single-part PolyLine/PolyLineZ lanes/boundaries are supported", source_id=identity)
                points = [[float(p[0]), float(p[1]), float(shape.z[i]) if hasattr(shape, "z") else None] for i, p in enumerate(shape.points)]
                vertices += len(points)
                if vertices > MAX_VERTICES:
                    raise ImportFailure("vertex_limit", f"{kind} exceeds {MAX_VERTICES} vertices")
                if len(points) < (1 if is_point else 2) or any(not math.isfinite(v) or abs(v) > 1e10 for p in points for v in p if v is not None):
                    raise ImportFailure("invalid_geometry", "Empty or non-finite/unbounded coordinates", source_id=identity)
                records.append({"source_id": identity, "source_properties": properties, "source_coordinates": points})
            if len(records) != reader.numRecords or reader.numShapes != reader.numRecords:
                raise ImportFailure("record_count_mismatch", f"{kind} SHP/SHX/DBF feature counts or deleted rows disagree")
            reader.close()
            return records, encoding
        except UnicodeError as exc:
            last_error = exc
        except ImportFailure:
            raise
        except Exception as exc:
            raise ImportFailure("invalid_shapefile", f"{kind}: {exc}") from exc
    raise ImportFailure("dbf_decode_failed", f"{kind}: strict DBF decoding failed ({last_error})")


def _crs(layers, files, options):
    from pyproj import CRS, Transformer
    from pyproj import network
    if network.is_network_enabled():
        raise ImportFailure("proj_network_enabled", "Use an offline PROJ configuration; implicit grid downloads are forbidden")
    declarations = []
    for kind, members in layers.items():
        if ".prj" not in members:
            if options.source_crs is None:
                raise ImportFailure("missing_crs", f"{kind}.prj missing; explicit source_crs is required")
            continue
        declarations.append(CRS.from_wkt(files[members[".prj"]].decode("utf-8-sig")))
    source = CRS.from_user_input(options.source_crs) if options.source_crs else declarations[0]
    if any(not source.equals(v, ignore_axis_order=True) for v in declarations):
        raise ImportFailure("crs_conflict", "Layers/explicit source CRS disagree; do not silently reinterpret coordinates")
    target = CRS.from_user_input(options.target_crs) if options.target_crs else source
    if not target.is_projected or len(target.axis_info) < 2 or any(abs(a.unit_conversion_factor - 1) > 1e-12 for a in target.axis_info[:2]):
        raise ImportFailure("metric_crs_required", "Choose an explicit projected target_crs whose XY units are metres")
    transformer = Transformer.from_crs(source, target, always_xy=True, allow_ballpark=False, only_best=True)
    inverse = Transformer.from_crs(target, source, always_xy=True, allow_ballpark=False, only_best=True)
    return source, target, transformer, inverse


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _stations(points):
    result = [0.0]
    for a, b in zip(points, points[1:]):
        length = _dist(a, b)
        if length < 0.01:
            raise ImportFailure("degenerate_segment", "Consecutive XY vertices must be at least 1 cm apart; no silent deduplication")
        result.append(result[-1] + length)
    return result


def _widths(lane, boundaries, options, flags):
    from shapely.geometry import LineString, Point
    identity, points = lane["source_id"], lane["centerline_local_m"]
    override = options.lane_width_overrides_m.get(identity)
    if override is not None:
        flags.append({"kind": "authored_width", "source_id": identity, "width_m": override, "measured": False})
        return [{"s_m": s, "left_m": override / 2, "right_m": override / 2, "width_m": override, "center_shift_m": 0.0} for s in lane["stations_m"]], {"left": [], "right": []}, "authored_override"
    sides = {"left": [], "right": []}
    for boundary in boundaries:
        props = boundary["source_properties"]
        # R_LinkID is the lane on the marking's right => its LEFT boundary.
        for side, fieldname in (("left", "R_LinkID"), ("right", "L_LinkID")):
            if _prop(props, fieldname) == identity:
                sides[side].append(boundary)
    if not all(sides.values()):
        raise ImportFailure("missing_boundary", "Both explicitly linked B2 lane boundaries are required, or an authored width override", source_id=identity)
    lines = {side: [LineString(b["local_coordinates_m"]) for b in values] for side, values in sides.items()}
    profile, samples, endpoint_projections = [], [], []
    # Check width along each segment, not only at widely separated source vertices.
    # The source polyline itself is never resampled or replaced.
    for index, (before, after) in enumerate(zip(points, points[1:])):
        length = lane["stations_m"][index + 1] - lane["stations_m"][index]
        steps = max(1, math.ceil(length))
        for step in range(steps):
            fraction = step / steps
            samples.append(([before[axis] + (after[axis] - before[axis]) * fraction for axis in range(3)], lane["stations_m"][index] + length * fraction, before, after))
    samples.append((points[-1], lane["stations_m"][-1], points[-2], points[-1]))
    profile = []
    for point, station, before, after in samples:
        norm = _dist(before, after)
        if norm < 0.01:
            raise ImportFailure("invalid_tangent", "A U-turn/cusp cannot be represented by this bounded writer", source_id=identity)
        nx, ny = -(after[1] - before[1]) / norm, (after[0] - before[0]) / norm
        distances = {}
        for side, sign in (("left", 1), ("right", -1)):
            ray = LineString([(point[0], point[1]), (point[0] + nx * sign * 8, point[1] + ny * sign * 8)])
            hits = []
            for line in lines[side]:
                intersection = ray.intersection(line)
                if intersection.is_empty:
                    # Projection changes can place the corresponding boundary
                    # endpoint micrometres outside the perpendicular ray. Only
                    # its normal component is used; retain this bounded derived
                    # estimate separately, never move the source boundary.
                    from shapely.ops import nearest_points
                    closest = nearest_points(Point(point[:2]), line)[1]
                    dx, dy = closest.x - point[0], closest.y - point[1]
                    tangent_error = abs(dx * (after[0] - before[0]) / norm + dy * (after[1] - before[1]) / norm)
                    normal_distance = (dx * nx + dy * ny) * sign
                    at_endpoint = min(closest.distance(Point(line.coords[0])), closest.distance(Point(line.coords[-1]))) < 1e-8
                    if at_endpoint and tangent_error <= 0.02 and 0.25 <= normal_distance <= 8:
                        hits.append(normal_distance)
                        endpoint_projections.append({"s_m": station, "side": side, "tangential_gap_m": tangent_error})
                    continue
                if intersection.geom_type == "Point":
                    hits.append(Point(point[:2]).distance(intersection))
                elif intersection.geom_type == "MultiPoint":
                    hits.extend(Point(point[:2]).distance(p) for p in intersection.geoms)
                else:
                    raise ImportFailure("ambiguous_boundary", "Boundary overlaps lane cross-section", source_id=identity)
            hits = sorted(set(round(v, 8) for v in hits))
            if len(hits) != 1:
                raise ImportFailure("boundary_coverage", f"Expected one {side} boundary intersection at source station {station:.3f} m", source_id=identity)
            distances[side] = hits[0]
        width = distances["left"] + distances["right"]
        shift = (distances["left"] - distances["right"]) / 2
        if not 1.5 <= width <= 8 or min(distances.values()) < 0.25 or abs(shift) > 0.5:
            raise ImportFailure("invalid_lane_width", "Boundary-derived width must be 1.5–8 m and lane centre displacement <=0.5 m", source_id=identity)
        profile.append({"s_m": station, "left_m": distances["left"], "right_m": distances["right"], "width_m": width, "center_shift_m": shift})
    if endpoint_projections:
        flags.append({"kind": "boundary_endpoint_projection", "source_id": identity, "max_tangential_gap_m": max(v["tangential_gap_m"] for v in endpoint_projections), "samples": endpoint_projections, "measured": False})
    return profile, {side: [b["source_id"] for b in values] for side, values in sides.items()}, "derived_from_B2_cross_sections"


def inspect_ngii_zip(raw: bytes, *, source_name: str = "NGII source", options: dict | NgiiImportOptions | None = None) -> dict:
    """Return JSON-safe evidence even on expected failures; never write or run engines."""
    result = {"schema_version": "ngii-conversion-report/1", "source_name": source_name, "source_sha256": hashlib.sha256(raw).hexdigest() if isinstance(raw, bytes) else None, "ready": False, "native_qualified": False, "normalized_network": None, "xodr_text": None, "xodr_sha256": None, "diagnostics": [], "inference_flags": [], "coordinate_transform": None, "converter": capabilities()["converter"], "fidelity": {"kind": "source_derived_road_geometry", "physical_validation": "not_established", "native_validation": "not_evaluated", "traffic_rules": "not_imported", "assets": "road_mesh_only", "georeferencing": "reversible_metadata_only; native_GNSS_not_qualified"}}
    try:
        opt = NgiiImportOptions.model_validate(options)
        result["options"] = opt.model_dump()
        files, result["source_members"] = _archive(raw)
        layers = _layers(files)
        dependency_status = capabilities()
        result["dependencies"] = dependency_status["dependencies"]
        if not dependency_status["available"]:
            raise ImportFailure("missing_optional_dependency", "Install the optional axiom-geospatial package to enable NGII conversion")
        records, encodings = {}, {}
        for kind, members in layers.items():
            records[kind], encodings[kind] = _read_layer(kind, members, files, opt)
        result["inventory"] = {"layers": {kind: {"feature_count": len(rows), "fields": sorted(rows[0]["source_properties"]) if rows else []} for kind, rows in records.items()}, "link_ids": [v["source_id"] for v in records["A2_LINK"][:2000]], "link_ids_truncated": len(records["A2_LINK"]) > 2000}
        source, target, transformer, inverse = _crs(layers, files, opt)
        link_records = records["A2_LINK"]
        if opt.selected_link_ids:
            requested = set(opt.selected_link_ids)
            missing = requested - {v["source_id"] for v in link_records}
            if missing:
                raise ImportFailure("unknown_link", "Selected source IDs not found", source_ids=sorted(missing))
            link_records = [v for v in link_records if v["source_id"] in requested]
        link_records = sorted(link_records, key=lambda v: v["source_id"])
        if not link_records or len(link_records) > MAX_LANES:
            raise ImportFailure("selection_limit", "Select between 1 and 256 source links")
        selected = {v["source_id"] for v in link_records}
        if set(opt.lane_width_overrides_m) - selected or any(set(ids) - selected for ids in opt.junction_links.values()):
            raise ImportFailure("unknown_option_link", "Width/junction options refer to unselected links")
        source_nodes = {v["source_id"]: v for v in records["A1_NODE"]}
        nodes_needed = set()
        for link in link_records:
            link["from_node"] = _prop(link["source_properties"], "FromNodeID", "F_NODE", "F_NODEID", required=True)
            link["to_node"] = _prop(link["source_properties"], "ToNodeID", "T_NODE", "T_NODEID", required=True)
            nodes_needed.update((link["from_node"], link["to_node"]))
        if nodes_needed - source_nodes.keys():
            raise ImportFailure("dangling_node", "A2 references missing A1 nodes", source_ids=sorted(nodes_needed - source_nodes.keys()))
        nodes = [source_nodes[k] for k in sorted(nodes_needed)]
        boundaries = [b for b in records.get("B2_SURFACELINEMARK", []) if _prop(b["source_properties"], "R_LinkID") in selected or _prop(b["source_properties"], "L_LinkID") in selected]
        origin_point = link_records[0]["source_coordinates"][0]
        ox, oy = transformer.transform(*origin_point[:2], errcheck=True)
        origin = opt.local_origin or [ox, oy, origin_point[2] or 0.0]
        max_roundtrip = 0.0
        for record in nodes + link_records + boundaries:
            local = []
            for x, y, z in record["source_coordinates"]:
                if z is None:
                    if not opt.allow_2d:
                        raise ImportFailure("missing_elevation", "2D source has no measured height; allow_2d must explicitly authorize flat z=0", source_id=record["source_id"])
                    z = 0.0
                tx, ty = transformer.transform(x, y, errcheck=True)
                rx, ry = inverse.transform(tx, ty, errcheck=True)
                max_roundtrip = max(max_roundtrip, math.hypot(rx - x, ry - y))
                point = [tx - origin[0], ty - origin[1], z - origin[2]]
                if any(not math.isfinite(v) for v in point):
                    raise ImportFailure("invalid_transform", "Non-finite coordinate transform")
                local.append(point)
            record["local_coordinates_m"] = local
        if any(p[2] is None for r in nodes + link_records + boundaries for p in r["source_coordinates"]):
            result["inference_flags"].append({"kind": "flat_elevation", "value_m": 0.0, "measured": False})
        result["coordinate_transform"] = {"source_crs_wkt": source.to_wkt(), "target_crs_wkt": target.to_wkt(), "source_authority": source.to_authority(), "target_authority": target.to_authority(), "always_xy": True, "axis": "projected_easting_northing_source_height", "local_origin_target_m": origin, "operation": transformer.description, "definition": transformer.definition, "declared_accuracy_m": transformer.accuracy, "max_xy_roundtrip_error_source_units": max_roundtrip, "vertical_transform": "none; source Z retained, vertical datum not transformed", "vertical_units": "NGII source Z in metres assumed; vertical datum not verified", "encodings": encodings, "proj_network_enabled": False}
        all_points = [p for r in link_records for p in r["local_coordinates_m"]]
        extent = [min(p[i] for p in all_points) for i in range(2)] + [max(p[i] for p in all_points) for i in range(2)]
        if max(extent[2] - extent[0], extent[3] - extent[1]) > MAX_EXTENT_M or max(abs(v) for v in extent) > 10_000:
            raise ImportFailure("extent_limit", "Selected road footprint exceeds 5 km or local origin is more than 10 km away")
        lane_by_id, junction_lookup = {}, {}
        for jid, ids in sorted(opt.junction_links.items()):
            result["inference_flags"].append({"kind": "authored_junction", "junction_id": jid, "connector_source_ids": ids, "measured": False})
            junction_lookup.update({identity: jid for identity in ids})
        selected_length = 0.0
        for index, link in enumerate(link_records):
            link["opendrive_road_id"] = str(index + 1)
            points = link.pop("local_coordinates_m")
            link["centerline_local_m"] = points
            link["stations_m"] = _stations(points)
            link["length_m"] = link["stations_m"][-1]
            selected_length += link["length_m"]
            if selected_length > MAX_LENGTH_M:
                raise ImportFailure("length_limit", "Selected lane lengths exceed 5 km; choose a smaller explicit subset")
            for endpoint, node_id in ((points[0], link["from_node"]), (points[-1], link["to_node"])):
                if _dist(endpoint, source_nodes[node_id]["local_coordinates_m"][0]) > 0.2:
                    raise ImportFailure("node_geometry_mismatch", "A2 order/endpoints do not match F/T A1 nodes within 0.2 m; no automatic reversal/snap", source_id=link["source_id"])
            link["direction"] = "source_from_node_to_node"
            link["width_profile"], link["boundary_ids"], link["width_provenance"] = _widths(link, boundaries, opt, result["inference_flags"])
            link["junction_id"] = junction_lookup.get(link["source_id"])
            lane_by_id[link["source_id"]] = link
        total_length = sum(l["length_m"] for l in link_records)
        if total_length > MAX_LENGTH_M:
            raise ImportFailure("length_limit", "Selected lane lengths exceed 5 km; choose a smaller explicit subset")
        connections = [{"from_lane": a["source_id"], "to_lane": b["source_id"], "source_node_id": a["to_node"], "provenance": "exact_shared_A1_node_id"} for a in link_records for b in link_records if a is not b and a["to_node"] == b["from_node"]]
        network = {"schema_version": "ngii-lane-network/1", "source_sha256": result["source_sha256"], "nodes": nodes, "lanes": link_records, "boundaries": boundaries, "connections": connections, "junctions": [{"id": k, "connector_source_ids": v, "provenance": "authored_option"} for k, v in sorted(opt.junction_links.items())], "extent_local_m": extent, "total_lane_length_m": total_length, "road_grouping": "one_directional_A2_link_per_OpenDRIVE_road", "width_sampling_max_step_m": 1.0, "geometry_policy": "piecewise_linear_source_vertices_no_smoothing", "max_reference_line_source_error_m": 0.0, "max_lane_center_source_offset_m": max(abs(w["center_shift_m"]) for l in link_records for w in l["width_profile"])}
        result["normalized_network"] = network
        from .writer import write_opendrive
        result["xodr_text"] = write_opendrive(network, result["coordinate_transform"], source_name)
        result["xodr_sha256"] = hashlib.sha256(result["xodr_text"].encode()).hexdigest()
        result["normalized_network_sha256"] = hashlib.sha256(json.dumps(network, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result["ready"] = True
        result["diagnostics"].append({"severity": "warning", "code": "bounded_road_mesh_only", "message": "Conversion passed for retained polylines and explicit node topology. Lane changes, traffic controls, markings semantics, buildings, continuous curvature and CARLA driving are not validated/imported."})
        if not opt.selected_link_ids:
            result["diagnostics"].append({"severity": "info", "code": "all_source_links_selected", "message": "Every A2 link in this ZIP was selected; no geographic clipping or invented continuation was applied."})
    except ImportFailure as exc:
        result["diagnostics"].append(exc.diagnostic)
    except Exception as exc:
        result["diagnostics"].append({"severity": "error", "code": "conversion_failed", "message": f"{type(exc).__name__}: {str(exc)[:500]}"})
    if not result["ready"]:
        result["xodr_text"] = None
        result["xodr_sha256"] = None
    return result
