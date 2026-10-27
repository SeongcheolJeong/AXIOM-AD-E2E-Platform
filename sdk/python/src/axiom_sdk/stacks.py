"""Immutable component stacks and recorded open-loop decision evidence."""
from __future__ import annotations

import math
from pathlib import Path
import re
import time
from typing import Any

from .datasets import STATUSES, _text
from .perception import TERMINAL, _identity, _integer


class StackClientMixin:
    def stack_components(self) -> list[dict[str, Any]]:
        return self._request("GET", "/stack-components").json()

    def stacks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/stacks").json()

    def create_stack(self, **definition: Any) -> dict[str, Any]:
        return self._request("POST", "/stacks", json=definition).json()

    def get_stack(self, stack_id: str, *, revision: int | None = None) -> dict[str, Any]:
        stack_id = _identity(stack_id)
        params = {} if revision is None else {"revision": _integer(revision, "revision", 1, 2**31 - 1)}
        result = self._request("GET", f"/stacks/{stack_id}", params=params).json()
        if not isinstance(result, dict) or result.get("id") != stack_id:
            raise ValueError("Stack identity mismatch")
        actual = _integer(result.get("revision"), "revision", 1, 2**31 - 1)
        if revision is not None and actual != revision:
            raise ValueError("Stack revision identity mismatch")
        return result

    def stack_revisions(self, stack_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/stacks/{_identity(stack_id)}/revisions").json()

    def revise_stack(self, stack_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        return self._request("POST", f"/stacks/{_identity(stack_id)}/revisions", json={
            **definition, "expected_revision": _integer(expected_revision, "expected_revision", 1, 2**31 - 1)}).json()

    def export_stack(self, stack_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        stack = self.get_stack(stack_id, revision=revision)
        return self._download_perception(f"/stacks/{_identity(stack_id)}/export", destination,
            params={"revision": stack["revision"]}, sha256=stack["manifest_hash"],
            size_bytes=stack["manifest_size_bytes"], maximum=1024 * 1024)

    def stack_replay_sources(self, recording_id: str) -> dict[str, Any]:
        recording_id = _identity(recording_id)
        result = self._request("GET", f"/stack-replay-sources/{recording_id}").json()
        if not isinstance(result, dict) or result.get("recording_id") != recording_id:
            raise ValueError("Stack replay recording identity mismatch")
        return result

    def submit_stack_replay(self, *, label: str, stack_id: str, stack_revision: int, recording_id: str,
                            binding_revision: int, lidar_channel_id: int, ego_channel_id: int,
                            ego_ordinals: list[int], idempotency_key: str,
                            ego_decoder_id: str = "axiom-ego-json", ego_decoder_version: str = "1",
                            ego_log_time_offset_ns: str = "0") -> dict[str, Any]:
        """Calculate commands from pinned recorded inputs; commands do not move a vehicle.

        Source order and exact clock offset are preserved. After a lost reply,
        explicitly retry this endpoint with the same key and identical body.
        """
        headers = self._intent_headers(idempotency_key)
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        _text(label, "label")
        _identity(stack_id)
        _identity(recording_id)
        _integer(stack_revision, "stack_revision", 1, 2**31 - 1)
        _integer(binding_revision, "binding_revision", 1, 2**31 - 1)
        _integer(lidar_channel_id, "lidar_channel_id", 0, 65535)
        _integer(ego_channel_id, "ego_channel_id", 0, 65535)
        if lidar_channel_id == ego_channel_id:
            raise ValueError("LiDAR and ego channels must be distinct")
        if not isinstance(ego_ordinals, list) or not 1 <= len(ego_ordinals) <= 128:
            raise ValueError("ego_ordinals must contain 1..128 explicit file ordinals")
        for ordinal in ego_ordinals:
            _integer(ordinal, "ego ordinal", 0, 99999)
        if len(set(ego_ordinals)) != len(ego_ordinals):
            raise ValueError("Ego ordinals must be unique; supplied order is preserved")
        if (ego_decoder_id, ego_decoder_version) != ("axiom-ego-json", "1"):
            raise ValueError("Unsupported ego decoder identity")
        if (not isinstance(ego_log_time_offset_ns, str)
                or not re.fullmatch(r"0|-?[1-9][0-9]{0,19}", ego_log_time_offset_ns)
                or abs(int(ego_log_time_offset_ns)) > 2**64 - 1):
            raise ValueError("Ego offset must be an exact canonical signed decimal string with uint64 magnitude")
        return self._request("POST", "/stack-replays", headers=headers, json={
            "label": label, "stack_id": stack_id, "stack_revision": stack_revision,
            "recording_id": recording_id, "binding_revision": binding_revision,
            "lidar_channel_id": lidar_channel_id, "ego_channel_id": ego_channel_id,
            "ego_decoder_id": ego_decoder_id, "ego_decoder_version": ego_decoder_version,
            "ego_log_time_offset_ns": ego_log_time_offset_ns, "ego_ordinals": list(ego_ordinals)}).json()

    def stack_replays(self, *, status: str | None = None, limit: int = 100,
                       before_seq: int | None = None) -> dict[str, Any]:
        _integer(limit, "limit", 1, 100)
        if before_seq is not None:
            _integer(before_seq, "before_seq", 1, 2**63 - 1)
        if status is not None and status not in STATUSES:
            raise ValueError("Unsupported stack replay job status")
        return self._request("GET", "/stack-replays", params={key: value for key, value in {
            "status": status, "limit": limit, "before_seq": before_seq}.items() if value is not None}).json()

    def get_stack_replay(self, replay_id: str) -> dict[str, Any]:
        replay_id = _identity(replay_id)
        result = self._request("GET", f"/stack-replays/{replay_id}").json()
        if not isinstance(result, dict) or result.get("id") != replay_id:
            raise ValueError("Stack replay identity mismatch")
        return result

    def cancel_stack_replay(self, replay_id: str) -> dict[str, Any]:
        return self._request("POST", f"/stack-replays/{_identity(replay_id)}/cancel").json()

    def wait_stack_replay(self, replay_id: str, *, timeout: float = 180,
                          poll_interval: float = .5) -> dict[str, Any]:
        """GET-only polling; timeout does not cancel or resubmit an uncertain job."""
        _identity(replay_id)
        for key, value in (("timeout", timeout), ("poll_interval", poll_interval)):
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0 or (key == "poll_interval" and value == 0)):
                raise ValueError("timeout must be finite/nonnegative and poll_interval finite/positive")
        deadline = time.monotonic() + timeout
        while True:
            replay = self.get_stack_replay(replay_id)
            if replay.get("status") not in STATUSES:
                raise ValueError("Invalid stack replay lifecycle status")
            if replay["status"] in TERMINAL:
                return replay
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Stack replay {replay_id} is still {replay['status']}; not cancelled or resubmitted")
            time.sleep(min(poll_interval, remaining))

    def export_stack_replay(self, replay_id: str, destination: str | Path) -> Path:
        replay = self.get_stack_replay(replay_id)
        if replay.get("status") not in TERMINAL:
            raise ValueError("Stack replay export requires a terminal sealed job")
        return self._download_perception(f"/stack-replays/{_identity(replay_id)}/export", destination,
            sha256=replay.get("manifest_hash"), size_bytes=replay.get("manifest_size_bytes"), maximum=32 * 1024 * 1024)

    def export_stack_replay_points(self, replay_id: str, tick_index: int, destination: str | Path) -> Path:
        """Save the complete consumed ego-frame XYZ float32 LE bytes, verifying SHA-256."""
        _integer(tick_index, "tick_index", 0, 127)
        replay = self.get_stack_replay(replay_id)
        ticks = replay.get("ticks")
        if not isinstance(ticks, list):
            raise ValueError("Replay response has no tick evidence")
        selected = [tick for tick in ticks if isinstance(tick, dict) and type(tick.get("index")) is int and tick["index"] == tick_index]
        if len(selected) != 1 or not isinstance(selected[0].get("points"), dict):
            raise ValueError("Selected replay tick has no unique consumed point artifact")
        points = selected[0]["points"]
        count = _integer(points.get("point_count"), "point_count", 0, 20000)
        size = _integer(points.get("size_bytes"), "size_bytes", 0, 20000 * 12)
        if points.get("encoding") != "xyz_float32_le" or count * 12 != size:
            raise ValueError("Point artifact encoding or complete point count is inconsistent")
        return self._download_perception(f"/stack-replays/{_identity(replay_id)}/ticks/{tick_index}/points", destination,
            sha256=points.get("sha256"), size_bytes=size, maximum=20000 * 12, allow_empty=True)
