#!/usr/bin/env python3
"""Stop only the temporary external-access processes recorded by this workspace."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import os
import time


def identity(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pointer = root / ".runtime/external-access-current.json"
    if not pointer.exists():
        print("외부 접속 세션이 없습니다.")
        return
    base = Path(json.loads(pointer.read_text())["directory"]).resolve()
    if base.parent != (root / ".runtime").resolve():
        raise SystemExit("Unexpected session directory; no process was stopped.")
    records = json.loads((base / "processes.json").read_text())
    # Validate every supported record before sending the first signal. Records
    # for APIs, workers, CARLA or other services are never considered.
    names = ("supervisor", "gateway", "tunnel", "awake_guard")
    for name in names:
        record = records.get(name)
        if record and (type(record.get("pid")) is not int or record["pid"] <= 1
                       or not isinstance(record.get("identity"), str)
                       or not record["identity"]):
            raise SystemExit("Invalid process identifier; no signal sent.")
    for name in names:
        record = records.get(name)
        if not record:
            continue
        pid = record["pid"]
        if type(pid) is not int or pid <= 1:
            raise SystemExit("Invalid process identifier; no signal sent.")
        current = identity(pid)
        if not current:
            print(f"{name}: 이미 종료됨")
        elif current != record["identity"]:
            print(f"{name}: 프로세스가 달라 종료하지 않았습니다.")
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"{name}: 외부 접속 종료 요청 완료")
                if name == "supervisor":
                    # The owner cleans up its children first. If it cannot,
                    # continue with the separately pinned child identities.
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and identity(pid) == current:
                        time.sleep(0.1)
            except ProcessLookupError:
                print(f"{name}: 이미 종료됨")


if __name__ == "__main__":
    main()
