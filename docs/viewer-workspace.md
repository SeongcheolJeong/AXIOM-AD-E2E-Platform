# 작업 진입점과 결과 Viewer · 2026-10-08

첫 화면 **시작 · 가능한 작업**에서 작성·실행·재생 업무와 선택한 작업공간의 실제 결과를
함께 확인한다. 기존 일반 Experiments 입력 폼은 **Experiments · 상세 설정**에 보존한다.
새 메뉴는 기존 versioned API와 동일한 프로젝트 권한을 사용한다.

| 메뉴 | 사용자가 완료할 수 있는 업무 |
| --- | --- |
| Object Sim · 작성 / 재생 | 6개 작성 템플릿 선택 → 객체의 3D 배치·행동 수정 → 불변 시나리오 개정 저장 → 작업량 확인 → 선택 native 시험 제출 → 저장 궤적 재생·기준 판정·원본 다운로드 |
| Sensor Sim · 영상 / RAW | 저장된 완료·실패 실행 선택 → CARLA 전방/후방 시간축 재생·센서 A/B 나란히 보기·현재 원본 프레임 다운로드 → 렌즈×센서 ISP·550 nm 격자 비교와 RAW·픽셀/각도 조사 |
| Feedback 주행 · 카메라 | 기준 계획·목표 속도·카메라 작성 → preview/저장/제출 → 실제 CARLA 자차 응답·새 RGB·계획/실제 궤적·제어값 동시 조사 |
| 결과 Viewer | 실행 종류·상태로 결과 선택 → 지원 궤적·시계열·영상 표시 → 봉인 영수증과 원래 아티팩트 다운로드 |
| Log replay / Validation | 기존 Replay Spec 작성과 검증 계획 업무로 이동 |

`#workspace`, `#object-sim`, `#sensor-sim`, `#viewers`를 지원한다. 작성은
`#object-sim?mode=author&project=default` 또는 `#sensor-sim?mode=author&project=default`로 연다.
결과 링크에는 정확한 `project`와 `execution`을 넣는다. 다른 실행을 선택한 뒤 새로고침해도
그 실행을 유지하며 Object 작성/결과 탭 왕복에서도 현재 선택과 작성 중인 초안을 보존한다.
조회나 탭 이동은 작업을 제출하지 않는다.

영상·궤적을 JSON·파일 목록보다 먼저 보여준다. Object 궤적은 실제 기록 시간으로 재생·
일시 정지·스크럽할 수 있으며, 제한된 viewer 표본 수와 전체 native 표본 수를 구분한다.
실행 상태와 native 관측 기준 판정은 별도 표시한다. 상세 실행이 갱신되면 선택 목록의
상태·fidelity·완료 집계도 그 실제 상세 결과에 맞춘다. 목록 조회 중을 0건으로 표시하지 않는다.
원본 이미지와 다운로드는 각각 byte size·SHA-256을 검증한다.

## 실제 제공 데이터와 한계

현재 외부 접속은 `8021`의 `default` 프로젝트다. 초기 정상 UI 시험 후 저장 실행은
Object Sim 1건, CameraE2E 완료 2건·실패 2건, 합성 reference planning 완료 1건이었다.
이후 사용자 요청으로 기존 CARLA 3건을 명시적으로 연결하여 그 인수 시점의 `default`는 총 9건이었다.
현재는 새 feedback 실행의 첫 실패 1건과 수정 버전 완료 1건을 추가하여 총 11건이다.
이를 다른 작업공간의 과거 실행 수와 합산하지 않는다. 목록은 최근 100건부터 읽고
Viewer에서 이전 기록을 더 불러올 수 있다.

CameraE2E 영상은 작성한 중앙 slanted-edge 입력과 저장된 **추정 센서**를 사용한
native 기하 광학·고정 ISP 진단이다. 원래 영상은 **32×32**, 공통 각도 격자는 **19×19**다.
현재 원본 영상이 매우 어두운 상태를 그대로 표시한다. 자동 gain이나 사진으로 대체하지 않는다.
도로 영상·물리 센서 검증·제품 우열 평가를 제공한다고 해석하면 안 된다.
기존 `8020`의 CARLA 센서 결과 3건을 원래 봉인·입력·40개 파일 hash로 연결했다.
640×360 전방/후방 1,200프레임의 Range 기반 시간축과 A/B 표시를 제공한다.
이 원본은 정지 차량의 CARLA 후처리 RGB이며 실제 이동 주행이나 물리 카메라 근거로
합산하지 않는다. [연결과 별도 인수 근거](carla-road-viewer.md).

새 [CARLA feedback 센서 Viewer](carla-feedback-sensor-viewer.md)는 지도 기준 제어기를 실제 자차 상태에
연결하고 이동한 차량의 새 RGB를 출력한다. 실제 4초·9.65m·40개 제어 응답·전후방 80 RGB,
동기화된 계획/실제 궤적·속도·제어값과 원본 다운로드를 정상 local/HTTPS에서 확인했다.
카메라는 기준 제어기의 관측 출력이며 학습형 카메라 입력 E2E·물리 센서·안전 판정은 별도 gate다.
첫 실패는 지우지 않았으며 종료 정리의 새 snapshot 확인을 추가한 1.0.1을 인수했다.

Object 편집기의 3D와 결과의 평면 표시는 객체 배치·native 궤적 도식이다. 센서 렌더링·
실측 도로 geometry·reactive 주행 정책·주행 안전 인수가 아니다. 현재 native 어댑터가
지원하지 않으면 실행 준비를 차단하며 reference 결과로 대체하지 않는다.

## 검증과 외부 반영

정상 비밀번호 브라우저에서 시나리오 r1을 저장하고 r2의 속도를 수정했으며 r1 해시를
보존했다. 선택한 seed42·설정 6초의 시험 **1건만** 명시적으로 제출했다.
`job_725e7cee79034d7483f3a5244a953f45`는 실제 esmini v3.8.2가 62개 표본을 반환했다.
실행은 completed이고 충돌 기준은 failed다. 기준별 passed4/failed1/observed2를
함께 보존했다. 원래 CSV·봉인 영수증의 다운로드 해시와 시간축·재생/일시 정지를 확인했다.

기존 센서 결과의 네 조합·원래 픽셀·RAW·좌표·receipt, 실패 기록의 선택/새로고침과
360·390·768·1440px의 네 진입점도 확인했다. 실제 public HTTPS 브라우저 재검증은
조회·다운로드만 수행했고 신규 native/GPU 작업은 제출하지 않았다.
Backend·시나리오·센서 어댑터 계약 45개와 별도 native plan 계약 5개,
gateway/supervisor/stop 148개 검사를 통과했다. Mocked 계약 검사는 실제 엔진 실행과 별도다.

검증한 별도 빌드를 외부에 반영하며 supervisor를 한 번 교체했다. 임시 URL은 변경됐지만
기존 비밀번호·Engineer 권한·GET37/POST17 범위·**2026-10-10 03:45:21 KST** 만료를 보존했다.
향후 같은 고정 빌드 경로의 UI 갱신은 소유자의 local SIGHUP으로 완전히 검증한 snapshot만
교체한다. 인증·설정·만료·권한을 재로딩하는 공개 API는 없다.
원래 `frontend/dist`, 기존 platform API·worker·CameraE2E·CARLA는 유지했다.

[최종 근거](validation/viewer-workspace-20261008.json). 원래 시험과 초기 test-harness 오류 기록은
`.runtime/viewer-workspace-20261008`에 별도 보존한다. 재검증은 `verify-viewers.mjs`를 사용하며
기본 실행은 조회 전용이다. 최초 bounded CPU 시험은 `--execute-object`를 명시적으로
선택한 경우에만 제출하고, 저장된 execution 기록이 있으면 다시 제출하지 않는다.
상용 release 또는 Applied Intuition 기능 동등성의 완료 근거는 아니다.
