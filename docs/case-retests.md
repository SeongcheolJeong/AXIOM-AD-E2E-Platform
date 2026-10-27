# I3 first slice · Case → authored retest → campaign → captured outcome

## 재시험의 의미

This is a manually authored **object-reference** test inspired by an investigation.
The source recording is provenance, NOT an input to the reference engine. Source
camera pixels, ONNX models, lighting, image size, real vehicle physics and the
original failure are not reconstructed. Passing this test never resolves the case.

## 화면에서 사용

1. Investigation cases에서 Case를 열고 **Define derived retest**를 누른다.
2. 추종·선행차 제동·객체 횡단 template 중 하나를 선택한다. 가설/가정, 기본 속도·간격·
   trigger·마찰·반응 지연·시험 시간, 명시적인 gap/seed 변형과 모든 판정 기준을 확인한다.
   화면에서 gap은 각 variant가 기본 gap을 override한다. API는 기존 여섯 parameter override를 지원한다.
3. reference-only 제한을 확인하고 **Publish retest definition**을 누른다.
   새 Scenario r1, 해당 기준, Suite r1과 Case의 계획 링크를 함께 저장한다. 아직 Job은 없다.
4. 고정된 입력·판정 기준을 펼쳐 확인한 뒤 **Submit N variants to queue**로 계산을 요청한다.
   일반 campaign/Job을 사용한다. 이 작업은 계산을 소비하며 같은 입력의 재요청도 새 attempt가 될 수 있다.
   응답 유실 재시도만 같은 key/본문으로 중복 방지된다. 개발자가 바뀐 본문을 임의로 재실행하지 않는다.
5. Case에서 각 variant의 job 상태·실제 값·기준·판정을 조사한다. 정확한 Run/Job으로 이동 가능하다.
   완료와 통과는 별개이며 취소/누락/중단은 not_evaluated로 남는다.
6. 종료 후 **Capture finished outcome**은 실패/미완결도 기존 Baseline 봉인 기능으로 저장하고
   같은 Case의 새 개정에 연결한다. Case 상태·원래 인지평가·조건 태그를 자동으로 바꾸지 않는다.
7. **Retest activity**는 recording 범위의 미제출 계획, 모든 시도와 변형별 상태를 함께 보여 준다.
   원본 조건은 계획 작성 당시의 Case 개정에 고정되며 나중에 태그를 수정해도 시험 조건처럼 바뀌지 않는다.

## 지원과 한계

- template: `reference-follow/1`, `reference-lead-brake/1`, `reference-crossing/1`.
- built-in Object Reference adapter만 사용한다. 같은 ID의 외부 대체 plugin은 거절한다.
- 계획당 변형1–8개, 기준1–8개, 시도최대8개; Case당 계획최대8개; 실행시간2–30초.
- 시나리오·모델의 품질 최적화가 아닌 연결 인수다. CARLA·센서 simulation·학습형 E2E 실행은 이번 경로에 없다.
- `verified_sensor_condition_cells=0`이다. source의 야간·작은 객체 태그가 있어도 해당 조건의
  실제 센서 시험을 수행한 것으로 집계하지 않는다. 반복 시도도 독립 장면 coverage로 세지 않는다.
- 조건 coverage(I2)는 기존 사례 분모/정의를 유지한다. 재시험 activity는 별도의 표이며
  실측 ODD coverage·안전성 확률·인지성능 개선 증명이 아니다.
- report는 live 또는 captured다. live의 실행 상태가 바뀌면 과거 expected_hash export는409.
  captured 결과는 기존 Baseline hash로 고정한다. 늦은 변경을 과거 capture에 덮어쓰지 않는다.

## 저장·원자성

DB는schema12를 유지한다. 새 Case manifest `axiom-investigation-case/3`에 retest 정의·시도·
capture 참조를 추가하고 기존case/1·case/2와 과거 bytes/hash는 유지한다. 별도의 미완성 queue나
시험 판정기를 만들지 않는다. Suite/Campaign/Baseline 서비스의 transaction-composable helper를
사용해 작성·큐 제출·Case 연결·결과 capture가 각각 하나의 transaction에서 commit된다.
중간 실패 시 orphan scenario/suite/job/link를 남기지 않는다. 파일/DB 백업은 기존 도구로 수행한다.

문서·패키지의 개별 모델 이름은 외부 제품 연동 완료를 의미하지 않는다. 현재 기능은 네 영역의
내부 데이터 흐름 연결이며 Foretellix/Applied SDK를 호출하거나 로그를 정확한 장면으로 복원하지 않는다.

## API·SDK

모든 경로는 `/api/v1` 아래이고 기존 인증 경계를 따른다.

| 경로 | 역할 |
| --- | --- |
| `GET /case-retest-templates` | template·제한·생성 schema |
| `POST /cases/{id}/retests` | expected_revision과 Idempotency-Key로 계획 작성 |
| `POST /cases/{id}/retests/{plan}/campaigns` | expected_revision·plan_hash와 key로 실행 및 연결 |
| `POST /cases/{id}/retests/{plan}/campaigns/{campaign}/capture` | 종료 결과를 영속 snapshot으로 연결 |
| `GET /cases/{id}/retests?revision=N` | 정확한 Case 개정의 계획 및 live/captured 결과 |
| `GET /cases/{id}/retests/export?revision=N&expected_hash=...` | 같은 내용만 JSON export |
| `GET /case-retest-activity?recording_id=...` | 전체 계획/변형/시도의 상태, 원본 조건은 출처로만 표시 |
| `GET /case-retest-activity/export?...&expected_hash=...` | 전체 선택 범위 내보내기 |

SDK: `case_retest_templates`, `create_case_retest`, `submit_case_retest`, `capture_case_retest`,
`case_retests`, `export_case_retests`, `case_retest_activity`, `export_case_retest_activity`.
쓰기에는 명시적key/expected_revision이 필요하며 실패 응답의 자동 resubmit은 없다.
원본이 바뀌었거나 다른 Case/plan/campaign을 잘못 연결하면 거절한다.

## 후속 범위

동일 원본의 실제 검출 모델 재평가는 [기록 모델 재평가](case-model-replays.md)에 별도로 연결했다.
외부 실행 adapter, 지원되는 센서 조건의 실제 재현,
유효 ODD 조합/시험 필요성 정의와 coverage 환류는 추가 인수 대상이다. 새 이득/물리 상수 탐색을
통합플랫폼 개발의 선행조건으로 삼지 않는다. 한 adapter마다 실제 입력·출력·실패 경계를 인수한다.
