"""AXIOM opt-in plan guards and native point-center diagnostic evidence.

Installed only in an isolated CameraE2E source copy. The original API/source
remain untouched. Physical footprint, road labels and product ranking are not
established by these model calculations.
"""
from __future__ import annotations

import io
import re
from typing import Any

import numpy as np
from PIL import Image

from .registry import ConflictError, IntegrityError, canonical, digest

CONTRACT = {
    "schema": "camera_lens_sensor_point_center_contract_v1",
    "scope": "central_slanted_edge_model_diagnostic_only",
    "coordinate_model": "strict_raytrace_radial_geometry_inverse_piecewise_linear",
    "coordinate_convention": "row_positive_down_col_positive_right_degrees",
    "source_wavelength_nm": [450.0, 550.0, 650.0],
    "comparison_map_wavelength_nm": 550.0,
    "pixel_integrated_effective_area_matched": False,
    "physical_validation": "not_established",
    "product_ranking_allowed": False,
    "perception_label_correspondence": "not_established",
}


def plan(service: Any, payload: dict, kind: str) -> dict:
    from . import lens_sensor_raw_job as raw, lens_sensor_quality_job as quality

    if not isinstance(payload, dict) or set(payload) != {"specification"}:
        raise ValueError("Declare exactly specification for the versioned native plan.")
    frozen = (raw if kind == "raw" else quality).prepare(service, payload["specification"])
    if kind == "raw" and (
        frozen["specification"]["schema"] != raw.DENSE_COMMON_SCENE_SCHEMA
        or frozen["specification"]["scene_kind"] != "slanted_edge"
    ):
        raise ValueError("This planned path supports only v5 dense central slanted edge.")
    if kind == "quality" and frozen["specification"]["schema"] != quality.SCHEMA_V2:
        raise ValueError("This planned path requires quality-study v2.")
    return {
        "schema": f"camera_lens_sensor_{kind}_plan_v1",
        "plan_hash": digest(frozen), "frozen_request": frozen,
        "output_contract": CONTRACT,
    }


def submit_planned(service: Any, payload: dict, kind: str) -> dict:
    if (
        not isinstance(payload, dict) or set(payload) != {"specification", "plan_hash"}
        or not isinstance(payload["plan_hash"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", payload["plan_hash"])
    ):
        raise ValueError("Declare specification and the exact native plan_hash.")
    fresh = plan(service, {"specification": payload["specification"]}, kind)
    if fresh["plan_hash"] != payload["plan_hash"]:
        raise ConflictError("Native lens/sensor source, configuration or executable changed after preview.")
    # Registry persists the accepted hash with the original immutable prepared
    # fields. The execution's fresh prepare check still verifies every field.
    document = {**fresh["frozen_request"], "accepted_plan_hash": fresh["plan_hash"],
                "planned_output_contract": CONTRACT}
    return service.registry.submit(document, digest(document))


def diagnostic_export(service: Any, identity: str) -> dict:
    from pyisetcam.dataset import _sha256_array
    from . import lens_sensor_quality_job as quality
    from .lens_sensor_common_angular_mtf import _grid_coordinates, _sample_all
    from .lens_sensor_quality_study import SLOTS

    job = quality.get(service, identity)  # verifies sealed original RAW/ISP/metrics
    if job["status"] != "succeeded" or not job.get("accepted_plan_hash"):
        raise ValueError("A completed planned quality study is required.")
    frozen = {key: job[key] for key in quality.prepare(service, job["specification"])}
    if digest(frozen) != job["accepted_plan_hash"]:
        raise IntegrityError("Sealed quality plan hash differs from original inputs.")
    summary = job["result"]["summary"]
    arrays: dict[str, np.ndarray] = {}
    previews = []
    coordinate_descriptors = {}
    with np.load(service.registry.blob_path(summary["evidence_arrays"]["sha256"]), allow_pickle=False) as sealed:
        images = {slot: sealed[f"{slot}_linear_isp_rgb"].copy() for slot in SLOTS}
        masks = {slot: sealed[f"{slot}_valid_rgb_mask"].copy() for slot in SLOTS}
    for slot in SLOTS:
        conditions = summary["cases"][slot]["conditions"]
        point = conditions["native_output_angular_grid"]["distortion_aware_point_center"]
        coordinates = np.asarray(point["coordinates_deg_row_col_by_wavelength"], dtype=np.float64)
        if coordinates.shape != (3, *images[slot].shape[:2], 2) or not np.isfinite(coordinates).all():
            raise IntegrityError("Native point-center coordinates differ from retained RGB geometry.")
        arrays[f"{slot}_coordinates_deg_row_col_by_wavelength"] = coordinates
        coordinate_descriptors[slot] = {"shape": list(coordinates.shape), "dtype": "float64",
                                        "array_sha256": _sha256_array(coordinates), "contract": point}

    def png(array, name, slot, stage):
        data = np.rint(np.clip(array, 0, 1) * 255).astype(np.uint8)
        stream = io.BytesIO(); Image.fromarray(data).save(stream, format="PNG")
        descriptor = service.registry.store_blob(stream.getvalue(), name, "image/png")
        descriptor['url'] = f'/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-artifacts/{descriptor["id"]}'
        previews.append({"slot": slot, "stage": stage, "artifact": descriptor,
                         "display_transform": "clip_linear_rgb_0_1_then_round_to_uint8_no_auto_gain"})

    for slot in SLOTS:
        png(images[slot], f"{slot}-native-fixed-isp.png", slot, "native_fixed_isp")
    common = summary["comparison"].get("common_grid_object_angle_edge_mtf50") or {}
    if common.get("available") is True:
        grid = common["grid"]
        coordinates = _grid_coordinates(summary["cases"], grid)
        if coordinates is None or not np.isfinite(coordinates).all():
            raise IntegrityError("Saved common grid cannot be reconstructed.")
        sampled = _sample_all(summary["cases"], images, masks,
                              summary["comparison"]["matched_object_angle_edge_mtf50"],
                              coordinates.reshape(-1, 2), tuple(coordinates.shape[:2]),
                              tuple(grid["step_deg_row_col"]))
        if sampled is None:
            raise IntegrityError("Native common-grid sampling leaves retained valid support.")
        arrays["common_coordinates_deg_row_col"] = coordinates
        for slot in SLOTS:
            arrays[f"{slot}_common_angular_rgb"] = sampled[0][slot]
            arrays[f"{slot}_common_valid_mask"] = np.ones(coordinates.shape[:2], dtype=bool)
            png(sampled[0][slot], f"{slot}-common-angular-fixed-isp.png", slot, "common_550nm_point_grid")
    stream = io.BytesIO(); np.savez_compressed(stream, **arrays)
    correspondence = service.registry.store_blob(stream.getvalue(), "native-point-correspondence.npz", "application/octet-stream")
    correspondence['url'] = f'/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-artifacts/{correspondence["id"]}'
    diagnostic = {"schema": "camera_lens_sensor_native_diagnostic_export_v1", "job_id": identity,
                  "accepted_plan_hash": job["accepted_plan_hash"], "contract": CONTRACT,
                  "native_coordinates": coordinate_descriptors, "correspondence_arrays": correspondence,
                  "common_angular_diagnostic": common, "previews": previews,
                  "arrays": {key: {"shape": list(value.shape), "dtype": str(value.dtype),
                                    "array_sha256": _sha256_array(value)} for key, value in arrays.items()}}
    result = service.registry.store_blob(canonical(diagnostic).encode(), "native-correspondence-diagnostic.json", "application/json")
    result['url'] = f'/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-artifacts/{result["id"]}'
    return {"job": job, "diagnostic": diagnostic, "artifacts": [*job["artifacts"], correspondence, result,
            *(preview["artifact"] for preview in previews)]}


def install_routes(app: Any, service: Any) -> None:
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse, FileResponse
    # This isolated connector service shares only read-only source catalogs and
    # upstream assets. Its writes are limited to its own planned native queue;
    # the original CameraE2E's broader authoring APIs are intentionally absent.
    allowed_posts = {f"/api/v1/{base}/{action}" for base in ("lens-sensor-raw", "lens-sensor-quality-studies") for action in ("plan", "planned")}
    allowed_get = re.compile(r"^/(?:openapi\.json|api/v1/(?:bootstrap|session|health|artifacts/[A-Za-z0-9_-]{1,200}|lens-sensor-raw(?:/[A-Za-z0-9_-]{1,200}(?:/export)?)?|lens-sensor-quality-studies(?:/[A-Za-z0-9_-]{1,200}(?:/(?:export|diagnostic-export|diagnostic-artifacts/[A-Za-z0-9_-]{1,200}))?)?))$")
    @app.middleware("http")
    async def source_boundary(request, call_next):
        if not ((request.method == 'POST' and request.url.path in allowed_posts)
                or (request.method == 'GET' and allowed_get.fullmatch(request.url.path))):
            return JSONResponse({'error': 'Isolated native lens/sensor connector: source catalogs are read-only; only planned native jobs are supported.'}, status_code=403)
        return await call_next(request)
    def endpoints(selected):
        def preview(payload: dict):
            return plan(service, payload, selected)
        def submit(payload: dict):
            return submit_planned(service, payload, selected)
        return preview, submit
    for kind, base in (("raw", "/api/v1/lens-sensor-raw"), ("quality", "/api/v1/lens-sensor-quality-studies")):
        preview, submit = endpoints(kind)
        app.add_api_route(base + "/plan", preview, methods=["POST"], tags=["axiom planned native lens sensor"])
        app.add_api_route(base + "/planned", submit, methods=["POST"], status_code=202, tags=["axiom planned native lens sensor"])
    @app.get("/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-export", tags=["axiom planned native lens sensor"])
    def export(identity: str):
        return diagnostic_export(service, identity)
    @app.get('/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-artifacts/{artifact_identity}', tags=['axiom planned native lens sensor'])
    def diagnostic_artifact(identity: str, artifact_identity: str):
        # Generated derivative blobs are deliberately absent from the original
        # general artifact-owner index. Bind access to this exact verified
        # completed job and its deterministic diagnostic export instead.
        export = diagnostic_export(service, identity)
        expected_url = f'/api/v1/lens-sensor-quality-studies/{identity}/diagnostic-artifacts/{artifact_identity}'
        entries = [item for item in export['artifacts'] if item['id'] == artifact_identity and item.get('url') == expected_url]
        if not entries:
            raise HTTPException(404, 'Diagnostic artifact is not bound to this native quality job.')
        descriptor = entries[0]
        path = service.registry.blob_path(descriptor['sha256'])
        if path.stat().st_size != descriptor['bytes']:
            raise IntegrityError('Diagnostic artifact byte size differs from its sealed descriptor.')
        return FileResponse(path, media_type=descriptor['media_type'], filename=descriptor['name'])
