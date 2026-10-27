#!/usr/bin/env python3
"""Run real four-role SIL Kit CAN + UDP-frame exchange against a sealed bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time

ROLES = ("perception", "adas", "brake", "restbus")
# Independent golden frame checked by tests/test_wire.py: IPv4 192.0.2.10→.20,
# UDP24000→24001, AXO1 sequence1/time50ms/one object. No Ethernet FCS included.
ETHERNET = bytes.fromhex("020000000020020000000010080045000058000140004011b675c000020ac00002145dc05dc10044ba8441584f31000000010000000002faf080000101613ff0000000000000c00000000000000000000000000000003fe00000000000004008000000000000")
CAN = bytes.fromhex("8813c40901800102")

def verify(bundle):
    manifest=json.loads((bundle/"manifest.json").read_text())
    for item in manifest["runtime"].values():
        binary=bundle/item["relative_path"]
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("Runtime hash changed: "+str(binary))
    children=[];trace=[]
    with tempfile.TemporaryDirectory(prefix="axiom-net-smoke-") as temporary:
        log=Path(temporary)/"native.log"
        with log.open("w+") as sink:
            def spawn(arguments):
                p=subprocess.Popen(arguments,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sink,
                                   text=True,bufsize=1)
                children.append(p);return p
            def receive(process):
                if not select.select([process.stdout],[],[],15)[0]:
                    raise TimeoutError("SIL Kit smoke response timeout")
                line=process.stdout.readline()
                if not line:raise RuntimeError("SIL Kit process exited before reply")
                reply=json.loads(line)
                if not reply.get("ok"):raise ValueError("Participant rejected request: "+line)
                return reply
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            uri=f"silkit://127.0.0.1:{port}"
            try:
                registry=spawn([str(bundle/"bin/sil-kit-registry"),"--listen-uri",uri,"--log","off"])
                for attempt in range(100):
                    if registry.poll() is not None:raise RuntimeError("Registry terminated")
                    try:
                        with socket.create_connection(("127.0.0.1",port),.1):break
                    except OSError:
                        if attempt==99:raise
                        time.sleep(.02)
                nodes={role:spawn([str(bundle/"participant"),role,uri]) for role in ROLES}
                identities={role:receive(p) for role,p in nodes.items()}
                spawn([str(bundle/"bin/sil-kit-system-controller"),"--connect-uri",uri,"--log","off",*ROLES])
                for tick in range(4):
                    for role,p in nodes.items():
                        frames=[]
                        if tick==0 and role=="perception":frames=[{"bus":"ethernet","data":list(ETHERNET)}]
                        if tick==1 and role=="adas":frames=[{"bus":"can","can_id":256,"data":list(CAN)}]
                        p.stdin.write(json.dumps({"op":"tick","time_ms":tick,"frames":frames})+"\n")
                        p.stdin.flush()
                    trace.append({role:receive(p) for role,p in nodes.items()})
                for role in ROLES:
                    eth=[f for tick in trace for f in tick[role]["frames"] if f["bus"]=="ethernet"]
                    can=[f for tick in trace for f in tick[role]["frames"] if f["bus"]=="can"]
                    if role!="perception":
                        assert len(eth)==1 and bytes(eth[0]["data"])==ETHERNET,(role,eth)
                    if role!="adas":
                        assert len(can)==1 and can[0]["can_id"]==256 and bytes(can[0]["data"])==CAN,(role,can)
                for p in nodes.values():p.stdin.write('{"op":"close"}\n');p.stdin.flush()
                closes={role:receive(p) for role,p in nodes.items()}
                return {"schema":"axiom-native-network-smoke/1","actual_execution":True,
                        "roles":list(ROLES),"engine":"silkit/5.0.8","virtual_ticks":4,
                        "identities":identities,"trace":trace,"close_acknowledged":closes,
                        "ethernet_sha256":hashlib.sha256(ETHERNET).hexdigest(),
                        "can_payload_sha256":hashlib.sha256(CAN).hexdigest(),
                        "runtime":manifest["runtime"],"physical_bus_validation":False}
            except Exception:
                sink.flush();sink.seek(0)
                raise RuntimeError("Native network verification failed; engine output: "+sink.read())
            finally:
                for p in children:
                    if p.poll() is None:p.terminate()
                for p in children:
                    try:p.wait(timeout=3)
                    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
                    if p.stdin:p.stdin.close()
                    if p.stdout:p.stdout.close()

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--bundle",type=Path,required=True)
    args=parser.parse_args();print(json.dumps(verify(args.bundle.resolve()),sort_keys=True))
