# AXIOM Python SDK and CLI

CARLA 센서 recipe는 `api.simulation_sensor_profiles()` 또는 `axiom simulation-sensor-profiles`로 조회합니다. `control.sensor_profile_id`를 명시해 제출하면 유효 scenario seed와 함께 실행에 고정됩니다. [설정 선택·비교 및 버전 호환](../../docs/carla-sensor-profiles.md).

UI와 동일한 `/api/v1`을 사용하는 동기 Python client다. 엔진·DB를 직접 열지
않으며 LLM에 데이터를 전송하지 않는다. Python 3.10+가 필요하고 client 자체의
외부 의존성은 httpx다. 예제 MCAP 생성에는 저장소의 backend test 환경도 필요하다.

저장소 루트에서 전체 개발 환경을 설치하거나 SDK만 설치한다.

```sh
bash scripts/setup.sh
# SDK만 기존 Python 환경에 설치할 때
python -m pip install -e ./sdk/python
```

API의 기본 주소는 `http://127.0.0.1:8000`이다. CLI는 `--url` 또는 `AXIOM_URL`,
토큰은 `ADE2E_API_TOKEN`을 읽는다. Python에서는 URL과 token을 명시적으로
전달한다. `--url`은 CLI 하위 명령 앞에 둔다.

```sh
.venv/bin/axiom --url http://127.0.0.1:8000 health
.venv/bin/axiom sensor-decoders
.venv/bin/axiom sensor-rigs
.venv/bin/axiom recordings --query Synthetic
```

```python
import os
from axiom_sdk import AxiomClient

with AxiomClient("http://127.0.0.1:8000", token=os.getenv("ADE2E_API_TOKEN")) as api:
    print(api.sensor_decoders())
    print(api.sensor_rigs())
    print(api.recordings(query="Synthetic"))
```

## 실제 API를 사용하는 센서 예제

먼저 API를 실행한다. `scripts/dev.py`로 이미 실행 중이면 중복 기동하지 않는다.
센서 기록 조사 자체에는 실행 queue worker나 CARLA 서버가 필요하지 않다.
다음 명령은 API에 recording·rig·binding을 생성하고 새 로컬 디렉터리에 파일을
내보낸다. 재실행할 때는 사용하지 않은 출력 경로를 지정한다.

```sh
.venv/bin/python examples/sensor_workflow.py \
  --url http://127.0.0.1:8000 \
  --output .runtime/sensor-example
```

예제는 직접 작성한 장면의 ROS2 Image/PointCloud2와 Foxglove CompressedImage
MCAP를 만든다. 원본을 등록하고 rig·clock/age 선언을 고정한 뒤 PNG·XYZ preview,
원본 MCAP, rig/binding의 개정 전후 manifest와 `summary.json`을 내보낸다.
`summary.json`에는 실제 recording/rig ID, channel ID, 선택 ordinal이 남는다.
장면·pixels·points는 authored synthetic data이며 CARLA 실행·실측 센서·인지 추론
또는 calibrated sensor physics 결과가 아니다.

API 등록 없이 원본 fixture만 만들 수도 있다. 목적 파일이 이미 있으면 덮어쓰지
않는다. `--frames`의 범위는 1–61, 기본값은 31이다.

```sh
.venv/bin/python examples/generate_sensor_recording.py .runtime/synthetic-sensors.mcap --frames 31
```

## 센서 API 메서드

| 작업 | Python 메서드 |
|---|---|
| Decoder capability | `sensor_decoders()`, `sensor_channels(recording_id)` |
| Rig 작성·조회 | `create_sensor_rig(**definition)`, `sensor_rigs()`, `get_sensor_rig(id, revision=None)` |
| Rig 개정 | `revise_sensor_rig(id, expected_revision=N, **definition)`, `sensor_rig_revisions(id)` |
| Recording binding | `bind_recording_sensors(id, expected_revision=N, **definition)` |
| Binding 이력 | `sensor_binding(id, revision=None)`, `sensor_binding_revisions(id)` |
| 정확한 message | `sensor_frame(id, channel_id, ordinal, max_points=20000)` |
| Cursor 조회 | `sensor_bundle(id, at_ns=..., binding_revision=N, channel_ids=[...])` |
| Immutable export | `export_sensor_rig(id, destination, revision=N)`, `export_sensor_binding(id, destination, revision=N)` |
| Preview export | `export_sensor_preview(id, channel_id, ordinal, destination, component="camera", max_points=20000)` |
| 기존 run MCAP 연결 | `import_run_recording(run_id, artifact_id)`, `recording_origin(recording_id)` |

Binding 생성의 `expected_revision=0`은 최초 개정이다. 이후 변경은 현재 개정 번호와
완전한 새 정의를 보낸다. Rig/binding 작성에는 자동 재시도나 Idempotency-Key
계약이 없다. 409이면 이력을 다시 읽고 수정 의도를 확인한다. 나중의 rig 개정이
이미 저장된 binding을 바꾸지 않는다.

`sensor_bundle()`은 `at_ns`와 optional `max_age_ns`를 정확한 십진 문자열로 보낸다.
`binding_revision`을 생략하면 항상 unbound이며 저장된 latest binding을 추정하지
않는다. Binding을 지정하면 query `max_age_ns`로 정책을 덮을 수 없다. 채널은
최대 네 개이며 큰 binding에서는 조사할 subset을 지정한다. `max_points`는
1–100000 범위의 preview 표본 한도다.

응답은 `bundle["entries"]` 아래 각 채널의 `selection_status`, `time_relation`,
`freshness`, `age_ns`, `message`와 `frame`을 제공한다. 미래 관측은 대체하지 않고
stale preview는 생략한다. `frame["status"] == "decoded"`일 때 `frame["decoded"]`
아래 `camera` 또는 `point_cloud`를 조사한다. Capture/log/publish 시계와 source
declaration은 별개이며 같은 커서 조회가 실제 취득 동기화를 증명하지 않는다.

Preview export는 정확 ordinal·channel·recording identity 및 base64 size/SHA를
확인한다. `component`는 `camera`(PNG), `points`(XYZ interleaved float32 LE),
`intensity`(선택 point와 같은 개수의 float32 LE)다. Rig transform은 point bytes에
자동 적용되지 않는다. Foxglove message pose의 적용 여부는 decoded 결과의
`transform_policy`와 `source_pose`로 확인한다. Preview는 표본일 수 있으므로
`original_point_count`, `point_count`, `invalid_points`, `skipped_points`를 보존한다.
Manifest export는 조회한 개정을 명시적으로 pin하고 exact bytes의 hash/크기를
검증한다. Export는 기존 파일을 덮어쓰지 않으며 실패 시 자신이 만든 부분 파일만
정리한다.

## CLI 명령 형태

아래 대문자 인자는 실제 ID·숫자·경로로 바꾼다. JSON 정의 파일은
[센서 계약](../../docs/sensors-contract.md)의 완전한 작성 모델을 사용한다.
CLI에서는 파일 안에 `expected_revision`을 넣지 않고 옵션으로 전달한다.

```text
axiom create-sensor-rig RIG_JSON
axiom revise-sensor-rig RIG_ID RIG_JSON --expected-revision 1
axiom sensor-rig RIG_ID
axiom sensor-rig-revisions RIG_ID
axiom sensor-channels RECORDING_ID
axiom bind-recording-sensors RECORDING_ID BINDING_JSON --expected-revision 0
axiom sensor-binding RECORDING_ID --revision 1
axiom sensor-binding-revisions RECORDING_ID
axiom sensor-frame RECORDING_ID CHANNEL_ID ORDINAL --max-points 20000
axiom sensor-bundle RECORDING_ID --at-ns DECIMAL_NS --binding-revision 1 --channel-id CHANNEL_ID
axiom export-sensor-preview RECORDING_ID CHANNEL_ID ORDINAL image.png --component camera
axiom export-sensor-preview RECORDING_ID CHANNEL_ID ORDINAL points.xyz.f32 --component points
axiom export-sensor-rig RIG_ID rig-r1.json --revision 1
axiom export-sensor-binding RECORDING_ID binding-r1.json --revision 1
axiom import-run-recording RUN_ID ARTIFACT_ID
axiom recording-origin RECORDING_ID
```

`import-run-recording`은 이미 존재하는 `application/mcap` artifact를 가져오는
작업이며 시뮬레이터를 다시 실행하지 않는다. `recording-origin`이 null이면 run
연결이 확인되지 않은 recording이다. Engine artifact라는 사실만으로 measured
sensor data로 분류하지 않는다.

## 모델 등록과 기록 이미지의 실제 추론

Perception 메서드는 `/api/v1/perception`을 사용한다. 기존 simulation job과
같은 durable worker lifecycle을 쓰지만 driving Scenario/Run을 만들지 않는다.
SDK 자체에는 ONNX Runtime 또는 detector weights를 설치하지 않는다. API의
`perception_runtimes()`와 worker의 optional runtime 설치를 먼저 확인한다.
등록 성공은 모델의 graph 호환성이나 실행 성공을 뜻하지 않는다.

모델은 원본 bytes artifact와 해석 정의를 나누어 등록한다. 아래 `model-definition.json`은
[ModelCreate 계약](../../docs/perception-contract.md)의 완전한 정의다. 실제 graph에
맞는 profile·입력 크기·**순서가 고정된 전체 labels**·source·license·note를 작성한다.
선택한 OpenCV Zoo YOLOX-S는 `opencv_yolox_rgb_v1`, 640 × 640, COCO 80개 label
순서를 사용한다. 라이선스 필드는 검증 완료가 아닌 작성자의 선언이다.

```python
import json
import os
from pathlib import Path
from axiom_sdk import AxiomClient

with AxiomClient("http://127.0.0.1:8000", token=os.getenv("ADE2E_API_TOKEN")) as api:
    artifact = api.upload_model_artifact("weights.onnx")
    definition = json.loads(Path("model-definition.json").read_text())
    model = api.create_model(**{**definition, "artifact_id": artifact["id"]})
    job = api.submit_perception_job(
        label="Recorded camera review",
        model_id=model["id"], model_revision=model["revision"],
        recording_id="RECORDING_ID", channel_id=1, ordinals=[0, 2],
        decoder_id="ros2-cdr", decoder_version="1",
        runtime_id="onnx-cpu", seed=42,
        score_threshold=0.35, iou_threshold=0.45, max_detections=100,
        idempotency_key="camera-review-001",
    )
    result = api.wait_perception_job(job["id"])
    api.export_perception_job(result["id"], "perception-evidence.json")
    # 완료된 frame의 selection_index를 고른다. 원본 file ordinal과 구분한다.
    completed = next((frame for frame in result["frames"] if frame["status"] == "completed"), None)
    if completed is not None:
        api.export_perception_image(result["id"], completed["selection_index"], "inference-input.png")
```

`RECORDING_ID`, channel 및 ordinals는 실제 MCAP catalog에서 확인한 값으로 바꾼다.
순서가 `[0, 2]`이면 selection index는 각각 `0, 1`이다. Frame의 `completed`와
빈 `detections`는 검출 0개이며 `failed`와 다르다. 취소·중단된 job도 이미 확정한
프레임과 전체 입력 분모를 보존하고 export할 수 있다. PNG export는 추론에 사용한
**저장 input 이미지**이며 그려진 overlay를 내려받는 명령이 아니다.

| 작업 | Python 메서드 |
|---|---|
| 원본 model bytes | `upload_model_artifact(path, name=None)`, `download_model_artifact(artifact, destination)` |
| 모델 작성·head 조회 | `create_model(**definition)`, `models()`, `get_model(id, revision=None)` |
| Immutable revision | `revise_model(id, expected_revision=N, **definition)`, `model_revisions(id)` |
| 고정 model export | `export_model(id, destination, revision=N)` |
| Runtime capability | `perception_runtimes()` |
| 명시적 실행 입력 | `submit_perception_job(..., model_revision=N, ordinals=[...], idempotency_key=...)` |
| 실행 조사 | `perception_jobs(status=None, limit=100, before_seq=None)`, `get_perception_job(id)` |
| 대기·취소 | `wait_perception_job(id, timeout=120)`, `cancel_perception_job(id)` |
| 확정 증거 export | `export_perception_job(id, destination)`, `export_perception_image(id, selection_index, destination)` |
| 비교 고정 | `capture_perception_comparison(left_id, right_id, name=..., note=..., idempotency_key=...)` |
| 비교 조사·export | `perception_comparisons()`, `get_perception_comparison(id)`, `export_perception_comparison(id, destination)` |

업로드는 원본 파일을 1 MiB chunk로 전송하며 128 MiB 한도를 적용한다. 응답의
artifact SHA·크기·이름도 실제 보낸 bytes와 대조한다. 같은 bytes/name의 명시적
재업로드는 기존 artifact로 수렴한다. Model 생성에는 자동 재시도/멱등성 key
계약이 없고, revision 변경은 `expected_revision`으로 충돌을 검사한다.

Job과 comparison 제출에는 **명시적 Idempotency-Key가 필수**다. 응답이 유실되면
동일 endpoint·key·body로만 다시 요청한다. SDK는 자동 실행 재시도나 최신 모델
선택을 하지 않는다. `wait_perception_job()`은 GET만 사용하며 timeout이 취소를
의미하지 않는다. 실패·취소·중단 결과를 그대로 반환한다. 두 job의 예측 비교는
동일 입력 PNG 여부와 configuration 차이를 드러내며 정확도 개선/회귀를 주장하지 않는다.

모든 perception export는 same-origin 고정 API 경로에서 가져온 bytes의 SHA와
크기를 검증하고, model export는 조회한 revision을 다시 pin한다. 응답의 외부
`download_url` 또는 redirect로 인증정보를 전달하지 않는다. 새 파일만 만들고,
전송 중단·잘못된 길이/hash·짧은 write·close 실패 시 해당 호출이 만든 부분 파일만
정리한다. 등록·저장 경로는 [모델 출처 및 지원 계약](../../docs/perception-model-notes.md)을 따른다.

```text
axiom upload-model-artifact weights.onnx --name weights.onnx
axiom create-model MODEL_JSON
axiom models
axiom model MODEL_ID --revision 1
axiom model-revisions MODEL_ID
axiom revise-model MODEL_ID MODEL_JSON --expected-revision 1
axiom export-model MODEL_ID model-r1.json --revision 1
axiom download-model MODEL_ID original-weights.onnx --revision 1
axiom perception-runtimes
axiom submit-perception-job JOB_JSON --idempotency-key camera-review-001
axiom perception-jobs --status queued
axiom perception-job JOB_ID
axiom wait-perception-job JOB_ID --timeout 120
axiom cancel-perception-job JOB_ID
axiom export-perception-job JOB_ID evidence.json
axiom export-perception-image JOB_ID SELECTION_INDEX input.png
axiom capture-perception-comparison LEFT_JOB_ID RIGHT_JOB_ID --name Review --note "Prediction changes only" --idempotency-key comparison-001
axiom perception-comparisons
axiom perception-comparison COMPARISON_ID
axiom export-perception-comparison COMPARISON_ID comparison.json
```

`JOB_JSON`은 위 Python 호출과 같은 입력 정의이며 key는 파일 안에 넣지 않고
`--idempotency-key`로 전달한다. CLI JSON 파일은 1 MiB 이하다. 실행 가능한
전체 흐름은 [perception_workflow.py](../../examples/perception_workflow.py)에 있다.

## 검토한 detection dataset과 저장 평가

데이터셋 이미지는 MCAP의 명시적 camera message에서 캡처한다. Detector 실행과
독립적이며, capture가 annotation 작성이나 사람 검토를 대신하지 않는다.

```python
import json
from pathlib import Path
from axiom_sdk import AxiomClient

with AxiomClient(token="YOUR_API_TOKEN") as api:
    image = api.capture_dataset_image(
        "RECORDING_ID", 1, 0, decoder_id="ros2-cdr", decoder_version="1",
    )
    api.export_dataset_image(image["id"], "source-camera.png")
    # 원본 이미지를 검토한 사람이 작성한 전체 dataset 정의를 사용한다.
    # image_id, split, review_status/reviewer/review_note, annotation_source와
    # 원본 pixel bbox를 포함하며 빈 unreviewed 목록은 negative 선언이 아니다.
    definition = json.loads(Path("reviewed-dataset.json").read_text())
    dataset = api.create_dataset(**definition)
    api.export_dataset(dataset["id"], "dataset-r1.json", revision=dataset["revision"])

    # 아래 IDs는 이미 terminal인 실제 perception job과 저장 dataset revision이다.
    evaluation = api.submit_detection_evaluation(
        name="Reviewed test split", note="Saved pipeline predictions only",
        dataset_id=dataset["id"], dataset_revision=dataset["revision"], split="test",
        inference_job_ids=["PERCEPTION_JOB_ID"],
        class_mapping=json.loads(Path("class-mapping.json").read_text()),
        evaluator_id="coco-bbox", score_threshold=0.0, diagnostic_iou=0.5,
        idempotency_key="reviewed-test-evaluation-001",
    )
    result = api.wait_detection_evaluation(evaluation["id"], timeout=180)
    api.export_detection_evaluation(result["id"], "evaluation-evidence.json")
```

`class-mapping.json`은 모든 source model label을 정확히 한 번 선언한
`[{"model_label":"person","dataset_label":"person"}, ...]` 배열이다. 대상 task에서
일부 source class를 제외하려면 해당 `dataset_label`을 명시적으로 null로 둔다.
Dataset에 있는 다른 GT class는 분모에서 사라지지 않는다. Source job은 동일한
model manifest, inference configuration/runtime과 서로 겹치지 않는 입력 선택이어야 한다.

| 작업 | Python 메서드 |
| --- | --- |
| 원본 PNG 캡처·조회·export | `capture_dataset_image(recording_id, channel_id, ordinal, decoder_id=..., decoder_version=...)`, `get_dataset_image(id)`, `export_dataset_image(id, destination)` |
| Dataset 작성·조회 | `create_dataset(**definition)`, `datasets()`, `get_dataset(id, revision=N)` |
| 새 immutable revision | `revise_dataset(id, expected_revision=N, **definition)`, `dataset_revisions(id)` |
| 고정 dataset bytes | `export_dataset(id, destination, revision=N)` |
| Evaluator 설치·정책 | `detection_evaluators()` |
| 고정 데이터 평가 제출 | `submit_detection_evaluation(..., dataset_revision=N, inference_job_ids=[...], class_mapping=[...], idempotency_key=...)` |
| 조사·페이지 탐색 | `get_detection_evaluation(id)`, `detection_evaluations(status=None, limit=100, before_seq=None)` |
| GET 대기·취소 요청 | `wait_detection_evaluation(id, timeout=180)`, `cancel_detection_evaluation(id)` |
| Terminal 근거 export | `export_detection_evaluation(id, destination)` |

SDK는 capture 응답의 recording/channel/ordinal/decoder와 원본 hash/clock,
PNG 크기·pixel 한도를 확인한다. PNG와 JSON export는 같은 API의 고정 route를
사용하며, 응답의 외부 URL이나 redirect를 따르지 않는다. SHA·실제 byte 길이를
검증하고 기존 파일을 덮어쓰지 않는다. Dataset export에서 revision을 생략하면
조회한 head revision을 다운로드 요청에 고정한다. PNG 한도 8 MiB, dataset manifest
16 MiB, evaluation manifest 32 MiB이며 manifest export에는 이미지를 재렌더링하지 않는다.

평가 제출은 명시적 key가 필요하고 응답 유실 시 동일 `/detection-evaluations` endpoint,
key, body로 다시 요청한다. `wait_detection_evaluation()`은 GET만 사용하며 timeout이
취소/재계산을 의미하지 않는다. 완료/실패/취소/중단 상태와 `report.metrics=null`을
그대로 보존한다. 전체 selected split 분모가 충족되지 않으면 부분 row 진단이 있어도
전체 AP나 accuracy가 있다고 판단해서는 안 된다.

```text
axiom capture-dataset-image RECORDING_ID CHANNEL_ID ORDINAL --decoder-id ros2-cdr --decoder-version 1
axiom dataset-image IMAGE_ID
axiom export-dataset-image IMAGE_ID original-camera.png
axiom create-dataset DATASET_JSON
axiom datasets
axiom dataset DATASET_ID --revision 1
axiom dataset-revisions DATASET_ID
axiom revise-dataset DATASET_ID DATASET_JSON --expected-revision 1
axiom export-dataset DATASET_ID dataset-r1.json --revision 1
axiom detection-evaluators
axiom submit-detection-evaluation EVALUATION_JSON --idempotency-key reviewed-test-evaluation-001
axiom detection-evaluations --status queued
axiom detection-evaluation EVALUATION_ID
axiom wait-detection-evaluation EVALUATION_ID --timeout 180
axiom cancel-detection-evaluation EVALUATION_ID
axiom export-detection-evaluation EVALUATION_ID evaluation-evidence.json
```

Dataset/evaluation CLI 요청 JSON은 최대 16 MiB다. Revision 충돌은 409로 유지하고
자동으로 최신 revision에 덮어쓰지 않는다. [API 계약](../../docs/datasets-contract.md)과
[평가 산식·라이선스·한계](../../docs/detection-evaluation-notes.md)를 함께 확인한다.

## 고정 component stack과 기록 기반 제어 명령

아래 Stack replay API는 실제 MCAP point bytes와 ego telemetry로 제어 명령을 계산하는
**recorded open-loop replay**다. `control_application="not_applied_open_loop"`이며
출력 명령이 차량이나 다음 센서 frame을 바꾸지 않는다. 같은 stack의 선택적
CARLA live 실행은 아래 `submit_job(..., control=...)` 경로를 사용한다.
기존 RGB/LiDAR capture에 없는 ego state를 자동 생성하지 않는다.

`stack_components()`의 typed metadata와 parameter schema를 먼저 확인한다.
Stack 작성·개정은 각 validator가 해석한 **전체 normalized parameters**를 고정하며
실행 시 최신 defaults에 의존하지 않는다. `ground_reference_z_m` 기본값은 null이고
정지한다. 값을 선언하려면 nonblank `ground_reference_source`가 필요하다.
Rig transform·source frame axes·clock offset도 명시적인 source 선언이어야 하며,
mount 높이가 지면 높이나 검증된 calibration을 증명하지 않는다.

아래 두 JSON 파일은 [StackCreate와 고정 입력 계약](../../docs/stacks-contract.md)에
따라 작성한다. `stack-definition.json`은 name/description/note/definition 전체이고,
`stack-source-selection.json`은 실제 조회한 recording_id, binding_revision,
lidar_channel_id, ego_channel_id, ego_ordinals와 필요한 clock offset을 담는다.
Ego ordinal은 선택 순서의 index가 아니라 원본 파일의 메시지 번호다.

```python
import json
import os
from copy import deepcopy
from pathlib import Path
from axiom_sdk import AxiomClient

with AxiomClient(token=os.getenv("ADE2E_API_TOKEN")) as api:
    print(api.stack_components())
    definition = json.loads(Path("stack-definition.json").read_text())
    stack = api.create_stack(**definition)
    api.export_stack(stack["id"], "stack-r1.json", revision=stack["revision"])
    revised = deepcopy(definition)
    revised["definition"]["policy"]["parameters"]["time_headway_s"] = 3.0
    second = api.revise_stack(stack["id"], expected_revision=1, **revised)
    api.export_stack(stack["id"], "stack-r2.json", revision=second["revision"])

    selection = json.loads(Path("stack-source-selection.json").read_text())
    print(api.stack_replay_sources(selection["recording_id"]))
    job = api.submit_stack_replay(
        label="Pinned recorded decision review", stack_id=stack["id"],
        stack_revision=second["revision"], **selection,
        idempotency_key="recorded-stack-review-001",
    )
    result = api.wait_stack_replay(job["id"], timeout=180)
    print(result["status"], result["counts"])
    api.export_stack_replay(result["id"], "stack-replay-evidence.json")
    tick = next((item for item in result["ticks"] if item["points"] is not None), None)
    if tick is not None:
        api.export_stack_replay_points(result["id"], tick["index"], "consumed-points.xyz")
```

`export_stack_replay_points()`는 preview decimation이 아닌 **실제 소비한 전체**
ego-frame XYZ float32 LE bytes를 내려받는다. Count × 12의 길이와 SHA-256을
검증하며 최대 20,000점이다. Stack/replay export도 고정 same-origin API 경로,
revision·hash·size 검사를 사용하고 기존 파일을 덮어쓰지 않는다.

| 작업 | Python 메서드 |
| --- | --- |
| Component·stack 조사 | `stack_components()`, `stacks()`, `get_stack(id, revision=N)`, `stack_revisions(id)` |
| 작성·개정 | `create_stack(**definition)`, `revise_stack(id, expected_revision=N, **definition)` |
| 고정 stack bytes | `export_stack(id, destination, revision=N)` |
| 지원되는 원본 채널 | `stack_replay_sources(recording_id)` |
| 고정 입력 제출 | `submit_stack_replay(..., stack_revision=N, binding_revision=N, ego_ordinals=[...], idempotency_key=...)` |
| 실행 조회·대기·취소 | `stack_replays(status=None, limit=100, before_seq=None)`, `get_stack_replay(id)`, `wait_stack_replay(id)`, `cancel_stack_replay(id)` |
| Manifest·소비 bytes | `export_stack_replay(id, destination)`, `export_stack_replay_points(id, tick_index, destination)` |

```text
axiom stack-components
axiom create-stack STACK_JSON
axiom stacks
axiom stack STACK_ID --revision 1
axiom stack-revisions STACK_ID
axiom revise-stack STACK_ID STACK_JSON --expected-revision 1
axiom export-stack STACK_ID stack-r1.json --revision 1
axiom stack-replay-sources RECORDING_ID
axiom submit-stack-replay REPLAY_JSON --idempotency-key recorded-stack-review-001
axiom stack-replays --status queued
axiom stack-replay REPLAY_ID
axiom wait-stack-replay REPLAY_ID --timeout 180
axiom cancel-stack-replay REPLAY_ID
axiom export-stack-replay REPLAY_ID evidence.json
axiom export-stack-replay-points REPLAY_ID TICK_INDEX consumed.xyz
```

CLI의 `REPLAY_JSON`은 label/stack_id/stack_revision과 위 source selection을 함께
담고, key는 파일 안이 아닌 `--idempotency-key`로 전달한다. 응답 유실 시 같은
endpoint·key·body로 명시적으로 재요청한다. `wait_stack_replay()`는 GET만 수행하며
timeout은 취소나 재제출이 아니다. 모든 선택 tick의 분모와 실패·취소·미실행을
확인한다. 완료된 `unknown_stop`도 관측 성공이나 주행 시험 통과를 뜻하지 않는다.

전체 실행 예제는 [stack_replay_workflow.py](../../examples/stack_replay_workflow.py)다.
독립 authored points·ego bytes를 생성해 missing/empty/stale 정지와 같은 입력에서
두 headway revision의 명령 차이를 검증한다. 이 fixture를 실제 CARLA 측정이나
폐루프 제동 결과로 표시하지 않는다. [사용 흐름](../../docs/stacks.md).

## 선택적 CARLA 0.4 live stack

`submit_job(scenario_id, adapter_id="carla", control=control, idempotency_key=...)`은
고정 stack·rig revision, LiDAR sensor key와 `carla-lidar-ego/1` profile을 선택한다.
`run(..., control=...)`도 지원한다. CLI는 `submit-job` 또는 `run`에
`--control control.json`을 추가한다. 정확한 JSON과 실행 범위는
[live stack 안내](../../docs/carla-live-stack.md)를 따른다.

이 모드는 2–4초·20Hz LiDAR와 simulator ego/map oracle을 입력으로 사용하며 RGB는
생성하거나 소비하지 않는다. `control` 생략은 기존 GT controller다. 기본 모드와
같은 `carla` 실행 자원을 공유하고, timeout·취소 요청 후 자동 재실행하지 않는다.
반환된 run의 고정 `execution_context`, 원본·소비 bytes, ACK와 다음 physics frame을
함께 확인한다. Origin의 context는 `recording_origin(id)["snapshot"]` 아래 있다.

[carla_stack_workflow.py](../../examples/carla_stack_workflow.py)는 전용 CARLA에 실제
명령을 적용하고 각 run의 원본 MCAP를 같은 stack으로 재계산한다. 예제 실행은
시뮬레이터 작업을 생성하며, 현재 배포의 실제 인수 여부는 [status](../../docs/status.md)를
확인한다. 기록 replay의 일치가 주행 성능 통과를 의미하지 않는다.

## 실행과 시험 관리

새 실행에는 `submit_job(..., idempotency_key=...)`와 `wait_job(id)`를 사용한다.
동일 요청 응답이 유실되면 같은 endpoint·key·body로 재요청하고 작업 상태를 읽는다.
SDK는 엔진 실행을 자동 재시도하지 않으며 polling timeout은 취소나 재제출이 아니다.
`wait_campaign()`은 모든 member가 terminal인 `status="finished"`까지 기다리며
`verdict`의 failed/incomplete 상태를 passed로 바꾸지 않는다.

- [실행 큐 안내](../../docs/execution-jobs.md)
- [시험 묶음·baseline·comparison](../../docs/test-suites.md)
- [에이전트 사용법](../../docs/agent-guide.md)
- [센서 지원 포맷과 한계](../../docs/sensor-format-notes.md)
- [현재 상태와 실서버 검증 gate](../../docs/status.md)

이 SDK는 로컬 개발 플랫폼을 사용하는 도구다. 상용 배포·조직 격리·실측 센서
정확도 또는 실제 CARLA engine acceptance를 보증하지 않는다.

## HIL 장면 선택

`hil_sim_scenes(project_id=...)`는 기존 HIL base 장면에 적용할 앞차 감속과
정지 후 재출발 일정을 조회한다. 반환된 full descriptor에서 정확한
`{id, revision, sha256}`를 골라 `save_hil_sim_test(..., scene_profile=ref)` 또는
`preview_hil_sim_preset(..., scene_profile=ref)`에 전달한다. 카탈로그 조회·시험
저장은 엔진 없이 가능하고 preview는 native 사전 점검을 수행한다.

아래 예제는 프로젝트에 이미 연결된 지연 preset으로 새 시험을 저장하고
미리보기까지만 만든다. 기존 시험을 개정할 때는 원래 binding, parameters와
선택한 DUT profile refs를 함께 보존하고 `test_id`, `expected_revision`을 지정한다.

```python
import os
from axiom_sdk import AxiomClient

with AxiomClient(os.environ["AXIOM_URL"], token=os.environ["ADE2E_API_TOKEN"]) as api:
    project = "default"
    catalog = api.hil_sim_scenes(project_id=project)
    scene = next(item for item in catalog["items"] if item["id"] == "lead_stop_go")
    scene_ref = {key: scene[key] for key in ("id", "revision", "sha256")}
    presets = api.hil_sim_presets(project_id=project)
    preset = next(item for item in presets["items"] if item["id"] == "input_delay")
    test = api.save_hil_sim_test(
        project_id=project, name="앞차 재출발과 입력 지연", preset_id="input_delay",
        binding_revision=preset["binding_revision"], binding_sha256=preset["binding_sha256"],
        parameters={"delay_ms": 400, "start_s": 2, "duration_s": 3},
        scene_profile=scene_ref, idempotency_key="hil-stop-go-test-001",
    )
    test_ref = {key: test[key] for key in ("id", "revision", "sha256")}
    preview = api.preview_hil_sim_test(test_ref, project_id=project)
    print(preview["scene_profile"], preview["preview"]["preview_hash"])
```

`scene_profile` 생략/`None`은 기존 4초 정지 장면이며 새 필드를 추가하지 않는다.
선택한 장면은 저장 시험과 experiment input에 full descriptor로 고정한다.
원래 `source.scenario_ref`와 두 해시는 base 시나리오를 계속 가리키고, 앞차 일정은
별도 descriptor hash 및 전체 input hash로 연결된다. 반환된 일정을 임의로 수정하지
않는다. [정확한 장면 계약](../../docs/api-contract.md#hil-fixed-scene-selection--2026-10-11).

실행은 검토한 preview를 `execute_hil_sim_preview(preview, idempotency_key=...)`에
명시적으로 전달한다. 정상 Experiment queue와 시험 실행 연결을 재사용한다.
응답 유실 시 같은 body/key를 보존하며, `HilExecutionLinkError`에는 이미 제출한
`execution_id`가 있으므로 새 실행 대신 연결만 재시도한다. 입력 일정의 목표 속도와
원본 trace의 실제 앞차 속도·응답 사건을 구분한다.


## 원격 실행 결과 회수

원래 작업의 실패·중단 이력을 보존하면서 이미 저장된 원격 결과만 가져온다. 이 명령들은 시뮬레이터를 다시 실행하거나 엔진 격리를 자동 해제하지 않는다. 원격 저장소가 있는 새 작업만 지원하며, 부분 archive는 정상 실행 결과가 아닌 진단 자료다.

```sh
axiom inspect-remote-execution JOB_ID
axiom submit-execution-recovery JOB_ID --note "응답 유실 뒤 기존 결과 회수" --idempotency-key recovery-intent-001
axiom execution-recoveries --source-job-id JOB_ID
axiom wait-execution-recovery RECOVERY_ID --timeout 180
axiom execution-recovery RECOVERY_ID
axiom export-execution-recovery RECOVERY_ID recovery.json
axiom export-remote-execution-archive RECOVERY_ID remote-archive.json
# 가져오기 작업만 취소한다. 원격 시뮬레이터 중지를 의미하지 않는다.
axiom cancel-execution-recovery RECOVERY_ID
```

Python에는 `inspect_remote_execution`, `submit_execution_recovery`, `execution_recoveries`, `get_execution_recovery`, `wait_execution_recovery`, `cancel_execution_recovery`, `export_execution_recovery`, `export_remote_execution_archive`가 있다. 상태 조회와 대기는 GET만 사용한다. 다운로드는 동일 인증 origin에서 고정 경로를 사용하며 SHA-256·크기 검증 후 저장하고 기존 파일을 덮어쓰지 않는다. 새 실행의 `recovery.source_execution_id`는 원래 artifact 안의 실행 ID이며 새 로컬 run ID와 다를 수 있다.

이미 실패/중단된 원격 작업을 대상으로 실행하는 예제:

```sh
.venv/bin/python examples/recovery_workflow.py --source-job-id JOB_ID \
  --note "원래 실행의 저장된 결과 확인" --idempotency-key recovery-example-001 \
  --output /tmp/axiom-recovery-example
```

출력 디렉터리는 새 경로여야 한다. 예제는 입력·상태·원본 archive·회수 manifest를 보존하고 원래 job이 바뀌지 않았는지 검사한다. `outcome=recovered`와 `diagnostics_only`를 구분하며, timeout 후 자동 재실행·취소는 하지 않는다.

## CARLA 0.8 configurable sensor input

A simulation sensor configuration stores a complete immutable ray-cast recipe;
its physical position, quaternion and frame come from the selected rig revision.
These are uncalibrated simulator declarations. Read the current deployment and
actual acceptance in [status](../../docs/status.md).

```sh
axiom simulation-sensor-capabilities --adapter-id carla
axiom simulation-sensor-configurations
axiom create-simulation-sensor-configuration recipe.json --idempotency-key sensor-design-001
axiom simulation-sensor-configuration CONFIGURATION_ID --revision 1
axiom revise-simulation-sensor-configuration CONFIGURATION_ID recipe-r2.json --expected-revision 1 --idempotency-key sensor-design-002
axiom simulation-sensor-configuration-revisions CONFIGURATION_ID
axiom export-simulation-sensor-configuration CONFIGURATION_ID sensor-r1.json --revision 1
```

`recipe.json` contains `name`, a nonempty `note`, and `parameters`; optional fields
are `description`, `adapter_id="carla"`, `capability_id="carla-raycast-lidar/1"`
and `capability_version="1"`. The catalog provides exact parameter types, defaults,
mount bounds and evidence limits. `noise_seed` is derived from the effective
scenario seed. A legal per-frame recipe can exceed the complete run's evidence
budget and be rejected. No point decimation or assumed dropout discount is used.

Python exposes `simulation_sensor_capabilities`, `simulation_sensor_configurations`,
`create_simulation_sensor_configuration`, `get_simulation_sensor_configuration`,
`revise_simulation_sensor_configuration`, `simulation_sensor_configuration_revisions`
and `export_simulation_sensor_configuration`. Create/revise require an explicit
`idempotency_key`; export pins the returned revision and verifies size/SHA before
writing a new file. A lost authoring response is retried with the same body and key.

Pass this explicit new selection to `submit_job(..., control=selection, ...)`,
`run(..., control=selection, ...)`, or a CLI `--control selection.json`:

```json
{
  "schema_version": "simulation-control-selection/2", "mode": "stack",
  "stack_id": "STACK_ID", "stack_revision": 1,
  "rig_id": "RIG_ID", "rig_revision": 1, "lidar_sensor_key": "front",
  "sensor_configuration_id": "CONFIGURATION_ID", "sensor_configuration_revision": 1
}
```

Omit `sensor_profile_id` in this selection. Legacy selections and omitted control
retain their existing meaning. Inspect the saved context/2 and native attribute/
mount evidence; an authored transform or ready engine alone does not prove physical
visibility, free space, calibrated sensing or a passed driving requirement.

From the repository root, the complete authored examples are
[`carla-configurable-lidar.json`](../../examples/carla-configurable-lidar.json)
and [`carla-configurable-rig.json`](../../examples/carla-configurable-rig.json).
They specify every sampling field and the `front` sensor key. Verify that the
remote worker reports 0.8.0 and use a supported 2–4 second scenario on the loaded
map. These examples do not establish visibility or driving quality.

```sh
axiom simulation-sensor-capabilities --adapter-id carla
axiom create-simulation-sensor-configuration examples/carla-configurable-lidar.json --idempotency-key front-lidar-design-001
axiom create-sensor-rig examples/carla-configurable-rig.json
```

Put the returned configuration/rig IDs and the intended existing stack ID in the
selection above, then save it as `selection.json`. Use the exact desired revisions
and replace `SCENARIO_ID`, `JOB_ID` and `RUN_ID` with returned IDs:

```sh
axiom submit-job SCENARIO_ID --adapter carla --seed 42 --control selection.json --idempotency-key front-lidar-trial-001
axiom wait-job JOB_ID --timeout 240
axiom job JOB_ID
axiom export RUN_ID evidence.json
```

A wait timeout does not terminate or restart the job: query/wait on the same ID.
If submission acknowledgement is lost, keep the same endpoint, body and
idempotency key. Sensor-rig creation has no idempotency key; inspect the rig
library after an uncertain response before creating another rig. A failed driving
verdict may still have a completed execution and useful original artifacts.
Download every retained artifact, including those attached to a partial/error run:

```python
from pathlib import Path
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    job = api.get_job("JOB_ID")
    if job["run_id"] is not None:
        run = api.get_run(job["run_id"])
        destination = Path("retained-run-artifacts")
        destination.mkdir(exist_ok=False)
        for artifact in run["artifacts"]:
            api.download_artifact(artifact, destination / artifact["name"])
```

The SDK checks each downloaded artifact's recorded size and SHA-256. A job without
a run has no run artifacts yet; use its status and the explicit
[remote recovery workflow](#원격-실행-결과-회수) when applicable.

### Apply one pinned control selection to a full suite

The same `selection.json` used for an individual CARLA job can be applied to every
case in an immutable suite revision. Both preset and configurable sensor selections
are supported. Each case retains its saved seed and parameter overrides; the
campaign independently pins its selected assets and each member's expected input,
context and request hashes. All cases must pass preparation before any jobs are
created. This does not run remote preflight during submission.

```sh
axiom submit-campaign SUITE_ID --suite-revision 1 --label "Pinned sensor suite" --adapter carla --control selection.json --idempotency-key sensor-campaign-001
axiom wait-campaign CAMPAIGN_ID --timeout 600
axiom campaign CAMPAIGN_ID
axiom export-campaign CAMPAIGN_ID campaign-report.json
axiom create-baseline CAMPAIGN_ID --name "Observed pinned control" --note "Review original failures and missing evidence" --idempotency-key sensor-baseline-001
```

The Python equivalent is:

```python
import json
from pathlib import Path
from axiom_sdk import AxiomClient

selection = json.loads(Path("selection.json").read_text())
with AxiomClient() as api:
    campaign = api.submit_campaign(
        "SUITE_ID", suite_revision=1, label="Pinned sensor suite", adapter_id="carla",
        control=selection, idempotency_key="sensor-campaign-001",
    )
    report = api.wait_campaign(campaign["id"], timeout=600)
    print(report["verdict"], report["counts"])
```

After an uncertain acknowledgement, retry with the original body and key. After a
wait timeout, inspect the same campaign ID. `finished` describes job lifecycle;
failed criteria stay failed. Changed stack, rig or sensor configuration contexts
are `not_comparable` to the old baseline. Omit `control` for the previous adapter
controller behavior. See the [campaign control contract](../../docs/campaign-control.md).

### FPGA Lab

Optional SimPy camera timing designs use the same project-scoped API as HIL → FPGA Lab.
Install `integrations/fpga_lab` into both API and worker environments. With a worker running:

```python
preset = client.fpga_lab_templates()['items'][0]
design = client.save_fpga_lab_design(name='RAW12 normal', config=preset['config'],
                                     idempotency_key='my-design-1')
job = client.execute_fpga_lab_design(design, idempotency_key='my-run-1')
# Poll client.experiment_execution(job['id']); then export the immutable receipt.
receipt = client.export_fpga_lab_execution(job['id'])  # only after terminal status
```

Use the same keys to recover uncertain responses. Inspect and compare exact saved revisions with
`fpga_lab_design`, `fpga_lab_designs`, and `fpga_lab_executions`. See
[`examples/fpga_lab_workflow.py`](../../examples/fpga_lab_workflow.py) for bounded polling and
[model boundaries](../../docs/fpga-lab.md). These are behavioral timing calculations, not RTL or hardware validation.

## SA8650 reference ECU Lab

The optional SimPy target ECU mode shares the project-scoped API with the UI.
It simulates 1–4 synthetic RAW senders, shared CSI arbitration, receive queues,
processing and logical watchdogs; it does not run Qualcomm firmware or physical ECU I/O.

```python
preset = client.ecu_lab_templates()['items'][0]
profile = client.save_ecu_lab_profile(
    name='Reference SA8650', config=preset['profile']['config'],
    idempotency_key='unique-profile-write-key')
# Save each FPGA sender using save_fpga_lab_design, then pin id/revision/sha256.
# Complete executable authoring -> queue -> result example: examples/ecu_lab_workflow.py
```

See [ECU Lab contract](../../docs/ecu-lab.md) for profile/test revisions, preflight,
immutable input hashes, execution idempotency and the six example outcomes.

### Four-channel ADAS Camera Module RTL

`examples/adas_module_rtl_workflow.py` uses the Engineering Lab methods to save
four-channel module tests, execute actual Verilator jobs, inspect same-input A/B,
verify artifact hashes and export evidence. The default runs six full
1920×1080×30-frame-per-channel cases. `--build` runs a separate ECP5 OOC job;
`--resume` reuses saved revisions and jobs. `--smoke` is explicitly reduced
functional verification, never full-HD acceptance. Inspect the receipt's
`validation_gates`: successful native execution does not imply FPGA timing or
physical HIL readiness. See [module contract](../../docs/adas-camera-module-rtl.md).

`examples/compare_adas_module_campaigns.py --before <receipt> --after <receipt>`
compares two full-resolution campaigns without executing a substitute model.
It rehashes artifacts and exports, compares every committed RGB frame and input
hash, checks faults/recovery/configuration versions, and records discarded partial
output and timing changes separately. `--verify-shared-store` also checks both
sets of execution manifest hashes through the after receipt's public SDK service.
The comparison JSON and Markdown include pixel A/B links for all six cases and
an independent OOC build A/B link (`build` / `buildCompare`).

### ADAS pre-board campaign

`preview_adas_campaign_plan(config)` returns the deterministic, hashed case plan.
Save and execute `engine='adas_validation_campaign'` with the existing Engineering
Lab methods. `adas_campaign_progress(job_id)` returns project-scoped provisional
counters; only terminal sealed execution evidence contains a verdict. Standalone
`engine='adas_preboard_checks'` runs actual four-state/lint/reference-boundary checks.

`examples/adas_preboard_workflow.py` runs the default ten-second four-channel 1080p30
campaign and a separate 100 MHz OOC build, verifies all downloaded hashes and sealed
exports, and supports exact `--resume`. Reduced `--smoke`, randomized two-state init,
four-state pixel probes, digital CDC reference tests and physical board readiness
are distinct gates. See [pre-board scope](../../docs/adas-preboard-validation.md).

## Virtual ECU Lab

Use `vecu_catalog(project_id=...)`, `save_vecu_record("packages"|"inputs"|"tests", body, idempotency_key=...)`,
`preview_vecu_test(test)` and `execute_vecu_test(test, idempotency_key=...)`.
Read exact revisions with `vecu_record`; use existing experiment execution polling, cancellation and export.
See `examples/vecu_workflow.py` and `docs/virtual-ecu-lab.md` for the executable workflow and fidelity limits.

### Virtual ECU Network timing evidence

`vecu_network_causality(execution_id, project_id=...)` reads the same
`vecu-network-causality/1` report used by the Network Lab timeline. It checks
retained source integrity on the server and returns detector evidence, matching
CAN transmit/receive bytes, applied braking, subsequent CARLA observations,
latencies, source pointers and manifest/trace/report hashes. It supports UDP
delay and Restbus loss; missing links and other detection kinds remain explicit.
It does not run a simulator. See [the contract and clock definitions](../../docs/virtual-ecu-network-lab.md).

### SIL → HIL bindings

Use `hil_bridge_profiles()` and `save_hil_binding()` to attach a device profile to
an existing test revision. `preview_hil_binding()` validates capabilities and live
readiness; `execute_hil_binding()` submits the same immutable queue contract used
by the UI. `hil_bridge_execution()`, `hil_bridge_causality()` and
`export_hil_bridge_execution()` read observed evidence. A vCAN profile never
establishes physical ECU readiness. Native acceptance is tracked in docs/hil-bridge.md.
