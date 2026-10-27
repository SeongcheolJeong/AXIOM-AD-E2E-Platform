#!/usr/bin/env python3
"""Read-only native traffic light/stop waypoint geometry. Never loads/ticks a map."""
import argparse,hashlib,json,math
from pathlib import Path

def pose(transform):
    return {"x":transform.location.x,"y":transform.location.y,"z":transform.location.z,"yaw_rad":math.radians(transform.rotation.yaw)}

def main():
    import carla
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--port",type=int,required=True);p.add_argument("--map",required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    c=carla.Client("127.0.0.1",a.port);c.set_timeout(10);w=c.get_world();m=w.get_map()
    if m.name.split("/")[-1]!=a.map:raise ValueError("native map mismatch")
    signals=[]
    for light in w.get_actors().filter("traffic.traffic_light"):
        stops=[]
        for wp in light.get_stop_waypoints():
            stops.append({"pose":pose(wp.transform),"width_m":wp.lane_width,"road_id":wp.road_id,"lane_id":wp.lane_id,"s":wp.s})
        signals.append({"id":str(light.id),"opendrive_id":light.get_opendrive_id(),"pose":pose(light.get_transform()),"stop_waypoints":stops})
    result={"schema_version":"carla-signal-geometry/1","map":m.name,"opendrive_sha256":hashlib.sha256(m.to_opendrive().encode()).hexdigest(),"carla_server_version":c.get_server_version(),"signals":signals}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,sort_keys=True,indent=2));print(json.dumps({"output":str(a.output),"signals":len(signals),"stop_waypoints":sum(len(s["stop_waypoints"]) for s in signals),"opendrive_sha256":result["opendrive_sha256"]}))

if __name__=="__main__":main()
