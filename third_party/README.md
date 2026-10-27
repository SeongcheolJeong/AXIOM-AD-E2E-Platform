# Preserved upstream notices

These are exact notice files associated with the pinned recording and sensor dependencies,
not a complete release SBOM or a license for the platform's own code/data.

| File | Version/source |
| --- | --- |
| `licenses/mcap-1.4.0.txt` | MCAP 1.4.0 upstream commit `b33fa682a5c517b1d213faeabd118e0b4f9d9d93`; [original LICENSE](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/LICENSE) |
| `licenses/lz4-4.4.5.txt` | Exact `lz4-4.4.5.dist-info/licenses/LICENSE` from the installed PyPI wheel |
| `licenses/zstandard-0.25.0.txt` | Exact `zstandard-0.25.0.dist-info/licenses/LICENSE` from the installed PyPI wheel |
| `licenses/pillow-12.3.0.txt` | Exact `pillow-12.3.0.dist-info/licenses/LICENSE` from the installed `cp312-cp312-macosx_11_0_arm64` PyPI wheel; Pillow MIT-CMU plus bundled dependency notices. Runtime PNG/JPEG decoding/PNG encoding |
| `licenses/mcap-ros2-support-0.5.7.txt` | Upstream release tag `releases/python/mcap-ros2-support/v0.5.7`, commit `b13eb43cec62fd812689759a54eadcb6a0c64bfd`; [exact MIT LICENSE](https://github.com/foxglove/mcap/blob/b13eb43cec62fd812689759a54eadcb6a0c64bfd/LICENSE). Test/example-only independent ROS2 fixture writer; not the production payload decoder |

The MCAP and mcap-ros2-support wheels do not themselves include their upstream
license file. The pinned source notices are preserved here separately. Native codec libraries and each
chosen redistribution artifact still need release-specific inventory. Frontend
font notices are preserved separately in `frontend/public/licenses/`.

Sensor dependency notices were checked on **2026-09-13**. The complete Pillow
wheel notice was preserved, including its bundled codec/library conditions;
calling Pillow MIT-CMU does not replace those component notices. Its SHA-256 is
`dda12a98c1979cf3d94df1cff45d27a4cb3f04a60c76f76902ac54cac03ec0ce`.
The mcap-ros2-support source notice SHA-256 is
`da11235665c17d4c1634072dae92b8ba1b38d6fdde2ccf19a6bbede33253f58d`.

The platform's fixed CDR1 reader, bounded payload validation, fixture message
definitions and CARLA JSON schema descriptions were authored here against public
standard layouts. They do not embed or execute an uploaded schema as Python
code. Foxglove schema layouts and ROS2 sensor_msgs definitions are referenced
with their own [MIT](https://github.com/foxglove/schemas/blob/main/LICENSE) and
[Apache-2.0 package declaration](https://github.com/ros2/common_interfaces/blob/jazzy/sensor_msgs/package.xml)
respectively; this is limited-format interoperability, not blanket support for
all Foxglove/ROS messages. The selected runtime dependencies are recorded in
`requirements.lock`; example/test use is distinguished in `backend/pyproject.toml`.
Code notices grant no rights to third-party recordings, photographs, datasets,
maps, engine assets or sensor calibration data.

See [the component register](../docs/open-source-register.md) and
[MCAP source notes](../docs/mcap-format-notes.md) and
[sensor format notes](../docs/sensor-format-notes.md) for scope and provenance.

## Optional inference dependencies

[Perception notice manifest](licenses/perception/manifest.json) records exact
source/wheel notice bytes and SHA-256 for ONNX 1.22.0, ONNX Runtime 1.30.0,
NumPy 2.5.3, OpenCV-headless 5.0.0.93, protobuf 7.36.1, ml_dtypes 0.6.0,
FlatBuffers 25.12.19 and the selected OpenCV Zoo YOLOX directory. This includes
bundled third-party notices from the installed macOS wheels; other distribution
artifacts need their own release inventory. FlatBuffers notice comes from its
[pinned upstream tag](https://github.com/google/flatbuffers/blob/v25.12.19/LICENSE).
The selected model notice comes from its
[pinned directory](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/LICENSE).
Weights and COCO images are not bundled. The original pixel-rule fixture and
images are project-authored and inherit no third-party model accuracy claims.

## Optional detection evaluator

The exact [pycocotools notice](../integrations/coco/third_party/pycocotools-license.txt) accompanies the pinned
`pycocotools==2.0.11` evaluator. It uses the BSD 2-clause terms and upstream
FreeBSD disclaimer; this code license does not license COCO annotation or image
data. No COCO dataset is included. [Source and metric notes](../docs/detection-evaluation-notes.md)
pin the implementation, explain stored-prediction limits, and separate dataset
rights from evaluator code. NumPy notices remain in the perception manifest.

FPGA Lab notices (2026-10-13): `licenses/react-flow-12.10.1.txt` is the exact
MIT notice from installed `@xyflow/react` 12.10.1; `licenses/simpy-4.1.1.txt`
is the exact MIT notice from installed SimPy 4.1.1. Transitive dependency versions
remain in the frontend lockfile. These code licenses do not grant rights to
LabVIEW assets, FPGA vendor IP, MIPI specifications, Serializer models or external
RAW datasets. FPGA Lab includes only an authored synthetic ramp pattern.
