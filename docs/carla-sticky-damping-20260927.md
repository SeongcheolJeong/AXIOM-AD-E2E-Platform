# 저속 타이어 정지 제약의 감쇠 후보 · 2026-09-27

## 결과: 지정한 close-lead 조건의 기존 기준 통과

별도 CARLA 2300에서 기존 1ms 물리 설정, 응답 계수 4.029874, terminal cap/1, LiDAR/kernel2를 유지하고 기준군과 후보를 각각 한 번 완료했다. 종방향 sticky 제약의 활성 규칙은 그대로 두고, 생성된 acceleration-spring 행의 damping만 1000에서 4로 변경했다.

후보의 최대 jerk는 **29.438903 → 3.730783m/s³**로 기존 10 이하 기준을 통과했다. 이는 가상 차량 모델 후보의 **지정 시나리오 통과**이며, 실차 보정·안전 인수·운영 배포 완료가 아니다.

| 기존 4초 평가 | 새 기준군 1000 | 후보 4 | 기존 기준 |
| --- | ---: | ---: | --- |
| 최대 감속 m/s² | 1.989261 | 1.989264 | ≤6 |
| 최대 jerk m/s³ | 29.438903 | **3.730783** | ≤10 |
| 관측 접촉 수 | 0 | 0 | =0 |
| 최소 TTC s | 3.851011 | 3.850988 | ≥1.5 |
| 이동 거리 m | 2.249460 | 2.256095 | 기존 참고 지표 |
| 종합 기록 판정 | failed | **passed** | 기존 판정 유지 |

Jerk는 87.327% 감소했고 이동 거리는 **6.635mm 증가**했다. 후보는 2.00초에 속도 0.029268m/s로 기존 standstill 기준인 0.03m/s 이하를 충족했다. 정지 계획 종료 시점인 2.15초에는 0.005489m/s로 기존 조건을 만족해 hold로 전환했다. 완전한 0 속도와 sleep은 2.55초에 관측됐다. 기준군의 sleep은 2.35초였다.

두 조건 모두 pilot_cruise 1구간, controlled_stop 42구간, hold 37구간이며 emergency_stop은 없었다. 정지 기한이나 허용 속도를 완화하지 않았다. 각 조건을 한 번 실행했으므로 반복 신뢰성을 주장하지 않는다.

## 직접 달라진 정지 전이

| 시각 s | 기준 속도 m/s | 후보 속도 m/s |
| --- | ---: | ---: |
| 1.90 | 0.073602 | 0.073602 |
| 1.95 | 0.000018 | **0.051103** |
| 2.00 | 0.000031 | **0.029268** |
| 2.05 | 0.000033 | **0.016759** |
| 2.10 | 0.000030 | **0.009594** |
| 2.15 | 0.000026 | **0.005489** |

양쪽 모두 1.95초 관측에서 네 바퀴의 종방향 sticky 제약이 활성화돼 있었다. 후보는 제약을 끄거나 차체 속도를 직접 덮어쓰지 않았다. 이번 설치 빌드와 조건에서 해당 감쇠가 과도한 정지 응답에 기여한다는 개입 근거다. 다른 속도·경사·차종·노면이나 모든 PhysX 동작의 단일 원인을 확정하는 결과는 아니다. 실제 brake torque와 constraint impulse는 여전히 미관측이다.

## 변경 범위와 구현

이번 도구는 **읽기 전용이 아니다**. GDB가 직접 생성한 별도 2300 프로세스에서 solver에 전달되기 직전의 임시 `Px1DConstraint.mods.spring.damping` 4바이트만 변경했다. 실행파일과 debug 파일 자체는 바꾸지 않았고, production 2000에는 attach하거나 쓰지 않았다.

대상은 private scene의 **두 차량**에 생성되는 종방향 sticky 행이다. ego만 변경했다고 주장하지 않는다. 생성된 행의 원래 flags, target velocity, impulse limits, stiffness, 행 수와 횡방향·서스펜션 행은 원래 bytes를 유지한다. 이후 동역학이 달라져 자연스럽게 발생하는 상태 차이와 직접적인 필드 수정을 구분한다.

`carla-private-sticky-damping/1`에 1000 기준군과 4 후보를 사전에 고정했다. 0으로 비활성화하거나, 실행 후 최적값을 골라 이번 비교를 덮어쓰지 않았다. ELF SHA와 DWARF의 80-byte row 및 348-byte constant-block을 확인한다. 해당 leaf 함수의 유일한 RET와 포인터 레지스터 불변을 대조하며, 모르는 layout·flag·damping이면 쓰기를 거절한다.

기준군도 같은 hook을 사용하지만 변경되는 data bytes는 0이다. 후보는 **24,812개 감쇠 필드**를 다시 읽어 확인했다. 관측된 모든 행에서 감쇠 이외의 bytes 변경은 0이다.

이 감쇠는 속도 오차에 대한 spring 응답 계수이며, 4라는 값은 탐색용 모델 가설이다. 실차에서 식별한 값이나 업계 공통 권장값이 아니다. 일반적인 물리식 참고와 실제 설치 layout 검증을 구분한다.

참고: NVIDIA PhysX Px1DConstraint documentation (4.1)
`https://nvidiagameworks.github.io/PhysX/4.1/documentation/physxapi/files/structPx1DConstraint.html`

## 입력·출처·시간

Scenario, seed, 인지, 정책, controller 정의, 센서 profile과 기반 source bundle은 정확히 같다. 새 execution ID와 native damping profile을 별도로 고정했다. 초기 속도 차이는 4.60e-10m/s였다. 숨은 엔진 상태 전체나 관측 이력이 완전히 동일하다고 주장하지 않는다.

`source-intent.json`의 `native_candidate_hash`는 기반 input hash, physics profile, native intervention profile과 실행 소스 6개의 hash를 묶는다. **기존 adapter artifact의 base_input_hash만으로는 이 native 변경을 식별할 수 없다.** 따라서 일반 0.8 플랫폼 Run으로 가져오거나, 같은 차량 모델의 회귀 결과로 묵시적으로 취급해서는 안 된다. 공개 API와 차량 모델 revision 연결은 후속 작업이다.

1ms/50 substeps에 대한 기존 client 경고는 양쪽 `driver.log`에 보존했다. 실제 clock 관측은 각각 103frame, 차량 Update 5,150호출이며 frame당 50회였다. 계측 구간의 81개 frame과 정확히 연결했다. 경고 범위 밖인 설정의 범용 지원이나 내부 타이어 반복 횟수를 주장하지 않는다.

## 검증과 실패 이력

완료된 A/B는 **160명령·162개 API 관측**이다. 원본 XYZ 속도에서 모든 감속과 jerk를 재계산했고 기존 지표·기준과 일치했다. Native corrected wheel speeds와 API 값, 명령 ACK와 다음 frame, native stream의 hash chain, intervention 행의 before/after 원본을 대조했다. 기준군 24,176개, 후보 24,812개 종방향 행을 검증했다.

첫 FinishBreakpoint 방식의 기준 실행 1건은 계측 부담으로 중단했고 `not_evaluated` 상태로 보존했다. SIGALRM 종료 경로가 기록한 deadline 문구와 수동 중단 요청 기록을 함께 남겼다. 이는 제동 품질 실패와 다르다. 단일 RET 계측으로 바꾼 뒤 새 ID와 폴더에서 A/B를 실행했다. 이전 실행의 초기 `cleanup_uncertain`은 사후 조회에서 관련 PID와 포트가 모두 없음을 확인했으며, 원래 종료 보고서는 수정하지 않았다.

선택 회귀시험은 **106개 통과**, 기존 의존성 경고 2개다. 전체 플랫폼 시험을 다시 수행한 결과는 아니다. Chrome에서 결과 2개, 원본 162행, 그래프 8개, 정지 구간 확대, 390px 모바일 표시와 페이지 오류 0을 확인했다.

계측용 GDB와 CARLA는 모두 종료했다. 운영 소스 92파일, world/map/23actor/settings/weather, 프로세스와 store UUID, 기존 데이터 749파일은 불변이다. DB 무결성과 외래키 검사도 정상이었다.

## 산출물과 다음 단계

비교 화면: `.runtime/sticky-transition-20260927-e9fd69ab-r2/review/index.html`

같은 폴더의 `analysis.json`, `samples.csv`에 전체 표본과 검증 결과가 있다. 원본은 상위 폴더의 `sham/capture`, `candidate/capture`에, 실행 소스·분석기·원래 bundle은 `reproducible-sources.tar.gz`에 있다.

[검증 요약·실험 ID·hash](validation/carla-sticky-damping-20260927.json)

다음은 이 차량 모델 변경을 명시적인 native 설정/revision으로 연결하고 추가 속도·경사·cruise에서 같은 기준으로 평가하는 것이다. GDB 실험 방식을 production 배포로 간주하지 않는다. 기존 production 10ms, 제어기와 과거 실패 판정은 유지한다. Cruise의 지면 관측 범위 변동 문제는 별도로 남아 있다.
