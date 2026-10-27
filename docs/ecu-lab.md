# SA8650 ECU Lab · 참조 동작 모델

HIL → FPGA Lab → SA8650 ECU에서 카메라 1–4채널의 송신 조건과 목표 ECU의 수신·처리 가정을
저장하고 SimPy로 실행한다. 사건·프레임 재생, 채널별 상태/수신 큐, 원인 추적, 저장 결과 A/B,
정확한 시험 개정·시점 복원, JSON/CSV 내보내기가 같은 versioned API를 사용한다.
기존 FPGA Lab v1의 저장 설계·실행은 그대로 유지한다.

## 목표와 근거

목표는 FPGA 카메라 입력의 준비 순서, 대역폭, 프레임 누락과 고장 회복을 빠르게 검토하는 것이다.
SA8650 펌웨어/ISP/RTL 실행, GMSL/CSI 전기적 해석, 실제 CAN 통신과 물리 ECU 검증은 포함하지 않는다.
기사에 나온 11개 카메라와 DES 3그룹은 도식에 참조로 표시하며, 최대 4개의 선택 입력만 계산한다.
나머지 카메라·Ethernet·GNSS/IMU·display·storage는 미모델링이다.

- `article_observed`: [SA8650 ECU teardown](https://en.eeworld.com.cn/news/qcdz/eic706483.html)의
  구성도. 보드 실제 배선과 실측 성능은 확인되지 않았다.
- `manufacturer_verified`: [ADI MAX96717R data sheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max96717r.pdf)의
  MAX96712/MAX96722 quad deserializer 분류 및 [ADI DSI serializer 설명](https://ez.analog.com/interface-isolation/gigabit-multimedia-serial-link-gmsl/a/faqs/FA285/which-gmsl-serializers-can-support-concurrent-gmsl1-and-gmsl2-operation-when-two-links-are-active)의
  MAX96755 분류. 기사에 있는 반대 역할 설명은 모델에 채택하지 않았다.
- `manufacturer_verified`: [NXP S32K3xx data sheet](https://www.nxp.com/docs/en/data-sheet/S32K3xx.pdf)의
  S32K342 MCU 분류. 여기의 감시 기능은 사용자 지정 동작 모델이며 그 MCU의 펌웨어가 아니다.
- `model_assumption`: 모든 편집 가능한 용량·처리율·시간·감시 정책. 4ms/frame은 칩 성능 수치가 아니다.
- `unknown`: 실제 register/CAN ID, firmware, ISP, CSI routing과 보드 성능. 임의 주소나 CAN ID를 만들지 않는다.

오픈소스는 SimPy 4.1.1의 이산 사건 실행과 React Flow의 블록 표현에 사용한다. 기존 저장된
[SimPy](../third_party/licenses/simpy-4.1.1.txt)·[React Flow](../third_party/licenses/react-flow-12.10.1.txt)
라이선스를 유지한다. 공개 데이터시트 참조는 데이터시트/IP/표준의 재사용 허가를 뜻하지 않는다.
새 vendor SDK, 독점 펌웨어, 기사 이미지나 신경망 weight를 복사하지 않는다.

## 실행 모델

하나의 SimPy Environment/ns 시간축에서 모든 송신 채널과 공유 CSI·처리기를 실행한다.
독립 채널 결과를 합쳐 경쟁처럼 표현하지 않는다.

1. 합성 RAW line 크기와 frame ID를 [기존 FPGA 계약](fpga-lab.md)의 DDR/FIFO/직렬 링크 처리율로 계산한다.
   실제 RAW 파일이나 전체 byte stream은 전송하지 않는다. 초기 적재는 t=0이며 source offset은
   다음 공급과 송신 기준시각에 적용된다. 유효 송신 Mbps는 GMSL 명목 속도와 동일하다고 가정하지 않는다.
2. ECU profile의 폭·높이·RAW bits·FPS와 sender를 일치시킨다. channel과 VC는 각각 유일해야 한다.
   기존 FPGA v1의 `expected_fps/deadline_us/timeout_us`는 target ECU 감시를 제어하지 않는다.
3. t=0 power-on 후 boot, configure, link lock, start 순서로 입력이 준비된다. 준비 전 입력은 폐기한다.
   stop/reset 명령과 channel link loss, 공유 processor stall, heartbeat loss를 지정할 수 있다.
4. 가상 DES는 채널별 유한 line buffer를 가진다. 공통 CSI는 준비된 라인을 round-robin으로 처리한다.
   같은 시각의 초기 tie는 channel ID 순서다. CSI 소요시간은 payload+line overhead와 lane 유효율을 포함한다.
5. 완전한 프레임만 ECU 수신 큐로 이동한다. 채널별 큐는 처리 중인 프레임을 제외한 대기 프레임 수이며,
   포화 시 새 프레임 전체를 폐기한다. 하나의 처리기는 준비된 채널을 순환해 고정 처리시간을 적용한다.
6. 입력 timeout은 profile FPS의 예정 source 시각 기준, 처리 deadline은 완전한 CSI 수신부터
   대기+처리 완료까지다. 늦게 도착하거나 완료한 프레임도 결과에 남기며 deadline 실패를 지우지 않는다.
7. 링크 잠금과 정상 상태는 별도다. 기본 3개의 연속 정상 완료 후 normal이며 한 채널의 링크 고장이
   다른 채널을 중지하지 않는다. MCU heartbeat는 논리 사건이며 실제 CAN 주기/메시지가 아니다.
8. 유한 입력과 큐를 끝까지 배출한다. 1–4채널, 합계120프레임, 보수적 100만 사건/60초 가상 시간
   상한을 사전 검사한다. 지원되지 않는 입력과 초과는 거절하며 결과를 조용히 자르지 않는다.

## 기본 예제와 완료 조건

| 예제 | 입력 | 기대 결과 |
|---|---|---|
| 정상 | 1080p RAW12 30FPS, 1채널60프레임 | 60개 처리 완료, 폐기 없음 |
| 준비 전 송신 | source offset 0 | 초기 폐기 후 준비된 프레임 처리 |
| VC 중복 | 2채널 VC0 | 저장/사전 검사422, 실행 없음 |
| 링크 회복 | 2채널, CH0 350–420ms 단절 | CH0 부분 프레임 폐기·재잠금·정상 회복, CH1 유지 |
| CSI 병목 | 4채널30프레임, 4×200Mbps 유효80% | CSI 대기·입력 deadline 초과 |
| 처리 큐 적체 | 4채널30프레임, 20ms/frame | 수신 큐 포화·프레임 폐기·처리 deadline 초과 |

기본 CSI는4×1Gbps 유효80%, 수신 큐3frames/channel, 처리4ms/frame,
boot100ms/link10ms, 입력timeout100ms/처리deadline100ms다. 모두 가정이며 수정 가능하다.

## UI / API / SDK

화면 주소는 `#hil-sim?project=default&mode=fpga&target=sa8650`이다.
작성안을 변경해도 저장 결과는 바뀌지 않는다. 저장 후 실행해야 새 값이 계산된다.
A/B는 각각 별도로 계산된 결과이며 설정 차이와 같은 channel/frame의 시간을 대조한다.
URL의 `test/revision/execution/compare/event`로 정확한 저장 작업을 다시 연다.

`/api/v1/hil-sim/ecu-lab`:

- GET `/templates`, GET/POST `/profiles`, GET `/profiles/{id}?revision=...`
- GET/POST `/tests`, GET `/tests/{id}?revision=...`
- POST `/tests/{id}/preview`, GET `/executions`

계약은 `ecu-profile/1`, `ecu-input-test/1`, `ecu-lab-input/1`, `ecu-lab-trace/1`, `ecu-lab-result/1`이다.
시험은 profile와 채널별 FPGA design의 `id/revision/sha256`을 고정한다. 프로젝트·권한·hash를 확인하며,
저장 요청에는 `Idempotency-Key`, 수정에는 `id/expected_revision`을 사용한다.
preview→기존 Experiment API의 저장/제출/취소/조회/export를 재사용한다.
kind=`ecu_timing`, adapter=`sa8650-ecu-behavioral` version0.1.0, fidelity=`behavioral_timing`.
`ecu-trace.json`에는 전체 고정 입력·source hash·근거·가정·원인 사건·seed·adapter version이 있고,
`summary.json`, `frames.csv`와 기존 실행 manifest는 artifact hash를 보존한다.

SDK는 `ecu_lab_templates`, `save_ecu_lab_profile`, `ecu_lab_profiles`, `ecu_lab_profile`,
`save_ecu_lab_test`, `ecu_lab_tests`, `ecu_lab_test`, `preview_ecu_lab_test`, `execute_ecu_lab_test`,
`ecu_lab_executions`, `export_ecu_lab_execution`을 제공한다.
[실행 예제](../examples/ecu_lab_workflow.py)는 같은 API를 사용하므로 agent도 별도 계산 경로가 필요 없다.

## 설치와 검사

API와 worker의 같은 Python 환경에 선택형 adapter를 설치한다. 코어는 SimPy를 직접 import하지 않는다.
새 프로세스가 entrypoint를 읽으므로 이미 떠 있는 서비스의 반영은 별도 운영 작업이다.

```sh
.venv/bin/python -m pip install -e integrations/fpga_lab
.venv/bin/python -m pytest backend/tests/test_ecu_engine.py backend/tests/test_ecu_lab.py backend/tests/test_fpga_lab.py backend/tests/test_experiment_core.py backend/tests/test_hil_beta.py -q
npm --prefix frontend run build
cd e2e
npx playwright test --config ecu-lab.config.ts
```

브라우저 검사는 독립8101/5201 포트와 DB를 사용한다. API 계약 검사에는 실제 queued SimPy
5개 정상 실행 예제와 예상 VC 거부1개, SDK·권한·hash·불변 개정·멱등성·취소·실패 상태가 포함된다.
계산 실패를 강제로 일으키는 queue 계약 검사는 실제 칩/장비 고장 실험과 구분한다.

실물 연동 후속: 실제 DES/CSI 배선과 포맷, startup/control protocol, reference clock, 실측
link/processing latency와 receiver 정책을 확보해 별도 profile로 보정한다. RTL/Verilator 검증과
장비 adapter는 아직 연결하지 않았다. 임의 firmware execution 또는 장비 검증으로 표시하지 않는다.

## 보존 실행 미리보기 · 2026-10-14

별도 로컬 [ECU Lab](http://127.0.0.1:5199/#hil-sim?project=default&mode=fpga&target=sa8650)을
준비했다. API8099/worker와 `e2e/.runtime/ecu-demo` DB를 사용한다. 기존 운영 서비스는 변경하지 않았다.
개발 프로세스를 종료하면 이 URL은 열리지 않는다. [실행 근거](validation/ecu-lab-20261014.json)에
정확한 시험/프로파일 참조, 입력·artifact·코드 hash, 정상/비교 직접 열기 URL이 있다.

실제 full-resolution 계산은 정상60/60, 준비 전 송신57/60, 링크 회복57/60(비고장채널30/30),
CSI 병목108/120·deadline120, 처리 적체61/120·폐기59다. 별도의 VC 중복은 예상422 거부다.
85개 집중 backend 검사, ECU2개·기존 FPGA2개의 실제 worker 브라우저 흐름과 production build를 확인했다.
브라우저는 축소된 RAW와 정상·고장·병목·거부를 사용하고, backend/보존 실행은1080p를 사용한다.
링크 단절 후 이전 버퍼의 정상 프레임은 처리될 수 있지만 재잠금 후3정상 프레임 판정에는
포함하지 않는다. 이전 잠금 타이머도 새 연결의 잠금을 앞당기지 못하도록 epoch를 분리한다.
