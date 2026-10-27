# HIL Workbench · Software DUT 데모

사용자 방향(2026-10-10): 상세 검증 확대보다 **3–5분 외부 화면 공유에서 입력 고장과 차량 반응을 설명할 수 있는 실행 기능**을 우선한다. 첫 범위는 CARLA와 별도 프로세스 Software DUT를 연결한 폐루프 시연이다.

## 사례 모음 후속 · 200ms 실제 A/B 완료 · 2026-10-10

200ms 지연 실행 `job_3f30f238d95948eda6327fde25c6cc5d`를 정상 API로 완료하고 사례 모음에
연결했다. 각 조건 제어 응답360개·RGB360장·누락0·cleanup confirmed다. 응답 시각3.05–7.05초에
고장이 반영되고 최대 입력 age는200ms다. 250ms 임계값 아래에서 A/B 모두 `tracking`을 유지해
기존400ms의 `degraded`와 구분된다. 그러나5.00초 속도는 A0.86/B1.60m/s로 달라 동일 상태를
동일 주행 반응으로 설명하지 않는다. HIL3개 사례의 브라우저 재확인에서200ms 표시·정확한 실행
연결·5.00초 탐색·재생/정지·새로고침·목록 복귀를 통과했다.

사용자 승인으로 CARLA2000→2100을 일시 전환한 뒤2000을480×270·Town10HD_Opt·비동기·
차량/센서/보행자0으로 복원했고2100은 중지했다. 기존 API 서비스는 유지했다. 객체 GT를 사용하는
Software DUT 시뮬레이션 범위이며 실제 ECU·실시간·안전 검증은 후속이다.
[사례·발표 순서](demo-case-library.md) · [실행·복구·브라우저 근거](validation/demo-case-library-20261010.json).

## 사용자가 완료할 수 있는 흐름

`HIL Sim` → 프로젝트/프리셋 선택 → 지연 또는 단절 값 변경 → 변경 내용 확인 → A/B 실행 → 새 결과 열기 → 같은 시간의 영상·신호 비교.

- **객체 입력 지연:** 기본300ms, 3–7초 주입.50–1,000ms를50ms 단위로 변경한다.
- **객체 입력 단절:** 기본1초, 3.5초부터 주입.0.5–4초를0.5초 단위로 변경한다.
- A는 정상, B는 고장 조건으로 같은 시나리오·seed·DUT 소스에서 순차 실행한다. 실제 새 실행 두 회를 하나의 Experiment에 보존한다.
- 새 실행이 진행되는 동안 명시적으로 연결한 대표 결과를 계속 볼 수 있다. 완료 후 `새 A/B 결과 열기`를 눌러 전환한다. URL·새로고침으로 선택한 결과를 복원한다.
- 추적/전방 카메라, 전체 화면,0.5/1/2배속, 고장/상태 이벤트 이동, 자동 가이드, 입력 age·제동·속도·전달된 앞차 간격을 제공한다.
- 왼쪽 편집값은 다음 실행 조건이며 오른쪽의 저장 결과에는 해당 실행의 실제 고장값을 표시한다.

## 고정 시연 장면과 실행 경계

Town10HD_Opt의 등록된 직선 경로, Prius2대,18초, 제어20Hz. 자차 목표3m/s, 약12m 앞 선행차2m/s가4초에 정지를 요청한다. 전방·추적 RGB는640×360/10Hz다.

`CARLA 객체 GT → simulated-time fault injector → JSON-lines Software DUT 프로세스 → throttle/brake/steer → CARLA vehicle physics`가 실제 실행 경로다. 자차 상태는 최신이고 고장은 객체 채널에만 적용한다. DUT는 입력 age250ms 초과 시 제동하고, 정상 입력3회가 연속으로 도착하면 복귀한다. 이는 데모용 참조 정책이다.

RGB는 관측 영상이며 DUT의 인지 입력이 아니다. 입력 age/고장 시점은 시뮬레이션 시간이고 IPC 지연은 부모 프로세스의 wall-clock 관측값이다. 고정 timestep은 wall-clock 실시간 보장을 의미하지 않는다. 실제 ECU·차량·센서의 물리 HIL 검증이나 안전 성능 판정은 포함하지 않는다.

시각 축은 응답 프레임 기준으로 맞춘다.20Hz 제어에서3.00초 입력에 주입한 고장은3.05초 응답 영상·신호에 나타난다. 전체 요청 trace에는 원래 입력 시간도 보존한다. HIL 경로는 두 차량 명령의 동기 batch acknowledgement를 받은 뒤 명시적으로 world tick을 진행하고, CARLA readback과 다음 차량 상태를 기록한다.

## 3–5분 시연 순서

1. 연결 흐름과 `Software DUT · CARLA Simulation` 범위를30초 설명한다.
2. 대표300ms 지연 결과의 전체 화면에서 자동 가이드를 실행한다. 고장 시점으로 이동해 입력 age와 제동 차이를 보여준다.
3. 단절 프리셋으로 바꾸고 정지→정상 입력 확인→복귀를 보여준다.
4. 지연 값을400ms로 편집해 차이를 확인하고 새 A/B를 제출한다. 저장 결과와 새 실행의 조건이 분리돼 있음을 보여준다.
5. 완료 후 새 결과를 열고 원본 실행 식별자와 입력·응답 기록으로 마무리한다. 실행 대기 시간은 실제 GPU/전송 환경에 따라 달라진다.

## 구현 단위와 후속 작업

| 단위 | 현재 기능 | 재사용한 기반 | 이후 개발 범위 |
|---|---|---|---|
| 장면/프리셋 | 고정 lead-stop와2고장 프리셋, 입력 제한·revision pin | 기존 Scenario/API/catalog | 더 다양한 장면·DUT 프로필 |
| 환경·차량 | 실제 CARLA 순차 A/B, 원본 RGB | 선택형 CARLA adapter·물리 엔진 | 다른 plant/실차와 상관성 |
| DUT 실행 | 별도 프로세스 init/step/close, timeout·실패 노출 | Python 프로세스·JSON-lines | 실제 ECU SDK·reset·power/health |
| 고장 | 객체 지연·단절, age·복귀 상태 | 직접 구현한 bounded injector | CAN/Ethernet·센서·복합 고장 |
| 시간·I/O |20Hz simulated clock, sequence·request/response 기록 | 기존 native frame 및 artifact 체계 | PTP·하드 실시간·jitter 보장 |
| 비교·표시 | 원본 A/B 동기 RGB·신호·이벤트·전체 화면 | 기존 City Viewer·React | 다중 DUT·장시간 분석 |
| 실행·근거 | 일반 Experiment 큐·취소·SDK·agent API, hash 보존 | 기존 플랫폼 | 실제 장비 인수·광학/RF/RAW 주입 |

CARLA 엔진 코드와 지도/차량 asset, 데이터, 모델 가중치, 표준 문서의 사용권은 각각 구분한다. 기존 catalog의 출처/권리 메타데이터를 그대로 가져오며 외부 공개 재배포 승인을 뜻하지 않는다.

## API / SDK

- `GET /api/v1/hil-sim/presets?project_id=...`
- `POST /api/v1/hil-sim/presets/{id}/bindings`: 정확한 scenario_ref와 선택적 대표 실행 연결.
- `POST /api/v1/hil-sim/presets/{id}/preview`: binding revision과 파라미터를 고정해 일반 Experiment 요청과 preview hash 반환.
- 이후 기존 `/experiments`, `/experiments/{id}/executions`를 사용한다. 종류는 `hil_demo`, 비교는 `same_scene_fault_ab`, adapter는 `carla-software-dut@1.0.0`이다.
- Python SDK: `hil_sim_presets`, `bind_hil_sim_preset`, `preview_hil_sim_preset`, `execute_hil_sim_preview`.

결과는 `hil-demo-result/1`, fidelity는 `actual_carla_software_dut`. 정상/고장 원본은 `baseline-`/`faulted-` artifact로 각각 보존한다. `hil-demo-trace/1`에는 프로세스 신원, 전체 요청/응답, 실제 적용 명령, 다음 CARLA frame, IPC시간, compact presentation frames가 들어간다. Viewer는 외부 실행 receipt와 각 원본 hash를 확인한 후 side를 선택한다. HIL 검증 연결은 `context_only` / `not_evaluated`이며 CARLA 진단 기준을 안전 판정으로 올리지 않는다.

## 로컬 시연 준비

`scripts/internal_platform.py`로 새 workspace와 private access directory를 만든다. 선택형 engine을 켜고 `AXIOM_CARLA_HIL_{SSH,PYTHON,SCRIPT,DIR,SPOOL,OWNER,PORT}`를 설정한다. 생략한 값은 기존 City transport 설정을 사용하며 HIL launcher는 City script 옆 `hil_native.py`가 기본값이다. 기존 deployment의 파일을 덮어쓰지 말고 별도 source directory에 동일 hash의 native 파일을 둔다. 전용 CARLA endpoint의 공통 lease/cleanup 경계는 유지한다.

```sh
.venv/bin/python -B scripts/hil_sim_demo.py --api http://127.0.0.1:8036/api/v1 --token-file <private-token-file> prepare --source-api <catalog-api>/api/v1 --source-project <project> --output <runtime>/prepared.json
.venv/bin/python -B scripts/hil_sim_demo.py --api http://127.0.0.1:8036/api/v1 --token-file <private-token-file> rehearse --prepared <runtime>/prepared.json --output <runtime>/rehearsal.json
```

준비는 공개 API로 native catalog와 lead-stop 시나리오를 별도 프로젝트에 등록한다. 리허설은 정상 큐로 두 프리셋의 A/B를 실행하고 성공한 기본값 결과만 대표 결과로 연결한다. 실패/불확실/진행 중 실행 ID는 receipt에 보존한다. 재개는 같은 receipt를 사용하며 실패를 새 작업으로 조용히 대체하지 않는다.

## 완료 확인 범위

관련 backend/SDK·adapter 계약과 실제 별도 프로세스 검사, production build, 두 프리셋의 실제 CARLA A/B, 정상 브라우저의 편집·제출·결과 전환·재생을 확인한다. 상세 물리 상관성 연구, 전체 회귀시험, 실제 장비 인수와 상용 release gate는 후속이다. 실행별 근거와 남은 제한은 `docs/validation/hil-sim-demo-20261010.json`에 기록한다.
