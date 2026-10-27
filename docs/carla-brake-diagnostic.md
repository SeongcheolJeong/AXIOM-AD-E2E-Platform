# CARLA 제동 응답 진단

이 도구는 고정 입력에 대한 차량 모델의 응답을 조사합니다. 운영 주행 스택이나
기존 판정을 변경하지 않는 별도 실험이며, 플랫폼 job API에 등록된 실행은 아닙니다.
초기 조건을 작성하고 원격에서 한 번 실행한 뒤 원본과 비교 화면을 보존합니다.
실측 차량 보정, 센서 검증, 주행 안전 또는 제어기 품질의 통과를 발급하지 않습니다.

## 고정된 첫 실험

`scripts/carla_brake_protocol.py`의 `carla-brake-protocol/2`는 다음을 고정합니다.

| 항목 | 조건 |
| --- | --- |
| 차량과 환경 | Tesla Model3 한 대, collision observer, 기존 Town10HD_Opt; 지도 재로딩 없음 |
| 입력 출처 | 이전 실행의 physics readback과 명시적 spawn pose·weather, 실행 전 원래 world 전체 |
| 제어 설정 | 자동변속 요청, forward gear1 요청, steer0·reverse false·handbrake false |
| 정차 준비 | 최소8·최대100tick, 기존 자세/움직임 기준에 speed0.03m/s·acceleration0.1m/s², 연속5개 |
| 속도 주입 | 정차 후 body velocity2m/s 요청 한 번, 다음 snapshot 관측; 요청 ACK는 unknown |
| Throttle 준비 | `clip(0.4*(2-v∥),0,0.35)`, brake0; 실제1.9–2.1m/s 연속5개, 최대300tick |
| 계측 | 20Hz,4초,80명령과81관측; 준비 trace 별도 보존 |
| Coast | 전체80구간 brake0 |
| Pulse | 처음5구간 brake0, 다음5구간 지정 brake, 나머지70구간 brake0 |
| 준비·계측 상한 | 출발점30m, 횡방향1m, heading 차5°, XYZ speed3m/s; main600초·cleanup40초 |
| 생성 후 정착 상한 | 수평속도0.3m/s·수직속도 절댓값4.5m/s, 수평이동0.5m·수직이동 절댓값1.25m; 정차 수렴 기준은 그대로 |

지정 pulse는0/0.025/0.05/0.1/0.2/0.35/0.6/1 중 선택합니다. 준비 속도에 도달하지
못하면 velocity 주입·gear 변경·시간 연장으로 대체하지 않습니다. 시작 및 pulse 직전의
속도 조건 실패도 원본으로 남습니다. Seed는 요청 출처이며 독립 확률 시험의 증거가 아닙니다.

두 초기화 방식은 같은 선언 제어 조건을 사용합니다. 단일 ego·LiDAR 없음·명시적 gear1
요청 등은 이전 production 실행과 다른 실험 조건입니다. 이전 실패 궤적의 완전한 재현으로
해석하지 않습니다. `gear` readback이나 wheel `omega`를 실제 구동계 상태·단위·힘의
검증으로 해석하지 않습니다.

최초 protocol1은 생성 직후 정착에도 XYZ speed3m/s를 적용했습니다. 실제 첫 진단은
수평속도0·수직속도−3.43m/s의 낙하 중 중단됐고, 초기화·계측을 시작하지 않았습니다.
원본85개 event와 실패 보고서·정리·예약 해제 증거를 보존했습니다. 과거 같은 spawn의
실제 정착3건에서는 수직속도3.92m/s·낙하량0.972m가 관측돼, 이 공통 상한은 해당 초기화와
맞지 않았습니다. Protocol2는 `inventory_refresh`와 `rest`에만 별도 상한을 적용합니다.
이는 고정 실험 조건의 공학적 경계이며 검증된 안전 한계가 아닙니다. 준비·계측의3m/s,
정차 수렴 조건, 기존 주행 판정은 유지합니다. 과거23표본의 정착은 새 엄격한 수렴 조건을
연속5개 만족했다는 증거가 아니므로 새 실험에서 다시 관측합니다.

Protocol1 원본은 계속 해석하며 새 수집기는 protocol2와 명시적으로 선택한 protocol3을 실행합니다. 첫 실패 뒤
나머지 protocol1 입력3개는 실행하지 않으며, 새 입력·UUID·source hash로 비교를 구성합니다.

## 실제 초기화 비교 · 2026-09-14

원격 CARLA build `1ae5356`에서 protocol2의 두 초기화 방식을 교대로2회씩 실행했습니다.
4회 모두 정착26tick 후 계측4초·80명령 ACK·81관측을 완료했습니다.
위 protocol1의 계측 전 실패1건까지 포함해5건의 원본과 해시를 보존했습니다.
[실행·검증 기록](validation/carla-brake-initialization-2026-09-14.json).

| 초기화 | 계측 시작 속도 m/s | 0.25초 속도 m/s | 4초 속도 m/s | 계측 중 raw gear |
| --- | ---: | ---: | ---: | --- |
| Body velocity 주입 · 2회 | 1.957952 | 1.954409 | 1.925977 | 0 |
| Throttle 준비 · 2회 | 1.948817 | 1.754365 | 0.368334 | 1 |

80개 계측 명령의 throttle·brake·steer는 모두0이고 다음 관측의 캐시된 제어 값이 일치합니다.
계측 첫 관측 F0는 아직 마지막 준비 명령의 결과입니다. Throttle의 F0 readback에는
throttle0.007853이 남고, 속도 주입의 F0 engine acceleration에는39.15893m/s²가 남습니다.
이를 첫 coast 명령의 응답으로 계산하거나0으로 바꾸지 않습니다.

**속도 범위에 연속5개 들어왔다는 조건은 정상상태 수렴이 아닙니다.** Throttle 준비의
마지막5개 속도는2.090407→2.055850→2.020424→1.980367→1.948817m/s로 감소 중이었습니다.
동일 spawn에서 시작해도 계측 시작 위치는 두 방식 사이 약1.142824m 차이이며, 준비 이력과
gear/RPM·바퀴 상태도 다릅니다. 무제동 응답 차이는 확인했지만 원인을 하나로 분리하거나
보정된 engine-braking 힘을 얻은 것은 아닙니다. 모드별 반복의81개 운동 상태가 정확히
같으므로 독립 확률 표본으로 세지 않습니다. 네 결과 모두4초 내 정지 미관측입니다.

**예정된 제동 pulse8회는 실행하지 않았습니다.** Throttle coast가0.25초 전에
1.9–2.1m/s 진입 조건을 벗어나므로, 같은 조건에서는 제동 직전에 중단됩니다.
후속은 초기·제동 시작 상태와 준비 수렴 조건을 새 protocol로 고정하는 작업입니다.
시작 위치·기어/이력 차이를 다룬 비교와 held-out 응답을 확보하기 전에 제어기 상수를
교체하거나 기존 감속·jerk 판정을 통과로 바꾸지 않습니다.

소유 actor 정리·원래23actor/world/map/settings/weather 복원과 예약 해제를5회 모두
확인했습니다. 기존 DB49개 테이블3,394행과 관리 파일745개는 그대로이며 운영 worker
heartbeat는 행 동일성 대상에서 제외합니다. 원격 worker/엔진 재시작과 production source
변경은 없었습니다. 별도 수집기의 약6–8초 wall time을 LiDAR·전체 스택 실행의 성능 개선으로
비교하지 않습니다.

## Protocol3 · 고정 위치의 준비 상태 검사

새 버전은 실제 관측 시간에 따른 PI 준비와 최초 12m 평면 통과를 사용합니다.
직전 1초의 속도·가속도·제어·raw 회전 상태가 고정 조건을 만족할 때만 즉시 pulse를
요청합니다. 첫 실제 commissioning은 준비 조건에 실패했고 계측 명령은 0개입니다.
[정확한 조건과 실패 원본](carla-brake-road-entry.md)을 확인하세요. Protocol2의 기본
작성과 과거 원본은 유지합니다. 새 입력에는 다음 옵션을 명시합니다.

```text
--protocol-version carla-brake-protocol/3 --initialization throttle
```

분석기는 PI 제안·ACK 이후 상태와 모든 진입 gate를 원본에서 다시 계산합니다.
조건부 제동 비교에는 `--baseline-experiment-id UUID`로 같은 v3 조건의 새 무제동
결과를 명시해야 합니다. 준비 이력·진입 상태가 맞지 않으면 비교 불가 사유를 표시하며,
일치한 경우에만 같은 표본 번호의 추가 속도 손실을 계산합니다. 성공한 실제 v3 기준
결과가 아직 없으므로 현재 파일의 조건부 제동 비교는 미평가입니다. 해당 비교의
작성된 입력 시험을 실제 CARLA 제동 응답의 인수로 해석하지 않습니다.

## 작성과 실행 경계

`make_protocol()`로 조건을 만들고 `measurement_sequence()`로80개의 명령을 사전 생성합니다.
Intent에는 고유 UUID, protocol/sequence SHA-256, source 세 파일의 hash, 기존 Store UUID,
CARLA/adapter version, 원래 world/map/actor/settings/weather, spawn, 요청 weather,
기존 physics 설정과 원본 응답 파일 hash를 포함합니다. `validate_intent()`와
`parse_intent()`는 미지원 버전·필드·변형된 고정 정책·중복 JSON 키를 거절합니다.

`prepare_carla_brake_intent.py`로 원본 응답에서 physics를 가져와 한 intent를 작성할 수
있습니다. 환경 JSON에는 `expected_world`, `expected_store_id`, `engine_build`,
`adapter_version`, `spawn_transform`, `requested_weather`를 명시합니다. World ID는
정밀도 손실을 피하도록 십진 문자열입니다. 작성 도구는 CARLA에 연결하지 않으며,
저장된 환경의 현재 일치 여부는 수집기가 실행 직전에 확인합니다.

```bash
python scripts/prepare_carla_brake_intent.py \
  --environment /path/to/pinned-environment.json \
  --source-evidence /path/to/original-carla-vehicle-response.json \
  --source-dir /path/to/captured-diagnostic-sources \
  --initialization throttle --measurement coast --repeat-index 1 \
  --output /path/to/new-intent.json
```

진단 소스 세 파일은 `carla_brake_protocol.py`, `diagnose_carla_brake_response.py`,
`diagnose_carla_self_hit.py`입니다. 마지막 파일의 이전 실험 `run()`을 호출하지 않고
저장·소유권·callback helper만 사용합니다. 실험마다 새 UUID를 발급하므로 응답 유실
때 작성 도구를 다시 호출해 같은 시험을 중복 실행하지 않고 기존 UUID를 조사합니다.

원격에서 배포된 optional CARLA 환경을 사용합니다. 기존 `executions/engine.lock`과
영속 예약 marker를 공유하고, 같은 experiment UUID의 중복 실행을 거절합니다.
다른 작업이나 quarantine이 있으면 실행하지 않습니다. actor 생성 반환 handle을
즉시 보관하며, 현재 episode/ID/type/nonce/parent가 확인된 소유 actor만 정리합니다.
collision sensor 중단·삭제와 callback drain 뒤 ego를 삭제하고 원래 world를 복원합니다.
정리나 저장이 불확실하면 예약을 유지합니다. 자동 재시도·엔진 재시작은 하지 않습니다.

```bash
# 전용 원격 호스트의 기존 optional environment에서 실행
ADE2E_CARLA_EXCLUSIVE=1 python diagnose_carla_brake_response.py \
  --intent /path/to/pinned-intent.json \
  --runtime-dir /path/to/axiom-carla-worker/runtime \
  --output /path/to/new-trial
```

원본에는 요청/명시적 ACK/다음 snapshot, world frame/source time, XYZ pose·velocity·acceleration,
별도 frame bracket의 control·선택형 raw telemetry가 있습니다. 한 snapshot의 가속도와 속도
차분은 같은 엔진 상태의 두 표현이며 독립 실측 증거가 아닙니다. Collider callback0도
충돌 stream의 완전성이나 무충돌을 보장하지 않습니다.

## 비교 화면

`scripts/analyze_carla_brake_response.py`는 수집 원본을 읽기만 하고, 새 출력 디렉터리에
`analysis.json`과 네트워크 의존성이 없는 `index.html`을 만듭니다. 초기화 방식별 속도,
가속도, 요청 brake를 전체 계측 표본으로 비교합니다. 준비 실패·부분 trace·정지하지 않은
결과와 cleanup 상태를 구분하며, 미관측 값을0이나 통과로 채우지 않습니다.

```bash
python scripts/analyze_carla_brake_response.py \
  --trial /path/to/trial-a --trial /path/to/trial-b \
  --source-dir /path/to/captured-diagnostic-sources \
  --source-evidence /path/to/original-carla-vehicle-response.json \
  --output /path/to/new-analysis
```

원본 intent/source/event chain과 frame·ACK가 맞는지 먼저 확인합니다. 완료 수집은
80/81의 분모를 요구하고 실패한 수집의 prefix를 유지합니다. 정지하지 않은4초 결과에서
장래 정지시간이나 정지거리를 외삽하지 않습니다. 제동 gain·새 제어기 상수를 이 화면에서
자동 발급하지 않습니다. 후속 속도/이력/held-out 검증은 [실험 설계](carla-brake-response-design.md)를 따릅니다.

서로 다른 protocol 소스를 비교할 때는 `--source-dir`를 각각 반복해서 지정합니다.
출력의 원본 파일 링크는 함께 복사한 bytes를 가리키므로 폴더 전체를 보존하세요.
이 workspace의 실제5건 비교는
`.runtime/brake-response-v2-2026-09-14/analysis-five-coast-study-v1/index.html`입니다.
화면에서 실행 선택, 준비/정착/계측 구간, 속도·높이·가속도·제동 명령 그래프,
전체324개 계측 행과 원본 파일 링크를 조사할 수 있습니다.

새 protocol·작성기·native 수집기·분석기의 계약시험236개가3.98초에 통과했습니다.
기존 native/startup/helper 시험77개(9.36초)와 실제 엔진5건은 별도 범위입니다.
실제 HTML을 브라우저에서 열어 구간·실행 선택, 실패6개 정착 표본, throttle 준비38개,
계측324개와 원본 hash 표시를 확인했습니다. 가짜 world의 자동화 시험을 실제 CARLA
장애 주입이나 제동 품질 인수로 세지 않습니다.
