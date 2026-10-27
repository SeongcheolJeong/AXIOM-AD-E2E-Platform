#!/usr/bin/env python3
"""Create a new private map revision from uniquely matched native stop geometry.

Unmatched/ambiguous stop waypoints are recorded, never assigned to a guessed lane.
Original upstream map and capture are preserved and hashed in the derived receipt.
"""
import argparse,hashlib,json,math
from pathlib import Path
import xml.etree.ElementTree as ET
from axiom_autoware.map_contract import LaneletMap
from axiom_autoware.contract import carla_to_ros_pose


def resample(points,count=30):
    lengths=[0.]
    for a,b in zip(points,points[1:]):lengths.append(lengths[-1]+math.hypot(b[0]-a[0],b[1]-a[1]))
    result=[]
    for i in range(count+1):
        distance=lengths[-1]*i/count
        j=next((k for k in range(len(lengths)-1) if distance<=lengths[k+1]),len(lengths)-2)
        ratio=(distance-lengths[j])/max(1e-9,lengths[j+1]-lengths[j])
        result.append(tuple(points[j][k]+ratio*(points[j+1][k]-points[j][k]) for k in range(3)))
    return result


def lane_match(point,yaw,lane):
    center=[tuple((l[i]+r[i])/2 for i in range(3)) for l,r in zip(resample(lane["left"]),resample(lane["right"]))]
    best=None
    for a,b in zip(center,center[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1];norm=dx*dx+dy*dy
        if norm<1e-8:continue
        t=((point["x"]-a[0])*dx+(point["y"]-a[1])*dy)/norm
        if t<-.01 or t>1.01:continue
        t=max(0.,min(1.,t));distance=math.hypot(point["x"]-a[0]-t*dx,point["y"]-a[1]-t*dy)
        angle=abs(math.atan2(math.sin(yaw-math.atan2(dy,dx)),math.cos(yaw-math.atan2(dy,dx))))
        if distance<=.75 and angle<=math.radians(15) and (best is None or distance<best[0]):best=(distance,angle)
    return best


def shared_stop_line(stops):
    """Preserve native endpoints for adjacent lanes controlled by one signal.

    A shared regulatory element is valid only for same-direction, contiguous
    stop segments. No bridging a median, crossing road or offset stop line.
    """
    if not 1 <= len(stops) <= 8:
        raise ValueError("a signal needs one to eight explicit lane stops")
    poses = [carla_to_ros_pose(stop["pose"]) for stop in stops]
    yaw = poses[0]["yaw_rad"]
    direction = (math.cos(yaw), math.sin(yaw))
    normal = (-direction[1], direction[0])
    segments = []
    longitudinal = []
    for stop, pose in zip(stops, poses):
        angle = abs(math.atan2(math.sin(pose["yaw_rad"] - yaw), math.cos(pose["yaw_rad"] - yaw)))
        if angle > math.radians(1):
            raise ValueError("shared signal stops are not same-direction")
        width = float(stop["width_m"])
        if not 0 < width <= 10:
            raise ValueError("invalid native lane width")
        local_normal = (-math.sin(pose["yaw_rad"]), math.cos(pose["yaw_rad"]))
        points = [(pose["x"] + sign * width / 2 * local_normal[0],
                   pose["y"] + sign * width / 2 * local_normal[1], pose["z"])
                  for sign in (-1, 1)]
        points.sort(key=lambda point: point[0] * normal[0] + point[1] * normal[1])
        interval = [point[0] * normal[0] + point[1] * normal[1] for point in points]
        segments.append((interval, points))
        longitudinal.append(pose["x"] * direction[0] + pose["y"] * direction[1])
    if max(longitudinal) - min(longitudinal) > 0.2:
        raise ValueError("shared signal stops have longitudinal offset above 0.2 m")
    segments.sort(key=lambda item: item[0][0])
    gaps = []
    for previous, current in zip(segments, segments[1:]):
        gap = current[0][0] - previous[0][1]
        if abs(gap) > 0.05:
            raise ValueError("shared signal lanes are not contiguous within 0.05 m")
        if abs(current[1][0][2] - previous[1][1][2]) > 0.1:
            raise ValueError("shared signal stops are at different elevations")
        gaps.append(gap)
    points = [point for _, segment in segments for point in segment]
    return points, {
        "lane_count": len(stops),
        "longitudinal_spread_m": max(longitudinal) - min(longitudinal),
        "boundary_lateral_gaps_m": gaps,
        "rule": "same native signal; heading <=1deg; longitudinal spread <=0.2m; adjacent boundary gap/overlap <=0.05m; elevation gap <=0.1m; original endpoints retained",
    }


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--source-map",type=Path,required=True);p.add_argument("--native-capture",type=Path,required=True);p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    source=args.source_map.read_bytes();capture_bytes=args.native_capture.read_bytes();capture=json.loads(capture_bytes)
    m=LaneletMap(args.source_map);root=ET.fromstring(source);relations={r.attrib["id"]:r for r in root.findall("relation")}
    if m.lights:raise ValueError("source already has traffic-light regulations; no duplicate augmentation")
    if args.output.resolve()==args.source_map.parent.resolve():raise ValueError("derived map must use a different directory")
    args.output.mkdir(parents=True,exist_ok=True)
    if (args.output/"lanelet2_map.osm").exists():raise ValueError("derived immutable revision already exists")
    next_id=max([int(x.attrib["id"]) for x in root if "id" in x.attrib]+[1_000_000])+1
    def identifier():
        nonlocal next_id
        result=str(next_id);next_id+=1;return result
    def node(x,y,z):
        id=identifier();n=ET.SubElement(root,"node",id=id,visible="true",version="1",lat=str(y/110574.),lon=str(x/111320.))
        for key,value in (("local_x",x),("local_y",y),("ele",z)):ET.SubElement(n,"tag",k=key,v=str(value))
        return id
    def way(points,kind):
        id=identifier();w=ET.SubElement(root,"way",id=id,visible="true",version="1")
        # Insert nodes before the way to retain regular OSM ordering after sort.
        for pt in points:ET.SubElement(w,"nd",ref=node(*pt))
        ET.SubElement(w,"tag",k="type",v=kind)
        if kind=="traffic_light":ET.SubElement(w,"tag",k="subtype",v="red_yellow_green")
        return id
    matches=[];unmatched=[];ambiguous=[];used_lane_stops=set();signal_groups=[]
    for signal in capture["signals"]:
        group=[];failed=False
        for stop in signal["stop_waypoints"]:
            pose=carla_to_ros_pose(stop["pose"])
            candidates=[(lane,lane_match(pose,pose["yaw_rad"],lane)) for lane in m.lanes]
            candidates=[item for item in candidates if item[1] is not None]
            record={"native_id":signal["id"],"opendrive_id":signal["opendrive_id"],"stop_waypoint":stop,"candidate_ids":[v[0]["id"] for v in candidates]}
            if len(candidates)!=1:
                (ambiguous if candidates else unmatched).append(record);failed=True;continue
            lane,score=candidates[0]
            key=(lane["id"],round(pose["x"],1),round(pose["y"],1))
            if key in used_lane_stops or any(item[3]==key for item in group):
                ambiguous.append(record|{"reason":"duplicate stop control"});failed=True;continue
            group.append((record,lane,score,key))
        if failed or not group:
            unmatched.extend(item[0]|{"reason":"incomplete signal group; no partial regulation emitted"} for item in group)
            if not signal["stop_waypoints"]:
                unmatched.append({"native_id":signal["id"],"reason":"native signal has no stop waypoints"})
            continue
        try:
            points,geometry=shared_stop_line([item[0]["stop_waypoint"] for item in group])
        except ValueError as error:
            ambiguous.extend(item[0]|{"reason":str(error)} for item in group)
            continue
        stopline=way(points,"stop_line")
        lightpose=carla_to_ros_pose(signal["pose"])
        # One physical signal may govern adjacent lanes. Its single shared
        # regulatory element retains actual native stop endpoints for all lanes.
        lightway=way([(lightpose["x"],lightpose["y"],lightpose["z"])],"traffic_light")
        rid=identifier();relation=ET.SubElement(root,"relation",id=rid,visible="true",version="1")
        ET.SubElement(relation,"member",type="way",ref=lightway,role="refers");ET.SubElement(relation,"member",type="way",ref=stopline,role="ref_line")
        ET.SubElement(relation,"tag",k="type",v="regulatory_element");ET.SubElement(relation,"tag",k="subtype",v="traffic_light")
        for record,lane,score,key in group:
            used_lane_stops.add(key)
            ET.SubElement(relations[lane["id"]],"member",type="relation",ref=rid,role="regulatory_element")
            matches.append(record|{"lanelet_id":lane["id"],"regulatory_id":int(rid),"lateral_error_m":score[0],"heading_error_rad":score[1],"signal_pose":signal["pose"]})
        signal_groups.append({"native_id":signal["id"],"opendrive_id":signal["opendrive_id"],"regulatory_id":int(rid),"lanelet_ids":[item[1]["id"] for item in group],"shared_stop_line":geometry})
    root[:]=sorted(root,key=lambda e:{"node":0,"way":1,"relation":2}.get(e.tag,3))
    result=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    (args.output/"lanelet2_map.osm").write_bytes(result);(args.output/"map_projector_info.yaml").write_text("projector_type: Local\n")
    receipt={"schema_version":"axiom-native-signal-map/2","status":"complete_unique_geometry_mapping" if not unmatched and not ambiguous else "partial_mapping_not_signal_accepted",
             "derivation_script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"source_map_sha256":hashlib.sha256(source).hexdigest(),"native_capture_sha256":hashlib.sha256(capture_bytes).hexdigest(),"native_opendrive_sha256":capture["opendrive_sha256"],
             "derived_map_sha256":hashlib.sha256(result).hexdigest(),"source_rights":"unconfirmed; inherited private evaluation restriction; do not redistribute",
             "rule":"native get_stop_waypoints to unique same-direction lanelet centerline: lateral<=0.75m, heading<=15deg; no missing/ambiguous assignment",
             "signal_groups":signal_groups,"accepted":matches,"unmatched":unmatched,"ambiguous":ambiguous,"fidelity":"simulator map/traffic-control geometry correspondence; not physical/sensor validation"}
    (args.output/"signal-mapping-receipt.json").write_text(json.dumps(receipt,indent=2));print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in receipt.items()}))

if __name__=="__main__":main()
