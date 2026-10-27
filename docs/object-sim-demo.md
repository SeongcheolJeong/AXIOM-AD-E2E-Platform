# Object Sim 빠른 데모

## 브라우저 인증과 연결 복구

직접8033에 접속할 때는 Workspace settings에서 기존 프로젝트 토큰으로 연결한다.
인증 정보는 현재 탭의 세션에만 저장된다. 다른 브라우저·새 세션에는 별도 연결이 필요하다.
2026-10-10부터 연결 검증 성공 시 현재 URL을 유지하며 열린 화면을 다시 불러오므로,
Object Sim 비교에 남은 이전401을 없애기 위해 수동 새로고침을 할 필요가 없다.
잘못된 토큰은 거절하며, 장기 저장·공개 접속·권한 확장은 추가하지 않았다.

## 시연 범위

아래 세 프리셋과 실행 수는 최초 데모 인수 범위다. 이후 추가한 도시 주행·다섯 번째
도심 우회전 프리셋과 native RGB 시연은 [도시 상호작용](object-sim-urban-interaction.md) 및
[최신 개발 상태](status.md)에 기록한다. 발표에 사용하는 실제 지원 목록은 선택 프로젝트의
일반 preset API 결과와 고정 binding을 기준으로 한다.

2026-10-09. 일반 UI에서 **프리셋 선택 → 속도·위치·차로 변경 설정 → 실제 재실행 → A/B 동기 재생 → 판단 이유 조사**를 연결한다. 기존 CARLA 반응형 planner와 시나리오·실험·큐·원본 보존을 사용한다.

프리셋은 **장애물 회피**, **양보 후 진입**, **8대 교차로** 세 가지다. 선택한 프로젝트에 명시적으로 고정된 시나리오만 사용한다. 프리셋 `ready`는 작성 입력이 준비됐다는 뜻이며, 실제 실행은 기존 native 사전점검을 거친다. 원본 RGB는 시뮬레이터의 렌더링 결과다.

현재 전용 작업공간은 `.runtime/object-demo-20261009/workspace`, API·UI는 `http://127.0.0.1:8033`이다. 발표자 시작 주소:

```
http://127.0.0.1:8033/#object-sim?project=project_bc937f94c7c94a7884993c074213a86c
```

Workspace 설정에는 로컬 소유자 전용 파일 `.runtime/object-demo-20261009/access/presenter-token`의 **프로젝트 Engineer 토큰**을 입력한다. 토큰 파일이나 내부 API 운영자 토큰을 공유 자료에 포함하지 않는다.

## 시연 흐름

1. **장애물 회피**를 선택하고 저장된 실제 A 결과를 재생한다. 차로 변경 허용을 끄고 `변경안 실행`으로 새 B를 만든다.
2. A/B 비교에서 설정 차이·속도·이동 거리·목표 오차·접촉 관측을 확인하고 같은 시각으로 이동한다. 차로 변경을 금지한 B의 목표 미도달은 그대로 설명한다.
3. **양보 후 진입**에서 옆 차로 차량의 속도·초기 간격을 바꿔 재실행하고 대기·진입의 판단 전환을 조사한다.
4. **8대 교차로**에서 객체별 경로·판단·원본 영상을 조사한다. 발표 모드에서 화면을 크게 표시한다.

새 실행과 저장 결과는 구분해서 표시한다. 새 실행을 기다리는 동안 저장된 A 결과를 재생할 수 있다. 종료된 결과에 이후 관측을 생성하지 않으며, 실패·미완료·불확실 상태를 유지한다. 현재 리허설의 5개 native 작업은 모두 `completed`이고, 장애물 회피 B의 기존 주행 판정은 `fail`이다. 작업 완료가 모든 주행 기준 통과를 뜻하지 않는다. 개별 실행·판정·비교 근거는 `.runtime/object-demo-20261009/rehearsal.json`에 있다.

브라우저에서 속도5→4.5m/s와 장애물46→50m를 바꿔 제출한 추가 native 실행도 완료했다.
6개 완료 실행의 RGB는 총1,425장, 누락0이다. 취소1회·부분 비교·공통 시간/배속·URL 재진입과
8대 저장 결과 재생을 별도로 확인했다. [실행 및 소스 근거](validation/object-sim-demo-20261009.json).
배경은 절차적 도로이며 3D는 좌표·크기의 도식이다. 사실적인 도시 환경이나 실측 센서 검증을 제공하는 데모로 설명하지 않는다.

## API와 Python SDK

모든 경로는 `/api/v1` 기준이며 프로젝트 토큰의 경계를 적용한다. 새 실행 엔진이나 비교 DB 테이블을 추가하지 않았다.

| 메서드·경로 | 입력과 결과 |
| --- | --- |
| `GET /object-sim/presets?project_id=…` | `object-sim-presets/1`: `items`에 `id`, `version`, 한국어 `title`·`description`, `ready`·`status`·`reason`, `parameters`·`defaults`, `binding_revision`, `scenario_ref`, `saved_execution_id`, 선택적 `saved_comparison_execution_id` 반환 |
| `POST /object-sim/presets/{identity}/preview` | `{project_id, parameters:{…}, actor_poses?:{actor_id:{x,y,z,heading_rad}}}` → `object-sim-preview/1`: `scenario-v2/3` 초안, 실제 map catalog·`lanes`, 고정 출처, 해석된 설정, 객체 역할, 경고. 저장·실행 없음 |
| `POST /object-sim/presets/{identity}/bind` | Engineer 전용. `{project_id, expected_revision, scenario_ref, actor_roles, saved_execution_id?, saved_comparison_execution_id?}` → 버전이 증가하는 명시적 binding. 같은 내용의 재시도는 중복 개정을 만들지 않음. 저장 비교는 완료·호환·동일 프로젝트 조건을 검사 |
| `GET /object-sim/comparison?project_id=…&left_execution_id=…&right_execution_id=…` | `object-sim-comparison/1`: `left`·`right`에 기존 execution 상세·고정 scenario·참조·summary, `compatibility:{compatible,reasons}`, 실제 `changes:[{path,left,right}]`, 기존 `metrics:[{path,left,right,delta}]` 반환 |

프리셋 ID는 `obstacle_bypass`, `gap_release`, `junction_eight`다. `scenario_ref`는 `{id,revision,configuration_hash,manifest_hash}`를 모두 고정한다. `actor_roles`에는 ego 객체 ID와 프리셋에 필요한 obstacle/rear 객체 ID를 명시한다. 저장 결과는 같은 프로젝트·같은 고정 시나리오의 완료된 실행이어야 한다.

| 설정 | 프리셋 | 범위 |
| --- | --- | --- |
| `ego_speed_mps` | 회피·양보 | 0–12 m/s |
| `obstacle_offset_m` | 회피 | 원래 위치에서 진행방향으로 −15–15 m |
| `rear_speed_mps` | 양보 | 0–12 m/s |
| `rear_gap_m` | 양보 | 자차 기준 종방향 중심 간격 5–60 m |
| `allow_lane_changes` | 회피·양보 | Boolean |

객체 드래그는 5 m 이내의 등록 차로에 맞추고, 움직이는 객체는 원래 목적지까지 방향이 연결된 경로를 다시 계산한다. 범위를 벗어난 점·알 수 없는 객체·연결되지 않거나 폐쇄된 경로는 거절한다. 한 객체에 위치 설정과 드래그를 동시에 적용하지 않는다. UI는 해당 위치 설정을 바꾸면 드래그를 지우고, 드래그하면 위치 설정을 기본값으로 돌린다.

비교는 프로젝트·지도 개정·seed·adapter/planner 빌드·fidelity와 시험 구조의 호환성을 확인한다. 객체 지표는 배열 순서가 아닌 `actor_id`로 대응시킨다. 호환되지 않거나 한쪽이 미완료·불확실한 경우 수치 차이를 만들지 않는다. 원래 판정을 유지하며 새로운 승패 판정을 생성하지 않는다.

SDK에는 `object_sim_presets`, `preview_object_sim_preset`, `bind_object_sim_preset`, `object_sim_comparison`이 있다.

```python
from pathlib import Path
from axiom_sdk import AxiomClient

token = Path('.runtime/object-demo-20261009/access/presenter-token').read_text().strip()
project = 'project_bc937f94c7c94a7884993c074213a86c'
with AxiomClient('http://127.0.0.1:8033', token=token) as client:
    presets = client.object_sim_presets(project_id=project)
    draft = client.preview_object_sim_preset(
        'obstacle_bypass', project_id=project,
        parameters={'allow_lane_changes': False})
    # draft['scenario']는 기존 create_scenario_v2 → experiment preview/create/submit으로 실행한다.
```

## 준비·시작·복구

저장소 루트에서 실행한다. `serve`는 고정 production UI, 격리된 DB·파일·토큰, 기존 launcher의 API·worker를 사용한다. 실행 환경 값은 명시한 `AXIOM_*` JSON에서 읽는다. 기존 8000/8021 서비스나 다른 launcher를 종료하지 않는다.

```bash
.venv/bin/python -B scripts/object_sim_demo.py serve \
  --runtime "$PWD/.runtime/object-demo-20261009" \
  --engine-env "$PWD/.runtime/object-demo-20261009/engine-environment.json" \
  --port 8033
```

이미 8033이 실행 중이면 재실행하지 않는다. UI를 수정한 뒤에는 production build를 만든 후 **해당 launcher만** 정상 종료·재시작해야 새 UI snapshot을 읽는다. foreground launcher는 `Ctrl-C`로 종료한다. background인 경우 `launcher.json`의 PID와 실제 프로세스 명령·작업공간을 대조한 뒤 그 launcher에만 `SIGTERM`을 보낸다. launcher가 소유한 worker를 먼저 정리하고 API를 종료한다. `killall`, DB 삭제, 다른 CARLA 작업 강제 종료를 사용하지 않는다.

초기 준비는 정확한 source 시나리오 개정·hash·actor role이 든 selection을 입력한다. generated map은 원본 작성 지도에서 다시 빌드하고 asset의 기존 이용 조건을 보존한다. `prepare` 자체는 native 작업을 제출하지 않는다.

```bash
.venv/bin/python scripts/object_sim_demo.py prepare \
  --source-api http://127.0.0.1:8032/api/v1 \
  --token-file "$PWD/.runtime/object-demo-20261009/access/api-token" \
  --selection "$PWD/.runtime/object-demo-20261009/selection.json" \
  --output "$PWD/.runtime/object-demo-20261009/prepared.json"
```

새 리허설은 아래 명령으로 회피 A/B·양보 A/B·교차로 A의 **5개 실제 작업**을 제출한다. 이미 완료한 receipt와 같은 출력 경로를 쓰면 기존 작업을 확인하고 이어간다. 새 반복 시험은 별도 output을 사용한다.

인증된 다른 source를 이용한다면 `--source-token-file`을 추가한다. 위 selection은 기존 8032 연구 작업공간의 정확한 개정을 가리킨다.

```bash
.venv/bin/python scripts/object_sim_demo.py rehearse \
  --token-file "$PWD/.runtime/object-demo-20261009/access/presenter-token" \
  --prepared "$PWD/.runtime/object-demo-20261009/prepared.json" \
  --output "$PWD/.runtime/object-demo-20261009/rehearsal.json"
```

복구할 때는 같은 workspace·access·engine environment로 시작하고 저장 결과부터 연다. 대기 작업이 있으면 launcher가 묵시적으로 재개하지 않는다. 기존 receipt·작업 상태를 조사한 후 의도적으로 재개할 때만 `serve --resume-queued`를 사용한다. 실패를 숨기기 위해 acceptance 기준·목적지·원본을 변경하지 않는다.

브라우저 흐름을 다시 확인하려면 `node e2e/object-demo-native.mjs .runtime/object-demo-20261009`를 사용한다.
이는 수정 실행1개와 바로 취소하는 실행1개를 실제 제출한다. 이미 남긴 실행을 재사용하는 확인은
동일 명령에 `--resume`을 붙이며, 이전 브라우저 보고서를 보존한다. Chrome 또는 지정한 `PLAYWRIGHT_CHANNEL`이 필요하다.

## 선택적 외부 발표 접속

이번 구현에서 **새 외부 링크를 발급하지 않았으며 기존 8787 gateway·터널은 변경하지 않았다.** 외부 주소가 필요하면 별도 세션·포트(예: 8788)에 기존 gateway를 준비한다.

- 소유자만 읽는 `0600` config와 project Engineer token 파일, production dist 절대 경로를 사용한다.
- `upstream`은 `http://127.0.0.1:8033`, `expected_project_id`는 위 데모 프로젝트, `access_mode`는 `project_engineer`로 고정한다. 시작 시 실제 토큰의 프로젝트·역할을 검증한다.
- `password`는 새로 생성한 24 byte 이상 값, `expires_at`은 **발급 시각+48시간**의 Unix seconds로 지정한다. 기존 만료 시각을 자동 연장하지 않는다.
- 감사한 기존 프로젝트·지도·시나리오·실험 조회 경로에 `GET /api/v1/object-sim/presets`, `GET /api/v1/object-sim/comparison`을 추가한다. 기존 시나리오 저장·실험 preview/create/submit/cancel에 `POST /api/v1/object-sim/presets/{identity}/preview`를 추가한다.
- preset `bind`는 로컬 준비 기능으로 남기며 gateway가 항상 차단한다. 다른 `project_id`를 명시한 query/body도 forwarding 전에 차단한다. 조회 전용 gateway 모드는 모든 POST를 계속 차단한다.

```bash
.venv/bin/python scripts/external_access_gateway.py \
  --config /absolute/private/demo-session/engineer-config.json --port 8788
```

기존 supervisor를 쓰는 경우 세션 디렉터리를 저장소 `.runtime` 바로 아래에 `0700`으로 만들고, 감사된 tunnel 바이너리·같은 config·`--port 8788`을 명시한다. 발표 전 비밀번호 로그인·같은 프로젝트 조회·저장 결과 재생·새 실행 1건·고정 만료를 확인한다. 외부 비밀번호·토큰을 문서나 로그에 기록하지 않는다. 자세한 gateway 경계는 [외부 프로젝트 접속](external-access.md)에 있다.
