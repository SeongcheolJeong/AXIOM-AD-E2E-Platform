# 라벨 데이터셋과 인지 평가

**Detection datasets**에서 원본 카메라 이미지를 선택하고 bbox·class·검토 선언을
작성합니다. 모델을 실행하기 전에도 데이터셋을 만들 수 있습니다. 평가할 때는
dataset revision·split과 종료된 추론 작업을 명시해 저장된 예측을 정답 라벨과
비교합니다. 모든 기능은 UI와 `/api/v1`·Python SDK·CLI가 함께 사용합니다.

## 이미지를 고정하고 라벨 작성하기

1. Data catalog에서 지원되는 MCAP camera 기록을 등록합니다. Dataset 화면에서
   recording·channel·정확한 message ordinal·광고된 decoder version을 선택합니다.
2. Capture image로 PNG를 고정합니다. 원본 recording/message SHA, 세부 source 선언,
   log/publish ns와 PNG SHA·크기·해상도가 함께 저장됩니다. 검출 결과가 없어도 됩니다.
3. Dataset에 ordered class labels를 정의하고 원본 pixel 좌표의 box를 그리거나
   숫자로 수정합니다. Crowd는 명시적으로 표시합니다. 임의 ignore/segmentation은
   현재 지원하지 않습니다. 모든 box가 저장된 이미지 경계 안에 있어야 합니다.
4. 이미지별 train·validation·test·unspecified 분할과 annotation source를 선택합니다.
   검토 상태를 reviewed로 바꾸려면 reviewer와 검토 설명을 입력합니다.
   **빈 box 목록만으로 negative가 되지 않습니다.** Reviewed이며 box가 없는 경우만
   검토자가 선언한 negative로 계산합니다.
5. 저장하면 불변 개정이 생깁니다. 수정은 새 개정으로 저장하며 이전 라벨·PNG·평가
   결과는 유지됩니다. 라벨 또는 taxonomy를 바꾸면 검토를 다시 선언해야 합니다.
   Split만 바꾸는 경우 기존 라벨 검토를 유지할 수 있습니다.

검토자 이름은 사용자 선언이며 로그인 신원이나 별도 승인자의 검증을 뜻하지 않습니다.
모델의 예측을 자동으로 정답 라벨로 복사하거나 승인하지 않습니다. 같은 PNG SHA가
train/validation/test 사이에 겹치면 저장을 거절합니다. 같은 split의 동일 이미지
bytes는 서로 다른 원본 관측으로 유지하고 중복 수를 표시합니다. 이 검사는
near-duplicate나 모델 학습 데이터와의 중복을 판별하지 않습니다.

## 고정된 예측 평가하기

Perception experiments에서 해당 원본 이미지에 대한 추론을 먼저 완료합니다.
평가에는 1~16개 종료 작업을 넣을 수 있습니다. 모든 작업의 model manifest·seed·
runtime·원래 score threshold/NMS/max detections와 관측된 실행 환경이 같아야 합니다.
두 모델의 작업은 각각 평가합니다. 같은 입력이 여러 작업에 중복되면 거절합니다.

모든 model label을 dataset label 또는 명시적 제외(null)에 대응시킵니다. 자동으로
비슷한 class 이름을 추정하지 않습니다. 제외된 검출 수와 split 밖의 원본은 따로
보존합니다. Dataset의 모든 선택 split image가 평가 분모에 남습니다.

| 이미지 상태 | 의미 |
| --- | --- |
| evaluated | 검토된 라벨과 동일한 원본 PNG·decoder의 완료된 추론 |
| unreviewed | 정답 라벨 검토가 끝나지 않음 |
| missing_prediction | 이 원본 입력의 예측 작업이 선택되지 않음 |
| failed_prediction | 원본 frame 추론이 실패·미시도·중단됨 |
| input_changed | 원본 identity는 연결되나 PNG·해상도 또는 decoder가 다름 |

하나라도 evaluated가 아니면 전체 AP/AR/precision/recall/F1과 class AP/PR을
`null`로 보류합니다. 계산 가능한 이미지의 TP/FP/FN/crowd 진단은 부분 범위를
명시해 조사할 수 있습니다. 정상적으로 완료된 검출 0개는 유효한 결과이며, positive
정답이 있으면 FN으로 계산합니다. Failed inference를 검출 0개로 바꾸지 않습니다.

완료된 평가는 입력 dataset 개정·모델/작업 manifests·class mapping·evaluator 버전·
정책·원래 추론 제한·추가 score filter·전체 이미지 분모·결과 해시를 저장합니다.
응답을 받지 못한 제출은 같은 endpoint·Idempotency-Key·본문으로 조회/복구합니다.
조회 timeout은 취소나 재제출이 아닙니다. 실행 취소는 별도의 명시적 요청입니다.

## 지표 읽기

실제 `pycocotools==2.0.11`을 별도 CPU child에서 호출합니다. AP/AR는 저장된
pipeline predictions에 대한 COCO bbox 계산입니다. 원래 추론의 threshold·NMS·
global cap에서 제거된 후보는 복구할 수 없습니다. Official benchmark 제출이나
필터 이전 raw model의 PR이라고 표시하지 않습니다.

AP는 기본 IoU .50:.05:.95, 101개 recall grid, 이미지/class당 maxDets100을 사용합니다.
개별 이미지 진단은 선택한 IoU에서 저장된 모든 mapped prediction(최대300/image)을
사용하므로 AP 상한 뒤의 FP도 볼 수 있습니다. PR curve와 진단 precision은 cap과
평균 방식이 다릅니다. 크기별 AP/AR의 면적은 bbox width×height이며 mask 면적이
아닙니다. Crowd overlap은 intersection/detection area이고 일반 IoU와 다릅니다.

GT가 없는 class의 AP/PR은 null이며 평균에서 제외됩니다. 해당 class의 FP 수는
남습니다. Negative만 있는 완전한 데이터셋도 AP/recall이 정의되지 않을 수 있습니다.
Source가 synthetic이면 실제 도로 정확도나 sensor validation으로 해석하지 않습니다.
상세 계산 근거와 원문은 [평가 정책](detection-evaluation-notes.md)에 있습니다.

## 재현 가능한 API 예제

전체 개발 설치는 optional ONNX와 COCO adapter를 설치합니다. Core만 설치한 경우
`integrations/onnx`와 `integrations/coco`를 API와 worker의 같은 Python 환경에
설치하고 두 프로세스를 다시 시작하세요. Native evaluator는 core import와 분리됩니다.

```sh
.venv/bin/axiom detection-evaluators
.venv/bin/python examples/dataset_evaluation_workflow.py --output .runtime/dataset-example
```

API·worker 실행 중 새 출력 경로를 사용합니다. 예제는 직접 작성한 이미지 6개를
모델 실행 전에 등록하고, 원래 rectangle 생성 규칙에서 정답 라벨을 독립적으로
작성합니다. 모델 출력은 정답 작성에 사용하지 않습니다. 첫 개정은 1개가 미검토라
전체 점수를 보류합니다. 다음 개정은 검토 선언을 완료하고 실제 ONNX rule A/B를
실제 COCO evaluator로 계산합니다. IoU0.5에서 A는 TP4/FP0/FN0, B는 TP2/FP0/FN2가
되며 기존 개정/평가 파일 불변성과 모든 다운로드 SHA를 확인합니다. 이는 평가
pipeline 산술 검증이며 학습된 semantic detector의 정확도 측정이 아닙니다.

현재 한도는 개정당 이미지256개·class256개·이미지당 box100개, PNG8MiB/2MP입니다.
Dataset manifest16MiB, prepared evaluation16MiB, report12MiB, terminal export32MiB이며
원래 source job의 전체 manifest를 고정하므로 frame 수가 적어도 크기 한도에 닿을
수 있습니다. 대용량 원격 object storage·표준 COCO dataset import·polygon/segmentation·
검토자 권한/승인·분산 평가·학습 데이터 독립성 검사는 후속 범위입니다.

[정확한 API 계약](datasets-contract.md) · [SDK/CLI](../sdk/python/README.md) ·
[현재 인수 상태](status.md) · [교체형 evaluator](../integrations/coco/README.md)
