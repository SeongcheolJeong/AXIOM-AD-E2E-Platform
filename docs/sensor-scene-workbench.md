# Sensor Sim · 공통 시간축 조사

Sensor Sim의 결과에서 원본 영상과 그 시점의 공간·센서·제어 근거를 함께 조사한다.
재생은 보존한 실행 결과를 읽는 기능이다. 실시간 제어, 학습형 카메라 정책,
실측 센서 품질이나 안전 판정은 이 Viewer의 인수 범위가 아니다.

## 정상 화면에서 완료하는 업무

- **Feedback 주행 · 카메라 실행**에서 시간·속도·카메라를 작성하고
  Preview → 불변 저장 → 명시적 Submit으로 실행한다. 결과에서는 큰 전/후방 영상,
  지도·계획·실제 자차를 한 시간축으로 조사한다. 표본 이동, 재생 속도, 2D/3D,
  자차 추적, 지도/궤적 맞춤, 확대, 레이어와 전체 화면을 사용한다.
- 기존 정지 CARLA 결과는 RGB·LiDAR·radar를 정확히 같은 native 표본으로 연다.
  반환점 3D/XY, intensity/속도 색상, 원본 index·tuple·등록 좌표와 float32 다운로드를 제공한다.
  A/B는 같은 상대 index를 나란히 표시하되 원래 두 clock을 유지한다.
- 새 지도·scene 및 기존 trace·manifest·현재 센서 표본·봉인 receipt를
  같은 프로젝트 권한의 원본 API에서 다운로드한다.

기존 `job_536461b9400b473f963fc96693d662c4`에는 지도·native bbox 원본이 없다.
그 결과는 저장된 계획·자차 궤적과 방향 기호만 표시한다. 차로·차량 형상을 추정해 채우지 않는다.
정지 결과에도 원래 도로 형상이 없어 반환점 화면은 명시적 metric grid와 축을 사용한다.

## 새 주행 scene 계약

선택적 CARLA handler `carla-feedback-sensor/1.1.0`은 기존 spec/trace/result v1을 유지한다.
새 결과에 `sensor-scene.json` (`sensor-scene/1`)과 원본 `map.xodr`를 추가한다.
과거 입력·실패·봉인 artifact를 다시 생성하거나 수정하지 않는다.

scene은 configuration hash/개정·seed·외부 실행 ID·adapter 버전·native/support/helper 코드,
map·route·trace SHA를 고정한다. native `generate_waypoints`의 2m 표본을 계획 경로
XY 영역에 40m 여유를 준 범위에서 보존한다. 원래 road/section/lane, 위치·방향·폭·junction을
보존하고 S 간격이나 공간 공백에서 구간을 분리한다. 원본 XODR의 driving lane ID를 확인한다.
구간 연결·차선 폭·차량 치수를 임의로 보충하지 않는다.

차로 ribbon과 흰 경계는 native 표본에서 계산한 표시다. CARLA renderer의 pavement mesh,
도색·건물·재질·사진식 3D 환경을 복제한 것은 아니다. 자차의 실제 native bbox center offset,
rotation·extent와 매 응답의 전체 world matrix를 보존한다. 주변 actor·반응형 traffic은 없다.
카메라 frustum은 원래 측정 pose와 nominal pinhole intrinsics의 시야 도식이다.

원래 CARLA 좌표는 LH, X 전방/Y 우측/Z 위다. scene renderer는 축 치환 행렬로
world/local matrix를 함께 변환한다. native 차선 yaw는 한 바퀴 밖의 값도 보존하며
producer의 ±4π 유한 범위와 같은 계약으로 검사한다. 입력 frame `f`의 계획/제어와
정확히 다음 물리 frame `f+1`의 자차·카메라를 구분한다. 없는 응답을 보간하지 않는다.

원격 native/helper와 local adapter source가 Preview 이후 바뀌면 dispatch를 거절한다.
기존 CARLA 자원·shared host lease·exclusive world·소유 actor 정리 fence를 유지한다.
지도 8MiB, scene 4MiB, 전체 native 근거 128MiB 및 closed ZIP member 집합을 검사한다.
Core/SDK는 CARLA를 import하지 않는다.

## 보존 RGB·LiDAR·radar

정지 streaming 결과의 봉인 export·prepared input·rig hash와 native manifest의
전체 member 집합·clock·sensor/parent ID·readback·측정/ego matrix를 먼저 확인한다.
현재 표본만 인증된 ZIP Range로 읽고 member SHA·크기·16-byte stride를 확인한다.
카메라 BGRA8을 원래 640×360 RGBA로 표시하며 캐시는 8장이다. 반환점 캐시는 16프레임이다.
재생은 모든 활성 카메라와 반환점의 원본 검증이 끝날 때까지 기다린다.

LiDAR는 원래 `(x,y,z,intensity)` LH float32 tuple이다. Radar는 원래
`(velocity,azimuth,altitude,depth)` tuple을 보존한다. 등록 좌표는
`S × inverse(M_ego_LH) × M_sensor_LH × p_sensor_LH`로 계산한 ego RH다.
선택 point index는 프레임 안의 순서이며 actor 추적 ID가 아니다. 빈 원본 반환도 빈 상태로 유지한다.

기존 A/B 첫 tick radar 속도는 약 564–686m/s로 비정상적이다. |100|m/s 초과 표본을
이상으로 눈에 띄게 표시하고 원본값·분모·다운로드를 유지한다. 보정하거나 클리핑하지 않는다.
이 표시는 source 이상 진단이며 물리 속도 허용 기준이 아니다. 후속 tick은 이 원본에서 0m/s다.
nominal 80m 범위의 약간의 native depth 초과도 임의로 자르지 않는다.
A/B는 순차 실행이며 world clock·교통 신호·renderer 이력이 다르다.

## API·SDK

기존 `/api/v1/experiments`의 프로젝트별 capabilities/preview/create/submit/detail/export/artifact를
사용한다. GET은 실행을 제출하지 않는다. UI와 agent의 권한·불변 ID·queue·hash가 같다.
SDK `export_experiment_execution()`과 `download_experiment_artifact()`가 원본 SHA/크기를 검사한다.
[읽기 전용 SDK 예제](../examples/sensor_scene_investigation.py)는 scene/map/trace/manifest와
봉인 export를 새 폴더에 내려받는다. ZIP 전체 다운로드나 엔진 실행을 자동으로 하지 않는다.
토큰은 환경변수로 설정하고 command line이나 결과 파일에 기록하지 않는다.

## 실제 인수 · 2026-10-08

정상 UI로 새 native 실행 **`job_e90dead0980c4c6fbdac08c677b9f6e7`**을 한 번 제출했다.
CARLA build `1ae5356`, reference map-lane controller, configuration revision 1/seed42,
4초·10Hz·3m/s 목표, 40 causal 응답·전/후방 RGB80장·수평 이동9.6486m를 보존했다.
새 지도 구간78개·native waypoint1,004개와 실제 bbox4.7918×2.1635×1.4877m를 확인했다.
자차/카메라 actor134/135/136 삭제 ACK 이후 별도 새 snapshot의 부재와 설정 복원을 확인했다.
warm-up10tick·응답40tick·정리1tick을 분리한다. 평가 상태는 `not_evaluated`다.

기존 A/B **`job_715992f7f0f34b5199630f8196434ffd`**의 두 rig에서 각각100tick·400record,
총 LiDAR326,882점·radar14,352점을 원래 SHA/clock/행렬로 디코딩했다. 새 GPU 실행을 하지 않았다.
기존 native10/native30, 실패 결과·Object Sim·프로젝트 권한과 현재 외부 접속 세션을 보존한다.
Mock 계약 검사와 위 실제 실행·원본 디코딩은 별개 근거다.
최종 정상 local/외부 브라우저와 보존 인수는 [기계 판독 인수 기록](validation/sensor-scene-workbench-20261008.json)에 기록한다.

Backend/SDK101개·native mock 계약81개·frontend97개와 TypeScript/build가 통과했다.
local/외부 HTTPS 각각 정상 업무4개에서 새·기존 주행 픽셀12개, 정지 A/B 픽셀12개,
반환점 tuple/등록좌표12개, 원본 다운로드·재생·모바일·기존 Object Sim을 확인했다.
두 읽기 전용 브라우저 인수는 API 쓰기0·추가 엔진 실행0이다. 새 native 제출은 정확히 한 건이다.
첫 실제-source UI 인수의 native yaw 거절은 보존했고, 같은 원본으로 수정·재검사했다.
native 주행을 재실행하거나 원본 각도를 바꾸지 않았다.

기존 SQLite 모든 행·봉인 파일·프로젝트 권한과 원래 CARLA/worker PID를 보존했다.
외부 static snapshot만 바꾸고 gateway/tunnel/supervisor·비밀번호·기존 cookie·라우트·고정 만료를 유지했다.
최종 queue/resource는 idle, 원격 dynamic actor0·quarantine 없음이다.
현재 접속은 원래 임시 서버의 2026-10-10 03:45:21 KST 만료까지 유지한다.

남은 gate는 주행 중 RGB/LiDAR/radar의 새 동시 인수, 반응형 주변 actor,
학습형 카메라 정책 피드백, 센서 물리/RAW/ISP 상관성, Log의 공통 공간·시간축,
장시간 팀 운영과 Applied 동등 수준이다. 상용 목표를 완료 처리하지 않는다.
