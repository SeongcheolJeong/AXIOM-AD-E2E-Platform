#!/usr/bin/env python3
"""Local counterexamples for the standalone diagnostic cleanup; no CARLA connection.

All lifecycle and RPC behavior below is explicitly mocked. The original nested cleanup body is
compiled without its enclosing run(), so this file cannot start a simulator run.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "diagnose_carla_self_hit.py"
NONCE = "counterexample_owned_nonce"


def load_module():
    spec = importlib.util.spec_from_file_location("self_hit_diagnostic_v2_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_cleanup(module, injected, function_name="cleanup"):
    """Preserve the real cleanup logic, changing only its closure storage location."""
    tree = ast.parse(SCRIPT.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    cleanup = copy.deepcopy(next(n for n in run.body if isinstance(n, ast.FunctionDef) and n.name == function_name))

    class ClosureGlobals(ast.NodeTransformer):
        def visit_Nonlocal(self, node):
            return ast.copy_location(ast.Global(names=node.names), node)

    cleanup = ClosureGlobals().visit(cleanup)
    source = ast.fix_missing_locations(ast.Module(body=[cleanup], type_ignores=[]))
    namespace = {**vars(module), **injected}
    exec(compile(source, str(SCRIPT), "exec"), namespace)
    return namespace


def run_outer_finalizer(namespace):
    """Exercise the real run() file-close branch without entering run()."""
    tree = ast.parse(SCRIPT.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    finalizer = next(n.finalbody for n in run.body if isinstance(n, ast.Try) and n.finalbody)
    source = ast.fix_missing_locations(ast.Module(body=copy.deepcopy(finalizer), type_ignores=[]))
    exec(compile(source, str(SCRIPT), "exec"), namespace)


class Journal:
    def __init__(self):
        self.stream = io.BytesIO()
        self.entries = []
        self.fail = False

    def event(self, kind, data):
        if self.fail:
            raise OSError("injected journal fsync failure")
        self.stream.write(b"event\n")
        self.entries.append((kind, copy.deepcopy(data)))


class Actor:
    def __init__(self, world, actor_id, type_id, role_name=None, parent=None):
        self.world = world
        self.id = actor_id
        self.type_id = type_id
        self.attributes = {} if role_name is None else {"role_name": role_name}
        self.parent = parent
        self.is_alive = True
        self.is_listening = type_id.startswith("sensor.")
        self.stop_error = False
        self.destroy_error = False
        self.destroy_false = False
        self.destroy_effect_then_error = False
        world.actors[actor_id] = self

    def get_world(self):
        return self.world

    def stop(self):
        self.world.calls.append(("stop", self.id))
        if self.stop_error:
            raise TimeoutError("injected sensor stop timeout")
        self.is_listening = False

    def destroy(self):
        self.world.calls.append(("destroy", self.id))
        if self.destroy_error:
            raise TimeoutError("injected destroy timeout before server effect")
        if self.destroy_false:
            return False
        self.is_alive = False
        self.world.actors.pop(self.id, None)
        if self.destroy_effect_then_error:
            raise TimeoutError("injected lost destroy acknowledgement after server effect")
        return True


class World:
    def __init__(self):
        self.id = "original-episode"
        self.actors = {}
        self.calls = []
        self.omit_owned_sensors = False
        self.omit_all_owned = False
        self.inventory_reads = 0
        self.frame = 100
        self.refresh_stays_stale = False
        self.freeze_async_snapshot = False
        self.settings = "diagnostic-settings"
        self.weather = "diagnostic-weather"
        self.map = {"name": "pinned-map", "opendrive_sha256": "fixed-hash"}

    def get_actors(self):
        self.inventory_reads += 1
        self.calls.append(("inventory", self.inventory_reads))
        values = list(self.actors.values())
        if self.omit_all_owned:
            values = [a for a in values if a.id <= 23]
        elif self.omit_owned_sensors:
            values = [a for a in values if not a.type_id.startswith("sensor.")]
        return values

    def get_actor(self, actor_id):
        self.calls.append(("get_actor", actor_id))
        return self.actors.get(actor_id)

    def get_snapshot(self):
        if self.settings == "original-settings" and not self.freeze_async_snapshot:
            self.frame += 1
        self.calls.append(("snapshot", self.frame))
        return SimpleNamespace(frame=self.frame, timestamp=SimpleNamespace(delta_seconds=0.05, elapsed_seconds=self.frame * 0.05))

    def tick(self, **kwargs):
        self.frame += 1
        self.calls.append(("tick", self.frame))
        if not self.refresh_stays_stale:
            self.omit_owned_sensors = False
        return self.frame

    def get_settings(self):
        return self.settings

    def get_weather(self):
        return self.weather

    def set_weather(self, value):
        self.calls.append(("restore_weather", value))
        self.weather = value

    def apply_settings(self, value):
        self.calls.append(("restore_settings", value))
        self.settings = value
        return 321


class Harness:
    def __init__(self, module, tempdir):
        self.module = module
        self.world = World()
        self.original_actors = [Actor(self.world, i, "traffic.traffic_light") for i in range(1, 24)]
        original = {
            "world_id": self.world.id, "map": self.world.map.copy(),
            "actors": module.identities(self.original_actors),
            "settings": "original-settings", "weather": "original-weather",
        }
        self.owned = {}
        for role, actor_id, type_id in (
            ("ego", 248, "vehicle.tesla.model3"),
            ("lead", 249, "vehicle.tesla.model3"),
            ("regular", 250, "sensor.lidar.ray_cast"),
            ("semantic", 251, "sensor.lidar.ray_cast_semantic"),
        ):
            parent = self.owned["ego"] if type_id.startswith("sensor.") else None
            self.owned[role] = Actor(self.world, actor_id, type_id, f"axiom_self_hit_{NONCE}_{role}", parent)
        expected = {
            role: {"type_id": actor.type_id, "role_name": actor.attributes["role_name"],
                   "returned_actor_id": actor.id, "parent_actor_id": actor.parent.id if actor.parent else None}
            for role, actor in self.owned.items()
        }
        self.journal = Journal()
        self.gate = module.CallbackGate()
        reservation = {"execution_id": "diagnostic_mock", "request_hash": "mock-request", "store_id": "mock-store"}
        root = Path(tempdir)
        self.quarantine = root / "quarantine.json"
        self.quarantine.write_bytes(module.canonical(reservation))
        self.reservation_bytes = self.quarantine.read_bytes()
        self.report = {"cleanup": {"status": "not_started", "errors": []},
                       "requested_settings": self.world.settings, "requested_weather": self.world.weather,
                       "inventory_refresh_ticks": 0}
        self.ns = extract_cleanup(module, {
            "world": self.world, "client": SimpleNamespace(get_world=lambda: self.world),
            "original": original, "original_settings": original["settings"], "original_weather": original["weather"],
            "expected": expected, "returned": self.owned, "sensors": {r: self.owned[r] for r in ("regular", "semantic")},
            "changed": True, "reservation_owned": True, "reservation": reservation,
            "quarantine": self.quarantine, "execution_root": root, "nonce": NONCE,
            "recorder": self.journal, "report": self.report, "callback_gate": self.gate,
            "callbacks_drained": False, "deadline": time.monotonic() + 1,
            "guard": self.guard, "read": self.read,
            "map_identity": lambda world: world.map.copy(),
            "setting_fields": lambda value, fields: value,
            "SETTINGS": (), "WEATHER": (), "CLEANUP_SECONDS": 0.1,
        })

    def guard(self):
        remaining = self.ns["deadline"] - time.monotonic()
        self.module.require(remaining > 0, "Injected bounded test deadline exceeded")
        return remaining

    def read(self, function):
        self.guard()
        value = function()
        self.guard()
        return value

    def cleanup(self):
        self.ns["cleanup"]()
        return self.report["cleanup"]

    def refresh(self):
        ns = extract_cleanup(self.module, self.ns, function_name="inventory_check")
        ns["event_call"] = lambda name, function, data=None: function()
        ns = extract_cleanup(self.module, ns, function_name="refresh_spawn_inventory")
        ns["refresh_spawn_inventory"]()

    def operations(self):
        return [x for x in self.world.calls if x[0] in ("stop", "destroy", "restore_weather", "restore_settings")]


class DiagnosticCounterexamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = Harness(self.module, self.tmp.name)

    def assert_uncertain_retained(self):
        self.assertEqual(self.h.report["cleanup"]["status"], "uncertain")
        self.assertTrue(self.h.ns["reservation_owned"])
        self.assertEqual(self.h.quarantine.read_bytes(), self.h.reservation_bytes)

    def test_stale_inventory_cannot_skip_either_retained_sensor(self):
        self.h.world.omit_owned_sensors = True
        self.h.cleanup()
        operations = self.h.operations()
        for actor_id in (250, 251):
            self.assertEqual(operations.count(("stop", actor_id)), 1)
            self.assertEqual(operations.count(("destroy", actor_id)), 1)
            self.assertLess(operations.index(("stop", actor_id)), operations.index(("destroy", actor_id)))
            for vehicle_id in (248, 249):
                self.assertLess(operations.index(("destroy", actor_id)), operations.index(("destroy", vehicle_id)))
        self.assertEqual(self.h.report["cleanup"]["status"], "confirmed")
        self.assertEqual(set(self.h.world.actors), set(range(1, 24)))
        self.assertFalse(self.h.quarantine.exists())
        run_outer_finalizer(self.h.ns)
        self.assertTrue(self.h.journal.stream.closed)

    def test_delayed_sensor_inventory_has_one_bounded_prelisten_refresh(self):
        self.h.world.omit_owned_sensors = True
        self.h.refresh()
        self.assertEqual(self.h.report["inventory_refresh_ticks"], 1)
        self.assertEqual([x for x in self.h.world.calls if x[0] == "tick"], [("tick", 101)])
        self.assertEqual(self.h.gate.snapshot()["started"], 0)

    def test_still_stale_inventory_fails_after_one_tick_without_retry(self):
        self.h.world.omit_owned_sensors = True
        self.h.world.refresh_stays_stale = True
        with self.assertRaisesRegex(RuntimeError, "Inventory still differs"):
            self.h.refresh()
        self.assertEqual([x for x in self.h.world.calls if x[0] == "tick"], [("tick", 101)])
        with self.assertRaisesRegex(RuntimeError, "Only one"):
            self.h.refresh()
        self.assertEqual([x for x in self.h.world.calls if x[0] == "tick"], [("tick", 101)])

    def test_foreign_actor_prevents_inventory_refresh_tick(self):
        self.h.world.omit_owned_sensors = True
        Actor(self.h.world, 999, "vehicle.foreign", "foreign-nonce")
        with self.assertRaisesRegex(RuntimeError, "not limited"):
            self.h.refresh()
        self.assertFalse(any(x[0] == "tick" for x in self.h.world.calls))

    def test_missing_vehicle_is_not_treated_as_sensor_cache_lag(self):
        self.h.world.omit_all_owned = True
        with self.assertRaisesRegex(RuntimeError, "not limited"):
            self.h.refresh()
        self.assertFalse(any(x[0] == "tick" for x in self.h.world.calls))

    def test_fully_stale_inventory_cannot_substitute_for_owned_destroy_ack(self):
        self.h.world.omit_all_owned = True
        self.h.owned["semantic"].destroy_effect_then_error = True
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertIn(("destroy", 251), self.h.operations())
        self.assertNotIn(251, self.h.world.actors)

    def test_destroy_false_ack_is_uncertain_even_when_inventory_omits_sensor(self):
        self.h.world.omit_owned_sensors = True
        self.h.owned["semantic"].destroy_false = True
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertTrue(self.h.owned["semantic"].is_alive)
        self.assertNotIn(("destroy", 248), self.h.operations())

    def test_stop_rpc_failure_does_not_skip_other_sensor_or_certify_cleanup(self):
        self.h.owned["regular"].stop_error = True
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertIn(("stop", 251), self.h.operations())
        self.assertIn(("destroy", 251), self.h.operations())
        # A successful destroy ACK supersedes inability to observe stop return,
        # but the lost RPC acknowledgement still forbids cleanup certification.
        self.assertIn(("destroy", 250), self.h.operations())

    def test_episode_change_preserves_all_actors_and_world(self):
        self.h.world.id = "foreign-episode"
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertEqual(self.h.operations(), [])

    def test_nonce_mismatch_preserves_reused_actor(self):
        self.h.owned["regular"].attributes["role_name"] = "foreign_nonce_regular"
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertNotIn(("stop", 250), self.h.operations())
        self.assertNotIn(("destroy", 250), self.h.operations())
        self.assertTrue(self.h.owned["regular"].is_alive)

    def test_parent_mismatch_preserves_sensor(self):
        self.h.owned["regular"].parent = self.h.owned["lead"]
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertNotIn(("destroy", 250), self.h.operations())

    def test_foreign_actor_is_preserved_and_prevents_world_restoration(self):
        foreign = Actor(self.h.world, 999, "vehicle.foreign", "foreign-nonce")
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertTrue(foreign.is_alive)
        self.assertNotIn(("destroy", 999), self.h.operations())
        self.assertFalse(any(x[0].startswith("restore_") for x in self.h.operations()))

    def test_visible_id_conflict_cannot_be_deleted_via_stale_retained_handle(self):
        # Retained metadata still says ours, but the current inventory explicitly
        # contradicts that identity. No stale handle may authorize this ID.
        foreign = Actor(self.h.world, 250, "sensor.lidar.ray_cast", "foreign-nonce", self.h.owned["ego"])
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertIs(self.h.world.actors.get(250), foreign)
        self.assertNotIn(("stop", 250), self.h.operations())
        self.assertNotIn(("destroy", 250), self.h.operations())

    def test_foreign_reservation_cannot_be_released(self):
        marker = b'{"foreign":"reservation"}'
        self.h.quarantine.write_bytes(marker)
        self.h.cleanup()
        self.assertEqual(self.h.report["cleanup"]["status"], "uncertain")
        self.assertTrue(self.h.ns["reservation_owned"])
        self.assertEqual(self.h.quarantine.read_bytes(), marker)

    def test_no_new_snapshot_after_async_restore_cannot_certify_stale_inventory(self):
        self.h.world.freeze_async_snapshot = True
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertNotIn("observed", self.h.report["cleanup"])

    def test_journal_failure_does_not_prevent_owned_cleanup_attempts(self):
        self.h.journal.fail = True
        self.h.cleanup()
        self.assert_uncertain_retained()
        for actor_id in (250, 251):
            self.assertIn(("stop", actor_id), self.h.operations())
            self.assertIn(("destroy", actor_id), self.h.operations())

    def test_rejected_late_callback_never_accesses_closed_journal(self):
        self.h.gate.stop_accepting()
        self.h.journal.stream.close()
        namespace = extract_cleanup(self.module, self.h.ns, function_name="callback")
        namespace["callback"]("regular")(object())
        self.assertEqual(self.h.journal.entries, [])
        self.assertEqual(self.h.gate.snapshot()["active"], 0)
        self.assertEqual(self.h.gate.snapshot()["rejected_after_stop"], 1)

    def test_active_callback_blocks_vehicle_cleanup_and_reservation_release(self):
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()

        def callback():
            accepted = self.h.gate.begin()
            if not accepted:
                return
            entered.set()
            try:
                release.wait(2)
                self.h.journal.event("injected_callback_completion", {})
            finally:
                self.h.gate.end()
                exited.set()

        thread = threading.Thread(target=callback)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(3)))
        self.assertTrue(entered.wait(1))
        self.h.cleanup()
        self.assert_uncertain_retained()
        self.assertFalse(self.h.ns["callbacks_drained"])
        run_outer_finalizer(self.h.ns)
        self.assertFalse(self.h.journal.stream.closed)
        self.assertNotIn(("destroy", 248), self.h.operations())
        self.assertFalse(self.h.gate.begin(), "Shutdown must close entry to new callbacks")
        release.set()
        self.assertTrue(exited.wait(1))
        thread.join(1)
        self.assertTrue(self.h.gate.drain(0.1))
        self.assertEqual(self.h.journal.entries[-1][0], "injected_callback_completion")
