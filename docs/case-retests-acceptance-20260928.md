# I3 첫 통합 단위 인수 · 2026-09-28

## 새로 완료하는 업무

일반 Investigation case에서 수동 파생 시험의 template·기본 parameters·명시적 변형·기준을
작성한다. Scenario/Suite r1과 Case 계획을 원자적으로 저장하며 작성만으로는 계산하지 않는다.
명시적인 실행에서 기존 campaign/queue에 모든 변형을 제출하고 Case 링크도 같은 transaction으로
발행한다. 결과는 Case에서 읽고 정확한 Job/Run으로 이동하며, 종료 후 실패/미완결도 기존
Baseline으로 capture해 새 Case 개정에 연결한다. 조사 상태나 원래 인지평가를 자동 변경하지 않는다.

Retest activity에는 원본 recording 범위의 미제출 계획·전체 시도·변형별 execution/verdict를
표시한다. 계획 작성 당시 조건 개정은 출처로 고정한다. reference engine은 원본 sensor bytes나
lighting/image-size 조건을 실행하지 않으므로 verified sensor condition cells는0이다.
I2의 조건 재고 표를 sensor/ODD 검증 완료로 채우지 않는다.

## 실제 실행과 브라우저

작성한 원본 이미지·pixel-rule 모델로 실제 ONNX CPU/COCO 평가를 수행했다. 미검출 행에서
Case를 만들고 gap20/10m의 파생 객체시험 두 변형을 작성·실행했다. 거리≤0이라는 의도적으로
실패하는 사용자 기준에서 두 Job은completed, 두 시험은failed였다. 전체 분모와 원래 기준을 유지했다.
제출 응답을 끊고 같은 key로 재시도해도 campaign/attempt는1개만 생성됐다.
Case는r1→계획r2→제출r3→capture r4로 진행됐고 조사 상태는open 그대로였다.
원래Case r1과 원래 평가 export는 변경되지 않았다. export hash와 재로딩·정확한Job 이동,
활동표·desktop/mobile을 검사했다. 원래 인지 실패가 객체시험으로 해결됐다는 주장은 아니다.

빌드된 로컬 패키지에서는 I1→I2→I3을 같은 token/API/worker로 검사했다.
두 참조 변형의 완료와capture, 실제 JSON 다운로드hash, 미인증401, activity의조건승격0을 확인했다.
188개 파일의 패키지를 기존 Python dependency 환경에서 사용했고 Node/Vite/CARLA는 실행하지 않았다.
이는 새 장비 clean install이나 외부고객/실차 인수가 아니다.

최초 패키지 브라우저 검사는 coverage탭에서 같은hash로goto하면 사례탭으로 초기화될 것이라
가정해 timeout됐다. 실제 탭버튼을 선택하도록 검사를 수정하고 새 격리 폴더에서10.9초에 통과했다.
최초 실패trace/context와 성공자료를 모두 보존했다. 실제 사용자모델·native센서 실행 실패가 아니다.

## 보존·복원·검사

전체 Python 묶음 **2,803통과·기존경고2개**. 이후 보강한 rollback/대체adapter/링크변조/
일반개정호환/Agent키 검사는 최종 관련 **162통과**에 포함한다. 두 범위는 합산하지 않는다.
개발Chrome의 I1/I2/I3 업무3개 통과 후, 정확한Job이동 추가와 최종화면에 대해 I3한건을 재실행했다.
고정패키지 Chrome업무1개, TypeScript typecheck와build도 통과했다. GitHub 원격CI 실행 결과가 아니다.

검사 데이터의57파일 cold backup/새폴더 restore 후 실제API를 두 번 시작했다. Case r4의
captured retest report hash·활동표hash·원본Case r1·평가export가 유지됐고 SDK도 같은API에서
정해진경로·SHA·크기로 다운로드했다. 복원검사는 worker/원격엔진을 시작하지 않았다.
직접 uvicorn API는SIGTERM으로종료돼exit−15를 기록한다. 패키지 wrapper는API/worker 모두0으로
종료하고forced stop/복구필요없음을기록했다. 이둘을동일한종료코드로덮어쓰지않았다.

DBschema12와기존사업테이블을유지한다. Case/3manifest만새개정에사용한다.
기존 .data749파일과Gitindex는작업전/후hash로보존확인하며커밋/운영배포/CARLA실험은하지않았다.

## 산출물과 다음 범위

- 사용자/API계약: `docs/case-retests.md`.
- 기계판독기록: `docs/validation/case-retests-20260928.json`.
- 로그·원본보고서·브라우저증거·backup/restore: `.runtime/case-retests-20260928/`.
- 사용가능한패키지: `.runtime/case-retests-20260928/package-pilot-r2/relocated application/`.

이번은참조객체시험을통한업무연결인수다. 실제원본모델재평가·지원센서조건실행·외부E2E모델연결과
그에따른유효coverage는다음범위다. 네상용제품의SDK연동이나전체I3완료로확대하지않는다.
