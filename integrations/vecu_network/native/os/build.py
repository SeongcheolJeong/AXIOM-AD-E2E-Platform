#!/usr/bin/env python3
"""Build the pinned real Trampoline kernel and the external-tick application."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path, archive: Path | None, goil: Path | None) -> dict:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Trampoline runner currently supports Linux x86_64 only")
    if output.exists():
        raise RuntimeError("output directory already exists; use a fresh immutable build directory")
    output.mkdir(parents=True)
    upstream = json.loads((HERE / "upstream.json").read_text())
    if archive is None:
        archive = output / "upstream.tar.gz"
        urllib.request.urlretrieve(upstream["archive_url"], archive)
    if sha(archive) != upstream["archive_sha256"]:
        raise RuntimeError("Trampoline archive hash mismatch")
    with tarfile.open(archive, "r:gz") as tar:
        # Refuse escaping paths and links even for an already pinned archive.
        for member in tar.getmembers():
            target = (output / member.name).resolve()
            if not target.is_relative_to(output.resolve()):
                raise RuntimeError("unsafe archive path")
            if member.issym() or member.islnk():
                link = (target.parent / member.linkname).resolve()
                if not link.is_relative_to(output.resolve()):
                    raise RuntimeError("unsafe archive link")
        tar.extractall(output)
    source = output / f"trampoline-{upstream['commit']}"
    for relative, expected in upstream["patched_originals"].items():
        if sha(source / relative) != expected:
            raise RuntimeError(f"upstream file mismatch: {relative}")
    with (output / "build.log").open("w") as log:
        def run(args: list[str], cwd: Path) -> None:
            log.write("$ " + " ".join(args) + "\n"); log.flush()
            subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True)

        run(["patch", "-p1", "--batch", "--forward", "-i", str(HERE / "patches/external-tick.patch")], source)
        if goil is None:
            run(["python3", "build.py"], source / "goil/makefile-unix")
            goil = source / "goil/makefile-unix/goil"
        app = output / "app"
        app.mkdir()
        shutil.copyfile(HERE / "network_os.c", app / "network_os.c")
        (app / "network_os.oil").write_text((HERE / "network_os.oil").read_text().replace("@TRAMPOLINE@", str(source)))
        run([str(goil), "--target=posix/linux", f"--templates={source / 'goil/templates'}", "network_os.oil"], app)
        run(["python3", "make.py", "-s"], app)
    binary = output / "axiom-network-os"
    shutil.copy2(app / "axiom-network-os", binary)
    compiler = subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0]
    manifest = {
        "schema_version": "vecu-network-os-build/1", "protocol": "vecu-network-os/1",
        "engine": "trampoline-posix", "upstream_commit": upstream["commit"],
        "upstream_archive_sha256": upstream["archive_sha256"],
        "patch_sha256": sha(HERE / "patches/external-tick.patch"),
        "binary": binary.name, "binary_sha256": sha(binary),
        "compiler": compiler, "platform": "linux-x86_64", "goil_sha256": sha(goil),
        "host_port_flags": "-O0 -g -Wall -Wextra -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0",
        "sources": {str(p.relative_to(HERE)): sha(p) for p in [HERE / "build.py", HERE / "network_os.c", HERE / "network_os.oil", HERE / "upstream.json"]},
        "generated": {str(p.relative_to(app)): sha(p) for p in sorted(app.rglob("*")) if p.is_file() and p.suffix in {".c", ".h", ".oil"}},
        "clock": {"source": "external_tick", "tick_ms": 1, "bsw_period_ms": 10, "control_period_ms": 50},
        "fidelity": "actual-host-os-kernel; no MCU/WCET/physical validation",
    }
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--goil", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve(), args.archive.resolve() if args.archive else None, args.goil.resolve() if args.goil else None)))
