# 기존 CARLA 도로 장면 연결 · 2026-10-08

시작 화면의 **CARLA 도로영상 재생** 또는 **Sensor Sim · 영상 / RAW**에서
기존 CARLA 전방·후방 영상을 선택하고 재생·일시 정지·시간축 이동을 할 수 있다.
센서 구성 A/B를 같은 capture index로 나란히 보고 현재 BGRA8 프레임과
봉인 실행 영수증을 원본 해시로 확인해 다운로드한다.

## 연결된 원본

사용자 요청에 따라 내부 `8020` 작업공간의 아래 세 실행만 공유 `8021/default`에
명시적으로 연결했다. 다른 실행·프로젝트·권한·연구 폴더는 자동 이관하지 않았다.

| 원래 실행 이름 | 구성 | 보존된 capture index | RGB 프레임 |
| --- | --- | --- | --- |
| native10 | 단일 구성, 전방/후방 | 100 | 200 |
| native30 | 단일 구성, 전방/후방 | 300 | 600 |
| sensor-ab10 | 구성 A/B 각각 전방/후방 | 각 100 | 400 |

총 **1,200 RGB 프레임**, 격자는 **640×360**이다. `Town10HD_Opt`의
**정지 차량에서 얻은 CARLA 후처리 RGB 시계열**이며 실차 주행 영상·센서 Bayer RAW·
물리 카메라 품질·주행 제어 검증 근거가 아니다. A/B는 순차 실행이므로 각 원본 clock이
다르며 renderer·교통 신호 상태를 완전히 동기화한 센서 영향 평가로 집계하지 않는다.

native30 직접 진입:
`#sensor-sim?project=default&execution=job_59913285dc8d4e38afca789b72bcd0a3`.
나머지 원래 실행 ID는 아래 검증 근거에 보존한다. 첫 화면과 상세 화면에
**내부 CARLA 작업공간 → 공유 default · 보존 결과 연결**을 표시한다.
이 설명과 정지 장면 한정은 execution ID뿐 아니라 원래 receipt SHA가 일치할 때만 적용한다.

## 원본과 실행 경계

`scripts/import_retained_experiments.py`는 선택한 execution ID와 봉인 SHA를 명시적으로
받는 operator 도구다. 기본은 읽기 전용 preview이고 `--apply`로 보존 결과만 발행한다.
현재 지원하는 source schema17/18와 target schema18의 관련 다섯 테이블을 검사하며
초기화·마이그레이션·adapter 실행·job 제출·권한 변경을 수행하지 않는다.

세 Experiment·Job·Execution, 원래 순서의 이벤트 3개와 아티팩트 40개
**1,148,992,934 bytes**를 보존했다. 원래 ID·입력·seed·구성·adapter 버전·fidelity·
실행 시각·manifest bytes/hash·artifact bytes/hash는 그대로다. 대상 DB 내부의
submission/event sequence만 새로 배정하며 두 sequence는 봉인 영수증에 포함되지 않는다.
원래 execution에 import 이벤트나 가공 아티팩트를 추가하지 않는다.

별도의 `retained-execution-import/1` 관리 receipt에 연결 출처를 보존한다.
UI의 public source annotation은 같은 원래 execution/receipt 핀을 사용하며,
일반 사용자의 조회·다운로드는 기존 `/api/v1/experiments` 계약을 사용한다.
UI가 원본 실행 상태나 시간을 새 시험 결과로 바꾸지 않는다.

파일 SHA·size·regular-file/no-symlink, 원래 입력/봉인 검증을 통과한 파일을 fsync/staging 후
exclusive publication하고 DB transaction 내에서 봉인을 다시 확인한다.
정확히 동일한 재시도는 `already_retained`이며 충돌·부분 이관·미등록 파일은 거절한다.
일반 실패는 이번 실행이 소유한 파일/행만 되돌린다. 파일 시스템과 SQLite 사이의
프로세스/호스트 장애까지 crash-atomic인 이관 도구는 아니므로 그 경우 부분 상태를
자동 삭제·합산하지 않고 operator 조사 대상으로 남긴다.

## 영상 표시와 다운로드

첫/마지막 PNG 16장만 반복해 전체 영상을 표현하지 않는다. 원래 네 ZIP의
ZIP_STORED member를 기존 인증된 artifact API의 정확한 HTTP Range로 읽는다.
중앙/로컬 header·member 이름·범위·크기, manifest의 카메라 index/key와 프레임 SHA를
확인한 후 BGRA8 채널을 RGBA로 바꾸어 원래 픽셀을 Canvas에 표시한다.
ZIP64·압축·암호화·잘못된 응답은 거절하며 전체 ZIP fallback을 하지 않는다.

캐시는 최대 8프레임이고 현재 프레임을 확인한 뒤 다음 시점으로 이동한다.
표시 간격은 원본 timestamp 차이에 따른 16 ms–3 s 범위이며 조회·검증 지연이 더해진다.
실시간 10Hz 영상 전달이나 재인코딩된 동영상 파일을 제공한다는 뜻은 아니다.
현재 프레임 다운로드는 원래 `.bgra8` 바이트다. 전체 ZIP은 기존 API/SDK의 스트리밍
다운로드를 사용하며, 일반 UI 메모리 다운로드는 기존 256 MiB 제한을 유지한다.

## 인수와 외부 반영

Backend/retention 27개, CARLA mocked adapter 계약 25개, ZIP parser/Range 계약 20개를
통과했다. Mocked adapter 검사는 이번에 새 CARLA/GPU 시험을 실행한 근거가 아니다.
실제 원본·외부 HTTPS의 tail/central/middle-frame Range를 각각 확인했다.
정상 브라우저의 local/public 각 7개 업무에서 시작 링크, 세 원본 실행,
처음·중간·마지막 전/후방 픽셀 24개 및 A/B 나란히 12개 표시를 원본 RGBA hash와
비교했다. 재생·일시 정지, 원래 ns/frame, 3개 sealed receipt와 BGRA8 원본 다운로드,
정확한 deep-link 새로고침, 기존 Lens/Object Viewer, 360/390/768/1440px를 확인했다.

조회 중 신규 실행/API 쓰기·브라우저 오류는 0건이다. 현재 공개 `default`에는
기존 6건과 연결된 CARLA 3건, 총 9건이 있다. 전체 DB의 별도 프로젝트 2건과
원래 source 기록도 유지한다. 예전 Viewer 인수 근거는 수정하지 않고 별도 보존했다.

검증한 UI를 고정 snapshot 경로에 반영하고 소유자를 확인한 gateway에만 SIGHUP을 보냈다.
**외부 URL·기존 로그인 cookie·비밀번호·Engineer 권한·GET/POST 범위·
2026-10-10 03:45:21 KST 만료는 유지한다.** API/worker와 터널을 재시작하지 않았다.
원래 `frontend/dist`와 기존 실행·권한·다른 서비스의 상태를 보존했다.

[인수 근거](validation/carla-road-viewer-20261008.json).
실행 receipt·원본 픽셀 기대값·browser 보고서·스크린샷·이관/무변경 재시도 receipt·
별도 빌드는 `.runtime/carla-road-connection-20261008`에 보존한다.
`verify-carlas.mjs`는 조회와 다운로드만 수행하며 시험을 제출하지 않는다.
현재 기록된 외부 로그인 세션은 private 파일이고 배포 아티팩트에 포함하지 않는다.
초기 브라우저 selector 오류 보고서는 수정 전 근거로 따로 남겼다.

후속 [CARLA feedback 센서 Viewer](carla-feedback-sensor-viewer.md)에서 지도 기준 제어기에 반응해
실제로 이동한 차량의 새 RGB를 별도 실행·입력·fidelity로 연결했다. 이 정지 결과를 바꾸지는 않는다.
학습형 카메라 입력 E2E·물리 센서 평가·상용 release gate는 별도로 남아 있다.
