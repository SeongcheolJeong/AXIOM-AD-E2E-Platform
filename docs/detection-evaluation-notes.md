# Detection evaluation: 근거와 적용 경계

원문 확인일: **2026-09-13**. Optional adapter `coco-bbox@0.1.0`의 계산 정책은
`coco-bbox-stored/1`이다. 실제 고정 패키지를 호출하는 산술·프로세스 시험을
구현했다. 데이터셋/저장 평가의 API 인수 상태는 [status](status.md), 정확한
리소스 계약은 [datasets-contract](datasets-contract.md)를 따른다.

## 패키지와 라이선스

조사일 PyPI 최신 릴리스는 **pycocotools 2.0.11**, 게시일은 2025-12-15다.
PyPI 공식 배포 저장소는 원래 COCO API의 패키징·수정 fork인
`ppwwyyxx/cocoapi`이며, 배포 provenance가 고정 소스 commit
`ac87f5077ad6b8864c2dc5e93d14cae62d1db05a`를 연결한다. Source distribution
SHA-256은 `34254d76da85576fcaf5c1f3aa9aae16b8cb15418334ba4283b800796bd1993d`다.
실제 설치 wheel의 hash는 해당 플랫폼 배포 항목을 사용해야 한다.
[PyPI 릴리스와 배포 provenance](https://pypi.org/project/pycocotools/2.0.11/).

PyPI 표기는 FreeBSD이고 고정 저장소의 `license.txt`는 두 조건의 BSD 문구와
FreeBSD disclaimer를 포함한다. 재배포 시 저작권·조건·면책 원문 보존이 필요하다.
이 문서가 별도 상용 법률 승인을 의미하지는 않는다.
[고정 원문](https://raw.githubusercontent.com/ppwwyyxx/cocoapi/ac87f5077ad6b8864c2dc5e93d14cae62d1db05a/license.txt),
[저장한 원문](../integrations/coco/third_party/pycocotools-license.txt).
Adapter는 NumPy 2.5.3도 고정한다. 코드 라이선스는 평가 대상 모델/이미지의 권리를
포함하지 않는다. COCO annotation/website는 CC BY 4.0이고, COCO는 원본 이미지
저작권을 소유하지 않는다. 각 이미지 조건은 별도 확인 대상이다.
[COCO 공식 이용 조건](https://raw.githubusercontent.com/cocodataset/cocodataset.github.io/master/dataset/termsofuse.htm).

## 공식 동작에서 중요한 구분

COCO AP는 여러 IoU와 class에 걸친 PR 요약이다. 기본 grid는 IoU
0.50:0.05:0.95, recall 0:0.01:1, maxDets 1/10/100이다. COCO의 공식 scale 설명은
segmentation mask 면적을 사용한다. 사람이 작성한 bbox-only 데이터의 면적을
그것과 혼동하면 안 된다.
[공식 평가 설명](https://raw.githubusercontent.com/cocodataset/cocodataset.github.io/master/dataset/detection-eval.htm).

고정 Python 구현은 `useCats=1`에서 **이미지/class별** 상위 100개를 사용한다.
공식 웹의 이미지 전체 100개 설명과 다르므로 본 정책은 실제 구현의 범위를 명시한다.
점수 정렬은 stable mergesort, 매칭은 IoU별 greedy이며 PR은 precision envelope의
101개 recall 표본이다. GT가 없는 class는 `-1`로 제외된다. 기본 summarizer의
주 AP는 maxDets=100을 찾으므로 임의 cap으로 바꾸지 않는다. 일반 `ignore`는 bbox
준비 단계에서 `iscrowd`로 덮어써진다. Area 경계 32²와 96²는 인접 구간에 함께
포함된다. 이 adapter는 해당 동작을 수정하지 않는다.
[고정 COCOeval 구현](https://raw.githubusercontent.com/ppwwyyxx/cocoapi/ac87f5077ad6b8864c2dc5e93d14cae62d1db05a/PythonAPI/pycocotools/cocoeval.py).

BBox는 `[x,y,width,height]`의 연속 좌표이고 `+1`을 더하지 않는다. Crowd와의
overlap은 교집합/검출 면적이며 하나의 crowd가 여러 검출을 무시시킬 수 있다.
[실제 bbox IoU 구현](https://raw.githubusercontent.com/ppwwyyxx/cocoapi/ac87f5077ad6b8864c2dc5e93d14cae62d1db05a/common/maskApi.c).
`COCO.loadRes([])`는 첫 원소 접근 때문에 실패하므로 빈 검출은 동일
images/categories와 `annotations=[]`인 COCO index로 구성한다. 빈 검출을
실패한 추론의 대용으로 쓰지는 않는다.
[고정 결과 로더](https://raw.githubusercontent.com/ppwwyyxx/cocoapi/ac87f5077ad6b8864c2dc5e93d14cae62d1db05a/PythonAPI/pycocotools/coco.py).

## 구현된 metric 계약

정확한 공개 타입은 `backend/src/ade2e/evaluator_runtime.py`에 있다.
Adapter는 API가 검증한 이미지·예측 내용을 전달받으며 PNG를 다시 해석하거나
최신 데이터셋/추론 결과를 조회하지 않는다. Canonical image 순서, annotation 순서와
prediction 순서를 유지한다. 따라서 동일 점수·동일 overlap의 tie도 재현할 수 있다.

| 항목 | 정책 |
| --- | --- |
| 좌표 | 원본 PNG의 유한한 positive-area pixel xyxy. 경계 밖/퇴화 box 거절 |
| 분류 | 고정 taxonomy와 core의 명시적 class mapping. 자동 이름 유사도 매칭 없음 |
| GT | 일반 box와 명시적 iscrowd. 임의 ignore/segmentation/RLE 입력 거절 |
| AP/AR | 고정 COCO 기본 grid와 maxDets. 값은 0..1, 미정의는 null |
| Scale | `area_source=bbox_width_times_height`, mask-area 지표와 구분 |
| 진단 | 선택 IoU에서 모든 저장·mapping·추가 score-filter 통과 검출(max300/image)을 별도 COCOeval로 매칭 |
| 진단 분모 | noncrowd GT, 별도 crowd_gt, TP/FP/FN/crowd_ignored와 source ID 쌍 보존 |
| PR | 선택 IoU의 COCO 101-recall precision, class별 max100 기준. GT 없는 class는 null |
| Score | 추가 필터는 `score >= threshold`. 원래 추론 threshold/NMS/global cap 이전 후보는 복구하지 않음 |

진단 precision=`TP/(TP+FP)`, recall=`TP/(TP+FN)`,
F1=`2TP/(2TP+FP+FN)`이며 해당 분모가 0이면 null이다. 이 micro 수치와
COCO의 class 평균 AP/AR는 서로 다른 질문에 답한다. AP 밖으로 제외되는
absent-class FP도 진단 count에는 남는다. Crowd와 매칭된 pair의 IoU는 일반 IoU와
분모가 다르므로 UI에서 같은 값의 의미로 섞지 않아야 한다.

모든 selected split image가 report row에 남는다. `reviewed`의 빈 annotation만
검토자가 선언한 negative다. Unreviewed/missing/failed/input_changed는 빈 검출로
치환하지 않는다. 전체 row가 유효하지 않으면 `metrics`, class AP/PR은 모두 null이고
`diagnostic_scope=evaluated_rows`의 부분 row 진단만 표시한다. 완전한 negative-only
데이터의 경우 coverage는 complete여도 AP/recall이 미정의일 수 있다.

이 결과의 대상은 **저장된 inference pipeline의 predictions**다. 낮은 점수 후보나
NMS/global limit에서 제거된 후보까지 평가한 raw model PR이라고 주장하지 않는다.
원래 model/runtime/전처리/threshold/NMS/limit과 source hash는 core manifest가 보존한다.
Dataset revision, 분할, mapping, evaluator policy 또는 threshold가 다른 결과는
그 차이를 드러내야 하며 동일 조건의 모델 개선으로 자동 판정하면 안 된다.

## 인수 사례

현재 adapter 시험은 다음 산술과 실제 child 경계를 검증한다. 이 입력들은 authored
arithmetic fixtures이며 실차 인지 정확도·주석 품질의 근거가 아니다.

| 입력 또는 변경 | 기대 결과 |
| --- | --- |
| 동일한 GT와 검출 1개 | AP≈1, TP1, FP0, FN0, 실제 pair IoU1 |
| positive GT + 완료된 빈 검출 | AP0, recall0, FN1; 실패로 오인하지 않음 |
| 검토한 negative만 있고 검출 없음 | coverage complete, AP/precision/recall/F1 null |
| positive 정답보다 고득점인 negative-image FP | AP 감소, 해당 음성 row FP 보존 |
| GT 없는 다른 class의 검출 | 해당 class AP/PR null, FP 보존 |
| 정답 뒤 낮은 점수 duplicate | AP≈1일 수 있으나 TP1/FP1/precision0.5 |
| GT `[0,0,10,10]`, prediction `[0,0,5,10]` | IoU0.5, AP50≈1, AP75=0, AP≈0.1 |
| score tie에서 FP/TP 순서 교환 | 고정 순서에 따라 AP가 달라짐; 순서도 입력 증거 |
| 동일 overlap GT tie | 공식 matcher의 선택 ID와 FN ID 보존 |
| 작은 detection 2개가 큰 crowd 안에 있음 | crowd_ignored2, 일반 TP/FP/FN0, detection-area overlap1 |
| 일반 GT와 crowd가 모두 일치 | 공식 nonignored GT 우선 매칭 |
| 32×32 bbox / 큰 unmatched FP | 실제 bbox scale 경계 및 out-of-area FP 무시 동작 |
| 정답이 같은 class 점수 101위 | COCO AP/AR100에서 탈락, all-retained 진단 TP에는 남음 |
| 다른 class 100개 + 정답 class 1개 | Python의 per-category cap에 따라 정답 class 포함 |
| 일부 row 미검토/실패/누락/입력변경 | 전체 분모 유지, aggregate와 class AP/PR withheld |
| NaN/무한대/경계 밖/중복 ID/개수 초과 | native bbox 계산 전에 명시 거절 |
| package version drift | metadata unavailable 및 실행 거절 |
| 실제 child 평가 중 취소 | 소유 프로세스 종료 확인 후 취소 예외 |

실행 한도와 설치는 [optional adapter README](../integrations/coco/README.md)에 있다.
Native import를 API/worker host에서 수행하지 않고, 32 MiB request/response와
120초 operation deadline을 사용하는 공통 managed subprocess를 사용한다.
이 방식은 bounded local evaluation이며 OS 보안 sandbox나 분산 실행을 완성하지 않는다.
