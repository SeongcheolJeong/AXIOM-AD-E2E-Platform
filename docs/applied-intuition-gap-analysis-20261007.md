# Applied Intuition 대비 AXIOM 구현 상태 분석

분석일: **2026-10-07** · 비교 문서: **Applied Home / ADP v1.75**

현재 AXIOM은 **데이터 조사·선별·검출 평가·검증 계획을 연결한 내부 개발판**이다.
실제 CARLA 센서/제어와 실제 VAD-Tiny GPU 추론 근거도 존재한다. 다만 이 근거의 지원 범위는
좁으며, Applied의 개발 플랫폼과 기능·정밀도·규모·운영에서 동등하다고 판정할 수 없다.

다음 개발의 중심은 실제 주행 모델의 결과를 일반 실행·DB·검증 계획에 연결하는 것이다.
센서/ISP의 실제 처리와 평가, 학습형 검색, 팀 운영을 이어야 현재의 공통 기반이 실사용 플랫폼으로 확장된다.

## 확인 방법과 비교 범위

- 제공된 계정으로 `home.applied.co`에 정상 로그인하고 아래 문서/소개 페이지를 직접 열람했다.
  계정 식별자·비밀번호·인증 토큰은 이 보고서와 감사 기록에 저장하지 않았다.
- 현재 소스, UI/API/SDK 연결, 날짜가 명시된 인수 기록과 내용 해시를 대조했다.
  이번 분석에서 GPU 추론·CARLA 실행·전체 테스트·운영 서버 재배포를 새로 수행하지 않았다.
- 소스 체크아웃, 독립 인수 패키지, 현재 운영 배포는 같은 상태라고 가정하지 않았다.
  특히 VAD 인수 기록의 `operating_platform_deployed`는 false다.
- 일부 상세 문서는 계정 권한 밖이었다. Validation Toolset, Cloud Engine, Neural Sim, Copilot,
  VehicleSim은 제품 소개로 이동하며 조직 접근 권한이 없다는 안내를 보였다.
  VehicleSim 소개에는 제목 외 상세 기능 내용이 없었으므로 ADP 개요의 동역학 설명까지만 확인했다.
- Applied의 설명은 공급사가 기술한 기능 범위다. 이 조사에서 Applied 실행 성능·정밀도·결정성을
  독립 측정한 것은 아니다. 비교를 위해 문서를 요약했으며 원문·이미지·코드·자산을 복제하지 않았다.

공개 [제품 목록](https://www.appliedintuition.com/products)은 Dana, Tools for Vehicle Intelligence,
SDS, Vehicle OS를 구분한다. 이번 비교 대상은 주로 **개발·데이터·시뮬레이션·검증 도구**다.
차량에 탑재되는 상용 SDS와 Vehicle OS 전체를 개발 플랫폼의 미구현 항목으로 합산하지 않았다.
현재 공개 개발 방향은 [Tools for Vehicle Intelligence](https://www.appliedintuition.com/physical-ai)와
[Dana](https://www.appliedintuition.com/dana)도 함께 참고했다.

## 현재 사용자가 완료할 수 있는 업무

1. 지원 MCAP를 등록하고 카메라/점군/신호를 원본 시간·채널·ordinal과 함께 조사한다.
2. 관심 구간 또는 평가 실패 이미지로 Case를 만들고 조건·검토 상태·근거를 개정한다.
3. 여러 원본을 Collection으로 고정 선별하고 중복·제외·미검토를 보존한 Dataset을 발행한다.
4. 등록된 검출 모델의 라벨 초안을 확인하고 사람이 수정·검토한다. 선택한 reviewed 표본의
   알려진 라벨 생성 모델과 평가 모델의 가중치 SHA가 같으면 평가 제출을 거절한다.
5. 고정 데이터와 두 검출 모델을 Verification Plan에 선택해 추론·COCO 평가를 실행하고,
   전체/조건별 악화와 데이터 부족·라벨/조건 검토·미평가를 나눠 조사한다.
6. Case에서 지원되는 수동 참조 시나리오를 만들고 Suite/Campaign 결과를 다시 연결한다.
   원본 영상·센서 조건의 재현 시험으로 계산하지 않는다.
7. 개발 CARLA profile에서 제한된 실제 시뮬레이션을 실행하고 저장된 RGB/LiDAR·제어·응답을 조사한다.
   CARLA를 자동으로 로드하지 않는 독립 local pilot과는 별도 실행 profile이다.
8. Driving Policy 화면에서 실제 VAD 입력/결과 JSON의 계약과 해시를 검사하고 시계열 궤적을 표시한다.
   이 화면에서 서버 GPU 추론을 제출하거나 정책 A/B·검증 판정을 끝낼 수 있는 상태는 아니다.

이 목록은 지원 범위 안의 제품 경로를 설명한다. 모든 경로가 최신 운영 배포에 반영됐다는 의미나
실제 내부 사용자 데이터로 전체 업무를 새로 인수했다는 의미는 아니다.

## 기능별 비교

상태는 `제한 사용 가능`(일반 UI/API 업무 연결), `부분 구현`(주요 범위 부족),
`연구 실행 인수`(실제 실행 근거가 있지만 제품 실행 연결 미완성), `미구현`으로 구분한다.
어떤 항목도 Applied 동등성 또는 상용 출시 완료를 뜻하지 않는다.

| 기능 영역 | Applied 문서에서 확인한 범위 | 현재 AXIOM과 가능한 업무 | 상태와 남은 핵심 |
|---|---|---|---|
| 통합 플랫폼·공통 DB | [ADP](https://home.applied.co/manual/v1.75/docs/adp/)의 데이터·시뮬레이션·검증 통합 | Scenario/Run/Recording/Case/Collection/Dataset/Model/Suite/Campaign/Plan 개정과 원본/파생물 추적 | **제한 사용 가능.** 학습형 PolicyRun·센서 실험의 영속 실행 모델, 프로젝트 인가 경계 부족 |
| Data Explorer | [로그 탐색](https://home.applied.co/manual/v1.75/docs/data_explorer/), 이벤트 추출·triage·Collection/하류 도구 연계 | MCAP 시간 탐색, 지원 이미지/점군 decoder, Case·조건 검색·Collection | **제한 사용 가능.** fleet ingestion, 자동 사건 추출·담당자 운영, 실제 도로 로그 품질/용량 인수 부족 |
| 라벨·검출 모델 평가 | ADP의 perception 검증과 데이터 개발 흐름 | 라벨 개정, ONNX 추론, COCO bbox, 동일 기준 A/B, 실패 Case, 모델 출처 보호 | **제한 사용 가능.** 다중센서/3D/추적 라벨·통계적 판단·승인 실데이터 업무 인수 부족 |
| 의미·행동 검색 | 데이터 검색/사건 탐색, Dana의 장면 선별 | 수동 장면 태그, 8×8 RGB 배치·히스토그램 시각 유사도 | **부분 구현.** 학습형 의미/문장 검색, 끼어들기 등 시간 구간 사건 탐색 없음 |
| Object Sim | [객체·행동·observer·변수 sweep·외부 stack](https://home.applied.co/manual/v1.75/docs/object_sim/) | 시나리오 개정, 참조 2D 운동학, 제한 sweep/기준, 교체형 stack, Suite/Campaign | **부분 구현.** 일반 행위 라이브러리·복잡한 교통/ODD·상호작용 편집·범용 observer 언어·다른 실제 엔진 인수 부족 |
| Log Sim | [원본 메시지/시간 보존과 출력에 반응하는 재시뮬레이션](https://home.applied.co/manual/v1.75/docs/log_sim/) | 원본 MCAP 재생, 검출 재평가, 기록 ego/LiDAR를 기하 stack에 입력하는 open-loop 비교 | **부분 구현.** 제어에 따라 자차와 다음 센서 입력이 변하는 로그 기반 폐루프 없음 |
| Sensor Sim | [물리 센서·배치·preview·open/closed-loop 평가](https://home.applied.co/manual/v1.75/docs/sensor_sim/) | 실제 CARLA RGB/ray-cast LiDAR 수집, 고정 LiDAR 설정/rig·속성/pose readback | **부분 구현.** 실측 상관성, Radar/초음파·실제 CameraE2E/RAW/ISP 처리·센서 A/B 업무 없음 |
| 실제 학습형 E2E | ADP의 외부 stack 연결, 공개 개발 도구의 모델 실행/평가 | 실제 VAD-Tiny 3-frame GPU 실행, 반복 결과 일치, 일반 화면의 receipt/궤적 확인 | **연구 실행 인수.** 서버 dispatch·PolicyRun DB/queue·동일 입력 A/B·Plan 판정 연결 없음 |
| Synthetic Datasets | [분포 기반 장면 생성→다중 노드 실행→라벨·검토·표준 export](https://home.applied.co/manual/v1.75/docs/synthetic_datasets/) | 기존 원본 이미지·라벨의 선별/개정/평가 | **생성 기능 미구현.** Dataset 관리와 합성 데이터 생성은 다른 기능; depth/flow/segmentation 생성 pipeline 없음 |
| Neural Sim | [로그 기반 환경/센서 재구성과 폐루프](https://home.applied.co/product/neural_sim) 소개 | 지원 로그/센서 원본과 도식 3D 재생 | **미구현.** 동적 3D reconstruction, 새 시점 렌더링, reconstruction 품질 평가 없음 |
| Map Toolset | [지도 편집·OSM·질의·3D world generation·지도 검증](https://home.applied.co/manual/v1.75/docs/map_toolset/) | 참조 도로 도식과 CARLA 기존 맵 사용 | **미구현.** 제품 지도 import/editor/query/검증·world generation 없음 |
| VehicleSim | [ADP 개요](https://home.applied.co/manual/v1.75/docs/adp/#vehicle-dynamics)의 차량동역학 기능; 제품 상세는 접근 불가 | CARLA 설정 readback·응답 trace와 분리된 차량 모델 진단 연구 | **동등 모델 연결 미구현.** 고정 차량 모델의 실측 상관성·다른 동역학 엔진 계약/인수 없음 |
| HIL Sim | [ECU 센서 주입·TAP·실시간 transport·SIL/HIL 공통 시험](https://home.applied.co/manual/v1.75/docs/hil_sim/) | 공통 scenario/evidence 계약 기반 | **미구현.** 실제 장비·신호 주입·시간/지연·fault·실시간 동역학 연결 없음 |
| Cloud Engine | [CI trigger·병렬 대규모 실행·회귀 조사](https://home.applied.co/product/cloud_engine) 소개 | 동일 host/DB durable queue, lease·취소, 선택형 원격 CARLA worker, 일부 실제 장애 결과 회수 | **부분 구현.** cluster scheduler·autoscale·분산 DB/소유권·GPU pool·운영 규모 인수 없음 |
| Validation Toolset | [요구사항·traceability·추상/논리 시나리오·coverage/성능](https://home.applied.co/product/validation_toolset) 소개 | 요구사항/시험 기준, Suite/Campaign/Baseline, 검출 A/B Plan과 조건별 공백 | **제한 사용 가능.** Plan은 detection_ab만 판정; 일반 ODD 제약/자동 샘플링·공백 생성·E2E/센서 판정 없음 |
| Visualization | ADP의 시나리오/신호/센서/결과 조사 | orbit/top/chase 도식 3D, 시간축·신호·카메라/점군·검출/라벨·A/B·공백·VAD 궤적 | **제한 사용 가능.** 실제 map geometry·대용량 point cloud·동기 다중 run 비교·복잡한 scene 편집 부족 |
| API·SDK·Agent | [REST/SDK/메시지 레퍼런스](https://home.applied.co/manual/v1.75/reference/) 및 [Data Explorer SDK](https://home.applied.co/manual/v1.75/reference/data_explorer_sdk/) | 같은 `/api/v1`, OpenAPI, Python SDK/CLI, llms.txt, 수동 agent tool catalogue | **제한 사용 가능.** Plan/Driving Policy 도구 발견 누락, 실제 agent client 업무 인수·자원별 실행 정책 부족 |
| Copilot·Dana | [Copilot](https://home.applied.co/product/copilot) 자연어 작성/질의; [Dana](https://www.appliedintuition.com/dana) workflow orchestration | 외부 agent가 사용할 공통 API/SDK와 명시적 작업 흐름 | **제품 에이전트 미구현.** 내장 자연어 업무 실행·권한/비용/검토 경계·실제 orchestration 인수 없음 |
| 내부 팀·상용 운영 | local/cloud 개발 도구·협업/enterprise integration 방향 | 단일 운영자 local package, token 경계, cold backup/restore, 임시 인증 read-only viewer | **부분 구현.** 사용자별 인가·프로젝트 격리·SSO/감사·팀 업무·clean install/upgrade·규모/운영/상용 권리 gate 미인수 |

## 강점과 중요한 문제

### 공통 기반은 재사용할 가치가 있다

개정 고정, 원본 identity, 해시, 전체 분모, 미검토/미평가, 비교 불가 조건을 보존하는 구조가
데이터·검출·시나리오 시험에 연결되어 있다. Collection 발행의 원자성, known 모델이 만든
reviewed 라벨의 자기평가 거절, 실패한 Case 재시험 근거 보존도 있다. 미검토 표본의 평가 입력을
일괄 거절하는 것은 아니며, 검토가 완결되지 않으면 aggregate 지표를 억제한다. 새 엔진을 직접 다시 만드는 것보다 이
구조에 실제 모델·센서 실행을 넣는 것이 우선이다.

### 검증 계획의 범위를 확대해 표현하면 안 된다

[`verification_schemas.py:46`](../backend/src/ade2e/verification_schemas.py#L46)의
`verification_type`은 `detection_ab`, 허용 근거는 `reviewed_dataset_predictions`이며 두 모델만 받는다.
[`verification_plans.py:63`](../backend/src/ade2e/verification_plans.py#L63)의 외부
Case/Run/Suite/Campaign 연결은 `satisfies_criteria=False`인 문맥 근거다.
따라서 “모든 시험을 하나의 계획에서 연결하고 판정하는 일반 검증 플랫폼”까지 완성한 상태는 아니다.
시나리오 요구사항/판정은 기존 Validation/Suite 경로에 존재하지만 Plan v1 판정과는 구분된다.

### 실제 VAD 실행 성공과 제품 실행은 다르다

[2026-10-05 인수 기록](validation/vad-native-acceptance-20261005.json)은 실제 mini/CAN/가중치,
연속 3프레임·6카메라, 프레임별 3모드·6점의 GPU 추론과 reset 두 실행의 동일 출력을 보존한다.
실제 모델 실행을 완료하지 못했다는 과거 판단은 갱신되어야 한다.
그러나 [`driving_policies.py:7`](../backend/src/ade2e/driving_policies.py#L7)은 서버 실행을
`not_configured`, 결과 출처를 self-reported, 판정을 `not_evaluated`로 유지한다.
화면은 JSON 검사·궤적 표시이고, PolicyRun 저장→queue 제출→재실행→A/B→계획 판정은 아직 없다.
입력/결과 해시 검사가 실행 출처의 독립 증명이 되는 것도 아니다.

이 데이터/모델 시험은 사용자가 승인한 **격리 비상업 연구**다. 코드 라이선스와 데이터·가중치의
상용 사용 권리는 각각 확인해야 한다. 짧은 open-loop 재현 결과를 주행 정확도·실시간 성능·폐루프 안전성으로 확대하지 않는다.

### 실제 CARLA 실행은 존재하지만 센서/차량 검증에는 공백이 크다

[CARLA live 인수](validation/carla-live-stack-2026-09-13.json)는 실제 LiDAR→공통 stack→명령/ACK→
다음 physics를 보여준다. 기하 corridor와 정지 surface 가정, simulator/map oracle을 사용한다.
학습형 카메라 E2E 폐루프와 다른 경로다. 저장된 실패 verdict는 유지한다.

현재 RGB는 CARLA postprocess이고 LiDAR는 ray-cast 방식이다. 장착값/속성/pose readback은
설정이 적용된 근거이며 실측 센서 정확도 검증은 아니다.
[LiDAR seed 진단](carla-lidar-seed-diagnosis.md)은 seed42/43/44의 동일 출력과 reseed 누락을 기록한다.
요청 seed를 저장한 것을 독립 무작위 시험 다양성으로 합산하면 잘못된 coverage가 된다.

[카메라 원리 문서](https://home.applied.co/manual/v1.75/docs/sensor_sim/sensor_types/camera/camera_principles)는
렌즈·CFA/센서·노이즈/셔터·노출/색 처리 항목을 기술한다. AXIOM에는 이 모델들을 실제
CameraE2E/ISP adapter와 지표까지 연결한 처리 경로가 아직 없다.

### Agent 도구 발견이 UI/SDK보다 뒤처져 있다

[`main.py:346`](../backend/src/ade2e/main.py#L346)의 `agent_tools()`를 AST로 확인했다.
정의된 경로에 `verification-plans`/`verification-attempts`/`driving-policies`가 없었다.
라우트 등록과 SDK는 있으므로 API 자체가 없는 문제는 아니지만 자동 발견이 불완전하다.
수동 catalogue와 OpenAPI 사이의 일치 검사를 통해 같은 문제가 반복되지 않게 하는 것이 필요하다.

### 보고 문서와 출시 근거도 정리해야 한다

`docs/status.md`의 상단은 2026-10-05 인수까지 갱신되었지만 하단과 일부 문서에는 오래된 상태가 남는다.
예를 들어 [`cases.md:88`](cases.md#L88)의 I3 미구현 문장은 현재 Case retest 소스/인수와 맞지 않는다.
아래 기록은 실행 당시의 범위로 읽어야 하며 최신 전체 상태로 합산하지 않는다.
오래된 “상용 25%/CARLA pilot 75%”는 고정된 기능 분모·가중치가 없는 판단으로 이번 감사의 진행률로 사용하지 않았다.

작업 트리에는 기존 미커밋/미추적 변경이 많다. 실행 근거가 있는 내부 source snapshot과
검토·설치·운영 기준을 통과한 상용 release는 구분해야 한다. 이 분석에서 기존 변경은 정리하지 않았다.

## 다음 개발 단위와 완료 기준

| 순서 | 구현할 업무 | 완료 시 사용자가 끝낼 수 있는 일 | 필수 인수 경계 |
|---|---|---|---|
| 0 · 작게 병행 | API/agent catalogue 일치, 최신 capability/status 정리 | Agent도 Plan/Policy API를 발견하고 실제 지원 범위를 확인 | OpenAPI와 도구 목록 일치, UI/API/SDK 지원 범위 회귀 검사 |
| 1 · 최우선 | 학습형 Policy 입력·가중치·환경 개정/PolicyRun DB→기존 queue/실행 adapter→원본 결과 | 화면에서 실제 모델 실행 제출·취소·실패 조사·결과 재조회 | 원격 GPU 실제 실행, 입력/환경/출력 provenance, 재시작 보존, 연구 권한 경계; 업로드 receipt와 실행 출처 구분 |
| 2 · 1에 연결 | 고정 입력의 정책/설정 A/B와 Plan의 open-loop 증거 유형 | 동일 로그에서 변경 영향과 미평가/입력 부족을 계획에서 확인 | 유효한 시간/좌표/목표 지표와 평가 분모; open-loop 근거로 충돌 회피 통과 금지 |
| 3 · 센서 핵심 | 지원 CameraE2E/RAW/ISP 변경 하나→고정 인지 모델→평가/Plan | 동일 장면에서 센서·ISP A/B가 성능에 미치는 영향을 조사 | 실제 RAW/처리 출력, 장면/대응 라벨·모델 고정, 센서 실험과 동일 입력 모델 비교를 다른 유형으로 정의 |
| 병행 강화 | 학습형 의미 검색과 사건 구간 단위 선별 | 문장/이미지로 관련 장면을 찾고 독립 사건을 Collection에 선별 | 사람 선정 관련 장면의 검색 품질을 현 색상 기준선과 비교; 인접 프레임을 독립 사건으로 합산 금지 |
| 공유 전 병행 | 승인 실데이터와 두 번째 사용자, 권한·프로젝트 경계·고정 실행 진입점 | 다른 엔지니어가 동일 업무를 반복하고 결과를 인계 | 실제 반복 업무, 역할별 접근/실행/검토, cold restore/clean install, 지원 용량·지연·복구 기준 |
| 후속 | 실제 학습형 정책 폐루프·범위 확장·분산 실행 | 제어→다음 차량/센서 상태→정책 입력의 회귀 시험 | 해당 엔진/센서/모델 조합 실제 인수, 실행 품질과 시험 대상 품질 분리 |

Neural reconstruction, HIL, 전체 map editor, 자체 정밀 차량동역학을 모두 먼저 만들어야
내부 모델·센서 평가를 사용할 수 있는 것은 아니다. 해당 기능은 adapter와 별도 인수로 확장한다.
현재 개발 목표는 메뉴 수를 늘리는 것이 아니라 **실제 모델/센서 변경을 검증 계획의 결정 근거로 만드는 것**이다.

## 근거 목록과 현재성

| 영역 | 확인한 소스/인수 기록 |
|---|---|
| 현재 목표·전체 상태 | [internal-platform.md](internal-platform.md), [status.md](status.md), [roadmap.md](roadmap.md) |
| 데이터·라벨·검색 | [Collections 인수](collections-acceptance-20260928.md), [라벨 인수](automatic-labels-acceptance-20260928.md), [장면 인수](scene-discovery-acceptance-20260928.md), `cases.py`, `collections.py`, `annotation_assist.py`, `scene_features.py` |
| 모델/시험의 Case 연결 | [모델 재평가](case-model-replays-acceptance-20260928.md), [참조 재시험](case-retests-acceptance-20260928.md), [기록 stack](case-stack-replays-acceptance-20260928.md) |
| Verification Plan | [독립 패키지 인수](verification-plans-acceptance-20261004.md), [기계 판독 근거](validation/verification-plans-20261004.json), `verification_schemas.py`, `verification_plans.py` |
| 실제 VAD | [driving-policies.md](driving-policies.md), [인수 JSON](validation/vad-native-acceptance-20261005.json), `integrations/vad/src/axiom_vad/native.py`, `driving_policies.py` |
| CARLA/센서/복구 | [연결](carla-connection.md), [live stack](carla-live-stack.md), [센서 설정](carla-configurable-sensors.md), [seed 진단](carla-lidar-seed-diagnosis.md), [복구](carla-recovery.md) |
| 운영/API | [local pilot](local-pilot.md), [API 계약](api-contract.md), [agent 안내](agent-guide.md), `main.py`, `local_pilot.py`, `workspace_backup.py` |

이번 감사에서 Plan 인수 JSON의 현재 소스 **12/12**, VAD implementation **4/4**,
VAD 원본/결과/반복성 등 artifact **16/16**의 SHA-256 일치를 다시 확인했다.
이는 저장된 실행 기록과 현재 파일의 연결 근거이며 이번에 테스트를 재실행했다는 의미는 아니다.
[감사 snapshot](validation/applied-intuition-audit-20261007.json)에 확인 날짜·출처·해시·비교 경계를 보존한다.

전체 진행률은 아직 고정한 전체 기능 목록·인수 분모·가중치가 없어 단일 숫자로 확정하지 않았다.
기능별로 보면 데이터/검출/검증의 연결이 가장 앞서 있고, 실제 정책·센서 실행과 팀 운영이 다음
제품 완성 단위이며, 정밀 엔진·재구성·HIL·대규모 운영은 큰 공백으로 남는다.
