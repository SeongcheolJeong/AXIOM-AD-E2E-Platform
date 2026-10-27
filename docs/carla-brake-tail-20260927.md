# Close-lead 저속 제동 후보 · 2026-09-27

## 결과와 인수 상태

실제 LiDAR → kernel2 → command/ACK → 다음 physics 경로에서 close-lead를 7회 실행했다.
5ms·기존 제어기 기준 최대 감속 **7.751580m/s²**, jerk **154.921008m/s³**에서,
1ms·응답 계수 보정·말단 brake 상한 후보는 **1.989260m/s²**, **29.439052m/s³**였다.
각각 **74.34%, 81.00% 감소**했다. 감속 ≤6은 통과했지만 jerk ≤10은 여전히 실패다.
**품질 통과·운영 배포·실차 보정 완료가 아니다.** 실패 후보와 낮은 속도 표본을 모두 보존했다.

| 실제 close-lead 조건 | 최대 감속 m/s² | 최대 jerk m/s³ | 거리 m | 품질 |
| --- | ---: | ---: | ---: | --- |
| 5ms, 기존 gain8 | 7.751580 | 154.921008 | 2.717715 | failed |
| 5ms, 탐색 gain4.029874 | 8.208345 | 163.708313 | 2.155617 | failed |
| 5ms, gain4.029874 + 가속도 피드백 | 8.239653 | 159.370752 | 2.144546 | failed |
| 1ms, 기존 gain8 | 2.952280 | 59.039135 | 2.806328 | failed |
| 0.5ms, 기존 gain8 | 2.948031 | 58.948662 | 2.807893 | failed |
| 1ms, 탐색 gain4.029874 | 1.991104 | 39.814082 | 2.241901 | failed |
| 1ms, gain4.029874 + 저속 brake 상한 | 1.989260 | 29.439052 | 2.249462 | failed |

네트워크 전달된 원본과 로컬 파일 hash를 대조했으며 XYZ 속도 norm으로 모든 관측의
속도·감속·jerk를 재계산했다. 관측 접촉 수는 전부0, 기록된 최소 TTC는 모두1.5초 이상이다.
이는 callback의 완전성이나 실차 안전 보장이 아니다. 각 조건은 한 번의 실행이며 통계적
신뢰구간을 제공하지 않는다. 이 차례에서는 cruise를 실제로 실행하지 않았다.

## 식별과 제어 변경

이전 `physics-live-candidate-20260926-a1/close-lead-candidate`의 5ms 원본에서
brake≥0.04이고 다음 관측 속도≥0.7m/s인 **26구간(index6..31)**을 사용했다.
0 절편 최소제곱 `감속 ≈ gain × brake`의 탐색 gain은 **4.029873674501462**였다.
절편을 허용하면 gain3.993468, offset0.009991이었다. 기존 선형 가정8과는 차이가 크다.
이것은 부분 제동·특정 속도 범위에서 얻은 탐색값이며, full-brake·저속 전체의 보정값이 아니다.
식별에 사용하지 않은 저속 구간도 품질 평가에서는 전부 포함했다.

gain만 변경하거나 가속도 오차 피드백을 추가한 5ms 후보는 오히려 jerk가 커졌다.
기본 제어기에 적용하지 않는다. `brake_feedback.py`는 미채택 실험 구현으로만 남기며
기본 registry에는 등록하지 않았다. 이 실험의 실행 당시 소스는 별도 archive로 보존했다.

1ms에서는 기존 gain8이 정지 계획 종료 시점(2.15초)에 약0.312m/s로 움직이고 있어
기존 plan-expiry guard가 full brake를 요청했다. 응답 gain 보정으로 이 추종 오차를 줄이면
긴급정지가 아닌 controlled_stop→hold로 끝났지만, 정지 말단의 원본 속도 급락은 남았다.

마지막 후보 `terminal-brake-control/1`은 controlled_stop에서만
`brake = min(기존 brake 요청, 1.0 × 관측 속도[m/s])`를 적용한다.
조향·기존 제동 계획·관측 지면 판단·정지거리 검사·plan expiry·최대 추종오차 조건은 바꾸지 않는다.
force_brake/hold/unknown/emergency에서는 기존 full brake가 우선하며 positive throttle은 없다.
이는 제동을 줄여 정지거리를 늘릴 수 있으므로 기존 거리·만료 guard를 우회해서는 안 된다.
gain 보정 단독 대비 이동거리는 약0.00756m 증가했다.

## 여전히 남은 현상

최종 후보에서도 **1.90초 속도0.073602m/s**, 요청 brake0.073602 이후 다음 1.95초에
속도가0.0000178m/s가 됐다. 이 구간 감속1.471686m/s²가 다음 거의0 가속도로 바뀌며
최대 jerk29.439052m/s³를 만들었다. raw wheel omega 네 값도0으로 바뀌었다.
이때는 controlled_stop이며 hold 전환은2.15초다. hold가 이 spike의 원인이라는 설명은 맞지 않는다.

1ms→0.5ms의 기존 제어기 결과는 큰 추가 개선이 없었다. 이것만으로 모든 속도·모든 제어기의
수치 수렴이나 엔진 내부 solver step 수를 입증하지 않는다. 공개 PhysX3.4 소스는 저속 wheel-lock
정지 제약을 조사하는 참고용으로 보존했지만, **설치 binary와 source 일치를 확인하지 않았으므로
특정 엔진 코드가 유일 원인이라고 결론내리지 않는다.** API의 sticky_control과 tire constraint도 구분한다.

## 구현과 호환성

`carla-world-physics-candidate/1`의 기존10ms/5ms bytes와 production 기본10ms는 유지한다.
명시적인 `/2` 후보만 1ms·max_substeps50 또는0.5ms·100을 허용한다.
외부 제어/관측 간격50ms, 원래 감속/jerk 기준, 기존 rest→target-velocity 초기화는 동일하다.
이것은 물리 throttle 정속 준비 gate 통과가 아니다. F0 가속도=0인 기존 평가 관례도 유지했다.

`TerminalBrakeControl`과 미채택 `AccelerationFeedbackControl`은 기본 registry 미등록이다.
실제 실험은 버전·stack/context/input hash와 source manifest를 고정한 별도 registry에서 실행했다.
특히 a2/a6의 실험 당시 registry 변경은 동결 bundle에만 있으며 운영 소스에 적용하지 않았다.
과거 Run·Job·실패 판정과 원격 production release는 교체하지 않았다.

## 검사와 보존

실제 **7실행·560명령·567관측·581설정 readback**을 확인했다. 미실행 intent와 복사본은
실행 수에 넣지 않았다. 고정 소스6개 archive(92 또는93파일)를 회수해 manifest의 각 hash와 대조했다.
실행별 명령 ACK, 다음 frame, 시간 간격, artifact/result/receipt hash와 지표 재계산이 일치했다.
최종 world/map/settings/weather·원래23actor, worker PID/start ticks, store UUID를 확인했다.
production source92파일과 `.data`749파일은 그대로이며 DB quick_check 정상·FK오류0이다.

CARLA 어댑터 전체+기존 kernel2+가속도 피드백 회귀는 **603통과, 기존 경고2개**였다.
그 이후 추가한 terminal cap의 **10개 별도 계약시험**도 통과했다. 앞선 부분 검사를 합산하지 않는다.
실험 후보의 초기 자동 registry 등록이 기존 kernel1 시험 helper의 암묵 선택과 충돌한 문제는
미채택 후보를 기본 registry에서 제거해 해결했다. 기존 kernel1 기대값을 완화하지 않았다.
이 시험들은 authored fake-world/계약시험이며 native7건과 구분한다.

Chrome에서 실제 결과7행과 모든 선택의81관측,6개 그래프 경로,390px 모바일 표시,
JavaScript 오류0을 확인했다. 운영 플랫폼 전체 UI 인수는 아니다.

## 산출물과 다음 작업

대화형 비교: `.runtime/brake-tail-20260927-a1/review/index.html`.
동일 폴더의 `analysis.json`·`samples.csv`에 원본 출처와 전체 표본이 있다.
실행 원본: `.runtime/brake-tail-20260927-a{1..6}/`의 실행된 case/capture.
소스: 각 폴더 `source-bundle.tar.gz`, 공통 회수 검증 `a1/source-archives.json`.
[실험 ID·hash·보존·검사 요약](validation/carla-brake-tail-20260927.json).
`.runtime`은 Git에 포함되지 않으므로 archive와 원본을 별도로 보존해야 한다.

다음은 새 이득을 무작정 더 찾거나 저속 표본을 제외하는 것이 아니라, 약0.074m/s의
말단 속도 급락을 발생시키는 차량/타이어의 정지 상태 전환을 확인해 제거하는 작업이다.
현재 후보를 운영 기본값으로 올리지 않는다. Cruise의 관측 지면 범위 변동은 별도 미완료다.
