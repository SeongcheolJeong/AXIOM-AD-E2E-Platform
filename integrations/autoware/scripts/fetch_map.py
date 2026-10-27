#!/usr/bin/env python3
"""Fetch upstream map into an explicit private runtime location with provenance.

Map redistribution rights are unresolved. This script never puts map data in
the source tree or declares the CARLA engine/code license to cover the map.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT="062f94b5322e679ee0430e2daa6995dca9f7f0ea"


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--town",choices=["Town01","Town10HD"],required=True);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    path=args.output.resolve();path.mkdir(parents=True,exist_ok=True)
    url=f"https://api.bitbucket.org/2.0/repositories/carla-simulator/autoware-contents/src/{COMMIT}/maps/vector_maps/lanelet2/{args.town}.osm"
    body=urllib.request.urlopen(url,timeout=30).read(16*1024*1024+1)
    if len(body)>16*1024*1024 or b"<osm" not in body[:1000]:raise ValueError("unexpected map payload")
    target=path/"lanelet2_map.osm"
    if target.exists() and target.read_bytes()!=body:raise ValueError("refusing to overwrite another map revision")
    target.write_bytes(body);(path/"map_projector_info.yaml").write_text("projector_type: Local\n")
    # This profile consumes Lanelet2, not a LiDAR map. A one-point synthetic map
    # is deliberately NOT substituted for the unsupported pointcloud input.
    receipt={"schema_version":"axiom-map-source/1","town":args.town,"source_url":url,"source_commit":COMMIT,
             "sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body),"rights":"unconfirmed; private evaluation only; not bundled or redistributed",
             "coordinate_frame":"upstream CARLA y-inverted local Lanelet2; native route alignment must be verified",
             "pointcloud_map":"not downloaded; no NDT or perception map usage"}
    (path/"source-receipt.json").write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))


if __name__=="__main__":main()
