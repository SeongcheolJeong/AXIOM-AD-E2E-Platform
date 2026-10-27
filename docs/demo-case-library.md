# 질문 중심 데모 사례 목록

2026-10-10 · 로컬 발표자 화면 공유. **실제 결과가 연결된 사례 10개**를 준비했다. 승인된 후속 작업에서 HIL 200ms A/B를 실제 실행했다.
사례 수는 독립적인 도로 장면 수나 검증 커버리지가 아니다. 10개 사례는 12개의 고유 실행을
참조하며, 하나의 실행 안에 정상/변경 A/B가 들어가는 경우도 있다.

## 사용자가 완료하는 흐름

`Workspace` → 프로젝트 선택 → 질문 중심 사례 선택 → 고정된 결과 A/B → 핵심 시점 조사 →
설정 편집 또는 원본·Case로 이동한다. 실행이 없는 질문도 목록에 남고 이유가 표시된다.
원본/변경 설정, 실행 ID와 관찰 메모를 함께 보여준다. 최신 실행으로 자동 교체하지 않는다.

현재 네 원본 작업공간은 서로 다른 DB와 권한을 유지한다. 같은 프로젝트 ID가 나타나는
Sensor/Neural도 별도 작업공간이다. 아래 review 주소는 새 UI와 읽기 전용 사례 목록을
기존 서비스에 연결하는 로컬 검토용 주소이며, 공개 서비스가 아니다. 각 origin의 Workspace
settings에서 해당 작업공간에 대한 인증 세션이 필요하다. 토큰은 발표 자료에 포함하지 않는다.

| 작업공간 | 원본 API/UI | 로컬 review | 프로젝트 | curation |
| --- | --- | --- | --- | --- |
| Object / Log | `http://127.0.0.1:8033` | `http://127.0.0.1:8053` | `project_bc937f94c7c94a7884993c074213a86c` | `object-curation.json` |
| Sensor | `http://127.0.0.1:8034` | `http://127.0.0.1:8054` | `project_c653516fae7447e0bd77f53af15c6964` | `sensor-curation.json` |
| HIL | `http://127.0.0.1:8036` | `http://127.0.0.1:8056` | `project_9f48a965d4194be0b18859e334e007bb` | `hil-curation.json` |
| Neural | `http://127.0.0.1:8040` | `http://127.0.0.1:8060` | `project_c653516fae7447e0bd77f53af15c6964` | `neural-curation.json` |

시작 URL은 각 origin의 `/#workspace?project=<project_id>`다. 발표용 바로가기는
`.runtime/demo-cases-20261010/presenter.html`에 있다. 이 HTML은 진행표이며 실제 근거 Viewer를
대체하지 않는다. 정상 브라우저에서 네 작업공간의 사례 10개를 목록에서 열고,
핵심 시점 이동·새로고침 복원·실제 결과 연결을 확인했다. JavaScript 오류는 없었다.

## 기존 서비스를 유지하는 검토 화면

검토용 UI는 `.runtime/demo-cases-20261010/ui`에 고정했고 파일 hash는 `ui-sha256.json`에 있다.
`review-servers.json`은 이번에 시작한 검토 서버만 기록한다. 원본 8033/8034/8036/8040은
재시작하지 않았다. 새 사례 조회는 기존 DB의 읽기 전용 연결을 사용하며, 다른 API 요청은
브라우저의 Bearer와 요청 키를 그대로 원본 서비스로 전달한다. 검토 서버는 worker·migration을
시작하지 않는다. 예를 들어 Object/Log 검토 서버를 다시 열 때는 사용 중이지 않은 포트를 선택한다.

```sh
.venv/bin/python scripts/demo_case_review.py \
  --workspace .runtime/object-demo-20261009/workspace \
  --upstream http://127.0.0.1:8033 \
  --operator-token-file .runtime/object-demo-20261009/access/api-token \
  --curation .runtime/demo-cases-20261010/object-curation.json \
  --ui .runtime/demo-cases-20261010/ui --port 8053
```

## 준비한 질문과 실제 관찰

| 사례 ID | 변경 / 소스 성격 | 고정 결과에서 관찰한 내용 |
| --- | --- | --- |
| `object_lead_wait` | 도심 우회전 · 앞차 대기 0/6초 | 자차 arrived 첫 관측 13.85/21.45초. B는 6.45초에 앞차 정지 반응, 8.95초에 재추종 |
| `log_lead_wait` | 같은 도심 원본의 Case · 대기 6→2초 | 저장된 Case에서 native 재실행. arrived 21.45→17.45초, 원본·변경 각각 RGB 720장 |
| `object_gap_release` | 절차적 도로 · 뒤차 속도 7→5m/s와 간격 8→6m | 우회 시작 6.35/7.45초, arrived는 둘 다 32.65초. 두 변수가 함께 바뀌어 단일 변수 인과 효과는 분리하지 않음 |
| `sensor_driving_fov` | CARLA 도심 주행 · FoV 90/70° | 실제 센서 readback 확인. A/B 각각 200시점×4센서, 누락 0, 이동 56.85m |
| `sensor_lidar_density` | CARLA 정지 장면 · 32,000/8,000points/s | 100시점의 실제 반환 점수 평균 1,634.41/408.41개. 설정 발사율과 반환 점수는 다름 |
| `sensor_radar_range` | CARLA 정지 장면 · 80/30m | 100시점의 실제 반환 점수 평균 71.76/13.01개. 다른 장면의 탐지 성능으로 일반화하지 않음 |
| `hil_delay_200` | Software DUT · 객체 입력 200ms 지연 | 3.05–7.05초 지연, 최대 age200ms. A/B 모두 tracking이지만 5.00초 속도는 0.86/1.60m/s. 각 조건 360제어·RGB, 누락0 |
| `hil_delay_400` | Software DUT · 객체 입력 400ms 지연 | 응답 시각 3.05초 지연 반영, 3.30초 degraded, 7.05초 recovering, 7.15초 tracking |
| `hil_dropout_1` | 같은 Software DUT 장면 · 1초 단절 | 응답 시각 3.55초 단절 반영, 3.80초 degraded, 4.55초 recovering, 4.65초 tracking |
| `neural_frozen_sweep` | CARLA 주행 소스의 Neural 재구성 | 원본 5.0초 고정, 좌우 ±1.5m의 실제 61시점 렌더. 카메라 경로 재생 0–6초는 원본 시간과 별도 |

`source_families`는 같은 작성 장면·주행 또는 캡처 설정 계열을 묶는 탐색 정보다. Object/Log의
조건 변형, Sensor/HIL의 반복 수집, Neural의 시점 증가는 독립적인 실도로 장면 확보를 뜻하지 않는다.
Neural에는 시뮬레이션 시간용 핵심 시점 링크를 넣지 않는다. 화면의 카메라 pose와 고정 원본 시각을 확인한다.

## 10분 발표와 질의응답

| 시간 | 화면 / 진행 | 말할 핵심 |
| --- | --- | --- |
| 0–1분 | Neural sweep · 큰 Viewer | “기록의 한 순간을 고정하고 카메라 위치를 바꿔 생성한 결과입니다.” 원본 5초와 경로 재생 시간을 구분 |
| 1–4분 | Object 앞차 대기 → Log Case → 6/2초 비교 | “정지 원인을 조사하고 조건을 바꾼 결과를 원본과 연결해 보존합니다.” Case와 재실행의 실제 ID를 확인 |
| 4–6분 | Sensor 주행 FoV A/B → 동기 LiDAR/Radar | “설정이 관측을 어떻게 바꾸는지 같은 상대 시각에서 확인합니다.” 센서 출력이 제어 입력인 것처럼 설명하지 않음 |
| 6–9분 | HIL 200ms → 400ms → 1초 단절 | 200ms의 tracking 유지와 달라진 차량 응답, 400ms의 degraded 전환, 단절 후 복귀를 입력 age·영상·제동 신호로 비교 |
| 9–10분 | Workspace 사례 목록 | 질문별 결과·관찰·다음 행동을 정리하고 청중 질문에 맞는 사례로 이동 |

질의응답 동선:

- “조건을 바꿔도 결과가 같을 수 있나?” → `object_gap_release`: 우회 시작은 달라도 최종 도착 시점이 같은 사례.
- “센서 설정과 실제 반환이 어떻게 다른가?” → `sensor_lidar_density` 또는 `sensor_radar_range`: 원본 반환 점수와 설정값을 구분.
- “작은 지연도 영향이 있나?” → HIL 200ms의 5.00초: tracking 상태는 같아도 자차 속도는 0.86/1.60m/s다. 상태 동일과 행동 동일을 구분한다.
- “고장 이후 복귀까지 보이나?” → HIL 단절 사례의 4.55/4.65초.
- “실제 저장된 문제에서 시험으로 이어지나?” → Log의 원본 조사·Case 링크와 보존된 6→2초 변형.
- “이게 실제 센서/차량 검증인가?” → CARLA GT 기반 제어, native 센서 모델, Neural 재구성, Software DUT 각각의 범위를 설명.

브라우저 리허설 기록은 `.runtime/demo-cases-20261010/browser/report.json`에 있다. 작업공간별
WebM 4개가 `browser/video/`에 있으며 진행표에서 바로 열 수 있다. 이는 저장 결과를 확인한
브라우저 리허설 백업이며, 편집된 발표 영상이나 새 native 실행 기록은 아니다.

본편은 고정 결과로 진행한다. 라이브 생성은 GPU와 엔진 상태를 먼저 확인한 뒤 기존 Neural
checkpoint의 제한된 시점 변경 1건만 사용한다. 30초 이상 걸리면 진행 상태를 유지하고 저장 결과로
이어간다. 새 결과와 저장 결과를 구분한다. 초기 9개는 보존 결과를 재사용했고 승인 후 HIL 200ms A/B 한 건을 새로 실행했다.

## API / SDK와 고정 결과 준비

`GET /api/v1/demo-cases?project_id=...`는 `demo-cases/1`을 반환한다. 목록 조회 자체는
엔진 접속 확인이나 실행을 하지 않는다. `items`의 `ready`, `status`, `reason`, `observation`,
`executions`, `primary_link`, `links`, `key_moments`를 사용한다. `ready`는 보존된 실행·설정과
파일의 확인 범위이며, 엔진의 현재 실행 가능 상태나 전체 UI 검증 통과를 뜻하지 않는다.

SDK는 같은 API를 호출한다. 저장소 루트에서 실행한다. 아래 예제는 토큰 값을 출력하지 않는다.

```sh
PYTHONPATH=sdk/python/src .venv/bin/python -B - <<'PY'
from pathlib import Path
from axiom_sdk import AxiomClient

token = Path('.runtime/object-demo-20261009/access/presenter-token').read_text().strip()
with AxiomClient('http://127.0.0.1:8053', token=token) as client:
    cases = client.demo_cases(project_id='project_bc937f94c7c94a7884993c074213a86c')
    for case in cases['items']:
        print(case['id'], case['status'], case['primary_link'])
PY
```

명시적 curation은 `AXIOM_DEMO_CASES_MANIFEST`가 가리키는 `demo-case-curation/1` JSON이다.
프로젝트와 실행의 `id`, `input_hash`, `manifest_hash`를 고정하고 비교에서는 `left`/`right`,
단일 A/B 또는 sweep에서는 `result`를 사용한다. 현재 파일은
`.runtime/demo-cases-20261010/{object,sensor,hil,neural}-curation.json`에 있다.
각 작업공간에는 자기 curation만 연결한다. 실행·DB·토큰을 다른 작업공간으로 복사하지 않는다.

기존 네 API에서 snapshot과 curation을 다시 준비하는 명령:

```sh
.venv/bin/python -B scripts/prepare_demo_cases.py \
  --output .runtime/demo-cases-20261010/evidence.json
```

이 도구는 일반 API를 읽고 로컬 receipt를 저장한다. 실행 제출·preset 재바인딩·서비스 재시작을 하지 않는다.
`snapshots/`에는 원본 export와 핵심 Object/HIL/Sensor 자료를 보존하며 내려받을 때 SHA256을 확인한다.
`checks.json`은 이번 준비의 프로젝트 경계·고정 hash 검사 기록이다. API 결과의 관찰 메모는
해당 pin에 대한 큐레이터 해석이며 자동 안전 판정이 아니다.

## 남은 범위와 확인 상태

- HIL 200ms 실행 `job_3f30f238d95948eda6327fde25c6cc5d`를 정상 preview→Experiment→실행 API로 완료했다.
  실제 정상/고장 2캡처, 총720 RGB, 누락0, cleanup confirmed다. 참조 DUT의 250ms 기준 아래에서
  tracking을 유지하지만 제동과 차량 응답은 달랐다. 실제 ECU·하드 실시간 판정은 아니다.
- GPU 자원 전환을 명시적으로 승인받아 CARLA2000을 잠시 정지하고2100에서 실행한 뒤,
  2000을 원래480×270·Town10HD_Opt·async·차량/센서/보행자0 상태로 복원했다.2100은 종료했다.
  원본 API4개와 worker는 유지했고 활성 작업0을 확인했다. 실행·hash·원복 근거는
  `.runtime/demo-cases-20261010/hil-200-native/completion.json`에 있다.
- 초기 backend/SDK/검토 서버32개·frontend22개 검사에 이어200/400ms 계약2개를 추가 확인했다.
  10개 사례 모두 정상 브라우저 동선을 통과했고, 후속 HIL3개에서는 정확한 실행 ID·지연값·시점·
  재생·새로고침·목록 복귀를 다시 확인했다. 전체10분 연속 라이브 발표 리허설은 후속이다.
- 검토 화면은 지속 실행 프로세스로 다시 기동했다. 원본8033/8034/8036/8040을 재시작하거나 DB를 합치지 않았다.
- 기존 CARLA 코드·엔진·지도/차량 asset의 출처와 사용 범위를 유지한다. nuScenes·실도로 결과는 이 외부 발표 묶음에 포함하지 않는다.
- 정밀 센서 물리, 실측 상관성, 실제 ECU HIL, 하드 실시간, 대규모 일반화와 공개 서비스 운영은 이번 완료 범위가 아니다.

상세 모듈 동선은 [Object](object-sim-demo.md), [Log](log-sim-demo.md),
[Sensor](sensor-sim-demo.md), [HIL](hil-sim-demo.md), [Neural](neural-sim-demo.md)을 따른다.
