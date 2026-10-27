"""Explicit map-derived GT occupancy and fail-closed signal correspondence."""
from __future__ import annotations
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from .contract import ContractError, carla_to_ros_pose, number


def tags(element):
    return {item.attrib["k"]:item.attrib["v"] for item in element.findall("tag")}


class LaneletMap:
    def __init__(self,path,resolution=0.5):
        content=Path(path).read_bytes()
        self.sha256=hashlib.sha256(content).hexdigest()
        root=ET.fromstring(content)
        self.nodes={}
        for node in root.findall("node"):
            props=tags(node)
            if "local_x" in props and "local_y" in props:
                self.nodes[node.attrib["id"]]=(float(props["local_x"]),float(props["local_y"]),float(props.get("ele",0)))
        self.ways={way.attrib["id"]:[self.nodes[n.attrib["ref"]] for n in way.findall("nd") if n.attrib["ref"] in self.nodes] for way in root.findall("way")}
        self.polygons=[];self.lights={};self.lanes=[]
        for relation in root.findall("relation"):
            props=tags(relation)
            if props.get("type")=="lanelet" and props.get("subtype") in ("road",None):
                bounds={member.attrib["role"]:self.ways.get(member.attrib["ref"],[]) for member in relation.findall("member") if member.attrib["type"]=="way"}
                if bounds.get("left") and bounds.get("right"):
                    polygon=bounds["left"]+list(reversed(bounds["right"]))
                    self.polygons.append(polygon)
                    self.lanes.append({"id":relation.attrib["id"],"polygon":polygon,"left":bounds["left"],"right":bounds["right"]})
            if props.get("type")=="regulatory_element" and props.get("subtype")=="traffic_light":
                points=[p for member in relation.findall("member") if member.attrib["role"] in ("refers","light_bulbs") for p in self.ways.get(member.attrib["ref"],[])]
                if points:self.lights[int(relation.attrib["id"])]=(sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points))
        if not self.polygons:raise ContractError("Lanelet2 map has no local-coordinate road polygons")
        self.resolution=resolution
        allpoints=[p for poly in self.polygons for p in poly]
        self.origin=(math.floor(min(p[0] for p in allpoints))-5,math.floor(min(p[1] for p in allpoints))-5)
        self.width=math.ceil((max(p[0] for p in allpoints)-self.origin[0]+5)/resolution)
        self.height=math.ceil((max(p[1] for p in allpoints)-self.origin[1]+5)/resolution)
        if self.width*self.height>4_000_000:raise ContractError("map occupancy exceeds profile capacity")
        self.base=[-1]*(self.width*self.height)
        for poly in self.polygons:self._fill(self.base,poly,0)

    def _fill(self,grid,polygon,value):
        ymin=max(0,math.floor((min(p[1] for p in polygon)-self.origin[1])/self.resolution))
        ymax=min(self.height-1,math.ceil((max(p[1] for p in polygon)-self.origin[1])/self.resolution))
        for row in range(ymin,ymax+1):
            y=self.origin[1]+(row+.5)*self.resolution
            cross=[]
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                if (a[1]<=y<b[1]) or (b[1]<=y<a[1]):cross.append(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
            cross.sort()
            for left,right in zip(cross[::2],cross[1::2]):
                start=max(0,math.ceil((left-self.origin[0])/self.resolution-.5));end=min(self.width-1,math.floor((right-self.origin[0])/self.resolution-.5))
                if end>=start:grid[row*self.width+start:row*self.width+end+1]=[value]*(end-start+1)

    def occupancy(self,actors):
        result=self.base.copy()
        for actor in actors:
            pose=carla_to_ros_pose(actor["pose"]);d=actor["dimensions"]
            length=number(d["length"],"length")/2;width=number(d["width"],"width")/2
            c,s=math.cos(pose["yaw_rad"]),math.sin(pose["yaw_rad"])
            polygon=[(pose["x"]+x*c-y*s,pose["y"]+x*s+y*c) for x,y in ((-length,-width),(length,-width),(length,width),(-length,width))]
            self._fill(result,polygon,100)
        return result

    def map_signals(self,signals,tolerance_m=5.0):
        if not self.lights:
            return {"status":"unsupported_map_regulatory_elements_missing","mapped":[],"unmatched":[str(s.get("id")) for s in signals],"ambiguous":[],"map_sha256":self.sha256}
        mapped=[];unmatched=[];ambiguous=[];used=set()
        for signal in signals:
            p=carla_to_ros_pose(signal["pose"])
            candidates=[(math.hypot(p["x"]-x,p["y"]-y),identifier) for identifier,(x,y) in self.lights.items() if math.hypot(p["x"]-x,p["y"]-y)<=tolerance_m]
            if len(candidates)!=1:
                (ambiguous if candidates else unmatched).append(str(signal["id"]));continue
            distance,identifier=candidates[0]
            if identifier in used:
                ambiguous.append(str(signal["id"]));continue
            used.add(identifier)
            state=str(signal["state"]).lower()
            if state not in ("red","yellow","green","unknown"):state="unknown"
            mapped.append({"lanelet_id":identifier,"state":state,"native_id":str(signal["id"]),"distance_m":distance})
        return {"status":"mapped" if not unmatched and not ambiguous else "incomplete","mapped":mapped,"unmatched":unmatched,"ambiguous":ambiguous,"map_sha256":self.sha256}
