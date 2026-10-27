# I2 조건·검색·사례 coverage 인수 · 2026-09-28

## 새로 연결된 내부 업무

일반 Investigation cases에서 수동 조건을 새 개정에 저장하고, 조건 교집합·검토 상태·
진단 태그로 사례를 검색한다. Condition coverage에서 미분류·초안까지 포함해 조사하고,
셀에서 같은 사례·원본·평가로 이동한다. 화면/API/SDK와 필수 인증 패키지에 같은 기능이 있다.
별도 연구 뷰어나 네 외부 제품의 실제 connector를 만든 것이 아니다.

내장 taxonomy r1은 lighting / primary target / target image size 세 축이다. 임의 taxonomy
편집은 이번 범위가 아니다. 사용자 선언과 검토 근거를 보존하며, 규칙 태그는 고정된 평가의
FP/FN·부족한 근거에서만 계산한다. 영상에서 날씨·객체를 자동 인식하거나 실측 조건으로 인증하지 않는다.

## 실제 업무 검사

일반 API/worker에서 실제 ONNX CPU 및 pycocotools 평가를 실행한 authored 데이터로 확인했다.
미검출 이미지를 참조하는 Case 두 개와 전체 원본 구간을 참조하는 Case 하나를 사용했다.
하나는 세 조건을 reviewed로 선언하고, 하나는 draft, 하나는 미분류 상태로 남겼다.

첫 조건 업무의 전체 사례3 / reviewed 완전분류1 / 초안·미분류2 / 전체격자36을 확인했다.
동일 원본 선택은2개이며 중복 선택 Case1개를 별도로 표시한다. 부분적으로 겹친 구간의
독립성을 증명하거나 중복 제거했다고 주장하지 않는다. 미분류 표본만 화면에서 골라도
export는 사례3개와 전체36셀을 유지했다. JSON byte hash를 API 선언과 대조했다.

저장 응답 유실 뒤 같은 요청/key의 재시도는 같은r2를 반환했고, 원래r1·평가 export는 불변이었다.
조건을 편집하면 검토 확인이 풀리는 것, 새로고침 후 분류 유지, 격자→조건검색→진단필터 연결을
확인했다. 잘못된 새 scope의조회가실패할때 이전coverage가현재결과로남지 않았다.
Desktop와390px mobile에서페이지overflow와JavaScript오류가없음을자동검사하고이미지를보존했다.

이 결과는 통합 업무/계약 인수이며, 실제 보행자·조도·거리 또는 실차 인지 성능을 검증한 결과가 아니다.

## 정확한 분모와 정책

Coverage는 전체 workspace 또는 선택 recording의 **현재 Case**를 센다. 원본 source selection
중복을 공개하고, resolved/dismissed도 포함한다. 목록 필터나첫50개page를전체coverage분모로쓰지않는다.
고정Segment의모든sample에동일evaluation ID의유효한행이있을때만complete다. A/B부분행을합쳐
완결로만들지않으며, 다른모델의FP/FN·미검토·누락은태그로계속남는다.

이러한complete는원래dataset전체·모든모델·ODD·실행coverage·안전·통과를뜻하지않는다.
값이없는셀은조사재고의빈칸이며필수/유효ODD시험의누락이라고단정하지않는다. 시험을자동생성하지않는다.
Source byte는이metadata census에서재검증하지않으며원본조사의sample검사를따른다.

## 복원·재시작 검사와 최초 실패 원인

별도 브라우저 시험 DB를 cold backup해55개payload를새폴더에복원했다. 원본`.data`는사용하지않았다.
최초 복원 검사기는 첫 조건 업무의3사례snapshot을 기대했으나, 후속 I1 브라우저 회귀가 내용으로
중복 제거된 같은 recording에2사례를추가했다. 따라서 현재snapshot은5사례이며hash가달라져검사가실패했다.
기존3사례descriptor와taxonomy가바뀌지않았고추가2사례의ID/제목을대조해원인을확인했다.

실패로그와첫복원본을보존했다. 재검사는 모든브라우저회귀가끝난원본시험DB에서5사례snapshot을
먼저고정한뒤별도백업/복원했다. 실제uvicorn API를두번시작하여같은Case r2·분류·정확한source
sample·조건검색과coverage export가byte단위로유지됨을확인했다. 미인증API는401이다.
검사기는소유API에SIGTERM을보냈으며exit−15와로그·port해제를기록한다. worker/CARLA는시작하지않았다.

실제 패키지 인수는별도다. 새패키지에서토큰로그인→ONNX/COCO→Case→조건r2→36셀coverage→
인증된export까지통과했다. 패키지의API/worker는정상exit0,강제종료없음,복구필요없음으로끝났다.
기존Python dependency환경을사용했으므로새장비clean install이나공유사용자보안인수가아니다.

## 회귀·저장 호환성

전체Python묶음 **2,777개통과·기존경고2개**. 전체검사시작후추가한taxonomy타입/없는recording/
변조경계와패키지문서목록은최종 **관련106개통과**로다시확인했다. 겹치는시험수는합산하지않는다.
개발Chrome업무3개와고정패키지Chrome업무1개, frontend typecheck/build를통과했다.
전체브라우저시험을모두다시실행한것은아니다.

DBschema12는유지하며테이블추가/기존행migration이없다. 새조건은Case /2의새revision에만저장하고
이전Case /1·평가원본·요청hash는바꾸지않는다. 다음의최종검증JSON에원본749파일·Git index보존과
소유port해제검사를연결한다. Commit·운영배포·원격엔진실행은하지않는다.

## 산출물과 남은 범위

- 사용법: `docs/case-conditions.md`.
- 최종 기계 판독 근거: `docs/validation/condition-workspace-20260928.json`.
- 이번 작업과 실패/성공 로그: `.runtime/condition-workspace-20260928/`.
- 일반 브라우저 원본: 같은 폴더의 `browser-evidence/`.
- 독립 복원·재시작: `recovery-r2/acceptance.json`.
- 사용 가능한 고정 패키지: `package-pilot/relocated application/`.

다음은 I3의Case→재시험정의→기존suite/campaign→동일Case의결과환류다.
사용자taxonomy작성·자동장면인식·유사검색·실행/ODDcoverage와실측데이터수용은남아있다.
