# Neural Sim · 발표자 화면 공유 데모

## 완료 연결 · 실도로 장면과 실제 모델 출력 비교

2026-10-10 사용자 후속 지시로 **실도로 로그 한 개 → 실제 재구성 → 제한된 새 시점 →
동일 인지 모델 출력 비교 → Case 저장**을 일반 UI/API/SDK에서 완료했다. 기존 CARLA 데모는 유지한다.
원본·동일 시점 재구성·변경 시점에 같은 모델/전처리/임계값을 사용하고, 3–5개 선택 시점의
검출을 비교한다. 이 결과는 모델 출력 민감도 조사이며 정답 기반 정확도나 안전 판정이 아니다.
재구성 손상과 시점 변경의 영향을 구분할 수 있도록 세 입력을 함께 제공한다.

확보된 nuScenes 데이터의 기존 사용 범위는 로컬 비상업 연구다. 데이터의 외부 상업 공개나
재배포 권한을 추가로 확보한 것으로 표시하지 않는다. 모델 가중치·코드·데이터의 출처와
이용 범위를 각각 보존한다. 동적 객체 편집·제동 제어·센서 폐루프는 이번 연결의 범위 밖이다.
아래 CARLA 인수 기록은 이전 완료 범위이며 실도로 연결의 완료 근거로 대신하지 않는다.

실제 입력은 nuScenes mini `scene-0655`의 CAM_FRONT96프레임·8초다. 원본 JPEG·기록 시각·
카메라 보정과 pose·LiDAR 출처를 고정하고,640×360으로 재구성했다. Splatfacto10,000단계
학습은 native271.755초, 원본frame48·4.050016초를 고정한 ±0.5 m 카메라 위치61개 렌더는
14.767초에 완료했다. 모든 선택 원본을 학습에 사용했으며 별도 held-out 품질 인수는 하지 않았다.

정상 UI에서 OpenCV Zoo YOLOX-S FP32의 고정 개정으로 렌더frame0·15·30·45·60의
원본/동일 pose 재구성/새 시점15입력을 실제 ONNX CPU 추론했다. 원본과 재구성은 각각12개,
새 시점 검출은11·10·12·10·9개였다. 중앙 동일 pose는 PNG·입력 tensor·검출 결과까지 동일했다.
검출 개수와 score는 모델 출력이며, 이 차이를 정답 기반 검출 누락이나 성능 악화로 해석하지 않는다.
현재 영상에는 전경 차선 늘어짐과 움직이는 차량·수목의 흐림이 남는다.

| 완료 단계 | 고정 실행 또는 Case |
| --- | --- |
| 원본 가져오기 | `job_4b604b4631794b629e660da9ed48cb78` |
| 실제 장면 학습 | `job_ab0fe592f190448ba718bf43cd7351f5` |
| 실제 카메라 이동 렌더 | `job_78b680974803487f86e8a93aa282b6cb` |
| 실제 ONNX 모델 비교 | `job_14822c3e000d40f889730093fa82fb23` |
| +0.5 m 출력 조사 Case | `case_9e1fd4350bf747ab87cc335ebb70399e` r1 |

Case 저장·열기, Case에서 정확한 렌더/모델 비교로 돌아오기, 리로드 복원, 발표 전체 화면과
실제 카메라 경로 재생을 확인했다. 중간 미추론 시점에서는 이전 박스가 사라지고 모델 미실행
표시와 Case 저장 비활성화가 나타나며, frame60으로 돌아오면9개 새 시점 박스가 복원된다.
Backend·SDK72개, adapter47개, frontend14개 집중 검사와 production build를 각각 통과했다.
스크린샷·실행 hash·Case 출처는 [실도로 인수 기록](validation/neural-sim-real-road-20261010.json)에 보존했다.

실도로 데모 주소:
<http://127.0.0.1:8040/#neural-sim?project=project_737605966a6a42aa8c0287c604ac8c38&preset=sweep&result=job_78b680974803487f86e8a93aa282b6cb&perception=job_14822c3e000d40f889730093fa82fb23>.
실도로 sweep은 원본별 기본값을 사용하며 이번96프레임에서는 frame48·반경0.5 m다.
실제 도로 재구성은 `neural_reconstruction_of_real_road`, 모델 비교는
`neural_model_output_sensitivity` fidelity로 구분한다.

동일 비교는 SDK의 `preview_neural_perception(render_id, model_id=..., model_revision=...,
project_id=..., frame_indices=[0,15,30,45,60])`와 `execute_neural_sim_preview`로 요청한다.
완료 후 `neural_perception_result`로 세 입력의 실제 검출·모델/렌더 개정을 읽고,
`capture_neural_perception_case`로 선택한 렌더 프레임을 원본 recording 시각에 연결한다.
원본 recording을 가져와 프로젝트에 연결해야 Case 저장이 가능하며, 생성 영상에 원본 정답을
복사하거나 기존 객체 참조 재시험을 Neural 회귀시험으로 표시하지 않는다.

준비 CLI는 `scripts/neural_demo.py import-source --source-id …`와
`perception --render-execution-id … --model-id … --model-revision 1`을 제공한다.
기존처럼 API URL·토큰 파일·프로젝트·receipt 파일을 명시하고, 같은 receipt로 실행 상태를
다시 확인한다. 소스 준비는 운영자가 구성한 데이터만 사용하며 임의 서버 경로를 요청으로
전달하지 않는다. `render-presets --include-sweep`는 기존 CARLA의 반경1.5 m·frame50을
유지하므로, 이번 실도로 준비는 UI의 원본별 기본값 또는 명시적인
`render --mode camera_sweep --lateral-m .5 --height-m 0 --frame-index 48`을 사용한다.

## 이전 CARLA 발표 데모 완료 범위

결정일: 2026-10-10. 첫 목표는 **기존 CARLA 주행 로그 → Neural 3D 장면 학습 →
카메라 위치 변경 → 원본·재구성·새 시점 비교**를 일반 UI/API/SDK에서 3–5분 동안
시연하는 것이다. 상세 상관성 연구보다 실제 기능 연결과 영상 중심 발표를 우선한다.

2026-10-10 인수: 실제 GPU 학습 1회, 기본 시점 렌더 3회와 정상 브라우저에서 요청한
단일 프레임 렌더 1회를 완료했다. 프로젝트 선택·학습·장면 연결·프리셋·편집·생성·비교·
재생·전체 화면을 제공한다. 로컬 발표자 화면 공유용으로 준비했으며 외부 공개 서버를
배포한 상태는 아니다. [실행·브라우저 근거](validation/neural-sim-demo-20261010.json).

데모 주소: <http://127.0.0.1:8040/#neural-sim?project=project_c653516fae7447e0bd77f53af15c6964>.
새 브라우저의 Workspace settings에는 `.runtime/neural-demo-20261010/access/api-token`의
로컬 토큰을 입력한다. 토큰을 발표 화면·URL·공유 문서에 넣지 않는다.

## 고정 범위와 발표 흐름

### 시간 고정 · 카메라 이동 시연

원본과 기준 재구성이 비슷한 것은 복원 목적에 맞지만, 그것만으로는 새 시점 생성의
가치가 잘 보이지 않는다. 발표의 첫 장면은 **주행 로그의 5.00초를 고정하고 카메라만
왼쪽 1.5 m에서 오른쪽 1.5 m까지 이동**하는 `sweep` 프리셋으로 바꾼다.
가까운 나무·기둥·교차로 노면 표시와 먼 건물의 상대적인 이동을 큰 영상에서 보여준다.
원본은 작은 고정 영상으로 함께 두고, 원본/재구성 품질 비교는 필요할 때 연다.

`camera_sweep`는 같은 checkpoint에서 61개 카메라 pose를 실제 렌더한다.
출력 시간은 0–6초·10 Hz이며, 원본 시간은 5.00초·frame50에 고정된다.
재생은 좌우 왕복, 슬라이더는 렌더된 카메라 위치를 선택한다. 화면의 카메라 위치는
요청 중인 값이 아니라 현재 표시한 프레임의 pose를 나타낸다.
미리 렌더한 시점 사이를 탐색하는 기능이며 자유 시점의 즉시 GPU 렌더를 주장하지 않는다.

입력의 `lateral_m`는 sweep에서 양수 진폭(0 초과·2 m 이하), `height_m`는 고정 높이
(±0.3 m), `frame_index`는 정지할 원본 프레임이다. 기존 frame/sequence 모드의
좌우 ±0.5 m 제한과 저장 결과는 유지한다. 대표 sweep의 ±1.5 m는 관찰 가능한 시연
설정이며 정확도가 검증된 공간 범위가 아니다. 현재 모델은 미관측 경계의 흐림·깨짐이
남고, ±2 m에서는 가장자리 결함이 더 드러났다.

Sweep의 결과/요약은 `neural-render-result/2`·`neural-render-summary/2`를 사용한다.
각 출력 프레임의 `frame_index`·`time_s`는 렌더 순서와 재생 시간이고,
`source_frame_index`·`source_time_s`·`source_frame`는 고정 원본을 가리킨다.
`applied_lateral_m`·`applied_height_m`와 기준/변경 camera-to-world pose를 보존한다.
기존 주행/단일 프레임 결과는 `/1` 계약으로 계속 읽는다.

입력은 완료된 `carla-driving-multisensor` A/B 실행의 **rig A·전방 RGB·20초·200프레임**이다.
카메라 내부 보정, 프레임별 pose, 원본 파일과 실행 receipt의 SHA-256을 보존한다.
CARLA 장면의 정적 재구성에 Splatfacto를 사용하며 모든 Torch/CUDA 코드는 optional
`integrations/neural` 안에 둔다. 원본 카메라의 고정 영상에 crop만 적용해 새 시점으로 표시하지 않는다.

| 프리셋 | 기본 변경 | 편집 범위 |
| --- | --- | --- |
| 시간 고정 · 카메라 이동 (`sweep`) | frame50 고정·좌우 −1.5 → +1.5 m | 진폭 0 초과·2 m 이하, 높이 ±0.3 m |
| 왼쪽 시점 (`left`) | 좌우 −0.3 m, 높이 0 m | 좌우 ±0.5 m |
| 오른쪽 시점 (`right`) | 좌우 +0.3 m, 높이 0 m | 좌우 ±0.5 m |
| 높은 시점 (`raised`) | 좌우 0 m, 높이 +0.2 m | 높이 ±0.3 m |

조작 범위는 데모 입력 제한이다. 새 시점의 정확도가 검증된 공간 범위라는 뜻이 아니다.
프리셋마다 원본 실행, 완료된 학습 실행, 대표 렌더 실행을 명시적으로 연결한다.
저장 결과는 해당 프리셋의 기본값과 일치해야 한다. 기존 세 프리셋은 전체 구간·frame index 0,
sweep은 camera_sweep·frame index 50이다.
임의 변경 결과와 단일 프레임 결과는 별도로 열며 대표 결과를 자동 교체하지 않는다.

1. **시간 고정 · 카메라 이동** 대표 장면을 열고 왕복 재생하거나 카메라 위치를 드래그한다.
2. 원본 영상은 정지한 채 가까운 물체와 먼 건물의 상대 위치가 바뀌는 것을 보여준다.
   이어 왼쪽·오른쪽·높은 시점의 주행 영상을 열거나 **원본 ↔ 기준 시점 재구성**을 비교한다.
3. 위치를 직접 수정하고 현재 프레임 미리보기 또는 전체 구간 생성을 제출한다.
4. 진행 중에는 저장 결과를 재생한다. 완료된 새 결과를 명시적으로 열어 실제 적용된 설정을 확인한다.
5. 공통 시간축·배속·비교 슬라이더·전체 화면으로 발표한다.

화면의 필수 표기는 **“CARLA 합성 로그의 Neural 재구성”**이다. 원본, 재구성, 새 시점을
분명하게 표시하고 편집 중 설정과 현재 표시 결과의 설정을 분리한다. 결과는
`neural_reconstruction_of_carla` fidelity와 `not_evaluated` 평가 상태를 보존한다.

## 구현·오픈소스·후속 범위

| 구분 | 이번 범위 |
| --- | --- |
| 기존 제품 재사용 | Experiment preview·고정 저장·큐·취소·receipt·artifact, 프로젝트 권한, Agent OpenAPI, Python SDK |
| 오픈소스 도입 | Nerfstudio Splatfacto의 정적 3D Gaussian 학습·checkpoint·새 카메라 pose 렌더 |
| 자체 구현 | CARLA/고정 실도로 원본·pose/보정 변환, checkpoint identity, GPU 실행 adapter, 프리셋 binding, 영상 비교·실패 표시, 실제 ONNX 출력 비교·Case 연결 |
| 후속 기능 | 여러 실도로 로그로 입력·품질 일반화, 동적 객체 편집, 다중 카메라, E2E 센서 폐루프, 재조명·날씨 |
| 후속 검증 | 실측 센서 상관성, 물리 RAW·광학·LiDAR/Radar 정확도, 큰 시점 변형 품질, 대규모 회귀시험·상용 release |

Splatfacto 코드, CUDA rasterizer, 모델·데이터·asset의 사용권은 별도로 관리한다.
해당 엔진을 설치한 것만으로 새 시점 품질 또는 센서 물리 정확도가 확보되지는 않는다.
학습/렌더 작업은 구성된 공유 GPU resource lease를 사용하고, 실패·취소·원격 실행 결과
불명확 상태를 정상 완료로 바꾸지 않는다.

## 일반 API·SDK

API 경로는 모두 `/api/v1` 아래에 있다. Viewer는 목록·결과를 읽고 Engineer는
binding·preview·Experiment 제출을 수행한다. Agent 도구는 동일 OpenAPI에서 생성된다.

- `GET /neural-sim/sources?project_id=...`: 완료된 CARLA/실도로 원본·학습·렌더·모델 비교 실행 목록.
- `GET /neural-sim/presets?project_id=...`: 네 프리셋과 원본별 기본값·정확한 source/checkpoint/대표 결과 연결.
- `POST /neural-sim/presets/{id}/bindings`: `expected_revision`을 사용한 불변 binding 개정.
- `POST /neural-sim/presets/{id}/preview`: 위치·frame/sequence 설정을 일반 Experiment draft로 변환.
- `POST /experiments/preview`, `POST /experiments`, `POST /experiments/{id}/executions`: 학습과 렌더의 공통 실행 경로.
- `POST /neural-sim/perception/preview`: 고정 렌더·등록 모델 개정·3–5개 시점을 일반 Experiment draft로 변환.
- `GET /neural-sim/perception/{execution_id}?project_id=...`: 실제 검출·이미지·모델·원본 출처를 조회.
- `POST /neural-sim/perception/{execution_id}/case`: 정확한 원본 기록 시각과 선택한 출력의 Case를 저장.

실도로 원본은 `neural_source_import`와 `{source_id}`로 운영자 등록 패키지를 가져온다.
모델 비교는 `neural_perception`, adapter `neural-onnx-sensitivity/1.0.0`을 사용한다.
두 작업 모두 기존 Experiment 큐·receipt·프로젝트 권한을 거친다.

재구성 Experiment는 `kind: neural_reconstruction`, `adapter_id: neural-splatfacto`,
`adapter_version: 1.0.0`, `comparison_kind: single`, 입력은
`{source_execution_id, rig: "rig_a", camera: "front"}`다. 렌더 Experiment는
`kind: neural_render`, 입력은 `{reconstruction_execution_id, lateral_m, height_m,
mode: "frame" | "sequence" | "camera_sweep", frame_index: 0..199}`다. 학습과 렌더 모두 seed를 고정한다.

```python
from axiom_sdk import AxiomClient

# API 주소·인증은 사용하는 로컬 workspace profile에서 설정한다.
with AxiomClient(base_url="http://127.0.0.1:8040", token=token) as client:
    sources = client.neural_sim_sources(project_id=project_id)
    training = dict(project_id=project_id, name="Neural Sim · CARLA scene",
        kind="neural_reconstruction", adapter_id="neural-splatfacto", adapter_version="1.0.0",
        input=dict(source_execution_id=source_id, rig="rig_a", camera="front"), seed=42)
    preview = client.preview_experiment(**training)
    saved = client.create_experiment(**training, idempotency_key="neural-train-001")
    assert saved["preview_hash"] == preview["preview_hash"]
    run = client.submit_experiment(saved["id"], preview_hash=preview["preview_hash"],
        idempotency_key="neural-train-001")

    # 완료한 학습 실행 ID를 확인한 뒤 명시적으로 연결한다.
    binding = client.bind_neural_sim_preset("left", project_id=project_id,
        source_execution_id=source_id, reconstruction_execution_id=reconstruction_id,
        expected_revision=0)
    view = client.preview_neural_sim_preset("left", project_id=project_id,
        expected_binding_revision=binding["revision"], parameters={"lateral_m": -0.2},
        mode="frame", frame_index=80)
    render = client.execute_neural_sim_preview(view, idempotency_key="neural-frame-001")

    # 같은 장면에서 시간을 멈추고 실제 카메라 위치 61개를 렌더한다.
    sweep_binding = client.bind_neural_sim_preset("sweep", project_id=project_id,
        source_execution_id=source_id, reconstruction_execution_id=reconstruction_id,
        expected_revision=0)
    sweep = client.preview_neural_sim_preset("sweep", project_id=project_id,
        expected_binding_revision=sweep_binding["revision"],
        parameters={"lateral_m": 1.5, "height_m": 0}, mode="camera_sweep", frame_index=50)
    sweep_run = client.execute_neural_sim_preview(sweep, idempotency_key="neural-sweep-001")
```

학습 checkpoint는 `neural-checkpoint.ckpt`, 학습 요약은
`neural-reconstruction-result.json`으로 보존한다. 렌더는
`neural-render-result.json`과 `neural-render-frames.zip`을 생성한다.
원본/기준/변경 PNG의 개별 hash·크기·시각과 ZIP hash를 확인하고 공통 timeline으로 표시한다.

반복 준비용 `scripts/neural_demo.py`는 `train`, `render`, `render-presets`, `bind`, `status`를
제공한다. `--url`, `--token-file`, `--project-id`를 명시하고 제출/binding에는 `--output`으로
재개 가능한 receipt를 고정한다. 같은 인자로 같은 receipt를 재사용하면 기존 job을 확인하며
실패한 작업을 자동 재실행하지 않는다. `render-presets`는 명시한 완료 학습에서 세 기본
시퀀스를 요청하고 실제 완료된 결과만 대표 프리셋에 연결한다.
[명령 예시·GPU 환경 계약](../integrations/neural/README.md)을 따른다.

## 실행 준비와 최소 인수

기존 Sensor Sim workspace의 API/worker를 중단하지 않고 선택한 완료 실행만 보존한다.
새 Neural workspace에는 그 실행의 원래 ID·sealed receipt·published artifact를 유지하며
대기 작업·원격 소유권·프로젝트 토큰을 복사하지 않는다. 실행 환경 변수는
`AXIOM_NEURAL_` 접두사로 구성하고 `internal_platform.py --enable-configured-engines`에서
optional package 경로와 함께 전달한다. 런처는 자동으로 기존 대기 작업을 재개하지 않는다.

완료 조건은 다음 네 가지다.

1. 원본 시간·좌표·보정·내용 hash를 확인하고 실제 GPU 학습 1회와 checkpoint를 보존한다.
2. 같은 checkpoint에서 기준 시점과 실제 변경 pose의 렌더를 생성한다.
3. 시작·중간·끝 영상에서 장면 식별과 심한 깨짐을 확인한다.
4. 정상 브라우저에서 편집·preview·제출·진행/실패·새 결과·재생·전체 화면을 확인한다.

backend/SDK와 adapter의 authored fixture 검사는 실제 학습·실제 렌더 확인과 별도로 기록한다.
GPU 환경 문제가 있으면 구현·준비 상태로 남기며 외부 시연 완료로 표시하지 않는다.

## 이번 실행 결과와 운영 상태

시간 고정 카메라 이동은 정상 UI에서 `job_fb3aabe95d254b6db65603ac4de50ca9`로 생성하고
`sweep` 대표 영상으로 저장했다. frame50·반경1.5 m·높이0에서 61개 실제 시점을 렌더했으며
native 시간은19.385초였다. 원본 시간5.00초와 원본 PNG hash는 전 구간 동일하고,
61개 새 시점 PNG hash는 서로 다르다. 기존 checkpoint를 재사용했으며 재학습은 하지 않았다.
[확장 실행·브라우저 근거](validation/neural-sim-sweep-20261010.json).

Nerfstudio 1.1.5 / gsplat 1.4.0 / RTX 2070 SUPER에서 200프레임을 10,000단계 학습했다.
원본 LiDAR의 RGB 투영으로 초기화한 55,091개 점을 사용했다. 학습 native 시간은 약390초,
세 기본 구간 렌더는 각각 약41초였다. 이 시간은 실행 기록의 해당 GPU 관측값이며 보장 SLA가 아니다.
Checkpoint SHA-256은 `023d58e2909c6e6562e039421a9564fb2c1feb56f1f6959a9d80e2f6d8c7e6e8`이다.

| 실행 | ID | 결과 |
| --- | --- | --- |
| 장면 학습 | `job_9f91dd1e944e4cd28b48db7c8ceb0d05` | 완료·checkpoint 보존 |
| 왼쪽 −0.3 m | `job_482cf37e09bc48f9b8e8ccce9a33de41` | 200프레임·대표 저장 |
| 오른쪽 +0.3 m | `job_03f8e57c2ae74b698d97fbd6721c0a82` | 200프레임·대표 저장 |
| 높이 +0.2 m | `job_ee60d9ab82f04d64a3f94bd4279e458c` | 200프레임·대표 저장 |
| UI 편집·frame50 | `job_e7ee95e7b028474c98b9eb320167a4d4` | 좌우+0.15 m·높이+0.10 m·완료 |

정상 브라우저에서 원본·재구성·새 시점의 시작/중간/끝과 1920×1080 발표 화면을 확인했다.
초기 프레임 로딩은 약1초, 1× 재생 관측은18프레임/1.909초였다(클릭 시간 포함).
새 프레임 생성 중 기존 영상과 표시 설정을 유지하고, 완료 후 새 결과를 열어 설정과 frame50을 확인했다.
backend·SDK·어댑터 집중 검사58개, frontend 계약6개 및 build를 통과했다.
Mocked browser 검사와 실제 native/browser 근거는 별도 파일로 보존했다.

앞선 CARLA 인수 때 기존 CARLA를 중지해 학습 GPU를 확보한 뒤 **2000번을 원래 명령으로
복구**하고 **2100번을 중지**했다. 2000번 복구 후에도 위 UI
프레임 렌더를 실제 완료했다. 두 CARLA를 함께 켰을 때의 기존 여유619 MiB로는 현재 Neural
렌더 수요를 충족하지 못한다. 원격 복구 helper와 원래 명령 기록은
`.runtime/neural-demo-20261010/gpu-runtime-state.json`을 참조한다.
이 운영 기록은 이전 CARLA 인수 시점의 상태다. **새 장면 재학습 전에는 2000번도
잠시 중지해 GPU를 확보해야 한다.** 이번 학습은 CARLA가 모두 중지된 상태에서 완료했으며,
CARLA와 학습의 동시 실행은 인수하지 않았다.
실도로 학습의 별도 GPU 준비 기록은 `.runtime/neural-real-road-20261010/gpu-runtime-state.json`과
`carla-stop-state.json`에 보존했다.

시스템 드라이버나 패키지를 변경하거나 호스트를 재부팅하지 않았다. 현재 로드된 NVIDIA595.84
커널과 맞춘 별도 userspace를 이 작업의 실행 프로세스에만 적용했다. 호스트 재부팅·드라이버
재로딩 후에는 런처 환경을 다시 확인해야 한다. 원격 학습 디렉터리와 task-owned CUDA runtime은
새 렌더에 필요하므로 시연 중 삭제하지 않는다.
