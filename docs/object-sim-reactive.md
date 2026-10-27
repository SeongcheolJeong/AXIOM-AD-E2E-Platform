# Object Sim · 지도 작성과 반응형 교통

2026-10-09. 기존 도시 Object Sim을 확장한 `scenario-v2/3`의 사용법과 구현 범위다.
실제 인수 결과는 [실행 보고서](validation/object-sim-reactive-20261009.json)에 기록한다.
엔진의 RGB와 GT 기반 주행은 실측 센서·실차 검증이 아니다.

## 일반 화면에서 사용하는 순서

1. **Object Sim → 지도 편집**에서 빈 도로, T자 또는 십자 교차로를 만든다. 직선·원호·완화곡선,
   차로 수·폭·속도·변경 허용·폐쇄·우선권과 연결을 편집한다. 2D 배치와 3D 미리보기를 전환한다.
2. 서버 미리보기의 오류 위치와 이유를 확인한다. 원본 개정을 저장하고, 대상 CARLA 빌드를 선택해
   지도 빌드를 발행한다. 빌드는 XML·차로 그래프 생성이며, 이 시점에는 엔진을 실행하지 않는다.
3. **시나리오·행동**으로 지도를 넘긴다. 카탈로그의 차량·보행자·정적 asset을 배치하고 경로를 선택한다.
   `reactive`를 선택하면 행동과 planner 설정을 편집할 수 있다. 다른 차로에서 끝나는 동작은
   안내 경로와 별도로 목적지 차로의 점을 명시적으로 선택한다.
4. 행동을 순서대로 추가하고 시간·거리·상대 속도·TTC·영역·신호·이전 행동 완료 조건을 정한다.
   위험 행동은 `risk_mode`를 명시적으로 켜야 한다. GUI와 YAML은 동일한 개정을 저장한다.
5. 저장된 개정으로 실행 미리보기를 확인하고 기존 실행 큐에 제출한다. 취소와 실패 결과도 보존한다.
6. 결과 Viewer에서 actor를 선택해 실제/계획 경로, 현재 결정과 간격·양보 근거를 조사한다.
   이벤트를 누르면 동일 시간축으로 이동한다. RGB가 낮은 빈도로 저장된 경우 실제 프레임 시간과
   유지 시간을 표시하며, 없는 이미지를 새 프레임처럼 만들지 않는다.
7. **검증 계획에 연결**을 누르면 실제 실행의 프로젝트·종류·fidelity를 확인하고 해당 실행 하나를
   기존 Validation의 Experiment plans에서 선택한다. 호환되는 저장 계획을 고른 뒤 명시적으로
   평가한다. 화면 진입만으로 계획을 저장하거나 평가하지 않는다.

## 지도 계약

`object-map/1`은 편집 원본이다. 원본은 기존 프로젝트별 불변 catalog 저장소에
`object-map-source` 종류로 보관한다. `/maps`에는 별도의 실행용 불변 빌드를 발행한다.
컴파일러는 optional `axiom-opendrive` 패키지와 `scenariogeneration==0.16.7`을 사용한다.

```sh
.venv/bin/python -m pip install -c requirements.lock -c integrations/opendrive/requirements.lock -e integrations/opendrive
```

빌드는 원본 ID/개정/hash, compiler 버전·파일 hash, geometry 파일 hash, OpenDRIVE hash와
대상 엔진 빌드를 고정한다. 직접 `/maps`에 generated 출처를 작성해 컴파일 이력을 꾸밀 수 없다.
비ASCII 이름은 XML 문자 참조로 내보내 CARLA 문자열 변환으로 원문 hash가 손상되는 것을 막는다.
원문 내용과 실행 엔진의 `to_opendrive()` 내용은 여전히 정확히 비교한다.

지원 범위는 일정 폭 차로, 명시적인 차로 연결·교차로 connector와 기본 도로 3D다.
네이티브 Town의 도로를 수정한 결과를 기존 Town 건물·지형과 일치한다고 표시하지 않는다.
가져온 OpenDRIVE의 미지원 요소는 원문과 이유를 보존하고 읽기 전용으로 취급한다.
AXIOM 자체 내보내기는 재컴파일 검증을 거쳐 왕복한다. 임의 표준 파일 전체의 손실 없는 편집은 아니다.
일반 OpenDRIVE object의 CARLA mesh 생성을 주장하지 않는다. 실제 장애물은 asset이 고정된
시나리오의 정적 actor로 배치한다. 지도 객체 메타데이터만 있는 빌드는 거절한다.

## 행동과 planner

라이브러리는 기본 주행·목표 속도·추종·정지·재개·대기, 차선 변경·끼어들기·합류·분기,
추월·양보·횡단·장애물 우회·경로 재탐색·도착과 위험 주입 행동을 제공한다.
각 실행은 같은 시점의 관측을 공유하고, 10Hz로 계획해 20Hz 물리 제어에 전달한다.

`axiom-reactive-planner/1.1`은 가까운 정지 선행차 뒤에서 작은 양의 속도 요청으로 생기던
반복 전진을 억제한다. 현재 선행차 속도≤0.1m/s·범퍼 간격≤설정 최소 간격+1m이면
`follow_lead_stop`으로 정지를 유지한다. 같은 앞차 속도≥0.3m/s 또는 간격≥최소 간격+1.5m이면
관측에 따라 해제하며, 대상 변경·소실은 해당 유지 상태를 초기화한다. 원래 신호·교차·접촉 제약은
계속 적용된다. 진단의 `lead_hold`는 holding/released/cleared와 실제 대상·속도·간격·해제 이유를
보존한다. [도심 우회전 실행 범위](object-sim-urban-interaction.md).

- 기본 입력은 simulator의 객체·지도 GT다. 카메라 픽셀을 소비하는 인지 모델이 아니다.
- 차량 외곽 크기·상대 속도·간격, 지도 우선권과 도착 순서로 판단한다. 명시적인 V2X 통신은 없다.
- 차선 이동에는 합법적인 같은 방향 인접 차로가 필요하다. 경로가 없으면 이유를 남기고 정지한다.
- 우회·복귀 중에도 관측 간격을 다시 확인한다. 원래 목적지는 바꾸지 않는다.
- 자동 우회는 관측된 정적 종류 장애물에 한정한다. 정차 차량 우회·추월은 명시적인 행동으로 설정한다.
- 정지 표지는 정지 후 1초 대기를 요구하며, 신호·교차 구간과 진행 불가 상태를 별도로 기록한다.
- 동률 우선권의 actor ID 규칙은 시뮬레이터의 결정 규칙이다. 인간 운전 분포나 협상 통신의 재현은 아니다.
- 위험 행동의 실행 여부와 ego 대응의 성공 여부를 분리한다. 위험 모드도 실제 위치를 순간이동시키지 않는다.

`destination`을 생략하면 안내 경로의 마지막 점이 고정 목적지다. 별도 목적지를 지정하면
정확한 차로 그래프 점으로 검사하며, 이전 안내 경로와 새 실제 계획 경로를 모두 보존한다.
기존 `/1`, `/2`는 그대로 유지한다. `/3`의 reactive actor는 이전 controller의 wait/stop/headway
중복 설정을 사용하지 않고 행동·planner 설정으로 이를 표현한다.

## 공통 API·SDK·실행 기록

주요 API는 `/api/v1/object-maps`, `/object-maps/preview`, `/object-maps/import`,
`/object-maps/{id}/build`, `/object-maps/{id}/export`, `/object-behaviors`다.
시나리오 저장·YAML·실행은 기존 `/scenario-v2`, `/experiments` API를 재사용한다.
프로젝트 권한과 AI agent 도구 목록에도 동일한 계약을 제공한다.

Python SDK에는 `object_map_capabilities`, `object_maps`, `object_map`, `preview_object_map`,
`save_object_map`, `build_object_map`, `import_object_map`, `export_object_map`, `object_behaviors`가 추가됐다.
조회는 페이지·원본 개정을 고정하고 내보내기는 응답 바이트의 hash를 검사한다.
시나리오·실행·평가 메서드는 기존 것을 사용한다.
Engine SDK는 코어 API에 import하지 않는다.

native 실행의 원본에는 `planned_paths`의 경로 개정과 `planner_events`, 각 응답의
`plan.reactive`, planner 코드 hash, 고정 목적지와 실제 geometry·명령·RGB가 포함된다.
진행 경로에 대한 추종 오차와 고정 목적지 오차는 서로 다르게 계산한다.
이벤트 시간·actor·출처, 경로 개정·연속성·등록 도로 범위를 검사한 후 결과를 게시한다.

기존 Experiment Verification Plan으로 native contact, 추종 오차, 목적지 오차,
최종 속도와 actor별 이동 거리를 평가한다. 행동 발생 검사는 이 모션 판정과 별도로 기록한다.
교차로 전체 통과를 평가할 때는 자차뿐 아니라 모든 대상 차량의 목적지·최종 속도를 기준에 넣는다.
8대가 움직였다는 사실만으로 교차로 교통이 끝까지 해소됐다고 판단하지 않는다.
명시적 목적지는 검증 평가에서도 안내 경로의 끝점보다 우선하며, reactive 관측에는
`native-city-reactive-destination/1`과 고정 planner hash를 추가로 남긴다.
배치 인수는 `scripts/object_sim_acceptance.py`에서 동일한 공개 API와 큐를 사용한다.
개별 seed 반복은 장면 다양성이나 독립적인 실세계 검증으로 계산하지 않는다.

## 현재 실행 한계

도심·교외의 포장 도로, 최대 12m/s, 기본 20Hz 제어, 최대 32 actor/120초의 작성 계약이다.
실행 시 camera 해상도·빈도·원본 보존 용량과 고정 입력 크기를 추가로 검사한다.
최대 작성 수치가 모든 하드웨어에서 실시간 실행된다는 보장은 아니다.
고속도로 제어, 지형·건물 제작, 가변 폭·입체 도로 전체 편집, 학습형 교통 분포,
완전한 OpenSCENARIO 입출력과 V2X는 이 구현의 완료 범위가 아니다.
Autoware는 별도 선택 경계로 유지하며, 외부 planner 실패 시 기본 planner로 바꿔 성공시키지 않는다.

코드 라이선스와 지도·asset·엔진 사용 권리는 별도로 유지한다. 경쟁사의 비공개 UI·자료는 사용하지 않는다.
