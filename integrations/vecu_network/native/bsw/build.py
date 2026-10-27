#!/usr/bin/env python3
"""Build the pinned optional autoas BSW executable; never download during a build."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build(output: Path) -> dict:
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Build output must be outside the immutable source tree")
    upstream = ROOT / "vendor" / "autoas"
    lock = json.loads((ROOT / "vendor" / "autoas.lock.json").read_text())
    for name, expected in lock["files"].items():
        if digest(upstream / name) != expected:
            raise ValueError(f"Pinned autoas file changed: {name}")
    json_lock = json.loads((ROOT / "vendor" / "nlohmann.lock.json").read_text())
    for name, expected in json_lock["files"].items():
        if digest(ROOT / "vendor" / name) != expected:
            raise ValueError(f"Pinned nlohmann file changed: {name}")
    output.mkdir(parents=True, exist_ok=False)
    cc, cxx = shutil.which("cc"), shutil.which("c++")
    if not cc or not cxx:
        raise RuntimeError("A native C/C++ compiler is required")
    include = [ROOT, ROOT / "config", upstream / "infras/include",
               upstream / "infras/communication/Com", upstream / "infras/communication/PduR",
               upstream / "infras/communication/CanIf", upstream / "infras/libraries/mempool"]
    sources = [ROOT / "bridge.c"] + [upstream / f"infras/communication/{name}" for name in
        ["Com/Com.c", "PduR/PduR.c", "PduR/PduR_Com.c", "PduR/PduR_CanIf.c", "CanIf/CanIf.c"]]
    sources.append(upstream / "infras/libraries/stdbit/src/std_bit.c")
    flags = ["-O2", "-Wall", "-Wextra", "-DUSE_DET", "-Wno-unused-parameter"]
    includes = [flag for path in include for flag in ("-I", str(path))]
    commands, logs, objects = [], [], []
    def run(command):
        commands.append(command)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
        logs.append(proc.stdout + proc.stderr)
        (output / "build.log").write_text("\n".join(logs))
        if proc.returncode:
            raise RuntimeError(f"Native build failed: {proc.stderr}")
    for i, source in enumerate(sources):
        obj = output / f"module-{i}.o"
        run([cc, "-std=c11", *flags, *includes, "-c", str(source), "-o", str(obj)])
        objects.append(str(obj))
    binary = output / "axiom-autoas-bsw"
    run([cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", str(ROOT / "runner.cpp"),
         *objects, "-o", str(binary)])
    source_hashes = {str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.rglob("*"))
                     if p.is_file() and "__pycache__" not in p.parts}
    manifest = {"schema": "axiom-bsw-build/1", "upstream": lock, "json_dependency": json_lock,
                "binary": binary.name, "binary_sha256": digest(binary),
                "source_sha256": hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest(),
                "source_hashes": source_hashes,
                "compiler": subprocess.check_output([cxx, "--version"], text=True).splitlines()[0],
                "platform": platform.system() + "/" + platform.machine(),
                "commands": commands, "build_log_sha256": digest(output / "build.log"),
                "protocol": "axiom-bsw/1", "modules": ["Com", "PduR", "CanIf"],
                "production_ecu": False, "physical_can": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir.resolve())))
