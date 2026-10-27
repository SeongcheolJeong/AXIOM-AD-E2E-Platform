"""Host launcher: one CPU-only, isolated ROS domain, pinned image per episode."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from . import IMAGE


def docker_command(map_dir, output_dir, episode_id, ros_domain=78, source_root=None):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", episode_id):
        raise ValueError("invalid episode ID")
    if not 1 <= ros_domain <= 232 or ros_domain == 42:
        raise ValueError("choose a dedicated non-default ROS domain; domain42 is reserved")
    root = Path(source_root or Path(__file__).resolve().parents[2]).resolve()
    maps, output = Path(map_dir).resolve(), Path(output_dir).resolve()
    if not (maps/"lanelet2_map.osm").is_file() or not (maps/"map_projector_info.yaml").is_file():
        raise ValueError("verified Lanelet2 map and Local projection metadata are required")
    output.mkdir(parents=True, exist_ok=True)
    # CPU only: no --gpus, CUDA or host DDS access. Host IPC and network are not shared.
    return ["docker", "run", "--rm", "--init", "-i", "--name", f"axiom-autoware-{episode_id}",
            "--network", "none", "--shm-size", "512m", "--memory", "6g", "--cpus", "6",
            "-e", f"ROS_DOMAIN_ID={ros_domain}", "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
            "-e", "CYCLONEDDS_URI=file:///axiom/config/cyclonedds.xml",
            "-e", "RCUTILS_LOGGING_USE_STDOUT=0", "-e", "PYTHONPATH=/axiom/src",
            "-v", f"{root}:/axiom:ro", "-v", f"{maps}:/maps:ro", "-v", f"{output}:/output",
            "--entrypoint", "bash", IMAGE, "-lc",
            "source /opt/autoware/setup.bash && exec python3 -m axiom_autoware.native"]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--ros-domain", type=int, default=78)
    args=parser.parse_args()
    command=docker_command(args.map_dir,args.output_dir,args.episode_id,args.ros_domain)
    # The owned container exits with stdin; --init handles descendant reaping via native cleanup.
    process=subprocess.Popen(command)
    def stop_owned(_signum=None,_frame=None):
        subprocess.run(["docker","stop","--timeout","5",f"axiom-autoware-{args.episode_id}"],
                       stdout=sys.stderr,stderr=sys.stderr,check=False,timeout=10)
    signal.signal(signal.SIGTERM,stop_owned)
    try:
        return process.wait()
    except KeyboardInterrupt:
        stop_owned()
        return process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
