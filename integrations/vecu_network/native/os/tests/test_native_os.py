"""Real compiled Trampoline checks; no mock or Python scheduler is used.

AXIOM_NETWORK_OS_EXE=/absolute/build/axiom-network-os python3 test_native_os.py
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

EXE = os.environ.get("AXIOM_NETWORK_OS_EXE")


def ticks(start, end, **extra):
    return [{"op": "tick", "tick_ms": i, **extra} for i in range(start, end + 1)]


@unittest.skipUnless(EXE and Path(EXE).is_file(), "set AXIOM_NETWORK_OS_EXE to a real Linux build")
class NativeOS(unittest.TestCase):
    def run_requests(self, requests, expected=0):
        payload = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests)
        p = subprocess.run([EXE], input=payload, text=True, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, expected, p.stderr)
        rows = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["engine"], "trampoline-posix")
            self.assertEqual(row["upstream_commit"], "ff287023252bf73273067f00d2dbc8bbc9a20401")
        return rows

    def test_actual_tasks_alarm_event_priority_18_seconds(self):
        rows = self.run_requests([{"op": "init"}] + ticks(1, 18000) + [{"op": "close"}])
        self.assertEqual(len(rows), 18002)
        for row in rows[1:-1]:
            t = row["tick_ms"]
            self.assertEqual(row["bsw_due"], t % 10 == 0)
            self.assertEqual(row["control_due"], t % 50 == 0)
            self.assertEqual(row["bsw_count"], t // 10)
            self.assertEqual(row["control_count"], t // 50)
        self.assertEqual([event["name"] for event in rows[50]["events"]], [
            "BswAlarm.activate", "BswTask.run", "ControlAlarm.activate",
            "ControlEvent.set", "ControlTask.run", "ControlTask.control_request",
        ])

    def test_control_event_suppression_preserves_bsw_and_recovery(self):
        requests = [{"op": "init"}] + ticks(1, 2999)
        requests += ticks(3000, 6999, suppress_control_event=True)
        requests += ticks(7000, 7100) + [{"op": "close"}]
        rows = self.run_requests(requests)
        for row in rows[3000:7000]:
            self.assertFalse(row["control_due"])
            self.assertEqual(row["bsw_count"], row["tick_ms"] // 10)
        self.assertTrue(rows[7000]["control_due"])
        self.assertEqual(rows[-1]["suppressed_count"], 80)
        self.assertEqual(rows[-1]["control_count"], 62)

    def test_reset_restarts_real_kernel_preserves_global_time(self):
        requests = [{"op": "init"}] + ticks(1, 123) + [{"op": "reset"}]
        requests += ticks(124, 200) + [{"op": "close"}]
        rows = self.run_requests(requests)
        reset = rows[124]
        self.assertEqual((reset["op"], reset["tick_ms"], reset["epoch"]), ("reset", 123, 1))
        self.assertEqual(reset["bsw_count"], 0)
        self.assertEqual(reset["control_count"], 0)
        self.assertEqual(reset["pid"], rows[0]["pid"])
        self.assertEqual([r["tick_ms"] for r in rows[125:] if r["control_due"]], [150, 200])
        self.assertEqual(rows[-1]["bsw_count"], 8)

    def test_repeated_resets(self):
        requests = [{"op": "init"}]
        for i in range(1, 301):
            requests += ticks(i, i)
            if i in (50, 123, 200):
                requests.append({"op": "reset"})
        requests.append({"op": "close"})
        rows = self.run_requests(requests)
        self.assertEqual(rows[-1]["epoch"], 3)
        self.assertEqual(rows[-1]["control_count"], 2)
        self.assertEqual(rows[-1]["bsw_count"], 10)

    def test_three_identical_logical_runs(self):
        requests = [{"op": "init"}] + ticks(1, 1000) + [{"op": "reset"}]
        requests += ticks(1001, 2000, suppress_control_event=True) + ticks(2001, 3000) + [{"op": "close"}]
        hashes = []
        for _ in range(3):
            rows = self.run_requests(requests)
            for row in rows:
                row.pop("pid")
            hashes.append(hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
        self.assertEqual(len(set(hashes)), 1)

    def test_wallclock_does_not_advance_counter(self):
        p = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            p.stdin.write('{"op":"init"}\n'); p.stdin.flush()
            self.assertEqual(json.loads(p.stdout.readline())["tick_ms"], 0)
            time.sleep(.15)
            p.stdin.write('{"op":"tick","tick_ms":1}\n'); p.stdin.flush()
            row = json.loads(p.stdout.readline())
            self.assertEqual(row["tick_ms"], 1)
            self.assertEqual(row["bsw_count"], 0)
            p.stdin.write('{"op":"close"}\n'); p.stdin.flush()
            self.assertEqual(json.loads(p.stdout.readline())["op"], "close")
            self.assertEqual(p.wait(timeout=3), 0)
        finally:
            if p.poll() is None: p.kill(); p.wait()
            for stream in (p.stdin, p.stdout, p.stderr): stream.close()

    def test_invalid_ticks_and_json_fail_closed(self):
        invalid = [
            {"op": "tick", "tick_ms": 0}, {"op": "tick", "tick_ms": 2},
            {"op": "tick", "tick_ms": 1.0}, {"op": "tick", "tick_ms": True},
            {"op": "tick", "tick_ms": 1, "suppress_control_event": 1},
            {"op": "tick", "tick_ms": 1, "unknown": 2},
            '{"op":"tick","tick_ms":1,"tick_ms":1}',
            '{"op":"tick","tick_ms":01}', '{"op":"tick",}',
            {"op": "tick", "tick_ms": 2147483648},
            {"op": "unsupported"}, {"op": "init"},
        ]
        for request in invalid:
            with self.subTest(request=request):
                rows = self.run_requests([{"op": "init"}, request], expected=64)
                self.assertFalse(rows[-1]["ok"])
                self.assertFalse(rows[-1]["control_due"])
        self.assertFalse(self.run_requests([{"op": "tick", "tick_ms": 1}], expected=64)[-1]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
