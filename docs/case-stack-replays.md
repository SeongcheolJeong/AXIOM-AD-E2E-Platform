# Case의 기록 시계열 스택 재생 · 동일 입력의 판단·요청 비교

Case에서 완료된 recorded stack replay를 기준으로 선택하고, 저장된 다른 stack revision을
**동일한 전체 자차 상태·LiDAR 시계열**에 실행한다. 기존 queue/worker/stack kernel을 사용한다.
출력 throttle/brake/steer는 **차량에 적용하지 않는 open-loop 요청**이다. 차량 궤적, 정지거리,
센서 관측 또는 주행 안전성이 개선됐다는 뜻이 아니다. 학습형 멀티카메라 E2E 정책도 아니다.

## 일반 화면에서 사용

1. Component stacks에서 지원 로그의 기록 재생을 한 번 완료한다. 필요하면 기존 stack editor에서
   별도 revision을 작성한다. 지원 role은 `axiom.EgoState.v1` 자차 상태와 binding된 LiDAR다.
2. Data catalog에서 그 재생에 실제 입력된 자차/LiDAR 메시지의 구간을 Case로 등록한다.
   Case의 모든 메시지가 기준 재생의 정확한 recording/channel/ordinal/hash와 일치해야 한다.
   시간만 가까운 영상 Case를 임의로 연결하거나 새 센서 입력을 추정하지 않는다.
3. Case의 **Configure temporal stack replay**에서 완료 baseline ID, candidate stack ID/revision,
   목적을 입력한다. 목록은 최신 100개 완료 작업 중 같은 recording을 추천하며 더 오래된 정확한 ID도 입력 가능하다.
4. **Preview full temporal sequence**로 전체 tick 수, Case와 겹치는 tick 수, source byte 수,
   binding/stack snapshot과 hash를 확인한다. Preview는 job을 만들지 않는다.
5. 전체 입력 재생과 open-loop 경계를 확인한 뒤 **Submit recorded stack replay**를 누른다.
   Job과 Case 링크는 같은 transaction이다. 응답 유실 재시도는 같은 key/본문을 사용한다.
6. 모든 baseline tick의 상태·정책 mode·제어 요청·입력 불가 이유를 비교한다. baseline/candidate
   trace 링크는 정확한 job을 연다. 실행 중에는 기존 replay cancel 경로로 중단을 요청할 수 있다.
7. **Capture stack replay outcome**으로 terminal manifest와 비교 hash를 Case의 새 개정에 고정한다.
   failed/cancelled 결과도 고정 가능하다. Case 상태와 과거 평가/재시험은 자동으로 바꾸지 않는다.
8. **Download case stack comparison JSON**은 모든 시도와 전체 baseline tick을 포함한다.
   Live 상태가 바뀌면 과거 expected_hash export는409이며 다시 조회해야 한다.

## 동일 입력과 시간의 의미

기준 replay의 **전체 1–128 ego tick**을 원래 순서대로 실행한다. 조사할 한 tick만 골라 실행하지 않는다.
각 실행의 stack session은 처음에 새로 시작하고 그 이후의 state는 기존 kernel 규칙으로 유지한다.
원본 recording/schema, binding/rig, decoder, log-time offset, ego ordinal 순서와 predecessor LiDAR
선택은 기준에서 고정한다. source byte hash는 preview와 실제 worker에서 검사한다.
선택된 unique source payload 합계는128MiB 이내이며 일부를 잘라 통과시키지 않는다.

후보와 기준은 같은 명시적 kernel contract를 사용해야 한다. 비교 변수는 등록된 stack revision이다.
센서 포즈·시간 보정·전처리 의미가 달라졌는데 같은 입력으로 취급하지 않는다. 현재 source 선택이
기준과 달라지면 제출을 거절하고, 실행 중 decoded packet 또는 transformed point bytes가 달라지면
`not_comparable`로 수치 차이를 보류한다. offset/rig/자차·경로 정보는 실측 인증이 아니라 원본 선언이다.

`unknown_stop`은 정상적으로 계산한 보수적 요청일 수 있으며 실행 실패와 구분한다.
선행 LiDAR 없음, 빈 scan, 오래된 scan은 기존 kernel의 입력 불가 근거와 함께 유지한다.
한쪽 실행이 중단·취소되거나 tick이 누락되면 `not_evaluated`로 전체 delta를 보류한다.
반대편 출력으로 누락을 채우지 않고, 모든 baseline tick을 분모에 남긴다.

비교에는 changed request 수만 표시하며 better/worse·collision·TTC·comfort 판정을 새로 만들지 않는다.
같은 기록에서 명령이 달라진 것은 다음 차량 상태가 달라졌다는 폐루프 검증이 아니다.
원래 인지/제동 실패, Case 조사 상태와 ODD·센서 조건 coverage를 승격하지 않는다.

## API·SDK·저장

Prefix `/api/v1`, 기존 인증·Host/Origin 경계를 유지한다.

| API | 역할 |
| --- | --- |
| `POST /cases/{id}/stack-replays/preview` | 전체 source·stack 검사, preview hash, jobs_created=0 |
| `POST /cases/{id}/stack-replays` | expected_revision·preview hash·동의·Idempotency-Key로 기존 queue 제출 및 Case 연결 |
| `POST /cases/{id}/stack-replays/{plan}/capture` | expected_revision·plan hash·Idempotency-Key로 종료 근거 고정 |
| `GET /cases/{id}/stack-replays?revision=N` | 해당 Case 개정의 시도·동일입력 비교, 계산 없음 |
| `GET /cases/{id}/stack-replays/export?revision=N&expected_hash=...` | 같은 내용만 전체 JSON 내보내기 |

SDK: `preview_case_stack_replay`, `submit_case_stack_replay`, `capture_case_stack_replay`,
`case_stack_replays`, `export_case_stack_replays`. 자동 재제출·원격 다운로드·새 scheduler는 없다.

DBschema12를 유지하며 이관은 없다. 새 `axiom-investigation-case/5`에서 stack replay 링크를
추가한다. 기존case/1–4와원본bytes/hash를유지하며조건·일반메모·참조재시험·이미지모델재평가와
공존한다. Case당최대8시도, 기존2MiB Case manifest와16MiB report 한도를유지한다.
구버전 앱으로 새 Case를 편집하지 말고 새 패키지를 사용한다. 먼저 새 복원본으로 인수한다.

## 인수의 범위

검사는 원래 작성된 MCAP와 실제 decoder/stack kernel을 사용한다. 학습 모델이나 CARLA를
실행한 검사가 아니며, authored geometry의 정책 요청 차이를 확인하는 통합 업무다.
별도 baseline/candidate 직접 실행 결과와 Case에서 실행한 결과를 대조한다.
현재 업무의 실측 시각 정합·차량 유효성·전체 멀티센서 정책 지원은 별도 후속 인수 대상이다.
