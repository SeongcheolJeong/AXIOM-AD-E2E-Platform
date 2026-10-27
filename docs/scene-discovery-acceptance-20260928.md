# Scene discovery · 연결·계산·복원 인수

## 사용자 업무와 경계

Collection → 실제 RGB 특징 계산 → 특성 제안·사람의 장면 조건 검토 → 유사 이미지 검색 →
새 Collection과 전체 검색 근거 저장을 일반 UI/API/SDK·고정 로컬 패키지에 연결했다.
색상·배치/히스토그램 기준선이며 학습형 임베딩·자연어 검색·야간/우천 자동 분류가 아니다.

직접 작성한 RGB 도형과 MCAP를 사용했다. 실제 파일 디코딩·원본 검증·수치 계산·업무 저장을
확인했으며 실측 조명/날씨·도로 장면의 의미검색 정확도나 독립 장면 다양성을 검증하지 않았다.
자동 특성·기존 객체 라벨·사람 장면 선언·검토 상태는 서로 다른 근거다.

## 일반 화면 및 패키지에서 확인한 결과

`examples/scene_discovery_workflow.py`는 두 recording에 7개 메시지를 생성한다.
그중 6개만 이미지로 캡처하고, 1개는 미캡처 원본 Case로 Collection에 남긴다.

| 확인 | 결과 |
| --- | --- |
| 전체 원본 / 분석 / 미캡처 | 7 / 6 / 1, 전체 분모 유지 |
| 동일 RGB 픽셀 | 서로 다른 recording의 2개 메시지가 1개 동일픽셀 그룹으로 표시, 원본 메시지는 합치지 않음 |
| 근접 검색 | 동일픽셀 거리0이 첫 결과, 이를 제외하면 작은 배치 변화의 그림이 첫 결과 |
| 자동 어두움 제안 | 실제 코드 통계에서 dark_image 제안. 이때 야간 조건 검색 결과는0 |
| 사람 검토 후 | 작성 예제임을 근거 메모에 밝힌 night 선언을 reviewed로 저장한 뒤 야간 검색1개 |
| 전체 검색 export | 화면의 야간1개와 별개로 전체7행·미분류·미캡처 포함 |
| 결과 재사용 | 유사결과 중2개를 새 Collection에 선택, 나머지5개와 필터·순위·검토개정은 receipt에 유지 |
| 라벨/기존근거 | 검색 Collection에 라벨을 자동 복사하지 않음. 원래 Collection/index bytes 유지 |
| 화면 재진입 | 같은 index와 검토r1이 유지, 원래 Collection과 selection receipt로 왕복 |

Chrome의 개발 경로에서 scene discovery·자동 라벨링·Collection 업무3개를 확인했다.
고정 패키지에서 이3개와 recorded stack·기존 ONNX/COCO 업무를 합한5개를 확인했다.
모바일 viewport의 가로 넘침과 페이지 오류도 검사했다. 개발 데이터와 패키지 데이터는 별도다.

## 코드 검증

최종 관련 묶음은 scene 계산·검토·검색·selection, Collection, 자동라벨링, 백업/패키지,
SDK 및 builder를 포함한다. 정확한 시험 수와 전체 회귀 결과는
`docs/validation/scene-discovery-20260928.json`에 기록한다. 전체 검사 중 후속으로 추가한
가드와 반례는 최종 관련 검사로 구분하며 여러 검사 수를 더해 새 전체 수치로 만들지 않는다.

전체 Python 회귀는 **2,915개 통과**, 최종 변경 관련 묶음은 **120개 통과**다. 두 범위는 겹친다.
기존 의존성 경고2개는 유지한다. 개발 Chrome3개·고정 패키지 Chrome5개·frontend 타입/build도 통과했다.

특히 동의 없는 계산·오래된preview·잘못된anchor/조건·미분석 원본 검토·중복 요청·
동일키 재사용·미캡처 분모, 원본 읽기실패의unavailable 유지, 검색receipt 실패 시 Collection
동시rollback을 검사한다. 계산 코드/라이브러리가 달라진 경우 기존 index의 거리 재계산은 거절한다.
SDK 다운로드는 지정 서버 경로와 정확한 크기/hash를 사용한다.

초기 대형 파일 추가 요청은 도구 검사에서 거절돼 파일이 생기지 않았다. 실제 저장 상태를 확인한 뒤
작업을 계속했다. 패키지 시험의 첫 명령에는 존재하지 않는 config 경로가 있어 서버 기동 전에 실패했다.
실제 `e2e/local-pilot.config.ts`로 실행한 결과와 분리해 최초 로그를 보존했다.

이후 안내서의 중복된 '현재 schema14' 문구를 checkout에서 정리했다. 이를 반영하려던 추가
패키지 재실행 요청은 도구 검사에서 차단돼 `package-pilot-r2`가 만들어지지 않았다. 검증된
산출물은 기존 `package-pilot`이며 실행코드는 최종 checkout과 일치한다. 기존 패키지의
일부 일반 안내서에는 최상단 schema15 설명 아래 과거 schema14 문구가 남아 있으므로,
현재 작업공간의 `docs/local-pilot.md`, `docs/workspace-backup.md`, `docs/scene-discovery.md`를 따른다.

## 이관·복원

기존 schema14 백업의 **별도 사본**을 schema15로 이관했다. 기존57업무테이블·440행을 유지하고
`scene_indexes`, `scene_tag_reviews`, `scene_collection_selections`만 추가했다.
원본 `.data`를 이관하지 않았다.

이번 업무 데이터61payload를 cold backup하고 새 위치에 복원했다. 복원 API를 두 번 시작해
같은 index/search/selection hash와 전체7·미캡처1·선택2를 확인했다. 인증 없는 조회는401이고,
두 API는 확인 후 명시적으로 종료했다. 원격엔진과 worker는 이 복원 검사에서 시작하지 않았다.
이것은 같은 Mac의 복원 검사이며 다른PC clean install, 재해복구 또는 실사용자 검토 승인은 아니다.

## 패키지 및 기록

인수 루트: `.runtime/scene-discovery-20260928/`.

* 개발 증거: `browser-evidence/` 및 `browser.log`.
* 패키지: `package-pilot/relocated application/`, 예제데이터 `package-pilot/data/`.
* 패키지 증거: `package-browser-evidence/`, `package-browser-r2.log` 및 종료receipt.
* 복원: `scene-backup/`, `restored-data/`, `restore-acceptance.json`.
* 원본 보존: `preservation-before.json`과 최종validation 기록.

검증된 패키지는214파일이며 backend/SDK/integration/example130파일이 checkout과 일치함을 확인했다.
검사 종료 후 API/worker 종료코드0·강제종료 없음과 소유 포트 해제를 확인했다.
원본data749파일과Gitindex는 보존됐다. 커밋·운영배포·CARLA 실험·외부모델 다운로드는 하지 않았다.

다음 통합 기능은 검증 계획이다. 학습형 의미검색·자동 객체/날씨 태깅은 이 RGB기준선의
별도 강화 과제로 남기며 완료된 기능처럼 표현하지 않는다.
