# FPGA Lab v1 · 카메라 HIL 동작·타이밍

HIL → FPGA Lab에서 합성 RAW 1채널의 설정을 저장하고 SimPy로 계산한다.
계산이 완료되면 사건/프레임 이동, 점유율과 송수신 파형, 저장 실행 A/B,
원인 사건 이동, JSON/CSV 다운로드와 정확한 URL 이어하기를 사용할 수 있다.

## SA8650 대상 ECU 확장

1–4채널의 공유 CSI·수신 큐·처리/감시 모델은 별도 [SA8650 ECU 모드](ecu-lab.md)에 있다.
아래 v1의 계약과 저장 결과는 유지한다.

## 설치와 로컬 실행

기존 API/worker와 같은 Python 환경에 선택형 adapter를 설치한다.

```sh
.venv/bin/python -m pip install -e integrations/fpga_lab
npm --prefix frontend ci
```

API와 worker는 동일한 DB와 adapter 버전을 사용해야 한다. 새 프로세스의 plugin discovery가
`simpy-camera-timing` 0.1.0을 찾는다. 설치된 adapter 표시는 worker의 가동을 보장하지 않는다.
기존 서비스에 반영하려면 별도의 운영 절차가 필요하다. 이 기능의 개발 검증은 독립 로컬
API/worker와 임시 DB에서 수행하며 CARLA/GPU나 실제 장비를 사용하지 않는다.

## 사용

1. 정상 전송 템플릿에서 설계 이름과 RAW·DDR·FIFO·스케줄러·링크·ECU 설정을 편집한다.
2. 개정을 저장한 뒤 계산을 실행한다. 변경한 편집안은 저장하기 전 실행할 수 없다.
3. 사건 위치를 옮겨 프레임의 Trigger, DDR 준비, 송신 시작/종료, ECU 도착을 조사한다.
4. 대역폭 부족 또는 다른 설정을 저장·실행하고 비교 B에 이전 실행을 선택한다.
5. URL에는 프로젝트·설계 개정·실행 A/B·사건 위치가 보존된다. 결과 다운로드는
   인증된 기존 Experiment artifact API를 사용한다.

기본 1920×1080 RAW12는 라인 2,880 bytes, 프레임 3,110,400 bytes다.
30 FPS의 payload는 93,312,000 bytes/s이며 라인 오버헤드와 전파 지연은 별도다.

## 모델 계약

- integer ns 이산 사건, 동일 seed/설정/adapter에서 동일 trace. 입력 시간은 공급 일정,
  Trigger는 예정 송신 시각이며 실제 노출 또는 하드웨어 clock 측정값이 아니다.
- RAW8/10/12/16의 tight-packed line 크기를 사용한다. RAW10 폭은 4의 배수,
  RAW12는 2의 배수다. stride·메타데이터·임의 파일 업로드는 미지원이다.
- 소스는 `ramp/1`: `(x+y+frame+seed) mod 2**bits`. 미리보기는 해당 패턴을
  256×128로 subsample하고 8bit로 표시한다. 엔진은 payload bytes를 전부 만들거나
  전송하지 않고 라인 크기와 frame/line ID를 계산한다. source hash는 패턴 정의의 hash다.
- 초기 프레임은 t=0에 DDR에 존재한다. 나머지는 프레임별 공간을 먼저 예약하고
  직렬 쓰기 포트로 적재한다. 공간 부족 시 새 프레임 전체를 폐기한다.
- DDR는 독립 읽기/쓰기 처리율 모델이다. 읽은 라인은 FIFO로 이동하며 DDR 공간을
  반환한다. FIFO는 최소 한 라인을 담아야 한다. 송신 중 payload도 FIFO 점유에 포함된다.
- 스케줄러는 프레임 순서를 유지한다. 준비되지 않은 입력을 기다리며 source underflow를
  기록한다. 같은 프레임의 라인 사이에서 송신이 멈추면 FIFO underflow를 기록한다.
- jitter는 `[-jitter_us,+jitter_us]` 정수 균등분포, seed 고정이며 Trigger 역전을 제한한다.
  매 N번째 프레임 누락은 초기 적재 프레임에도 적용된다.
- 링크는 라인 payload+추가 bytes를 순차 송신한다. 고정 전파 지연은 링크 처리율을
  점유하지 않는다. Serializer/Deserializer는 이 경로의 추상 블록이다.
- ECU 기준 시각은 `frame_index/expected_fps`. deadline 초과, timeout 후 늦은 수신,
  끝까지 도착하지 않은 프레임을 구분한다. 유한 소스를 공급한 뒤 큐를 끝까지 배출하므로
  10초 입력 제한이 전체 계산 시간축의 종료 제한을 뜻하지 않는다.
- 한 실행당 최대 120프레임·10초 소스·100만 사건. 보수적 사건 상한을 준비 단계에서
  검사하고 초과는 거절한다. 런타임 제한 초과도 실패이며 trace를 조용히 자르지 않는다.
- 전체 사건을 보존하고 화면 차트만 약 2,000점과 주요 프레임 사건으로 줄인다. 정확한 peak는 엔진에서
  전체 사건을 사용해 계산한다. 사건 커서와 다운로드는 원본 값을 사용한다.

RTL/FPGA 합성·비트스트림, 실제 RAW 파일, 범용 블록 배선, 다채널 경쟁, CSI-2 CRC/ECC,
D-PHY, 특정 Serializer IP, DRAM bank/refresh, 실제 ECU I/O와 전기적 정합성은 미포함이다.
기능 성공은 물리 HIL 또는 센서 검증 통과를 뜻하지 않는다.

## API와 SDK

계약: `fpga-lab-design/1`, `fpga-lab-input/1`, `fpga-lab-trace/1`, `fpga-lab-result/1`.
`/api/v1/hil-sim/fpga-lab`의 `/templates`, `/designs`, `/designs/{id}?revision=...`,
`/designs/{id}/preview`, `/executions`를 제공한다. 저장에는 `Idempotency-Key`와
기존 설계 수정 시 `id/expected_revision`이 필요하다. preview는 정확한 `revision/sha256`을 받는다.

preview가 반환하는 experiment를 기존 `/experiments`에 저장하고 해당 preview hash로
`/experiments/{id}/executions`에 제출한다. kind=`fpga_timing`, comparison=`single`,
fidelity=`behavioral_timing`이다. 두 실행 비교는 원본 결과를 변경하지 않는 조회 작업이다.
오류 후 재시도는 같은 요청·멱등성 키를 사용한다. UI는 불확실한 실행 제출의 키를 탭 세션에 보존한다.

Python SDK: `fpga_lab_templates`, `save_fpga_lab_design`, `fpga_lab_designs`,
`fpga_lab_design`, `preview_fpga_lab_design`, `execute_fpga_lab_design`,
`fpga_lab_executions`, `export_fpga_lab_execution`.

## 검사

```sh
.venv/bin/python -m pytest backend/tests/test_fpga_lab.py backend/tests/test_hil_beta.py backend/tests/test_experiment_core.py -q
npm --prefix frontend run build
cd e2e
npx playwright install chromium
npx playwright test --config fpga-lab.config.ts
```

FPGA 검사에는 실제 SimPy 전체 해상도 네 예제, 독립 단일 라인 계산식, 재현성,
취소·용량·패킹 오류·불변 개정·권한 격리·SDK 내보내기 대조가 포함된다.
브라우저 검사는 별도 8097/5197 포트와 개발용 DB를 사용하며 기존 서비스를 재시작하지 않는다.

## 현재 개발 미리보기

2026-10-13에 별도 `http://127.0.0.1:5198/#hil-sim?project=default&mode=fpga`를
띄우고 API 8098, 독립 worker, `e2e/.runtime/fpga-demo` DB에 네 예제의 실제 계산을
보존했다. [검증 기록](validation/fpga-lab-20261013.json)에 A/B 직접 열기 URL과
실행·설정·artifact hash가 있다. 이 개발 프로세스를 종료하면 주소는 더 이상 열리지 않는다.
외부 공개 접속과 기존 발표/베타 서비스 업데이트는 수행하지 않았다.
