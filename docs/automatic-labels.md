# 자동 라벨링 첫 단위 · 모델 제안 → 미검토 데이터셋 → 사람 검토

2026-09-28. 기존 Collection에서 발행한 dataset을 포함해, 등록된 detector로 빈 미검토
이미지의 라벨 초안을 생성한다. 신규 외부 서비스나 주행 정책이 아니라 기존 ONNX/queue의 실제
활용이다. 모델 예측을 정답으로 승인하거나 정확도·ODD coverage를 인증하지 않는다.

## 사용자 업무

1. Detection datasets에서 정확한 dataset revision을 선택한다. **Automatic label drafts**의
   **Configure automatic labels**를 연다. Collection에서 발행한 dataset도 같은 화면을 쓴다.
2. 신뢰된 등록 모델·개정과 class mapping을 선택한다. 같은 이름은 대응 후보로 보이지만
   알 수 없는 class는 명시적으로 지정하거나 제외해야 한다. score threshold와 목적을 확인한다.
3. **Preview annotation workload**에서 전체/대상/보존 이미지 수, job·recording 수를 확인한다.
   preview는 원본과 PNG bytes를 검사할 뿐 Job·dataset·label을 만들지 않는다.
4. 모델 실행에 동의한 뒤 **Generate label drafts**를 누른다. 기존 perception queue를 사용하고,
   모든 Job과 annotation task intent가 한 transaction으로 발행된다. 새 scheduler는 없다.
5. 모든 대상 이미지의 상태·박스 제안·raw score·제외된 class를 확인한다. 진행 중 각 기존 inference
   job의 취소를 요청할 수 있다. 제안 박스는 원본 이미지에 표시되며 아직 dataset을 바꾸지 않는다.
6. 전체 Job이 종료되면 **Publish unreviewed draft revision**으로 새 dataset revision에 저장한다.
   정상 제안만 저장하고 실패·미실행·변경된 입력은 미검토 상태와 원래 분모에 남긴다.
7. 기존 **Create dataset revision**에서 박스를 수정·추가·삭제하고 이미지마다 검토자·근거·
   검토 여부를 선언한다. 점수가 높거나 검출이 0이라는 이유로 자동 검토 완료하지 않는다.
8. 원본 draft receipt와 task report는 수정 전 모델 출력·정책·hash를 유지한다. UI의 lineage와
   평가 export의 annotation_lineage에 알려진 모델 보조 출처가 남는다.

## 고정 범위

- dataset의 모든 **비어 있는 unreviewed 이미지**를 선택한다. 이미 박스가 있거나 reviewed인
  이미지는 선택하지 않는다. 사람이 작성 중인 draft도 덮어쓰지 않는다.
- dataset 최대256이미지, 선택 payload합64MiB, 최대16개 inference Job; 각 recording/channel/
  decoder별로 묶고 최대64이미지씩 나눈다. 일부를 몰래 제외해 한도를 맞추지 않는다.
- 지정된 ONNX CPU detector profile만 사용한다. 임의 URL·shell 코드·외부 API key를 받지 않는다.
  model subprocess는 보안 sandbox가 아니므로 신뢰된 모델만 등록·실행한다.
- API는 threshold/NMS/max_detections/seed와 class mapping을 고정한다. 화면 첫 버전은
  NMS0.45·max_detections100·seed42를 명시한다. score는 보정된 확률로 취급하지 않는다.
- 검출0은 `empty_prediction`, decode/실행실패는 별도 상태다. 실패를 음성 정답으로 만들거나
  다른 모델 예측으로 채우지 않는다. raw 예측·runtime의 truncation 정보도 보고서에 남긴다.
- task 제출 당시 dataset이 수정되면 publication을 거절한다. 새로운 라벨과 자동 병합하거나
  재시도 명목으로 새 추론을 실행하지 않는다. 같은 요청·키로 응답 유실만 복구한다.

## 출처와 자기 평가 방지

출판은 항상 unreviewed/imported이다. 이미지별 라벨 검토를 일반 editor에서 별도로 선언해야 한다.
이 검토자는 현재 사용자 입력 이름이며 개인 인증·검토 독립성 인증은 아니다.

같은 dataset의 후속 개정, Collection을 통한 명시적인 label source 복사에서도 알려진 model
origin을 보존한다. 이 첫 버전에서는 수동 수정만으로 origin을 제거하지 않는다.
선택한 reviewed 표본의 라벨 생성 모델과 평가 모델의 가중치 SHA가 같으면 신규 평가 제출을 거절한다.
모델 이름/등록 ID만 바꿔 우회할 수 없다. unreviewed 평가에는 기존대로 aggregate 점수가 없고,
출처 snapshot도 export에 남는다. 다른 모델로 평가한다고 독립적 정답이 보장되는 것은 아니다.

외부에서 JSON을 다시 작성하거나 파일로 출처를 지운 뒤 새로 등록하는 행위까지 추적하는
보안 장치는 아니다. 독립적인 정답 데이터셋 수용과 실제 모델 정확도 검증은 별도 절차다.

## 저장·API·SDK

Schema14는 `annotation_tasks`, `annotation_publications` 두 테이블만 추가한다. 기존 dataset
manifest 형식과 과거 bytes/hash는 유지한다. 새 앱은schema11–14의백업/복원과14로의additive
이관을 지원한다. 운영 원본에서 바로 시험하지 말고 백업의 새 복원본을 사용한다.

| API (`/api/v1`) | 역할 |
| --- | --- |
| POST `/datasets/{id}/annotation-tasks/preview` | 읽기 전용 원본·작업량·정책 preflight |
| POST `/datasets/{id}/annotation-tasks` | 동의/preview hash/개정/key를 확인해 기존 추론 queue와 intent 생성 |
| GET `/datasets/{id}/annotation-tasks` | limit/offset으로 작업 이력 조회 |
| GET `/annotation-tasks/{id}` | 모든 대상 상태·제안·raw output·publication 조회 |
| POST `/annotation-tasks/{id}/publish` | 종료 report hash/개정/key로 새 미검토 dataset 개정 발행 |
| GET `/annotation-tasks/{id}/export?expected_hash=...` | 상태가 같은 전체 report만 내보내기 |
| GET `/annotation-tasks/{id}/publication` | 고정된 원본 draft receipt 다운로드 |
| GET `/datasets/{id}/annotation-lineage?revision=N` | 정확한 개정의 알려진 model origin 조회 |

SDK: `preview_annotation_task`, `create_annotation_task`, `annotation_tasks`, `annotation_task`,
`publish_annotation_drafts`, `annotation_lineage`, `export_annotation_report`. UI와동일한API를사용한다.
자동 재제출·자동 라벨 승인·GET에서실행시작은없다. scene tagging·유사검색·tracking/3D주석은
이번 detector bbox초안 기능과 별도다.
