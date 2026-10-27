# CARLA feedback 주행 · 센서 영상

새 `carla-feedback-sensor` 선택적 handler는 같은 CARLA 차량에서 계획·피드백 제어·
물리 상태 변화·새 RGB 렌더링을 보존하는 `sensor_capture` 실험이다.
기존 정지 차량 capture와 별도의 입력·결과 계약을 사용한다.

## 사용 흐름

Sensor Sim에서 **Feedback 주행 · 카메라 실행**을 선택한다. 실행 시간과 목표 속도를
선택하고 입력 점검 결과의 고정 구성·실행량을 확인한 뒤 실험을 저장·제출한다.
완료된 결과에서는 하나의 시간축으로 원본 RGB, 계획 경로, 실제 주행 경로,
제어 전후 속도, 요청·관측 throttle/brake/steer를 조사한다.

첫 지원 범위는 기존 `Town10HD_Opt`의 지도 경로 기준 제어기,
2–6초·10Hz·640×360 카메라 1–2개다. 계획은 native map waypoint에서 생성하고
제어기는 매 tick의 실제 CARLA 자차 상태를 사용한다. RGB는 같은 차량에서 새로
렌더링한 관측 근거이며 이 기준 제어기의 입력은 아니다. 학습형 카메라 E2E 정책,
RAW/ISP·물리 센서 상관성·충돌 회피 안전 판정은 별도 인수가 필요하다.

## 시간과 원본 계약

입력 frame `f`의 실제 자차 상태에서 계획·제어를 계산하고 제어 acknowledgement를
보존한다. 한 번의 CARLA physics tick 후 response frame `f+1`의 실제 상태와
정확히 같은 frame/timestamp의 RGB를 저장한다. 계획 시점과 영상 시점을 혼동하지 않도록
Viewer에 두 시점을 함께 표시한다. 실제 궤적은 관측 pose이며 계획 궤적은 지도 기준 출력이다.

API는 기존 `/api/v1/experiments`의 capabilities → preview → create → executions →
원본 artifact/export를 사용한다. UI와 SDK의 계약·권한·idempotency·queue가 같다.
입력 schema는 `carla-feedback-sensor-spec/1`, 결과 summary는
`carla-feedback-sensor-result/1`, native manifest는 `carla-feedback-native-result/1`,
trace는 `carla-feedback-trace/1`이다. 기존 `carla-streaming-capture`의 정지 조건을
완화하거나 원래 결과를 주행 결과로 바꾸지 않는다.

원본 ZIP_STORED BGRA8 프레임은 기존 인증된 HTTP Range로 필요한 member만 읽고
manifest의 이름·크기·SHA-256을 확인한 후 Canvas에 표시한다. JSON trace/manifest 역시
불변 artifact의 SHA로 검사한다. 재생은 보존 결과를 조회하는 기능이며 실시간 영상 전송이 아니다.

## 실행 경계

원격 SSH/Python/script 경로는 운영자 설정만 사용한다. Core에는 CARLA Python import가 없다.
기존 `carla` resource 예약을 공유하고 실행 전에도 고정 map/build/설정/script hash와
소유하지 않은 actor가 없는지 다시 확인한다. 이 조건이 바뀌면 실행을 거절한다.
실행이 만든 actor만 정리하고 이전 world settings를 복원한다. 외부 결과·정리 확인이
불확실하면 해당 resource를 격리하며 자동 재시도하지 않는다.

봉인 결과는 입력/configuration 개정·seed·handler 버전·map 및 실행 코드 hash,
fidelity·native frame/time·계획/제어/응답·센서 readback·원본 hash와 정리 기록을 보존한다.
실행 완료와 평가 기준 통과는 별도이며 첫 연결의 주행/센서 품질 판정은 `not_evaluated`다.

공유 host lock은 기존 `8100` worker와 새 feedback 실행이 사용한다. 같은 `8021` 작업공간의
정지 capture는 DB의 `carla` 예약으로 분리된다. 기존 `8020` 정지 capture와 임의 CARLA client는
이 host lock을 사용하지 않으므로 모든 client의 최초 동시 변경을 예방한다고 주장하지 않는다.
현재 단일 운영자가 작업공간 간 시험을 순서대로 실행해야 한다. 실행 중 actor·설정·날씨·map·
frame 간섭은 새 feedback 실행의 실패와 quarantine으로 남기며 오염된 결과를 완료로 게시하지 않는다.
모든 managed client를 하나의 조정 경계에 넣는 작업은 후속 운영 gate다.

## 실제 인수 · 2026-10-08

공유 `default`의 완료 실행은 `job_536461b9400b473f963fc96693d662c4`다.
시작 화면의 **Feedback 주행 영상 재생** 또는 아래 링크에서 바로 연다.

`#sensor-sim?project=default&execution=job_536461b9400b473f963fc96693d662c4`.

| 항목 | 실제 관측 |
| --- | --- |
| Engine / controller | CARLA build `1ae5356` / native map lane-follow reference |
| Handler / 구성 | `carla-feedback-sensor/1.0.1` / configuration revision 1 / seed 42 |
| 주행 응답 | 4초, 10 Hz, 40개 causal 제어 응답 |
| RGB | 전방/후방 각각 40개, 총 80개, 640×360 BGRA8 |
| 수평 이동 / 최대 속도 | 9.6486457176 m / 3.0023632435 m/s |
| 입력 / 결과 | 입력 frame `f` → 제어 ACK → 다음 physics frame `f+1`의 pose·속도·RGB |
| 정리 | 소유 actor 131/132/133의 destroy ACK와 별도 새 frame 127864239에서 실제 부재 확인 |
| Scope | actual CARLA reference feedback / engine RGB, 주행·센서 품질 `not_evaluated` |

첫 실행 `job_1c000e40bb9f4194b632d55bd8c310bf`는 주행/영상 저장 후 이전 snapshot의
actor 목록으로 정리 확인이 실패했다. 실패·불확실성·원래 봉인 SHA를 그대로 보존했다.
운영자가 새 snapshot의 actor 부재·원래 설정·원래 native PID 종료를 확인한 뒤 공유 lock 아래
정확히 그 실행의 reservation만 해제하고 별도 reconciliation을 기록했다. 실패를 성공으로 바꾸지 않았다.

수정한 1.0.1은 callbacks를 닫고 소유 actor의 삭제 ACK를 확인한 뒤 현재 설정이 자신이
소유한 설정인지 확인한다. 외부 actor가 없을 때에만 별도의 단일 정리 tick으로 새 snapshot과
actor 부재를 확인한다. 이 tick은 40개 주행 응답·80개 RGB에 포함하지 않는다.
warm-up 10 tick·주행 40 tick·정리 관측 1 tick은 preview workload에 별도로 표시한다.
현재 설정의 소유권을 잃었으면 다른 client의 설정을 덮어쓰지 않고 quarantine을 유지한다.

Backend/core·adapter 계약 83개, frontend/ZIP 계약 42개와 TypeScript/build를 통과했다.
이 mock 계약 검사와 별개로 실제 Linux CARLA에서 두 번 실행했으며 첫 실패와 두 번째 완료를
모두 보존했다. SDK의 일반 preview도 확인했다. 정상 local/외부 HTTPS 각각 5개 업무에서
처음·중간·마지막 전/후방 픽셀 6개, 해당 시점 계획·요청/ACK/readback·실제 상태,
재생·scrub·deep-link 새로고침·원본 BGRA/trace/manifest/봉인 export 다운로드를 확인했다.
기존 정지 CARLA·Lens·Object Viewer와 360/390/768/1440px 화면도 확인했다.
조회 검증에서 추가 실행/API 쓰기와 브라우저 오류는 0건이다.

외부 gateway의 validated static snapshot만 소유자를 확인해 reload했다.
API/worker는 동일 `8021`·data·instance·권한/token으로 업데이트했다. 외부 gateway·tunnel·
supervisor·비밀번호·기존 cookie·route 권한·2026-10-10 03:45:21 KST 만료를 유지했다.
기존 모든 실행·별도 프로젝트·8020 원본 결과·원래 frontend/dist를 보존했다.
현재 공유 default는 기존 9건 + 첫 실패 1건 + 새 완료 1건, 총 11건이다.

[인수 근거](validation/carla-feedback-sensor-viewer-20261008.json).
실행/원본 JSON·픽셀 기대값·browser 보고서·스크린샷·첫 실패·operator reconciliation·
별도 빌드와 publication은 `.runtime/carla-feedback-20261008`에 보존한다.
API/SDK 예제는 [carla_feedback_workflow.py](../examples/carla_feedback_workflow.py)이며
기본은 preview다. 실제 제출은 명시적 `--submit --idempotency-key`로 요청한다.
