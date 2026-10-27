# ADP / Object Sim / Sensor Sim / Log Sim 통합 인수 · 2026-10-07

승인된 첫 확장 단위를 일반 UI/API/SDK와 기존 영속 queue에 연결했다. 현재 내부 개발
작업공간에서는 입력과 실행 버전을 고정하고, 실제 엔진에 제출하고, 원본 파일·이미지·궤적을
조회하고, 같은 실행 근거를 형식별 검증 계획에 연결할 수 있다.
이 기록의 완료 범위는 아래 기능과 인수 조건이다. Applied 동등성·상용 출시·물리 센서 정확도·
실도로 안전성은 아직 인수하지 않았다. [기계 판독 근거와 파일 해시](validation/four-module-expansion-20261007.json).

## 사용자가 완료할 수 있는 업무

| 모듈 | 정상 화면에서 가능한 업무 | 실제 실행으로 확인한 범위 |
| --- | --- | --- |
| ADP | Project·역할 설정 → 불변 Experiment preview/저장 → 명시적 실행 → 원본 다운로드 → 검증 계획 평가 | Engineer 작성·실행, Reviewer 평가, Viewer 조회·해시 다운로드와 다른 project 차단 |
| Object Sim | 여섯 ScenarioV2 템플릿·3D 위치/액션/트리거 작성 → bounded sweep → 선택 실행 → timeline·observer·native CSV/XML | 실제 Linux esmini 3.8.2의 36회 완료 및 통합 UI에서 추가 native 실행 |
| Sensor Sim | 지원 rig 로드 → RGB 두 대·LiDAR·radar 단일/순차 A/B → 보존 이미지와 전체 native 파일 조사 | 실제 CARLA 10초·30초·10초 rig A/B; 30초의 300 tick·1,200 관측·누락0 |
| CameraE2E 연결 | 동일 보존 RAW에 ISP 후보 둘 설정 → 고정 외부 계획 → 처리 결과·이미지·원본 파일 조회 | 센서 모델이 생성한 네 RAW 프레임·실제 ISP 처리·60 native 파일; 실측 RAW는 아님 |
| Log Sim | 고정 SceneSequence/ReplaySpec → 입력/시각/pose 점검 → 기준 planning feedback → 궤적·시계열·native trace | 기준 controller와 bicycle 피드백; 주변 객체는 기록대로 비반응형 |
| 실제 학습형 정책 | 승인된 연구 입력 import → native VAD queue 실행 → 원래 시점별 세 모드 궤적·원본 receipt 조사 | 실제 nuScenes의 3프레임×6카메라·VAD-Tiny GPU 추론. 비상업 연구이며 정책 A/B·안전 판정은 아님 |

화면·API·SDK·agent catalog는 같은 버전의 계약을 사용한다. Engine import와 운영자 실행 경로는
선택적 adapter 패키지에 있다. 미설정·미지원 능력은 preview에서 거절하고 예제 결과로 대체하지 않는다.
사용법은 [공통 계약](experiment-contract.md), [Object](object-sim-v2.md),
[Sensor/ISP](sensor-capture-expansion.md), [Log](planning-replays.md), [VAD](driving-policies.md)를 따른다.

## 접속과 현재 실행 상태

주소는 **http://127.0.0.1:8020/#experiments**다. Built frontend·API·기존 worker를 한 진입점에서
실행하고 있으며, 데이터는 `.runtime/internal-platform/data`에 있다. 화면의 `Workspace settings`에
`.runtime/internal-platform/access/api-token` 값을 입력한다. 이는 **로컬 API 토큰**이며 SSH 또는
Applied 계정 비밀번호가 아니다. 이 문서에는 비밀 값을 저장하지 않는다.

현재 source checkout에 맞는 운영자 설정으로 다시 시작할 때는 저장소 루트에서 실행한다.

```sh
.venv/bin/python .runtime/internal-platform/start.py
```

이 private runtime wrapper는 검증된 engine 설정을 읽고 `scripts/internal_platform.py`를 실행한다.
통합 작업공간의 ISP adapter는 별도 소유 CameraE2E 서비스 `127.0.0.1:8772`를 사용한다.
Launcher는 그 외부 서비스나 원격 CARLA를 시작하지 않는다. 설정이 없거나 서비스가 종료된 경우
해당 capability를 실행 가능한 것으로 대체하지 않는다. 일반 설치·백업·복원·종료 절차는 공통 계약에 있다.

최종 readback에서 보존된 7개 실행은 모두 `completed`였고 receipt hash는 원래 값과 같았다.
현재 소유 worker 하나가 살아 있는 idle 상태였고 engine resource 둘은 idle이었다.
복원 데이터의 과거 worker 행은 이력이며 살아 있는 worker로 계산하지 않는다.
기존 운영 DB·서버·원격 CARLA와 외부 공유를 자동 이관·재시작·배포하지 않았다.

In-app browser와 임시 개발 Chrome에서 정상 업무를 확인했다. 기존 사용자 Chrome에는
`ERR_BLOCKED_BY_CLIENT`가 관찰됐다. 사용자 확장·정책을 변경하지 않았고, 이를 모든 브라우저의
접속 성공으로 보고하지 않는다.

## 실행 완료와 검증 판정

Object native 36회는 **27 실패·6 미평가·3 통과**를 보존한다. 이 실행은 작성된 객체 움직임을
실제 엔진으로 실행한 근거이며 실제 주행 정책이나 센서 모델 성능 인수가 아니다. 여러 seed는
현재 결정적인 장면을 독립 장면으로 늘려 주지 않는다. XML import는 플랫폼이 lossless로 내보낸
지원 subset에 한정되며 임의 OpenSCENARIO/OpenDRIVE 호환을 주장하지 않는다.

통합 UI에서 작성·제출한 추가 객체 실행도 native observer의 전체 판정이 **실패**였다.
같은 실행을 연결한 계획은 `maximum_speed ≤20 m/s`라는 선택된 속도 기준만 통과했다.
실제 관측 12 m/s·충돌 실패·계획의 제한된 통과를 모두 유지한다. 계획 통과가 native 전체 실패를
덮어쓰거나 안전성 통과를 뜻하지 않는다.

센서·ISP·VAD 계획은 **context_only / not_evaluated**다. 지원하는 정확도 evaluator가 없으므로
실행 파일이 존재한다는 이유만으로 통과시키지 않는다. 기준 Log의 속도 조건도 작성된 reference
입력과 planar dynamics에 대한 한정된 기준이며 실도로 판정이 아니다.

CARLA RGB는 엔진의 처리된 RGB이며 물리 RAW가 아니다. ego는 고정 상태다. 순차 sensor A/B는
장면 정의·spawn을 고정하지만 world clock·신호·renderer 상태까지 같은 것으로 증명하지 않았으므로
모델 정확도 변화의 인과 실험으로 계산하지 않는다. 36개 1초 GPU 표본에서 관찰한 최대는
4,448 MiB였으며 순간 peak·장시간 성능·p95를 측정한 결과가 아니다.

ISP의 원본 RAW·선택 array 해시와 실제 외부 실행 ID를 보존했다. 이후 제품 queue/UI 제출은 같은
외부 처리 결과의 명시적 idempotent 재사용이며 독립적인 실측 시험으로 늘려 세지 않는다.
렌즈·센서 A/B는 frozen scene plan과 native pixel 대응 계약이 없어서 **미지원**으로 유지한다.

VAD는 실제 GPU 실행을 일반 queue에 연결했고 통합 작업공간에서도 새 native 실행을 완료했다.
하나의 모델·checkpoint만 사용했으며 기록 영상의 open loop다. 학습형 정책 두 개의 A/B,
반응형 센서 폐루프, 정확도와 안전성 판정은 완료하지 않았다. nuScenes와 checkpoint의 상용 권한은
확정하지 않았고 사용자 승인 범위인 격리 비상업 연구를 유지한다.

## 발견한 결함과 보존한 실패

Native CARLA actor의 spawn 직후 pose가 일시적으로 원점 identity로 읽힌 실제 결함을 발견했다.
이를 정상 pose로 사용하지 않도록 native map spawn과 첫 동기 snapshot의 frame/time/pose 일치를
검사하고 이후 모든 기록의 고정 pose를 확인하도록 수정했다. 이전 실패 receipt는 수정하지 않았다.
소유 actor 정리·callback 종료·world 설정 복원을 원본 실패 manifest와 후속 native probe로 확인한 뒤
정확한 이전 resource만 reconciliation했다. 실패와 `outcome_uncertain` 이력은 그대로 남는다.

전체 backend 검사에서 새 schema17을 기존 backup/pilot 도구가 거절하는 실제 결함도 발견했다.
지원 schema 범위와 구 schema 기대값을 수정하고 관련 99개를 재검증했다.
팀 workflow용 실제 데이터의 냉백업·복원과 센서 데이터 87파일·2,299,951,950 bytes의 새 작업공간
복원에서 원본 bytes와 복원 inventory를 확인했다. schema16→17 검사는 사본에서만 수행해 기존
queue ID·순서·key·payload·event와 FK를 보존했다.

## 검증 기록과 남은 조건

검사는 실제 엔진 실행, 작성된 adapter/contract 검사, 브라우저 업무를 분리했다.
전체 backend 최초 결과는 1,403 통과·5 실패였으며 수정 뒤 전체를 다시 돌렸다고 주장하지 않는다.
이후 관련 99개, 공통/SDK 등 396개, 센서/객체 32개·subtest13개, launcher4개와 최종
Log/VAD/SDK41개가 통과했다. 이 집합들은 겹치므로 합산하지 않는다.
정상 역할 흐름·구 API 호환·reference Log·실제 VAD·실제 ISP·실제 객체 실행·센서 조회의
브라우저 업무와 최종 frontend 타입 검사·production build도 통과했다.

프로젝트 권한은 신규 모듈에 적용한다. 기존 Recording·Case·Dataset·모델처럼 project 소유권이
없는 전역 리소스는 team token에 노출하지 않고 operator 영역으로 유지한다. 팀 전체 업무의 격리와
실제 팀원 반복 사용 인수를 완료했다고 보지는 않는다.

다음 완료 조건은 frozen lens/sensor plan·픽셀 대응, 실제 승인 도로 object/planning 로그,
두 실제 정책의 동일 입력 A/B, 반응형 카메라 폐루프, depth/segmentation 인수,
학습형 의미 검색·시간 사건 구간, legacy 데이터 소유권과 팀 반복 인수다.
물리/실도로 상관성·장시간/부하/장애·다중 worker의 engine 소유권·상용 데이터/모델/자산 권한과
출시 조건도 남아 있다. 상용 플랫폼 목표는 계속 열린 상태다.
