# 기록 이미지의 인지 추론과 예측 비교

Perception experiments는 MCAP 카메라 메시지의 정확한 파일 ordinal을 선택하고,
고정한 모델 개정으로 CPU 추론을 실행해 입력 PNG·검출 box·모델 score·전후처리
근거를 저장한다. 시나리오 실행과 같은 영속 worker 큐를 사용하며, 추론 작업이
주행 시험의 run/verdict 또는 요구사항 통계에 섞이지 않도록 kind를 구분한다.

현재 범위는 기록 이미지의 object detection이다. 실제 CARLA 서버의 센서 캡처,
라벨 기반 정확도 평가, perception→planning→control의 closed loop는 아직 필요하다.

## 화면에서 사용하는 흐름

1. **Data catalog → Recordings**에서 권리를 확보한 MCAP를 등록한다. 지원 camera
   decoder가 있어야 하며 파일 ordinal과 timestamp는 서로 다른 식별자다.
2. **Perception experiments → Models → Register model**에서 ONNX 원본, profile,
   입력 크기, 순서가 있는 class labels와 출처·weights/code license 선언을 저장한다.
   파일 저장 성공은 ONNX 호환성·정확도 검증을 뜻하지 않는다.
3. **Experiments → New experiment**에서 모델 개정·runtime·recording·camera channel·
   decoder 버전과 1..64개 ordinal을 선택한다. 센서 이미지의 **Run inference on
   this image**는 조사 중인 정확한 channel/ordinal을 전달한다.
4. score threshold·NMS IoU·최대 검출 수·seed를 고정해 제출한다. 입력 스냅샷과
   stable Idempotency-Key가 저장되며 worker가 별도 프로세스에서 계산한다.
5. 결과의 입력 선택 버튼으로 원본 크기의 PNG와 SVG 검출 box를 함께 조사한다.
   다운로드 PNG의 SHA·길이·크기를 확인한 후 그 위에 같은 pixel 좌표계로 표시한다.
   box 위치, label, score, transform, tensor/model hash는 저장된 실제 출력이다.
6. 종료된 두 작업을 **Compare**에서 선택하고 비교를 저장한다. 원본 메시지와
   실제 입력 PNG가 동일한 행에서 검출 수 차이와 예측의 정확한 일치 여부를
   보여 준다. 모델·threshold·실행 라이브러리 차이는 별도로 기록한다.

프레임은 `not_attempted → running → completed|failed`로 진행한다. 전체 선택 수는
항상 유지한다. 작업 취소나 lease 중단 시 처리 중이던 프레임은 명시적 실패 이유를
남기며 이미 완료한 예측은 보존한다. 검출 0개는 정상 완료이고, 미시도·오류·검출
누락 판정과 다르다. terminal manifest는 부분 결과를 포함해 불변 bytes로 보존된다.

## 실행 가능한 예제

API·worker·UI를 `scripts/dev.py`로 시작한다. 전체 설치는 Python 3.12+이며 ONNX
adapter를 선택형 패키지로 설치한다. API core 자체는 ONNX에 직접 의존하지 않는다.

```bash
bash scripts/setup.sh
.venv/bin/python scripts/dev.py
# 별도 터미널; 새 출력 디렉터리 사용
.venv/bin/python examples/perception_workflow.py --output .runtime/perception-example
```

이 예제는 직접 작성한 RGB 이미지 6개와 입력에 따라 달라지는 ONNX pixel rule을
만든다. 실제 CPU graph가 빨간 pixel 영역을 계산하며, 학습된 semantic detector는
아니다. 모델 r1을 제출한 뒤 r2로 수정하고, 이전 작업·manifest가 바뀌지 않는지
검사한다. 같은 PNG에 점수 gain만 다른 r1/r2를 적용하면 threshold 0.6에서 검출 수는
각각 `[1,1,0,1,1,0]`, `[1,0,0,1,0,0]`이다. 이는 fixture의 규칙 결과이며 성능 향상이나
회귀를 의미하지 않는다. 원본 PNG·모델 manifest·작업·비교 export의 hash를 검증한다.

실제 학습 가중치의 실행을 추가하려면 공식 OpenCV Zoo의 고정 파일을 가져온다.
설치 스크립트가 weights를 자동 다운로드하지 않는다.

```bash
.venv/bin/python examples/fetch_opencv_yolox.py .runtime/opencv-model
.venv/bin/python examples/perception_workflow.py \
  --output .runtime/perception-with-trained-model \
  --opencv-model .runtime/opencv-model/opencv-yolox-s.onnx
```

다운로더는 공식 LFS SHA·길이와 고정 commit의 license bytes를 대조한다. YOLOX-S
역시 위의 직접 작성한 이미지에서 실행되므로 검출 수를 road perception 정확도로
해석하지 않는다. 실행 시간은 initialization/queue/HTTP를 제외한 단일 CPU frame의
측정으로 범위를 명시한다. 모델 배포별 RGB/BGR·입력 정규화·grid decode 차이와
라이선스 근거는 [모델 노트](perception-model-notes.md)에 있다.

## 교체·재현·실패 경계

`ade2e.inference_runtimes` entry point로 runtime을 발견한다. `onnx-cpu 0.1.0`은
`opencv_yolox_rgb_v1`, `boxes_xyxy_v1`만 지원하며 알려지지 않은 profile은 실행하지
않는다. 새로운 profile은 검증·역좌표 변환·출력 계약과 함께 추가해야 한다. 단순히
ONNX라는 확장자만 맞추거나 다른 runtime 이름을 넣어 호환성을 가정하지 않는다.

모델 개정은 file SHA, ordered-label hash, source/license 선언, profile/입력 크기를
고정한다. 작업은 recording/schema/message SHA·uint64 timestamp 문자열·decoder·
runtime 버전·seed·threshold를 고정한다. 완료 프레임은 실제 PNG, tensor digest,
전후처리 library versions/transform/count와 예측을 보존한다. runtime이 보고한
hash는 단독으로 계산 수행을 증명하지 않으며 실제 input-dependent 시험을 함께 쓴다.

재시도에는 같은 요청과 같은 멱등 키를 사용한다. HTTP 대기 timeout은 작업 취소가
아니다. cancel 요청·child 종료 확인·interrupted는 각각 구분한다. 모델 초기화 또는
프레임당 30초의 native 작업 제한을 두지만 subprocess는 OS 보안 sandbox가 아니다.
이 로컬 단계는 관리자가 신뢰하는 모델을 사용하며 적대적 모델의 원격 실행은
별도 isolation/resource 정책이 필요하다.

원본 파일 변경·manifest 관계 불일치·PNG 손상·유한하지 않은 box/score·잘못된
class map·unsupported decoder/profile은 오류로 남긴다. 비교 입력의 합집합에서
누락·변경·미평가 행을 제거하지 않는다. 정답 라벨이 없으므로 precision/recall/mAP,
miss rate, 검출 개선·주행 안전성 판정을 만들지 않는다.

[API 계약](perception-contract.md) · [SDK/CLI](../sdk/python/README.md) ·
[adapter](../integrations/onnx/README.md) · [현재 인수 상태](status.md)
