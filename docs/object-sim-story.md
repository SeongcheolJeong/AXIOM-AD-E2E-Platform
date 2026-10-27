# Object Sim 관측 사건 시연

Object Sim의 **시연 시작**은 봉인된 `city-scene.json`에서 대표 planner 사건을 골라 자동 재생한다. A/B 비교에서는 양쪽을 같은 시뮬레이션 시각으로 재생하고, 8대 교차로 같은 단일 결과도 같은 방식으로 조사한다. 새 엔진 실행이나 DB 기록을 생성하지 않는다.

## 사용 흐름

- 결과 원본을 읽은 뒤 `시연 시작`을 누르면 사건 전후 구간을 순서대로 재생한다. 사건에 대응하는 보존 프레임에서 2초 정지해 설명을 표시한다.
- 공통 시간 이동·사건 선택 등 수동 조사를 하면 자동 전환을 멈춘다. `이 구간 시연 재개`로 다시 이어갈 수 있다.
- 구간 버튼으로 대표 사건을 선택하고, `시연 구성 근거`에서 원래 이유·객체·프레임·선택 규칙을 확인한다.
- 원본이 없거나 실행이 부분 완료·실패·불확실한 경우 자동 시연을 구성하지 않는다. 기존 결과 상태와 수동 재생·사건 탐색을 유지한다.

설명은 관측된 planner 판단의 요약이다. 설정 차이의 인과관계, 센서 인지 성공, 안전 통과, TTC·위험 점수를 새로 추론하지 않는다. 기존 실행·평가 판정은 그대로 표시한다.

## 대표 사건과 시각 계약

현재 선택 규칙은 `object-story-rules/3`다. 우회·복귀·도착은 실제 원본에 대응 이벤트가 있을 때만 추가한다.
대기 설명은 기록된 판단 이유를 따른다. 시나리오의 `wait` 명령은 **설정한 대기**로 표시하고
교통 양보·간격 판단이나 신호 대기와 구분한다. 이 문구 수정은 원본 사건·관측 시각·선택 구간을 바꾸지 않는다.

| 종류 | 선택하는 원본 기록 |
| --- | --- |
| 대기 | 설정한 `wait`·간격 대기·양보·신호 대기 등 `decision_changed`; 실제 이유별 설명 |
| 우회 | `route_replanned`의 우회 경로 선택 또는 우회 판단 전환 |
| 복귀 | 우회·추월 행동 완료 또는 `automatic_bypass_rejoined` |
| 도착 | `decision_changed`의 `arrived` |
| 진행 제한 | 차로 변경 금지·교착·행동 실패 등 명시적인 판단 전환 |

각 종류는 실행별 ego 사건을 우선하고, 같은 우선순위에서는 가장 이른 원본 사건을 선택한다. 종류별 대표 사건을 기준으로 앞 2초·뒤 4초 구간을 만들고 겹치는 구간을 합친다. 최대 5구간이며 설명 정지 시간을 포함한 전체 시연은 60초 이하다. 원본 로딩 시간은 이 재생 예산에 포함하지 않는다. 모든 판단 전환을 포함하는 보고서는 아니다.

**명령 시각과 화면에 보여줄 관측 시각을 구분한다.**

- 이벤트의 `time_s`, `index`, `input_frame`은 원본 그대로 보존한다. `time_s`는 planner 명령 시각이다.
- 추가 필드 `observation_time_s`는 원본 `frames[event.index].time_s`다. 같은 명령에 뒤따른 보존 응답 프레임의 실제 시각을 사용한다.
- 구간의 시작·정지 기준·끝은 `observation_time_s`를 기준으로 구성한다. 첫 프레임보다 앞서 시작하지 않는다. 예를 들어 명령 사건이 0초에 있어도 첫 보존 프레임이 0.05초이면 0.05초 화면에서 설명한다.
- A/B의 사건 발생 시각을 서로 맞춰 이동하지 않는다. 두 화면은 동일한 시뮬레이션 시각을 공유하며, 각 원본 사건의 명령·응답 시각 차이를 유지한다. 이 대응은 실차 제어 지연 측정이라는 뜻이 아니다.

## API·SDK

프로젝트 Viewer 이상이 읽을 수 있다.

```
GET /api/v1/object-sim/story?project_id=…&left_execution_id=…&right_execution_id=…
```

단일 결과에서는 `right_execution_id`를 생략한다. 응답은 `object-sim-story/1`이며 다음을 포함한다.

- `status: available|unavailable`, `reason`, `rule_version`, `manual_replay_available`.
- `clock: shared_simulation_time_s`, `event_clock: command_index_times_timestep`, `observation_clock: retained_response_frame_time_s`.
- `left`·`right`: 기존 execution 상세, 불변 scenario, scenario 참조와 기존 summary. 단일 결과의 `right`는 null.
- `sources.left`·`sources.right`: 실행 ID, 봉인 manifest hash, 원본 artifact ID·이름·SHA-256, scenario 참조.
- `segments`: `id`, `start_time_s`, `anchor_time_s`, `end_time_s`, `hold_s:2`, `playback_rate:1`, 제목·설명·객체 ID와 원본 사건 목록.
- 각 사건은 원래 필드에 `side`, `category`, `observation_time_s`를 추가한다. `anchor_time_s`는 선택된 사건의 `observation_time_s`와 일치한다.
- `total_presentation_s`, 선택 한도 `limits`, 해석 범위 `limitations`.

완료된 봉인 실행과 원본 source hash·객체·시각·index를 확인한다. `city-scene.json`은 경로·SHA-256·크기를 검증한 최대 128 MiB의 bytes만 읽는다. 누락·부분 원본·호환되지 않는 A/B·선택할 사건 없음은 `unavailable`과 빈 구간으로 반환한다. 프로젝트 접근 오류와 실행 자체의 봉인 무결성 오류는 기존 API 오류 계약을 유지한다.

```python
with AxiomClient('http://127.0.0.1:8033', token=project_token) as client:
    single = client.object_sim_story('job_A', project_id=project_id)
    comparison = client.object_sim_story('job_A', 'job_B', project_id=project_id)
```

외부 gateway를 준비할 때는 기존 감사한 GET 목록에 `/api/v1/object-sim/story`를 명시적으로 추가한다. 새 API가 자동 공개되지는 않는다. 작업공간 시작·복구·토큰·선택적 외부 접속 절차는 [빠른 데모 운영 문서](object-sim-demo.md)를 따른다.

## 시각 자산의 출처와 한계

3D 차량은 Kenney Car Kit 3.1의 CC0 sedan을 사용한 설명용 표현이다. 원본 GLB·texture의 hash와 라이선스, 좌표 변환·사용 범위는 [자산 manifest](../frontend/public/assets/kenney-car-kit/manifest.json)에 보존한다. 보존된 native 외곽 크기에 맞춰 표현하며 CARLA blueprint mesh와 같다고 주장하지 않는다. 원본에 없는 바퀴·서스펜션·차체 움직임을 추론해 추가하지 않는다.

3D 장면과 렌더링 RGB, planner의 GT 입력, 기존 평가 결과는 각각의 출처를 유지한다. 실제 차량·센서 정합이나 모든 시나리오의 주행 인수 완료를 의미하지 않는다. 새 native 카메라 실행과 브라우저 시연 결과는 별도의 실제 실행 기록으로 확인한다.

상황 카메라는 기록된 판단 대상이 있으면 해당 객체를 우선한다. 대상 기록이 없는 첫 장면에서는 현재 보존 프레임의 75m 이내 주변 객체를 최대 5개까지 포함하고 정지 객체를 우선한다. 이 주변 구도만으로 판단 관계선을 추가하지 않는다. 부드럽게 이동하는 것은 카메라이며 차량 자세는 원본 표본을 유지한다.

## 새 원본 카메라 실행과 재현

`scripts/object_sim_demo_cameras.json`은 ego 전방과 뒤쪽 외부 추적의 두 RGB 카메라를 지정한다. 각각 640×360, 5Hz다. native 위치 표본은 20Hz이며 웹 3D 렌더링 FPS와 서로 다른 주기다. 기존 yaw/position 센서 계약을 사용한다.

```bash
.venv/bin/python -B scripts/object_sim_demo.py film \
  --prepared .runtime/object-demo-20261009/prepared.json \
  --token-file .runtime/object-demo-20261009/access/presenter-token \
  --camera-profile scripts/object_sim_demo_cameras.json \
  --output .runtime/object-story-20261009/camera-rehearsal.json
```

기존 output receipt가 있으면 해당 실행을 재조회하며 중복 제출하지 않는다. 새 실행은 현재 baseline 시나리오를 복사하여 카메라만 교체하고, B는 기존 차로 변경/간격 차이를 적용한다. `--probe`는 회피 A의 6초 구도 확인이며 주행 인수로 세지 않는다. 전 5건 완료 후 명시적 `--publish-baselines`가 A 3건을 새 개정으로 연결한다. 기존 결과와 실패는 보존된다.

브라우저 확인은 이미 보존된 결과만 읽는다. 실행 제출은 하지 않는다.

```bash
node e2e/object-story-native.mjs \
  .runtime/object-demo-20261009 .runtime/object-story-20261009
```

`?cityPerf=1`을 URL hash 앞에 넣으면 각 3D canvas가 실제 render callback 횟수를 `data-render-count`에 기록한다. 8대 결과 두 화면의 일정 구간 카운터 차이를 wall time으로 나누며 native 표본/RGB 프레임 수를 렌더링 성능으로 계산하지 않는다.

2026-10-09 실제 촬영5건의 RGB2,400장·누락0, SDK/브라우저 시연과 성능 측정은
[실행·소스·원본 근거](validation/object-sim-story-20261009.json)에 보존했다.
Apple M4의1080p 8대 A/B는 양쪽 약60fps였지만 해당10초 동안 원본 시각은7.4초 진행했다.
이는 당시3D 렌더 목표 결과다. 이후 재생 시계 수정 결과는 다음과 같다.

## 재생 지연 수정 · 2026-10-09

공통 시간의 다음 표본이 준비된 뒤 매번50ms를 추가로 기다려 처리 지연이 누적됐다.
현재는 예정 시각까지 남은 시간만 기다린다. 양쪽 RGB 준비가 완료돼야 한 단계 전진하며,
원본 표본·해시·사건 시각은 바꾸지 않는다. 일시정지·수동 seek·배속·구간/설명 단계·
탭 가시성 전환은 예정 시각을 초기화한다.100ms를 넘는 지연은 뒤늦게 몰아서 재생하지 않는다.

같은8대 A/B·1920×1080·Apple M4·Chrome155의10초 측정은
수정 전7.25/7.35초에서 수정 후9.95/9.95초로 개선됐다.3D 렌더링은 약60fps다.
이는 첫 재생/반복 재생의 로컬 결과이며 모든 네트워크·영상 해상도에서의 보장은 아니다.
전체 캐시는 최근8장이므로 반복 재생에도 원본 조회가 발생한다.

```bash
node e2e/object-replay-timing.mjs .runtime/object-demo-20261009 \
  .runtime/object-story-20261009 .runtime/object-story-20261009/replay-timing-after.json
```

[전후 측정과 변경 소스](validation/object-replay-cadence-20261009.json).
