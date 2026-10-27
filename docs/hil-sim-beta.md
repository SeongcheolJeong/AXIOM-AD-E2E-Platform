# HIL Workbench · 소수 초대형 Software DUT 베타

개인 발표 이후의 목표는 사용자가 자신의 고장 조건을 저장하고, 실행 결과를 다시 열어
관찰과 문제를 남기는 업무를 끝내는 것이다. 첫 대상은 Engineer 권한의 내부 또는 초대 사용자
3–5명이다. 실제 ECU 연결과 하드 실시간은 별도 후속 단계다.

## 현재 기능 묶음

`HIL → 시험 관리 → 조건 저장 → 실행 준비 확인 → A/B 제출 → 이력 → 관찰/이슈 → JSON 내보내기`

- 시험 이름·목적·지연/단절 값과 고장 시작·종료 시각을 저장한다. 수정은 새 개정으로 남기고 복제는 별도 시험을 만든다.
- 이름 있는 DUT 임계값·회복 프로필을 저장·개정·복원하고, 두 프로필을 시험에 고정해 같은
  고장 조건의 DUT A/B를 비교한다. 프로필의 최신 개정이 바뀌어도 저장 시험은 원래 개정을 유지한다.
- 기존 앞차 정지와 고정된 감속·정지 후 재출발 중 장면을 선택한다. 선택한 속도 일정의
  개정/hash를 시험·실행에 고정하고 작성 시각과 실제 앞차 응답 사건을 구분해 조사한다.
- `순차 실행`에서 저장된 시험 개정 3–5개와 순서를 고정한다. 서버가 한 조건씩 실행하며
  화면을 벗어나도 진행 기록을 다시 열 수 있다. 중단·실패 뒤에는 미완료 조건만 명시적으로 재개한다.
- 시나리오 개정·두 source hash·seed·adapter 버전과 당시 프리셋 binding을 고정한다.
  프리셋의 대표 결과가 바뀌어도 저장 시험은 원래 입력을 유지한다.
- 저장에는 native 연결이 필요 없다. 실행 준비 확인에서만 현재 엔진을 점검한다.
- 실행은 기존 Experiment 큐를 사용한다. 제출한 실행을 시험의 정확한 개정에 연결하며,
  연결 응답이 유실되면 같은 실행 ID로 복구한다. 재시도를 새 실행으로 바꾸지 않는다.
- 완료·실패·취소 이력을 남긴다. 기존 결과를 연결하려면 source·seed·fault·adapter와
  비교 종류·DUT 프로필·장면 일정의 개정/hash가 일치해야 한다.
- A/B 영상의 응답 시점에 관찰 또는 이슈를 남긴다. 기록에는 선택한 쪽과 원본 실행 식별자,
  hash 및 보존된 trace 표본이 연결된다. 관찰은 사용자의 해석이며 자동 안전 판정이 아니다.
- JSON으로 시험 개정·실행 영수증·관찰을 내려받는다. 영상과 전체 raw trace는 기존
  인증된 실행 artifact 화면에서 확인한다. 내보내기는 공개 게시 또는 재배포 승인을 뜻하지 않는다.
- 순차 실행 결과표는 원본 응답 trace의 DUT 열화·추적 회복, 최대 입력 age·제동을 요약하고
  조건별 A/B·관찰로 연결한다. 목록 내보내기는 고정 시험과 모든 시도·원본 실행·관찰을 포함한다.

이 묶음은 기존 18초 Town10 base 장면의 앞차 행동과 지연·단절 fault의 시험 관리를 확장한다.
동일한 참조 Software DUT의 정책 비교를 지원한다. 감속·정지 후 재출발은 앞차 속도 일정의
제한된 변경이며 새 지도나 독립된 장면 다양성을 확보한 것은 아니다. 다음은 자체 DUT 실행
파일 연결이며 다른 지도·행동군과 실제 ECU 연결은 후속이다.

## 베타 시작 전에 준비할 것

| 준비 항목 | 첫 베타에서 필요한 수준 | 현재 경계 |
| --- | --- | --- |
| 참가자 업무 | 조건 저장·복제·A/B 조사·관찰 내보내기 | 3–5명에게 동일한 짧은 업무를 제공 |
| 계정·권한 | 참가자별 프로젝트와 Engineer 자격, 읽기 검토용 Viewer | 공통 프로젝트 ACL 재사용; 실제 참가자 초대는 별도 |
| 실행 자원 | 전용 CARLA 시간대 또는 별도 GPU, 작업당 한 A/B | 기존 발표 엔진을 자동 중지/전환하지 않음 |
| 저장·복구 | 원본 결과 보존, 여유 공간, 실패 실행 재조사 | 보존 데이터 자동 삭제 금지; 보존 기간·운영 담당자 결정 필요 |
| 문제 접수 | 실행·개정·시점에 연결한 관찰/이슈와 JSON | 연락처·비밀키를 메모에 넣지 않음 |
| 접속 | 우선 같은 환경의 로컬 초대형 시험 | 외부 HTTPS·공개 호스팅·기존 서비스 반영은 별도 운영 작업 |
| 완료 기준 | 실제 참가자 5명 중 4명 이상이 도움 없이 업무 완료 | 개발자 검사 통과와 사람의 베타 인수를 구분 |

## 참가자 업무 · 15–20분 목표

1. 자신의 프로젝트 자격으로 접속해 HIL의 `시험 관리`를 연다.
2. 지연 프리셋에서 질문을 이름으로 붙이고 300ms 조건을 저장한다.
3. 시험을 복제해 400ms로 바꾸고 저장한다. 새로고침 후 두 조건이 유지되는지 확인한다.
4. native 실행 시간대에는 준비 확인 후 A/B를 제출한다. 그 외에는 정확히 일치하는 보존 결과를 연다.
5. 입력 age·제동·속도 차이를 조사하고 선택 시점에 관찰 또는 이슈를 저장한다.
6. 이력에서 해당 개정을 다시 열고 JSON을 내려받는다. 막힌 단계와 기대했던 동작도 기록한다.

300ms/400ms를 반복한 횟수는 독립 장면의 수 또는 검증 커버리지가 아니다.
CARLA 객체 GT가 Software DUT에 들어가며 RGB는 관측 영상이다. 실제 ECU·센서 인지 성능·
wall-clock 실시간·차량 안전성은 이 베타의 검증 대상에 포함하지 않는다.

## 이어서 빠르게 구현할 순서

1. **고장 시간 편집 완료:** 지연·단절의 단일 연속 구간 시작·종료를 UI/API/SDK에서 편집·저장하고 실제 trace와 나란히 표시한다. 반복·여러 구간 패턴은 후속이다.
2. **DUT 프로필 완료:** 입력 age 임계값·회복 조건을 개정으로 저장하고 같은 고장의 DUT 프로필 A/B를 실행한다. 지연·단절 native 실행, 브라우저 조사·관찰 저장·JSON 다운로드와 SDK export를 확인했다.
3. **작은 반복 실행 완료:** 선택한 3–5개 조건의 서버 순차 실행·중단·명시적 재개·결과 요약을 연결했다. 실제 3조건 완료와 실행 중 취소·미완료 조건만 재개를 확인했다. 접수 실패·경합·불확실한 결과 차단은 작성한 handler 검사와 구분한다.
4. **장면 확대 완료:** 기존 Town10 base의 앞차 감속·정지 후 재출발 일정을 UI/API/SDK에 연결하고 두 장면의 실제 native A/B를 확인했다. 새 지도나 범용 장면 작성은 후속이다.
5. **다음 · 사용자 DUT:** 고정 JSON-lines 계약의 자체 실행 파일 등록·handshake·timeout·로그 수집.

각 묶음은 편집→실제 실행→A/B 또는 관찰 저장을 완결하고 다음으로 넘어간다.
새 고장·DUT·장면으로 실행 동작이 바뀔 때 대표 native 실행 한 건을 확인한다.
광범위한 회귀·정밀 물리 상관 연구를 빠른 기능 구현의 선행 조건으로 확대하지 않는다.

실제 ECU 단계는 대상 장비·I/O·주기·reset·latency 계약을 확보한 후 한 장비/한 transport부터
별도로 진행한다. 범용 CAN/Ethernet 연결, PTP, raw/광학/RF 주입은 이번 작은 베타와 분리한다.

## API와 SDK

`hil-delay`는 기존 `delay_ms`에 선택적 `start_s`, `duration_s`를 추가한다.
`hil-dropout`은 기존 `duration_s`에 선택적 `start_s`를 추가한다.
시작은 1–17초, 지속은 0.5–4초이며 둘 다 정확한 0.5초 단위, 종료는 17.5초 이하다.
UI에서는 시작과 종료를 편집한다. 시작 변경 시 지속을 유지하고 종료 변경 시 지속을 계산한다.
프리셋의 `fault_timing: hil-fault-timing/1` 메타데이터가 이 범위와 기본값을 제공한다.
생략한 기존 지연 기본 구간은 3–7초, 단절 기본 시작은 3.5초로 유지한다.
이전 개정의 생략된 키·hash를 다시 쓰지 않으며 preview는 저장된 experiment를 그대로 사용한다.
예: 지연 `parameters={"delay_ms":400,"start_s":2,"duration_s":3}`.

- `/api/v1/hil-sim/tests`: 이름 있는 시험의 목록·개정 저장.
- `/hil-sim/tests/{id}`: 정확한 시험 개정 조회.
- `/hil-sim/tests/{id}/preview`: 저장 개정/hash를 지정한 native 준비 확인.
- `/hil-sim/tests/{id}/executions`: 기존 Experiment 실행 연결·이력.
- `/hil-sim/observations`: 실행·시점·개정에 연결한 관찰/이슈.
- `/hil-sim/tests/{id}/export`: 시험·실행·관찰 JSON.

Python SDK에는 `hil_sim_tests`, `save_hil_sim_test`, `hil_sim_test`,
`preview_hil_sim_test`, `attach_hil_sim_test_execution`, `hil_sim_test_executions`,
`create_hil_sim_observation`, `hil_sim_observations`, `export_hil_sim_test`가 있다.
`execute_hil_sim_preview`는 시험 preview도 지원하며 정상 Experiment 제출 뒤 개정에 연결한다.
연결 응답이 불확실하면 `HilExecutionLinkError.execution_id`를 보존해 연결만 다시 시도한다.

### DUT 프로필 계약

`/hil-sim/dut-profiles`의 GET/POST와 `/{id}?project_id=...&revision=...` GET으로 프로필을
목록 조회·저장·복원한다. 이름과 목적, 입력 age 임계값 `max_object_age_ms`(정수 50–1000,
50ms 단위), 연속된 새 입력 회복 횟수 `recovery_fresh_steps`(정수 1–10)를 저장한다.
같은 프로필의 수정은 새 개정이며 기존 개정은 유지한다. 저장에는 native 연결이 필요 없다.
SDK 메서드는 `hil_sim_dut_profiles`, `save_hil_sim_dut_profile`, `hil_sim_dut_profile`이다.

시험 저장의 선택 항목 `dut_profiles`는 `baseline`과 `faulted` 각각의 `id/revision/sha256`
참조를 받는다. 서버는 프로젝트 권한과 원본 hash를 확인하고 전체 프로필 기록을 시험과
실행 입력에 고정한다. 이후 프로필의 최신 개정이 바뀌어도 저장 시험의 정책은 바뀌지 않는다.
일반 Experiment API도 동일한 원본 프로필을 확인한다.

프로필 두 개를 지정하면 비교 종류는 `same_fault_dut_ab`다. A와 B에 같은 장면·seed·고장
시간과 크기를 적용하고 정책만 바꾼다. 폐루프의 주행 반응이 달라지면 이후 객체 관측값도
달라지므로 두 실행의 모든 입력 바이트가 같다는 뜻은 아니다. 저장 슬롯 이름
`baseline/faulted`는 유지하지만 화면에서는 **DUT A/B**로 표시한다.
생략한 기존 시험은 계속 `same_scene_fault_ab`, 기본 250ms·새 입력 3회 정책을 사용한다.
기존 개정·receipt·관찰의 hash를 다시 쓰지 않는다.

### 고정 장면 선택 계약

`GET /hil-sim/scenes?project_id=...`와 SDK `hil_sim_scenes`는 고정 카탈로그를 제공한다.
`lead_deceleration`은 입력 4초에 목표 속도를 2→0.5m/s로 바꾸고, `lead_stop_go`는
입력 4초 정지·8초 2m/s 재출발을 요청한다. 시험 저장과 preset preview의 선택 항목
`scene_profile`은 정확한 `{id,revision,sha256}`를 받는다. 응답과 experiment input에는
`hil-scene-profile/1` 전체 기록을 고정하며 이전 시험의 생략/null은 기존 4초 정지를 유지한다.

원래 `source.scenario_ref`와 두 hash는 base 시나리오를 계속 식별한다. 변경된 앞차 일정은
별도 descriptor hash와 전체 input hash에 연결되고 고장 시간·DUT 프로필은 독립적으로 유지한다.
snapshot·양쪽 summary·trace의 장면 정보가 저장값과 다르면 실행 연결·관찰·묶음 요약을 거절한다.
입력 일정과 물리 tick 뒤 실제 응답 시각을 구분하며, 목표 속도만으로 도달을 추정하지 않는다.
[API 계약](api-contract.md#hil-fixed-scene-selection--2026-10-11) ·
[SDK 저장·미리보기 예제](../sdk/python/README.md#hil-장면-선택).

## 운영 상태와 인수 근거

### 순차 실행 계약

`시험 관리 → 순차 실행`에서 저장된 시험 개정 3–5개를 선택하고 순서를 고정한다.
같은 시험의 서로 다른 개정은 선택할 수 있지만 동일한 개정을 중복 선택하지 않는다.
목록은 이름·목적·전체 시험 원본과 DUT 프로필 개정/hash를 보존한다. 이후 목록을 바꾸려면
복제해 새 목록을 만든다. 목록 저장에는 엔진이 필요하지 않다.

준비 확인은 아직 완료하지 않은 조건의 입력을 검사한다. 시작은 확인한 입력 hash를 다시
검증하고 고정한 뒤, 서버 worker가 한 조건씩 기존 Experiment 큐에 제출한다. 브라우저의
조회나 새로고침은 작업을 제출하지 않는다. 이전 조건이 완료돼야 다음 조건을 제출한다.
자식 실행 생성·시험 연결·목록의 시도 기록은 같은 DB 트랜잭션으로 저장한다.

중단 요청은 현재 실행의 취소를 요청하고 뒤의 조건을 제출하지 않는다. 실제 종료 확인 전에는
다시 실행할 수 없다. 실패도 목록을 멈추며, 사용자가 준비 확인 후 **미완료 조건 다시 실행 후
이어가기**를 선택해야 새 시도를 만든다. 완료된 조건은 건너뛰고 이전 취소·실패 기록을 보존한다.
결과가 불확실하거나 자원이 정리되지 않은 실행은 자동 또는 묶음 재개로 다시 제출하지 않는다.
이때 원본 실행 기록과 자원 상태를 먼저 확인한다.

결과표의 DUT 열화 진입·추적 회복 시각과 최대 입력 age·제동은 해시를 확인한 원본 응답 trace에서
계산한다. 제동 최댓값에는 고장 외의 장면 반응도 포함된다. 해당 표본은 측정된 Software DUT
시뮬레이션 반응이며 안전 통과 판정이 아니다. 조건별 실제 A/B·관찰 화면으로 이어지고,
내보내기에는 목록·시험·모든 시도·원본 실행 hash·해당 실행의 관찰을 포함한다.

API 경로는 `/api/v1/hil-sim/batches`다.

- `POST /batches`: `project_id`, `name`, `note`, 순서 있는 `tests: [{id, revision, sha256}]` 저장.
- `GET /batches`, `GET /batches/{id}`: 프로젝트 목록과 현재 진행 기록 조회.
- `POST /batches/{id}/preview`: `project_id`, `expected_revision`으로 미완료 조건의 준비 확인.
- `POST /batches/{id}/start`, `/resume`: 같은 개정과 `preview_hash`로 시작 또는 명시적 재개.
- `POST /batches/{id}/cancel`: 현재 상태 개정을 지정해 중단 요청.
- `GET /batches/{id}/export`: 목록·조건별 실행·관찰 JSON.

저장·시작·중단·재개는 `Idempotency-Key`를 요구한다. 응답을 받지 못하면 같은 본문과 키로
확인하며 새 목록이나 실행을 만들지 않는다. `revision`과 `sha256`은 보존된 진행 상태의 개정과
hash이고 `definition_sha256`은 처음 고정한 시험 목록의 hash다.

SDK 메서드는 `create_hil_sim_batch`, `hil_sim_batches`, `hil_sim_batch`,
`preview_hil_sim_batch`, `start_hil_sim_batch`, `cancel_hil_sim_batch`,
`resume_hil_sim_batch`, `export_hil_sim_batch`다. SDK 자체는 자식 실행을 제출하거나
자동 재시도하지 않는다. 정상 worker와 HIL 전용 베타 worker가 같은 서버 조정 함수를 사용한다.

### 앞차 감속·정지 후 재출발 · 2026-10-11

기존 Town10 base와 두 source hash를 유지한 채 고정 앞차 행동 2종을 선택·저장하고
native A/B로 실행했다. 원본 trace의 양쪽 앞차 응답은 다음과 같다.

| 장면·고장 | 완료 실행 | 실제 앞차 응답 |
| --- | --- | --- |
| 감속 · 400ms 지연 3–7초 | `job_12d1d59d58024dd8bd1d1257e9701f26` | 입력 4초 목표 0.5m/s, 응답 4.15초에 속도 ≤0.75m/s |
| 정지 후 재출발 · 단절 8–9초 | `job_1afbb7648d694d14b805441d6da4ae03` | 입력 4초 정지·8초 재출발, 응답 4.20초에 ≤0.2m/s·8.45초에 >0.5m/s |

각 실행의 A/B는 제어 360개·RGB 360장·누락 0이며 종료 정리를 확인했다. 이전 15건의
영수증 bytes를 보존했다. 감속은 정상 브라우저에서 선택·저장·준비 확인·실행·관찰·JSON
내보내기를 확인했고, 재출발은 SDK 저장·미리보기·실행으로 완료했다. 이후 정상 브라우저
이력에서 재출발 A/B·native RGB를 열어 8.45→15.55초 재생, 8.45초 관찰 저장과 정확한
시점 URL 새로고침을 확인했다. 두 장면의 브라우저 JSON은 `exported_at`을 제외하면
SDK export와 일치하며 원본 실행·장면·관찰 연결을 유지한다. 브라우저 오류는 0건이다.
기존 정지·감속·재출발의 3조건 목록 `hilbatch_889c4d283fb7437e981c7f0170c1378e`를
UI에서 저장·새로고침했다. 이 목록은 시도 0건인 작성 인수이며 새 native 묶음 실행은 아니다.

native/adapter 집중 검사 191개, backend/SDK 216개, frontend 81개와 production build를
통과했다. 작성한 handler의 혼합 장면 묶음 검사는 실제 새 장면 묶음 실행과 구분한다.
로컬 8075는 같은 DB·자격을 유지하며 새 native 소스는 독립 디렉터리에 배치했다.
CARLA 2100은 재시작하지 않았다. 현재 서버의 기동 명령은 아래와 같다. 이미 실행 중이면
중복 기동하지 않는다.

```sh
.venv/bin/python -B .runtime/hil-beta-20261011/launch-scenes.py
```

두 실행 뒤 GPU 조회는 프로세스별 595.84 환경에서 정상이었고 2100의 PID/시작 식별자는
유지됐다. 2000·2160은 중지 상태다. 로컬 여유 공간은 2,228,449,280 bytes(약 2.08GiB)로,
다음 native 실행 전 출력·보존 용량을 다시 확인해야 한다. 이번 작업에서 자료를 삭제하지 않았다.

④ 장면 확대의 이번 범위는 기존 base의 제한된 앞차 행동이다. 독립 지도·장면 다양성이나
안전 커버리지로 세지 않는다. 다음은 사용자 DUT이며 실제 참가자 업무 인수·공개 운영·
실제 ECU·하드 실시간은 남아 있다. [인수 근거](validation/hil-scenes-20261011.json).

### 3조건 순차 실행 · 2026-10-11

정상 Engineer 브라우저에서 저장 시험의 선택·순서 변경·목록 저장·복제·새로고침과 2조건
목록의 저장 차단·동일 개정 중복 추가 거절을 확인했다. 준비 확인·시작 후 배치 화면을 벗어나도 서버가 진행했고,
다시 열어 중단·종료 확인·재개를 수행했다. 목록
`hilbatch_7c73213b9dc64d9a97e3f8e9faaaeb87`은 r14에서 아래 3조건을 완료했다.

| 조건 | 완료 실행 | 원본 응답 trace에서 관측한 DUT 반응 |
| --- | --- | --- |
| 300ms 지연 3–7초 · 정상/고장 | `job_d125d2adb9f2423491e373104ee7adbe` | B 열화 3.30초·추적 회복 7.15초; A 추적 유지 |
| 400ms 지연 2–5초 · 정책 A/B | `job_7e2f2155aa81484daa1ed6e90b62be29` | A 열화 2.30초·추적 회복 5.15초; B 추적 유지 |
| 단절 2–3초 · 정책 A/B | `job_ac4e2107d5144b5cb899199ef34d733b` | A 열화 2.30초·회복 3.15초, B 열화 2.55초·회복 3.40초 |

정책 비교는 A 250ms/새 입력 3회와 B 500ms/8회다. 각 완료 실행의 양쪽에서 제어 360개·
RGB 360장·누락 0을 확인했다. 총 5시도에는 실행 중 취소 2건이 포함되며 실패나 완료로
덮어쓰지 않았다. 첫 취소는 아직 완료 조건이 없을 때 요청했다. 재개 후 앞의 두 조건을
완료하고 세 번째 실행을 다시 취소했다. 종료·유휴 확인 후 수정 API로 재시작했으며,
다음 preview의 미완료 조건은 1개였다. 세 번째만 새 시도로 완료하고 앞의 두 실행은 유지했다.
다음 조건의 접수 시각이 이전 조건의 종료 뒤임을 확인했다.

기존 영수증 10건의 bytes와 catalog 56개 행을 보존했다. 종료 후 자원 idle·잔여 객체 0과
프로세스별 환경의 GPU 조회 성공을 확인했다. 기존 엔진 2100의 PID·시작 식별자와
native source는 그대로다. 400ms 결과의 2.30초에 관찰
`hilnote_db414759711042eaad9b9d9944641c51`을 저장하고 두 원본 응답 표본 연결을 확인했다.
결과 이동·새로고침에서도 정확한 새 실행과 2.30초가 유지됐으며 새 A/B 영상 재생·일시
정지를 확인했다. 브라우저에서 내려받은 JSON은 `exported_at`을 제외하면 SDK export와
같고 이 묶음에 속한 새 관찰 1건만 포함한다. 최종 브라우저 오류는 0건이다.

집중 검사 247개 후 관련 backend 72개를 다시 통과했다. 마지막 수정은 완료 영수증의
반영을 다음 접수와 분리해, 접수 실패가 앞선 완료 기록을 되돌리지 않도록 한 것이다.
두 트랜잭션 사이의 취소도 다음 접수를 차단한다. 이 경로는 작성한 handler 검사로
재현했으며 실제 엔진 장애를 주입한 인수는 아니다. 최종 세 번째 native 조건은 수정 API로
완료했다. frontend 73개 후 관련 19개와 production build를 확인했다. 재검사 수는 중복 포함이다.
실제 native 인수는 3조건이며 5조건 경계와 불확실한 결과의 재개 차단은 계약 검사 근거다.

8075는 같은 DB·자격을 유지했다. 아래는 순차 실행 인수 당시 명령이며 현재 기동에는
위 장면 확대 절의 `launch-scenes.py`를 사용한다. 공유 GPU·2100의 배정과 별도
프로세스별 NVIDIA 환경은 그대로다.

```sh
.venv/bin/python -B .runtime/hil-beta-20261011/launch-sequential.py
```

이는 18초 Town10 앞차 정지 장면의 Software DUT 반응이다. 당시 다음 단계였던
④ 장면 확대는 위 별도 인수로 이어진다. 실제 참가자 업무 인수·공개 호스팅·사용자 DUT·
실제 ECU·하드 실시간은 남아 있다.
[구현·native·브라우저 근거](validation/hil-sequential-20261011.json).

### DUT 임계값·회복 프로필 · 2026-10-11

8075의 정상 Engineer 화면에서 A 250ms/회복 3회 r1과 B 500ms/회복 8회 r2를 만들고,
B의 이전 500ms/회복 3회 r1이 그대로 보존됨을 확인했다. 프로필 작성·개정 저장·범위 오류 차단과 두 프로필
선택 후 기존 시험 `hiltest_baf632049ae34183ba20c17cdf807b20`을 r4로 저장했다.
이후 프로필을 수정해도 저장 시험은 원래 프로필 개정/hash를 참조한다.

400ms·2–5초 지연의 `job_3acf348b96584fafbd8cbba89ff0b75c`을 정상 브라우저의 준비 확인과
제출로 완료했다. 3.55초에는 입력 age가 양쪽 400ms였고 A는 정지·제동 100%, B는
2.530m/s·제동 0%였다. A는 2.30초 degraded에 진입해 5.15초 tracking으로 회복했고,
B는 tracking을 유지했다. 양쪽 영상 재생·3.55초 관찰 저장·새로고침을 확인했으며
브라우저 오류는 0건이다.

SDK로 시험 `hiltest_1d0938aca0e142eeae3708c3a3870ff1` r1을 만들고 같은 두 프로필의
2–3초 단절을 실행해 `job_9b19439acaf845a3bca67caefa032b58`을 완료했다. degraded 진입은
A 2.30초/B 2.55초, tracking 회복은 A 3.15초/B 3.40초였다. 두 작업의 각 A/B는 제어
360개·RGB 360장·누락 0이며 종료 정리를 확인했다. 기존 8건의 manifest hash를 보존한다.
브라우저 이력에서 단절 결과를 열어 3.20초의 A tracking·제동 0%, B recovering·제동 100%를
확인하고 관찰 `hilnote_646a64e0673941ff916d9ce8665965fa`를 저장했다. 지연의 3.55초 관찰은
`hilnote_c94c7f7498264fd99b742df2b93db6ed`다. 두 시험의 SDK export에서 프로필·실행·메모·
표본 연결을 확인했다. 브라우저에서 내려받은 지연 시험 r4 JSON은 `exported_at`을 제외하면
SDK export와 일치하며 전체 프로필·실행 ID·메모·표본 식별자가 같다.

backend/SDK/native 통합 검사 273개, 프런트엔드 검사 62개를 통과했다. 마지막 시간 표시
문구 수정 뒤 관련 13개와 production build를 다시 통과했다. 이는 참조 Software DUT의
정책 반응을 확인한 결과이며 실제 ECU·센서 인지 성능·차량 안전성의 검증이 아니다.

8075는 같은 DB·자격을 유지해 새 UI/API로 갱신했다. native 소스는 별도
`axiom-hil-profiles-20261011` 디렉터리에 배치했으며 기존 엔진 2100을 재사용했다.
아래는 프로필 인수 당시 실행 명령이다. 현재 서버는 위 장면 확대 절의 기동 명령을 사용한다.

```sh
.venv/bin/python -B .runtime/hil-beta-20261011/launch-dut-profiles.py
```

이 인수 당시 다음 단계였던 ③ 순차 실행은 위의 별도 기록으로 이어진다.
[구현·native·브라우저 근거](validation/hil-dut-profiles-20261011.json).

### 고장 시간 편집 · 2026-10-11

8075의 정상 Engineer 화면에서 기존 시험의 3–7초 구간을 2–5초로 바꿔 r3으로 저장하고,
새로고침·실제 준비 확인·새 native A/B 실행을 완료했다. 실행은
`job_1d103b20361b4891aaa08d6dfa60bfe7`이다. 고장 입력은 60개 스텝(2.00–4.95초)에
적용됐고 응답 이벤트는 2.05/5.05초, DUT tracking 회복은 5.15초다.
양쪽 각각 제어 360개·RGB 360장·누락 0이며 실행 후 자원은 idle, 잔여 객체는 0이다.

최종 backend/SDK/native 계약 검사 194개, 프런트엔드 검사 35개와 production build를 통과했다.
정상 브라우저에서 범위 오류 차단, 저장·복원, 새 실행, A/B 영상·신호, 관찰 북마크와 console
error 0을 확인했다. SDK export에는 r3·새 실행·원본 trace 표본이 연결된다.
관찰 중 발견한 3.55초의 부동소수점 표본 선택 오류는 영상/신호/메모에 동일한 절대 오차
1e-9초를 적용해 수정했다. 초기 3.50초 연결 메모를 보존하고 정확한 3.55초 정정 관찰을 추가했다.

작성 구간과 보존된 실행 구간을 별도로 표시한다. 작성안을 2–5초로 바꿔도 이전 r2 영상은
3–7초로 표시되며 새 결과를 열 때만 2–5초로 바뀐다. 이전 실행 7건의 manifest hash를 유지한다.
새 native 소스는 별도 원격 디렉터리에 배치했고 기존 엔진 2100과 owner/spool을 재사용했다.
8075는 같은 DB·자격으로 새 UI/API를 반영했다. 이 인수 당시 실행 명령은 다음과 같았으며,
현재 서버 기동 명령은 위 장면 확대 절을 따른다.

```sh
.venv/bin/python -B .runtime/hil-beta-20261011/launch-fault-timing.py
```

이 인수 당시 DUT 정책은 age 250ms·fresh 입력 3회였으며 프로필을 지정하지 않은 기존
시험의 기본값으로 유지한다. GPU 배정과 복원 절차는 아래와 같다.
[구현·native·브라우저 근거](validation/hil-fault-timing-20261011.json).

### Native 복구와 현재 GPU 배정

2026-10-11 사용자 요청으로 엔진·GPU 상태를 조사하고8075에서 새 native 실행을 추가했다.
현재는 아래의 최초 offline 인수 시점과 다르게 **native 옵션과 HIL worker가 활성화**돼 있다.
저장 시험 `hiltest_baf632049ae34183ba20c17cdf807b20` r2의400ms 조건을 정상 Engineer 화면에서
실행해 `job_c3d63693eaa34413b73962569c9ec475`를 완료했다. A/B 각각 제어360개·RGB360장,
누락0이며 실행 후 자원idle·잔여객체0이다. 4.55초의 A2.158m/s와 B정지·제동100%, B의7.15초
tracking 회복을 관찰에 남기고 새로고침과 SDK JSON export로 원본 연결을 확인했다.

원인은 세 가지였다. 2100은 이전 발표용GPU 반환 때 의도적으로 종료됐고,8075는 원래
worker 없는 작성 환경이었다. 별도로 시스템NVML595.99.02가 로드된 kernel595.84와 달라
기본GPU조회가 실패했다. 기존 공식595.84 라이브러리의 `LD_LIBRARY_PATH`·Vulkan ICD를
GPU조회와CARLA 프로세스에만 적용해 조회 및 렌더링을 복구했다. 시스템 설치·재부팅은 하지
않았으므로 전역 `nvidia-smi` 오류까지 해결했다고 설명하지 않는다.

현재8GiB GPU는 **HIL2100 전용**이다. 유휴·비동기·객체0인 발표용2000은 명령·PID 시작 식별자·
map을 보존한 뒤 중지했으며2160도 중지돼 있다. 기존 API와 보존 자료는 유지했다.
8075는 같은 DB/Engineer·Viewer 자격을 유지해 재시작했다. 연결 설정은 실행 가용성의 증명이
아니며, UI의 준비 확인이 실제 native probe를 실행하고 제출 시 입력을 다시 확인한다.

아래 명령은 최초 native 복구 시점의 기동·상태 조회 기록이다. 현재 서버 기동에는 위의
`launch-scenes.py`를 사용한다.

```sh
PYTHONPATH=backend/src:integrations/carla/src .venv/bin/python -B e2e/fixtures/hil_sim_beta_server.py .runtime/hil-beta-20261011 --port 8075 --ui .runtime/hil-beta-20261011/ui-native --enable-native --engine-env-file .runtime/hil-beta-20261011/native/engine-environment.json
ssh seongcheoljeong@carla-host.example.invalid /home/axiom-user/workspace/myproject/.venv/bin/python /home/axiom-user/workspace/axiom-hil-beta-20261011/host_control.py status
```

첫 명령은 서버가 이미 실행 중이면 port 점유로 종료한다. owner-only JSON의 정확한7개
`AXIOM_CARLA_HIL_*` 설정을 사용하며 상속된 다른CARLA 환경은 사용하지 않는다.
remote engine을 자동 기동하거나2000과 자동 전환하지 않는다. HIL 전용 worker는 기존 큐의
lease·heartbeat·취소·봉인 게시를 재사용하며 종료 시 현재 실행의 완료를 기다린다.
`status`는 kernel595.84를 확인하고 일치하는 환경으로GPU를 조회한다.

발표용2000으로 되돌릴 때는 먼저8075의 실행을 완료하고 이 서버를 정상 종료한 뒤,
위 remote `host_control.py`의 `restore` 명령을 사용한다. 이 명령은 HIL의 유휴 상태·원래
프로세스 식별자를 확인하고2100을 종료한 후2000을 원래 명령·map으로 복원한다.
이후8075를 native 옵션 없이 열면 작성·보존 재생을 계속할 수 있다. 최초 복구 인수에서는
`restore`를 실행하지 않았다. 다음 전환 때도 다른 작업·커널버전·저장공간을 다시 확인한다.

2026-10-11 후속 사용자 승인으로 Sensor 배치 r2의 실제 A/B를 위해 HIL2100·8075를 잠시
중지했다가 같은 명령·UI·DB·자격으로 복원했다. HIL owner의 새 PID·시작 식별자를 갱신하고
실제 연결 probe·유휴 worker·기존 catalog100개 보존을 확인했다. 현재 GPU 배정은 다시
HIL2100이며 Sensor8088에서는 보존된 A/B를 재생한다.
[임시 전환·복원 기록](validation/sensor-layout-native-20261011.json).

backend/SDK/adapter90개, runtime4개(작성 fixture), frontend22개, production build와
실제 브라우저 실행·재생·관찰·복원을 확인했다. [복구·새 native 근거](validation/hil-sim-native-recovery-20261011.json).
실제 ECU·hard realtime 검증, 참가자 업무 인수, 시스템 전역 driver 정합과GPU배정 자동화는 후속이다.

### 최초 작성·보존 결과 인수 시점

기존 발표 환경은 그대로 두고 `127.0.0.1:8075`에 별도 작성·보존 결과 조사 환경을 준비했다.
`.runtime/hil-beta-20261011/`의 새 DB에 SQLite 읽기 스냅샷을 만들고, 봉인된 실행의 artifact
92개를 APFS 복사 후 hash로 대조했다. 완료4건과 실패2건의 기존 실행 식별자를 유지한다.
native 시뮬레이션을 새로 실행한 것이 아니며 원본 attempt 디렉터리는 복사하지 않았다.
새 환경의 transport는 명시적으로 미설정 상태이고 worker도 없어 native 실행을 받지 않는다.
Engineer/Viewer 자격은 새 환경의 비공개 access 디렉터리에만 보관한다.

2026-10-11 읽기 점검에서 원격 저장 공간은 약74GB가 남아 있고, CARLA2000만 실행 중이며
2100/2160은 중지돼 있다. GPU 상태 조회는 NVML 드라이버/라이브러리 불일치로 실패했으므로
현재 GPU 여유 메모리는 확인하지 못했다. 로컬 디스크 여유는 약14GiB다. 이는 점검 시점의
관측이며 작업 시작 때 다시 확인한다. 저장 자료 삭제나 엔진 전환을 수행하지 않았다.

```sh
PYTHONPATH=backend/src:integrations/carla/src .venv/bin/python -B e2e/fixtures/hil_sim_beta_server.py <NEW_RUNTIME> --source-workspace .runtime/hil-demo-20261010/workspace --prepare-only
PYTHONPATH=backend/src:integrations/carla/src .venv/bin/python -B e2e/fixtures/hil_sim_beta_server.py <NEW_RUNTIME> --port 8075
```

첫 명령은 새 경로만 허용하며 source workspace에 쓰지 않는다. 준비 중 실패한 디렉터리도
지우거나 덮어쓰지 않는다. 두 번째 명령 전에 frontend production build를 준비한다.
이 도구는 HIL 데이터에 한정한 로컬 검사 환경이며 일반 백업·공개 배포 도구가 아니다.

현재 구현과 검사 기록은 [개발 상태](status.md), 과거 native 결과는
[HIL 데모](hil-sim-demo.md)를 참조한다. 실제 참가자의 업무 완료 인수는 별도다.

backend/SDK79개, frontend·시점 계약20개, production build와 실제 Engineer 브라우저의
저장·개정·복제·정확한 결과 연결·관찰·북마크 복원·다운로드를 확인했다.
SDK/API에서는 Viewer 쓰기 거절과 엔진 없는 native preview 거절도 확인했다.
[이번 범위의 검사 근거](validation/hil-sim-beta-20261011.json)에 보존 실행, 원본 hash,
검사 명령과 미완료 인수 항목을 구분한다. 네이티브 실행 의미는 바꾸지 않았고 이번 새 실행은0건이다.
