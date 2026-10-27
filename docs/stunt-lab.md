# 추력 차량 시험장

Object Sim의 **추력 차량 시험장**은 범용 스포츠카와 제한된 4개 추력기의 가정 모델을
저장하고 CARLA에서 실행하는 실험 화면이다. Tesla의 차량 형상·추력 사양·제어기를
재현하거나 실차 안전성을 검증한 기능이 아니다. fidelity는
`assumed_vehicle_rigid_body`, `physical_validation`은 `not_established`이다.

## 사용자 흐름

`#object-sim?project=default&mode=stunt`에서 Kickflip 기준/추력 부족 프리셋을 선택한다.
질량·진입 속도·총 추력·충격량 예산·목표 고도·시간 간격을 편집하고 개정을 저장한 뒤
실행 계획을 확인하고 제출한다. 실행과 취소는 기존 Experiment 큐를 사용한다.
완료 상태는 실행기의 종료 상태이며 착지 판정과 다르다. 영상·시계열·실패 결과를
보존하고, 저장된 실행 A/B와 개정을 URL로 복원한다.

기체 좌표의 네 노즐 위치는 `[±1.5, ±0.75, 0] m`이다. 각 노즐은 기체 Z 방향의
양방향 힘을 내며, 노즐별 한도와 합계 절댓값 한도 및 총 충격량 예산을 적용한다.
힘과 작용점으로부터 모멘트를 계산한다. 이는 실물 가스 장치가 아닌 제한된 추력원
모델이고 가스 열역학·동적 질량·공력 보정은 포함하지 않는다. 지상 초기 상태 이후
차량 위치/자세를 강제 설정하지 않는다. 제어 입력은 엔진 ground truth이며 카메라
인지 기반 자율주행이 아니다.

차량은 CARLA의 `vehicle.audi.tt` 에셋이며 질량을 설정값으로 변경한다. 관성 텐서와
타이어·차체 충돌은 엔진 에셋의 모델을 사용한다. 노즐 위치는 가정한 질량 중심을
기준으로 하며 실차의 노즐·질량 중심을 측정한 값이 아니다. 진입 속도는 출발 전
한 번 초기화하고, 이후 상태는 외력·모멘트·중력·접촉으로 계산한다.

착지 판정은 1회전 완료, 관측한 접촉, 접촉 전 수직 속도 2m/s 이하, 마지막 연속
1초 동안 roll/pitch 각각 10도 이하 및 속도 0.5m/s 이하를 요구한다. 이는 화면에
명시한 데모 기준이며 실제 차량이나 탑승자의 안전 기준이 아니다.

## API와 SDK

`/api/v1/object-sim/stunt-lab` 아래에 다음 API를 제공한다.

- `GET /templates`, `GET /readiness`: 입력 계약과 설치/설정/물리 응답 확인을 구분한다.
- `POST /designs`: `Idempotency-Key`와 `expected_revision`을 사용해 불변 개정을 저장한다.
- `GET /designs`, `GET /designs/{id}?revision=N`: 프로젝트 범위의 저장 개정을 읽는다.
- `POST /designs/{id}/preview`: 고정 개정·해시를 기존 Experiment 요청으로 준비한다.
- `GET /executions`, `GET /comparison`: 해당 어댑터의 저장 실행과 비교를 조회한다.

실행 생성/제출/취소/export는 기존 `/experiments` 계약을 사용한다. Python SDK의
`stunt_lab_*` 및 `save_stunt_lab_design`/`execute_stunt_lab_design` 메서드도 동일 API를 사용한다. 맵·차량·노즐 모델·제어기·native 스크립트·
엔진 빌드·설정·seed와 파일 해시를 입력/출력에 보존한다. 공개 API에서 SSH 명령이나
원격 파일 경로를 받지 않는다.

## 전용 실행 환경

로컬 데모 API/worker는 임시 터미널 실행 대신 다음 명령으로 분리 실행한다.
기존 전용 `environment.json`과 `data`를 사용하며 새 native 작업을 제출하지 않는다.

```sh
.venv/bin/python scripts/stunt_demo_service.py start --runtime .runtime/stunt-demo-20261023
.venv/bin/python scripts/stunt_demo_service.py status --runtime .runtime/stunt-demo-20261023
```

시작 시 API health와 해당 worker의 새 heartbeat를 확인한다. 로그와 PID/시작
identity는 선택한 runtime에 남고, 프로세스 종료 시 자동 재시작하지 않는다.
화면 서버 `5350`과 전용 CARLA는 별도다. [API 500 복구 기록](validation/stunt-lab-20261023/api-recovery.md).

선택적 `axiom-carla-adapter`가 `carla-stunt` 실행기를 제공한다. API/worker 양쪽에
같은 `AXIOM_CARLA_STUNT_` 환경 변수를 설정한다: `SSH`, `PYTHON`, `SCRIPT`, `DIR`,
`OWNER`, `SPOOL`, `PORT`. 서버는 명시적인 전용 인스턴스여야 하며 다른 차량·센서가
있으면 거절한다. 기존 서버를 재시작하거나 world를 자동 교체하지 않는다.

API/worker에는 `axiom-carla-adapter[stunt]`의 OpenCV 영상 검증 의존성이 필요하다. 원격 Python에는 서버와 일치하는 CARLA client와 ffmpeg가 필요하다. 원격 source에는
`stunt_native.py`, `stunt_math.py`를 함께 배치한다. owner JSON에는 해당 실행의 PID,
port, `purpose: AXIOM isolated stunt acceptance`를 기록한다. `--port`, `--owner`,
`--spool` 옵션은 모든 native 명령에 필요하다.

- `--probe`: 읽기 전용 버전/맵/비어 있는 world/검증 기록 확인.
- `--commission --output NEW_DIR`: 실제 자유낙하·외력·회전 응답을 확인한다.
- `--output NEW_DIR`: 표준 입력으로 고정 실행 payload를 받아 실행한다.

commissioning은 현재 스크립트 해시·엔진 빌드·서버 PID·맵 해시와 묶인다. 소스나
서버가 바뀌면 기존 기록으로 준비 완료를 선언하지 않는다. 토크 API 단위는 별도
충격량 쌍 응답과 비교하며, 코드에 메서드가 있다는 이유로 검증을 통과시키지 않는다.
정리 실패 시 spool 격리 기록을 유지한다. 원인을 확인하지 않고 이를 삭제해 재사용하지 않는다.

현재 빌드의 시간 표현 차이는 [native clock 조사](carla-stunt-native-clock.md)에
기록했다. 저장 설정의 10/5ms와 엔진에 전달한 정확한 float32 값을 함께 보존하며,
프레임 건너뜀은 거절한다. 카메라 영상은 실제 캡처 시각·world frame과 연결한다.
차량 출발 전 카메라를 0.3초 초기화하고, 출발 이전 콜백 수를 별도로 기록한다.
각 관측은 quaternion뿐 아니라 제어 계산에 사용한 원본 float32 자세 행렬도 보존한다.
검증기는 원본 행렬로 제어 입력을 재계산하고, quaternion/Euler 표현과의 일관성을
별도로 검사한다. 힘·모멘트·충격량 제한은 자세 표현의 반올림 허용치와 구분한다.

## Ramp 경계

`assets/stunt-ramp/source-v1`은 자체 제작한 곡면 램프와 충돌 hull **소스**다.
`scripts/build_stunt_ramp_asset.py`로 재생성하고 해시와 닫힌 메시 topology를 검사한다.
반경 6m, 폭 8m, 110도 곡면과 진입부/착지 바닥을 포함한다.

GPU 서버에 공식 Blender 4.2.23 LTS를 별도 설치하고, 램프·진입로·착지면을 세 FBX로
변환했다. 57개 UCX hull을 포함한 60개 객체의 이름·좌표·삼각형·방향을 재가져오기에서
확인했다. [도구 설치·변환 기록](validation/stunt-ramp-toolchain-20261023/acceptance.md)과
[Unreal import/cook 절차](stunt-ramp-build.md)를 따른다.

현재 배포 CARLA에는 전용 Ramp 맵과 호환 Unreal editor가 없다. import/cook 코드는
준비했지만 실제 Unreal 실행은 아직 검증하지 않았다. Epic 연동 소스 접근 권한과
빌드 디스크 확보가 필요하다. FBX 변환을 native 충돌 검증 완료로 취급하지 않는다. Ramp 설정은 저장할 수
있지만 실행은 차단한다. native 패키지와 호환 빌드를 확보하고 충돌·표면 법선·흡착
거리·벽 주행·이탈·착지 인수를 통과해야 지원 상태를 바꿀 수 있다. 현재 Kickflip에서
`suction_force_n`은 사용하지 않으며 UI는 Ramp 준비 상태와 함께 표시한다.

110도 끝단은 진행 방향이 진입로 쪽으로 돌아오는 형상이다. 이탈 위치·실제 충돌
여유·착지 구역을 함께 검증해야 한다. 별도 `stunt_ramp_math.py`의 형상·접촉·힘 예산
계산은 합성 입력 계약 검사이며 native 제어기에 아직 연결하지 않았다.
