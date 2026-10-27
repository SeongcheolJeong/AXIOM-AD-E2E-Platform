# AXIOM · 자율주행 개발·검증 통합플랫폼 내재화

## 추력 차량 시험장 · Kickflip native A/B · 2026-10-23

Object Sim에 불변 시험 개정 저장 → 실제 CARLA 실행/취소 → 외부·차량 영상과 관측
재생 → A/B·URL 복원·export를 추가했다. UI와 Python SDK는 같은 버전 API와 기존
Experiment 큐를 사용한다. 원본 자세 행렬로 명령을 재계산하고 힘·충격량 한도,
연속 프레임, 영상 디코딩, 접촉·착지 판정을 검증한다.

기준 10ms와 5ms는 약360° 회전 및 데모 착지 기준을 통과했고, 추력30%는
최대상승0.08m·회전 미완료로 미통과했다. native3조건·취소 정리, 실제 영상의
브라우저 A/B·시점/개정 복원·export를 확인했다. 관련 Python103개·기존37개,
브라우저 작성/모킹 계약2개와 build가 통과했다. 초기 실패는 보존했다.

Ramp는 Blender 4.2.23 LTS 설치와 FBX 3개 변환·57개 hull의 형상 재검사를 완료했다.
Unreal import/cook 코드는 준비했으나 실제 실행은 미검증이다. 호환 UE4.26 소스
접근 권한과 빌드 공간이 없어 native Ramp는 차단한다.
[설치·변환·남은 조건](validation/stunt-ramp-toolchain-20261023/acceptance.md).
일반 스포츠카의 가정한 강체·추력
모델이며 Tesla 재현이나 실제 차량·탑승자 안전 검증이 아니다.
[기능 계약](stunt-lab.md) · [실행·비교·검증 근거](validation/stunt-lab-20261023/acceptance.md).

## 복합 고장 회복 순서 역전·실행 간 A/B · 2026-10-18

HIL Bridge에 `복합 고장 · 이전 실행 A / 현재 실행 B` 비교 축을 추가했다.
두 실행의 고장 측 CARLA 영상과 남은 보호 원인·경계별 회복 버튼을 같은 커서로
조사하며 `compareModel`로 선택 실행을 복원한다. 원본 시험·seed·경계·보호 정책·
실행기·설치·fidelity가 다른 조합은 거절하고 각 영상의 원본 실행·해시를 유지한다.

새 native 전원 모델을400ms 해석하고6V 강하를300ms 유지했다. 기존100µs FPGA
정체 원본을 재사용해 FPGA가 먼저 회복되는 새 CARLA 정상/고장 A/B를 실행했다.
두 경계 보호 설정은500ms·정상3표본으로 유지했다. FPGA 보호 해제3.556초 뒤에도
전원 보호가 남은6표본에서 제동100%·정지를 확인했고, 전체 해제3.856초 이후
CARLA4.05초 관측에서 재이동했다. 새 실행은 기존5ms 조건을 통과했다.

기존 전원 먼저 회복 결과와 최종 해제 입력 시점이 같아 정상/고장각360개 차량
제어값이 모두 일치했다. 입력3.70초에 남은 보호 원인은 기존FPGA·이번전원으로
바뀐다. backend/adapter/SDK124개, UI 해석23개, 공유 영상 회귀 검사와 build가
통과했다. 브라우저 정책 차단으로 일반 브라우저 인수는 미검증이며 모델 재생·
참조 ECU·가상 CAN·CARLA 범위를 유지한다.
[실행·비교 영상·검증 근거](validation/hil-model-recovery-order-20261018/acceptance.md).

## 전원·FPGA 복합 고장과 독립 회복 · 2026-10-18

HIL Bridge의 모델 연결에서 동시 감시 모델을 추가하고, 저장된 모델별 정상/고장
실행과 재생·보호 조건을 함께 저장한다. 참조 ADAS는 각 경계를 독립 감시하며
하나라도 보호 중이면 제동을 유지한다. 화면은 남은 보호 원인, 개별 경계 회복과
전체 보호 해제를 구분하고 기존 CARLA A/B·시간 커서·URL·export를 재사용한다.

전원6V 강하(차량축3.003–3.007초)와 CH0 FPGA ready300ms 정체
(3.005–3.305초)를 겹친 새 native/CARLA 시험이 기존5ms 조건을 통과했다.
전원 보호 해제3.605초 이후에도 FPGA 보호가 남은3.60–3.80초 입력 표본에서
제동100%·정지를 유지했다. 전체 보호 해제3.856초 뒤 제동이 풀리고
CARLA4.05초 영상 관측에서 재이동했다. FPGA는4×1080p30·100MHz를 유지해
정상120프레임, 고장110완료/10무효를 검사했고 비고장3채널은각30프레임 정상이다.

복합 초기화가 기존 단일 DUT 메시지 제한을 넘은 첫 실패는 보존했다.
게이트웨이의 기존8MiB 제한에 맞춰 전송 경계를 수정했고, 종료·CAN 무송신·잠금
해제를 관측한 뒤 별도 새 실행으로 통과했다. backend/adapter/SDK123개,
UI 해석19개와 production build가 통과했다. 브라우저 정책 차단으로 일반 브라우저
인수는 미검증이며, 봉인 모델 출력 재생·참조 ECU 검증 범위를 유지한다.
[실행·영상·독립 검사](validation/hil-model-overlap-20261018/acceptance.md).

## 모델 출력 → Virtual ECU → CARLA A/B · 2026-10-18

HIL Bridge에서 RAW·FPGA·전원·아날로그의 완료된 native 정상/고장 출력을 선택하고,
고장 없는 CARLA Network Lab 시험에 연결해 새 A/B를 실행한다. 참조 ADAS의 측정
감시 → CAN 명령 → 독립 Brake 보고 출력 → CARLA 제동·회복을 같은 화면과
시간 커서에서 조사한다. 모델 설정·실행·binding 해시와 선택 위치를 보존한다.

네 종류 모두 실제 Verilator/ngspice 출력과 UDP·분리 vCAN·CARLA로 실행했고,
최종 4쌍이 기존 5ms 조건을 통과했다. 총 2,880개 측정값을 원본 모델과 대조하고
차량 제어값을 수신 CAN bytes와 대조했다. 초기 RAW/FPGA 시간 조건 미충족 결과는
보존했다. 모델 입력 주기를 차량 요청과 분리하고, 제한된 CARLA 캡처 동안의
Python cyclic GC를 조정했다. 참조 ECU 판단과 최소 보호 조건은 유지했다.

현재 연결은 **봉인된 모델 출력의 1:1 시간 재생과 참조 측정 감시**다. 50ms 구간의
짧은 abort·전압 이탈을 보존하며 원본 종료 후 마지막 관측 상태를 유지한다.
픽셀 인지·CSI PHY·실물 ADC/전원·양방향 RTL/SPICE 연동은 미구현이다.
UI 해석 18개와 backend/adapter/SDK 115개 검사, production build가 통과했다.
브라우저 정책 차단으로 클릭·영상 연동·새로고침의 일반 브라우저 인수는 미검증이다.
[실행·경계·A/B 링크](validation/hil-model-loop-20261017/acceptance.md).

## HIL 고장 주입 확장 · 2026-10-17

HIL Bridge에서 참조 ADAS 리셋·제어 이벤트 누락 일정을 추가하고 vCAN/UDP·CARLA
A/B로 실행한다. 별도 모델 주입 패널은 RAW valid 단절·FPGA 출력 정체를 4×1080p30
Verilator에, 전원 강하·센서 오프셋을 ngspice 예시 회로에 연결한다. 설정 저장,
동일 API/SDK 실행, 원본 artifact/export와 실행 URL을 재사용한다.

실물 리셋/OS 드라이버, CSI PHY, FPGA 장치 I/O, 전원 공급기·아날로그 장비 연동은
미완료다. RTL/SPICE 모델은 CARLA·실제 ECU와 연결되지 않았으며 이 경계를 화면에
명시한다. 현재 브라우저 정책 차단으로 클릭·새로고침 인수는 미검증이다.
[구현 범위](hil-bridge.md) · [native 실행·검사 근거](validation/hil-boundary-injections-20261017/acceptance.md).

## HIL 주입 지도·설정 안내 · 2026-10-17

HIL Bridge 상단에 객체 UDP·Restbus·CAN 중계·출력 피드백의 클릭형 지도를 추가했다.
선택 위치의 주입값·원본 시험 개정, 참조 ECU 감시 기준, 시험기 스케줄 허용오차를
분리하고 원본 편집·피드백 설정·장비 설정으로 연결한다. 전체 지원 범위는 같은
profile API를 사용한다. 실물 표시에는 프로필과 일치하는 준비 결과 및 두 장치
identity가 필요하며, 가상 ECU 준비 결과를 실물 연결로 표시하지 않는다.

선택 위치와 지원 범위 보기는 URL에 보존한다. 현재 Binding hash와 일치하는 저장
결과만 관측 시각으로 연결하며, 편집 중인 조건·미관측 검출·실행 중단을 구분한다.
UI 해석 검사 23개와 backend/adapter/SDK 계약 검사 26개, production build가 통과했다.
브라우저 접근 승인 거부로 이번 화면의 클릭·레이아웃·새로고침 인수는 **미검증**이다.
실행기 변경과 새 native/실물 실행은 없으며 이전 실행·인수 기록은 유지했다.
[변경·검사 근거](validation/hil-injection-map-20261017/receipt.json).

## SIL → HIL Bridge · 가상 버스 연결 검증 완료 · 2026-10-17

기존 SIL 시험 개정을 선택해 HIL Binding 저장 → 장비/주입 경계 점검 → 실제
UDP·분리 vCAN 실행 → CARLA 영상·ECU/CAN 인과 A/B → 복원·export를
UI/API/SDK에서 수행한다. 사용자 승인 후 전용 UI5340/API8240와 격리 Linux
게이트웨이를 배치했고, 기존 UI5330/API8230·SIL 소스·봉인 결과는 유지했다.
최종 어댑터에서 정상/객체 시험 2쌍과 CARLA 고장 5쌍(각 A/B 360표본,
18초/측, seed42)이 5ms 조건을 통과했다. 127개 로컬 검사·브라우저 인수 통과.
피드백 상실 중단·취소·정리와 미지원 기능 거절도 확인했다.
초기 시간 초과 4건은 보존하며, 실물 ECU 연결·하드 실시간·물리 검증은 미완료다.
[기능과 남은 경계](hil-bridge.md) · [실행 인수](validation/hil-bridge-20261017/acceptance.md).

## Virtual ECU Network Lab · 공개 BSW·OSEK·가상 차량 통신 · 2026-10-17

Virtual ECU Lab의 `네트워크 / BSW / OS`에서 네 ECU 역할, SIL Kit CAN/Ethernet,
공개 Com/PduR/CanIf와 ADAS Trampoline 커널을 연결한 실행을 조사한다.
네트워크·시험 개정과 원본 프레임/OS/BSW/제동 근거를 UI/API/SDK에서 함께 사용한다.
기존 C++ 패키지 A/B와 구분하며 전체 AUTOSAR 또는 Silver 호환을 뜻하지 않는다.
[구현 계약과 현재 검증 경계](virtual-ecu-network-lab.md) ·
[실제 native 인수](validation/vecu-network-20261017/native-validation.md).

## Virtual ECU · 빌드부터 차량 반응 A/B까지 · 2026-10-17

HIL → Virtual ECU Lab에서 등록 C++ 소스를 실제 Linux 바이너리로 빌드하고 두 패키지를
동일 CARLA 시험에 연결한다. 동기 영상·ECU 상태·제동·정지 거리·복귀 시각과 패키지 identity를
한 화면에서 조사하고 선택 이벤트·실행·빌드 URL을 복원한다. UI/API/SDK는 같은 실행 계약을 쓴다.

즉시 전제동 A와 300 ms 제동 상승·500 ms 복귀 B의 정상/지연/단절/반복 8개 차량 실행을
완료했다. 정상은 같고, 지연에서는 B의 고장 감지 후 정지 거리가 0.253 m 증가하고 정상 추종
복귀가 0.50초 늦었다. 참조 정책의 관측 차이이며 안전성 우위 또는 양산 ECU 검증이 아니다.
[기능·경계](virtual-ecu-lab.md) · [실행 근거](validation/vecu-cpp-20261017/comparison.md).
별도 UI 5320/API 8220을 사용하며 기존 서비스·기록을 보존했다.

## Virtual ECU Lab · 2026-10-17

호스트 ADAS reference 프로세스의 패키지/객체 입력/시험 개정 → native 실행 → A/B 조사 →
정확한 URL 재개·trace/export를 일반 UI/API/SDK에 연결했다. 20 Hz 고정 재생과 기존 CARLA
폐루프 실행을 분리한다. 정상·지연·중단·반복·설정 A/B 및 반복성 시험, 실제 CARLA 등
공개 SDK 실행 8개가 완료됐고 artifact/export hash를 확인했다. 관련 검사 92개 통과.
[기능·계약·제한](virtual-ecu-lab.md) · [실행 근거](validation/vecu-20261017.json).

현재 reference application이며 양산 ECU, Qualcomm 내부, Silver 호환, 실제 HIL 검증이 아니다.
카메라 RTL과 RGB→객체 인지는 연결하지 않았다. 기존 결과와 서비스를 유지하며 새 UI 5300/API 8200을 사용한다.

## ADAS pre-board campaign · 2026-10-17

공통 Engineering Lab 저장·실행·취소·artifact·export API를 재사용하는 `검증 캠페인`을
추가했다. deterministic 계획 hash, seed별 실제 RAW 차이, 모든 확정 픽셀의 독립 참조,
transactional consumer, 4상태 검사, 미설정 보드 게이트를 연결한다. 기존 픽셀/빌드 A/B는 유지한다.
UI 5280/API 8180과 이전 결과의 검증된 독립 복사본을 사용한다.
[기능·검증 경계](adas-preboard-validation.md) · [현재 결과](validation/adas-preboard-20261017.json).

현재 실제 실행은 4×1080p30의 연속 10초와 세 seed의 복합 고장 등 19개 case 통과다.
확정 1,477 / 의도한 폐기 35프레임, 확정 픽셀 오류 0, 같은 RTL의 공개 OOC 103.295 MHz를
보존했다. 캠페인/빌드 916개 artifact와 각 sealed export의 hash를 검증했다.

4상태 검사가 발견한 BLC 미정의 비트를 고쳤으며 최종 RTL의 100 MHz OOC를 별도 재검증했다.
예시 ULX3S 프로필과 생성 PLL은 실제 보드 연결 또는 PHY/I/O signoff가 아니다.
실행한 기간·해상도·seed와 미실행 장기/물리 게이트를 결과에서 구분한다.

## ADAS Camera Module RTL · 100 MHz 타이밍 개선 · 2026-10-17

`HIL → FPGA Lab → Engineering Lab → ADAS Camera Module`에서 픽셀 영상/파형 A/B와
별도의 **ECP5 빌드 A/B**를 함께 선택하고 정확한 URL 재개·export를 수행한다.
기존 실행을 보존한 로컬 UI 5270 / API 8170에서 개선 전후를 직접 비교할 수 있다.

프레임 경계 검사 → abort → 이벤트 큐 경로를 fault 등록과 terminal retirement로
분리했다. 이어서 CSR 실행 단계, FIFO·MHC 라인 출력의 elastic register, WB 곱셈과
포화 단계 분리로 다음 임계 경로도 줄였다. 채널별 4096픽셀 FIFO, 독립 ISP 4개,
100 MHz, seed 42와 **4×1920×1080 RAW12·30 FPS·입력 1초** 조건을 유지했다.

동일 최종 RTL hash의 실제 Yosys/nextpnr 공개 API 빌드는 **100 MHz OOC 통과**다.
Fmax **82.1423 → 101.3685 MHz**, LUT **9747 → 9735**, FF **9152 → 9648**,
BRAM **48**, DSP **4**다. 목표 대비 주파수 여유는 **1.3685 MHz**이며 경로별 slack과
구분한다. 평가용 LFE5U-85F/CABGA381/speed6 프로필과 도구 hash도 동일하다.
기존 12.174 ns 경계/이벤트 병목은 해소됐고 최장 경로는 MHC 라인 RAM 출력의 9.865 ns다.

전체 해상도 6개 시험을 새로 실행했다. 정상·WB·30 µs 정체는 각각 120프레임 완료,
100 µs overflow·reset·입력 중단은 각각 119개 완료 + 의도한 CH0 한 프레임 폐기 후
회복했다. 확정 영상·프로토콜 오류는 모두 0이며 입력 중단의 194개 성분 차이는
폐기된 부분 영상 기록에 보존한다. 전후 입력 720개와 확정 RGB 717개의 hash가 일치한다.
RGB 출력은 30 ns, 정상 프레임 확정은 40 ns 늦어졌다. 고장 프레임의 부분 출력 수와
abort 시각 변화는 별도로 기록했으며 정상 결과로 간주하지 않는다.

backend/native/comparer 182개 검사, 실제 worker 브라우저 2개 흐름, 보존 빌드 A/B
브라우저 1개 흐름과 production build를 통과했다. 브라우저 새 실행은 32×24 기능 검사이고,
1080p 인수 근거는 별도의 공개 SDK 전체 해상도 실행이다. 전후 원본 artifact 3267개와
sealed export 14개를 재해시하고 새 API에서 14개 실행의 manifest hash를 확인했다.
기존 UI 5260/API 8160, OpenISP 결과와 타이밍 실패 근거는 유지했다.

실제 보드·PHY·HIL은 미연결이다. OOC 통과는 bitstream·핀·CDC·I/O 타이밍 검증이 아니며,
`hardware_target`은 계속 실행 불가를 반환한다. 다음 게이트는 정확한 보드·I/O 경로 확정과
실물 FPGA 비교다. Qualcomm 내부 IP·CPU/GPU/NPU 복제와 상용 release gate는 별도다.
[사용·구현 경계](adas-camera-module-rtl.md) ·
[개선 전후 A/B](validation/adas-module-timing-closure-20261017-comparison.md) ·
[새 전체 실행](validation/adas-module-timing-closure-20261017.json) ·
[검사](validation/adas-module-timing-closure-checks-20261017.json) ·
[타이밍 개선 probe](validation/adas-module-timing-closure-probes-20261017.json) ·
[초기 실패 근거](validation/adas-module-rtl-20261017.json).

## Open ISP RTL · 2026-10-17

`HIL → FPGA Lab → Engineering Lab → OpenISP RTL`에서 RAW 입력·BLC/WB 설정을
개정 저장하고 실제 Verilator 픽셀 실행, 정수 참조·RGB·차이 영상 A/B, 전체 top-port
VCD, 정확한 재개와 export를 연결했다. AMD mini-isp MIT 소스와 독립 정수 oracle을
사용하며 완료 프레임 RAM 뒤에서 MHC를 실행한다. 기본128×96, 최대320×240·2프레임이다.

보존 실행은 픽셀7건과 별도 ECP5 OOC 빌드1건이며 원본271개와 sealed export8개의
hash/크기를 재확인했다. 기본25,393cycles에서 실제 출력 중 정체는26,393cycles,
reset 재생은25,456cycles로 늘었고 출력 픽셀 hash는 동일하다. WB 변경은 동일 RAW에서
다른 RGB를 만들며 모든 정상 픽셀 실행의 정수 참조 불일치는0이다. 최초 출력 전 정체
실행도 효과 없음으로 해석을 붙여 보존했다.

ECP5 LFE5U-85F/CABGA381/speed6 OOC는 LUT1,040/FF1,118/BRAM13을 사용했고,
100MHz 목표에 대해 보고된 내부 경로 Fmax107.27MHz로 통과했다. 실제 보드·IO 타이밍이나
bitstream 검증은 아니다. GW2A/Gowin·CycloneV/Quartus는 프로젝트 템플릿과 미실행 상태만
제공한다. Apollo SM8750의 구조 참고이며 SA8650 내부 또는 Qualcomm bit-exact 구현이 아니다.

관련 backend/adapter110개 검사, 실제 worker의 새 OpenISP3개·기존 Engineering3개 브라우저
흐름, production build를 통과했다. 기존 bundle 크기 경고는 남는다.
새 로컬 UI5250/API8150 및 `e2e/.runtime/open-isp-demo`를 사용하며 기존 서비스를 유지했다.
[사용·범위](open-isp-rtl.md) · [실제 실행/hash](validation/open-isp-rtl-20261017.json) ·
[검사 근거](validation/open-isp-rtl-checks-20261017.json). 상용 release gate는 계속 열려 있다.


## Engineering Lab · 사용자 RTL / 아날로그 회로 · 2026-10-14

`HIL → FPGA Lab → Engineering Lab`에서 사용자 `.v/.sv` 소스나 SPICE netlist를
편집·가져오기 → 개정 저장 → 실제 Verilator/ngspice 실행 → 파형 A/B → 정확한 재개·
원본 다운로드로 연결했다. UI/API/SDK가 같은 immutable 시험·worker 계약을 사용하며,
소스·설정·seed·도구/adapter/공유 runner 식별과 모든 결과 hash를 보존한다.
RTL은 고정 32-bit ready/valid 단일 클록에서 Identity/XOR 기대 동작 또는 관찰 모드를
선택한다. 컴파일 오류·프로토콜/데이터 오류·timeout·관찰 완료를 구분한다.
SPICE는 self-contained R/C/L/V/I/D 과도응답이며 실제 adaptive 표본 시각을 유지한다.

최종 native 실행 6건에서 정상/정상 XOR는 각각256워드 통과, 의도한 오류 XOR는 첫25ns부터
256불일치, RC의 약1ms 출력은1kΩ 0.632139V / 2kΩ 0.393484V,
RLC 최대 출력1.854467V를 확인했다. 아티팩트69개와 sealed export6개의 hash/크기를 검증했다.
관련 backend/adapter/SDK 검사220개(신규 API48 + native/관련 회귀172), 실제 worker 브라우저
3개 흐름(새 실행7건, 소스 수정·실패·A/B·재개·다운로드·좁은 화면), production build를 통과했다.
기존 큰 frontend bundle 경고는 남아 있다.

별도 로컬 UI5240/API8140과 `e2e/.runtime/engineering-demo`를 사용한다. 사용자 RTL은 명시적으로
허용한 비공유 로컬 운영자 환경에서만 실행하며 검증된 OS sandbox라고 주장하지 않는다.
Workbench 그래프에 사용자 모듈 배치, 임의 포트/다중 클록, RTL-SPICE 혼합 신호 공동 실행,
실제 FPGA 보드 검증은 후속이다. CSV 표본은 상승 에지 직전 값이고 VCD 같은 시각은 에지 후 값이다.

**Qualcomm 실제 내부 구현은 연결되지 않았다.** 지정 자료 폴더의 원본 PDF8개 hash는 확인했지만
현재 연결된 대상 칩용 실행 런타임·지원 호스트가 없고 사용 권한·공식 인터페이스 확인도 필요하다. SA8650/SM8750/SM8850
연결 profile을 저장하고 준비 조건을 볼 수 있으며, 미설정 preflight는422로 거부하고 실행을 만들지 않는다.
Apollo 참조 모델 결과를 Qualcomm 실제 실행으로 대체 표시하지 않는다. 상용 release gate는 계속 열려 있다.
[사용·계약·실행 링크](engineering-lab.md) · [최종 native 근거](validation/engineering-lab-20261014.json) ·
[검사 근거](validation/engineering-lab-checks-20261014.json) · [Qualcomm 로컬 확인](validation/qualcomm-local-readiness-20261014.json).

## FPGA Workbench · 2026-10-14

`HIL → FPGA Lab → FPGA Workbench`에서 블록 추가·이동·포트 배선·삭제·Undo/Redo로
Source → 1–4 직렬 FIFO → Sink 회로를 작성하고, 그래프가 생성한 SystemVerilog를
실제 Verilator로 실행한다. 독립 SimPy 모델과 동일 자극을 비교하며 논리 파형·데이터·
버퍼·전송 계측기, 공통 클록 커서, 엔진/시나리오 A/B와 정확한 개정·시점 재개를 연결했다.
프로브·패널·위치 변경은 실행 해시를 유지해 재실행 없이 보존 신호를 조사한다.
미완성 회로는 저장할 수 있고 잘못된 배선·계측 연결은 실행 전에 해당 위치와 함께 거부한다.

4096워드·100 MHz·32비트의 최종 native 실행 4개에서 SimPy/RTL 불일치0을 확인했다.
1/2단 FIFO 정상 지연은 각각1/2클록, 2단 정체·회복은4096개 전달·최대128개 버퍼·입력130클록 정체,
리셋은4062개 전달+34개 명시적 폐기다. 원본56개 artifact와 sealed export4개의 hash/크기를 검증했다.
관련 backend/adapter/SDK 검사152개, 실제 worker 브라우저2개 흐름과 production build를 확인했다.
브라우저는 팔레트 추가→직접 포트 배선→새 실행→프로브 변경→A/B→재개·다운로드,
미완성 저장과 timeout 실패 표시를 포함한다. 큰 frontend bundle 경고는 남아 있다.

별도 로컬 UI5230/API8130과 독립 데이터 폴더를 사용한다. 기존 서비스·결과는 유지했다.
저장 파형 탐색은 엔진 일시정지가 아니다. Workbench 그래프 내 사용자 RTL 노드·CDC·
혼합 신호·실제 FPGA 보드·Qualcomm 내부 구현 검증은 후속이다. 독립 사용자 RTL/SPICE 실행은 상단 Engineering Lab에 연결했다. [사용·모델 계약](fpga-workbench.md) ·
[최종 native 실행 근거](validation/fpga-workbench-final-20261014.json).

## FPGA RTL Lab · 2026-10-14

`HIL → FPGA Lab → RTL A/B`에서 참조 SystemVerilog FIFO와 독립 SimPy 모델을
동일 자극으로 실행한다. 개정 저장·실제 Verilator 컴파일/실행·클록 파형·엔진/시나리오 A/B·
정확한 재개·VCD/CSV/JSON 내보내기를 UI/API/SDK로 연결했다.
100 MHz·32비트·깊이64·4096워드 보존 실행 4개에서 클록별 불일치0을 확인했다.
정상4096개 전달, 정체·회복4096개 전달/입력193클록 정체, 반복 정체842클록,
리셋4063개 전달+33개 명시적 폐기다. cycle-budget timeout은 trace를 보존하고 검증 실패로 표시한다.
관련 backend/adapter/SDK 검사111개, 실제 worker 브라우저 2개 흐름(정상·정체·리셋·timeout), production build를 확인했다.
[사용·모델 계약](rtl-lab.md) · [실행/hash 근거](validation/rtl-lab-20261014.json).
별도 로컬 미리보기는5220/API8120이며 기존 Apollo·SA8650 서비스는 유지했다.
고정 Stream32 사용자 RTL과 독립 SPICE 실행은 상단 Engineering Lab에 연결했다.
범용 포트·CDC·혼합 신호·실제 보드 타이밍·Qualcomm 내부 구현 검증은 후속 범위다.

## Apollo SM8750 Camera Lab · 2026-10-14

별도 Apollo target에서 1–4채널 RAW 입력, TFE/OFE/IPE CPU 영상, 공유 DDR·큐 타이밍,
Preview/ZSL을 개정 저장 → worker 실행 → 영상·타이밍 A/B → 정확한 재개·export로 연결했다.
1080p 4채널 120프레임 실제 모델 실행에서 정상 120개 기한 내 완료, CSI 12개 폐기,
OFE 63개 폐기, DDR 99개 폐기와 ZSL Snapshot 3/3 완료를 보존했다.
73개 관련 backend/adapter 검사, 실제 worker 브라우저 2개 흐름, frontend build를 확인했다.
문서 구조와 가정한 성능의 CPU 참조 모델이며 Qualcomm bit-exact/SA8650/RTL/물리 검증은 아니다.
상세 범위·사용법·남은 작업은 [Apollo Lab](apollo-lab.md), 실행 근거는
[20261014 receipts](validation/apollo-lab-20261014.json). 상용 release gate는 계속 열려 있다.

## SA8650 Target ECU Lab · 2026-10-14

`HIL → FPGA Lab → SA8650 Target ECU`에서 1–4채널 송신과 ECU profile, 명령·고장 일정을
개정 저장하고 실제 SimPy 실행·사건/프레임 재생·수신 큐·원인·A/B·정확한 재개·JSON/CSV를
UI/API/SDK로 수행한다. 모든 채널이 하나의 시간축과 공유 CSI·처리기를 사용한다.
기존 FPGA Lab v1·Software-DUT HIL과 실행 종류를 구분하며 기존 서비스는 재시작하지 않았다.

1080p RAW12 보존 실행에서 정상60/60, 준비 전 송신57/60, 링크 단절57/60(정상 채널30/30),
4채널 CSI 병목108/120·deadline120, 처리 적체61/120·폐기59를 확인했다.
VC 중복 예제는422 사전 거부이며 실행 성공으로 세지 않는다. 수치는 가정한 모델의 결과다.
독립 oracle·고장 경계·권한/개정/SDK·기존 FPGA/HIL/Experiment 검사85개와 실제 worker를 쓰는
브라우저 ECU2개·기존 FPGA2개 흐름, production build를 확인했다. React Flow 노드/선 표시와 좁은 화면도 확인했다.
기존 큰 bundle 경고는 남아 있다.

새 로컬 미리보기는5199/API8099, 독립 DB는`e2e/.runtime/ecu-demo`다. 처리율·지연·queue·감시
정책은 모두 model assumption이다. 실제 SA8650 firmware/ISP/RTL/PHY/보드 검증은 미포함이며,
다음은 실제 보드 포트·startup/control·실측 지연으로 별도 profile을 보정하는 단계다.
[사용·모델 계약](ecu-lab.md) · [실제 실행/hash 근거](validation/ecu-lab-20261014.json).

## FPGA 설계 실험대 · 2026-10-13

사용자 지정 첫 범위인 카메라 1채널 템플릿·합성 RAW·동작 타이밍 계산·사건 재생을
HIL의 FPGA Lab 탭으로 연결했다. SimPy 실제 CPU 실행과 UI/API/SDK를 사용하며
기존 CARLA Software-DUT HIL과 별도 모델이다. [범위와 근거](fpga-lab.md)를 따른다.
실제 RTL/보드/전기 신호 검증과 기존 운영 서비스 반영은 후속이다.

## 공통 내부 베타 작업공간 · 2026-10-11

개인 발표 다음은 내부 3–5명의 독립 사용이다. 정상 시작 화면에서 모듈별 작성·저장 결과·새 실행
상태를 구분하고 Object·Log·Software-DUT HIL·Sensor 배치 과제와 정확한 개정의 이어하기, 재현 정보를 붙인
의견, 운영자 진행 현황·처리·JSON/CSV를 연결한다. `/api/v1/beta`를 UI/API/SDK/agent가 공유하며
Log의 기존 과제와 의견은 원본을 유지해 투영한다. 어댑터 설정은 worker 준비 완료로 표시하지 않는다.
Sensor 배치 편집은 네 센서의 B 위치·회전, 저장 개정, 실행 연결, 시점 관찰을 추가한다.
[배치 베타](sensor-sim-beta.md)는 사용자 승인 전환으로 저장 r2의 실제 A/B와 네 센서 재생·
9.9초 LiDAR 관찰·개정/시점 복원·JSON 대조를 개발자 업무로 확인했다. HIL2100·8075를 복원했고,
Sensor8088은 작성·보존 결과 재생 상태다. 새 실행에는 GPU 사용 시간과 엔진 준비가 필요하다.
[실제 실행·운영 복원 근거](validation/sensor-layout-native-20261011.json)를 별도 보존한다.
Neural은 기존 기능과 공통 의견 입력을 유지한다. 실제 참가자 초대·인수와 운영 서비스 반영은
별도이며 공개 운영이나 물리 검증 완료를 뜻하지 않는다. [사용·운영 준비](beta-workspace.md).

## HIL 다음 단계 · 사용자 시험 관리 베타 · 2026-10-11

개인 발표 다음은 사용자가 조건 저장·개정·복제→실행 연결→A/B 조사→시점 관찰·내보내기를
직접 끝내는 업무다. 기존 CARLA→Software DUT 실행 경로를 재사용하며 시험 관리와
개정으로 고정한 DUT 정책 비교를 정상 UI/API/SDK에 연결한다. 첫 범위는 소수 초대형 베타이며
실제 ECU와 하드 실시간은 후속이다.
별도 로컬8075에서 작성·보존 결과 조사에 이어 저장400ms/r2의 새 native A/B1건과
관찰·북마크 복원을 확인했다. 양쪽 각각 제어360개·RGB360장·누락0이다.
GPU kernel595.84와 시스템595.99.02 불일치는 HIL 프로세스별595.84 환경으로 우회한다.
현재 공유GPU는 HIL2100에 할당했고 발표용2000은 복원 정보를 보존한 중지 상태다.
베타 worker는 명시적 native 옵션으로 HIL만 실행한다. 실제 참가자 업무 인수는 남아 있다.
고장 시간 편집은 UI/API/SDK의 시작·종료 작성→개정 저장→native 실행까지 완료했다.
400ms 조건의 입력 2–5초 변경을 새 r3 A/B에서 확인했고 양쪽 제어/RGB 각각 360개·누락 0이다.
이전 실행 구간과 현재 작성안을 구분하며 기존 7건의 receipt hash를 유지한다.
DUT 임계값·회복 프로필의 저장·개정·복원, 시험의 정확한 두 프로필 고정과 같은 고장의
A/B 비교를 추가했다. A 250ms/3회와 B 500ms/8회의 지연·단절 native 2건에서 각 A/B의
제어/RGB 각각 360개·누락 0과 종료 정리를 확인했고 기존 8건의 hash를 보존했다.
지연·단절 결과의 브라우저 관찰 저장과 JSON 다운로드, SDK export의 원본 연결을 확인했다.
기존 정상/고장 시험과 프로필의 이전 개정은 유지한다.
③ 선택한 3–5개 조건의 순차 실행을 UI/API/SDK에 연결했다. 정확한 시험 개정과 순서를
고정하고 서버가 한 조건씩 실행하며, 화면을 벗어나도 진행 기록을 복원한다. 중단·실패와
미완료 조건의 명시적 재개, 원본 trace에 연결된 반응 요약에서 A/B·관찰로 이어진다.
실제 3조건 완료·실행 중 취소 2건을 보존했고 API 재시작 후 마지막 미완료 조건만 재개했다.
기존 영수증 10건과 catalog 56개 행을 유지했으며 엔진 2100은 변경하지 않았다.
접수 실패와 취소 경합 검사는 작성한 handler 근거이며 실제 엔진 실패와 구분한다.
④ 장면 확대는 기존 Town10 base의 앞차 감속·정지 후 재출발 2종으로 완료했다.
고정된 속도 일정의 개정/hash를 시험·실행에 연결하고 두 장면의 native A/B에서
각각 제어/RGB 360개·누락 0·종료 정리를 확인했다. 실제 앞차 응답은 감속 4.15초,
정지 4.20초·재출발 8.45초이며 입력 일정과 구분한다. 기존 영수증 15건의 bytes를 유지했다.
감속은 브라우저 저장·실행·관찰·export, 재출발은 SDK 저장·실행까지 확인했다.
재출발도 브라우저 영상·8.45초 관찰·시점 복원을 확인했고 두 장면의 JSON은 SDK와 대조했다.
세 장면의 혼합 목록은 UI 저장·복원만 확인한 시도 0건이다.
8075의 현재 launcher는 `launch-scenes.py`이며 같은 DB·자격과 기존 2100 엔진을 유지한다.
로컬 여유 공간 약 2.08GiB는 다음 native 실행 전 확인할 운영 제약이며 자료는 삭제하지 않았다.
새 지도나 독립 장면 다양성 확보는 아니다. 다음 기능은 **사용자 DUT**다. 현재는 한 Town10 base의 참조 Software DUT
반응이며 실제 참가자 업무 인수·공개 운영·실제 ECU·하드 실시간은 후속이다.
[DUT 프로필 근거](validation/hil-dut-profiles-20261011.json) ·
[순차 실행 근거](validation/hil-sequential-20261011.json) ·
[장면 확대 근거](validation/hil-scenes-20261011.json).
[구현·참가자 업무·운영 준비](hil-sim-beta.md)를 따른다.

## Log Sim 내부 베타 · 2026-10-11

안내형 후속에서는 추천 팩→조건 저장→실행→조사→검토 완료의 업무 기록과 이어하기,
막힌 단계·도움 필요 의견, 운영자 현황·JSON/CSV를 연결했다. 참가자용 독립 프로젝트3개와
각 팩을 별도8072에 준비하고 중복 준비 없이 복원했다. 집중 검사73개와 정상 브라우저의
대표 팩·입력 오류·후보 없음·응답 유실 복구를 확인했다. 새 CPU reference4개만 실행했으며,
실제 사람의 베타 인수·초대·공개 운영은 아직 수행하지 않았다.
[안내형 베타 근거](validation/log-sim-guided-beta-20261011.json).

지원 MCAP 업로드→채널/구간 점검→조건 개정 저장→실제 reference A/B→후보 조사→Case 검토·내보내기를
정상 UI/API/SDK에 연결했다. 영상 없는 입력은 BEV로 조사하며, 중복 시간 후보 묶기와 시점 복원,
원래 배치 후보 복귀를 제공한다. 별도 로컬 테스트 환경에 보존 CARLA 원본 5개와 작성한 오류 예제를
구분해 연결했다. 첫 512개 이내의 명시적 선택, 총2,528개 표본에서 reference10회가 완료됐고
조사 후보55개·실패0개다. 새 CARLA 실행이나 기존 서비스 재시작은 하지 않았다.
실제 사람3–5명의 업무 완료 인수·초대·외부 배포는 아직 남아 있다.
[지원 범위·참가자 업무·재현](log-sim-beta.md) · [검사 근거](validation/log-sim-beta-20261011.json).

## Object Sim 다음 우선순위 · 초대형 베타 · 2026-10-10

개인 발표 데모 다음 단계는 3–5명의 사용자가 템플릿을 복제하고 객체·경로·행동을 편집하여
실행·비교·관찰까지 완료하는 업무다. 작성 동선과 세 행동군(정지·재출발, 끼어들기,
교차로 좌/우회전)을 먼저 완성하고 지도·asset 수 확대는 뒤로 둔다.
작성/권한/기록 연결을 구현하고 네 템플릿의 새 native A/B8건을 확인했다. 응답/RGB 각각5,160개,
누락0·행동 발생·자차 도착을 보존했다. 최종 브라우저 수정 개정의 실제 실행1건·RGB A/B·관찰 저장·
같은 개정 재실행 취소·재로그인을 포함한20항목을 통과했고 page error는0건이다.
GLB 제공 오류와 긴 경로 설정 표시를 수정했다. 최종 보존 결과 브라우저4항목에서
간결한 설정·GLB/texture200·RGB A/B·북마크와 page error0을 확인했으며 새 실행은 없었다.

빠른 시연의 장애물 우회·간격 대기·8대 교차로·도시 주행을 같은 testerA 프로젝트에 정상 API로
준비하고 **기존 베타8건과 별도로 native6건**을 완료해 새 저장 결과를 연결했다. 기존 베타 binding은
유지했다. 응답5,280개·RGB2,880장·누락0, 고정 평가5pass와 의도한 목표 미도달1fail이다.
장애물 B는 출발점 정지, 간격 B는 후방차 통과0.4초 단축에도 자차 대기·횡이탈 시점 동일,
교차로8대 도착·정지, 도시는 작성된2초 wait의 실제 정지2.15초를 보존했다. 첫 세 장면은
generated map이고 도시는 native Town10이다. 준비 helper4개 검사와 정상 API 확인을 마쳤으며,
참가자 브라우저에서 새 네 장면·기존 우회전 RGB5종과 A/B3종/새로고침8개 묶음,
별도 작성6개 묶음을 통과했다. 새 실행·저장은 없었고 console error는0건이다.
안내형 story API의 gateway 허용 목록 누락을 수정하고 기존 프로젝트 권한·자격을 유지했다.
gateway148개·backend story45개 검사와 정상 참가자 브라우저 안내3개 묶음의 공통 timeline·
양쪽 RGB 진행·정확한 URL 복원·차단 경고 없음·console error0을 확인했다. 초기 차단 기록도 보존한다.

첫 실패 기록은 유지하고 GPU kernel/userspace 버전 불일치는 프로세스별595.84 라이브러리로
우회했다. 첫 베타 인수 후 복원에 이어, 추가6건의 실행 시간대도 마치고13:38:55UTC에2160을 종료하고
발표용2000을 Town10HD_Opt·비동기·동적 객체0으로 다시 복원했다. 베타 API는 작성·저장·기존 결과
재생 모드이며 새 native 실행은 의도적으로 비활성화했다. 8GiB GPU의 발표 엔진이 약5GiB를
사용하고 보존 자료 삭제 없이 남은 저장 공간은 약4GiB다. 다음 native 업무는 저장 용량과 전용 베타 시간대 또는 별도 GPU를
확보한 후 허용하며, 실제 초대 사용자의
도움 없는 업무 완료와 외부 HTTPS 접속은 다음 인수 항목이다.
[베타 범위·현재 상태·남은 인수](object-sim-beta.md)를 우선한다.
[추가 시연6건의 근거](validation/object-sim-quick-20261010.json)는 기존 베타8건과 별도 보존한다.

## 다음 1주 우선순위 · 질문과 근거가 있는 사례 데모 · 2026-10-10

기술·의사결정 혼합 청중을 대상으로 사례 확보·정리50%, 필요한 연결 기능35%, 시연 안정화15%를
배분한다. 기능 목록 확대보다 **질문 → 변경 조건 → 실제 관찰 → 다음 행동**을 채운다.
10개 비교 항목을 조사·재실행(Object/Log), 센서 관측(Sensor), 입력 장애·회복(Software-DUT HIL)의
세 이야기와 CARLA Neural 도입 장면으로 구성한다. 조건 변형을 독립 장면 수로 세지 않는다.

시작 화면의 프로젝트별 사례 목록, 고정 결과 링크, 핵심 시점, 관찰 기록, 목록 복귀를 구현했다.
10개 사례·12개 고유 실행의 연결과 정상 브라우저 동선을 확인했다. 남았던 HIL200ms를 새 native
A/B 1작업으로 완료했으며 각 조건 제어360개·RGB360장·누락0이다. 최대 입력 age200ms에서
둘 다 tracking을 유지하지만5.00초 속도는 A0.86/B1.60m/s로 다르다. 기존400ms의 degraded와
구분해 상태와 차량 응답을 함께 설명한다. HIL3개 최종 브라우저 확인과 Sensor 주행 화면의
이전 미확인 항목도 닫았다. 10개는 독립 장면10개 또는 안전 검증 커버리지를 뜻하지 않는다.

기존 API 서비스와 작업공간은 유지했다. 사용자 승인으로 CARLA2000→2100을 잠시 전환한 뒤
2000을480×270·Town10HD_Opt·비동기·차량/센서/보행자0으로 복원하고2100을 중지했다.
현재8053/8054/8056/8060의 별도 loopback 검토 서버로 시연하며 새 사례 조회는 읽기 전용이다.
다음은10분 연속 발표·질의응답과 라이브 생성 리허설이다. 세부 발표 순서·현재 근거는
[사례 데모](demo-case-library.md)를 따른다.
아래 초기 기능 데모 목표와 미확인 기록은 당시 계획이며, 최신 상태는 본 항목과 status.md를 우선한다.

## 현재 공통 목표 · 빠르고 임팩트 있는 외부 데모 · 2026-10-10

사용자는 상세 검증 확대보다 **외부 발표에서 기능의 변화를 바로 보여주는 구현**을 우선하도록
플랫폼 전체의 다음 milestone을 정했다. 기존 내부 통합 목표를 유지하며, 이번 작업 선택과
완료 기준에는 이 데모 목표를 우선 적용한다. 아래는 개발 목표이며 전체 통합 리허설 완료 기록이 아니다.

목표는 발표자가 정상 제품 화면에서 **설정 하나 변경 → 실제 실행 → 영상·신호 A/B →
원본 조사 또는 Case 저장**을 8–10분 안에 시연하는 것이다. 각 장면은 큰 영상으로 시작하고,
원본·변경값·적용된 결과가 화면에서 바로 연결되어야 한다. 기존 OSS 엔진과 공통 Experiment,
API/SDK·프로젝트 권한·Viewer를 재사용한다. 정밀 모델 자체 개발보다 이 연결을 먼저 완결한다.

### 발표 구성과 기능 우선순위

| 순서 / 목표 시간 | 보여줄 변화 | 재사용할 현재 기반 | 이번 milestone에서 먼저 마무리할 일 |
| --- | --- | --- | --- |
| 1 · Neural Sim / 2분 | 원본 시간을 고정한 채 실제 렌더된 카메라 시점을 이동 | CARLA 20초 로그·Splatfacto checkpoint·61시점 sweep·큰 Viewer | 대표 장면 즉시 재생, 현재 pose와 원본 시간 표시, 작은 카메라 편집→새 결과 전환 |
| 2 · Sensor Sim / 2분 | FoV·LiDAR 밀도·radar 범위 변경에 따른 관측 차이 | 정지 A/B 프리셋·실제 캡처·20초 주행 다중센서 결과 | 주행 화면의 남은 정상 브라우저 확인과 눈에 보이는 편집·동기 A/B 동선 |
| 3 · HIL Workbench / 2분 | 입력 지연·단절에 따라 Software DUT의 제동과 회복 변화 | 실제 별도 프로세스 DUT·CARLA 정상/고장 A/B·이벤트 가이드 | 큰 영상과 고장 시점·입력 age·제동 변화의 동기 표시, 고장값 편집→새 결과 |
| 4 · Object Sim / 1–2분 | 앞차 대기·차로 변경 허용을 바꾸면 주행 결과가 달라짐 | 도시 반응형 planner·저장 A/B·GUI/YAML 작성 | 대표 상호작용 하나의 편집→실행→비교를 짧게 연결 |
| 5 · Log Sim / 1–2분 | 영상에서 본 사건을 Case와 조건 변경 시험으로 연결 | CARLA 원본 조사·Case·앞차 정지 시간 변경·A/B | 선택 사건→Case 저장→실행/원본 역링크로 발표를 마무리 |

본편에서는 고정된 실제 실행 결과로 A/B를 보여주고, 라이브 생성은 기존 checkpoint의
Neural 단일 프레임 렌더 한 건을 우선한다. 나머지 모듈의 새 native 실행은 별도 짧은 리허설에서
확인한다. 표의 시간은 발표 목표이며 모든 엔진 작업이 그 안에 끝난다는 성능 보장이 아니다.
라이브 작업이 지연되면 진행 상태를 유지하고 저장 결과의 실행 ID·설정과 구분해 시연한다.

신규 기능은 한 번에 위 표의 **눈에 보이는 변화 하나**를 끝낸다. 우선은 Sensor Sim 주행
화면의 미확인 동선을 닫고, 실제 발표 환경에서 모듈별 진입·대표 결과·현재 설정을 확인한다.
다음에는 청중이 결과 차이를 이해하는 데 직접 필요한 편집·모델 출력 overlay·Case 연결을
추가한다. 외부 발표용 모델 비교에는 사용 범위를 확인한 입력을 사용한다. 단순 메뉴 증가,
광범위한 엔진 튜닝, 데이터 전면 이관을 이 목표를 위한 선행 작업으로 확대하지 않는다.

### 이번 데모의 완료 기준

1. 일반 UI에서 대표 결과를 바로 열고 편집·preview·제출·진행/실패·새 결과를 끝까지 사용할 수 있다.
2. 저장된 실제 결과와 현재 제출한 작업을 구분한다. 실행 대기 중 저장 결과를 재생하고,
   새 실행이 완료되면 사용자가 그 결과로 전환한다. 학습은 보존 checkpoint를 먼저 재사용한다.
3. 영상 중심 A/B, 시점 탐색, 전체 화면, 새로고침 복원과 Case/원본 이동 중 해당 기능에 필요한 동선을 확인한다.
4. 변경 관련 backend/adapter/SDK 검사와 frontend build·브라우저 workflow를 확인한다.
   실제 실행 동작이 바뀌면 대표 native 실행을 확인한다. 통과 뒤에는 다음 기능으로 진행한다.
5. 입력·설정·seed·adapter/fidelity·원본 hash, 미지원 입력 거절과 실패 표시를 유지한다.
   시뮬레이션, Neural 재구성, 모델 출력 비교, Software DUT의 의미를 화면에 짧게 표시한다.

실측 상관성, 상세 센서 물리, 모든 지도/행동 조합, 장기간·대규모 회귀, 실제 ECU HIL,
하드 실시간, 동적 Neural 편집, 학습형 센서 폐루프와 상용 출시 인수는 후속 범위다.
대표 기능이 실패하면 그 기능을 막는 문제를 먼저 수정하되 이를 전체 검증 캠페인으로 넓히지 않는다.

### 발표 환경과 현재 남은 확인

기본 전달 방식은 **발표자 로컬 화면 공유**다. 기존 모듈별 workspace·고정 결과를 재사용하며
공개 서버 배포·익명 접속·기존 서비스 재시작을 이번 방향 변경에 포함하지 않는다.
GPU를 공유하는 CARLA와 Neural 학습의 동시 실행을 가정하지 않는다. 저장 결과로 발표를 시작하고,
새 실행은 확인한 자원 상태에서 순차 제출한다. 이는 준비 계획이며 이번 문서 변경에서 프로세스를 조작하지 않았다.

현재 기록에서 Sensor Sim 주행의 실제 수집·parser 검사는 완료지만 정상 브라우저 최종 확인은
대기 상태다. 기존 정지 프리셋은 별도 완료 근거가 있다. Neural 실도로→모델 출력→Case는
로컬 연구 인수를 완료했지만 nuScenes 외부 공개 권한은 확보된 상태가 아니므로 기본 외부
시연 구성에 넣지 않는다. CARLA 소스·engine·asset의 기존 출처와 사용 범위도 유지한다.

모듈별 상세 동선은 [Neural](neural-sim-demo.md), [Sensor](sensor-sim-demo.md),
[HIL](hil-sim-demo.md), [Object](object-sim-demo.md), [Log](log-sim-demo.md)에 있고,
실제 완료/실패·미확인 범위는 [개발 상태](status.md)를 따른다.

## Neural Sim 후속 연결 · 실도로와 모델 출력 · 2026-10-10

사용자는 실도로 로그 한 개의 재구성·새 시점에 실제 인지 모델을 실행하고 결과를 비교하여
Case로 저장하는 흐름을 빠르게 진행하도록 지시했다. 기존 CARLA 데모를 유지하며 이 연결을
추가한다. 모델 출력 민감도와 재구성 품질 영향을 함께 보여주고 정확도·안전 통과로 표시하지
않는다. 확보된 nuScenes는 기존 로컬 비상업 연구 범위를 보존한다. 동적 객체 편집과
센서 E2E 폐루프는 후속이다. [구체 범위](neural-sim-demo.md)를 따른다.

## Neural Sim 새 시점 발표 데모 · 2026-10-10

현재 Neural Sim은 상세 검증보다 빠른 외부 화면 공유를 우선한다. 선택한 첫 연결은
기존 CARLA 주행 로그 → Splatfacto 장면 학습 → 카메라 위치 편집 → 원본·재구성·새 시점
비교다. 기존 Experiment·프로젝트 권한·SDK·Viewer 구조를 재사용한다.
[고정 범위·일반 UI/API/SDK·시연 순서](neural-sim-demo.md)를 따른다.
실도로·동적 객체 편집·센서 기반 E2E 폐루프와 상세 물리 검증은 후속으로 둔다.

## HIL Workbench 데모 우선순위 · 2026-10-10

이번 HIL 범위도 상세 검증 확대보다 빠른 외부 화면 공유 시연을 우선한다. 사용자가 선택한
첫 연결은 **별도 프로세스 Software DUT + CARLA 주행 + 객체 입력 고장 A/B**다.
기존 Scenario/Experiment/Viewer 기반을 재사용하고, 실제 ECU·하드 실시간·RAW/광학/RF
주입은 후속으로 둔다. [고정 범위·정상 UI/API/SDK·시연 순서](hil-sim-demo.md)를 따른다.
내부 통합 목표와 기존 Sensor Sim·Log Sim 작업은 유지한다.

## 현재 Sensor Sim 우선순위 · 외부 데모 기능 구현 · 2026-10-09

사용자는 상세 검증 확대보다 **빠르게 기능을 연결하여 외부에서 임팩트 있게 시연할 수 있는 수준**을
현재 Sensor Sim 목표로 선택했다. 내부 통합 기반을 재사용하되 이 모듈의 다음 작업은
프리셋·간편 센서 편집 → native 실행 → 다중센서 Viewer → A/B 비교·발표 흐름 순으로 고른다.
상세 범위와 완료 조건은 [Sensor Sim 데모 목표](sensor-sim-demo.md)가 기준이다.

변경한 실행 경로의 관련 계약 검사와 대표 native 실행·정상 브라우저 업무를 확인한 뒤 다음 기능으로
진행한다. 실측 상관성·정밀 sensor physics·대규모 반복 검증은 데모의 선행조건에서 제외한다.
원본/설정 연결·실패 표시·권한·미지원 입력 거절은 기존 경로를 유지한다.
정지 장면의 세 프리셋·간편 A/B 실행·발표 Viewer를 구현하고 실제 native 실행과 브라우저 업무를
확인했다. [현재 인수 기록](validation/sensor-sim-demo-20261009.json)은 로컬 화면 공유 데모 범위이며
이동 중 다중센서·상용·물리 검증은 후속이다.
아래 내재화 방향과 다른 모듈의 작업은 유지하며, Sensor Sim의 이번 우선순위에 한해 본 항목을 우선한다.

갱신: 2026-09-28. 사용자가 확정한 우선 목적은 **외부 OEM 판매 이전의 통합플랫폼 내재화**다.
이 문서가 현재 목표·범위·다음 작업 선택의 기준이다. `product-pilot.md`의 로컬 평가판은
이 통합 목표를 위한 첫 번째 사용 가능한 하위 모듈이며, 플랫폼 전체를 정의하지 않는다.
아래 구조와 단계는 개발 계획이다. 네 외부 제품의 기능 동등성이나 실제 연동 완료를 뜻하지 않는다.

## 1. 목표와 성공의 의미

Foxglove의 로그·센서 조사, FiftyOne의 데이터 선별·모델 평가, Applied Intuition의
개발 도구·시뮬레이션 실행 연결, Foretellix의 시나리오·검증 범위 관리를 참고하여
**데이터 발견 → 실패 이해 → 시험 생성/실행 → 결과 비교 → 검증 공백 확인 → 다음 데이터/시험**을
하나의 공통 데이터·개정·시간·판정 체계로 연결한다.

내재화는 모든 화면·알고리즘·엔진을 처음부터 새로 쓰거나 네 제품을 반드시 구매한다는 뜻이 아니다.
우리 데이터 모델, 태그/실패 분류체계, 시험 정의, 지표, 근거와 워크플로를 직접 소유하고,
선택한 자체 구현·오픈소스·계약된 외부 도구를 교체 가능한 경계로 연결한다.
상용 SDK/API 접근 권한과 실제 capability는 도구별로 확인한다. 공개 기능 설명만으로
사용 가능한 connector나 재배포 권리가 확보됐다고 가정하지 않는다.

내부 사용 가치는 개발 시간·수작업 감소, 반복 가능한 분석, 자산 재사용, 담당자 간 인계,
검증 공백의 가시성으로 판단한다. 경쟁사 전체 기능 동등성이나 외부 구매 실적이 선행조건은 아니다.
반대로 내부 사용이라는 이유로 데이터 접근 통제, 복구, 결과 유효성이나 유지보수 책임을 생략하지 않는다.

## 2. 네 기능 영역

| 내부 모듈 | 참고 영역 | 목표 기능 | 다음 단계에서 연결할 산출물 |
| --- | --- | --- | --- |
| Data & Log Workspace | Foxglove | 지원 센서/차량 신호의 시간 연계 조사, 원본 구간 선택, 이벤트/태그, 검색, 비교 재생 | Recording/Segment/Case와 원본 시간·채널·sample identity |
| Dataset & Model Evaluation | FiftyOne | 라벨·split·A/B 평가, 조건별 실패, 유사 사례 검색·중복 검토, 재학습 데이터 선별 | Dataset revision/Prediction/Evaluation/Case 연결 |
| Experiment & Simulation | Applied Intuition | 시나리오·모델·센서 설정 선택, 배치 시험, 외부 실행 환경 연결, open-loop/closed-loop 구분 | TestSpec/ScenarioRevision/Campaign/Run과 실행 fidelity |
| Coverage & Verification | Foretellix | ODD/센서 조건 분류, 유효 조합·제약, 관측/시험/판정 coverage, 공백 기반 시험 후보 | CoveragePlan/MetricRevision/Evidence와 다음 시험 계획 |

이 네 구분은 우리 책임 분장을 위한 것으로 각 회사의 실제 제품 영역이 상호 배타적이라는 뜻이 아니다.
일괄적인 학습형 주행 정책·모든 지도·모든 sensor physics를 먼저 구현하는 계획으로 확대하지 않는다.

## 3. 공통으로 직접 소유할 연결

기존 개정/manifest/queue/원본 보존 구현을 재사용한다. 모든 데이터를 하나의 새 저장소에 복사하지 않고
기존 원본 URI·내용 hash와 파생물 관계를 유지한다. 기존 DB/원격 파일을 묵시적으로 이관하지 않는다.

| 공통 대상 | 역할과 필요한 경계 |
| --- | --- |
| Project / Study | 목적, 소유자, 검토 범위. 조직/권한 경계와 단순 화면 분류는 별도로 인수 |
| Segment | 원본 recording·채널·정확한 구간·clock domain과 sample identity. 파일명/근접 timestamp만으로 연결하지 않음 |
| Case | 관측된 현상·실패 후보, 주석·담당·원본 근거. 가설과 확인된 원인 및 최종 조치 상태를 분리 |
| Taxonomy / Tags | 날씨·장면·객체·행동·센서 조건 정의의 개정. 수동/규칙/모델의 생성 출처·버전·confidence·검토 상태 |
| Dataset / Model / SensorConfig | 기존 불변 개정 확장. 가중치·전후처리·입력 schema·장착·보정·실행 환경의 명시적인 식별 |
| TestSpec / Scenario / Campaign | 원본 Case와 재시험의 연결. 고정 변수/변경 변수, 지원 엔진, 입력·평가 계약을 실행 전 고정 |
| Evaluation / Evidence / CoveragePlan | 평가 버전, 원래 분모, 실패·미평가, 유효 범위. 원본 및 재시험으로 양방향 탐색 |

Tag/Case/시험 정의가 외부 도구에만 존재하면 내재화가 아니다. 외부 ID는 내부 ID와 매핑하고,
사용한 도구와 exporter/importer 버전도 남긴다. 메타데이터 교환·실행 제어·화면 deep-link를
구분하며, 링크 버튼만 추가한 것을 전체 연동으로 완료 처리하지 않는다.

## 4. 첫 번째 통합 업무

예시 목표는 **지원 로그의 관심 구간 선택 → Case 등록/태깅 → 고정 모델 A/B 평가 →
실패 이미지에서 원래 시간축으로 이동 → 지원되는 재시험 정의 → campaign 결과 연결 →
coverage와 추가 시험 후보 확인**이다. 주행 모델의 품질이 실패해도 도구 연결은 검증할 수 있다.

Log segment는 그대로 closed-loop scene이 아니다. 초기에는 원본 참조와 수동 검토한
template parameter를 이용해 파생 Scenario를 작성하고, 정확한 실환경 재구성이라고 표시하지 않는다.
지원하지 않는 광학/날씨/물리 변형은 거절하거나 미구현 상태로 남긴다.
고정 영상에 노출/시점을 바꾼 것처럼 metadata만 붙여 물리 시험으로 집계하지 않는다.

합성·시뮬레이션 데이터로 통합 계약을 먼저 인수할 수 있지만 내부 실측/승인된 로그와 외부 모델을
이용한 실제 업무 인수는 별도로 진행한다. 외부 OEM 고객 데이터가 없다는 이유로
통합플랫폼 개발 전체를 정지시키지 않는다.

## 5. Coverage의 최소 정확성

초기 버전은 versioned 조건 격자와 명시적인 규칙부터 구현한다. 조건 축·bin·유효 조합·제외 사유를
고정하고, 선언만 된 조건과 실제 관측으로 확인된 조건을 구분한다.

- Data coverage: 해당 조건에 유효한 원본 segment가 있는가.
- Execution coverage: 해당 조건의 지원 가능한 시험이 실제 실행됐는가.
- Evaluation coverage: 유효한 판정에 필요한 입력과 지표가 완결됐는가.
- Pass/fail: 완결된 유효 판정이 기준을 충족하는가.

Coverage 자체는 안전 확률이나 품질 통과율이 아니다. 같은 clip의 인접 frame·재실행을
독립적인 scene 다양성처럼 세지 않는다. 미분류·미평가·실행 실패는 원래 분모와 함께 보인다.
표준 규격을 차용하더라도 표준 전체/외부 업체 generator 동등 지원이라고 표시하지 않는다.

## 6. 기존 구현의 위치

현재 README/구현에서 지원 MCAP/센서 조사, ONNX/COCO 평가와 A/B 검토, 개정/큐/
suite/campaign/원본 보존, 백업/복원/로컬 실행 패키지를 확인했다. 이를 폐기하지 않는다.
개발 저장소의 제한된 CARLA 경로와 CARLA를 제외한 로컬 평가 패키지는 별도 실행 profile이다.

공통 Segment/Case를 통한 전체 흐름, 조건 분류·검색, coverage 공백→시험 환류,
멀티센서·시계열의 외부 E2E 정책 실행 및 팀 단위 운영은 추가 개발·인수 대상이다.
최신 연구 결과가 정식 UI/API에서 가능한 기능으로 자동 승격되지는 않는다.

## 7. 단계와 완료 조건

| 순서 | 구현 단위 | 완료 조건 |
| --- | --- | --- |
| I1 | 공통 Segment/Case와 평가 결과 연결 | **구현·제한된 인수:** 단일 recording/channel의 참조 Segment, 영속 Case 개정·평가 링크, 원본/평가 왕복, UI/API/SDK. [범위·사용법](cases.md) · [실제 업무·복원본 인수](internal-cases-acceptance-20260928.md) |
| I2 | 조건 taxonomy·태그·검색·초기 coverage | **첫 단위 구현:** 내장r1 조건분류/수동검토선언, 고정평가근거의진단태그, 검색·36셀사례coverage. 미분류/중복/미평가보존. 사용자taxonomy편집·자동장면인식·ODD/실행coverage는후속. [범위](case-conditions.md) |
| I3 | Case→재시험→coverage 환류 | **첫 단위 구현:** 수동 object-reference template→Scenario/Suite→일반 campaign/queue→Case 결과 capture·활동 집계. source 조건을 실제 재현한 sensor/ODD coverage는 아직 없음. [범위](case-retests.md) |
| I4 | 외부 모델·도구 adapter | **첫 연결:** Case의 전체 원본 split ONNX→동일 COCO→A/B→capture와, 기록 ego/LiDAR 전체prefix→저장stack개정→요청비교→capture를 연결. 후자는기존기하stack의open-loop이며학습형멀티카메라E2E·새공급사SDK인수가아님. [모델](case-model-replays.md) · [기록 시계열](case-stack-replays.md) |
| I5 | 내부 팀 운영 | 내부 승인 데이터·두 번째 사용자로 반복 업무, 필요한 역할/접근 통제, 지원 용량·응답시간·복원/설치 기준 인수 |
| D1 | 여러 원본의 Collection→Dataset 선별 | **첫 단위 구현:** Case/dataset revision/기존 image의 고정 선택, 정확한 중복·모든 제외·미검토 보존, 기존 dataset+출처 receipt 원자적 발행과 UI/API/SDK. [범위](collections.md) |
| D2 | 모델 보조 자동 라벨링 | **첫 단위 구현:** 빈 미검토 이미지의 등록모델추론→미검토개정→사람수정/검토. 원래label·실패·모델출처보존,known생성모델자기평가거절. scene tagging·유사검색은후속. [범위](automatic-labels.md) |
| D3 | 장면 특성 검토·시각 유사 검색 | **첫 기준선 구현:** 고정Collection→RGB통계/배치·히스토그램→규칙제안·사람장면검토→유사검색→선택Collection+전체검색receipt. 미지원·미분류·중복·제외보존. 학습형semantic/자연어검색·자동날씨인식은미구현. [범위](scene-discovery.md) |

팀 사용에 필요한 보안은 I5까지 미루라는 뜻이 아니다. 공유 데이터 접근을 열기 전에 해당 경계를 검증한다.
연구와 독립적인 설치·복구 검증은 병행하되 판매 준비·패키지 보완만을 다음 업무로 반복하지 않는다.
각 단계는 작은 기능 연결을 일반 제품에 넣고 완료한다. 모델 성능 실패가 나오면
플랫폼 결함/지원 범위 부족/시험 대상 결함/모델 유효성 불명을 분류하고 별도 담당·종료 기준으로 넘긴다.

## 8. 센서 고유 내재화와 E2E 확장

추후 CameraE2E/Perception ISP/센서 prior 및 Lab·SIL·HIL 근거를 공통 SensorConfig와
TestSpec/Evidence에 연결할 수 있도록 설계한다. MTF/PSF·spectral/QE·noise/HDR·timing/보정 등의
실제 모델과 검증 범위는 해당 adapter 인수로 별도 증명한다. 현 단계 연결 완료를 주장하지 않는다.

학습형 E2E 모델까지 확장할 때는 단일 검출 프로파일에 맞추지 않고
시계열·다중센서·자차/경로 입력, 내부 state reset, 궤적/행동/제어 출력, 실행 주기와 지연을
명시한 별도 모델 계약을 추가한다. 학습 framework·동역학 엔진 자체의 재구현은 필수가 아니다.

사용자가 선택한 후속 순서는 **자동 라벨링 → 장면 태깅·유사 검색 → 검증 계획 → 실제 E2E·센서 모델 연결**이다.
자동 라벨링의 첫 단위는 [모델 제안·사람 검토](automatic-labels.md)로 구현한다. Collection의 고정
dataset과 기존 queue/편집기/평가기·근거 체계를 재사용하고, 모델 출력의 정답 승격은 금지한다.
그 첫 후속은 [Scene discovery](scene-discovery.md)의 pixel-rule/사람 태그 분리와 시각 검색→Collection으로 연결한다.
이 기준선의 의미검색 한계를 유지하며 다음 통합 기능은 **검증 계획**이다. 학습형 semantic adapter는
독립된 기능 강화 과제로 남기고 단순 색상 유사도를 이미 완성된 의미 검색으로 확대하지 않는다.
반복 평가 기준 묶음과 명시적으로 승인한 A/B 작업 흐름도 유지하되, 이를 별도 시스템으로 먼저
확장하느라 사용자가 선택한 기능 순서를 대체하지 않는다.
승인된 실제 모델·데이터의 업무 인수와 시계열/다중센서 E2E 계약은 별도 핵심 과제로 병행한다.
합성 인수를 실데이터·주행 정책 인수로 바꾸어 말하거나 detector/예제 CARLA 성능 튜닝으로
전체 개발 순서를 되돌리지 않는다.

## 9. 공개 기능 참고 자료

2026-09-28 확인. 아래는 기능 책임을 정하는 참고이며 성능 비교 시험이나 공급사 연동 계약은 아니다.

- Foxglove: https://docs.foxglove.dev/docs/data ; https://docs.foxglove.dev/docs/visualization
- FiftyOne: https://docs.voxel51.com/user_guide/evaluation/index.html ; https://docs.voxel51.com/brain/index.html
- Applied Intuition: https://www.appliedintuition.com/physical-ai
- Foretellix: https://www.foretellix.com/foretify-evaluate/ ; https://www.foretellix.com/foretify-generate/

## 10. 현재 검증 계획 첫 단위 · 2026-10-04

[Verification Plan v1](verification-plans.md)을 Validation에 추가했다. 고정 Dataset/모델 개정·편집 가능한
계획 조건·기준·기존 맥락 근거 → preview/명시적 A/B 제출 → worker/실제 COCO 조건 평가 → 공백/악화 →
조사 화면 왕복/새 개정/재실행/export가 현재 구현 범위다. 연구용 VAD-Tiny+nuScenes mini와
CameraE2E 보존 RAW의 ISP A/B를 다음 실제 연결 대상으로 선정했지만 이 단위의 인수로 주장하지 않는다.
임의 크기 bin/시간 구간/자동 매핑과 팀 권한·승인된 실데이터·안정적 내부 운영은 별도 남은 gate다.
