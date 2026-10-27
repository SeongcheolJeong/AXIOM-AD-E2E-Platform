# ADAS Camera Module RTL

AXIOM의 `HIL → FPGA Lab → Engineering Lab → ADAS Module RTL`은 FPGA를
카메라 처리 DUT로 삼는 네 개의 독립 스트리밍 ISP를 실행한다. 기존 OpenISP, SA8650
SimPy 모델, Apollo 참조 모델과 시험·실행 종류를 구분한다.

## 실행 계약

기본 입력은 채널당 1920×1080 RAW12, 30프레임, seed42다. 네 채널을 동기 시작하고
74.25MHz 상당의 2200×1125 raster를 100MHz 시간축에 분수 누산기로 배치한다.
유효 RAW 합계는 2.985984Gbit/s다. 이는 시험용 타이밍 가정이며 특정 센서의 명세가 아니다.
카메라 시각은 DUT ready와 무관하게 진행한다. 시작 CSR 설정 후 cycle256부터 입력한다.
입력 1초, 마지막 출력·terminal까지의 추가 배출 시간, 실제 호스트 실행 시간을 구분한다.

각 경로는 4096픽셀 FIFO → BLC → 수동 WB → 라인 메모리를 사용하는 MHC → RGB12
ready/valid 출력이다. 전체 프레임 저장 장벽과 공유 픽셀 출력 포트는 없다. 채널당 한 클록에
최대 한 픽셀을 처리하며 MHC 경계 crop 후 출력은 1916×1076이다. 출력 소비자는
`frame_commit` 이후에만 완전한 영상을 사용한다. `frame_abort`는 이미 받은 부분 영상도
무효화한다. 내부 reset/overflow는 외부에 제시된 `valid && !ready` 픽셀과 태그를 바꾸지 않는다.

FIFO overflow·프레임 경계 오류·채널 reset은 해당 채널을 flush하고 다음 수락 SOF에서
복구한다. 소스는 과거 프레임을 재전송하지 않는다. 별도 terminal FIFO가 가득 차면
새 프레임을 수락하지 않아 기존 결과 이벤트를 덮어쓰지 않는다. 오류 검출과 terminal
retirement는 한 클록씩 분리되며, 외부 출력은 commit 전까지 잠정 데이터다. FIFO 뒤의
1-beat ingress register와 skid, MHC line-output register는 4096 FIFO 용량과 별도다.

## 설정과 입력

32-bit AXI4-Lite의 채널 base는 `channel * 0x100`이다. AW와 W는 독립 수락한다.
고정 주소 decode 후 채널별 write action/data를 등록하고, 적용한 뒤 B 응답을 반환한다.
이 단계가 적용 중이거나 B 응답이 보존 중이면 새 AW/W를 수락하지 않는다.

| Offset | 레지스터 |
|---|---|
| 0x00 | enable bit0, channel reset W1 pulse bit1 |
| 0x04 | BLC shadow |
| 0x08 / 0x0c / 0x10 / 0x14 | R / Gr / Gb / B gain shadow, Q9.7 |
| 0x18 | COMMIT W1: shadow를 pending snapshot으로 저장 |
| 0x1c | enabled / active / pending 상태 |
| 0x20–0x3c | 수락·commit·abort·overflow·경계 오류·입출력·미수락 카운터 |
| 0x40 / 0x44 | 현재 / 최대 FIFO 점유율 |
| 0x48 / 0x4c | epoch / 적용된 config_version |

Pending 설정은 다음 **수락** SOF에 적용한다. 프레임 안에서는 고정되며, 다른 채널의
설정이나 결과에 영향을 주지 않는다. local reset은 active/pending 설정과 버전을 보존하고
stream epoch를 증가시킨다. 아직 적용되지 않은 pending 설정이 있을 때 추가 commit은
명시적인 AXI 오류를 반환한다. CSR 일정의 `at_cycle`은 버스 요청 시작 시각이며 버스 응답과
실제 프레임 적용 시각은 구분한다. reset 일정은 단일 W1 pulse만 지원한다.

채널별 합성 calibration pattern 또는 immutable RAW asset를 선택한다. RAW12 값은
little-endian uint16 컨테이너로 보존하며, 채널별 Bayer·크기·프레임 범위와 원본 hash를
검사한다. 고장 종류는 출력 정체, 입력 valid 중단, 채널 reset이다. 작은 크기는 기능 검사에
쓸 수 있지만 1080p30 인수로 판정되지 않는다.

## 실제 실행과 결과

UI·API·SDK는 `adas_module_rtl` 설정과 `adas-camera-4ch/1` 파이프라인을 공유한다.
개정 저장 → preview/preflight → worker native 실행 → 결과·A/B·export 흐름을 사용한다.
실행 중 취소와 실행 실패를 표시하고, 보존된 결과 열기는 새로운 실행과 구분한다.

Verilator는 실제 생성 RTL을 실행한다. C++ 공간 정수 MHC 참조는 RTL의 라인 메모리나
스케줄러를 재사용하지 않고 **수락된 모든 출력 픽셀의 RGB 성분**을 검사한다.
이 참조의 RAW/RGB hash와 실제 preview를 별도 NumPy convolution 참조로 교차 검사한다.
입력 valid 중단은 EOL에서 감지되기 전 일부 잘못된 픽셀을 내보낼 수 있다. 모든 차이는
보존하며 `committed_mismatch_count`와 `discarded_mismatch_count`로 구분한다. 명시적인
입력 중단 일정과 겹치고 경계 오류로 abort된 프레임의 차이만 예상된 폐기로 분류한다.
정상 commit 영상의 오류, 다른 채널 오류와 예상하지 못한 차이는 검증 실패다.

- `frames.json`: 모든 채널·프레임의 원본/출력/참조 hash, disposition, 설정 버전,
  epoch, 첫 입력·출력·terminal 시각, 오류와 FIFO peak.
- `previews/*.png`: 실제 RAW/RGB의 공간 샘플. 전체 해상도의 검사 결과와 구분한다.
  중단·미수락 프레임은 정상 출력 이미지로 표시하지 않는다.
- `native.csv`: 전 구간 약4000개 주기 표본과 선택 구간의 조밀 표본.
- `waveform.vcd`: 명시한 구간의 실제 top port. 전체 시간 VCD가 아니며 기본 첫1024클록,
  합계16384클록 이내다. VCD timestamp는 실제 edge 위상, CSV는 상승 edge 직전 값을 표시한다.
- `pixel-errors.jsonl`, `events.jsonl`, `summary.json`, RTL 원본, 컴파일 로그,
  도구·adapter·설정·입력·결과 hash와 sealed export.

전체 픽셀 CSV는 생성하지 않는다. 네 채널×30프레임의 RAW/RGB preview240개를 포함해
ADAS native artifact 수만512개로 제한한다. A/B는 동일 채널·프레임의 입력 hash가 같을 때
동일 RAW 비교임을 표시한다. URL은 시험 개정·실행·비교·채널·프레임·파형 표본을 복원한다.

## ECP5와 실제 장비 경계

같은 RTL design hash로 별도 Yosys 합성·nextpnr 배치배선을 실행한다. 평가용 프로필은
LFE5U-85F/CABGA381/speed6, 내부 100MHz OOC이며 실제 보드 선택이 아니다.
자원 적합·내부 타이밍·픽셀 동작은 독립 판정한다. timing 미달이면 실패 결과를 보존한다.
OOC는 I/O pin/PCB/PHY/CDC/전원/실시간 동작을 검증하지 않고 bitstream도 만들지 않는다.

`hardware_target`이 설정되면 현재 API는 실행 불가를 반환한다. 소프트웨어 결과로 장비
실행을 대신하지 않는다. 이후 정확한 보드·pin map·카메라 수신·출력 전송 경로를 확정하고
FPGA-in-the-loop의 RAW/CSR/RGB/카운터 비교, 실제 DES/CSI 인터페이스, 시스템 HIL 순으로
확장한다. 시험과 기대 결과 계약은 유지하고 장치 identity와 실행 adapter를 추가한다.

## 참조와 라이선스

주 구조 참조는 [Qualcomm ADAS 백서](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/documents/Efficient-Heterogeneous-Compute-Architecture-for-ADAS-ECUs.pdf)다.
카메라 입력·ISP·버퍼·설정·감시 역할을 참고하며 SA8650 CPU/GPU/NPU 또는 비공개 ISP의
bit-exact 구현을 주장하지 않는다. Apollo SM8750 자료는 보조 참조다.
[EEWorld 기사](https://en.eeworld.com.cn/news/qcdz/eic706483.html)의 이전 관찰 정보는 기존
SA8650 Lab에 유지되며 조회 오류가 난 원문에서 새로운 배선·성능·레지스터 값을 추정하지 않는다.

[AMD mini-isp](https://github.com/amd/mini-isp)의 MIT RTL은 commit
`67e752d6bf668179ec45446089a709bbc1ef9736`에 고정되어 있다. 원본 vendor 파일을 보존하고
채널 wrapper·AXI4-Lite·FIFO·검증 harness의 변경은 AXIOM 파일에 분리한다.
MHC의 ready 경로를 끊는 skid와 line-output register는 생성된 `axiom_adas_demosaic`
파생 모듈에 적용한다. `axiom_adas_colorgain`은 28bit 곱셈 결과를 기존 stage 2에
등록하고 Q7 절삭·포화를 기존 stage 3에서 수행한다. WB의 단계 수와 정수 연산은 같다.
원본과 파생 소스 hash, MIT 고지와 수정 이력을 함께 보존한다.
코드 라이선스, 입력 asset 권리, 도구 라이선스와 장비 사용 권한은 각각 별도다.

재현: `examples/adas_module_rtl_workflow.py --build`는 정상·WB·30µs 정체·100µs overflow·
reset·입력 중단을 실제 API/SDK로 실행하고 hash와 A/B 채널 분리를 확인한다.
`--resume`는 저장 시험·실행을 재사용하며 `--smoke`는 명시적인 축소 기능 시험이다.

## 2026-10-17 초기 구현 결과 · 타이밍 개선 전

동일 RTL design hash `6edc2f85bf132361fbf549dff8362fb6db8705a1e70604e9ad68275196e579d3`로
공개 API/SDK의 새 worker 실행을 수행했다. 아래 여섯 건은 모두 축소 없이4×1080p30,
입력1초·채널당30프레임이다. 선택한 입력 조건에서 마지막 결과가 입력 창 안에 나와
추가 배출 시간은0이다. 실제 호스트 연산 시간은 시험당 약54–57초이며 실시간 장비 성능이 아니다.

| 시험 | 정상 commit / abort | CH0 FIFO peak | 검증 |
|---|---:|---:|---|
| 정상 | 120 / 0 | 2 | 픽셀·프로토콜 오류0 |
| WB 변경 | 120 / 0 | 2 | CH0 frame3부터 config v1, 이전 프레임·타 채널 불변 |
| 30µs 출력 정체 | 120 / 0 | 1943 | 손실 없이 회복, 전체 RGB hash 동일 |
| 100µs 출력 정체 | 119 / 1 | 4096 | CH0 frame0 overflow, frame1부터 정상 회복 |
| 중간 reset | 119 / 1 | 2 | CH0 frame0 abort, epoch 증가·다음 프레임 회복 |
| 입력 valid 중단 | 119 / 1 | 2 | 경계 오류로 frame0 abort, 이후 정상 회복 |

정상 확정 영상의 불일치는 모두0이다. 입력 중단에서 감지 전 출력된 부분 영상은194개
RGB 성분이 참조와 달랐으며 모두 abort로 무효화했다. 전체 차이 수와 hash를 보존하고
상세 위치 로그는 첫100개까지 남긴다. 이 차이를 정상 영상 오류0으로 덮어쓰지 않는다.

ECP5 합성·배치배선은 실행됐으나 **100MHz 타이밍 게이트는 실패**했다. 최고 측정된
OOC Fmax는82.1423MHz, 자원은 LUT9747 / FF9152 / BRAM48 / DSP4다. 병목은 프레임 ID와
경계 검사 → abort → terminal event queue write-enable 경로이며12.174ns 중9.113ns가
배선 지연이다. 아홉 구현 probe의 소스·보고서와 실패를 보존했다. 타깃 주파수나 입력량을
낮추어 성공으로 판정하지 않았다. 이 상태를 실제 ECP5에서100MHz로 동작한다고 사용하면 안 된다.

[실행·전체 artifact hash·A/B 링크](validation/adas-module-rtl-20261017.json) ·
[타이밍 실패 근거](validation/adas-module-timing-probes-20261017.json) ·
[API·native·브라우저 검사](validation/adas-module-rtl-checks-20261017.json).
이 초기 결과의 100 MHz 타이밍 미달은 아래 후속 개선에서 재검증한다. 실제 보드·I/O
경로 확정과 물리 검증은 별도다.

## 2026-10-17 후속 결과 · 100 MHz 타이밍 통과

최종 design hash는 `f1e025a36d7212812873af7a7a8900cfa054bd1ed543e59915b54a0f154b0e47`다.
프레임 검사 결과를 fault register에 저장하고 다음 단계에서 abort·terminal 이벤트를
확정해 기존 조합 경로를 분리했다. reset/abort 동안 이미 제시한 ready/valid 픽셀·태그는
수락될 때까지 유지한다. event queue의 full/pop, 마지막 출력과 fault의 동시 발생,
terminal retirement 중 reset도 native 시험으로 확인했다.

첫 경로만 분리했을 때 Fmax는 87.176 MHz였다. 이후 CSR 주소 decode와 채널 action을
등록하고, WB 곱셈과 포화를 기존 파이프라인 안에서 나누고, FIFO와 MHC 라인 RAM 출력에
elastic register를 추가했다. 모든 probe는 동일 4채널·1920×1080·FIFO4096·100 MHz·seed42를
사용했으며 실패한 중간 결과도 보존했다. 원본 AMD 소스에는 직접 수정하지 않았다.

| 동일 ECP5 OOC 조건 | 개선 전 | 개선 후 |
|---|---:|---:|
| 목표 clock | 100 MHz | 100 MHz |
| Fmax | 82.142273 MHz | 101.368477 MHz |
| 타이밍 판정 | 실패 | 통과 |
| 최장 경로 | 12.174 ns | 9.865 ns |
| LUT | 9747 | 9735 |
| FF | 9152 | 9648 |
| BRAM / DSP | 48 / 4 | 48 / 4 |

공개 API의 새 빌드 `job_ad97f1d5f12a4b598449e2d8157d7359`에서 실제 Yosys 합성·nextpnr
배치배선·100 MHz 타이밍을 통과했다. 평가용 LFE5U-85F/CABGA381/speed6 프로필, 도구 hash와
seed42는 이전 빌드와 같다. 타이밍 예외나 목표 주파수 완화는 추가하지 않았다.
최장 경로는 MHC line RAM → 다음 라인 저장 FF로 이동했으며 주파수 여유는 1.368477 MHz다.
실제 보드 I/O나 pin/CDC/PHY 타이밍, bitstream 실행은 이 판정에 포함하지 않는다.

정상·WB·30 µs 정체·100 µs overflow·reset·입력 중단을 모두 **4×1080p30, 30프레임/채널**로
새로 실행했다. 앞 세 시험은 각각 120개 commit, 뒤 세 시험은 각각 119개 commit + CH0의
의도한 1개 abort다. 정상 확정 영상과 프로토콜 오류는 모두0이며 입력 공급창1초와
추가 배출시간0을 구분해 기록했다. 실제 호스트 실행시간은 실물 실시간 성능이 아니다.

전후의 입력 프레임 720개와 확정 RGB 717개 hash가 모두 동일하다. WB의 frame3 적용,
채널 독립성, fault 이유, 다음 프레임 복구, config version과 terminal epoch도 유지됐다.
측정상 첫 RGB 출력은 모든720프레임에서 **30 ns**, 확정717프레임의 terminal은 **40 ns**
늦어졌다. overflow의 폐기 부분 출력은2픽셀 감소, 입력 중단은1픽셀 감소했다. reset의
abort는20 ns, 입력 중단의 abort는10 ns 늦어졌고 overflow의 abort는40 ns 늦어졌다.
입력 중단의 폐기 부분영상 불일치194개는 유지된다. 이를 정상 영상 변화와 분리해 보존한다.

UI에 `ADAS 빌드 A`와 `ADAS 빌드 비교 B`를 추가했다. `build`·`buildCompare` URL은 픽셀
`execution`·`compare`와 독립적으로 복원한다. 동일 profile/target/seed/tool 조건,
Fmax·주파수 여유·자원 차이와 RTL/config hash를 확인하고 양쪽 근거를 export할 수 있다.
개선판은 UI5270/API8170의 별도 데이터 사본을 사용하며 기존5260/8160 서비스는 유지한다.

재현·검증:

```sh
.venv/bin/python examples/adas_module_rtl_workflow.py \
  --url http://127.0.0.1:8170 --ui http://127.0.0.1:5270 --build \
  --output docs/validation/adas-module-timing-closure-20261017.json --resume
.venv/bin/python examples/compare_adas_module_campaigns.py --verify-shared-store
```

첫 명령의 `--resume`는 보존 실행 재조회이며 새로운 실행이 아니다. 새 campaign에는 다른
output 경로를 사용하고 `--resume`를 생략한다. 비교기는 전후 artifact3267개와 sealed
export14개를 재해시하고, 새 API에 보존된14개 실행의 manifest hash를 대조한다.

[실행·해시](validation/adas-module-timing-closure-20261017.json) ·
[개선 전후 비교·6개 픽셀 A/B 링크](validation/adas-module-timing-closure-20261017-comparison.md) ·
[비교 상세 JSON](validation/adas-module-timing-closure-20261017-comparison.json) ·
[7개 실제 timing probe](validation/adas-module-timing-closure-probes-20261017.json) ·
[backend/native/브라우저 검사](validation/adas-module-timing-closure-checks-20261017.json).
