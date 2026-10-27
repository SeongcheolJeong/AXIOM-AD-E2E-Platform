# I1 · 로그 구간과 평가를 연결하는 조사 사례

`Investigation cases`는 데이터 조사와 모델 평가가 공유하는 영속 객체다. 관심 현상·실패 후보를
원본 구간에 연결하고, 같은 원본을 사용한 평가를 추가하면서 조사 기록을 개정한다.
이는 원인 자동 판정, 검증 통과, 사람의 인증된 승인, ODD coverage 또는 재시험 생성 기능이 아니다.

## 화면에서 사용하기

1. **Data catalog → Recordings**에서 로그와 채널·커서를 선택하고 **Create case from log interval**을 누른다.
   시작/종료 log-time(ns)을 확인해 제목·관측 메모·선택적 담당자 이름을 저장한다. 구간은 양끝 포함이다.
2. **Detection datasets → Evaluations**에서 평가 이미지 행을 선택하고 **Create investigation case**를 누른다.
   평가에 고정된 실제 recording/channel/ordinal로 단일 메시지 Segment를 만든다. 근접 timestamp로 대체하지 않는다.
3. **Investigation cases**에서 원본 메시지와 평가를 조사한다. **Open original recording at anchor**는
   해당 채널의 정확한 ordinal부터 열고, **Open linked evaluation**은 해당 평가·이미지를 연다.
4. 다른 평가의 같은 이미지를 조사할 때 **Existing case ID**를 입력하면 같은 Case에 평가 근거를 추가한다.
   평가 이미지가 고정 Segment의 recording/hash/channel/ordinal에 포함되지 않으면 거절한다.
5. **Edit investigation**으로 메모·담당·상태를 개정하고, **Download case revision**으로 정확한 개정을 내보낸다.
   이전 개정과 원래 평가·데이터셋·로그는 변경하지 않는다. 새로고침·직접 주소·재시작 후에도 같은 Case를 조회한다.

Case 생성 또는 개정의 응답이 유실되면 같은 화면의 재시도는 같은 본문과 Idempotency-Key를 사용한다.
현재 개정이 다른 작성자에 의해 바뀌었다면409이며 새로 읽은 후 편집해야 한다. 자동 병합하지 않는다.
담당자는 사용자가 선언한 문자열이다. 사용자 인증·인가·검토 서명과 동일하게 취급하지 않는다.

## 정확한 원본 구간의 의미

이 단계는 **하나의 recording, 하나의 channel**에 대한 참조형 Segment다. 새 MCAP/동영상 clip 파일이나
새 장면을 생성하지 않는다. 다른 센서의 clock 변환·보정·동기화는 검증하지 않는다.

원본 recording hash, channel 선언, index hash와 선택된 모든 메시지의 ordinal·log/publish timestamp·
size·SHA를 보존한다. 생성 시 해당 메시지 bytes를 검사하고 Case의 source samples 조회 때 다시 검사한다.
원본 전체 파일을 매번 다시 읽거나 차량 물리/센서 정확도를 검증하는 것은 아니다.

시간 구간 선택은 비단조 파일 순서도 log-time/ordinal로 정렬하며 동일 시각의 모든 메시지를 포함한다.
평가에서 만든 단일 이미지 선택은 정확한 ordinal 하나만 포함한다. timestamp가 같아도 다른 메시지로
연결하지 않는다. 빈 구간은422, 2000개 메시지 또는64MiB를 넘는 구간은413으로 거절하며 잘라서 저장하지 않는다.
원본이 나중에 손상되면 원본 조회는 실패하지만, 기존 Case 개정 자체의 export는 원래 근거로 유지된다.

linked evaluation은 terminal manifest·dataset revision·image identity·행 상태와 진단을 보존한다.
미검토·누락·실패 표본은 그 상태로 남으며 diagnostics가 없는 표본을 정상 검출0 또는 확정 실패로 바꾸지 않는다.
서로 다른 평가를 한 사례에 링크하는 것은 평가 조건이 동등하거나 성능 비교가 유효하다는 뜻이 아니다.
A/B 통계 비교에는 기존 comparison 계약을 별도로 사용한다. 최대32개의 평가/이미지 링크를 지원한다.

## UI·API·SDK 공통 계약

| 요청 | 역할 |
| --- | --- |
| `POST /api/v1/cases` | `CaseCreate`, 필수 `Idempotency-Key`; 새 Segment와 Case r1을 한 transaction으로 생성 |
| `GET /api/v1/cases` | recording_id/evaluation_id/image_id/state로 검색, limit/offset·next_offset 페이지 |
| `GET /api/v1/cases/{id}?revision=N` | 정확한 개정; 생략 시 현재 개정 |
| `POST /api/v1/cases/{id}/revisions` | expected_revision·Idempotency-Key로 새 개정, 선택적 add_evaluation |
| `GET /api/v1/cases/{id}/samples` | 고정 원본 메시지의 실제 bytes 검증·기존 decoder preview, limit/offset 페이지 |
| `GET /api/v1/cases/{id}/export?revision=N` | 전체 고정 Segment와 평가 참조가 있는 JSON, 원본 파일·이미지는 미포함 |

`GET /recordings/{id}/samples?channel_id=C&from_ordinal=N`은 정확한 ordinal을 포함하는
새 시작 selector다. `at_ns`, `from_start`, `after_ordinal`과 동시에 사용할 수 없다.
선택 ordinal이 해당 채널에 없으면404이며 다른 메시지로 대체하지 않는다.

```python
from axiom_sdk import AxiomClient

with AxiomClient("http://127.0.0.1:8100", token=token) as api:
    case = api.create_case(
        title="Review missed object", note="Observed candidate; cause is not confirmed.",
        source={"kind": "evaluation", "evaluation_id": evaluation_id, "image_id": image_id},
        idempotency_key="review-exact-image-001",
    )
    updated = api.revise_case(
        case["id"], expected_revision=case["revision"], idempotency_key="review-note-002",
        title=case["title"], note="Compare the earlier model using this same source.",
        assignee="Declared investigator", state="investigating",
    )
    api.export_case(case["id"], "case-r1.json", revision=1)
```

상태는 open/investigating/resolved/dismissed이며, 해결·제외 상태에는 별도 disposition_note가 필요하다.
이 상태는 조사 메모이며 평가 점수·요구사항 판정·출시 승인에 영향을 주지 않는다.

## DB·운영 호환성

Schema12는 `investigation_segments`, `investigation_cases`, `investigation_case_revisions`,
`investigation_case_evaluations` 네 테이블을 추가한다. 기존 업무 행·원본 파일·평가 manifest는 재작성하지 않는다.
업그레이드 전 서비스를 중지하고 DB와 파일의 일관된 백업을 남긴다. 먼저 새 복원본으로 인수한다.
새 backup/verify/restore는 schema11/12를 **그대로 보존**하며, 복원 과정에서 자동 migration하지 않는다.
새 API의 첫 시작에서11→12가 적용된다. 이전 schema11-only 프로그램/패키지는12를 열 수 없다.
되돌리기는 schema 번호 수정이 아니라 이전 앱과 **업그레이드 전 백업의 새 복원본**으로 한다.

기존 token·로컬 실행 경계는 유지한다. 내부 팀의 프로젝트 격리나 실측 데이터 수용이 완료된 것은 아니다.
I2의 첫 [조건·검색·사례coverage](case-conditions.md)가 연결됐다. I3의 시험 생성·campaign 환류는 아직 미구현이다.
