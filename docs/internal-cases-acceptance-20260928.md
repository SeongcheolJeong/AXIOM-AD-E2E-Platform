# I1 통합 인수 · 원본 로그 구간 ↔ 사례 ↔ 평가

## 새로 연결한 사용자 업무

일반 Data catalog와 Detection datasets에서 **동일한 Investigation case**를 만든다.
로그는 한 채널의 양끝 포함 log-time 구간, 평가는 정확한 image의 원본 ordinal을 고정한다.
사례에서 원본 메시지와 평가 이미지로 왕복하고, 같은 원본의 다른 평가를 기존 사례에 연결한다.
메모·선언된 담당자·open/investigating/resolved/dismissed 상태는 새 개정으로 보존한다.
이전 개정, 원래 평가와 데이터셋·원본 메시지는 변경하지 않는다. 해결 상태는 시험 통과나 승인과 다르다.

UI·REST·OpenAPI/Agent 도구·SDK에 같은 생성/조회/개정/페이지/원본조사/export 경로를 연결했다.
별도 실험용 viewer가 아니라 기존 앱의 `Investigation cases` 메뉴다. 새 로컬 패키지에도 포함했다.

## 실제 업무 인수

일반 API/worker로 authored-image 모델 A/B의 ONNX CPU 추론과 COCO 평가를 실제 실행했다.
후보의 미검출 이미지에서 사례를 만들고, 저장 응답을 한 번 유실시킨 뒤 같은 키로 재시도했다.
중복 Case는 생성되지 않았다. 선택 이미지의 원본 recording/channel/ordinal/SHA가 맞는지 확인했다.
사례의 원본 이동은 브라우저 sample inspector의 실제 선택 ordinal과 SHA까지 대조했다.

로그 화면에서 전체6메시지의 구간 사례도 별도로 만들었다. A/B의 같은 이미지를 원래 사례에
추가해 r2의 평가 링크2개를 확인하고, 조사 상태·담당자를 바꿔 r3를 저장했다.
재로드·평가 화면 왕복·이전 개정 다운로드 후에도 r1 bytes와 원래 evaluation bytes는 동일했다.
다른 Case 조회를503으로 실패시키면 이전 사례를 그 결과처럼 표시하지 않는다.
Desktop/390px 모바일 화면과 오류0을 확인했다.

이 결과는 실제 ONNX/COCO를 사용한 **작성된 원본 이미지·라벨의 통합 업무 검사**다.
고객 데이터의 인지 정확도, 실제 원인 판명, 시간 동기화·센서 보정의 인수를 주장하지 않는다.

## Schema11→12와 기존 데이터 보존

기존 백업을 별도 폴더로 복원한 뒤 schema12로 올렸다. schema_versions 외의 기존
**48업무 테이블·3,422행**은 모든 값을 대조해 동일함을 확인했다. 새 네 개 investigation 테이블만 추가됐다.
원래 .data는 업그레이드하거나 열지 않았다.

복원본 API를8194에서 두 차례 시작·정상 종료했다. 기존 저장 평가로 사례를 생성·개정하고
SDK로 r1을 export했다. 재시작 후 r2와 같은 원본 메시지, r1 및 평가의 불변 bytes를 확인했다.
Run74도 그대로 조회됐고 미인증 사례 조회는401이었다. worker/원격 엔진을 시작하지 않았다.
그 뒤 schema12의 cold backup과 다른 새 폴더 복원도 성공했다.

최초 검사 스크립트는 sqlite3 context manager를 connection 종료로 잘못 가정했다.
자체 조회 연결이 남아 cold-backup guard가 거절했다. 연결을 명시적으로 닫도록 고치고
새 migration-r2 폴더에서 전체 단계를 다시 통과했다. 최초 실패와 원본 파일은 보존했다.

## 시험 범위

- Python 전체 회귀 **2,755개 통과**, 기존 의존성 경고2개.
- 마지막 패키지 문서 포함 변경과 I1/backup/SDK를 **관련80개**로 재검사. 전체와 겹쳐 합산하지 않는다.
- 개발 실행 경로 Chrome4개: I1사례·기존A/B비교·MCAP조회·0시각중복 탐색.
- 고정 패키지 Chrome1개: 필수token·실제ONNX/COCO·A/B·새사례·원본/평가 왕복·새로고침.
- Frontend typecheck/build 통과. 브라우저 검사 모두를 새로 실행한 것은 아니다.

시간 중복·비단조파일순서·채널 interleave, exact ordinal 조회, 잘못된 source 연결,
빈/큰 구간의 원자적 거절, manifest/원본index변조, 미검토표본 유지, stale revision,
인증·멱등성·migration 중간실패 rollback을 계약시험에 포함했다.

## 근거와 다음 범위

실행·실패 로그와 browser evidence: `.runtime/internal-cases-20260928/`.
기존 데이터 migration/재시작: `migration-r2/migration-restart-acceptance.json`.
새 실행 패키지: `package-pilot/relocated application/`.
패키지 인수 데이터·token은 각각 `package-pilot/data`, `package-pilot/access`에 별도로 보존한다.
이 데이터는 인수용 authored dataset이며 운영 원본 데이터와 구분한다.
기계 판독 집계: `docs/validation/internal-cases-20260928.json`.

현재 Segment는 단일채널 참조 구간이고 export는 이미지/MCAP 원본을 포함하지 않는다.
조건 taxonomy/자동 태깅/coverage, Case→Scenario/Campaign 환류, 외부4제품 connector,
실제 다중센서 E2E 정책, 팀 인가/승인은 구현했다고 주장하지 않는다.
다음은 I2의 조건 분류·태그·검색과 coverage로 확장하며, CARLA 동역학 연구는 계속 분리한다.
