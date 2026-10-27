# 5ms 후보를 실제 LiDAR 주행 경로에 적용 · 2026-09-26 시작

## 이번에 달라진 점

고정 throttle 2초 진단을 반복하지 않았다. 기존 CARLA0.8의 실제 LiDAR → shared
stack kernel2 → 제어 명령/ACK → 다음 physics 경로로 돌아가, cruise와 close-lead를
각각 10ms 기준군과 5ms 후보로 새로 실행했다. 차량 생성·정착 이전부터 후보를 적용했다.
정상 production 진입점의 기본값은 여전히 10ms이며 운영 원격 release를 교체하지 않았다.

## 실제 결과

| 동일 시나리오의 새 실행 | 10ms 최대 감속 m/s² | 5ms 최대 감속 m/s² | 10ms 최대 jerk m/s³ | 5ms 최대 jerk m/s³ |
| --- | ---: | ---: | ---: | ---: |
| Cruise · 이동 선행차 gap20m | 8.800123 | **5.495618** | 173.610865 | **78.806097** |
| Close-lead · 정지 선행차 gap8m | 7.971694 | **7.751580** | 157.032756 | **154.921008** |

원래 기준인 최대 감속 **6m/s²**, 최대 jerk **10m/s³**를 그대로 사용했다.
5ms cruise의 감속만 실패→통과로 바뀌었으며, cruise jerk와 close-lead의 두 항목은
여전히 실패다. 두 시나리오의 5ms 종합 품질 판정도 failed다. 관측 접촉 수는 네 건 모두 0이며
센서 callback의 완전성이나 실차 무충돌 보장을 의미하지 않는다.

표의 기준군은 이번에 새로 실행한 결과다. 과거 close-lead 저장값 7.870616/157.169649와
이번 기준군 7.971694/157.032756은 같지 않다. 과거 원본·판정은 수정하지 않았으며,
이번 네 번만으로 완전 재현성이나 통계적 신뢰구간을 주장하지 않는다.

## 잔여 실패를 분리한 근거

### Cruise: 관측 범위 부족에 따른 긴급정지 전환이 그대로다

두 설정 모두 첫 세 관측의 지면 관측 범위가 6→5→4m로 변했다. 0.10초에서 reserve를
제외한 가용 정지거리는 2m, 요구 정지거리는 약 2.81m였다. 정책 이유는
`current_stopping_envelope_exceeds_horizon`이며 그때부터 brake=1이다.
모드 분모는 양쪽 모두 pilot_cruise 1, controlled_stop 1, emergency_stop 78이다.
이 시점에는 positive surface가 보고되지 않았다. 이를 장애물 검출이나 완전한 free-space
관측으로 바꾸어 해석하지 않는다. Substep 변경으로 이 정책 경로는 해결되지 않았다.

### Close-lead: 긴급정지 명령이 아니라 저속 정지 말단의 큰 물리 응답이 남는다

두 설정 모두 pilot_cruise 1, controlled_stop 42, hold 37이며 emergency_stop은 없다.
5ms의 1.95초에서 실제 속도는 0.387862m/s, 목표 가속도는 −0.356550m/s²,
요청 brake는 **0.151393**이었다. 다음 2.00초에 속도가 0.000283m/s로 내려가
구간 가속도 −7.751580m/s²가 발생했다. 이어 2.05초 가속도는 −0.005529m/s²로 바뀌며
최대 jerk 154.921008m/s³가 발생했다. Brake=1인 hold는 이보다 뒤인 2.15초다.

따라서 이 spike를 hold 전환이나 긴급정지 명령 탓으로 단정해서는 안 된다.
저속 brake 입력과 실제 차량 응답의 비선형성·정지 전이·제어 모델 불일치를 다음에
다뤄야 한다. 특정 타이어/solver/정지 clamp를 유일 원인으로 확정한 것은 아니다.
불리한 저속·정지 표본을 제외하거나 jerk를 평활화하지 않았다.

## 실행 계약과 구현

- `CarlaRunner.execute_physics_candidate(request, physics_profile)`가 명시적인 별도 진입점이다.
  기존 `execute_with_context()`와 일반 worker의 기본 설정은 유지한다.
- `carla-world-physics-candidate/1`은 50ms 외부 주기, substepping 활성,
  max_substeps=10, substep 상한 10ms 또는 5ms만 허용한다.
- `candidate_input_hash`는 원래 scenario/stack의 `base_input_hash`와 후보 profile을
  함께 고정한다. 전체 실행 intent는 별도로 고정한 source manifest hash도 포함한다.
- Native helper는 기존 engine lock과 store UUID, fresh world 및 source bundle을 확인한다.
  한 요청은 한 번만 실행하며, 원본 보존 및 world 복원 확인 전에는 reservation을 해제하지 않는다.
- 각 결과의 `carla-physics-candidate.json`에 생성 전, 정착 후, 전체 81개 측정 관측의
  world settings readback을 남긴다. 총 83회/실행이며 요청값과 readback이 다르면 실패한다.

원래 시나리오·센서 설정·rig·stack·seed42·평가 기준은 같지만, 초기 단계부터 physics를
바꾸었으므로 계측 F0 상태나 이후 LiDAR/제어 이력이 같다고 주장하지 않는다.
시작 속도의 두 설정 차이는 약 −0.000768m/s이며 강제로 맞추지 않았다.

**기존 초기화인 rest → target-velocity request를 유지했다.** 이번 실행은 이전에 실패한
throttle PI 정속 준비 gate를 통과한 결과가 아니다. F0 가속도=0인 기존 평가 관례도 유지하며,
초기 속도 주입 직후 엔진의 원본 가속도는 vehicle-response에 그대로 남아 있다.

이번 도구는 개발자용 standalone 실행이다. 일반 플랫폼 Job/Run, 공개 physics 설정 UI,
production rollout·승인이나 완전한 durable-worker 장애 복구 인수를 추가한 것은 아니다.
실행 wall은 약 10.48–10.55초/4초 simulation이었다. 양쪽은 같은 standalone 경로이지만
과거 약55초의 durable checkpoint 경로와 비교해 성능 개선률로 제시하지 않는다.

## 검증 및 보존

실제 네 건은 모두 completed이며 **320명령·324관측·332설정 readback**을 보존했다.
오프라인 검토는 원격 회수 hash, intent/result/artifact/release hash, 명령 ACK와 다음 frame을
대조하고 원본 XYZ 속도로 최대 감속·jerk를 다시 계산해 일치를 확인했다.

CARLA 어댑터 시험 **524개 통과·기존 dependency 경고2개**다. 앞선 관련48개와 중복이므로
합산하지 않는다. 작성한 fake-world/계약 시험과 위 실제 엔진 네 건은 별도다.
Chrome에서 네 개 KPI 행, 시나리오별162개 전체 표본, 8개 그래프 경로, 시나리오 전환 및
390px 모바일 viewport를 확인했다. 초기 HTML의 줄바꿈/모바일 표 넘침 오류는 원본과
검사 기록을 남긴 뒤 `report-final`에서 수정했다. Native 결과는 다시 실행하지 않았다.

최종 world·23actor·settings·weather 복원, production 원격 소스92파일과 후보 소스92파일의
hash 일치, worker PID 유지, quarantine 없음, 기존 `.data`749파일 불변을 별도 확인했다.
상세 검증·전체 실험 ID는 아래 JSON을 따른다. 상용 gate와 기존 실패 원본은 유지한다.

## 다음 우선 작업

5ms의 통제 비교는 마쳤다. 같은 고정-throttle/substep 진단을 반복하지 않는다.
다음은 **close-lead의 저속 brake–차량 응답 정합과 정지 말단 제어**를 우선 해결하고,
별도로 cruise의 지면 관측 범위 변동을 센서 입력·sampling·유효 관측 유지 관점에서 수정하는 것이다.
관측되지 않은 공간을 free로 간주하거나 감속/jerk 기준을 낮추는 방법으로 통과시키지 않는다.

## 결과 보기

```bash
open "/home/axiom-user/Documents/ChatGPT/AD E2E Platform/.runtime/physics-live-candidate-20260926-a1/report-final/index.html"
```

[검증 요약·실험 ID·hash](validation/carla-live-physics-candidate-20260926.json).
원본은 `.runtime/physics-live-candidate-20260926-a1/{cruise,close-lead}-{baseline,candidate}/capture/`에 있다.
고정 소스는 같은 폴더의 `sources-capture.tar.gz`이며 분석·전체 표본은 `report-final/analysis.json`이다.
`.runtime`은 Git에 포함되지 않으므로 소스와 원본의 별도 보존이 필요하다.
