"""OpenDRIVE writer over retained A2 polylines, using pinned pyoscx objects."""
import math
import json
from importlib.metadata import version
from xml.etree import ElementTree as ET

from .ngii import ImportFailure


def write_opendrive(network: dict, coordinate_transform: dict, name: str) -> str:
    if version("scenariogeneration") != "0.16.7":
        raise ImportFailure("compiler_version", "Requires scenariogeneration==0.16.7")
    from scenariogeneration import xodr
    lanes = {v["source_id"]: v for v in network["lanes"]}
    road_ids = {key: str(i + 1) for i, key in enumerate(sorted(lanes))}
    junction_ids = {j["id"]: str(i + 1) for i, j in enumerate(network["junctions"])}
    odr = xodr.OpenDrive(name, revMajor="1", revMinor="6")
    roads, driving_lanes = {}, {}
    for key, data in lanes.items():
        plan = xodr.PlanView()
        points, stations = data["centerline_local_m"], data["stations_m"]
        for a, b, s, end_s in zip(points, points[1:], stations, stations[1:]):
            plan.add_fixed_geometry(xodr.Line(end_s - s), a[0], a[1], math.atan2(b[1] - a[1], b[0] - a[0]), s=s)
        center = xodr.Lane(xodr.LaneType.none)
        first = data["width_profile"][0]
        first_next = data["width_profile"][1]
        initial_slope = (first_next["width_m"] - first["width_m"]) / (first_next["s_m"] - first["s_m"])
        driving = xodr.Lane(xodr.LaneType.driving, a=first["width_m"], b=initial_slope)
        profile = data["width_profile"]
        for index in range(1, len(profile) - 1):
            current, following = profile[index:index + 2]
            slope = (following["width_m"] - current["width_m"]) / (following["s_m"] - current["s_m"])
            driving.add_lane_width(a=current["width_m"], b=slope, soffset=current["s_m"])
        section = xodr.LaneSection(0, center)
        section.add_right_lane(driving)
        lane_container = xodr.Lanes()
        lane_container.add_lanesection(section)
        for current, following in zip(profile, profile[1:]):
            slope = (following["left_m"] - current["left_m"]) / (following["s_m"] - current["s_m"])
            lane_container.add_laneoffset(xodr.LaneOffset(s=current["s_m"], a=current["left_m"], b=slope))
        road = xodr.Road(road_ids[key], plan, lane_container, road_type=junction_ids[data["junction_id"]] if data["junction_id"] else -1, name=key)
        for a, b, s, end_s in zip(points, points[1:], stations, stations[1:]):
            road.add_elevation(s, a[2], (b[2] - a[2]) / (end_s - s), 0, 0)
        roads[key], driving_lanes[key] = road, driving
        odr.add_road(road)
    incoming = {key: [] for key in lanes}
    outgoing = {key: [] for key in lanes}
    for connection in network["connections"]:
        a, b = connection["from_lane"], connection["to_lane"]
        outgoing[a].append(b)
        incoming[b].append(a)
        pa, pb = lanes[a]["centerline_local_m"][-1], lanes[b]["centerline_local_m"][0]
        if math.dist(pa, pb) > 0.2:
            raise ImportFailure("connection_geometry_gap", "Shared-node lane endpoints exceed 0.2 m in 3D; no snap applied", source_id=a, target_id=b)
    for key, data in lanes.items():
        for end, neighbors, contact in (("predecessor", incoming[key], xodr.ContactPoint.end), ("successor", outgoing[key], xodr.ContactPoint.start)):
            if not neighbors:
                continue
            other_junctions = {lanes[n]["junction_id"] for n in neighbors}
            own_junction = data["junction_id"]
            if own_junction:
                if len(neighbors) != 1 or any(lanes[n]["junction_id"] for n in neighbors):
                    raise ImportFailure("unsupported_junction_topology", "A junction connector must have exactly one ordinary predecessor and successor", source_id=key)
                getattr(roads[key], "add_" + end)(xodr.ElementType.road, road_ids[neighbors[0]], contact)
                driving_lanes[key].add_link(end, -1)
            elif len(other_junctions) == 1 and None not in other_junctions:
                getattr(roads[key], "add_" + end)(xodr.ElementType.junction, junction_ids[next(iter(other_junctions))])
            elif len(neighbors) == 1 and other_junctions == {None}:
                getattr(roads[key], "add_" + end)(xodr.ElementType.road, road_ids[neighbors[0]], contact)
                driving_lanes[key].add_link(end, -1)
            else:
                raise ImportFailure("ambiguous_branch", "Shared-node branch/merge requires explicit junction connector links; connectivity is not guessed", source_id=key)
    for junction in network["junctions"]:
        obj = xodr.Junction(junction["id"], junction_ids[junction["id"]])
        for index, key in enumerate(junction["connector_source_ids"]):
            if len(incoming[key]) != 1 or len(outgoing[key]) != 1:
                raise ImportFailure("incomplete_junction_connector", "Each explicit connector needs one selected entry and exit", source_id=key)
            conn = xodr.Connection(road_ids[incoming[key][0]], road_ids[key], xodr.ContactPoint.start, id=index)
            conn.add_lanelink(-1, -1)
            obj.add_connection(conn)
        odr.add_junction(obj)
    root = odr.get_element()
    header = root.find("header")
    header.set("date", "1970-01-01T00:00:00Z")
    # Standard header offset transforms geometry, so inserting the national-grid
    # origin there would destroy the intentionally local driving coordinates.
    # Nor may the absolute projection alone be called this local frame's GPS
    # projection. Preserve the reversible transform as explicit metadata; native
    # GNSS/geolocation is unqualified until a consumer applies that transform.
    ET.SubElement(root, "userData", code="axiom-local-coordinate-transform", value=json.dumps(coordinate_transform, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    ET.SubElement(root, "userData", code="axiom-ngii-source-sha256", value=network["source_sha256"])
    for element in root.findall("road"):
        ET.SubElement(element, "userData", code="ngii-A2-source-id", value=element.get("name"))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode").encode("ascii", "xmlcharrefreplace").decode("ascii")
