# I2 첫 단위 · 조건 태그·검색·사례 기반 coverage

`Investigation cases`에서 원본 구간의 조건을 선언하고 조건/진단으로 사례를 검색하며,
`Condition coverage`에서 미분류까지 포함한 사례와 연결 평가의 현황을 조사한다.
이는 네 회사의 제품을 직접 연동한 기능, 자동 영상 인식, ODD 검증 또는 시나리오 생성이 아니다.

## 화면 사용

1. Case를 열어 **Edit scene conditions**를 선택한다. Lighting, Primary target, Target image size에서
   아는 값만 선택한다. 원본 확인 근거와 선언자를 입력한다. 모르는 항목은 비워 둔다.
2. 검토 확인을 체크하지 않으면 draft, 명시적으로 확인하면 reviewed로 새 Case 개정을 발행한다.
   조건/선언자/메모를 다시 편집하면 검토 체크가 풀린다. 모든 값을 비워 새 개정을 만들면 분류를 철회한다.
3. 사례 목록에서 조건의 교집합, 검토 상태, 제목/조사 메모/담당자 텍스트, 진단 태그로 검색한다.
   Recording ID로 범위를 좁힐 수 있다. 태그는 현재 개정 기준이며 오래된 분류는 이전 개정에서 확인한다.
4. **Condition coverage** 탭에서 전체/분류/미분류 사례, 평가 연결, 36개 격자를 조사한다.
   격자의 **View cases**는 해당 reviewed 조건의 사례 검색으로 이동한다. 원본·평가 왕복은 I1과 같다.
5. JSON 내보내기는 화면 표본 필터와 관계없이 선택 recording의 전체 현재 사례와 모든 격자를 포함한다.
   조회 뒤 해당 범위의 Case가 바뀌면409로 거절한다. 새 현황을 다시 확인해 내려받아야 한다.

Coverage 범위는 전체 workspace 또는 명시한 recording이다. 사례 목록의 텍스트/조건/상태 필터는
coverage 분모를 바꾸지 않는다. 존재하지 않는 recording은 빈 결과로 바꾸지 않고404다.
HTTP 인증과 로컬 패키지의 Host/Origin 제한을 그대로 적용한다.

## 첫 taxonomy의 범위

`investigation-conditions` r1은 코드와 함께 버전·내용hash가 고정된 내장 분류체계다.
이번 범위에는 사용자 정의 taxonomy 작성/수정 화면이 없다. 이후 개정은 기존 의미·snapshot을 보존해야 한다.

| 축 | 값 | 해석 |
| --- | --- | --- |
| lighting | daylight / night / low_light | 사용자가 원본을 보고 선언한 조건. low_light는 그 외 저조도이며 혼합·모호한 경우 비워 둠 |
| target | pedestrian / vehicle / cyclist / other | 조사 대상으로 지목한 한 객체의 종류. 장면 안의 모든 객체 집계가 아님 |
| target_size | small / medium / large | 원본 이미지의 지정 bbox 면적: <1024, 1024 이상9216 미만, 9216 이상 픽셀². 거리나 실물 크기가 아님 |

크기 선언에는 원본 이미지/annotation을 메모로 특정한다. 비영상·대상이 불명확한 경우 생략한다.
수동 조건은 source=manual, author, note, review_status, declared_at, segment_hash,
taxonomy 전체 snapshot과 hash를 보존한다. reviewed는 사용자의 선언이지 인증된 서명이나 실측 검증이 아니다.
키워드·파일명·시각만 보고 야간/날씨/보행자 등의 조건을 자동 추정하지 않는다.

## 규칙으로 계산하는 태그

`pinned-row-diagnostics/1`은 기존 Case의 고정된 평가 링크만 읽는다. 새 추론이나 평가를 실행하지 않는다.

- has_false_negative / has_false_positive: completed 평가의 evaluated 행에서 진단fn/fp가 양수인 근거가 있음.
- no_linked_evaluation: 연결된 평가가 없음.
- incomplete_evidence: 전체 source sample을 채우는 **단일 평가**가 없거나, 연결 중 미검토/누락/실패가 있음.

규칙 버전과 evaluation/image/manifest hash를 결과와 export에 남긴다.
미검토·누락은false-negative로 만들지 않는다. 평가A에서sample1, 평가B에서sample2를 가져와
두 표본을 모두 평가했다고 집계하지 않는다. A가 충분한 행을 제공하더라도 B의 실패/누락은
태그로 계속 남는다. 같은 평가의 동일 원본을 중복 링크해 분모를 늘리지 않는다.

## Coverage 분모와 한계

집계 단위는 **현재 Case**, 전체/선택 recording 범위이다. resolved/dismissed도 포함한다.
reviewed이며 세 축을 모두 채운 사례만36개 격자에 들어간다. 초안·부분 분류·미분류는 별도 분모에 남고,
축별 집계에도unknown으로 보인다. 조건 조합에 실제 사례가 없으면0을 표시하며 셀을 숨기지 않는다.

평가 연결 상태는 complete / partial / unusable / unlinked로 구분한다. complete는 고정 Segment의
모든 sample이 **어느 하나의 evaluation ID**에서 유효한 행으로 연결됐다는 뜻이다. 평가의 전체
데이터셋, 모든 모델, 인지 정확도, 원래 로그 전체가 검증됐다는 뜻이 아니다. 범용 비영상sample에는
객체검출 평가가 적합하지 않을 수 있으며, unlinked는 도구/시스템 실패 판정이 아니다.

case 수와 원본 recording hash/channel/ordinal 집합 기준의 exact source selection 수를 모두 표시한다.
같은 원본의 사례를 여러 개 만들어도 독립 장면의 수가 늘었다고 해석하지 않는다. 부분적으로 겹친
구간은 자동 중복 제거하지 않으며, 통계적 독립성도 증명하지 않는다.

36개 Cartesian 격자는 **내부 조사 재고 분류표**다. 유효 ODD 조합·필수시험 계획·통과율·안전 확률이 아니다.
사례가 없는 조합에서 자동 시험을 생성하거나 미구현 physics를 지원하는 것처럼 집계하지 않는다.
원본 파일의 현재 byte 건강은 이 metadata 집계에서 다시 검사하지 않는다. 원본 조사시 I1 sample hash 검사를 사용한다.
최대1000case·합계32MiB case manifest를 읽으며 넘으면413이다. 화면의 첫50개만으로 전체 coverage를 만들지 않는다.

## API·SDK·저장 호환성

| 경로 | 역할 |
| --- | --- |
| `GET /api/v1/case-condition-taxonomy` | 버전·hash·축·값·의미 조회 |
| `POST /api/v1/cases/{id}/conditions` | expected_revision·Idempotency-Key로 조건 개정 발행 |
| `GET /api/v1/cases?condition=lighting:night&condition=target:pedestrian&review=reviewed` | 선언 조건의 교집합 검색. q/diagnostic/recording_id도 사용 가능 |
| `GET /api/v1/case-condition-coverage?recording_id=...` | 일관된 DB snapshot에서 전체 현황과근거hash 생성 |
| `GET /api/v1/case-condition-coverage/export?expected_hash=...&recording_id=...` | 같은 snapshot내용만export. 내용이 바뀌면409 |

SDK: `case_condition_taxonomy()`, `classify_case()`, `cases(conditions=[...],review=...,diagnostic=...)`,
`case_condition_coverage()`, `export_case_condition_coverage()`. Download는같은API서버의고정경로만
사용하고크기/SHA를검사한다. Agent는동일API를조회한다. 자동분류실행권한이나자동승인을제공하지않는다.

DB는schema12를 유지하며새테이블/데이터이관이없다. 조건저장은새`axiom-investigation-case/2`
manifest를만든다. 기존case/1·평가원본·과거요청hash는바꾸지않는다. 기존일반조사개정/평가링크추가는
분류를보존한다. 구패키지에는새화면/API가없으므로새패키지를사용한다.

I3의첫 **Case→수동객체참조시험→일반campaign→Case결과capture**는 [재시험 기능](case-retests.md)으로 연결했다.
source조건은출처이며참조모델의pass를이36셀의실제sensor조건검증으로승격하지않는다.
전체ODD분류편집기·자동장면태깅·유사검색·실행coverage는추가범위로남긴다.
