# Collection→Dataset 기능 인수 · 2026-09-28

## 새로 완료하는 사용자 업무

일반 Data collections 화면에서 여러 Case·고정 dataset 개정·캡처된 이미지를 묶고,
겹친 원본의 선택 이유, 미검토·미캡처 상태와 모든 제외를 남겨 기존 dataset으로 발행한다.
기존 dataset 화면의 라벨 편집·평가와 원래 Collection으로의 역추적을 사용한다.
API·SDK·Agent가 같은 입력/출력·인증 경계를 쓰며 Collection 저장/발행이 모델을 실행하지 않는다.

## 실제 개발 UI와 별도 패키지의 업무 검사

기존 직접 작성 pixel-rule ONNX·COCO 예제로 기준 평가를 만들고 별도1프레임 MCAP를 추가했다.
해당 입력은 synthetic/authored이며 학습형 E2E·실측·고객 인지 정확도의 인수가 아니다.

| 확인한 흐름 | 결과 |
| --- | --- |
| 원본 selection | Case2개 + dataset r2의6개 이미지 + 다른 recording의캡처image1개 |
| 원본 중복 처리 | 확장9참조 → 고유7메시지, 겹친2참조는 원래 선택 사유와 함께 보존 |
| 기록 범위 | 2 recording, 동일PNG는 독립scene처럼 합산하지 않고 중복 그룹 표시 |
| 처음 dataset preview | 6reviewed·1unreviewed·2reviewed-negative. 새image를 음성정답으로 만들지 않음 |
| 최종 선별 | 명시적중복사유로1개제외·6개포함. Collection7원본과제외내역 유지 |
| 응답 유실 | publication 응답을버린뒤동일요청재시도: dataset와receipt한개만발행 |
| 기존 평가 연결 | 새dataset의실제COCO평가가완결되고기존동일입력기준의metrics와일치 |
| 왕복·과거 근거 | 새dataset→Collection r1, 새로고침, parent dataset r2/Collectionbytes 불변 |
| 인증·레이아웃 | 패키지무인증Collection401, 정상인증workflow, Chrome페이지오류없음·390pxoverflow검사통과 |

개발 경로는 Collection 외 기존 조건분류·Case model replay까지 **3개 Chrome workflow**를 통과했다.
소스/빌드UI를고정한패키지에서도 recorded stack·Collection·기존통합workflow **3개**를 통과했다.
Frontend typecheck/build를실행했다. Package는기존Python dependency환경을재사용하며신규PC설치인수가아니다.

## 무결성·라벨·저장 경계 검사

단위/계약검사는preview무쓰기,중복·동시요청,과거Case/dataset개정불변,잘못된label옵션,
미검토음성승격금지,잘못된이미지·taxonomy,모든행decision필수,원본손상,byte한도와중간실패rollback을포함한다.
같은labelvocabulary가아니면reviewed annotation이관을거절한다. 다른클래스추가를그클래스의검토완료로간주하지않는다.
같은recording 또는동일PNG의train/validation/test분할충돌은거절한다. near-duplicate나서로다른
파일로분할된동일주행의독립성을증명하지않으며수집Session grouping은후속이다.

처음전체회귀에서는schema12를기대하거나13을미지원으로취급하던검사3건과,
실행명령의`ADE2E_SEED_EXAMPLES=0`이기본seed시험의전제와충돌한1건이실패했다.
새현재schema기대값13·미지원14를명시하고,기본seed시험이원래설정으로실행되도록검사환경을수정했다.
원본운영경로대신별도pytest-default-data를사용한다. 최초로그와최종로그를함께보존한다.
최종전체회귀는 **2,869개 통과·기존 의존성 경고2개**, 마지막Collection/이관/SDK검사는
**25개 통과·경고2개**다. 두범위는겹치므로합산하지않는다. 초기실패와수정후실제실행결과는
`docs/validation/collections-20260928.json`에기록한다.

## 이관과 복원

schema12→13은Collection관련3개테이블만추가한다. 단위검사에서기존row표현보존,
동시초기화,DDL/commit단계오류시원복을확인했다.

과거schema11백업을**새디렉터리에복원**하고13으로열었을때기존48업무테이블3,422행이
그대로유지됐다. 이사본에는Case테이블이없었으므로실제추가분은Case4개+Collection3개다.
원본`.data`에서이관을실행하지않았다.

새Collection이있는browser store는64payload파일로cold backup/restore하고,
별도인증API를2차례시작·종료하여Collection·publication·dataset hash,reviewed-negative2개,
SDKexport와source역추적을확인했다. 복원검사에서는worker·새추론·원격엔진을시작하지않았다.
수동검사API는소유프로세스에종료신호를보내종료(-15)했으며,패키지API/worker는둘다exit0,
forced_stop없음으로종료됐다. 이를hot backup·재해복구·신규OS설치완료로확대하지않는다.

## 보존한 산출물과 경계

- `.runtime/collections-20260928/browser-evidence/`: 개발UI의실제입출력·스크린샷·선택ID
- `.runtime/collections-20260928/package-pilot/`: 고정실행패키지와분리된data/access
- `.runtime/collections-20260928/browser-backup/`, `restored-browser-data/`: 새데이터의복원인수
- `.runtime/collections-20260928/migrated-original-copy/`: 구schema이관확인사본
- `restore-acceptance.json`, `python-full.log`, `python-full-final.log`, `package-browser.log`: 성공과초기실패근거

새패키지의backend/SDK는마지막구현파일hash와일치한다. 패키지를만든이후의기획문서갱신은
개발문서에만반영되며이미시험한bundle을뒤에서덮어쓰지않는다. 운영배포·commit·CARLA실험은하지않는다.
원본파일과Gitstaging보존확인은최종validation JSON을따른다.

다음개발은이Collection에서고정한dataset의반복평가기준과명시적으로승인한모델A/B작업흐름이다.
자동labeling·장면검색·coverage plan·실제모델연동은별도후속이며이번기능완료에합산하지않는다.
