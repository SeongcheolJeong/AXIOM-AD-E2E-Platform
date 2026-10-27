# Sensor Sim 배치 편집 베타

첫 내부 베타는 **배치 편집 → 개정 저장 → 같은 주행 조건의 A/B 실행 → 원본 비교 → 시점 관찰 → 이어하기**다.
새 센서 모델이나 상세 물리 검증보다 참가자가 스스로 이 흐름을 완주할 수 있는 기능을 우선한다.
기존 발표 프리셋과 대표 결과는 유지한다. 공통 과제·피드백·운영자 처리에는
[내부 베타 작업공간](beta-workspace.md)을 사용한다.

## 편집과 저장

`#sensor-sim?project=PROJECT_ID&mode=layout`에서 배치를 작성한다. 전방 RGB·후방 RGB·LiDAR·Radar의
고정 네 센서를 사용하며, A는 기본 주행 배치이고 B의 위치와 회전만 바꾼다.
XYZ는 차량 좌표의 m, 회전은 pitch/yaw/roll의 도다. 입력 간격은 0.05m와 1도이며,
현재 수집 계약의 ±10m·±180도 범위를 벗어나면 저장을 거절한다.
상단/측면 도식은 배치 이해를 위한 도식이며 광학·전파 모델 출력이나 실제 수집 프레임이 아니다.

이름·메모·B 배치를 프로젝트별 개정으로 저장하고 복제·개정 이력·정확한 재열기를 제공한다.
저장은 엔진이 없어도 가능하다. A/B가 같은 배치인 초안도 저장할 수 있지만 비교 실행에는
한 개 이상의 배치 변경이 필요하다. 센서 종류·개수·내부 파라미터·지도·제어기는 이 화면에서 고정한다.

## 실행과 결과

기존 `carla-driving-multisensor/1`을 사용한다. Town10HD_Opt spawn 0에서 목표 속도 3m/s,
20초·10Hz의 지도 기반 참조 주행을 수행한다. 같은 장면/경로/seed 설정으로 A와 B를 순차 실행하며,
각 주행 안에서 네 센서를 함께 수집한다. 두 궤적의 수치적 일치를 보장하지 않는다.

준비 확인은 저장 개정/hash에 고정한다. 실행 제출은 기존 Experiment queue를 사용하고
해당 배치 개정과 실행 ID를 연결한다. 같은 제출의 재시도는 중복 실행을 만들지 않는다.
등록된 어댑터, worker 존재, 실제 native 준비 확인은 서로 다른 상태다.
엔진이 없거나 지원하지 않는 입력이면 실패 이유를 표시한다.

결과는 수정 중인 편집기 대신 그 실행에 저장된 A/B 배치를 표시한다. 원본 RGB와 LiDAR/Radar를
공통 상대 시간으로 조사한다. 기존 프리셋의 단일 속성 변경 규칙은 유지하고 배치 비교만 별도로 판별한다.
관찰은 배치 개정/hash·실행·센서·A/B 측·시점에 묶고 JSON으로 내보낸다. 200개 수집 프레임의
관찰 가능 시각은 0–19.9초이며, 저장 관찰의 링크로 같은 결과와 시점을 다시 연다.

## 안내 과제

공통 작업공간에서 Sensor 과제를 만들고 다음 중 하나를 선택한다.

| 안내 | 변경과 관찰 |
| --- | --- |
| 카메라 방향 | 카메라 회전을 바꾸고 원본 영상 구도를 비교한다. |
| LiDAR 높이 | LiDAR의 Z를 바꾸고 같은 상대 시점의 native 반환점을 비교한다. |
| Radar 방향 | Radar 회전을 바꾸고 native 검출 분포를 비교한다. |

보존 결과를 조사한 과제와 새 실행을 완료한 과제는 구분한다. 참가자는 막힌 단계와 의견을 남기고,
운영자는 기존 집계·처리·내보내기 흐름에서 확인한다. 첫 배치는 직접 프로젝트 인증된 서비스에서
사용한다. 임시 gateway의 Sensor 쓰기 권한은 이번 변경으로 확대하지 않는다.

## API

UI와 Python SDK는 다음 versioned API를 공유한다. 저장·실행·관찰 쓰기는
`Idempotency-Key`를 사용하고, 새 개정은 `expected_revision`으로 충돌을 확인한다.

| 경로 (`/api/v1` 아래) | 동작 |
| --- | --- |
| `GET /sensor-sim/layouts/templates` | 고정 A/B 초안·입력 범위·안내 과제 |
| `GET/POST /sensor-sim/layouts` | 프로젝트 목록·저장·복제 (`copied_from`) |
| `GET /sensor-sim/layouts/{id}` | `revision`으로 정확한 저장 상태 조회 |
| `GET /sensor-sim/layouts/{id}/revisions` | 개정 이력 |
| `POST /sensor-sim/layouts/{id}/preview` | `layout_ref`를 실제 adapter 준비 확인에 고정 |
| `POST /sensor-sim/layouts/{id}/executions` | `layout_ref`와 `preview_hash`로 생성·제출·연결 |
| `GET /sensor-sim/layouts/{id}/executions` | 개정별 실행 이력 |
| `GET/POST /sensor-sim/layout-observations` | 원본 프레임에 고정된 시점 관찰 |
| `GET /sensor-sim/layouts/{id}/export` | 배치·실행·관찰·원본 hash JSON |

`layout_ref`는 `id`, `revision`, `sha256`이다. 프로젝트 Engineer가 작성·실행하며 Viewer는
저장 상태와 결과를 읽을 수 있다. 기존 Experiment 원본 다운로드와 실행 상태 조회를 그대로 사용한다.

## 인수와 운영 경계

2026-10-11 로컬8088의 정상 Engineer 브라우저에서 카메라 방향 r1 저장, LiDAR 높이 r2 저장,
범위 밖 입력 차단, 새로고침·r1 복원·복제, 베타 과제 r2 연결·이어하기·피드백을 확인했다.
두 브라우저 JSON 다운로드는 SDK와 일치한다(내보낸 시각 제외). 집중 backend/SDK121개,
기존 adapter/주행/demo67개, frontend56개와 production build가 통과했다.
[오프라인 작성 인수 기록](validation/sensor-layout-beta-20261011.json)은 당시 상태 그대로 보존한다.

이후 사용자 승인으로 HIL 2100에서 Sensor 2000으로 잠시 전환하고, 저장 배치
`layout_65b1f1cc26cc4c1f82343d54df3a0a5e`의 r2
(`cb4d961872456219ef31288201018ac8c5d2857e5a04c11c7e30c03d6c143920`)를 실제 실행했다.
B는 전방 카메라 yaw −15°와 LiDAR Z 2.5m이며, 작업
`job_ef6750df40a0431e86480accc3d9069c`의 A/B 각각 20초·200tick·네 stream별 200개,
총 800개 관측을 보존했다. 양쪽 누락 0과 소유 객체 정리·world 설정 복원·lease 해제를 확인했다.

정상 Engineer 브라우저에서 네 센서의 A/B 원본 재생과 9.9초 LiDAR 관찰
`layoutnote_1af2b9bf960640a5bb02e5491ad822b1` 저장·새로고침 복원을 확인했다.
브라우저 export는 `exported_at`을 제외하면 SDK와 같으며, 연결 과제
`betatask_69aef7082016e63ac6e7e06a991e0150`은 `new_execution_observation`으로 완료됐다.
결과의 **크게 보기**는 페이지 안에서 확대하고 Escape로 닫는다. 지원되지 않는 native fullscreen을
조용히 요청하던 동작을 이 방식으로 바꿨다. 이는 개발자가 수행한 업무 인수이며 독립 참가자 인수가 아니다.
[실제 실행·브라우저·운영 복원 기록](validation/sensor-layout-native-20261011.json).

종료 후 HIL 2100과 기존 8075 서비스를 복원했고 Sensor 8088은 엔진 미설정의 작성·보존 결과
재생 상태로 되돌렸다. 저장된 배치·원본·관찰은 계속 조회할 수 있다. 새 Sensor 실행에는 별도의
GPU 사용 시간과 엔진 준비가 필요하며, 이번 전환으로 참가자 초대나 공개 ingress를 열지 않았다.

물리 센서 정확도, 제조사 모델, 재질/날씨 상관성, 센서 입력 기반 E2E 폐루프, 독립 센서 추가와
대규모 동시 실행은 후속 범위다. 실제 사람 3–5명의 독립 완료와 막힌 단계 수집도 남아 있다.
