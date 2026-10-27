# Object Sim · 첫 초대형 베타

목표는 3–5명의 사용자가 **템플릿 복제 → 객체·경로·행동 편집 → 개정 저장 →
CARLA 실행 → 기준 A/B → 관찰·문제 제보**를 스스로 완료하는 것이다.
첫 범위는 저속 도시의 기존 참조 제어기이며 사용자 모델 연결·고속도로·임의 지도 제작은 후속이다.

## 2026-10-10 구현 상태

작성 UI, 버전 API/SDK, 참가자별 권한, 실행 큐와 관찰 기록을 연결했다.
네 템플릿 각각 A/B의 **새 CARLA 실행8건을 완료**하고 원본 응답과 RGB를 각각5,160개,
누락0·cleanup confirmed로 보존했다. 정지·재출발, 목표 차로 진입, 좌·우회전과 자차 도착을
보존된 위치·속도·planner 기록으로 확인했다. 각 실행의 자차 목표 오차는0.91–0.98m,
최종 속도는0.2m/s 미만이며 native 접촉 기록은0건이다. 이는 이 조건의 simulator 관측이다.

정상 비밀번호 로그인에서 편집·개정 저장·새로고침 복원·프로젝트 격리를 확인했다.
최종 브라우저 검사에서는 수정 개정의 native1건을 완료해 기준 A/B·관찰 저장·복원,
같은 개정 재실행의 취소·종료 상태 복원과 재로그인까지20항목을 통과했고 page error는0건이다.
취소 실행의 `outcome_uncertain`은 false였다. 중간 검사에서 발견한 GLB404는 모델·texture·CC0
고지의 gateway 경로를 수정해 해결했으며 gateway/provisioning131개 검사도 통과했다.
실행 중 소유 actor6개가 있는 world에서도 기존 live lease를 확인해 정상 preview를 허용했다.
비교 화면의 긴 경로 JSON은 요약과 접힌 원문으로 정리했다. 최종 보존 결과 재생4항목에서
설정 영역650px 미만, GLB·texture의 최초/새로고침 응답200, RGB A/B·북마크와 page error0을 확인했다.
이 마지막 표시 확인에서는 새 실행을 제출하지 않았다.
관련 backend·adapter·SDK·frontend 검사 및 production build와 실제 엔진 근거를 구분한다.

첫 정지·재출발 A의 RGB warm-up timeout 실패와 불확실한 결과는 그대로 보존했다.
당시 가용 메모리 약0.5GiB와 NVIDIA kernel595.84/user library595.99.02 불일치를 관측했고,
새 프로세스의 기본 Vulkan이 llvmpipe만 인식함을 확인했다. 실패 실행의 소유 actor 삭제·
설정 복원을 독립 확인한 뒤 정상 운영자 API로 reconcile했다. 사용자 승인으로 발표용2000을
일시 중지하고, 공식595.84 패키지를 사용자 폴더에 추출해 프로세스 환경만 지정했다.
Vulkan/NVML의 RTX2070SUPER 인식을 확인한 후2160에서 위 native 인수를 수행했다.
시스템 패키지 설치·커널 교체·재부팅은 하지 않았으며 메모리를 유일한 실패 원인으로 단정하지 않는다.

첫 베타8건과 브라우저 인수 뒤13:11:29UTC에2160을 종료하고 발표용2000을 복원했다.
아래 빠른 시연6건을 위한 별도 실행 시간대를 마친 뒤 **13:38:55UTC에 다시2160 종료·
발표용2000 복원·베타 API 작성/재생 모드 전환을 확인**했다.
발표용2000은 기존 명령과 private595.84 환경으로 Town10HD_Opt·비동기·동적 객체0 상태이며,
RTX2070SUPER의 사용량은4,982/8,192MiB였다. 현재 베타 API는 작성·저장·기존 결과 재생을
제공하고 새 native 실행은 의도적으로 비활성화했다. 새 실행에는 발표용 엔진과 겹치지 않는
전용 베타 실행 시간대 또는 별도 GPU가 필요하다. 보존 자료를 삭제하지 않았으며 남은 저장 공간은
약4GiB이므로 다음 native 묶음에는 저장 용량도 먼저 확보해야 한다.

## 빠른 시연 네 장면 추가 · 별도 native6건

기존 발표 프로젝트의 최신 고정 시나리오·두 카메라 설정을 정상 API로 읽고, testerA의 기존
프로젝트 `project_5cd96f6f0076463e9f31c22702589127`에 네 프리셋을 준비했다.
지도·asset 정의와 source hash를 확인하고 필요한 generated map을 정상 작성/build API로
다시 만들었다. 기존 베타 네 템플릿과 권한은 유지했고 다른 프로젝트의 실행 결과는 복사하지 않았다.

기존 베타 A/B8건과 별도로 **새 native6건이 실행 완료**됐다. 응답5,280개·RGB2,880장,
누락0·cleanup confirmed이며 보존된 native 접촉 기록은0건이다. 고정된 평가 기준의 결과는
**5건 pass와 의도한 목표 미도달1건 fail**이다. 실행 완료6건을 평가 통과6건으로 세지 않는다.

| 빠른 시연 프리셋 | 지도 / 새 실행 | 보존된 실제 관찰 |
| --- | --- | --- |
| `obstacle_bypass` | generated 직선 도로 / A·B | A는 장애물 우회·복귀 후 도착. 차로 변경을 끈 B는 출발점에서 이동0m·목표 오차120m로 남아 목표 평가 fail. 장애물 앞까지 접근한 뒤 정지한 사례는 아님 |
| `gap_release` | 같은 generated 도로 / A·B | B에서 후방차 속도7→5m/s·초기 간격8→6m를 함께 변경. 후방차 중심 통과는0.40초 빨랐지만 자차 대기는 양쪽4.95초, 횡방향 이탈 시점은 양쪽7.85초로 같음. 양쪽 우회·복귀·도착 |
| `junction_eight` | generated 교차로 / A | 8대 모두 도착·정지. 목표 오차0.914–0.936m. 자차 중심의 기존 평가와 별도로 보존 좌표에서8대를 확인 |
| `urban_city` | native `Town10HD_Opt` / A | 자차에 작성한2초 wait 전후 주행·재출발과3대 도착. 실제 정지3.45–5.60초, 길이2.15초. 교통에 반응한 양보 명령은 아님 |

장애물·간격 장면은 양쪽 결과를, 교차로·도시는 단일 결과를 해당 프로젝트 프리셋에 연결했다.
장애물·간격의 기본 직접 재생은 원래 시험 A이며 비교 링크는 원래 시험 B→A 순서다.
화면의 좌우 A/B 표기와 시험 receipt의 A/B를 혼동하지 않고 선택된 실행 ID·설정을 함께 읽는다.
정상 참가자 브라우저에서 새 네 장면과 기존 우회전의 원본 RGB5종 재생/일시정지·프레임 변화를
확인했다. 장애물·간격·우회전의 RGB A/B3종은 정확한 프로젝트·실행 URL과 양쪽 영상을
새로고침 뒤에도 유지했다. 장애물 비교의 왼쪽은 예상 기준 미달, 오른쪽은 기준 통과로 표시되며
교차로의8개 actor 선택도 확인했다. 이8개 묶음의 console error는0건이고 새 실행·저장은 없었다.
앞선 작성 확인6개 묶음에서는 다섯 카드의 편집 가능 상태와 속도·간격·대기 변경 후 복원,
교차로 actor 선택을 확인했다. 안내형 story API의 gateway 허용 목록 누락을 발견해 기존 프로젝트
권한·자격을 유지하며 수정했고 gateway148개·backend story45개 검사를 통과했다. 세 gateway에
반영한 뒤 정상 참가자 브라우저의 장애물·간격·우회전 안내3개 묶음에서 공통 timeline과 양쪽 RGB
진행, 정확한 프로젝트·실행 URL의 새로고침 복원, 시작 재활성화·차단 경고 없음·console error0을
확인했다. 앞선 차단 기록은 보존하고 후속 receipt에서 해결을 기록했다. 새 빠른 시연의
작성6개·보존 재생8개·안내3개 묶음은 위 첫 베타 브라우저 인수와 별도이며 새 실행·저장은 없었다.

근거는 `.runtime/object-beta-20261010/`의 `quick-prepared.json`, `quick-native.json`,
`quick-measured-acceptance.json`, `quick-result-bindings.json`에 보존한다.
최신 환경 복원은 `quick-presentation-restoration.json`, 실행 비활성화 상태는
`quick-final-capabilities.json`에서 확인한다.
브라우저 근거는 같은 폴더의 `browser-quick/cua-authoring.json`, `browser-quick/cua-retained.json`,
`browser-quick/cua-story.json`이며 gateway 반영 근거는 `quick-story-access-fix.json`이다.
[추가 시연6건의 종합 근거](validation/object-sim-quick-20261010.json)를 기존 베타8건의 기록과 구분한다.

## 사용자가 할 수 있는 일

| 단계 | 구현 내용 | 현재 확인 범위 |
| --- | --- | --- |
| 접속 | 참가자별 프로젝트·Engineer credential, 비밀번호 로그인·HttpOnly 세션 | 정상 브라우저 로그인, 다른 프로젝트 거부, 재로그인 확인 |
| 복제·편집 | 네 템플릿, 이름, 객체별 속도, 지도 위 출발점·목적지, 행동 조건·대상 | 정상 API와 브라우저 확인 |
| 저장 | immutable scenario 개정, seed·map/asset/config hash, 사용자별 초안·제출 checkpoint | r1→r2와 새로고침 복원 확인 |
| 실행 | 기존 Experiment 큐, 취소·재시도, 한 CARLA 자원에 한 실행 | 템플릿 A/B8건; 최종 브라우저 수정1건과 같은 개정 재실행 취소 확인 |
| 비교 | 기준 결과와 새 결과의 A/B, URL 복원, 원본 영상과 3D 도식 | 새 native RGB A/B·북마크 복원, GLB·texture 제공과 간결한 설정 표시 확인 |
| 관찰 | 실행·개정·시점에 묶인 관찰/문제 제보 | 브라우저에서 완료 실행 기록·복원 확인; 실패 실행도 지원 |
| 행동 보고 | planner 행동 상태와 native 위치·속도·차로/교차점 측정을 분리 | 네 템플릿8건의 측정 조건 확인; 경로 회전은 명시적 행동 명령과 구별 |
| 빠른 시연 | 장애물 우회·간격 대기·8대 교차로·도시 주행의 저장 결과 | 별도 native6건·5pass/예상 fail1건, 참가자 RGB·비교·안내·새로고침 확인; gateway 차단 수정 후 안내3개 재확인 |

실행 대기·실패·원본 불일치 상태는 행동 미발생 또는 자차 실패로 바꾸지 않는다.
보고서는 planner가 기록한 완료와 실제 응답 좌표의 정지·재출발·목표 차로 진입·회전을 구분한다.
교차점 통과 시차는 보존된 actor 중심의 최근접 시점으로 계산하며 TTC나 안전 판정이 아니다.
관찰의 `behavior_exercised`와 `ego_outcome`은 사용자의 기록으로 자동 측정값과 구별한다.
기존 Case 계약에는 Object Sim 실행 형식이 없으므로 별도 버전 관찰 API를 사용한다.

## 첫 행동 묶음

| 템플릿 | 편집 대상 | 준비된 A/B 조건 | 새 native 인수 |
| --- | --- | --- | --- |
| `lead_stop_start` | 자차/선행차 속도, 대기 시작·길이 | 대기2초 / 4초 | 실제 정지2.05 / 4.05초와 재출발, 자차 도착 |
| `cut_in` | 자차/진입차 속도, 진입 시작, 목표 차로 | 시작1초 / 2초 | 완료8.5 / 10.8초, 목표 차로 오차0.080 / 0.070m, 자차 도착 |
| `urban_left_turn` | 좌회전 경로, 교차 차량 속도 | 교차 차량4 / 3m/s | 자차 약92°회전·도착; 교차 차량이 자차보다6.0 / 7.4초 뒤 교차점 통과 |
| `urban_right_turn` | 우회전 경로, 선행차 대기 | 대기0초 / 6초 | 양쪽 회전·도착, B 선행차 실제 정지6.05초와 재출발 |

좌회전 A/B의 자차 궤적은 동일하고 교차 차량의 통과 시점이 달랐다. 자차 반응 개선이나
일반적인 교차로 양보 성능을 입증한 것으로 설명하지 않는다. 좌·우회전은 경로 추종의 회전이며
별도 authored turn action의 시작/완료 이벤트가 없다는 점도 행동 보고에 유지한다.

세 행동군을 네 템플릿으로 제공한다. 좌·우회전은 native lane graph의 실제 연결을 따르며,
끼어들기는 native 같은 방향 이웃 차로와 차로 변경 허용을 요구한다.
시작점·목적지를 변경하면 경로를 다시 계산한다. 연결되지 않은 차로, 지원하지 않는 행동,
유효하지 않은 시점은 actor/field 오류로 돌려준다. 지도 그래프 검사는 실제 spawn 성공 보장이 아니다.
현재 Prius catalog에는 크기 정보가 없어 초기 겹침 검사는 부분 검사로 표시하고 native에서 최종 확인한다.

## 지도·asset·OSS 범위

첫 도시 지도는 기존 `Town10HD_Opt`를 재사용한다. 이번 capture는 native 차로의 좌우 연결·
변경 허용과 junction ID를 새 catalog 개정에 보존한다. 기존 catalog hash를 수정하지 않는다.
기본 차량은 기존 CARLA Prius이며 여러 차종·보행자·자전거와 asset 제작 도구는 이번 범위에 없다.
빠른 시연의 장애물·간격·8대 교차로는 procedural Object Map으로 생성한 지도이고,
`urban_city`는 native Town10의 기존 source catalog를 사용한다. 네 beta 템플릿의 더 풍부한
차로 그래프 catalog는 별도로 유지한다. 도시 템플릿을 모든 지도에서 보장하지 않는다.

CARLA의 지도·물리·RGB와 기존 AXIOM 반응형 planner, 공통 큐·viewer를 재사용했다.
새로운 일반 목적 충돌 방지 planner나 차량 모델을 개발한 것은 아니다.
CARLA 코드, Unreal 엔진, 차량·지도 asset의 기존 출처/권리는 각각 유지한다.
실차·센서 상관성, 안전 인증, 상용 공개 서비스 인수와 구별한다.

## 운영·재현

현재 별도 API는 `http://127.0.0.1:8063`이며 참가자 gateway는8863/8864/8865다.
각각 별도 프로젝트를 사용하고 같은 workspace DB와 지정 자원을 공유한다.
로그인 비밀번호는 `.runtime/object-beta-20261010/invites-bc/`와 `invites-a/`의
해당 참가자 `invitation-private.json`에만 보관한다. 실제 초대 발송·외부 ingress는 수행하지 않았다.
게이트웨이 설정은 시작 시 고정되며 재시작 시 세션이 폐기된다. 최종 빌드 반영을 위해
세 gateway를 재시작했고 `/login` 응답200을 확인했다. 기존 브라우저는 다시 로그인해야 한다.
[접속·권한·한도 상세](object-beta-access.md)를 따른다.

595.84 호환 라이브러리는 해당 프로세스의 `LD_LIBRARY_PATH`와 별도 Vulkan ICD에만 적용한다.
원격 task 폴더의 `nvidia-userspace-595.84/environment.json`과 다운로드 SHA·GPU probe 기록을
보존했다. 재부팅 후 커널 드라이버가 바뀌면 이 환경을 그대로 재사용하지 말고 버전을 다시 확인한다.

템플릿 준비와 native 인수는 분리한다. 준비는 정상 API만 사용하며 엔진을 제출하지 않는다.

```bash
.venv/bin/python -B scripts/prepare_object_beta.py prepare \
  --token-file .runtime/object-beta-20261010/access/api-token \
  --catalog .runtime/object-beta-20261010/catalog-Town10HD_Opt.json \
  --source .runtime/object-beta-20261010/source.json \
  --paths .runtime/object-beta-20261010/beta-paths.json \
  --output .runtime/object-beta-20261010/prepared.json
```

실제 엔진 자원이 준비되고 베타 handler를 연결한 실행 시간대에만 아래를 실행한다.
기존 실패 `native-runs.json`과 완료8건의 `native-retry-1.json`을 보존하며 추가 실행에는 새 output을 쓴다.

```bash
.venv/bin/python -B scripts/prepare_object_beta.py rehearse \
  --token-file .runtime/object-beta-20261010/access/api-token \
  --prepared .runtime/object-beta-20261010/prepared.json \
  --output .runtime/object-beta-20261010/native-next-explicit-run.json
```

네 템플릿 각각 A/B를 순차 제출하고 실패 시 중단한다. 모든 결과를 확인한 뒤 해당 프로젝트의
기준 결과를 연결한다. 다른 참가자 프로젝트에는 그 프로젝트에서 생성한 결과만 연결한다.
브라우저 확인은 `e2e/object-beta-native.mjs --no-submit`과 실제 실행을 포함하는 `--submit`을 구분한다.

빠른 시연 네 장면의 준비는 아래 별도 helper로 수행했다. source revision/hash를 먼저 전부
확인하고 기존 프로젝트에 catalog·scenario·binding만 만든다. 정확한 catalog 정의를 재사용하며
ID가 충돌하면 별도 namespace를 사용한다. receipt 재실행은 중복 생성 없이 복구한다.
이 명령은 native 실행을 제출하지 않으며 새 결과 연결은 실행 후 별도 검토를 거쳤다.

```bash
.venv/bin/python -B scripts/prepare_object_quick.py \
  --api http://127.0.0.1:8063/api/v1 \
  --source-api http://127.0.0.1:8033/api/v1 \
  --project project_5cd96f6f0076463e9f31c22702589127 \
  --token-file .runtime/object-beta-20261010/access/api-token \
  --source-token-file .runtime/object-demo-20261009/access/presenter-token \
  --selection .runtime/object-beta-20261010/quick-selection.json \
  --output .runtime/object-beta-20261010/quick-prepared.json

PYTHONPATH=backend/src:scripts .venv/bin/python -m pytest \
  scripts/tests/test_prepare_object_quick.py -q
```

관련4개 검사는 기존 프로젝트 보존·catalog 재사용/충돌·generated map 재생성과 재실행,
source pin 불일치·이미 연결된 프리셋·generated geometry 변화의 쓰기 차단을 확인했다.
별도 `quick-preparation-checks.json`은 네 프리셋의 정상 preview·정확한 pin·카메라 예산을 기록한다.

## 베타 시작 전 남은 항목

1. 새 native 실행을 허용할 전용 베타 시간대 또는 별도 GPU·저장 용량을 정하고 해당 시간대에2160과
   베타 handler를 준비한다. 현재 작성·보존 결과 재생 모드는 실행 준비 완료를 뜻하지 않는다.
2. 초대 사용자의 HTTPS 접속 주소·기간과 개별 자격 전달, 3–5명의 실제 사용성 인수를 진행한다.

80%의 도움 없는 완료율은 향후 사용자 인수 목표다. 자동 검사 수나 준비된 계정 수로 대체하지 않는다.
[현재 근거](validation/object-sim-beta-20261010.json)를 따른다.
