# 원본 이미지 모델 재평가 · Case → 실제 추론 → 평가 → 같은 Case

이 기능은 기존 객체 참조시험과 다르게 **기준 평가에 사용한 원본 카메라 메시지와
등록된 모델 파일을 실제로 사용**한다. 전체 baseline split을 재추론하고 같은 dataset 개정·
라벨 매핑·평가 기준으로 평가한 뒤, 해당 Case에 속하는 이미지 결과만 연결한다.
학습형 E2E 주행·멀티카메라/시계열 정책·새 센서 조건 생성·학습 기능은 아니다.

## 화면에서 완료하는 업무

1. Investigation cases에서 완료된 검출 평가가 연결된 Case를 연다.
2. **Configure recorded model replay**에서 연결된 baseline, 등록 model ID와 정확한 revision,
   재평가 목적을 선택한다. 모델은 기존 Perception experiments의 모델 등록 기능으로 가져온다.
3. **Preview full replay workload**는 원본·dataset·runtime·모델 선언과 전체 분모를 확인한다.
   계산을 시작하거나 Case를 저장하지 않는다. Case 한 이미지 때문에 split 전체를 실행할 수 있음을 표시한다.
4. 모델 파일을 신뢰하고 전체 작업량에 동의한 뒤 **Submit recorded inference**로 실제 계산을 제출한다.
   사용자가 확인한 preview hash가 바뀌면 재검토를 요구한다. 기존 queue와 Case 링크는 한 transaction이다.
5. 추론이 종료되면 **Evaluate stored replay predictions**를 눌러 원래 COCO 평가 경로로 제출한다.
   후속 단계를 GET/polling에서 자동 실행하지 않는다. 실패·취소된 추론도 분모를 유지한 채 평가 가능하다.
6. **Capture model replay outcome**은 종료 평가와 비교 hash를 고정하고, Case의 정확한 원본에
   대응하는 평가 행만 연결한다. 전체 A/B는 **Inspect full replay A/B comparison**에서 조사한다.
7. JSON export는 모든 시도와 전체 split 비교를 포함한다. 상태가 바뀌면 이전 expected_hash는409다.
   동일 source를 공유하는 과거 사례/평가/조건은 덮어쓰지 않으며, Case 상태도 자동 해결로 변경하지 않는다.

각 제출 버튼은 stable Idempotency-Key와 expected_revision을 사용한다. 응답을 못 받으면
같은 화면/본문으로 재시도하며 새 job을 만들지 않는다. 다른 사용자의 개정과 자동 병합하지 않는다.
추론과 평가의 취소 요청은 각각 기존 job cancel 경로를 사용한다. 요청은 즉시 종료의 보증이 아니다.

## 고정 범위와 실패 처리

- 첫 경로는 하나의 recording/channel/decoder로 구성된 **전체 split 1–64 images**, source payload 합계64MiB다.
  초과·혼합 채널·중복 원본 입력은 거절하고 일부만 실행하지 않는다. 실패 이미지 한 장만 골라 AP를 비교하지 않는다.
- 기준 평가가 Case에 이미 연결돼 있어야 하며, completed·sealed·전체 라벨/예측 coverage 완결이 필요하다.
  baseline이 부적합하면 기존 평가/데이터 조사 기능으로 돌아가 수정한다. 라벨을 자동 생성/승인하지 않는다.
- 모델은 같은 profile과 label vocabulary를 유지한다. class mapping·threshold/NMS/max detections·seed·
  dataset revision/split·evaluator policy는 baseline에서 고정한다. 현재 지정된 ONNX CPU profile이 실행 대상이며,
  임의 멀티입력 네트워크나 외부 학습 모델을 모두 지원한다는 뜻이 아니다.
- preview에서 원본 메시지 bytes를 검증하고 실제 worker도 원본·PNG/decoder·모델 파일을 검사한다.
  API preview의 모델 선언 hash만으로 ONNX 파일의 실행 안전성이나 전체 보안을 보증하지 않는다.
- 모델 파일은 신뢰할 수 있는 입력이어야 한다. 기존 child process는 보안 sandbox가 아니며,
  새로운 임의 실행 코드·URL·다운로드 명령을 요청으로 받지 않는다.
- 추론 실패/미실행/취소를 baseline prediction으로 채우지 않는다. 평가가 불완전하거나 runtime 의미가
  달라지면 aggregate delta를 보류한다. 0건 검출은 성공한 추론의 미검출로 남으며 실패와 구분한다.
- 원래 오류는 사라지지 않는다. I2 진단 태그는 과거를 포함한 pinned links를 보고, 개선 판단은
  별도 A/B에서 확인한다. 인지 정확도나 ODD/센서 조건 coverage·안전성을 자동 인증하지 않는다.
- Case당 최대8개 재평가 시도이며 기존 평가 링크32개와 manifest2MiB 제한을 유지한다.
  capacity 부족으로 capture가 거절돼도 원래 추론/평가 결과는 해당 job에 남는다.

## API / SDK

| API (prefix `/api/v1`) | 역할 |
| --- | --- |
| `POST /cases/{id}/model-replays/preview` | 읽기 전용 full-split/모델 preflight, jobs_created=0 |
| `POST /cases/{id}/model-replays` | preview hash·명시적 동의로 일반 추론 job + Case 개정 저장 |
| `POST /cases/{id}/model-replays/{plan}/evaluate` | 종료 추론만 사용해 일반 평가 job + Case 개정 저장 |
| `POST /cases/{id}/model-replays/{plan}/capture` | 종료 평가 hash·A/B hash 및 해당 Case 행 연결 |
| `GET /cases/{id}/model-replays?revision=N` | pinned Case 개정에서 전체 시도 및 현재 job 상태 조회 |
| `GET /cases/{id}/model-replays/export?revision=N&expected_hash=...` | 같은 내용의 JSON만 내보내기 |

SDK는 `preview_case_model_replay`, `submit_case_model_replay`, `evaluate_case_model_replay`,
`capture_case_model_replay`, `case_model_replays`, `export_case_model_replays`를 제공한다.
UI·SDK·Agent는 같은 인증 API를 사용한다. 다운로드는 동일 서버 고정 경로와 크기/hash를 확인한다.

DB는schema12를 유지한다. 새 Case manifest `axiom-investigation-case/4`에 실행 연결을 추가하며
과거case/1/2/3의 bytes는 바꾸지 않는다. 조건 편집·일반 개정·기존 참조 재시험과 함께 유지된다.
새 package를 사용하고, 구버전 앱으로 새 Case를 편집하지 않는다. backup/restore와 설치는
별도 복원본에서 인수하며, 원본 데이터나 기존 시험 패키지를 덮어쓰지 않는다.

## 다음 통합 경계

이 단계는 기존 registered ONNX/COCO 어댑터를 Case에 연결한 것이다. 새로운 공급사 SDK,
실제 고객 모델의 수용, 시계열·다중센서 E2E 정책 또는 GPU/훈련 adapter의 인수 완료는 아니다.
후속은 실제 사용할 외부 모델의 입출력 계약/지원 데이터와 내부 사용자 업무에 맞춰 선택한다.
객체참조 주행의 물리 상수 개선을 이 기능의 선행조건으로 다시 삼지 않는다.
