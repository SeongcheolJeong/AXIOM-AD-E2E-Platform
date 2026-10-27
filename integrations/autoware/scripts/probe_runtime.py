#!/usr/bin/env python3
"""Offline native ROS startup/route probe. This never constitutes CARLA driving."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--map-dir",required=True);p.add_argument("--output-dir",required=True);p.add_argument("--steps",type=int,default=100);p.add_argument("--request-file",type=Path,help="Authored init/ego/traffic_lights JSON for a different map; this remains a stationary ROS probe");args=p.parse_args()
    from axiom_autoware.launcher import docker_command
    maps=Path(args.map_dir);receipt={"sha256":hashlib.sha256((maps/"lanelet2_map.osm").read_bytes()).hexdigest()}
    process=subprocess.Popen(docker_command(maps,args.output_dir,"offline-probe"),stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def exchange(value):
        process.stdin.write(json.dumps(value)+"\n");process.stdin.flush();line=process.stdout.readline()
        if not line:raise RuntimeError("runtime exited")
        return json.loads(line)
    try:
        config={"op":"init","schema_version":"axiom-autoware-init/1","episode_id":"offline-probe","map_id":"Town01","map_sha256":receipt["sha256"],"goal":{"x":334.7104,"y":313.7428,"z":0.,"yaw_rad":1.5714},"dt_s":.05,"max_speed_mps":3.,"vehicle":{"blueprint":"vehicle.toyota.prius","rear_axle_offset_m":1.4,"max_steer_angle_rad":1.22,"wheel_base":2.7,"wheel_tread":1.55,"wheel_radius":.3,"front_overhang":.8,"rear_overhang":1.,"left_overhang":.13,"right_overhang":.13,"vehicle_height":1.5}}
        request=json.loads(args.request_file.read_text()) if args.request_file else {}
        if request:
            config=request["init"]
            config["map_sha256"]=receipt["sha256"]
            config["episode_id"]="offline-probe"
        print(json.dumps(exchange(config)),flush=True)
        output=Path(args.output_dir)/"offline-probe.jsonl"
        with output.open("w") as stream:
            for i in range(args.steps):
                observation={"op":"step","frame":i,"sim_time_s":100+i*.05,"wait_s":.2,"ego":{"pose":{"x":334.7346,"y":273.7429,"z":0.,"yaw_rad":1.5714},"velocity":{"x":0.,"y":0.,"z":0.},"acceleration":{"x":0.,"y":0.,"z":0.},"yaw_rate_rps":0.,"steering_tire_angle_rad":0.},"actors":[],"traffic_lights":[]}
                if request:
                    observation["ego"]=request["ego"]
                    observation["traffic_lights"]=request.get("traffic_lights",[])
                result=exchange(observation);stream.write(json.dumps(result)+"\n");stream.flush()
                if i%10==0 or result["status"]=="error":print(json.dumps({k:v for k,v in result.items() if k!="trajectory"}|{"trajectory_points":len(result.get("trajectory",[]))}),flush=True)
                if result["status"]=="error":break
    finally:
        if process.poll() is None:
            try:process.stdin.write('{"op":"close"}\n');process.stdin.flush()
            except BrokenPipeError:pass
        process.wait(timeout=20)

if __name__=="__main__":main()
