"""Sensor-data methods shared by the synchronous workspace client."""
from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any


class SensorClientMixin:
    def sensor_decoders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/sensor-decoders").json()

    def sensor_rigs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/sensor-rigs").json()

    def create_sensor_rig(self, **definition: Any) -> dict[str, Any]:
        """Save a source-declared rig; mathematical checks are not calibration validation."""
        return self._request("POST", "/sensor-rigs", json=definition).json()

    def get_sensor_rig(self, rig_id: str, *, revision: int | None = None) -> dict[str, Any]:
        return self._request("GET", f"/sensor-rigs/{rig_id}", params={"revision": revision} if revision is not None else {}).json()

    def sensor_rig_revisions(self, rig_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/sensor-rigs/{rig_id}/revisions").json()

    def revise_sensor_rig(self, rig_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        return self._request("POST", f"/sensor-rigs/{rig_id}/revisions", json={**definition, "expected_revision": expected_revision}).json()

    def sensor_channels(self, recording_id: str) -> dict[str, Any]:
        return self._request("GET", f"/recordings/{recording_id}/sensor-channels").json()

    def bind_recording_sensors(self, recording_id: str, *, expected_revision: int, **definition: Any) -> dict[str, Any]:
        """Pin one rig revision, channel identities, decoder versions and time policy.

        expected_revision=0 creates the first binding. A later rig edit does not
        modify a saved binding. Conflicts require explicit reloading and review.
        """
        return self._request("POST", f"/recordings/{recording_id}/sensor-bindings",
                             json={**definition, "expected_revision": expected_revision}).json()

    def sensor_binding(self, recording_id: str, *, revision: int | None = None) -> dict[str, Any]:
        return self._request("GET", f"/recordings/{recording_id}/sensor-bindings",
                             params={"revision": revision} if revision is not None else {}).json()

    def sensor_binding_revisions(self, recording_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/recordings/{recording_id}/sensor-bindings/revisions").json()

    def sensor_frame(self, recording_id: str, channel_id: int, ordinal: int, *, max_points: int = 20_000) -> dict[str, Any]:
        """Decode one exact message, including duplicates; this is not a clock cursor."""
        return self._request("GET", f"/recordings/{recording_id}/sensor-frame",
                             params={"channel_id": channel_id, "ordinal": ordinal, "max_points": max_points}).json()

    def sensor_bundle(self, recording_id: str, *, at_ns: int | str, binding_revision: int | None = None,
                      channel_ids: list[int] | None = None, max_age_ns: int | str | None = None,
                      max_points: int = 20_000) -> dict[str, Any]:
        """Observe latest recorded samples at/before an exact cursor.

        Time is a decimal string. Bindings must be pinned explicitly; omitting
        binding_revision never silently adopts a saved calibration revision.
        Shared log time does not prove synchronized acquisition clocks.
        """
        params: dict[str, Any] = {"at_ns": str(at_ns), "max_points": max_points}
        if binding_revision is not None:
            params["binding_revision"] = binding_revision
        if channel_ids is not None:
            params["channel_id"] = channel_ids
        if max_age_ns is not None:
            params["max_age_ns"] = str(max_age_ns)
        return self._request("GET", f"/recordings/{recording_id}/sensor-bundle", params=params).json()

    def import_run_recording(self, run_id: str, artifact_id: str) -> dict[str, Any]:
        """Import an existing MCAP artifact with a relational origin; never re-execute."""
        return self._request("POST", f"/runs/{run_id}/recordings", json={"artifact_id": artifact_id}).json()

    def recording_origin(self, recording_id: str) -> dict[str, Any] | None:
        return self._request("GET", f"/recordings/{recording_id}/origin").json()

    def _export_sensor_manifest(self, path: str, metadata: dict[str, Any], destination: str | Path) -> Path:
        # Pin the observed revision even when the authoring lookup used latest.
        # Never forward credentials to an arbitrary returned download_url.
        content = self._request("GET", path, params={"revision": metadata["revision"]}).content
        if len(content) != metadata["manifest_size_bytes"] or hashlib.sha256(content).hexdigest() != metadata["manifest_hash"]:
            raise ValueError("Sensor manifest size or SHA-256 mismatch; nothing written")
        return self._write_new(destination, content)

    def export_sensor_rig(self, rig_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        metadata = self.get_sensor_rig(rig_id, revision=revision)
        return self._export_sensor_manifest(f"/sensor-rigs/{rig_id}/export", metadata, destination)

    def export_sensor_binding(self, recording_id: str, destination: str | Path, *, revision: int | None = None) -> Path:
        metadata = self.sensor_binding(recording_id, revision=revision)
        return self._export_sensor_manifest(f"/recordings/{recording_id}/sensor-bindings/export", metadata, destination)

    def export_sensor_preview(self, recording_id: str, channel_id: int, ordinal: int,
                              destination: str | Path, *, component: str = "camera", max_points: int = 20_000) -> Path:
        """Write one SHA-256-verified PNG or float32 LE buffer to a new file.

        Components are camera, points or intensity. Points are a bounded display
        sample; inspect original_point_count/skipped_points before reusing them.
        """
        if component not in {"camera", "points", "intensity"}:
            raise ValueError("component must be camera, points or intensity")
        frame = self.sensor_frame(recording_id, channel_id, ordinal, max_points=max_points)
        if (frame.get("recording_id"), frame.get("channel_id"), frame.get("message", {}).get("ordinal")) != (recording_id, channel_id, ordinal):
            raise ValueError("Sensor preview identity mismatch; nothing written")
        decoded = frame.get("decoded")
        if not isinstance(decoded, dict):
            raise ValueError("Sensor sample has no decoded preview; nothing written")
        preview = decoded.get("camera" if component == "camera" else "point_cloud")
        if component == "intensity" and isinstance(preview, dict):
            preview = preview.get("intensity")
        if not isinstance(preview, dict):
            raise ValueError(f"Sensor sample has no {component} preview; nothing written")
        try:
            content = base64.b64decode(preview["data_base64"], validate=True)
            valid = len(content) == preview["size_bytes"] and hashlib.sha256(content).hexdigest() == preview["sha256"]
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise ValueError("Sensor preview encoding is invalid; nothing written") from exc
        if not valid:
            raise ValueError("Sensor preview size or SHA-256 mismatch; nothing written")
        return self._write_new(destination, content)
