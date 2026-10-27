# 저속 정지의 native 상태 관측 · 2026-09-27

## 이번에 확인한 것

별도 CARLA2300에서 기존 terminal cap/1 close-lead를 실행하며 실제 엔진의
타이어 제약·바퀴 내부 회전·내부 brake·rigid-body sleep을 API 관측과 같은 frame/actor ID로 연결했다.
**1.95초 급정지 관측에서 네 바퀴의 종방향 sticky-tire 제약이 모두 활성이고 sleep은 false였다.**
차체 sleep은 **2.35초**에 처음 관측돼 급정지보다0.40초 뒤다. 같은 시각의 내부 brake는
약0.073602이며 full-brake로 치환되지 않았다. 기존 hold 전환은2.15초다.

이는 특정 native 상태의 직접 관측이다. 50ms 관측 사이의 최초 발동 시각·제약 impulse·
실제 적용 brake torque를 기록한 것은 아니므로, 단일 원인이나 전체 solver 동작의 증명으로
확대하지 않는다. 실제 torque는 `not_observed`로 남겼다. 제약을 해제하거나 엔진 상수를
바꾸지 않았고 품질은 여전히 **감속1.989260m/s² 통과 / jerk29.439052m/s³ 실패**다.

| 관측 시각 | 속도 m/s | 종방향 활성 바퀴 | 횡방향 활성 바퀴 | 차체 sleep | 의미 |
| --- | ---: | ---: | ---: | --- | --- |
| 1.75s | 0.144571 | 0/4 | 4/4 | false | 횡방향 제약을 먼저 관측 |
| 1.90s | 0.073602 | 0/4 | 4/4 | false | 급정지 직전 관측 |
| 1.95s | 0.0000178 | 4/4 | 4/4 | false | 종방향 제약 활성과 속도 급락이 같은 관측에 존재 |
| 2.15s | 0.0000263 | 4/4 | 4/4 | false | 기존 정책이 hold로 전환 |
| 2.35s | 0 | 4/4 | 4/4 | true | sleep은 후행 상태 |

## 분리된 실행과 계측 범위

운영 engine2000/PID38851과 worker/PID39255에 attach·pause·memory write를 하지 않았다.
동일 실행파일을 GDB의 직접 자식으로 별도 기동하고 port2300 및 별도 user/HOME 디렉터리를
사용했다. native 데이터 쓰기나 inferior 함수 호출 없이 software breakpoint에서 읽었다.
Breakpoint의 프로세스 일시정지는 wall time과 실행 스케줄에 영향을 줄 수 있으므로 성능
벤치마크가 아니다. 동일 설정의 계측 비활성 기준 주행과 최대 속도 차이는5.3644e-7m/s였다.

완전 계측 실행 ID는 `native_observer_fc12567ddce8418ea7e1b2cfcfbf72da`다.
기준 실행 ID는 `native_observer_53a261524a1644acb03deb5a10b4f370`이며 별도 새 world의
관측 이력이다. 숨은 상태나 확률적 독립성을 주장하지 않는다. 기존 source bundle93파일과
제어/인지/센서 recipe를 사용했고 새 execution ID로 분리했다. Production Run/Job은 만들지 않았다.

최종 native stream은 header/owner2개와 차량 상태164개다. 각 차량의 정착 후1개와
주행81개를 포함한다. Ego의 **81/81관측**을 시간 offset·보간 없이 정확한 frame/actor로 연결했다.
API wheel omega는 native **corrected** wheel speeds와324/324값이 일치한다.
별도 raw integration speeds도 보존한다. 설치 getter의 기계어가 block+0x10을 읽는 것을
확인했으며 raw배열+0x00을 같은 필드로 취급하지 않는다. 처음 reader에서 두 필드를
혼동해 거절한 것은 허용오차를 늘리지 않고 실제 getter 의미에 맞게 수정했다.

## 실제 차량 업데이트 시간도 별도로 측정

설치 CARLA client는 max_substeps=50에1–16범위 경고를 출력했다. 이 경고만으로
실행이16회로 잘렸다고 추정하지 않았다. 실제 `FPhysXVehicleManager::Update` 함수의
float DeltaTime 인자를 기록한 결과는 다음과 같다.

- 차량이 있는 **103개 frame에서5,150개 Update 호출**, 모든 frame이50회다.
- 호출 DeltaTime은0.0010000000475–0.0010000281036초, frame당 합계는약0.0500000304초다.
- 전체 ego 주행81개 관측 frame에도 같은-counter clock group이 있고 합계가50ms에 부합한다.

따라서 **이 설치 빌드의 이번1ms요청은 실제 약1ms 차량 업데이트로 확인**됐다.
이는 추가적인 내부 tire/solver 반복 횟수나 다른 빌드의 지원 보장이 아니다.
0.5ms 후보의 실제 호출 간격까지 새로 확인한 것도 아니다. 경고와 실제 호출을 모두 보존하고
max_substeps50을 일반적인 지원 범위나 production 기본값으로 승격하지 않는다.
공식 client 경고 경로는 `Simulator::SetEpisodeSettings`의 max_substeps 검사와 별도다.

## 설치 바이너리와 읽기 위치

ELF SHA-256은 `d9433afa4f5961f1419399d61477ae52f83c54d4f657bd871f73739c8f11e958`,
실행파일/debug의 Build ID는 `a489e7257fbe49a8`이다. native observer는 이 executable SHA를
검사하고 GDB가 직접 만든 child/port/userdir가 아니면 거절한다. `may-call-functions`는 false다.
불완전한 top-level PhysX type에는 검증한 설치 기계어 offset을 사용하고, 실제 nested DWARF
layout을 함께 고정한다. 공개 PhysX header를 설치 layout이라고 대체하지 않는다.

## 실패와 중간 결과도 구분

첫 private `-nullrhi` 기동은 rendering 초기화에서 실패했고 주행으로 세지 않는다.
해당 private child만 종료한 뒤 원래와 같은 offscreen 방식으로 기준 주행을 완료했다.
대화형 SSH 세션 종료로 별도 프로세스가 종료된 뒤, 연결 수명과 분리된 bounded launcher로 바꿨다.
첫 batch 계측은 lazy DWARF type 로딩으로 stop observer를 설치하지 못했지만 clock-only 주행은
완료했다. 이를 완전 native 상태 계측으로 세지 않는다. 원본은 `clock-only-evidence.tar.gz`에 있다.
최종 launcher는 두 observer의 준비가 모두 확인되지 않으면 `run` 자체를 실행하지 않는다.
최종 완전 계측은 위 ID1건이며, 기준1건·중간 clock-only1건과 분리한다.

## 코드·검사·보존

`scripts/native_stop_observer_gdb.py`, `native_physics_clock_gdb.py`가 private native observer다.
`launch_native_stop_observer.py`는 소유 child만 시작·종료하며 `run_native_stop_observer_trial.py`는
고정된 기존 stack/physics 후보를 실행한다. Offline reader `analyze_native_stop_observer.py`는
hash chain·clock·frame/actor·API/native 필드를 대조하며 없는 flag를 false로 채우지 않는다.
`render_native_stop_report.py`는 검증된 JSON에서 전체 표본과 독립 viewer를 생성한다.

선택한 observer/terminal/physics-candidate 최종 회귀는 **77통과·기존 dependency 경고2개**다.
작성한 contract/fake-world 시험이며 실제 native 실행과 합산하지 않는다.
Chrome에서81행,8개 그래프 경로,말단/전체 구간 전환,390px viewport와page error0을 확인했다.
원격 회수36개 파일 hash와 source archive를 대조했다. 최종 production world/map/settings/weather·
23actor,두 production process start time과source92파일,로컬data749파일이 그대로다.
별도2300/2301/2302 listener와 소유 GDB/엔진은 종료됐고 운영 worker는ready다.

최종 소스 검토에서 world 복원 미확인 결과를completed/passed로 게시하지 않는 guard와
cleanup 중 소유권 재확인 실패를 기록하는 처리를 보완했다. 위 실제 성공 실행의 소스는
회수 archive에 고정돼 있고, 이후 hardening된 driver/launcher와 hash를 구분한다.
성공 결과를 재실행하거나 기존 원본을 수정하지 않았으며 최종77회귀에는 이 경계 검사가 포함된다.

## 산출물과 다음 작업

결과 화면: `.runtime/native-stop-observer-20260927/review/index.html`.
같은 폴더의 `analysis.json`·`samples.csv`와 상위 `native-stop.jsonl`,
`native-physics-clock.jsonl`, `capture-hashes.json`, `preservation.json`,
`static-accessors.txt`, `wheel-api-accessor.txt`를 함께 보존한다.
[검증 요약·hash·실행 ID](validation/carla-native-stop-observer-20260927.json).

다음 차량 모델 후보는 제어기 상수의 반복 탐색이 아니라 **종방향 저속 타이어 정지 제약의
전환 특성을 명시적인 분리 모델 변경으로 다루는 것**이다. 적용 torque/constraint impulse와
구간 내 발동 시각은 미관측으로 남으므로 변경 전후 증거에서 분리해야 한다.
제약을 무조건 꺼서 통과시키지 않고 원래 감속/jerk,정지거리,standstill/hold·기울기 조건을 함께
평가한다. 운영 binary/default는 그대로이며 cruise 관측 범위 변동은 별도 센서 개발 과제다.
