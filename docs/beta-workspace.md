# ADP 내부 베타 작업공간

내부 3–5명이 기존 기능으로 과제를 시작하고, 저장된 작업을 이어가며,
문제가 생기면 재현 정보를 붙여 운영자에게 남기는 공통 작업공간이다.
Object·Log·Software-DUT HIL·Sensor 배치 편집이 안내 과제 대상이며 Neural에도 공통 의견 입력을 제공한다.

## 참가자와 운영자

- `#workspace?project=PROJECT_ID`에서 모듈 상태, 자신의 프로젝트 과제와 최근 작업을 확인한다.
- 새 실행과 보존 결과 체험을 구분한다. 저장된 결과를 열었다고 새 실행을 완료한 것은 아니다.
- Object는 시나리오 개정, HIL은 시험 개정, Sensor는 배치 개정과 실행·관찰을 연결한다. Log는 기존 안내 과제를 그대로 사용한다.
- Sensor는 카메라 방향·LiDAR 높이·Radar 방향 안내를 선택한다. 센서·시점·저장 개정·실행을 고정한
  이어하기를 제공한다. [배치 편집 범위와 인수 상태](sensor-sim-beta.md)를 따른다.
- 연결한 Object/HIL/Sensor 실행에 관찰이 없다면 공통 과제에서 시점·제목·내용을 작성해 기존 관찰 API에
  저장할 수 있다. 관찰 저장 후 과제 연결만 실패하면 같은 관찰의 연결을 재시도한다.
- 문제 신고에는 사용자가 설명을 쓰고 허용된 작업 참조와 시점을 연결한다. 인증정보·원본 파일은 자동 첨부하지 않는다.
- 운영자는 전체 프로젝트 진행·막힘·의견을 확인하고 접수/조사 중/해결로 처리한다. 집계는 JSON/CSV로 내려받는다.
- 모듈별 API/worker가 서로 다른 서비스에 있다면 각각의 주소를 사용한다. 공통 화면은 현재 접속한 서비스의 프로젝트만 조회한다.

## 실행 준비 상태

어댑터 설정은 worker나 원격 엔진의 실행 준비 완료를 뜻하지 않는다.
작성, 보존 결과 조회, 새 실행 상태와 확인 시점을 따로 표시한다.
확인할 수 없는 실행 준비는 확인 필요로 남긴다. 상태 조회는 원격 엔진을 시작하거나
시험을 자동 제출하지 않는다. 실행 불가 시 작성·저장 결과 조회·문제 신고를 이용한다.
기존 실행 정책·프로젝트 권한·정확한 개정/hash 연결은 계속 적용된다.

## API와 저장

UI, Python SDK의 `Client`, agent tool은 `/api/v1/beta`의 동일한 계약을 사용한다.
`readiness`, `evidence`, `tasks`, `tasks/{id}/progress`, `feedback`이 참가자 경로다.
`overview`와 `feedback/{id}/triage`는 운영자 전용이다.
쓰기는 `Idempotency-Key`, 갱신은 예상 개정을 사용한다. 과제와 의견은 기존 버전형 카탈로그에
저장되며 시험·실행·Case 원본을 복제하지 않는다. Log 안내 과제는 기존 기록을 투영한다.

## 초대 전 준비

1. 기존 Team workspace/API에서 참가자별 독립 프로젝트와 Engineer 자격을 준비한다.
2. 각 서비스에서 해당 프로젝트에 허용된 대표 입력·시험을 연결한다. CARLA 발표 자료를 기본으로 하며
   nuScenes의 기존 로컬 비상업 연구 범위를 외부 공개 권한으로 확대하지 않는다.
3. 운영자는 새 실행 가능 시간·GPU 사용 순서·저장 공간·결과 보존 위치를 확인한다.
4. 참가자는 모듈당 대표 과제 한 건을 시도한다. 도움 여부와 막힌 단계를 기록한다.
5. 운영자는 실제 사람의 독립 완료를 확인한다. 자동화된 브라우저 검사 결과를 참가자 인수로 계산하지 않는다.

기존 직접 인증된 로컬 서비스에서 바로 사용할 수 있다. 비밀번호 전용 임시 gateway가 필요하면
기존 Engineer token 파일을 사용해 별도 설정을 준비한다. 다음 명령은 서버를 시작하거나 초대를 보내지 않는다.

```sh
.venv/bin/python scripts/beta_workspace_access.py \
  --upstream http://127.0.0.1:8075 \
  --project-id PROJECT_ID \
  --engineer-token-file /absolute/private/engineer-token \
  --dist /absolute/path/to/frontend/dist \
  --destination /absolute/private/new-beta-access
```

설정은 기존 `scripts/object_beta_access.py serve --config ... --port ...`로 서비스할 수 있다.
공개 ingress·실제 서비스 실행/재시작은 별도 운영 작업이다. 기존 설정 파일은 자동 변경하지 않는다.
이 gateway는 Log의 준비된 팩을 지원하며 바이너리 MCAP 업로드는 직접 인증된 서비스에서 수행한다.
Sensor·Neural gateway 범위는 보존 결과 조회와 의견 입력이다. 운영자 집계·처리 API는 gateway에서 차단된다.

## 검증 경계

집중 API·SDK·gateway 검사와 격리된 브라우저 과제를 사용한다. 검증 fixture는 작성한 예제이며
실제 센서·ECU·CARLA 결과로 주장하지 않는다. 실행 엔진 동작을 바꾸지 않는 이번 공통 계층은
새 native 실행을 요구하지 않는다. 실제 참가자 초대·공개 배포·대규모 동시 실행·물리 검증은 별도다.

2026-10-11 집중 검사94개, 기존 gateway 검사141개와 최신 frontend build를 통과했다.
격리된 브라우저8개 흐름에서 개정 복원·관찰 작성·과제 완료·신고·권한·운영자 처리를 확인했다.
브라우저 실행에는 작성한 queue 결과만 사용했고 native 엔진은 실행하지 않았다.
[검증 기록](validation/beta-workspace-20261011.json).

Sensor 배치의 별도 개발자 인수에서는 저장 r2의 실제 A/B를 실행하고 네 센서 원본 재생,
9.9초 LiDAR 관찰·이어하기·JSON 대조를 확인했다. 연결 과제
`betatask_69aef7082016e63ac6e7e06a991e0150`은 `new_execution_observation`으로 완료됐다.
HIL2100·8075 복원 후 Sensor8088은 작성·보존 결과 조사 상태이며, 이 결과는 실제 참가자3–5명의
독립 완료로 세지 않는다. [Sensor native 인수](validation/sensor-layout-native-20261011.json).
