# 차량 모델 revision과 추가 조건 검증 · 2026-09-27

## 결론

지난 단일 close-lead 통과 후보를 기본 모델로 승격하지 않았다. 모델 ID·revision·내용 hash를
실행에 연결한 뒤, 사전에 선언한 추가 세 조건을 기준군/후보로 각각 실행했다.
**6/6 실행은 완료·원본 검증됐지만 후보의 추가 인수는 1/3이다.**
1.5m/s 초기속도 close-lead는 통과했다. 2.5m/s와 cruise는 긴급정지 경로가 남았고,
cruise에서는 후보의 최대 jerk가 오히려 커졌다. 검증 묶음 판정은 `not_accepted`다.

## 사전에 고정한 시험과 실제 결과

양쪽 모두 이전 terminal cap/1·탐색 제동 응답 계수4.029873674501462·1ms physics를 유지했다.
기준 모델은 종방향 sticky damping1000, 후보는4이며 같은 private GDB hook을 사용한다.
각 pair에서 시나리오·제어기·인지·정책·센서·기반 소스는 같고 native damping만 다르다.

| 추가 조건 | 기준 감속 m/s² | 후보 감속 m/s² | 기준 jerk m/s³ | 후보 jerk m/s³ | 후보 지표 / 행동 |
| --- | ---: | ---: | ---: | ---: | --- |
| 정지 선행차 gap8m, ego 초기 요청1.5m/s | 1.727539 | 1.727534 | 28.278125 | **3.469571** | passed / passed |
| 정지 선행차 gap8m, ego 초기 요청2.5m/s | 4.029159 | 4.029190 | 66.359845 | **49.170111** | failed / failed |
| 이동 선행차 gap20m, ego/lead 초기 요청2m/s | 4.027246 | 4.027239 | 48.786212 | **56.434983** | failed / failed |

지표 기준은 원래의 최대 감속≤6m/s², 최대 jerk≤10m/s³, 관측 접촉0, 최소 TTC≥1.5s다.
접촉 관측은 전부0이었다. Cruise의 TTC는 기존 계산상 유한한 값이 없으며 이를 별도의
거리 안전 증명으로 바꾸지 않는다. 이동 거리는 이전과 같이 참고 지표다.

위 속도는 **초기 요청값**이다. 실제 첫 관측은 약1.467897,2.446500,1.957200m/s였으며,
실제 값을 강제로 맞추지 않았다. 정책의 cruise 목표2m/s는 모든 시험에서 동일하다.
기존 rest→target-velocity 초기화와 첫 가속도0의 평가 관례도 유지했다.
원래 엔진 가속도·XYZ 속도·timestamp는 그대로 보존했다. PI 정속 준비 인수가 아니다.

## 조건별 해석

### 1.5m/s 초기 요청: 추가 저속 정지 조건 통과

후보는 최대 감속1.727534, jerk3.469571로 기존 지표를 통과했고,1.90초에 원래 정지
속도 조건을 만족해 hold로 전환했다. 긴급정지는 없었다. 모든 hold 이후 관측은 원래
standstill 기준0.03m/s 이하였으며 마지막 속도도0이었다.
이동 거리는 기준1.495084m에서 후보1.501876m로 약6.79mm 증가했다.
이전2m/s 지정 조건과 이번1.5m/s 지정 조건의 통과이지, 그 사이 모든 속도의 보장은 아니다.

### 2.5m/s 초기 요청: 관측 범위와 정지거리 보호 조건에서 긴급정지

첫 관측은 controlled_stop이었다.0.05초에 지면 관측 prefix가6m에서5m로 줄면서
reserve 제외 가용 정지거리3m보다 필요한 정지거리 약3.797m가 커졌다.
기존 `current_stopping_envelope_exceeds_horizon` 보호 조건이 full brake를 요청했다.
양쪽 모두 controlled_stop1명령, emergency_stop79명령이다.
후보의 최대 jerk는0.80초에 발생했다. 직전 속도0.030084m/s와 가속도−2.716336m/s²가
다음0.017192m/s·−0.257830m/s²로 바뀌면서49.170111m/s³가 된다.
모델 변경이 일부 완화해도 긴급제동 경로의 품질까지 해결한 것은 아니다.

### Cruise: 지속 주행을 하지 못했고 후보 jerk도 악화

두 모델 모두 pilot_cruise1명령, controlled_stop1명령, emergency_stop78명령이다.
기존의 prefix6→5→4m 변화와0.10초의 긴급정지 전환이 남았다.
최종 속도0은 정지 시험에서는 필요할 수 있지만, cruise의 성공으로 취급할 수 없다.
최대 jerk는48.786212에서56.434983으로 악화됐다. 후보의 최대치는0.70초이며
가속도−3.992397→−1.170648m/s² 변화에서 발생했다.
이번 결과를 숨기거나 이전 통과 조건과 평균내어 후보를 승격하지 않는다.

## 새 실행 계약: 모델과 시험 조건의 명시적인 식별

`scripts/carla_vehicle_model_validation.py`가 다음을 고정한다.

- `carla-private-vehicle-model/1`: `model3-sticky-original` 또는 `model3-sticky-soft`,
  revision1, 설치 executable SHA/Build ID, 기반 source manifest,1ms physics,
  native profile, controller hash 및 실행 코드7개 hash.
- `carla-model-validation-selection/1`: 모델 manifest hash, 사전 선언된 case와 기존 기준,
  원본 reference intent hash를 묶는다. 같은 모델은 세 조건 모두 같은 manifest hash다.
- `model_execution_hash`: 실행의 native candidate hash와 모델·selection hash를 연결한다.
  실제 launcher, source-intent, report 사이의 동일성을 원본 분석에서 확인한다.

`--model-validation`은 `--sticky-damping`과 함께 명시적으로 선택해야 한다.
엔진 생성 전에 코드·reference·native profile·기반 manifest를 검사하고, driver가 다시 검사한다.
새 조건에는 별도 scenario ID와 정확히 계산한 input hash를 사용한다.
기존 scenario revision이나 과거 Run을 덮어쓰지 않는다.

이는 **private GDB-only 모델 선택 경로**다. 일반 worker의 공개 모델 설정 API,
네이티브 엔진 release 빌드 또는 production model registry 배포를 구현한 것이 아니다.
원래 adapter의 base input hash만으로는 이 native 모델을 식별할 수 없으며 일반 Run으로
묵시적으로 가져오면 안 된다. 모델 ID·revision만 아니라 내용 hash까지 함께 사용한다.

## 지표 통과와 작업 수행 성공의 분리

기존 adapter 지표와 판정은 수정하지 않았다. 검증 묶음에 **추가 행동 검사**를 선언했다.
정지 조건은 unknown/emergency 없이 hold에 도달하고 최종·hold 이후 전체 속도가 원래
standstill 기준 이내인지 확인한다. Cruise는80개 명령에서 pilot_cruise가 유지되고,
최종 속도가 기존 정책의 속도 추종 오차 한계 이내인지 확인한다.
두 판정을 모두 통과해야 추가 case 인수다. 실행 실패·미실행·회수 전 상태도 원래 분모에 남긴다.

## 검증·보존·한계

새로운 실제 실행은6건이며 **480명령·486관측**이다. 모든 pair의 입력 동등성을 확인했다.
초기 속도 차이는 pair별 약1.19e−7,−2.39e−7,1.21e−7m/s로0은 아니다.
원본·행 감쇠 변경·설정·ACK/다음 frame·전체 속도 차분 지표를 검증했다.
종방향 제약 행 총116,592개를 확인했으며 기준군의 동일값 확인과 후보의 실제 변경을 구분한다.
각 실행의 native Update는103frame에서50회씩 기록됐다.1–16범위 client 경고는 보존했다.
같은 조건의 통계적 반복, 다른 차종·노면, 실제 brake torque/impulse, 실차 정합성은 미인수다.

경사 조건은 **미구현·미실행**이다. 현재 경계는 원래 지도와 지면 가정이며, ego 자세를
기울이는 것을 경사 도로 시험으로 위장하지 않았다. 경사 authoring과 지면 좌표 검증이 선행돼야 한다.
최종 선택 회귀시험은 **715개 통과·기존 의존성 경고2개**다. 앞선704개와25개 부분 검사는
겹치므로 합산하지 않는다. 프런트엔드 typecheck도 통과했다. Chrome에서6개 결과,
조건별162개 관측,8개 그래프 경로,시간창 전환,390px 모바일과 페이지 오류0을 확인했다.
이는 offline 비교 화면 검사이며 플랫폼 전체 브라우저 인수를 새로 수행한 것은 아니다.
시험용 프로세스가 남지 않고2300/2301/2302포트가 해제된 것을 확인했다. 운영 world·23actor·
프로세스·store·source92파일과 기존 data749파일은 불변이며 DB quick_check 정상·외래키오류0이다.
최종 기록은 아래 검증 JSON에 남겼다. 커밋과 운영 배포는 하지 않았다.

## 산출물과 다음 단계

```bash
open "/home/axiom-user/Documents/ChatGPT/AD E2E Platform/.runtime/model-validation-20260927-9809c702/review/index.html"
```

같은 폴더의 `samples.csv`는 전체486개 관측이며 `analysis.json`은 모델·원본·판정의 연결을 담는다.
상위 폴더의 `suite.json`/`suite.sha256`은 실행 전에 고정한6개 요청이다.
각 case의 `capture.tar.gz`에는 해당 실행 소스·원본·모델 선택이 함께 있고,
`sources-and-plan.tar.gz`에는 공통 동결 소스와 검증 도구·계획을 보존했다.
[검증 결과·실험 ID·hash](validation/carla-model-validation-20260927.json).

다음 우선순위는 **지면 관측 prefix의 프레임별 변화가 왜 필요한 정지 범위를 잃게 하는지**를
원본 LiDAR·rig 변환·grid sampling에서 재현하고 개선하는 것이다. 후보 damping의 추가 tuning만으로
cruise 실패를 덮지 않는다. 미관측 공간을 free로 간주하거나 정지거리 guard/jerk 기준을 낮추지 않는다.
Native 모델의 일반 release 연결과 경사 검증은 별도 gate로 남긴다.
