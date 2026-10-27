# Perception model provenance and input contract

공식 배포·문서 확인일: **2026-09-13**. 현재 첫 모델은 **OpenCV Zoo YOLOX-S FP32**다. [Perception API 계약](perception-contract.md)은 모델 등록, 실제 추론, 저장된 예측을 구분한다. 모델이 출력한 box와 score는 ground truth, 보정된 확률, 실차 검출 정확도 또는 안전성 판정이 아니다.

## 선택한 모델과 라이선스 근거

| 항목 | 고정 값 / 근거 |
|---|---|
| 공식 배포 | OpenCV `opencv_zoo`, `models/object_detection_yolox/object_detection_yolox_2022nov.onnx` |
| 소스 revision | `47534e27c9851bb1128ccc0102f1145e27f23f98` |
| 원본 ONNX 크기 | `35,858,002` bytes |
| 공개 LFS SHA256 | `c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063` |
| 입력 / class map | 640 × 640, COCO의 연속 zero-based 80개 class label 순서 |
| 코드·배포 모델 조건 | 해당 모델 [README](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/README.md)의 “All files in this directory are licensed under” 문구와 [Apache-2.0 LICENSE](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/LICENSE) |

[고정 ONNX 다운로드](https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/object_detection_yolox_2022nov.onnx), [공개 LFS pointer](https://raw.githubusercontent.com/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/object_detection_yolox_2022nov.onnx). 공개 SHA는 내려받은 파일의 무결성을 대조하는 기준이다. 실행 여부·측정 지연·검출 결과는 별도 저장된 inference evidence에서 확인한다.

OpenCV의 디렉터리 전체 라이선스 표기는 ONNX weights를 포함하는 배포 근거로 기록한다. 플랫폼의 `weights_license`, `weights_license_url`, `code_license`, `training_data_note`는 **사용자 선언**이며 법적 승인이나 제3자 권리 검증 상태로 자동 승격하지 않는다. 원본 notice 유지·배포 조건은 [OSS 등록부](open-source-register.md)와 [third_party](../third_party/README.md)에서 별도로 관리한다.

ONNX Runtime 자체는 [Microsoft MIT 라이선스](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)다. runtime 코드 조건이 임의 model weights나 학습 데이터까지 포괄하지 않는다. 선택한 모델은 COCO로 평가된 배포다. COCO의 annotation/website는 CC BY 4.0이고, 이미지 저작권은 COCO Consortium 소유가 아니며 개별 이미지 이용 조건을 따라야 한다. 따라서 repository의 예제 JPEG나 COCO sample의 재배포 권리를 코드 라이선스에서 추정하지 않는다. [COCO 공식 이용 조건](https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/termsofuse.htm).

## 실제 배포별 전처리와 후처리

`opencv_yolox_rgb_v1`은 RGB 원본 이미지에서 비율을 유지해 linear resize하고, 좌상단에 배치해 오른쪽/아래를 114로 채운다. 입력은 RGB float32 NCHW의 **0..255 값**이며 `/255` 또는 mean/std normalization을 추가하지 않는다. 원본 크기와 실제 resize 크기·배율을 결과에 고정한다. [공식 OpenCV demo](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/demo.py).

출력은 stride 8/16/32 grid decode, objectness × 가장 높은 class score, class-aware NMS를 거친다. 플랫폼 계약은 연속 pixel 좌표의 IoU를 사용한다. 원본 이미지 좌표로 역변환한 유한 xyxy box만 허용하고, 이미지 범위로 clipping한 결과와 NMS/제거 개수를 기록한다. Ordered label list 자체를 canonical JSON으로 만든 SHA256이 `label_map_hash`다. 이름이 같아도 순서가 바뀌면 다른 class map이다. [공식 후처리](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/yolox.py).

**Megvii Nano용 전처리를 이 모델에 재사용하면 안 된다.** Megvii의 공식 `0.1.1rc0` Nano demo는 OpenCV BGR 입력을 그대로 사용하며 class-agnostic NMS가 기본이다. 배포 이름이 YOLOX라는 이유로 RGB/BGR, 입력 크기, graph 내 decode 유무, NMS 의미를 추정하지 않는다. [Megvii ONNX demo](https://github.com/Megvii-BaseDetection/YOLOX/tree/main/demo/ONNXRuntime), [후처리 구현](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/yolox/utils/demo_utils.py).

`boxes_xyxy_v1`은 위 모델과 별개인 명시적 확장 계약이다. RGB `/255`, stretch resize, `[1,N,6]` pixel xyxy/score/class ID 출력을 지원한다. 자체 작성 ONNX fixture를 쓰더라도 입력 이미지에 따른 실제 graph 실행 결과를 기록한다. Fixture 실행을 사전 학습 detector의 정확도 검증으로 표시하지 않는다.

## 등록과 실행의 경계

- 원본 model upload는 `application/octet-stream`으로 **128 MiB**까지 streaming 저장한다. 같은 bytes/name의 재업로드는 같은 artifact로 수렴한다. 서버가 사용자 URL을 대신 fetch하지 않는다. filename은 저장 경로로 사용하지 않는다.
- 등록 시 ONNX를 import하거나 graph를 실행하지 않는다. 모델 파일의 SHA와 크기를 검사한 뒤, provenance·입력 크기·profile·ordered labels·note를 최대 256 KiB canonical manifest에 고정한다. 가중치 bytes SHA와 모델 정의 manifest SHA는 서로 다른 증거다.
- revision 생성은 complete definition과 `expected_revision`을 받는다. 동시에 수정하면 하나만 성공하고 나머지는 409다. 이전 manifest와 가중치 파일을 덮어쓰지 않는다. export는 저장한 canonical bytes 그대로 반환하며 hash/size/관계/label map 일치를 검증한다.
- 다운로드와 `verified_model_path`는 파일을 chunk 단위로 읽어 실제 size/SHA를 검사한다. inference job은 선택한 revision과 model SHA를 별도로 고정해야 한다. 등록 성공은 runtime 설치, profile 호환성, graph 안전성 또는 추론 성공을 뜻하지 않는다.
- 일반적인 upload 중단·write/commit 실패는 해당 업로드의 임시/미확정 파일만 정리한다. filesystem과 SQLite의 host-crash 원자성, orphan 파일 자동 GC는 보장하지 않는다. runtime의 subprocess/timeout은 관리자 파일 변경이나 적대적 모델에 대한 OS 보안 sandbox가 아니다.

첫 수용 흐름은 권리를 확보한 이미지의 MCAP 메시지를 선택해 실제 CPU 추론을 실행하고, 정확한 원본 메시지 SHA·decoded PNG SHA·model SHA·전후처리 버전·box/score를 함께 저장하는 것이다. 검출 0개는 완료된 빈 결과로 보존하며 실패 프레임과 구분한다. 서로 다른 모델 또는 threshold의 예측 차이를 정확도 향상/회귀라고 판정하지 않는다.

## 비교한 후속 후보

| 후보 | 장점 | 현재 남은 경계 |
|---|---|---|
| [Megvii YOLOX-Nano `0.1.1rc0`](https://github.com/Megvii-BaseDetection/YOLOX/releases/tag/0.1.1rc0) | 공식 ONNX 3,659,407 bytes, 416², 작은 CPU 시범 후보 | 코드 Apache-2.0은 확인했으나 release weights만의 명시적 조건은 확인하지 못했다. 공개 asset SHA digest도 없어 최초 bytes 대조·등록이 필요하다. OpenCV-S의 라이선스 문구를 Nano에 일반화하지 않는다. |
| [PekingU RT-DETR R18](https://huggingface.co/PekingU/rtdetr_r18vd) | 공식 model card Apache-2.0, 640², DETR 방식 후속 비교 | 원본 safetensors 80,904,152 bytes. 공식 exporter로 ONNX를 생성하고 exporter revision·결과 SHA를 고정해야 한다. CPU 시간은 여기서 측정하지 않았다. |

RT-DETR의 고정 revision `ac77a11ff0170a41b771c03264987f8ce2b0d753` [processor config](https://huggingface.co/PekingU/rtdetr_r18vd/blob/ac77a11ff0170a41b771c03264987f8ce2b0d753/preprocessor_config.json)는 `/255`, `do_normalize=false`다. Model card의 mean/std 설명만 따라 normalization을 추가하면 실제 배포 계약과 달라진다. [공식 ONNX exporter](https://github.com/lyuwenyu/RT-DETR/blob/main/rtdetr_pytorch/tools/export_onnx.py)는 `images, orig_target_sizes`를 받아 `labels, boxes, scores`를 내보낸다.
