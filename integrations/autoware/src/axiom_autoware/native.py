"""JSONL runtime, executed only inside the pinned Autoware ROS image.

CARLA is never imported or ticked here. Native Autoware planning and MPC/PID run
under an AXIOM simulation supervisor; no full Autoware safety-stack claim.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from . import IMAGE, VERSION
from .contract import ContractError, EpisodeClock, PriusConverter, canonical_hash, carla_to_ros_pose, number, ros_to_carla_pose, acceleration_semantics, LEGACY_PID_SOURCE_SHA256, rear_axle_kinematics, bbox_corners_ros
from .map_contract import LaneletMap


def stamp_s(stamp):
    return stamp.sec + stamp.nanosec*1e-9


class Runtime:
    def __init__(self, config):
        # Optional packages are deliberately imported after receiving an init request.
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from geometry_msgs.msg import AccelWithCovarianceStamped, TransformStamped, Pose
        from nav_msgs.msg import Odometry, OccupancyGrid
        from rosgraph_msgs.msg import Clock
        from tf2_msgs.msg import TFMessage
        from autoware_vehicle_msgs.msg import SteeringReport, VelocityReport, ControlModeReport, GearReport
        from autoware_adapi_v1_msgs.msg import OperationModeState
        from autoware_perception_msgs.msg import PredictedObjects, PredictedObject, PredictedPath, ObjectClassification, TrafficLightGroupArray, TrafficLightGroup, TrafficLightElement
        from autoware_planning_msgs.msg import Trajectory, RouteState
        from autoware_control_msgs.msg import Control
        from autoware_planning_msgs.srv import SetWaypointRoute
        from autoware_internal_planning_msgs.msg import VelocityLimit
        from sensor_msgs.msg import PointCloud2, PointField
        from builtin_interfaces.msg import Time
        from builtin_interfaces.msg import Duration
        self.types=locals()
        if config.get("schema_version") != "axiom-autoware-init/1":
            raise ContractError("unsupported init schema")
        if config.get("map_id") not in ("Town01", "Town10HD_Opt"):
            raise ContractError("map profile not supported")
        self.config=config
        self.vehicle=config["vehicle"]
        if self.vehicle.get("blueprint") != "vehicle.toyota.prius":
            raise ContractError("only native Prius has an initial control profile")
        self.offset=number(self.vehicle["rear_axle_offset_m"], "rear_axle_offset_m")
        self.clock=EpisodeClock(config["dt_s"],config.get("max_command_age_s",.25))
        self.max_speed=number(config.get("max_speed_mps",3.0),"max_speed_mps")
        if not 0 < self.max_speed <= 8:
            raise ContractError("GT planning profile supports 0 < speed <= 8m/s")
        map_hash=hashlib.sha256(Path("/maps/lanelet2_map.osm").read_bytes()).hexdigest()
        if config.get("map_sha256") != map_hash:
            raise ContractError("Lanelet2 map hash mismatch")
        self.map=LaneletMap("/maps/lanelet2_map.osm")
        if "traffic_signals" in config.get("required_capabilities",[]) and not self.map.lights:
            raise ContractError("Lanelet2 map has no traffic-light regulatory elements; signal-dependent Autoware run unsupported")
        self.converter=PriusConverter("/opt/autoware/share/autoware_carla_interface",self.vehicle["max_steer_angle_rad"])
        self.pid_binary_sha256=hashlib.sha256(Path("/opt/autoware/lib/libautoware_pid_longitudinal_controller_lib.so").read_bytes()).hexdigest()
        # Set measured wheelbase/height in this disposable container, never in host profiles.
        vehicle_path=Path("/opt/autoware/share/sample_vehicle_description/config/vehicle_info.param.yaml")
        import yaml
        vehicle_params=yaml.safe_load(vehicle_path.read_text())
        for field in ("wheel_base","wheel_tread","wheel_radius","front_overhang","rear_overhang","left_overhang","right_overhang","vehicle_height"):
            if field not in self.vehicle:
                raise ContractError(f"measured native vehicle field missing: {field}")
            vehicle_params["/**"]["ros__parameters"][field]=number(self.vehicle[field],field)
        vehicle_params["/**"]["ros__parameters"]["max_steer_angle"]=self.converter.max_steer
        vehicle_path.write_text(yaml.safe_dump(vehicle_params))
        Path("/output/vehicle_info.param.yaml").write_text(vehicle_path.read_text())
        # The pinned image's optional RViz goal visualizer references an absent
        # component class. AXIOM renders the goal; omit only that UI include in
        # this disposable container and preserve both hashes in the receipt.
        mission_launch=Path("/opt/autoware/share/tier4_planning_launch/launch/mission_planning/mission_planning.launch.xml")
        original_launch=mission_launch.read_bytes();tree=ET.fromstring(original_launch);removed=0
        for parent in tree.iter():
            for child in list(parent):
                if child.tag=="include" and child.attrib.get("file","").endswith("/launch/goal_pose_visualizer.launch.xml"):
                    parent.remove(child);removed+=1
        if removed!=1:raise ContractError("pinned image optional visualizer launch changed")
        mission_launch.write_bytes(ET.tostring(tree))
        source_files={str(p.relative_to("/axiom")):hashlib.sha256(p.read_bytes()).hexdigest()
                      for directory in (Path("/axiom/src/axiom_autoware"),Path("/axiom/config"))
                      for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix in (".py",".xml",".yaml")}
        self.receipt={"schema_version":"axiom-autoware-runtime/1","adapter_version":VERSION,
            "image":IMAGE,"config_sha256":canonical_hash(config),"lanelet2_sha256":map_hash,
            "source_files":source_files,"source_fingerprint":canonical_hash(source_files),
            "coordinate_transform":"carla-lh-center-to-ros-rh-rear-axle/1",
            "kinematic_origin_transform":"planar-rigid-body-center-to-rear-axle/1; native yaw acceleration from consecutive observed rates; first sample assumes zero derivative",
            "ground_truth_bbox_origin":"center; obstacle cloud uses +/- half height",
            "native_control_gate":"minimum three current finite trajectory points; normalized quaternion; trajectory age <=0.5s; bounded command age; rejection clears engagement",
            "fidelity":"native Autoware planning and MPC/PID; simulator ground truth; AXIOM supervisor",
            "perception":False,"ndt_localization":False,"full_autoware_adapi_safety_gate":False,
            "object_prediction":"constant velocity, 3 seconds; extrapolation from simulator ground-truth state, not future ground truth",
            "occupancy":"Lanelet2 road polygons plus supplied GT actor footprints; unknown outside mapped roads; not sensor-derived",
            "obstacle_cloud":"supplied GT actor bounding-box corners; not LiDAR or static environment perception",
            "map_traffic_light_regulatory_elements":len(self.map.lights),
            "pid_binary_sha256":self.pid_binary_sha256,
            "legacy_acceleration_compatibility":{"version":"pinned-pid-0.50-acceleration-field/1","reference_source_sha256":LEGACY_PID_SOURCE_SHA256,"source_url":"https://raw.githubusercontent.com/autowarefoundation/autoware_universe/0.50.0/control/autoware_pid_longitudinal_controller/src/pid_longitudinal_controller.cpp"},
            "runtime_launch_patch":{"id":"omit-unused-rviz-goal-visualizer/1","original_sha256":hashlib.sha256(original_launch).hexdigest(),"derived_sha256":hashlib.sha256(mission_launch.read_bytes()).hexdigest()},
            "pedal_conversion":self.converter.provenance,"vehicle_info_sha256":hashlib.sha256(vehicle_path.read_bytes()).hexdigest()}
        Path("/output/runtime-receipt.json").write_text(json.dumps(self.receipt,indent=2))
        self.log=Path("/output/autoware.log").open("w")
        self.launch=subprocess.Popen(["ros2","launch","/axiom/config/planning_control.launch.xml"],stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        rclpy.init(args=[])
        self.node=Node("axiom_episode_bridge")
        self.rclpy=rclpy
        latched=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
        self.publishers={}
        for key,typ,topic in (("clock",Clock,"/clock"),("odom",Odometry,"/localization/kinematic_state"),
            ("accel",AccelWithCovarianceStamped,"/localization/acceleration"),("tf",TFMessage,"/tf"),
            ("objects",PredictedObjects,"/perception/object_recognition/objects"),
            ("signals",TrafficLightGroupArray,"/perception/traffic_light_recognition/traffic_signals"),
            ("steer",SteeringReport,"/vehicle/status/steering_status"),("velocity",VelocityReport,"/vehicle/status/velocity_status"),
            ("control_mode",ControlModeReport,"/vehicle/status/control_mode"),("gear",GearReport,"/vehicle/status/gear_status"),
            ("mode",OperationModeState,"/system/operation_mode/state"),("limit",VelocityLimit,"/planning/scenario_planning/max_velocity_default")):
            self.publishers[key]=self.node.create_publisher(typ,topic,latched if key in ("mode","limit") else 10)
        self.publishers["occupancy"]=self.node.create_publisher(OccupancyGrid,"/perception/occupancy_grid_map/map",latched)
        self.publishers["cloud"]=self.node.create_publisher(PointCloud2,"/perception/obstacle_segmentation/pointcloud",10)
        self.trajectory=None
        self.command=None
        self.route_future=None
        self.route_result=None
        self.route_state=None
        self.engaged=False
        self.received_trajectory=False
        self.control_subscription=self.node.create_subscription(Control,"/axiom/native_control_cmd",lambda msg:setattr(self,"command",msg),10)
        self.trajectory_subscription=self.node.create_subscription(Trajectory,"/planning/trajectory",lambda msg:setattr(self,"trajectory",msg),10)
        self.route_state_subscription=self.node.create_subscription(RouteState,"/planning/route_state",lambda msg:setattr(self,"route_state",msg.state),latched)
        self.route_client=self.node.create_client(SetWaypointRoute,"/planning/set_waypoint_route")
        self.diagnostics=[]
        self.started=time.monotonic()

    def ros_stamp(self, value):
        total=round(value*1e9)
        return self.types["Time"](sec=total//1_000_000_000,nanosec=total%1_000_000_000)

    def pose(self,value,offset=0.0):
        p=carla_to_ros_pose(value,offset)
        out=self.types["Pose"]()
        out.position.x,out.position.y,out.position.z=p["x"],p["y"],p["z"]
        out.orientation.z,out.orientation.w=math.sin(p["yaw_rad"]/2),math.cos(p["yaw_rad"]/2)
        return out

    def publish_observation(self,obs):
        t=self.types
        stamp=self.ros_stamp(obs["sim_time_s"])
        ego=obs["ego"]
        pose=self.pose(ego["pose"],self.offset)
        yaw_rate=number(ego["yaw_rate_rps"],"yaw rate")
        previous=getattr(self,"previous_yaw_observation",None)
        yaw_acceleration=0.0 if previous is None else (yaw_rate-previous[1])/(self.clock.time-previous[0])
        self.previous_yaw_observation=(self.clock.time,yaw_rate)
        kinematics=rear_axle_kinematics(ego,self.offset,yaw_acceleration)
        speed=kinematics["longitudinal_velocity"]
        c=t["Clock"]();c.clock=stamp;self.publishers["clock"].publish(c)
        od=t["Odometry"]();od.header.stamp=stamp;od.header.frame_id="map";od.child_frame_id="base_link";od.pose.pose=pose
        od.twist.twist.linear.x=speed;od.twist.twist.linear.y=kinematics["lateral_velocity"]
        od.twist.twist.angular.z=kinematics["yaw_rate"]
        for i in (0,7,14,21,28,35): od.pose.covariance[i]=.001;od.twist.covariance[i]=.001
        self.publishers["odom"].publish(od)
        ac=t["AccelWithCovarianceStamped"]();ac.header.stamp=stamp;ac.header.frame_id="base_link"
        ac.accel.accel.linear.x=kinematics["longitudinal_acceleration"];ac.accel.accel.linear.y=kinematics["lateral_acceleration"];ac.accel.accel.angular.z=-yaw_acceleration
        self.publishers["accel"].publish(ac)
        tf=t["TransformStamped"]();tf.header=od.header;tf.child_frame_id="base_link";tf.transform.translation.x=pose.position.x;tf.transform.translation.y=pose.position.y;tf.transform.translation.z=pose.position.z;tf.transform.rotation=pose.orientation
        self.publishers["tf"].publish(t["TFMessage"](transforms=[tf]))
        steer=t["SteeringReport"]();steer.stamp=stamp;steer.steering_tire_angle=-number(ego["steering_tire_angle_rad"],"steer")
        self.publishers["steer"].publish(steer)
        velocity=t["VelocityReport"]();velocity.header=ac.header;velocity.longitudinal_velocity=speed;velocity.lateral_velocity=od.twist.twist.linear.y;velocity.heading_rate=od.twist.twist.angular.z
        self.publishers["velocity"].publish(velocity)
        ctrl=t["ControlModeReport"]();ctrl.stamp=stamp;ctrl.mode=ctrl.AUTONOMOUS if self.engaged else ctrl.MANUAL;self.publishers["control_mode"].publish(ctrl)
        gear=t["GearReport"]();gear.stamp=stamp;gear.report=gear.DRIVE;self.publishers["gear"].publish(gear)
        mode=t["OperationModeState"]();mode.stamp=stamp;mode.mode=mode.AUTONOMOUS if self.engaged else mode.STOP
        mode.is_autoware_control_enabled=self.engaged;mode.is_in_transition=False;mode.is_autonomous_mode_available=True;mode.is_stop_mode_available=True
        self.publishers["mode"].publish(mode)
        limit=t["VelocityLimit"]();limit.stamp=stamp;limit.max_velocity=self.max_speed;limit.sender="axiom_frozen_scenario";self.publishers["limit"].publish(limit)
        objects=t["PredictedObjects"]();objects.header=od.header
        for actor in obs.get("actors",[]):
            if actor["kind"] not in ("vehicle","pedestrian","static"):
                raise ContractError("unsupported ground-truth actor classification")
            if actor["kind"]=="static" and any(abs(number(actor["velocity"].get(axis,0.),"static velocity"))>1e-6 for axis in ("x","y","z")):
                raise ContractError("static actor has nonzero velocity")
            obj=t["PredictedObject"]();obj.object_id.uuid=list(uuid.uuid5(uuid.NAMESPACE_URL,str(actor["id"])).bytes);obj.existence_probability=1.0
            cls=t["ObjectClassification"]();cls.label={"pedestrian":cls.PEDESTRIAN,"vehicle":cls.CAR,"static":cls.UNKNOWN}[actor["kind"]];cls.probability=1.0;obj.classification=[cls]
            obj.kinematics.initial_pose_with_covariance.pose=self.pose(actor["pose"])
            av=actor["velocity"];ayaw=number(actor["pose"]["yaw_rad"],"actor yaw")
            obj.kinematics.initial_twist_with_covariance.twist.linear.x=number(av["x"],"actor vx")*math.cos(ayaw)+number(av["y"],"actor vy")*math.sin(ayaw)
            obj.kinematics.initial_twist_with_covariance.twist.linear.y=number(av["x"],"actor vx")*math.sin(ayaw)-number(av["y"],"actor vy")*math.cos(ayaw)
            prediction=t["PredictedPath"]();prediction.time_step=t["Duration"](sec=0,nanosec=500000000);prediction.confidence=1.0
            for step in range(7):
                predicted=dict(actor["pose"])
                predicted["x"]+=number(av["x"],"actor vx")*step*.5
                predicted["y"]+=number(av["y"],"actor vy")*step*.5
                prediction.path.append(self.pose(predicted))
            obj.kinematics.predicted_paths=[prediction]
            obj.shape.type=obj.shape.BOUNDING_BOX
            for axis,key in (("x","length"),("y","width"),("z","height")):
                setattr(obj.shape.dimensions,axis,number(actor["dimensions"][key],key))
            objects.objects.append(obj)
        self.publishers["objects"].publish(objects)
        occupancy=t["OccupancyGrid"]();occupancy.header=od.header;occupancy.info.resolution=self.map.resolution
        occupancy.info.width=self.map.width;occupancy.info.height=self.map.height;occupancy.info.origin.position.x=float(self.map.origin[0]);occupancy.info.origin.position.y=float(self.map.origin[1]);occupancy.info.origin.orientation.w=1.0
        occupancy.data=self.map.occupancy(obs.get("actors",[]));self.publishers["occupancy"].publish(occupancy)
        cloud=t["PointCloud2"]();cloud.header=od.header;cloud.height=1;cloud.is_bigendian=False;cloud.is_dense=True;cloud.point_step=12
        cloud.fields=[t["PointField"](name=name,offset=offset,datatype=t["PointField"].FLOAT32,count=1) for name,offset in (("x",0),("y",4),("z",8))]
        points=[]
        for actor in obs.get("actors",[]):
            points.extend(bbox_corners_ros(actor))
        cloud.width=len(points);cloud.row_step=cloud.point_step*cloud.width;cloud.data=b"".join(struct.pack("<fff",*p) for p in points);self.publishers["cloud"].publish(cloud)
        signals=t["TrafficLightGroupArray"]();signals.stamp=stamp
        supplied_signals=obs.get("traffic_lights",[])
        if supplied_signals and "lanelet_id" not in supplied_signals[0]:
            self.signal_mapping=self.map.map_signals(supplied_signals)
            supplied_signals=self.signal_mapping["mapped"]
        else:
            self.signal_mapping={"status":"mapped" if supplied_signals else ("no_native_observations" if self.map.lights else "unsupported_map_regulatory_elements_missing"),"mapped":supplied_signals,"map_sha256":self.map.sha256}
        if "traffic_signals" in self.config.get("required_capabilities",[]) and self.signal_mapping["status"]!="mapped":
            raise ContractError("required native traffic-light correspondence incomplete or missing")
        for signal in supplied_signals:
            if signal["lanelet_id"] not in self.map.lights:raise ContractError("unregistered Lanelet2 traffic-light ID")
            group=t["TrafficLightGroup"]();group.traffic_light_group_id=int(signal["lanelet_id"])
            element=t["TrafficLightElement"]();element.color={"red":element.RED,"yellow":element.AMBER,"green":element.GREEN,"unknown":element.UNKNOWN}[signal["state"]]
            element.shape=element.CIRCLE;element.status=element.SOLID_ON;element.confidence=1.0;group.elements=[element];signals.traffic_light_groups.append(group)
        self.publishers["signals"].publish(signals)
        return speed

    def maybe_route(self):
        if self.route_future is None and self.route_state==self.types["RouteState"].UNSET and self.route_client.service_is_ready():
            req=self.types["SetWaypointRoute"].Request();req.header.frame_id="map";req.header.stamp=self.ros_stamp(self.clock.time)
            req.goal_pose=self.pose(self.config["goal"],self.offset)
            req.waypoints=[self.pose(p,self.offset) for p in self.config.get("waypoints",[])]
            req.allow_modification=False;req.uuid.uuid=list(uuid.uuid5(uuid.NAMESPACE_URL,self.config["episode_id"]).bytes)
            self.route_future=self.route_client.call_async(req)
        if self.route_future is not None and self.route_result is None and self.route_future.done():
            result=self.route_future.result();self.route_result={"success":result.status.success,"code":result.status.code,"message":result.status.message}

    def step(self,obs):
        if self.launch.poll() is not None:
            raise ContractError("native Autoware launch exited; inspect autoware.log")
        self.clock.observe(obs["frame"],obs["sim_time_s"])
        speed=self.publish_observation(obs)
        deadline=time.monotonic()+min(2.0,max(.05,number(obs.get("wait_s",.15),"wait_s")))
        while time.monotonic()<deadline:
            self.rclpy.spin_once(self.node,timeout_sec=.01)
            self.maybe_route()
        reply={"schema_version":"axiom-autoware-step/1","frame":obs["frame"],"sim_time_s":obs["sim_time_s"],"status":"preparing",
               "route":self.route_result,"route_state":self.route_state,"trajectory":[],"control":None,"diagnostics":[],"signal_mapping":self.signal_mapping}
        if self.route_result and not self.route_result["success"]:
            reply["status"]="route_rejected";return reply
        if self.route_result: reply["status"]="route_ready"
        if self.trajectory is not None:
            traj_stamp=stamp_s(self.trajectory.header.stamp)
            reply["trajectory_stamp_s"]=traj_stamp
            try:
                for p in self.trajectory.points:
                    q=p.pose.orientation
                    qx,qy,qz,qw=(number(getattr(q,k),"trajectory quaternion "+k) for k in ("x","y","z","w"))
                    if abs(qx*qx+qy*qy+qz*qz+qw*qw-1.0)>1e-3:
                        raise ContractError("native trajectory quaternion is not normalized")
                    yaw=math.atan2(2*(qw*qz+qx*qy),1-2*(qy*qy+qz*qz))
                    out=ros_to_carla_pose({"x":p.pose.position.x,"y":p.pose.position.y,"z":p.pose.position.z,"yaw_rad":yaw},self.offset)
                    out["speed_mps"]=number(p.longitudinal_velocity_mps,"trajectory speed")
                    out["acceleration_mps2"]=number(p.acceleration_mps2,"trajectory acceleration")
                    reply["trajectory"].append(out)
            except ContractError as exc:
                self.engaged=False;reply["status"]="command_rejected";reply["diagnostics"].append(str(exc));return reply
        # Pinned MPC0.50 ignores paths shorter than three points and can retain
        # its earlier reference. Fresh Control alone must never accept that cache.
        if self.engaged and len(reply["trajectory"])<3:
            self.engaged=False;reply["status"]="command_rejected"
            reply["diagnostics"].append("native trajectory has fewer than three current points")
            return reply
        if len(reply["trajectory"])>=3 and self.route_result and self.route_result["success"]:
            if not -1e-5 <= self.clock.time-reply["trajectory_stamp_s"] <= 0.5:
                self.engaged=False;reply["status"]="command_rejected";reply["diagnostics"].append("stale native trajectory");return reply
            self.engaged=True;reply["status"]="engaging"
        if self.command is not None and self.engaged:
            command_stamp=stamp_s(self.command.stamp)
            try:
                age=self.clock.command_age(command_stamp)
                if "trajectory_stamp_s" not in reply or not -1e-5 <= self.clock.time-reply["trajectory_stamp_s"] <= 0.5:
                    raise ContractError("stale native trajectory")
                longitudinal=self.command.longitudinal
                reply["acceleration_semantics"]=acceleration_semantics(longitudinal.is_defined_acceleration,self.pid_binary_sha256)
                reply["native_acceleration_defined_flag"]=longitudinal.is_defined_acceleration
                reply["control"]=self.converter.convert(longitudinal.acceleration,speed,self.command.lateral.steering_tire_angle,longitudinal.velocity)
                reply.update(command_stamp_s=command_stamp,command_age_s=age,target_speed_mps=longitudinal.velocity,
                             target_acceleration_mps2=longitudinal.acceleration,target_steering_rad=-self.command.lateral.steering_tire_angle,
                             status="driving" if speed>.05 else "engaged_stopped")
            except ContractError as exc:
                self.engaged=False;reply["status"]="command_rejected";reply["diagnostics"].append(str(exc))
        if time.monotonic()-self.started>180 and not self.engaged:
            reply["status"]="startup_timeout"
        return reply

    def close(self):
        if getattr(self,"launch",None) and self.launch.poll() is None:
            os.killpg(self.launch.pid,signal.SIGINT)
            try:self.launch.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(self.launch.pid,signal.SIGTERM)
                try:self.launch.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(self.launch.pid,signal.SIGKILL);self.launch.wait(timeout=2)
        if hasattr(self,"node"):self.node.destroy_node();self.rclpy.shutdown()
        if hasattr(self,"log"):self.log.close()


def main():
    runtime=None
    exit_code=0
    try:
        for line in sys.stdin:
            try:
                request=json.loads(line)
                if request.get("op")=="init":
                    if runtime is not None: raise ContractError("episode already initialized")
                    runtime=Runtime(request);response={"status":"initializing","receipt":runtime.receipt}
                elif request.get("op")=="step" and runtime is not None:response=runtime.step(request)
                elif request.get("op")=="close":break
                else:raise ContractError("init required, or unsupported operation")
                print(json.dumps(response,allow_nan=False),flush=True)
            except Exception as exc:
                print(json.dumps({"status":"error","error_type":type(exc).__name__,"message":str(exc)}),flush=True)
                exit_code=1
                break
    finally:
        if runtime is not None:runtime.close()
    return exit_code


if __name__=="__main__":raise SystemExit(main())
