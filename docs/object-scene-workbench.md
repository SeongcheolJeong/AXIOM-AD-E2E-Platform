# Object Sim · 지도와 전체 native 장면 조사

2026-10-08. 정상 **Object Sim**에서 시나리오 작성 → 불변 개정 저장 → 실행 범위 확인 →
선택한 native 시험 제출 → 지도 위 전체 actor 재생·조사를 완료한다.
기존 결과와 새 결과는 동일한 프로젝트 API에서 읽는다.

## 사용 순서

1. 시작 화면의 **시나리오 작성·실행**을 연다. `Traffic interaction`은 차량 4대와 보행자 1명이다.
2. 실제 지도 길이·차선 폭, actor 종류·역할·크기·자세·속도, 행동·trigger·시간 지정 경로를 편집한다.
   차로 snap과 그래픽 위치/경로 점 배치를 사용할 수 있다. 변경·저장만으로 실행되지는 않는다.
3. 저장 후 **1. 저장 개정의 실행 범위 확인 → 2. 선택한 시험 준비 → 3. 선택한 native 시험 제출**을 누른다.
   선택한 구성·seed 한 건만 제출하며 전체 sweep을 자동 제출하지 않는다.
4. 저장 결과에서 2D/3D, 실제 시간축 재생·속도·표본 이동, actor 선택·숨김·추적·근접 보기,
   궤적/지도 맞춤·zoom·fullscreen·겹침/차선/정지/엔진 이벤트 이동을 사용한다.
5. inspector에서 저장된 위치·heading·속도·bbox offset·차선·가속도를 확인한다.
   계산한 box 중심과 원래 actor 원점은 별도 값이다. 라벨은 몸체를 가리지 않도록 기본 꺼져 있다.

지도 작성과 재생은 같은 길이·차선 폭·world 축을 사용한다. 2D 왼쪽 드래그는 pan,
3D 왼쪽 드래그는 orbit이며 오른쪽 드래그는 pan이다. 숫자/그래픽 경로 편집은 같은 초안에 반영된다.
처음부터 같은 차로로 변경하도록 작성한 경우 경고를 보여주며 실제 이동했다고 간주하지 않는다.

## 원본과 API 계약

`esmini-object/0.2.0+esmini3.8.2`의 새 실행은 기존 6개 원본에 `object-scene.json`을 추가한다.
API 경로·프로젝트 권한·DB schema18은 유지한다.

| 원본 | 보존하는 내용 |
| --- | --- |
| 봉인된 execution export | 정확한 spec·handler·입력·scenario 개정·configuration·seed·결과와 artifact SHA |
| `scenario.xosc`, `road.xodr` | 실제 실행에 입력한 OpenSCENARIO·OpenDRIVE |
| `native-trace.csv`, `engine.log`, `native-response.json` | 원래 엔진 출력·build·binary/runner/source SHA |
| `object-trace.json` | 전체 native frame과 원래 clock·actor 상태 |
| `object-scene.json` | `object-scene/1`: source·검증한 지도 geometry·authored actor intent·전체 frame·출처가 있는 이벤트 |

UI는 봉인 export와 지도·전체 trace·scene·log의 SHA를 확인한다. 원본 지도/actor/source/clock가
맞지 않거나 지원하지 않는 지도면 재생을 거절한다. summary의 축약 preview를 전체 표본처럼 재생하지 않는다.
새 preview도 첫·마지막 표본을 포함한다. 중간 프레임을 만들어 엔진 관측처럼 표시하지 않는다.

차량 box 중심은 `x + bb_x*cos(heading) - bb_y*sin(heading)`,
`y + bb_x*sin(heading) + bb_y*cos(heading)`이다. 길이·폭은 native CSV,
높이는 authored scenario 출처로 구분한다. 원본 CSV에는 native 높이·수직 box offset이 없다.

관측 이벤트는 다음처럼 분리한다.

- `native_trace`: world XY footprint OBB의 표본 겹침 구간, 저장된 lane ID 변화, 정지 속도 임계값 진입.
  표본 사이의 정확한 접촉 시각이나 행동 완료를 보장하지 않는다.
- `native_log`: 엔진 collision marker와 행동 event의 running/complete 상태. 원래 행 번호·SHA를 보존한다.
  행동 상태가 complete여도 실제 차로 이동 성공으로 대체하지 않는다.
- `authored_intent`: 작성한 초기 상태·route·action. 관측 결과와 별도다.

기존 0.1 실행은 원래 봉인 입력·`road.xodr`·전체 trace에서 화면 geometry를 검증하여 파생한다.
이를 **Legacy scene**으로 표시하고 기존 DB·artifact·영수증을 다시 작성하지 않는다.

정상 API는 기존 `/api/v1/experiments/executions/{id}`와 `/export`, `/artifacts/{artifact_id}`다.
SDK도 같은 managed route를 사용하며 내려받은 파일의 SHA·크기·봉인 identity·지원 scene 계약을 확인한다.
잘못된 결과는 기존 destination을 보존한다.

```python
import os
from axiom_sdk import AxiomClient

with AxiomClient(os.environ["AXIOM_API_URL"], token=os.environ["AXIOM_API_TOKEN"]) as client:
    job = client.experiment_execution("job_6de642529f034a52a44a83d7b57c8dca")
    client.download_object_scene(job["id"], "evidence/object-scene.json")
    client.export_experiment_execution(job["id"], "evidence/sealed-execution.json")
```

SDK의 API token과 외부 브라우저의 접속 비밀번호는 서로 다른 인증 수단이다.
scene artifact가 없는 과거 실행은 기존 `download_experiment_artifact`를 사용한다.

## 실제 인수와 남은 범위

정상 Engineer UI에서 저장한 `scenario2_bc6ac69fc7a8478a906eccc4ddbf6ccc` r1을 실제 esmini
v3.8.2/build6400 CPU 엔진으로 한 번 실행했다. `job_6de642529f034a52a44a83d7b57c8dca`는
0–10.1초의 원본 102표본·5 actor·15 이벤트를 보존한다.
차량의 lane−2→−1 변화는 2.2초 표본, lead 정지는 5.6초 표본에서 관측됐다.
보행자는 지정 경로로 y−7→1을 횡단했으며 경로 종료 뒤 엔진이 x 방향 이동을 계속한 상태도 그대로 보존한다.

**이 예제에는 lane_changer–pedestrian, lane_changer–lead 접촉이 있다.**
고정 observer가 ego 대상으로 판정한 `passed`는 전체 actor 무충돌이나 주행 안전 통과가 아니다.
별도 XY 겹침 구간 2개와 원래 엔진 시작/해제 로그를 보여준다. authored 행동은 접촉에 반응하지 않는다.
과거 job725 r2의 62표본·원본 봉인은 보존했고 차로 이동이 없는 실제 결과도 그대로 재생한다.

지원 지도는 authored 직선 1개·오른쪽 차로 2개, actor 최대 8개,
차량 speed/stop/lane-change 및 명시적인 timed path다. 곡선·교차로·외부 지도·반응형 traffic·
학습형 정책·센서 물리·실측 상관성·Applied 전체 동등 수준은 이번 인수 범위가 아니다.
3D는 원래 native 위치와 authored 높이를 쓰는 절차적 도식이며 사진식 센서 영상이 아니다.

후속은 Sensor/Log Viewer의 공통 지도·계획/실제 궤적·센서/객체 시간축 연결이다.
기존 CARLA RGB와 별도 CameraE2E 중앙 진단을 서로 다른 fidelity로 유지한다.
[인수 기록](validation/object-scene-workbench-20261008.json).
