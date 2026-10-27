# 저속 정지 전이: brake 해제 대조 · 2026-09-27

## 이번 결론

기존 1ms·gain4.029874·terminal cap/1을 기준으로 실제 close-lead 세 건을 실행했다.
brake를 해제하면 이전 0.074m/s 급정지는 발생하지 않고 바퀴 회전 API 값이 회복됐다.
계속 해제하면 정지 계획을 완료하지 못하며, 50ms만 해제하고 다시 제동하면 약0.072m/s에서
급정지가 재발했다. **단순 brake 해제는 해법이 아니며 두 probe 모두 채택하지 않는다.**
이것은 제동 입력에 따른 관측 차이다. 설치 엔진의 sticky constraint, wheel integration,
sleep 중 특정 내부 경로가 활성화됐다는 직접 관측은 아직 없다.

| 이번 실제 실행 | 최대 감속 m/s² | 최대 jerk m/s³ | 이동 거리 m | 정책 종료 |
| --- | ---: | ---: | ---: | --- |
| cap/1 기준군 | 1.989260 | 29.439052 | 2.249462 | hold |
| 0.1m/s 미만 지속 해제 | 1.989261 | 35.292332 | 2.273725 | plan expiry → emergency_stop |
| sequence37의 50ms만 해제 | 1.989261 | 28.953878 | 2.254095 | hold |

세 건 모두 실행·정리는 완료됐지만 **jerk≤10m/s³는 모두 실패**다. 감속≤6과 기존 접촉/TTC
기준은 유지한다. 50ms probe의 작은 수치 차이를 주행 품질 개선으로 채택하지 않는다.
이 차례에서는 cruise, 새 차종·속도, 실차 검증 또는 production 배포를 하지 않았다.

## 핵심 관측: 해제와 재제동의 연결

기준군은1.90초의0.073602m/s에서 brake0.073602 뒤1.95초에 거의0이 된다.
새 두 해제 분기는1.85초(sequence37)의 약0.092136m/s에서 brake0을 요청한다.
1.90초에는 속도0.088388m/s, 네 바퀴 회전 API 값은 약0.2397로 회복된다.

지속 해제에서는1.90–2.15초 동안 약0.088m/s가 유지되고 brake readback도0이다.
2.15초에 기존 `plan_expired_before_observed_standstill` guard가 full brake를 요청하고,
2.20초에 정지한다. 정지 기한을 넘긴 것을 통과로 바꾸거나 guard를 완화하지 않는다.

50ms 해제 분기는1.90초에 brake0.088388을 다시 요청하고,1.95초의 속도는0.072398m/s다.
그때 brake0.072398 요청 뒤2.00초에 거의0으로 내려간다. 이때도 controlled_stop이며,
hold 전환은2.15초다. 즉 단발 해제는 급정지를 약한 제동으로 대체하지 못하고 시점을 옮겼다.
Wheel omega는 단위 미검증의 원래 API 값이며, force·torque나 실제 slip ratio로 환산하지 않았다.

## 비교 범위와 정확한 동등성

시나리오·seed42·1ms physics profile·인지·정지 계획·센서 profile은 같다.
기존 rest → target-velocity 초기화도 유지한다. 첫 가속도0인 기존 지표 관례와
원본 XYZ 속도·엔진 가속도는 별도로 보존했다. 정지 표본 제외·평활화는 없다.

기준군과 두 probe의 개입 전37명령은 **완전한 byte 동등성이 아니다**.
최대 throttle/brake/steer 차이는5.067e-7, 최대 속도 차이는5.365e-7m/s이며 모두 기록했다.
반면 두 해제 분기끼리는 관측0..38의 비교한 ego 위치·속도·가속도·회전·각속도 값이 정확히 같다.
이 역시 숨은 엔진 상태 전체의 동등성이나 통계적 독립성을 증명하지 않는다.
지속 해제와 단발 해제는 각각 동결한 source bundle을 사용하며 버전 차이를 숨기지 않는다.

## 설치 엔진 대조: 이전 공개 소스 참고보다 좁혀진 범위

설치 패키지에는 ELF 실행파일과 디버그 파일이 있으며 두 GNU Build ID가
`a489e7257fbe49a8`로 일치했다. 실행파일 SHA와 설치 API 필드 목록을 기록했다.
GDB는 **실행파일만 열어 정적 disassembly**에 사용했다. `run`, `attach`, 실행 중인
CARLA 메모리 수정·binary patch·재시작은 수행하지 않았다.

`UWheeledVehicleMovementComponent::CalcBrakeInput`과
`physx::setVehicleToleranceScale`의 설치 기계어·debug source 위치를 확인했다.
virtual `ProcessControl` 명령은 함수 참조를 해석하지 못했고 이를 전체 경로 확인으로 세지 않는다.
공개 PhysX3.4 소스와 설치 binary가 byte 단위로 일치한다는 주장도 하지 않는다.
현재 Python `VehiclePhysicsControl`에는 sleep/sticky-tire 설정·활성 상태가 없으며,
debug에 나온 Unreal 빌드 소스 경로도 이 런타임에 존재하지 않는다.

따라서 **실제 정지 순간의 sticky 활성, 물리 wheel speed, 적용 brake torque,
내부 brake 입력과 rigid-body sleep 상태를 같은 physics 시각에 기록하는 native 관측**이
다음 차량 모델 작업의 선행 조건이다. 없는 필드를0/false로 꾸미거나 동일 release를 덮어쓰지 않는다.
공개 저속 타이어 제약 코드는 기존 `brake-tail-20260927-a1/references/`에 별도 참고로 남아 있다.

## 코드와 검사

`backend/src/ade2e/terminal_release_probe.py`에 기본 미등록의 두 진단 release를 추가했다.
`terminal-brake-release-probe/1`은 controlled_stop의0.1m/s 미만에서 해제한다.
`/2`는 사전에 고정한 sequence37 한 구간만 해제하며 진입 속도0.08–0.1m/s 밖은 거절한다.
`/2`는 해당 기준 실험에 고정된 개입이지 임의 시나리오에 재사용할 주행 제어기가 아니다.
unknown/emergency/hold guard는 두 release 모두 우선한다. 기본 registry와 기존 cap/1은 그대로다.

실제 **3실행·240명령·243관측·249설정 readback**을 보존하고 원본 hash, ACK,
각 명령의 다음-frame float32 제어 readback과 원본 속도 기반 감속/jerk 재계산을 확인했다.
미실행 intent, 이전 결과와 보고서 복사본은 실행 수에 합산하지 않는다.
어댑터 전체·kernel2·말단 제어 선택 회귀는 **624개 통과, 기존 경고2개**다.
이후 hard-stop 우선 검사3개를 더한 probe 최종 모듈은 **23개 통과**이며 범위가 겹쳐 합산하지 않는다.
Chrome에서3개 결과, 각81개 표본,11개 그래프 경로,390px 화면과 JavaScript 오류0을 확인했다.

사후 engine lock 하의 별도 읽기에서 원래23actor·world/map/settings/weather와
worker PID/start tick·store ID를 확인했다. production source92파일과 `.data`749파일은 불변이다.
DB는74runs·81jobs·349artifacts·11recordings, quick_check 정상·FK오류0이다.
두 실행 소스 archive의 각94파일은 원격/로컬/manifest hash를 대조했다.

## 결과와 다음 작업

비교 화면:
`.runtime/brake-transition-20260927-8cd2a5d8/review-final/index.html`

같은 폴더에 `analysis.json`, `samples.csv`가 있다. 상위 폴더의
`intervention-review.json`, `static-engine-disassembly.txt`, `preservation.json`에
동등성 차이·정적 binary 대조·원본/환경 보존 범위를 분리했다.
[기계 판독 가능한 검증 기록](validation/carla-brake-transition-20260927.json).

단순 이득 변경이나 brake 해제만의 추가 탐색은 여기서 중단한다. 기존 최선 후보는
여전히 jerk 실패 상태로 보존한다. native 정지 상태 관측과 별개의 cruise 지면 관측 범위
문제를 다음 개발 작업으로 구분하며, 이 진단 결과를 신규 제품 기능·주행 품질 통과로 세지 않는다.
