# Optional NGII geospatial adapter

`axiom-geospatial` inspects one bounded NGII-style SHP ZIP and creates a retained
lane network plus OpenDRIVE 1.6 with pinned `scenariogeneration`. It imports no
simulator, runs no service, and writes no files. Core must retain the exact upload
bytes and its dataset/license/source metadata. Public GIS files and this code
have separate licenses; this adapter grants no map or presentation rights.

```python
from axiom_geospatial import inspect_ngii_zip, capabilities

report = inspect_ngii_zip(raw_zip_bytes, source_name="User-provided source", options={
    "selected_link_ids": ["source-link-id"],
})
# report["ready"] is conversion readiness only, NEVER native driving acceptance.
# Errors return diagnostics and xodr_text=None, retaining the source SHA and
# available member inventory. No guessed connectivity or fallback roads.
```

## Supported input and evidence

- A1_NODE Point/PointZ, A2_LINK single-part PolyLine/PolyLineZ, and explicitly
  associated B2_SURFACELINEMARK lane boundaries. `.shp/.shx/.dbf` are mandatory;
  `.prj` is mandatory unless the caller explicitly declares `source_crs`.
- Field names are case/underscore insensitive. A2 node aliases: `FromNodeID`,
  `F_NODE`, `F_NODEID`; `ToNodeID`, `T_NODE`, `T_NODEID`. IDs and all DBF attributes
  are retained. B2 `R_LinkID` identifies a lane on the marking's right (that
  lane's left boundary); `L_LinkID` supplies its right boundary.
- `.cpg` or explicit encoding is honored strictly; without either, UTF-8 then
  CP949 decoding is attempted and the successful encoding is recorded. A
  conflicting `.cpg`/explicit declaration is rejected.
- `pyproj` applies an offline, `always_xy`, no-ballpark XY transformation into a
  projected metre CRS. Source/target WKT, operation, stated accuracy, inverse
  residual, encoding, local origin, and dependency versions are retained.
- Source coordinates and local coordinates remain separate. Source Z is
  retained as NGII metres, without establishing its vertical datum or doing a
  vertical conversion. 2D data is blocked by default.
- Exact shared A1 IDs define directional connectivity. F/T ordering and
  endpoint geometry must agree within 0.2 m; no automatic reverse or snap.
- B2 cross-sections sampled at most 1 m apart derive variable widths. Required
  range is 1.5–8 m and centre displacement at most 0.5 m. Endpoint projection
  tangential gaps up to 0.02 m are explicitly recorded as derived estimates.
- Every original A2 polyline segment is written as a line; XY reference error
  against that source polyline is zero. Source elevation is piecewise linear.
  This preserves the source rather than asserting a continuously smooth road.
- One directed A2 link becomes one OpenDRIVE road with lane -1. Adjacent lane
  changes, lane grouping, marking semantics, traffic controls, buildings,
  textures and physical road validity are outside this version.

## Options and authored values

`NgiiImportOptions.model_validate(dict)` accepts only these keys:

| Key | Meaning |
| --- | --- |
| `selected_link_ids` | Explicit whole-link subset; no geometry clipping. Default all links. |
| `source_crs` | Explicit CRS if PRJ missing; must agree with existing PRJ. |
| `target_crs` | Projected metre output CRS. Default source CRS if projected metres. |
| `local_origin` | Three values `[x,y,z]` in target coordinates. Default first selected link's first point. |
| `encoding` | Explicit strict DBF encoding. Must agree with CPG. |
| `allow_2d` | Explicitly authorize flat Z=0 for missing heights; always flagged as authored. |
| `lane_width_overrides_m` | `{source_link_id: width}`; explicit 1.5–8 m widths, always marked unmeasured authoring. |
| `junction_links` | `{junction_id: [connector_source_id, ...]}`; explicit authored junction membership. |

Branches/merges without explicit connector membership are blocked. Each
junction connector must have exactly one selected ordinary entry and exit;
there is no invented turn curve. Junction membership is an authored option,
not a claim that generic NGII node sharing identifies legal turn permissions.

The local origin is preserved in report and OpenDRIVE `userData`. It is **not**
emitted as a standard header offset, which would reapply national coordinates.
No misleading absolute `geoReference` is emitted for the shifted local frame.
Native GNSS/geolocation remains unqualified; consumers must use the recorded
reversible transform. The output uses projected XY, with no hidden Y reflection.

ZIP bounds: 32 MiB compressed, 128 MiB total expanded, 32 MiB/member, 200:1 maximum
compression ratio, 512 members, no path traversal/duplicates/symlinks/encryption.
Input bounds: 100,000 features/layer and 200,000 vertices/layer. Conversion:
1–256 selected links, 5 km total lane length and 5 km footprint. These are safety
bounds, not a statement that every input within them is supported.

## Focused checks

Install this optional package in an isolated test environment and run:

```sh
python -m pytest integrations/geospatial/tests
```

Tests generate **synthetic semantic fixtures**, including an artificial 400 m
two-link network and an authored branch. They check geometry, elevation, CRS,
source identity, Korean encoding, explicit authoring, ZIP limits and rejection
paths. They are not actual NGII acquisition, real-road fidelity, CARLA execution,
sensor validation or commercial release evidence.
