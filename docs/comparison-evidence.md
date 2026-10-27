# 비교 근거 고정과 내보내기

캠페인의 **Live comparison**은 현재 작업 상태를 다시 읽습니다. 검토·공유할 결과를
남기려면 **비교 근거 저장**으로 해당 시점의 기준선과 후보 결과를 고정합니다.
저장 가능한 후보는 모든 member가 terminal 상태인 `finished` 캠페인입니다.
작업이 실패·취소·중단됐거나 일부 근거가 누락된 캠페인도 저장할 수 있지만,
누락된 시험과 판정은 그대로 남으며 승인·인증을 뜻하지 않습니다.

## 저장되는 내용

- 기준선의 전체 canonical manifest, 원본 SHA-256과 byte 수
- 후보 캠페인의 고정 suite·시나리오·판정 기준, job/run ID, 실제 metric와 입출력 hash
- case별 비교 판정·이유·전체 분모 및 사용한 comparison policy version
- 저장한 이름·검토 메모·시각과 전체 manifest의 SHA-256

저장된 화면과 다운로드는 현재 캠페인이나 시나리오를 다시 조회해 계산하지 않습니다.
큰 trajectory와 센서 bytes는 복제하지 않으며, 보존된 run/artifact ID로 원래 근거를
찾습니다. 원본 파일까지 보존하려면 관련 run artifact의 보존 정책도 함께 관리해야 합니다.

## API

모든 경로는 `/api/v1` 아래에 있으며 기존 bearer 인증을 사용합니다.

```http
POST /api/v1/comparisons
Idempotency-Key: review-candidate-001
Content-Type: application/json

{
  "campaign_id": "후보 캠페인 ID",
  "baseline_id": "고정 기준선 ID",
  "name": "검토용 비교 결과",
  "note": "비교 범위와 미평가 항목에 대한 검토 메모"
}
```

201 응답은 summary와 `manifest`를 포함합니다. Summary의 `campaign_label`,
`baseline_name`, `suite_revision`, `counts`, `candidate_verdict`,
`candidate_evaluation_complete`도 저장 시점의 값입니다. `manifest.candidate`는 후보
전체 보고서, `manifest.baseline`은 기준선의 고정 원문, `manifest.report`는 비교 결과입니다.

응답을 잃으면 같은 `/comparisons` endpoint·키·본문으로 재요청합니다. 기존 키는
현재 원본을 다시 평가하기 전에 확인하므로 같은 저장 결과를 반환합니다. 같은 키에
다른 이름이나 메모를 보내도 409입니다. 새 검토 산출물에는 새 키를 사용합니다.
멱등성 키는 `/campaigns`, `/baselines`, `/comparisons` 각각의 범위입니다.

| 요청 | 결과 |
| --- | --- |
| `GET /comparisons?campaign_id=&suite_id=&limit=100` | 최신 저장순 summary 배열, 선택 필터·limit 1–100 |
| `GET /comparisons/{id}` | 저장된 summary와 manifest |
| `GET /comparisons/{id}/export` | DB에 저장한 canonical JSON 원본 bytes |

다운로드의 `X-Content-SHA256`과 응답 summary의 `manifest_hash`가 같은 내용을
가리킵니다. 서버는 읽을 때 bytes의 크기·SHA-256, 내부 기준선 hash와 참조 관계,
검색용 metadata와 manifest의 일치를 확인합니다. 손상은 409, 없는 ID는 404입니다.
진행 중 후보 저장은 409, 잘못된 본문·키는 422, 32 MiB 저장 한도 초과는 413입니다.

## 비교와 미평가의 의미

서로 같은 case key를 비교하며 추가·삭제된 case도 전체 분모에 남깁니다. 시나리오
입력·revision·판정 기준·evaluator version·adapter ID와 version·지표 단위·fidelity·
결과 schema가 다르면 `not_comparable`입니다. 양쪽에 필요한 평가 근거가 없으면
`not_evaluated`로 남기며 과거 통과나 0으로 채우지 않습니다.

같은 이름의 metric도 서로 다른 엔진에서 측정법이 다를 수 있습니다.
`policy_version`은 비교 규칙을 고정하며 물리 모델의 동일성이나 인과관계를 입증하지
않습니다. 특히 adapter version 변화가 현재 판정기와 관측기를 함께 바꿀 수 있으므로
이번 구현에서는 비교 불가로 표시합니다. TTC의 null은 해당 관측 계약에서의
예측 충돌 없음이며, null 사이의 차이를 0이나 Infinity로 내보내지 않습니다.

## 저장 경계

후보 terminal 상태, 검증된 기준선, 비교 결과와 멱등성 키는 하나의 SQLite writer
transaction에서 읽고 저장합니다. 이 과정에서 엔진을 실행하거나 job 상태를 바꾸지
않습니다. 실패한 저장은 부분 comparison이나 소비된 키를 남기지 않습니다.
Schema 4의 `test_comparisons` 테이블이 canonical bytes를 보관합니다.

각 manifest의 한도는 32 MiB입니다. 이 수치는 전체 프로세스 메모리 한도나 대규모
조직 운영 보장이 아닙니다. 목록은 summary만 반환하며 manifest를 한 행씩 검증합니다.
SHA-256은 저장 내용의 변경 여부를 확인하며 전자서명·승인자 신원·외부 데이터의 진위를
대신하지 않습니다. 실차 안전성, 실제 CARLA 센서 정합성, 조직별 승인 절차는 별도 gate입니다.

시험·캠페인 계약은 [suites-contract.md](suites-contract.md), 실행과 중단 복구는
[execution-jobs.md](execution-jobs.md), 지표의 측정 범위는 [metrics.md](metrics.md)를 참고하세요.
