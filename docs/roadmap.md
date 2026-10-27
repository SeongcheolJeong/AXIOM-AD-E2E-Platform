# 단계별 제품 로드맵

## 소스 공유·재현 가능한 예제 · 2026-10-27

GitHub용 선별 소스, 작은 SQLite 3개와 결과 파일, 최소 설치·별도 CARLA 안내,
DB hash/복사/API·브라우저 재생 검사를 추가했다. 기본 참조 작업은 외부 simulator
없이 확인할 수 있다. 후속은 선택 native 환경별 설치 인수, 플랫폼 자체 공개
라이선스 결정과 배포물별 권리 확인이다. 실제 장비·센서 상관·상용 release gate를
이번 샘플 조회 검사로 대체하지 않는다. [검증 범위](github-verification.md).

## 한국 지도 내부 베타 · 실제 원본에서 주행·실패 조사까지 · 2026-10-24

원본 bytes·권리·불변 개정 → 변환/진단 → durable queue 기반 native 확인 →
서버의 근거 대조 후 map catalog 게시 → Object Sim의 고정 지도 시나리오 작성
흐름을 UI/API/SDK에 추가했다. 현재 실제 소스는 세종 OSM이며, OSM→XODR 사전
변환과 일반 제품 내 XODR 등록을 구분한다. NGII SHP 입력부는 선택 패키지로
구현했고 실제 NGII 자료 없이 합성 semantic 검사 31개를 통과했다.

세종 8 ways의 중심선 길이 합계 약 1.639 km를 16 OpenDRIVE roads·3 junctions로
변환했다. 원본/첨부 5개 bytes와 해시를 보존하고 실제 CARLA 지도 로드에서
동일 candidate/readback 해시, drivable nodes 1,682·spawn points 20과 정리를
확인했다. 이 인수는 지도 로드·차로 읽기이며 주행·센서·물리 검증이 아니다.
동일 map catalog r1 게시와 직진·좌우회전 네 시험 개정의 API 작성을 완료했다.

별도 3 m/s 실제 주행에서 직진·좌회전은 pass, 우회전은 횡오차 p95 0.715182 m가
0.5 m 기준을 넘어 fail이다. 실행 완료와 평가 통과를 분리하며 기준·실패 원본을
유지한다. 일반 UI에서 좌회전 r1의 속도 2 m/s·duration 30 s·이름을 편집하고 r2
저장·큐 제출·실행·재생으로 이어갔다. r2 횡오차 p95 0.305802 m로 pass하며 실제
4실행은 3 pass·1 fail이다. 별도 API로 작성한 2 m/s r1은 실행하지 않았다.
우회전 frame 272·13.65 s를 실제 UI에서 Case r1로 저장하고 새로고침 복원·봉인
download와 SDK get/export의 4,305 bytes·SHA를 대조했다. Case는 객체 상태 JSON
한 프레임과 원본 지도·시나리오·실행 계보를 보존하며 새 센서 측정이 아니다.
다음 작업은 우회전의 지도 형상·경로·제어 기여 원인을 분리하고 새 개정으로
검증하는 것이다. 전체 1.639 km 연속 주행이나 다양한 조건의 일반화 인수는 남는다.
이후 NGII 원본·사용조건을 확보하면 300–500 m source geometry·폭·방향·연결을
먼저 확인하고 명시 교차로와 1–3 km로 확장한다. OSM 기본폭·차로수·교차로 가정,
NGII 작성 보완값, 로드 인수와 실제 주행 거리·결과를 계속 분리한다.
공개 배포·재배포 인수와 상용 release gate는 별도로 남는다.
[한국 지도 베타 계약과 근거](korea-map-pilot.md).

최종 운영 snapshot은 본 베타 active job 0, 2100 OpenDriveMap의 dynamic actor 0,
공통 lock free·quarantine 없음, 기존 엔진 관련 PID 4개 보존이다.
[소개 HTML](../reports/axiom-korea-map-introduction-20261024.html)과
[베타 안내 HTML](../reports/axiom-korea-map-beta-guide-20261024.html)을 추가했다.
파일 링크·JS 문법·미디어 참조 정적 QA는 통과했으나 브라우저 `file://` 정책으로
HTML 시각 검토는 미실시다. 실제 제품 UI 인수 완료와 상용 release gate를
혼동하지 않는다.

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

## 6.75초 FPGA 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.85초 전원 강하 원본을 유지하고, CH0에6.75초200µs 정체를 추가한 새
native RTL·ECU·vCAN·CARLA A/B를 실행했다. 기존2.5초 source를4.5초·채널당
135프레임으로 확장했다. 4×1080p30·100MHz 그대로 정상540commit,
고장536commit/4abort, 비고장 채널135commit씩이며 픽셀/프로토콜 오류0이다.
이전 원본과 공통300프레임씩의 해시·상태·terminal·epoch도 일치했다.

입력6.75초의 해제 후보가6.80초 FPGA 재검출로 취소됐고, 보호 중 CAN87건·
Brake435건 모두 제동100%였다. 전체 해제는
6.805512→7.405788초,
CARLA 재이동은7.00→7.60초다.
보호 정책은 유지하고 실제 후보 순번/완료 구간과 최소 hold를 검증했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
625개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, 원본 종료 뒤 tail_hold와 물리/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 근거](validation/hil-recurrent-fpga-6750-20261018/acceptance.md).

## 5.85초 전원 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.25초 FPGA 재발 원본을 재사용하고, native 전원 해석을4초로 늘려
5.85–6.15초6V 강하를 추가한 새 ECU·vCAN·CARLA A/B를 실행했다.
최대20µs와 보호 정책은 유지했다. 실제 native 표본은 정상200,008개/고장200,080개다.
요청/실제 표본250,000개·native64MiB·연결 artifact32MiB의 제한을 유지한다.

입력5.85초의 해제 후보가5.90초 전원 재검출로 취소됐고, 보호 중 CAN75건·
Brake375건 모두 제동100%였다. 전체 해제는
5.905587→6.805512초,
CARLA 재이동은6.10→7.00초다.
후보 생성이 처리 위상에 따라 한 주기 늦어질 수 있음을 실제 보고 순번과
가변 위상 단위 재현으로 검증했다. 최초 고정순번 검사 실패도 보존했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
620개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, source 완료 뒤 tail_hold와 실물/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 검증 근거](validation/hil-pending-power-20261018/acceptance.md).

## 5.25초 FPGA 재주입 · 해제 후보 취소 · 2026-10-18

해제 검증 대기 중 5.25초에 CH0 정체 200µs를 추가해 native RTL·참조 ECU·vCAN·CARLA를
새로 실행했다. 기존 1초 source로는 늦은 고장을 주입할 수 없어 4×1080p30·100MHz를
유지한 75프레임/채널·2.5초 원본을 양쪽 새로 만들었다. 정상 300commit,
고장 297commit/3abort, 다른 채널 75commit씩, 픽셀/프로토콜 오류 0이다.

입력 5.25초의 해제 후보가 5.30초 FPGA 재검출로 취소됐다. 보호 중 CAN 57건·
Brake 285건이 모두 제동 100%를 유지했다. 전체 해제는 5.305826→5.905587초,
CARLA 재이동은 5.50→6.10초다. 같은 정상 side의 360개 제어가 일치했고,
720개 차량 제어·1,200개 측정 창을 원본과 대조했다. UI에 실제 입력 프레임 수
설정과 ‘해제 후보 취소 관측’을 추가했다. 후보 취소는 ECU 보고 상태 전이의
관측이며, 정확한 내부 취소 시각으로 표시하지 않는다. 로컬 검사 545개·UI 29개·build가 통과했다.

브라우저 워크플로는 정책 차단으로 미검증이다. 봉인 native 측정 재생이며 source
완료 뒤에는 마지막 관측 상태를 유지한다. 실물 ECU/전원·하드 실시간·안전 검증과 구분한다.
[CAN·영상 A/B와 검증 근거](validation/hil-pending-fpga-20261018/acceptance.md).

## 50ms 경계의 보호 해제 검증 개선 · 2026-10-18

복합 모델의 마지막 개별 보호가 풀리면 바로 제동을 해제하던 경로에
`fresh-window-v2` 검증을 추가했다. 해제 후보 뒤 연속 두 완료 측정구간을 확인해
후보 이후 온전한50ms 구간을 포함한다. 새 이상·누락·반복·미래·순번 건너뜀·
입력나이10ms 초과는 해제를 막는다. 기존500ms·정상3표본·8V 기준은 유지했다.
개별 고장이 없어도 전체 제동이 유지되는 `해제 검증 대기`를 UI에 구분했다.

보존한 원본 감시 코드와 개선 코드를 같은500개 타이밍 조합에 적용했다.
기존255개 조합의50ms 공백을 재현했고 개선 후에는0개였다(단위 재현).
별도로4.349/4.350/4.351/4.399초 전원 재주입을 각각 native 모델·vCAN·CARLA로
독립 실행했고 모두5ms 조건을 통과했다. 보호 중ADAS CAN182건·Brake910건에
중간 해제가 없고 차량제어2880개·native 측정4800개를 원본과 대조했다.
동일4.35초 기준 전체 해제는5.255772→5.305826초,재이동은CARLA5.45→5.50초다.

기존/개선 A/B는 두 봉인 출처의 실행기·설치 차이가 감시 코드 한 파일에만 있는지
검증하고 로직 변경을 화면에 명시한다. 기존 결과·export는 유지하고,구계약의
새 실행은 개선 binding 저장 전 거절한다. backend/adapter/SDK637개·UI28개·
build가 통과했다. 브라우저 인수는 기존 정책 차단으로 미검증이다.
검증 범위는 기록한50ms 측정·입력나이≤10ms·주입/처리 위상이다. 최신 구간 이후
새 고장의 미래예측·실물 ECU·하드 실시간·안전 보장은 포함하지 않는다.
[CAN 파형·CARLA A/B·반복 검증 근거](validation/hil-release-qualification-20261018/acceptance.md).


## 4.35초 전원 강하 재발·FPGA 보호 해제 교차 · 2026-10-18

전원 모델에 재주입 일정·최대 해석 간격을 추가하고,1.75초 native ngspice A/B를
실행해4.35–4.65초6V 강하를 기존3.8초 FPGA 재발 시험에 연결했다. 첫 강하는
3.003–3.303초 그대로다. 최대20µs·87,556 native 관측점·1:1 재생이며 기존
4×1080p30·100MHz FPGA 원본과경계별500ms·정상3표본 정책은 유지했다.

새 CARLA A/B는5ms 조건을 통과했다.4.405초 처리 주기에서FPGA 보호 해제와
전원 재검출이 교차해도제동100%를 유지했고,전체 보호 해제5.255772초 뒤
CARLA5.45초 관측에서 재이동했다(기존4.60초). 보호 중ADAS CAN44건·Brake220건에
제동0이 없고720개 차량 제어가 수신CAN과 일치했다. 개정·preview·export·인과·
단독/복합 반복 표식,backend/adapter/SDK/SPICE59개·UI26개·build가 통과했다.

**남은 위상 경계:**50ms 측정 구간 때문에4.35초 강하가4.40초 입력에 관측된다.
다른 합법적 스케줄 위상에서FPGA500ms 보호가4.35초에 해제되면50ms command
공백이 가능한 authored unit counterexample을 보존했다. 이번 실행에서는 공백이
없지만일반 무공백/안전 보장은 아니다. 입력 관측 age를 고려한전체 release
qualification과모든경계위상 검증은후속이다. 보호 시간을 조정해 결과를 통과시키지
않았다. 브라우저 인수는 기존정책차단으로미검증이며실물검증이 아니다.
[실행·A/B 영상·검사·위상 한계](validation/hil-power-recurrence-20261018/acceptance.md).


## 3.8초 FPGA 정체 재발·전원 보호 해제 교차 · 2026-10-18

FPGA 모델에 재주입 일정 편집·저장·native 실행을 추가했고, 단일/복합 연결의
주입 지도와 인과 표식에 모든 구간을 보존한다. 이전/현재 고장 측 A/B 화면은
첫 검출과 FPGA 재검출, 개별/전체 보호 해제를 구분해 영상 커서로 이동한다.
빈 재주입 목록은 기존 단일 정체 요청의 serialization/hash를 유지한다.

3.8초100µs 재주입은 프레임 초기 버퍼가 흡수해 두 번째 abort·ECU 재검출이
발생하지 않았다. 이 결과를 보존하고 시작은3.8초 그대로, 지속200µs로 새 시험을
실행했다. 4×1080p30·100MHz·1초 입력을 유지해 정상120완료,고장118완료/2무효,
비의도 픽셀·프로토콜 오류0을 확인했다. 기존400ms ngspice 전원 원본을 재사용했다.

새 CARLA A/B는5ms 조건을 통과했다. FPGA 재검출3.855662초 뒤 전원 보호가
3.855702초에 해제돼도 FPGA 보호가 유지됐다. 전체 해제는4.405669초,
CARLA 재이동 관측은 기존4.05초에서이번4.60초로 늦어졌다. 전체 보호 구간의
ADAS CAN27건·Brake 출력135건에 제동0이 없고720개 차량 제어가 수신 CAN과
일치했다. backend/adapter/SDK47개·UI 해석25개와build가 통과했다.
브라우저 클릭·새로고침 인수는 정책 차단으로 미검증이며 실물 검증은 아니다.
[기존/재주입 A/B와 근거](validation/hil-model-recurrence-20261018/acceptance.md).


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


## Network Lab 이상 검출·제동 시간 추적 · 2026-10-17

UDP 지연과 Restbus 단절의 검출 근거·CAN 송수신·실제 적용 제어·후속 차량 관측을
보존 trace에서 연결했다. 일반 UI/API/SDK에서 같은 분석을 사용하고, 서로 다른 실행의
B측 비교와 단계별 영상 이동·URL 복원을 지원한다. 다른 고장 종류의 검출 인과 추적과
물리 네트워크·ECU 제동 응답 검증은 후속이며 미관측 시점을 추정으로 채우지 않는다.
[검증 근거](validation/vecu-network-causality-20261017/receipt.json).

## Network Lab A/B 해석 · 2026-10-17

선택 실행에 고정된 A/B 조건과 고장 일정을 표시하고, 선택 시점의 고장 위치·ECU
보호·차량 반응을 한글로 연결했다. 고장 주입 종료, Brake 회복 확인, 제동 해제,
차량 재이동을 분리하며 상세 통신 경로는 펼쳐 조사한다. 보존된 실제 CARLA 결과의
표시·재개 검증이며 제어 정책 변경이나 새 물리 ECU 검증은 아니다.
[화면·검사 근거](validation/vecu-network-explanation-20261017/receipt.json).

## 통신 회복 경계의 리셋 · 2026-10-17

Restbus 단절 도중 시작한 200ms ADAS 리셋이 통신 회복 시점을 가로지르는 실행을 연결했다.
실제 6.9초 리셋에서는 heartbeat 복구 후에도 새 CAN 명령 3개를 확인하기 전까지 제동이
유지됐다. 기존 5초 리셋과 동일 시각의 영상·상태 및 제동 해제 지연 150ms를 비교한다.
[실행·비교](validation/vecu-network-restbus-reset-boundary-20261017/5s-vs-6_9s.md).

## Restbus 단절 + ADAS 리셋 중첩 · 2026-10-17

Restbus 단절 안에 포함된 ADAS 리셋의 저장·native 실행·영상 A/B·고장별 커서 표시를
연결했다. 새 CARLA A/B에서 리셋 중 제동 유지, heartbeat 복구 후 제동 해제·재출발을
관측하고 720개 제어 단계와 영상 원본 720프레임을 확인했다. 다른 중첩 조합은 아직
지원하지 않는다. [실행 근거](validation/vecu-network-restbus-reset-20261017/comparison.md).

## Virtual ECU Network Lab · 공개 통신 스택·OS 실행 연결 · 2026-10-17

SIL Kit CAN/Ethernet 가상 프레임과 autoas Com/PduR/CanIf, Trampoline OSEK
Task/Alarm/Event를 네 ECU 역할의 실행 경로에 연결했다. 네트워크 고장과 OS 이벤트
누락을 분리하고 C++ 제어 → CAN → Brake → CARLA 적용을 일반 UI/API/SDK로 조사한다.
[구현·검증 경계](virtual-ecu-network-lab.md) ·
[실제 native 인수](validation/vecu-network-20261017/native-acceptance-r3/receipt.json).

7종 고장의 18초 CARLA 정상/고장 A/B 7쌍을 실제 실행했고, 5,040개 제어 단계와
영상 5,040프레임 및 196개 artifact를 재검사했다. UDP 고장, CAN 전달 단절,
Restbus 소실, ADAS 리셋, OS 이벤트 누락의 발생 위치와 제동·회복 차이를 보존한다.
[CARLA A/B·실행 링크](validation/vecu-network-20261017/carla-comparison.md) ·
[원본·제어 감사](validation/vecu-network-20261017/carla-checks.json).

다음 단계는 실제 고객 신호 DB·SWC/RTE 계약의 운영자 등록, 긴 단절 후 카운터
재동기화, CAN 부하/중재 모델, FMU 및 진단 프로토콜, 공급된 상용 BSW·양산 ECU
소프트웨어의 별도 실행 어댑터다. 현재 공개 BSW와 OSEK 서비스 실행을 전체 AUTOSAR,
Silver 주요 기능 완성, MCU 시간 정확성 또는 실제 ECU HIL로 표현하지 않는다.

## Virtual ECU · C/C++ 실행 패키지 첫 단계 완료 · 2026-10-17

등록 소스의 실제 Linux C++ 빌드와 immutable package A/B를 일반 UI/API/SDK에 연결했다.
동일 18초 CARLA 시나리오의 정상·지연·단절·반복 네 쌍을 실행하고 영상·상태·제동·
정지 거리·복귀 시각을 비교한다. 고정 입력 재생의 반복성은 폐루프 차량 실행과 별도 검증한다.
[실행·결과](validation/vecu-cpp-20261017/comparison.md) · [구현 계약](virtual-ecu-lab.md).

다음 범위는 외부 사용자 C/C++ 소스의 운영자 등록·재현 가능한 빌드, 신호 사전과 단위 검증,
시험 중 calibration 계약, FMU와 추가 버스 프로필이다. 기능 수준 CAN/Ethernet과
공개 BSW·OSEK 실행은 별도 Network Lab에 연결했다. 실제 양산 코드·상용 OS/BSW 또는
타깃 바이너리를 확보한 뒤 해당 fidelity의 실행 어댑터를 추가한다. 현재 C++ 참조 실행으로 Silver 호환,
AUTOSAR/Qualcomm 가상화, 센서 인지·카메라 RTL 연결 또는 물리 HIL 완료를 주장하지 않는다.

## CARLA 패킷 반복 전달·검출 · 2026-10-17

Virtual ECU Lab에서 input_repeat을 저장·실행하고 영상의 중복/미수신 카운터를 확인할 수 있다.
새 reference DUT v2에서 반복 80회와 단절 80회를 구분한다. 중복 payload 변경을 거부하고,
중복 패킷으로 freshness 또는 신규 패킷 3회 회복 조건을 만족시킬 수 없다.
3–7초 repeat/drop/400ms delay를 실제 CARLA에서 각각 새로 실행했다. 세 조건 모두
3.25초 degraded → 7.00초 recovering → 7.10초 tracking이며 제어·속도 차이는 0이었다.
이는 현재 동일한 freshness 기반 완전 제동 정책의 결과이며 고장 전달 방식이 같다는 뜻은 아니다.
이전 v1 delay/drop과의 제어·속도 차이도 0이다. 관련 검사 216개와 production UI build 통과.
[실행/개정](validation/vecu-repeat-20261017/receipt.json) ·
[비교/검출 근거](validation/vecu-repeat-20261017/comparison.json).
UI 5310/API 8210 및 별도 remote source directory를 사용한다. 기존 서비스·결과는 유지한다.
실제 물리 ECU/네트워크/CSI 검증은 아니다.

## Virtual ECU Lab · 2026-10-17

호스트 ADAS reference 프로세스의 패키지/객체 입력/시험 개정 → native 실행 → A/B 조사 →
정확한 URL 재개·trace/export를 일반 UI/API/SDK에 연결했다. 20 Hz 고정 재생과 기존 CARLA
폐루프 실행을 분리한다. 정상·지연·중단·반복·설정 A/B 및 반복성 시험, 실제 CARLA 등
공개 SDK 실행 8개가 완료됐고 artifact/export hash를 확인했다. 관련 검사 92개 통과.
[기능·계약·제한](virtual-ecu-lab.md) · [실행 근거](validation/vecu-20261017.json).

현재 reference application이며 양산 ECU, Qualcomm 내부, Silver 호환, 실제 HIL 검증이 아니다.
카메라 RTL과 RGB→객체 인지는 연결하지 않았다. 기존 결과와 서비스를 유지하며 새 UI 5300/API 8200을 사용한다.

## ADAS 보드 전 검증 확장 · 2026-10-17

장시간·다중 seed·복합 고장·FIFO 경계 캠페인과 실제 4상태 초기화 검사,
정상 commit/abort 수신 계약을 일반 UI/API/SDK에 연결했다. ULX3S 85F를 미연결 예제로
선정해 공식 핀·클록 자료와 생성 PLL을 제공한다. 현재 구현/실행 근거는
[검증 범위](adas-preboard-validation.md), [상태](status.md),
[공개 SDK 캠페인](validation/adas-preboard-20261017.json)에서 확인한다.

10초·4×1080p30 및 세 seed의 고장/초기화 캠페인 19개와 같은 RTL의 103.295 MHz OOC를
실제 공개 worker에서 통과했다. 60초 전체 해상도 및 수 시간 soak는 아직 실행 근거가 없다.

다음 실제 보드 게이트는 CSI 수신 칩·포트와 고속 RGB 전송 경로 선정, 전체 board top,
호스트/입출력 CDC, PLL·I/O timing, 전원/reset sequence 및 실제 FPGA 결과 비교다.
4상태 축소 core 검사와 독립 CDC 참고 모델 통과를 이 게이트의 완료로 간주하지 않는다.
60초 설정은 지원하지만 실제 10초 실행과 장시간 soak coverage는 별도로 기록한다.
상용 release와 물리 HIL 게이트는 계속 열려 있다.

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

## FPGA Lab v1 · 2026-10-13

`HIL → FPGA Lab`에서 합성 RAW 1채널의 고정 경로를 편집·개정 저장하고 실제 SimPy
CPU 계산을 제출할 수 있다. DDR/FIFO 점유율, Trigger/송수신 파형, 사건·프레임 재생,
원인 사건, 저장 결과 A/B, 정확한 개정·시점 복원과 JSON/CSV를 UI/API/SDK에 연결했다.
정상·대역폭 부족·공급 지연·타이밍 고장 네 예제를 실행했고 정상 60/60 수신,
60 FPS 대역폭 부족 99/120 수신·21 overflow, 공급 지연 60 deadline 초과,
고장 주입 6 누락을 모델 결과로 확인했다. 실제 FPGA/RTL/PHY/ECU 측정은 아니다.

집중 backend/기존 HIL/Experiment 검사 53개, 실제 SimPy 브라우저 workflow 2개와
production build를 확인했다. 브라우저에서 제출 응답 유실 후 새로고침·재시도에도
실행 1건을 유지한다. 로컬 8098/5198 미리보기와 별도 DB를 준비했으며 기존 서비스는
재시작하지 않았다. 일반 build의 큰 bundle 경고는 남아 있다.
다음은 사용자 설계 조건 피드백이며 RTL 예제·실제 RAW·보드 연결은 별도 단계다.
[사용·모델 계약](fpga-lab.md) · [실제 계산 근거](validation/fpga-lab-20261013.json).

## Sensor 배치 편집 베타 · 2026-10-11

고정 네 센서의 B 위치·회전 편집 → 개정 저장·복제 → 기존 주행 A/B 실행 연결 → 원본 비교·관찰 →
베타 과제 이어하기를 UI/API/SDK에 연결했다. 격리된 로컬8088에서 편집·개정 복원·복제·JSON과
공통 과제 연결을 확인했다. 사용자 승인 전환으로 저장 r2의 실제 A/B 한 작업을 완료했고,
양쪽 각각200tick·800개 관측·누락0과 브라우저 네 센서 재생·9.9초 관찰·이어하기·JSON을 확인했다.
HIL2100·8075를 복원했으며 Sensor8088에서는 작성과 보존 결과 조사를 계속할 수 있다.
**다음은 실제 참가자3–5명의 독립 업무 완료와 막힌 단계 수집**이다. 새 Sensor 실행은 GPU 사용
시간과 엔진 준비를 먼저 확보한다. 센서 종류/개수 확대·세부 파라미터 편집·물리 상관성은 후속이다.
[범위·확인 상태](sensor-sim-beta.md) · [개발자 native 업무 인수](validation/sensor-layout-native-20261011.json).

## 공통 내부 베타 · 2026-10-11

시작 화면의 준비 상태 → Object/Log/HIL 과제 → 정확한 저장 개정 이어하기 → 문제 신고 →
운영자 처리·내보내기의 공통 계층을 제공한다. 기존 모듈의 실행과 결과를 재사용하고,
보존 결과 체험과 새 실행 완료를 구분한다. 다음 인수는 내부 3–5명의 독립 사용과 막힌 단계 수집이다.
참가자 접속 주소·대표 입력·실행 시간·저장 공간을 준비한 뒤 실제 사람의 완료를 확인한다.
공개 배포·기존 서비스 재시작·SSO·물리 ECU·대규모 동시 실행은 별도다. [작업공간 계약](beta-workspace.md).

## HIL 사용자 시험 관리 베타 · 2026-10-11

시험 저장·개정·복제, 정확한 개정별 실행 연결·이력, A/B 시점의 관찰/문제 기록과 JSON을
정상 UI/API/SDK에 연결한다. 작성·기존 결과 재생을 native 가용성과 분리하고,
GPU 환경과 전용2100·8075 worker를 복구하고, 정상 Engineer 브라우저에서 저장400ms/r2의
새 native A/B1건(양쪽 각각 제어360개·RGB360장·누락0)과 관찰·북마크 복원을 확인했다.
공유8GiB GPU는 현재 HIL에 할당되어 발표용2000은 중지돼 있다. 기본 시스템 NVML 불일치는
남고 HIL 프로세스에만 kernel과 일치하는595.84 환경을 적용한다.
실제 참가자3–5명의 업무 완료, 동시 사용자 실행 제한과 GPU 시간대 운영은 별도로 남긴다.
고장 시간 편집은 단일 구간의 시작·종료 저장과 실제 실행까지 완료했다. 3–7초를 2–5초로
바꾼 r3의 새 native A/B에서 고장 입력 60스텝·응답 2.05–5.05초·DUT 회복 5.15초를 확인했다.
양쪽 각각 제어/RGB 360개·누락 0이며 기존 7건의 receipt hash를 보존한다.
DUT 임계값·회복 프로필의 저장·개정·복원과 시험별 A/B 고정도 UI/API/SDK에 연결했다.
같은 고장의 A 250ms/3회와 B 500ms/8회를 비교한 지연·단절 native A/B 2건을 완료했다.
각 실행의 양쪽 제어/RGB는 각각 360개·누락 0이며 기존 8건의 hash를 보존했다.
정상 브라우저에서 지연·단절 결과 조사와 시점 관찰 저장, JSON 다운로드를 확인했고
SDK export의 원본 연결도 확인했다. 기존 정상/고장 모드와 참조 DUT 기본 정책은 유지한다.
③ 선택한 3–5개 조건의 순차 실행도 UI/API/SDK에 연결했다. 서버가 순서를 관리하며
중단·실패 뒤 미완료 조건만 명시적으로 재개하고 원본 trace의 DUT 반응 요약을 제공한다.
실제 3조건의 완료 3건·실행 중 취소 2건을 보존했고, 두 조건 완료 후 API 재시작·재개에서
마지막 조건만 실행했다. 각 완료 A/B의 제어/RGB는 각각 360개·누락 0이며 기존 영수증
10건과 catalog 56개 행을 보존했다. 다음 접수 실패·완료 기록 보존 경합은 작성한 handler
검사로 확인했으며 실제 엔진 실패 인수와 구분한다.
④ 장면 확대는 기존 Town10 base의 앞차 감속·정지 후 재출발 2종으로 완료했다.
일정의 개정/hash를 저장 시험·실행·관찰·export에 연결하고 두 장면의 native A/B를 확인했다.
감속은 실제 응답 4.15초에 ≤0.75m/s, 정지·재출발은 4.20초에 ≤0.2m/s·8.45초에 >0.5m/s였다.
각 A/B의 제어/RGB는 각각 360개·누락 0이며 기존 영수증 15건의 bytes를 보존했다.
감속의 브라우저 저장·실행·관찰·export와 재출발의 SDK 저장·실행을 확인했고,
재출발 결과의 브라우저 영상·관찰·시점 복원과 두 JSON의 SDK 대조도 완료했다.
세 장면의 혼합 목록은 UI 저장·복원까지이며 아직 시도 0건이다.
새 지도나 독립된 장면 다양성 확보는 아니며 기존 2100 엔진은 재시작하지 않았다.
로컬 여유 공간은 약 2.08GiB이므로 다음 native 실행 전에 출력·보존 용량을 확인한다.
다음 구현은 **사용자 DUT**다. 조건 반복을 독립 장면 다양성이나 안전
커버리지로 세지 않으며 실제 참가자 업무 인수·공개 운영·실제 ECU·하드 실시간은 남아 있다.
[시간 편집 근거](validation/hil-fault-timing-20261011.json) ·
[DUT 프로필 근거](validation/hil-dut-profiles-20261011.json) ·
[순차 실행 근거](validation/hil-sequential-20261011.json) ·
[장면 확대 근거](validation/hil-scenes-20261011.json).
[베타 범위·순서](hil-sim-beta.md).

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

> **Object Sim 초대형 베타 · 2026-10-10:** 3–5명에게 템플릿 복제→객체/경로/행동 편집→
> 개정 저장→실행→A/B→관찰의 업무를 제공한다. UI·버전 API/SDK·개별 권한·큐 한도와
> 네 템플릿의 새 native A/B8건을 완료하고 응답/RGB 각각5,160개·누락0·행동 발생과 자차 도착을
> 확인했다. 최종 브라우저의 수정 native1건·RGB A/B·관찰 저장·재실행 취소를 포함한20항목과
> page error0을 확인했다. 첫 warm-up 실패는 보존하며 GPU 버전 불일치는 사용자 폴더의595.84
> 라이브러리로 우회했다. 비교 설정을 간결하게 정리했고 최종 보존 결과 브라우저4항목도 통과했다.
> 빠른 시연 네 장면도 같은 testerA 프로젝트에 정상 API로 준비하고 별도 native6건의 결과를
> 연결했다. 응답5,280개·RGB2,880장·누락0, 평가는5pass·예상 목표 fail1건이다. 장애물 B는 출발점
> 정지, 간격 B는 후방차만0.4초 먼저 통과하고 자차 대기는 같으며, 교차로8대 도착·도시 작성 wait의
> 실제 정지2.15초를 확인했다. 첫 세 장면은 generated map, 도시는 native Town10이다.
> 참가자 브라우저 RGB5종·A/B3종/새로고침8개 묶음과 작성6개 묶음을 확인했다. 안내형 story API의
> gateway 허용 목록 누락을 수정하고 gateway148개·backend story45개 검사와 정상 브라우저 안내3개
> 묶음의 timeline·양쪽 RGB·정확한 URL 복원을 확인했다. 추가 실행 후13:38:55UTC에2160 종료·발표용2000
> 복원과 베타 API 작성/재생 모드 전환을 확인했다. 새 native 실행은 의도적으로 비활성화했으며,
> 다음 묶음은 전용 베타 시간대 또는 별도 GPU와 저장 용량 확보가 필요하다. 외부 HTTPS·실제 사용자80% 완료 인수,
> 지도·asset 수 확대와 물리·안전 검증은 후속이다. [범위·현재 상태](object-sim-beta.md) ·
> [추가 시연6건 근거](validation/object-sim-quick-20261010.json).

> **다음 1주 · 내용이 채워진 사례 데모 · 2026-10-10:** 사례 확보·정리50%, 연결 기능35%,
> 발표 안정화15%로 진행한다. 질문 중심 10개 항목 모두 실제 저장 결과를 UI/API/SDK에 연결했고,
> 12개 고유 실행을 사용한 10개 사례의 정상 브라우저 탐색·재생/pose·리로드·복귀를 확인했다.
> 남았던 HIL200ms는 새 native A/B 1작업으로 완료했다. 각 조건 제어360개·RGB360장·누락0이며,
> 입력 age200ms에서 둘 다 tracking을 유지하나5.00초 속도는 A0.86/B1.60m/s로 다르다.
> 사용자 승인으로 CARLA2000→2100을 잠시 전환한 뒤2000을480×270·Town10HD_Opt·비동기·
> 차량/센서/보행자0으로 복원하고2100을 중지했다. 기존 API 서비스는 유지했으며 HIL3개 브라우저 재확인도 완료했다.
> 새 엔진·대량 데이터 추가보다 세 가지 문제 해결 흐름과 10분 발표·질의응답을 우선한다.
> [구현·발표 동선과 남은 범위](demo-case-library.md).

> **현재 공통 우선순위 · 외부 데모 기능 구현 · 2026-10-10:** 상세 검증 확대보다 정상
> UI/API/SDK에서 눈에 보이는 편집→실제 실행→A/B 영상·신호→Case 연결을 빠르게 완결한다.
> Neural 시간 고정 시점 이동, Sensor 구성 변경, Software-DUT HIL 고장 반응, Object 상호작용,
> Log 사건→Case를 8–10분 화면 공유 흐름으로 구성한다. 기존 대표 결과·checkpoint를 재사용한다.
> 다음은 Sensor 주행 브라우저의 미확인 동선과 모듈별 발표 진입·대표 결과 준비를 마무리하는 것이다.
> 관련 검사·변경 실행의 대표 native run·정상 브라우저 확인 후 다음 기능으로 진행한다.
> 실측 상관성·상세 physics·전체 회귀·상용 gate는 후속이다. 이 항목은 목표 변경이며
> 새로운 실행·통합 리허설 완료 기록이 아니다. [공통 범위·완료 기준](internal-platform.md).

> **Neural Sim 실도로 업무 연결 완료 · 2026-10-10:** nuScenes mini 한 장면96프레임·8초 →
> 실제 Splatfacto 학습 → 원본frame48 고정·±0.5 m의61개 새 시점 → 동일 YOLOX-S ONNX의
> 5개 시점 비교 → Case 저장·정확한 실행 역링크·리로드 복원을 일반 UI/API/SDK로 연결했다.
> 모델 출력 변화로 표시하며 정확도·안전 판정은 하지 않는다. 전경 차선·동적 물체 품질,
> 여러 로그로의 일반화·동적 편집·센서 폐루프는 후속이다. 데이터는 비상업 연구 범위다.
> [시연 흐름](neural-sim-demo.md) · [완료 근거](validation/neural-sim-real-road-20261010.json).

> **Neural Sim 시연 개선 · 2026-10-10:** 고정한 원본 5.00초에서 실제 카메라 위치 61개를
> 렌더해 좌우 3 m를 왕복하는 큰 영상으로 발표 흐름을 바꿨다. 일반 UI 생성·결과 열기·
> 대표 저장, 위치 탐색·원본 작은 화면·현재 pose 표시를 연결했다. 기존 checkpoint를 재사용하며
> 미관측 도로 가장자리의 깨짐은 남는다. [범위](neural-sim-demo.md) ·
> [근거](validation/neural-sim-sweep-20261010.json).

> **Neural Sim 빠른 발표 데모 · 2026-10-10:** 기존 CARLA 주행20초의 전방 RGB → Splatfacto
> 학습 → 좌우/높이 변경 렌더 → 원본/재구성/새 시점 비교를 일반 UI/API/SDK로 연결했다.
> 실제 GPU학습1회·기본20초 렌더3회·정상 브라우저 편집 프레임1회, 재생·슬라이더·1080p
> 발표 화면을 확인했다. 상세 품질 연구보다 발표자 화면 공유의 기능 완성을 우선한다.
> 동적 편집·실도로·E2E 센서 폐루프·물리 검증은 후속이다. [고정 범위](neural-sim-demo.md) ·
> [근거](validation/neural-sim-demo-20261010.json).

> **HIL Workbench 빠른 시연 · 2026-10-10:** 객체 입력 지연·단절 → 별도 프로세스 Software DUT →
> 실제 CARLA 제어 → 영상/신호 A/B를 일반 UI/API/SDK로 연결했다. 실제3작업/6캡처·제어 응답과
> RGB 각각2,160개, 정상 브라우저 편집·제출·결과 복원·1080p발표 화면을 확인했다.
> 로컬 화면 공유용 데모이며 실제 ECU·하드 실시간·물리 검증·상용 gate는 후속이다.
> [사용·후속 범위](hil-sim-demo.md) · [근거](validation/hil-sim-demo-20261010.json).

> **Workspace 연결 복구 · 2026-10-10:** 연결 저장 성공 뒤 현재 화면의 이전 인증 오류가 남던
> 갱신 문제를 수정했다. 새 탭에서 인증 거절·성공·저장 A/B 자동 표시를 확인했으며 기존
> 탭별 인증 정책은 유지한다. [근거](validation/workspace-auth-refresh-20261010.json).

> **Sensor Sim 주행 다중센서 확장 · 2026-10-09:**20초 도심 주행 프리셋과 동기 RGB·LiDAR·radar,
> 시점별 속도·이동 거리 표시를 연결했다. 공개 API 큐의 실제 A/B 각각200시점×4센서,56.85m,
> drop0·cleanup confirmed이며 실제 프런트엔드 parser 확인을 통과했다.
> Mac 잠금으로 정상 브라우저의 새 주행 화면 최종 확인은 대기 중이다.
> [주행 근거와 남은 확인](validation/sensor-sim-driving-20261009.json).

> **Sensor Sim 발표 데모 구현 · 2026-10-09:** 센서 구성 A/B와 발표자 화면 공유 범위를 완료했다.
> 프로젝트별3프리셋(FoV·LiDAR밀도·radar범위) → 간편 편집 → native 실행 → RGB/거리센서 동기
> 비교·전체 화면을 일반 UI/API/SDK로 연결했다. 실제4작업/8캡처·3,200관측 및 정상 브라우저의
> 편집/제출/새 결과/새로고침·1080p화면을 확인했다. 상세 물리 검증보다 기능 구현을 우선한다.
> 기존 첫 범위는 정지 장면이다. 주행 확장은 위에 별도 기록하며 정밀RAW/광학·IQ/ADC·HIL·실측 상관성은 후속이다.
> Radar 첫 표본의 속도 이상치 경고를 보존한다. 로컬 화면 공유 준비이며 외부 공개 배포·상용
> release 완료를 뜻하지 않는다. [범위·사용법](sensor-sim-demo.md) · [근거](validation/sensor-sim-demo-20261009.json).

> **Log Sim 다중 실도로 조사 · 2026-10-09:** 원본10개 장면 선택 →15구간/381표본 일괄 분석 →
> 실제 참조 A/B30작업 →89개 조사 후보 우선순위 → 원본·요청 동기 조사 → 선택 Case 묶음을
> 일반 UI/API/SDK로 연결했다. 원본404표본 중 제외23개 이유와 장면·구간·후보 분모를 분리한다.
> 실제 브라우저의 가속/감속 반대 요청 조사, 선택3개 Case 저장·복원을 확인했다.
> 관련41개 backend/SDK 및22개 입력 검사·build 통과. 원본 GT 기반 open-loop 연구 시연이며,
> 학습 모델 연결·센서 반응 폐루프·상용 데이터 권리는 후속 gate다.
> [범위·사용법](log-sim-demo.md) · [근거](validation/log-sim-batch-20261009.json).

> **Log Sim 조사·비교·반복 시험 · 2026-10-09:** 사건 규칙 검색 → Case → 같은 전체 로그에
> 참조 제어기 A/B 실행 → 요청 차이/원본 왕복 → 고정 회귀시험 저장·재실행을 일반 UI/API/SDK로
> 연결했다. 실제 nuScenes10표본과 Case6표본의 차이, 새 큐 작업의 동일 출력 재현을 확인했다.
> 관련76개 검사·build·브라우저 업무 통과. 학습 모델 A/B는 입력 계약/모델 등록이 남았고,
> 원본 객체 주석 기반 open-loop 요청을 물리·안전·검토자 승인 근거로 승격하지 않는다.
> [범위·사용법](log-sim-demo.md) · [근거](validation/log-sim-workflow-20261009.json).

> **Log Sim 빠른 외부 시연 · 2026-10-09:** 로그 사건 → Case → 앞차 정지 조건 변경 → 실제
> CARLA 실행 → RGB/BEV 동기 비교를 일반 UI/API/SDK로 연결했다. 새 native 1회·RGB720장,
> nuScenes 원본 10프레임·4.499초 조사/Case 저장, 브라우저 흐름과 관련 56개 검사를 확인했다.
> 원본 6초/변경 2초에서 도달 시각 21.45→17.45초를 관측했다. 원본 전체 시나리오를 다시
> 실행하며 중간 상태 복원·임의 실도로 장면 재구성·학습형 센서 폐루프는 후속이다.
> 현재 마일스톤은 로컬 화면 공유 연구 데모다. 실차·센서 물리·상용 인수로 승격하지 않는다.
> [시연 절차와 결과](log-sim-demo.md) · [근거](validation/log-sim-demo-20261009.json).

> **Object Sim 재생 배치 · 2026-10-09:** 원본 대기 안내의 공간을 고정해 재생 중 화면 이동을
> 없앴다. 판단 설명·조작을 3D 도식 아래로 옮기고 작은 객체 ID와 대상 윤곽선을 유지한다.
> 저장 A/B의 브라우저 재생·상세 펼침, frontend 28개와 build를 확인했다. 새 native 실행은 없다.
> [범위·근거](object-sim-urban-interaction.md#재생-배치와-객체-가림-수정).

> **Object Sim 도심 우회전·선행차 반응 · 2026-10-09:** 다섯 번째 `urban_right_turn` 프리셋에
> 동일 자차 설정의 native A/B를 고정하고, 앞차 대기 0–6초 편집·공통 시간 비교·관측 사건 시연을
> 연결했다. 기존 네 binding은 유지했다. 초기 B의 크리프를 보존하고 정지 유지 로직을 개선한 뒤
> 최종 A/B 모두 기존 제한 기준을 통과했으며 RGB 1,440장·보고된 누락 0을 보존했다.
> B는 6.45–8.90초의 50개 응답 표본에서 실제 속도 0.000496m/s 이하를 유지하고
> 앞차 이동 관측 뒤 재개했다. 자차 도착 판단은 A 13.85초/B 21.45초다. 관련 시험 209개 통과.
> 현재 로컬 시연은 추적 RGB를 권장한다. 짧은 A/B 재생은 추적 약 0.945×/전방 약 0.678×였으며
> 전방 20Hz 수집과 UI 재생 성능은 별개다. 고해상도 A/B 재생 최적화·실제 센서 입력 정책·실차 검증은
> 후속이며, 이번 결과는 지도·객체 GT 기반 CARLA 시연 범위다.
> [기능·결과](object-sim-urban-interaction.md) · [근거](validation/object-sim-urban-interaction-20261009.json).

> **Object Sim RGB 전환 · 2026-10-09:** 다음 원본 확인 중 같은 실행·센서·격자의 직전 검증
> 프레임을 유지하도록 깜빡임을 수정했다. 화면·시각·hash·다운로드의 출처를 맞추고 늦은 응답과
> 무결성 오류·출처 전환을 처리한다. 실제 컴포넌트의 작성 fixture 브라우저17단계를 확인했으며
> 현재8033 탭의 기존 도시 전방 원본5초 재생에서 DOM838표본·표시50프레임의 숨김/출처표시 불일치0을
> 확인했다. 제한된 로컬 확인이며 센서 FPS 증가나 모든 환경의 보장은 아니다. 새 native 실행·표본 보간은 없다.
> [범위](object-sim-urban.md#rgb-전환-깜빡임-수정) · [근거](validation/object-rgb-display-20261009.json).

> **Object Sim 도심 배경·원본 A/B · 2026-10-09:** 네 번째 `urban_city` 프리셋과 검증된
> native Town 결과의 RGB 중심/분석 중심 전환을 추가했다. Town10HD_Opt·반대 차로 두 차량·
> 명시적 대기와960×540 전방/추적 각10Hz로 새 native A/B2회 완료·기존 기준 통과·RGB960장·누락0을
> 보존했다. A를 도심 프리셋에 연결하고8033에 적용했으며 브라우저7개 업무·API 미리보기20개 조건을
> 확인했다. 작성한 대기는 `object-story-rules/3`에서 명시적인 설정 대기로 설명하고 원본 시각을 유지한다.
> M4·1080p 로컬 첫 재생1회에서 약0.980×·양쪽3D59.99fps를 측정했다. 원격 성능 보장은 아니다.
> 첫 지도 로딩50초 timeout 실패·불확실 상태와 같은 전용 환경의 복구 근거를 별도로 보존한다.
> 도시 환경은 실제 CARLA RGB로 표현하고 웹 3D는 좌표 도식으로 유지한다.
> 다른 기기/원격 재생·실제 센서 입력 정책·실차 검증·상용 gate는 별도다.
> [현재 범위](object-sim-urban.md) · [실행·소스·근거](validation/object-sim-urban-20261009.json).

> **Object Sim 재생 시계 · 2026-10-09:** 화면 준비 뒤 추가50ms를 기다리던 누적 지연을 수정했다.
> 저장된8대 A/B의10초당 원본 진행이7.25/7.35초에서9.95/9.95초로 개선됐고 약60fps를 유지했다.
> 원본 확인·공통 시간·고정 표본 선택을 유지하며 별도 native 실행은 하지 않았다.
> 이 로컬 조건을 넘어선 고해상도/원격 재생 성능은 별도로 측정한다.
> [근거](validation/object-replay-cadence-20261009.json).

> **Object Sim 관측 사건 시연 · 2026-10-09:** 프리셋/공통 시간 A/B에서 실제 planner 사건을
> 자동 재생하고 설명한 뒤 수동 조사·재개하는 UI/API/SDK를 연결했다. CC0 차량 외형,
> 현재 판단·관측 간격, 주변 상황 카메라와640×360 전방/외부 추적 원본을 추가했다.
> 새 native5회/RGB2,400장·누락0, 기존 실패 보존, 브라우저 정상 업무를 확인했다.
> Apple M4의1080p/8대 A/B 실제3D 렌더링은 약60fps였으며 원본 재생시간은 별도로 기록한다.
> 로컬 재생 속도는 이후 위 항목에서 개선했다. 절차적 도로 배경, 고해상도/원격 재생,
> 실제 센서 입력 정책·실차 상관성·상용 gate는 후속이다.
> 현재8033 전용 데모에 적용했고 공유 외부 서비스는 변경하지 않았다.
> [사용·계약](object-sim-story.md) · [근거](validation/object-sim-story-20261009.json).

> **Object Sim 빠른 외부 시연 준비 · 2026-10-09:** 상세 검증 확대보다 편집·실행·비교의
> 전달력을 우선하여 회피/양보/8대 프리셋, 지도 드래그·간단 설정, 한 번 실행,
> 기준 A/변경 B의 3D·원본 RGB 동기 재생과 발표 화면을 일반 UI/API/SDK에 연결했다.
> 별도8033 workspace에서 native6회 완료와 UI 취소·부분 비교를 확인했다.
> 차로 변경 금지 B의 목표 미달을 보존했고 전체 인수/상용 출시로 승격하지 않는다.
> 선택적 외부 비밀번호·48시간 세션 경계는 준비했으며 새 공개 링크는 발급하지 않았다.
> 절차적 도로/도식 3D의 장면 표현을 넘어서는 환경 품질은 후속 범위다.
> [시연 운영](object-sim-demo.md) · [근거](validation/object-sim-demo-20261009.json).

> **Object Sim 지도·반응형 교통 · 2026-10-09:** `object-map/1` 편집/컴파일과
> `scenario-v2/3` 행동·기본 planner를 일반 UI/API/SDK에서 연결했다. 지도→시나리오→
> 실제 CARLA→다중 actor/RGB Viewer→기존 검증 계획 평가를 인수했다.
> 교차로의 처음 자차 중심 판정에서 놓친 후속 차량 미도착을 보존하고, 모든 차량의 도착/정지를
> 요구하도록 범위를 명시했다. 수정 후 같은 8대·60초·3 seed 모두 도착/정지, 최종 우회/간격/8대 회귀3건 통과.
> 명시적 목적지와 속도 변경의 실제 주행·정상 브라우저 평가/다운로드도 확인했다.
> 19종 전체 조합 인수, 고급 외부 지도·지형/asset 편집, 학습형 traffic·E2E 카메라 폐루프,
> 전체 Case/Campaign 자동화·팀 운영·실측 상관성·상용 동등 gate는 남는다.
> 기존 Autoware Town10 실패와 과거 참조 제어기의 성능 공백은 새 planner로 덮어쓰지 않는다.
> [지원 계약](object-sim-reactive.md) · [단계별 인수와 실패](validation/object-sim-reactive-20261009.json).

> **도시 Object Sim / YAML · 2026-10-09:** 지도/asset/controller의 불변 카탈로그와
> `scenario-v2/2`를 추가했다. Town01/Town10HD_Opt의 일반 UI 경로 작성·YAML 왕복,
> Town01의 실제 Prius 참조 제어·RGB 200장·Viewer·검증 계획 왕복을 인수했다.
> Autoware GT 폐루프 직진/목표 정지3회와 실제 다중 actor Viewer를 인수했다.
> 첫 Town01 suite21회(통과13/미달8), 수정9회(6/3), 최종 회귀2회(2/0)를 소스별로 보존했다.
> Town10 9종의 주행 완료27회는24통과/끼어들기3미달이며, 앞선 보행자 생성 실패1회도 별도로 남긴다.
> 보행자 시작 높이는 명시적 새 개정으로만 수정했고 고정 기준은 완화하지 않았다.
> YAML·저장 센서·실행 준비·다중 actor/원본 RGB·실패 판정의 정상 브라우저 업무를 인수했다.
> Town01 급우회전, Town10 끼어들기 후 자차 목표 도달, 현재 Autoware의 Town10 mission-planner
> 종료는 열린 문제다. 이전 Autoware 직진3회로 새 소스/곡선/모든 도시 인수를 주장하지 않는다.
> [단계별 원본과 결과](validation/carla-city-20261009.json) ·
> [Autoware 실패와 진단](validation/autoware-town10-driving-20261009.json).
> 기존 `/1`·esmini·Sensor Sim의 원본과 실패를 유지하며 vendor 동등·상용 gate는 열린 상태다.
> [범위와 계약](scenario-v2-city.md).

> **Sensor scene workbench · 2026-10-08:** 새 native 주행의 원본 map/ego geometry와
> 영상·계획·물리 응답을 공통 시간축으로 조사한다. 기존 정지 RGB/LiDAR/radar는
> 정확한 원본 tuple·clock·좌표 등록·이상 표시·A/B 조회를 제공한다. 지도가 없는 결과는
> 궤적/metric grid로 명시한다. 다음은 Log의 공통 공간·시간축과 주행 중 다중센서 인수다.
> 반응형 주변 actor·학습형 카메라 피드백·물리 센서·팀 운영·Applied 동등 gate는 열린 상태다.
> [정상 업무·원본 계약·인수 범위](sensor-scene-workbench.md).

> **Object scene workbench · 2026-10-08:** 정상 작성→저장→명시적 native 제출→원본 지도와
> 전체 actor 궤적·2D/3D·선택/추적·이벤트 조사를 연결했다. 실제 5 actor·102표본의
> 차로 변경·정지·횡단을 확인했으며 다른 actor 접촉도 숨기지 않는다. 기존 봉인 결과는 보존한다.
> 직선 2차로·authored kinematics 범위다. 다음은 Sensor/Log의 공통 지도·센서/객체 시간축,
> 지원 외부지도·반응형 모델의 별도 인수이며 물리 센서/안전/상용 동등 gate는 열린 상태다.
> [정상 업무와 인수 범위](object-scene-workbench.md).

> **CARLA planning / feedback 센서 Viewer · 2026-10-08:** 정상 UI에서 지도 기준 제어기를
> 실제 자차 물리에 연결해 새 RGB와 계획·실제 궤적·속도·제어값을 같은 시간축으로 조사한다.
> 실제 4초·9.65m·40개 응답·전/후방 80 RGB를 인수했다. 첫 정리 확인 실패는 보존하고
> 새 snapshot 정리 fence를 추가한 1.0.1을 검증했다. 학습형 카메라 입력 E2E·물리 센서·
> 안전 판정·모든 CARLA client의 전역 실행 조정은 남은 gate다.
> [지원 업무와 근거](carla-feedback-sensor-viewer.md).

> **CARLA 도로영상 Viewer · 2026-10-08:** 사용자가 요청한 기존 3건을 원래 입력·ID·
> 봉인 hash로 공유 작업공간에 연결했다. 1,200개의 전방/후방 원본 RGB 프레임을
> 시간축 재생·A/B 나란히 보기·원본 다운로드로 조사한다. 새 GPU 실행 없이 같은 외부 URL과
> 로그인·만료를 보존했다. 정지 차량 장면이며 실제 이동 정책·물리 센서 평가 gate는 남아 있다.
> [인수 범위](carla-road-viewer.md).

> **Viewer 업무 진입점 · 2026-10-08:** Object 시나리오 작성·실행·native 궤적 재생과
> Sensor 원본 영상·RAW·좌표를 전용 메뉴와 시작 화면으로 연결했다. 실제 esmini 한 건과
> 기존 센서 근거의 HTTPS 업무를 인수했다. 위의 명시적 CARLA 연결을 추가했으며
> 실제 이동 도로 장면·물리 센서/모델 평가 확장은 남아 있다. [정상 UI 업무와 제한](viewer-workspace.md).

> **현재 기능 단위 · 입력 확장 2026-10-07:** schema18의 명시적 project recording grant와
> [정상 원본 선택·다운로드·입력 점검](recorded-input-onboarding.md)을 구현했다.
> 정확한 clock·자차 자세/크기·causal object 표본을 점검하고 같은 preview SHA로 freeze해
> ReplaySpec·Experiment로 연결한다. 새 동일 origin8021 인수 작업공간은
> `.runtime/road-log-onboarding-20261007/platform`이며 기존8020/schema17을 보존한다.
> [내부 launcher](experiment-contract.md)는 실행별 UI snapshot을 고정하고 운영 DB를 자동 이관하지 않는다.
> 실제 nuScenes 원본3프레임의 source 선택·원본다운로드·점검/freeze·reference queue·봉인 결과 UI를 인수했다.
> 정상 Validation에서 저장된 TTC 계획·평가 다운로드도 확인했으며 속도 누락의 TTC0/3은 `not_evaluated`로 유지한다.
> 원본 GT·선언 ego geometry·비상업 연구·비반응형 객체 범위다. [원본/UI 근거](validation/nuscenes-planning-product-20261007.json).
> CameraE2E 중앙 렌즈2×저장센서2의 제품 실험 두 건(RAW8·quality2), API artifact28개 SHA와 정상UI 여섯 업무를 인수했다.
> 초기 실패 두 건을 지우지 않고 센서 계획은 분모4·infrastructure_failure2/context_only2·통과0/SUT실패0·not_evaluated다.
> 중앙32×32 모델 진단이며 실제 도로·물리 센서 품질·제품 순위 인수는 아니다. [렌즈·센서 범위](camera-lens-sensor-experiments.md).
> [입력·렌즈 확장 인수](input-and-lens-expansion-acceptance-20261007.md)에서 범위와 남은 gate를 갱신한다.
> 다음 gate는 승인 입력의 실제 사용자 인수·lens/sensor 픽셀 정합·두 실제 정책 A/B다.
> 비상업 연구 제한, 반응형 카메라 폐루프·실측 상관성·팀 반복 사용·운영/상용 권한·Applied 비교 gate는 유지한다.

> **앞선 네 모듈 인수 · schema17 · 2026-10-07:** [네 모듈 확장](four-module-expansion.md)의
> Project/Experiment·schema17·역할 토큰·형식별 검증 계획, Scenario v2/esmini,
> CARLA RGB/LiDAR/radar 10–30초·rig A/B, CameraE2E 동일 RAW ISP,
> 기준 계획 Log 피드백과 실제 VAD 연구 큐/UI를 구현·격리 인수했다.
> [안정적인 내부 실행점](experiment-contract.md)은 운영 DB를 자동 이관하지 않는다.
> 이 schema17 인수 당시의 후속 gate는 frozen lens/sensor 계획·픽셀 정합, 실제 승인 도로 object 로그와 두 실제 정책 A/B다.
> 반응형 카메라 폐루프·실측 상관성·팀 반복 사용·운영/상용 권한·Applied 실행 비교는 별도 gate로 유지한다.
> 아래 기록의 '다음'과 schema 번호는 당시 상태이며 현재 우선순위를 덮어쓰지 않는다.

> **실제 E2E 후속:** [VAD-Tiny 입력/runner 준비](driving-policies.md)를 진행했다. UI/API/SDK의
> 2026-10-05 승인된 비상업 연구에서 실제 mini/CAN/가중치의 3-frame GPU 추론·출력 재현·UI/API 표시를 인수했다.
> 정책 실행 DB·queue·A/B·검증 계획과 센서/ISP 연결을 완료했다고 계산하지 않는다.

> **현재 기능 단위:** [Verification Plan v1](verification-plans.md)의 계획 개정·기존 근거 연결·
> 고정 Dataset 모델 A/B 묶음 실행·전체/조건별 평가·공백·악화 조사·재실행 UI/API/SDK를 구현했다.
> 현재 schema16은 냉백업/복원본에서 인수한다. 실제 학습형 주행 모델과 CameraE2E 처리 연결은 다음 핵심이며,
> 임의 객체 크기 bin·시간 구간 조건·팀 권한·승인된 실데이터 인수는 아직 완료하지 않았다.

> **2026-09-28 이전 단계 기록:** 자동 라벨링 → 장면 태깅·유사 검색 → 검증 계획 → 실제 E2E·센서 모델 연결.
> [자동 라벨링 첫 단위](automatic-labels.md)는 Collection에서 발행한 dataset에도 적용하며,
> 등록 모델 제안→미검토 개정→사람 수정·검토와 출처 보존을 연결한다. 당시 장면 태깅/유사 검색은 후속이었고 현재 첫 기준선이 연결됐다.
> 기존 Collection·반복 평가 기능을 재사용하고, 별도 기반 확장이나 CARLA 튜닝이 이 순서를 대신하지 않는다.
> 현재 schema14는 백업 후 새 복원본으로 인수한다. 아래의 과거 ‘다음’ 문구는 현재 우선순위를 덮어쓰지 않는다.

2026-09-28: [Case의기록시계열stack비교](case-stack-replays.md)를 일반UI/API/SDK/패키지에연결했다.
정확한Case source를포함하는전체baseline prefix와binding을유지하고후보stack을일반queue로실행한다.
실제차량반응/학습형E2E인수가아니며,앞으로의확장은승인된실제입력·외부정책계약·내부사용자업무로선정한다.
기하stack정지품질이나예제차량물리조정을플랫폼전체개발의다음과제로자동선택하지않는다.

> **현재 추가 연결:** [기록 모델 재평가](case-model-replays.md)로 Case에서 전체 baseline split의
> 실제 ONNX 추론·COCO 평가·A/B·근거 capture를 일반 UI/API/SDK/패키지에 연결했다.
> 새 공급사 connector나 학습형 E2E 주행 지원을 완료한 것은 아니다. 다음 확장은 실제 사용할
> 데이터/모델과 시계열·다중센서 계약으로 선택하며 예제 detector/차량 성능 튜닝으로 돌아가지 않는다.

> **현재 내재화 우선순위 · 2026-09-28:** [통합플랫폼 내재화](internal-platform.md)가 현재 작업 선택의 기준이다.
> Foxglove형 데이터 조사·FiftyOne형 모델 평가·Applied형 실행 연결·Foretellix형 coverage를
> 공통 Segment/Case/시험/근거로 연결한다. 로컬 평가판은 첫 하위 모듈이며 판매 준비가 통합 개발보다
> 항상 우선하지 않는다. I1의 Segment/Case와 평가 연결은 [사례 기능](cases.md)으로 구현했다.
> I2의 첫 [조건 선언·진단 태그·검색·사례 coverage](case-conditions.md)를 연결했다. 이는 ODD/실행 coverage가 아니다.
> I3의 첫 [Case→명시적 객체 참조시험→일반 campaign→Case 결과 capture·활동표](case-retests.md)를 연결했다.
> 실제 센서 조건의 재현·실행 coverage는 미완료다. 다음은 실제 사용할 모델/실행 adapter의 입력·출력 연결이며 CARLA 연구는 분리한다.
> 아래 누적 이력의 '다음'·'최신' 문구는 현재 우선순위를 덮어쓰지 않는다.

2026-09-28 로컬 패키지: 빌드된 UI/API 단일 주소·필수 token·명시적 local worker·환경점검·
종료/재시작 인수를 추가했다. [인수기록](local-pilot-acceptance-20260928.md).
이 하위 모듈의 후속 인수는 실제 내부 사용자 데이터로의 업무수용과 clean source/dependency 배포 기준이다.
내부 source snapshot을 clean checkout release 또는 다중사용자 보안 완료로 취급하지 않는다.

최신 후속: [5ms 후보를 기존 실제 LiDAR 주행에 적용한 네 실행](carla-live-physics-candidate-20260926.md).
고정-throttle substep 진단 이후 기존 cruise/close-lead 재시험까지 완료했다. Cruise 감속만
기존 기준을 통과했고 jerk와 close-lead는 여전히 실패다. 다음 우선순위는 저속 brake–차량
응답 정합·정지 말단 제어이며, cruise 관측 지면 범위 변동은 별도 과제로 분리한다.
아래 과거의 '5ms 후보로 원래 주행을 재시험'이라는 계획은 이 기록으로 갱신한다.

**최신 갱신 · 2026-09-26:** 실제 고정 throttle의 0.01s 재적용 대조/0.005s 변경 비교에서
가속도 부호 교대 39/39→0/39와 가속도 범위 83.322% 감소를 확인했다.
[중복 제외 원본 집계·최신 검증](carla-substep-closeout-20260926.md).
다음은 substep을 별도 버전/설정으로 명시한 기존 cruise/close-lead 재평가다.
production 기본 설정·기존 실패·정밀 모델/상용 gate는 변경하지 않았다.

최초 작성: 2026-09-12 · 현재 인수 상태 갱신: 2026-09-14. 목표는 상용 AD E2E 개발·검증 플랫폼이다. 현재 출발점은 M0 로컬 개발판이다. 아래 일정은 날짜 약속이 아니라 **작게 구현하고 인수 조건을 통과하며 확장하는 순서**다. M0 완료 여부는 실행 테스트와 README의 검증 기록을 기준으로 한다.

MCAP 구현 상태 갱신: **2026-09-13**. M2의 기록 등록·탐색 slice를 구현했다. M2 전체나 실차 센서 재생이 완료된 상태는 아니다.

센서 데이터 구현 상태 갱신: **2026-09-13**. Schema 5에 재사용 sensor rig·기록별 binding의 불변 개정과 run-recording lineage를 추가했다. 지원 Foxglove JSON·ROS2 CDR1 이미지/point-cloud bytes를 해석하고, 선언된 log offset과 age 정책으로 관측을 조사한다. 실제 취득 clock 동기화·물리 calibration·센서 정확도 검증과 M2 전체 완료는 별도 gate로 남는다.

실행 큐 구현 상태 갱신: **2026-09-13**. M3 중 같은 host의 SQLite durable queue·별도 worker·lease·취소·불확실 외부 엔진 격리를 구현했다. PostgreSQL·분산 운영·조직 격리와 M3 전체 인수 조건은 남아 있다.

검증 관리 구현 상태 갱신: **2026-09-13**. M5 중 versioned suite·고정 case/criteria·원자적 campaign 제출·baseline 및 비교 근거 보존을 추가했다. 실제 고객 시험 팩·독립 observer 교체·구성요소 의존성에 따른 영향 분석·조직별 검토 승인은 후속이다.

인지 실행 상태 갱신: **2026-09-13**. M4의 기록 이미지→선택형 CPU ONNX 추론→입력 PNG·검출 근거·불변 비교 위에 독립 dataset 라벨·split·검토 선언의 불변 개정과 실제 COCO bbox 평가를 추가했다. Schema 7은 같은 작업 큐에서 simulation/perception/evaluation을 구분한다. 실제 학습 모델 실행과 입력 의존 pixel-rule 산술 시험을 구분하며, 실차 인지 정확도·센서 기반 주행 제어는 미완료다. 이후 원격 CARLA build1ae5356 실제 주행2건·센서 저장·기록 영상 YOLOX 추론을 확인했다([실제 연결 근거](carla-connection.md)). [추론](perception.md) · [라벨·평가](datasets.md).

CARLA 0.3 상태 갱신: **2026-09-13**. bounded rest-state startup gate·원본 trace·실패 diagnostic artifacts·엄격한 원격 wire·UI startup plots를 구현했다. 실제 build1ae5356에서 4초 lane-follow4/6m/s·emergency-brake4m/s 작업3건을 완료했으며 **세 주행 verdict는 모두 failed**다. startup은 각각23tick/streak5에서 rest criteria를 만족했지만 imposed target velocity 이후 wheel/speed convergence를 입증하지 않는다. 실제 불안정 startup timeout·복구는 미검증이며, M1 전체 완료로 올리지 않는다. [0.3 실제 인수 근거](validation/carla-startup-acceptance-2026-09-13.json).

CARLA 0.4 상태 갱신: **2026-09-13**. 같은 큐에 고정 stack/rig/profile을 저장하고 실제 LiDAR·ego/map oracle→shared kernel→동기 명령 응답→다음 physics를 연결했다. 실제4초 실행 두 건의 각80명령/81관측과 원본 MCAP 재생 각80tick이 일치했다. headway1.5/8초의 주행/정지 동작을 확인했으나 **둘 다 comfort verdict failed**다. 최초 signed-zero 검증 오류의 error run·격리/정리도 보존했다. [실제 인수 범위](validation/carla-live-stack-2026-09-13.json). M1/M4 전체 또는 상용 gate 완료가 아니다.

CARLA 0.4.1 상태 갱신: **2026-09-13**. 먼저 생성한 차량의 요청 tire scale이 후속 spawn 뒤3.5로 바뀌는 문제를 확인해, 기본/live 양쪽을 **모든 spawn→모든 physics 적용→rest 후 두 차량 readback gate**로 수정했다. Live의 원래 XYZ 속도·가속도·요청/ACK·다음 frame·선택적 telemetry를 원본 artifact와 UI에 연결했다. 실제 두 live 실행은 각80명령/81관측·원본 재계산80tick이 일치했고 두 차량 scale0.8을 확인했다. r1 감속/jerk0.606410/11.362139, r2 8.633477/170.319628로 **둘 다 failed**이며 기준과 전체 표본을 유지했다. 독립5조건 진단은 플랫폼 판정과 구분한다. [새 실제 인수](validation/carla-vehicle-response-2026-09-13.json) · [응답 의미](carla-vehicle-response.md). 당시 정밀 동역학·실제 장애 복구와 M1/M4 상용 gate가 후속이었다. 이후 실제 장애 회수 인수는 다음0.5 항목에 구분한다.

CARLA 0.5 상태 갱신: **2026-09-13 · 제한된 실제 회수 인수 완료**. 원격 execution ID·store UUID·요청/입력/context hash·native journal·partial checkpoint·sealed archive와 schema9의 별도 recovery 큐/API/SDK/UI를 구현했다. **독립 native10건(정상2·프로세스 종료8), 플랫폼 응답 유실/실제 worker 종료 회수2건, 최종 source D의 정상 launcher 실행1건으로 실제 엔진 실행13건**을 구분한다. 두 플랫폼 회수는 원래 실패 Job/Run/이벤트/파일을 보존하고 새 Run에 출처를 연결했으며 재시뮬레이션은0회다. 완전 응답 회수는 completed/failed, 부분 회수는 error/diagnostics_only와 빈 metrics/trajectory를 유지했다. 두 실제 결과의 UI 조사와 API/SDK 원본 bytes·SHA·export 검증을 완료했다. 원래48runs·55jobs·139artifacts 및 관리 파일534개를 보존했고 현재53runs·60jobs·169artifacts/schema9다. 전체 Python1460개·Chrome32개·build 통과 뒤 source D의 actor 보존 guard를 포함한 관련28개를 별도로 통과했다. 전체와 관련 시험 수를 합산하지 않는다. [복구 계약](carla-recovery.md) · [인수 근거](validation/carla-durable-recovery-2026-09-13.json). **Live 정상/완전 회수의 주행 verdict는 failed이며 기본 GT 정상 실행은 passed**다. 4초 simulation의 wall time은 live 약55초·GT 약19.65초로 realtime이 아니다. Host power/disk loss·임의 네트워크 장애·분산 소유권·팀 권한·정밀 센서/차량 상관성·상용 gate는 남는다.

CARLA checkpoint 성능 갱신: **2026-09-13 · 실제 인수·일반 worker 적용**. 같은4초 시험의 계측 wall time을 live55.214→28.362초, GT19.414→13.443초로 줄였으며 각각430/351 checkpoint와81관측을 유지했다. 전체 궤적·지표와 live 소비/원본 점군·제어가 baseline과 정확히 같다. 새 private checkpoint 형식의 실제 after-ACK 종료·구 reader 거절·정확한 회수와 일반 큐GT/live2건, 실제 UI 재생을 확인했다. 이번 실제 실행8건의 근거와 기존54runs/61jobs/170artifacts/관리 파일567개 보존은 [성능 인수](validation/carla-checkpoint-performance-2026-09-13.json)에 있다. 공개0.5.0 계약은 유지하고 배포 source hash를 별도로 고정했다. [방법·reader 호환성](carla-performance.md). Live jerk 실패와 장시간·대규모·host/storage 장애 및 상용 gate는 미완료이며 다음 주행 품질 개선을 우선한다.

CARLA0.6 상태 갱신: **선택형 센서 recipe와 실제8건 비교 인수**. baseline/dense를 UI·API·SDK·CLI에서 명시적으로 고정하고0.5원본 archive를0.6 reader로 정확히 읽습니다. gap20m cruise에서는 dense가jerk기준을통과했으나 gap8m정지선행차에서는양쪽failed이고dense가더큰급제동을보입니다. 요청seed42/43/44의profile별원본점군이같아서무작위다양성은미확인입니다. 각80/81원본분모·기존56runs/63jobs/관리파일586개보존, Python1599개·관련browser·actualUI확인을구분합니다. [인수근거](validation/carla-sensor-profiles-2026-09-13.json) · [설정과한계](carla-sensor-profiles.md). RNG실제적용·제동정책·상용gate는남습니다.

CARLA0.7·stack kernel2 갱신: 기존 kernel1을 유지한 채 격자 관측·정지거리·stateful S-curve 계획과 near recipe를 UI/API/replay/live에 연결했습니다. 실제 near 시험3건은 각80명령/81관측의 실행·저장을 완료했으나 모두 제동 품질 failed입니다. 차량 원점 주변 양의 높이 표면 때문에 kernel2 두 시험은 전 구간 emergency stop이며, 실제 완만 정지 효과는 미인수입니다. 자기 차체 반사 후보의 출처 확인·장착/자차 계약·seed 수정 엔진 인수가 다음입니다. [현재 상태와 진행률 추정](status.md) · [검증 기록](validation/stack-v2-2026-09-13.json). M1/M4 전체 또는 상용 gate를 완료로 올리지 않습니다.

CARLA0.8·재사용 센서 설정 갱신: **2026-09-14 · 구현·배포, 품질 gate 일부 미완료**. Schema10에 불변 광선 설정·개정·멱등 키를 추가하고 기존 rig의 위치/회전, stack, capability를 context/2로 고정합니다. UI·API·SDK·CLI의 작성/개정/선택·원본 export와 실제 actor 속성·81개 coherent pose readback을 연결했습니다. Context/1의 지붕 profile3개·과거 입력/결과 bytes는 유지합니다. 실제 정차 및 roll5°/pitch−8°/yaw20° 회전 정차는 각80 hold·throttle0 진단으로 passed였고, cruise는 실행 완료 후 감속8.800123/jerk173.610865, 가까운 정지 선행차는 감속7.870616/jerk157.169649로 failed입니다. 실제4건의80명령/81관측을 보존하고 원래43개 업무 테이블의3260행·모든 열/중복 수와 관리 파일685개·source92개를 확인했습니다. 최종 snapshot은71runs/78jobs/328artifacts·schema10·FK오류0·활성 job0입니다. [사용법](carla-configurable-sensors.md) · [실제 인수](validation/carla-configurable-sensors-2026-09-14.json). 전체 Python1934개(325.28초·기존 경고2개), Chrome 전체63pass/기존 기대값1fail 이후 수정된 관련13pass, 최종 build를 각각 구분합니다. **근접·측면·박형/낮은 외부 장애물의 전체 semantic 관측 인수와 context/2 실제 장애 주입, 완만 정지 품질은 남습니다.** M1/M4 전체나 상용 gate의 완료가 아닙니다.

Campaign control 갱신: **2026-09-14 · 실제 두 case 연결**. Schema11의 독립 실행 계획으로 suite 전체의 stack·rig·센서 개정과 case별 seed/input/context를 고정합니다. 미지원 입력의 원자적 거절, 계획·membership 변조 검사, 안전한 멤버 취소, 과거 NULL/원본 보고서 호환, 명시 CARLA0.8 관측 접촉 수 계약을 UI/API/SDK/CLI에 연결했습니다. 실제 campaign2case의8기준을 모두 평가했고 접촉 수/TTC4통과·감속/jerk4실패를 그대로 baseline에 보존했습니다. 원본 archive/artifact와160ACK·162physics/pose, 원래46업무테이블3340행·725파일·과거API/export 보존을 확인했습니다. [사용법](campaign-control.md) · [실제 인수와 시험 범위](validation/campaign-control-2026-09-14.json). 이는 시험 구성→실행→판정의 연결이며 제동 품질·실시간·정밀 모델 또는 상용 gate 완료가 아닙니다.

독립 차량 응답 진단 갱신: **2026-09-14 · standalone 개발자 도구와 실제 coast4건 완료**. 플랫폼 job/API/Run 기능을 추가하지 않고, 입력 작성·bounded native 실행·원본 hash 검증 분석기를 연결했습니다. Injection1→throttle1→injection2→throttle2의 각4초·80명령/81관측을 보존했고 네 건 모두 rest26tick·cleanup confirmed입니다. Injection 속도1.957952→1.925977m/s와 throttle1.948817→0.368334m/s가 달랐습니다. Throttle 준비의5표본 속도 창 충족은 정상상태를 뜻하지 않았고, 시작 위치1.142824m와 raw gear0/1 등의 차이 때문에 초기화만의 인과 효과로 단정하지 않습니다. Protocol1의 rest 실패·당시 소스도 그대로 보존했습니다. [도구](carla-brake-diagnostic.md) · [실험 제안과 실제 범위](carla-brake-response-design.md) · [근거](validation/carla-brake-initialization-2026-09-14.json). 원래12개 제안 중 초기화4개만 수행했으며, throttle step5 속도1.754365m/s가1.9–2.1m/s 진입 창 밖이어서 pulse8개는 새 계약 전까지 미실행입니다. 기존49테이블3394행·745관리파일을 보존했고 heartbeat는 byte 비교에서 제외했습니다. 새 도구 관련236개/3.98초와 기존 native/helper77개/9.36초의 시험 범위는 별도입니다. 기존 원격source92개와 worker/engine PID를 유지했습니다. 제어기·차량 모델 보정 또는 M1/M4·상용 gate의 완료가 아니며, 전체 상용 약25%·CARLA 개발 파일럿 약75%라는 범위 판단은 유지합니다.

## 제품 방향

자기 차체 반사 진단 갱신: **2026-09-14**. 센서 수명·목록 지연·callback 정리를 보완한 도구에서 실제8쌍의 일반점54,448개 전체를 semantic XYZ와 바이트 일치로 연결했습니다. 근접 후보8,853개 모두 현재 자차 actor ID입니다. 이번 정차 진단의 원인을 확인했으며 주행 결과·기존 점·기준은 그대로입니다. [도구·방법·검증](carla-self-hit-diagnostic.md). 이 진단 당시 다음 과제였던 광선 설정의 재사용 개정·rig 위치/회전·실제 readback은 위0.8의 context/2 경로로 구현했습니다. 기존 context/1은 유지하며 완전한 외부 장애물 시야·새 context 장애 주입은 미인수입니다.

모듈/품질 작업의 앞선 기록: 동일 component ID의 version 병행과 정확한 선택을 구현하고 기존 저장 스택·대기 replay를 보존했다. Python1665개·Chrome9개·build와 로컬 정상 재시작/데이터 보존을 확인했다. [당시 검증 범위](validation/stack-component-versions-2026-09-13.json). 0.6의 dense cruise 한 조건은 passed이고 close-lead는 양쪽 failed다. [Seed configuration 누락의 바이너리 조사와 미빌드 source patch](carla-lidar-seed-diagnosis.md), [전체 close-lead 구간 분석 및 v2 설계 기록](carla-observed-horizon-design.md)을 후속0.7 구현의 근거로 사용했다. 새0.7 실제 결과는 위에 구분한다.

스택 실행 상태 갱신: **2026-09-13**. M4에 교체형 관측·정책·제어 kernel, 불변 stack revision, 기록 ego/LiDAR 입력의 queued open-loop replay와 점군·판단·명령 trace를 추가했다. Schema 8은 `stack_replay`를 별도 작업으로 보존한다. 작성 시 실제로 해석된 전체 파라미터를 고정하며 원본 입력·clock·좌표 선언과 소비 bytes를 추적한다. 이후 선택적 CARLA live bridge를 연결한 인수 범위는 위0.4 항목을 따른다. [스택 사용법](stacks.md) · [실행 근거](validation/stacks-local-2026-09-13.json).

하나의 시험을 `요구사항 → 데이터/시나리오 → 연결한 스택/모델 → 실행 → 결과 → 검토 가능한 근거`로 연결한다. 사용자는 UI로 탐색하고, AI Agent와 CI는 동일한 API로 반복한다. 엔진 교체는 이 업무 흐름을 다시 구현하지 않고 가능해야 한다.

**문제라고 판단한 지점:** 모든 엔진과 화면을 동시에 구현하면 시뮬레이션 정확도·데이터 추적성·운영 안정성이 뒤로 밀린다. 단계마다 실제로 실행되는 업무 하나와 검증 가능한 산출물을 만든다. 초기 toy runner의 시각화나 pass 수치를 상용 성능 또는 차량 안전성의 근거로 제시하지 않는다.

## Milestone과 인수 조건

| 단계 | 사용자에게 생기는 기능 | 필수 인수 조건 | 후속으로 남는 범위 |
| --- | --- | --- | --- |
| **M0 · 로컬 업무 흐름** | 시나리오 작성/수정, 단순 실행, 3D 재생, 지표·요구사항·데이터 탐색, API/SDK | UI에서 생성한 입력으로 실행; 실제 저장된 trajectory와 metric 표시; 실패 예제 포함; 서버 재시작 후 저장 데이터 유지; SDK로 같은 작업 수행; 미연결 adapter를 실행 가능하다고 표시하지 않음 | 외부 엔진, 실차 센서, 인증·분산 운영 |
| **M1 · CARLA 실제 주행·센서** | **일부 인수:** capture·startup·live LiDAR 연결, physics 설정 gate·차량 응답, 실제 응답 유실·worker 종료 회수. 0.6 선택형 LiDAR에서 dense gap20m cruise는 passed, gap8m 정지 선행차는 양쪽 failed. 0.7 near와kernel2 연결·원본 보존 인수, 주행3건 failed. 0.8 설정/rig 개정과 actor 속성/pose 적용 확인; 정차 hold 진단 passed, cruise failed. 별도 개발자 도구의 초기화 coast4건과 v1 rest 실패 보존; pulse8건·제동 모델 보정 미인수. 외부 장애물 시야·context/2 장애 주입·완만 정지·seed 수정 엔진·물리 불안정 startup timeout 후속 | engine/client/adapter version 고정; 전용 GPU server 실제 연결; lane-follow·emergency-brake 실행; 미지원 category 거절; frame·좌표·시간 정렬; 오류·server 정리; artifact hash 검증 | 추가 센서/학습 controller, 보정된 sensor/vehicle physics, 모든 scenario category |
| **M2 · 실차 데이터에서 시험으로** | **일부 구현:** MCAP·정확한 ns/전체 메시지 탐색, 지원 ROS2/Foxglove sensor preview, rig/binding 개정·같은 커서의 age 조사·run artifact lineage. **후속:** 실제 로그 pilot, 이벤트 구간→시험 후보 | 원본 hash·schema·별도 capture/publish/log clock·calibration 출처 보존; 손상/누락 오류; 원본→파생 lineage; log open-loop와 재시뮬레이션 구분 | 실제 clock/calibration 검증, 추가 schema·video·RAW/깊이 형식, log-to-scenario, 새로운 자차 시점의 sensor reconstruction |
| **M3 · 팀 단위 신뢰성** | **일부 인수:** SQLite queue·lease·fenced publish·취소·격리, 원격 영속 archive·동일 원본 실행의 회수·부분 진단·명시적 정리. **후속:** PostgreSQL·object storage, host/storage 장애, 명시적 재시도 정책, 프로젝트 접근 제어 | migration/rollback·backup/restore; worker kill·중복 요청·취소 경합; project 격리; 감사 기록; 장기 작업 복구 | 분산 소유권, orphan 자동 수거, 대규모 GPU 운영 최적화 |
| **M4 · 센서·인지·스택 E2E pilot** | **일부 구현:** 기록 camera→ONNX 추론·COCO bbox 평가; component version 병행, 기록 ego/LiDAR open-loop 재생·불변 trace. **제한된 실제 인수:** CARLA LiDAR→shared kernel→명령 응답·다음 physics·원본 재계산, 차량 응답·설정 readback; 0.6 dense cruise 한 조건 통과, close-lead 실패. 관측 구간·정지 envelope v2 구현과0.7 실제 연결 완료, near 두 조건 emergency stop·주행 실패; 0.8 불변 센서 설정과 일반 rig pose의 실제 적용/원본 보존, 외부 장애물 시야·실제 S-curve 정지 품질 미인수 | 고정 ODD·센서·스택 version; 실제 sensor 입력에서 perception→planning→control 출력 연결; 실측 대비 오차·유효 범위 보고서; 같은 조건의 비교 시험 | 모든 ODD/센서/차종 일반화 |
| **M5 · 검증 관리와 fleet loop** | **일부 구현:** suite 개정·campaign·기준 결과·조건별 비교·봉인 보고서. **후속:** 독립 observer·통계 비교·변경 영향·검토 절차 | 변경 전후 근거 불변성; coverage 분모·미평가 조건 공개; metric version; 실패 triage→시험 재생성; 독립 검토 기록 | 고객별 인증/승인 책임을 자동 대체하는 기능 |
| **M6 · 상용 배포판** | 설치·업그레이드·운영·지원이 가능한 release | 고객 pilot 수용 시험; 보안·성능·복구·호환성 gate; SBOM/자산 출처; 배포 설명서·운영 대응 절차; 지원 범위와 SLA 합의 | 요구하지 않은 인증·실시간·정확도 보장 |

M3의 보안·격리는 외부 고객 데이터를 다루기 전에 당긴다. M4의 perception 연결은 사용자의 주행·인지 우선순위에 따라 M1 직후 작은 pilot으로 앞당길 수 있다. 데이터·운영 단계는 이와 병행한다. HIL과 neural reconstruction은 사용 사례·장비·데이터가 확보된 이후 독립 workstream으로 추진한다. esmini/SUMO 표준·traffic 통합은 CARLA 이후 확장 후보로 둔다.

### CARLA 0.3 인수 경계와 이후 0.4 연결

0.3의 실제 세 run은 각각81개 physics 표본과23개의 startup 원본 샘플을 남겼다. lane-follow4m/s의 최대 감속/jerk는 **0.660m/s² / 25.726m/s³**, lane-follow6m/s는 **26.963 / 580.344**, emergency-brake4m/s는 **26.273 / 446.259**다. 4m/s에서도 두 tick 주기의 속도 교번과 jerk 실패가 남는다. 6m/s의 full-brake 설명은 저장된 actor 상태와 hazard/headway 정책을 이용한 재구성이며, 실제 요청·적용 control logging이 없으므로 확정된 actuator 원인 분석으로 취급하지 않는다. rest startup 통과를 측정 주행 통과나 transient 전체 해결로 바꾸지 않는다.

실패한 외부 실행의 bounded 진단 artifact를 보존하고 worker version·성공/실패 envelope·전송 byte 한도를 검증한다. UI는 artifact/run 문맥과 SHA 검증 후 vz·선속도·stable streak를 표시한다. invalid/부분 샘플은 gap으로 유지하고 다른 선택의 조회가 실패하면 이전 trace를 숨긴다. 전체 Python1171개·Chrome workflow16개·TypeScript/Vite build가 통과했다. startup UI의4개 시험은 authored diagnostic fixture이며 실제 workspace의 startup 그래프도 확인했다. 당시 정상 실제 시험 후 actor0·원래 world 설정·지도 hash를 보존했지만 unstable startup timeout·원격 중단·복구 인수는 후속이었다. 프로세스 중단·회수의 이후 실제 인수는 위0.5 항목을 따른다.

[0.4 shared stack 설계](carla-stack-design.md)는 당시 제안 기록이다. 이후 구현한 선택적 live bridge의 현재 계약과 실제 인수는 [사용법](carla-live-stack.md)과 위0.4 근거를 따른다. 같은 `carla` queue 자원을 사용하고 고정 stack/rig context를 PreparedRun에 보존하면서 기존0.2/0.3 run hash·idempotency·export를 유지한다. 현재는 원본 LiDAR와 실제 ego telemetry MCAP를 추가하고 frame f의 입력→shared kernel→요청/적용 명령→frame f+1의 실제 physics를 연결한다. 당시0.3은 주변 actor ground-truth controller이며 기존 capture에 없는 ego 채널을 만들어 과거 run에 덧붙이지 않는다.

0.4.1의 별도 기본 GT lane-follow4m/s·gap10m·4초는 감속0.051685/jerk1.169115로 passed이며, RGB/LiDAR3쌍과 startup context의 양쪽 설정 일치를 보존했다. 같은 버전의 live 두 개정은 failed다. 기본 GT 한 조건의 통과를 센서 기반 제어·정밀 동역학·상용 gate 통과로 확대하지 않는다.

### 구현된 MCAP 기록 등록·탐색 slice

`mcap==1.4.0`, `lz4==4.4.5`, `zstandard==0.25.0`을 고정했다. 별도의 bounded parser가 실제 record와 제공된 CRC를 검사하고, 원본 파일·파생 payload 파일·관계형 메시지 인덱스를 저장한다. 비압축·LZ4·Zstandard, indexed/unindexed 파일을 수용하며 원본 Summary 통계에 의존하지 않고 실제 메시지에서 count·시간 범위를 계산한다.

UI·API·SDK에서 channel/schema·출처·hash를 조사하고 uint64 ns를 십진 문자열로 유지한다. 시간 조회와 별도로 `from_start`·`after_ordinal` 페이지 탐색을 제공해 timestamp 0과 한 페이지를 넘는 중복 시각 메시지도 모두 읽는다. 기본 payload 탐색은 bounded JSON과 opaque bytes이며, 지원 센서 형식은 별도 decoder로 해석한다. 원본 다운로드는 크기·SHA-256을 확인한다. MCAP 테이블은 schema2에서 추가됐다. 이후 schema10의 센서 설정 개정과 schema11의 고정 campaign 계획도 기존 시나리오·실행·recording·시험·모델·추론·평가 근거를 유지한다.

현재 import는 로컬 동기식 작업으로 업로드 128 MiB, record/해제 chunk 32 MiB, 누적 parsing volume 256 MiB, 메시지 100,000개의 한도가 있다. 이 값은 프로세스 전체 메모리 한도나 fleet 규모 성능 보장이 아니다. 범용 protobuf·custom ROS message, 실측 clock/calibration 검증과 log-to-scenario는 남은 작업이다. 사용법과 실제 검증 범위는 [README](../README.md), [기록 데이터 사용법](data-recordings.md), [recording API 계약](recordings-contract.md), [형식 검토](mcap-format-notes.md)를 따른다.

### 구현된 센서 선언·기록 replay slice

`SensorDecoder`와 교체 가능한 registry가 정확한 schema/encoding을 확인한 뒤 이미지·point-cloud payload를 해석한다. 결과는 원본 message ordinal·SHA·capture/publish/log time과 decoder version을 유지하며, PNG 또는 XYZ float32 LE preview에 별도 hash를 붙인다. 지원하지 않는 RAW·depth·video·custom message는 형식을 추정하여 보여주지 않는다.

재사용 rig는 센서 frame·선언된 장착 transform·intrinsics·출처를 불변 revision으로 저장한다. 기록별 binding은 rig revision·channel·decoder version·age·offset을 고정한다. 같은 커서에서도 미래 관측은 대체하지 않고 stale 관측은 preview를 보류한다. age 정책이 없으면 unknown이며, frame/좌표/transform이 맞지 않으면 rig view를 막는다. 이는 취득 동기화나 물리 calibration 인증이 아니다.

`examples/sensor_workflow.py --output NEW_DIRECTORY`는 직접 작성한 장면의 표준 ROS2/Foxglove MCAP를 생성하여 등록·선언·재생·내보내기·이전 revision 보존 흐름을 수행한다. CARLA나 실측 데이터가 아니다. 기존 run의 MCAP artifact를 가져오면 별도 origin 관계가 원본 실행·adapter·입력/출력 hash를 보존한다. [센서 계약](sensors-contract.md), [지원 포맷](sensor-format-notes.md), [SDK·CLI](../sdk/python/README.md)에 실행 방법과 한계를 정리했다.

### 구현된 로컬 durable 실행 slice

`POST /jobs`는 필수 멱등성 키와 함께 시나리오 revision·유효 입력·adapter version을 저장하고 job ID를 반환한다. 별도 worker는 claim과 dispatch intent를 commit한 뒤 DB transaction 없이 엔진을 호출한다. artifact staging도 DB 밖에서 준비하고, 살아 있는 owner token·lease를 확인해 Run·파일 참조·job 결과를 함께 게시한다. 대기 작업은 재시작 후 계속 진행하며, 만료된 실행은 `interrupted`·결과 불명으로 남고 자동 재실행되지 않는다.

대기/엔진 호출 전 취소와 adapter가 확인한 중단을 구분한다. 취소를 지원하지 않는 외부 adapter의 결과가 도착하면 보존하고 `cancel_not_honoured`를 표시한다. 외부 결과가 불명인 adapter ID는 운영자가 이전 dispatcher와 엔진 정리를 확인하여 reconcile하기 전까지 다음 dispatch를 막는다. 과거 시도가 늦게 완료되어도 제출 순서가 더 새로운 유효 실행 근거를 덮지 않는다.

UI와 새 SDK workflow는 job 제출·상태 조회·결과 열기를 사용한다. 기존 `POST /runs`도 같은 큐와 자원 예약 경로를 거쳐 inline 실행하거나 결과를 기다린다. 멱등성 키 범위는 endpoint별이므로 응답 유실 후에는 **같은 endpoint·키·본문**을 사용해야 한다. 자세한 사용법은 [실행 큐 안내](execution-jobs.md), 상태와 API는 [jobs 계약](jobs-contract.md)을 따른다.

0.5는 같은 큐의 별도 recovery 작업으로 원격 status와 봉인된 archive를 읽는다. 정상·부분 결과는 새 Run에 원래 execution ID와 store/request/archive hash를 연결하고, 원래 실패와 기존 baseline bytes를 바꾸지 않는다. 실행 순위는 원래 source job의 제출 순서다. 프로세스가 죽어도 남은 명령을 재개하지 않으며, archive 회수는 엔진 자원 격리를 자동 해제하지 않는다. 실제 사후 정리는 원래 actor identity·world 상태를 별도로 확인했고 원본 cleanup unknown은 유지했다.

공통 큐는 같은 host·DB·관리 파일을 공유하는 로컬 구현이다. PostgreSQL 큐, 다른 workspace가 공유하는 원격 엔진의 분산 소유권, 자동 retry, tenancy와 활성 attempt까지 고려한 orphan 파일 자동 수거는 미구현이다. 이번 실제 process/응답 유실 인수를 host power/disk loss·임의 네트워크 장애·장시간 복구 보장으로 확대하지 않는다. 물리 불안정 startup timeout과 sensor/vehicle 품질 gate도 남는다.

## 과거 연구의 후속 작업 — 현재 제품 우선순위는 product-pilot.md 참조

최신 [모델 revision/추가 조건6건](carla-model-validation-20260927.md)은 후보 인수1/3이다.
1.5m/s 초기속도 close-lead는 통과하지만2.5m/s와 cruise는 긴급정지/jerk 실패이며 cruise는 악화됐다.
**다음은 지면 관측 prefix 변화의 원인을 원본 LiDAR→rig 변환→grid sampling으로 추적하고,
관측하지 않은 공간을 free로 바꾸지 않으면서 필요한 유효 관측을 확보하는 작업**이다.
새 private 모델 식별·해시·사전 고정 suite는 실행/보고서에 연결했지만 일반 worker native release와
경사 검증은 미완료다. 단일 성공 조건을 근거로 damping4를 전역 기본값으로 바꾸지 않는다.

2026-09-27의 [종방향sticky 감쇠A/B](carla-sticky-damping-20260927.md)에서지정close-lead의
기존jerk≤10/감속≤6기준을실제로통과했다(3.730783/1.989264). native활성규칙은유지하고
별도엔진의생성된행damping1000→4만개입했다. 다음차량모델작업은이변경의명시적native설정/
revision연결과추가속도·경사·cruise회귀다. 현재GDB전용시험을일반Run/운영배포나실차보정으로
취급하지않는다. 아래실패·미채택후보는이전단계의원본이력으로유지한다.

2026-09-27 [별도 엔진 native 계측](carla-native-stop-observer-20260927.md)을 완료했다. 급정지1.95초의종방향sticky4/4활성,같은시각sleep=false·내부brake0.073602,2.35초의후행sleep을 직접 관측했다. Ego81frame과164개차량native상태를 ID로 연결하고103frame/5,150개의실제약1ms Update호출도 기록했다. 다음 차량 모델 작업은 **종방향 저속 타이어 정지 제약 전환을 분리된 모델 후보로 수정·비교하는 것**이다. 실제brake torque/제약impulse와구간내최초발동시각은 아직미관측이므로 완전한원인증명으로 확대하지 않는다. 기존jerk29.439052실패,production기본·원래평가기준은 유지한다. 단순brake해제나이득반복탐색으로 돌아가지 않고,정지거리·hold·기울기조건도 함께 평가한다. Cruise지면관측범위문제는 독립적으로 진행한다.

기존 5ms 시험에 이어, 2026-09-27에는 close-lead7건으로 응답 계수·가속도 피드백·1ms/0.5ms 설정·말단 brake 상한을 분리 비교했습니다. [실제 결과](carla-brake-tail-20260927.md). 최종 후보는 최대 감속1.989260m/s², jerk29.439052m/s³로 개선됐지만 jerk≤10은 실패입니다. 다음은 약0.074m/s에서 한 frame 안에 거의0으로 되는 차량/타이어 정지 전이를 제거하는 것입니다. 5ms의 gain 단독/피드백 악화 후보는 미채택이며 production 기본10ms와 기존 제어기를 유지합니다. 별도로 cruise의 지면 관측 범위6→5→4m 변화에 의한 emergency_stop을 다룹니다. 이득·필터·평가 구간을 임의 변경해 통과시키지 않고, 기존 rest→target-velocity 초기화·과거 실패·미실행 제동 pulse8개·실차 정합성 gate는 그대로 구분합니다.

센서 출처 진단의 handle 기반 정리·목록 지연·callback 반례를 추가하고 실제8쌍 수집을 완료했습니다. 최초0/8 실패도 보존합니다. [진단·정리 근거](validation/carla-self-hit-confirmed-2026-09-14.json). 그 후 센서 설정의 불변 개정·rig pose 적용·실제 readback·UI/API/SDK 실행을0.8로 연결했습니다. [원래 설계와 미완료 인수 조건](carla-configurable-sensors-design.md)은 역사와 현재 gate 상태를 구분합니다. 다음은 근접·측면·박형 외부 장애물/semantic observer 시야 검증, context/2 실제 장애 주입과 원본 조건의 완만 정지 품질입니다. 장착 적용 확인만으로 차체·장애물의 가림이나 clearance를 보장하지 않으며, 자차 점을 삭제하거나 가려진 공간을 관측된 것으로 취급하지 않습니다.

Suite는 시나리오 개정과 요구사항 정의를 case별로 고정하고, campaign은 전체 member job과 함께 commit한다. 기준 결과와 저장된 비교 보고서는 원래 분모·미평가·실패를 유지한다. 현재 비교는 engine/observer 의미가 달라진 수치를 성능 회귀로 분류하지 않는다. [시험 묶음 사용법](test-suites.md), [정확한 API](suites-contract.md), [비교 근거 보존](comparison-evidence.md)을 따른다. 정적 요구사항 연결과 고정 입력 차이는 보여주지만 센서·ISP·모델·ECU 전체 의존성 graph를 통한 영향 분석은 아직 없다.

각 작업은 독립 검토할 수 있는 산출물로 끝낸다. 단순한 메타데이터 등록은 외부 엔진 연결 작업의 완료로 세지 않는다.

| 우선순위 / 작업 | 산출물 | 완료를 확인하는 방법 |
| --- | --- | --- |
| P0 · M0 통합 확인 | 실제 UI·API 실행 및 저장 결과 | 생성→실행→재생→새로고침의 동일 run 확인 |
| P0 · 실행 snapshot 검증·확장 | M0 scenario snapshot/hash 검증; 후속 metric/model revision 확장 | 시나리오를 수정해도 과거 run 입력과 결과 불변 |
| P0 · 지표 의미 문서 | 단위·계산식·validity·threshold 설명 | 알려진 충돌/비충돌과 미평가 예제로 확인 |
| P1 · adapter 계약시험 | test fixture와 capability schema | 미지원 기능 거절, 오류 분류, 종료·정리 확인 |
| P1 · CARLA 실제 시험 확장 | 0.3 startup·lane-follow·emergency-brake 실행과 bounded 실패 진단 구현; 주행3건 failed. 0.4.1 설정 유실 수정·원본 차량 응답 추가. 0.5 프로세스 종료·응답 유실의 원격 회수 인수. 별도 초기화 coast4건 완료·첫 rest 실패 보존; pulse 진입 상태 재정의·제동 모델 보정, 물리 불안정 startup timeout·장시간 장애 후속 | rest·설정 gate·주행 판정 분리; 요청/ACK/다음 frame 원본 속도·가속도와 고정 제동 조건; 실패/timeout의 부분 근거와 actor 정리 |
| P1 · perception 후속 pilot | 구현된 모델 registry·실제 RGB 추론·라벨 평가 위에 실제 라벨 데이터 pilot과 CARLA sensor→planning/control 연결 | 입력·라벨·split·model revision 고정; 평가 분모/실패 보존; 실제 주행 출력까지 trace |
| P1 · shared stack live 연결 | [0.4.1 실제 연결 인수](carla-live-stack.md): LiDAR·ego/map→공통 kernel→명령·다음 frame·차량 응답, 원본 MCAP 재계산 일치. 실제 주행 verdict failed 유지; 0.5 강제 종료·응답 유실 회수 인수. 후속은 더 긴 조건·실제 장애물·추가 센서/학습 controller | 같은 carla 자원과 기존 근거 불변성; 고정 stack/rig·sensor frame→변환된 입력→관측·정책→요청/적용 명령→다음 world frame; 누락·취소·실패의 부분 trace와 정리 확인 |
| P1 · agent walkthrough | SDK 예제, 오류 수정 예제, idempotent 실행 | 빈 workspace에서 문서만 보고 재현 |
| P1 · 기록 데이터 후속 pilot | 구현된 sensor replay 위에 선택한 실제 로그의 clock/calibration 근거·추가 schema 연결 | 원본 payload와 실제 장착·clock 오차를 대조; 정렬의 유효 범위와 누락 표본을 공개하고 이벤트 구간을 시험 후보로 연결 |
| P2 · 데이터 이관 | PostgreSQL migration과 object storage adapter | 실제 SQLite 샘플 이관 후 관계·수치·산출물 비교 |
| P2 · durable 실행 확장 | 구현·실제 인수한 원격 archive 회수 위의 host/storage 장애·명시적 새 attempt/retry 정책·분산 소유권·orphan 정리 | 종료된 worker의 엔진 상태 확인 후에만 새 시도; 기존 근거·활성 작업을 훼손하지 않는 정리와 복구 |
| P2 · 팀 접근 제어 | 사용자/project scope 및 감사 기록 | 다른 project 접근·수정 시 거절 및 기록 |

## 모듈별 범위와 재사용 전략

| 제품 기능 | 직접 소유할 핵심 | 재사용 / 연결 후보 | 검증 기준 |
| --- | --- | --- | --- |
| Object Simulation | scenario·actor 계약, observer, 결과 형식 | CARLA 우선; esmini, SUMO, 고객 행동 모델 후속 | 지원 행동·시간·충돌 판정 범위 |
| Sensor Simulation | 구현된 rig/calibration 선언·revision, 후속 sensor model·상관성 기록 | CARLA, 고객 optical/sensor/ISP 모델 | 실제 센서 생성 경로·실측 조건별 출력 오차와 알려진 미지원 현상 |
| Log Simulation | 원본/파생 lineage, 구현된 sensor replay 계약, 후속 구간 추출 | MCAP·지원 ROS2/Foxglove decoder 구현; 실제 log pilot 후속 | clock·schema·sample 손실·경로 변화 처리 |
| Neural Reconstruction | reconstruction job와 품질/가시영역 관리 | 향후 모델·엔진 선정 | 새로운 시점·동적 객체·가림·조명 오차 |
| HIL | 시험 재사용, signal·clock·fault 계약 | 장비별 driver·고객 ECU | 실제 장비에서 latency/jitter·I/O·fault 검증 |
| Execution | 구현된 SQLite job·lease·취소·게시 fencing·격리, 원격 execution/store 식별·불변 archive·별도 recovery | container/cluster scheduler 후속 | 실제 process 종료·응답 유실 회수 경계 유지; host/storage 장애·분산 소유권·성능/비용 측정 추가 |
| Map / World | revision, 좌표·의미 검증, source/license | OpenDRIVE, 고객 지도·scene | 차로 연결·고도·표면·좌표 변환·권리 출처 |
| Data Explorer | metadata·검색·lineage·triage | MCAP 인덱스·전체 메시지·센서 preview·age 조사 구현; 원격 object storage 후속 | 정확한 구간 검색·원본 추적; 실제 취득 동기화는 별도 검증 |
| Perception models | 모델 artifact·불변 개정·runtime/profile·입력/예측·비교 계약 구현 | ONNX Runtime CPU + 명시적 OpenCV Zoo YOLOX; 추가 모델/accelerator 후속 | 실제 input-dependent 추론·좌표/label/hash; 정답 라벨 정확도와 closed-loop 별도 gate |
| Component stacks | 관측·정책·제어 protocol·registry·전체 파라미터 revision, 기록 replay·부분 trace 구현 | 선택형 Python component entry point; CARLA live LiDAR bridge 구현 | 동일 기록 입력의 판단 차이, 입력 불명 시 정지, 전체 bytes·출처 고정; 실제0.4 명령/다음 frame 인수, 실측 센서/차량 품질은 별도 gate |
| Synthetic Datasets | 원본 이미지/라벨/split·review declaration·불변 개정·COCO bbox 평가 구현; 분포/생성 후속 | 선택형 pycocotools evaluator; 연결된 sensor engine 후속 | label 정합성·전체 분모·실데이터 domain gap·학습/평가 누수 방지 |
| Validation | requirement/test/result 관계·변경 영향 | 고객 요구사항/시험 관리 connector | 판정 기준 고정·근거 불변성·검토 기록 |
| Agent Interface | schema·권한·workflow·추적 | LLM provider와 agent client | 일관된 API·실패 복구·권한 및 자원 한도 |

SDS/Vehicle OS 전체를 첫 플랫폼과 함께 자체 개발하지 않는다. 고객 스택·ROS 2·ECU를 연결할 계약을 먼저 제공하고, 개발 도구와 시험 대상 소프트웨어의 책임을 구분한다.

## 상용화 gate

숫자 목표는 pilot의 데이터 크기·동시 사용자·ODD를 정한 뒤 기준 장비와 함께 고정한다. 측정 전 성능 목표를 달성 실적으로 표기하지 않는다.

| Gate | 통과 근거 |
| --- | --- |
| 기능 | 대표 고객 workflow의 수용 시험과 API·UI 일치 |
| 재현성 | 전체 입력/환경 manifest, checksum, 재실행 비교 및 허용오차 |
| 모델 유효성 | 실측 대비 metric·조건·표본·불확도·실패 영역 보고서 |
| 데이터 | version/lineage, import 검증, migration·backup·restore 기록 |
| 성능 | 명시된 장비·데이터량에서 API p95, job throughput, 재생 성능, 비용 측정 |
| 신뢰성 | process/storage/network 장애 주입, 취소/재시도 경합, 데이터 손실 여부 |
| 보안 | 인증·인가·프로젝트 격리, 감사·비밀정보, 취약점 대응, 의존성 관리 |
| 권리·출처 | 배포 revision의 SBOM·NOTICE, 코드/asset/data/model 별 license 및 변경 기록 |
| 사용자 경험 | 빈 환경 설치부터 실패 분석까지 문서만으로 재현; keyboard·빈 데이터·오류 화면 |
| 운영 | 지원 플랫폼·업그레이드·복구 절차, 관측·알림, 책임 및 지원 범위 |

## pilot에서 고정할 가정

초기 예제는 합성 도로·객체와 단순 모델을 사용한다. 실차 로그·고객 스택·센서 상세 사양·정밀 지도·HIL 장비는 아직 확보했다고 가정하지 않는다. 이후 실데이터 pilot에서 다음 항목을 명시한다.

첫 외부 엔진의 우선순위는 **CARLA 주행·인지**로 결정했다. 현재 Mac 플랫폼은 사용자 Linux GPU PC의 실제 CARLA build1ae5356에 연결됐다. capture·startup·실제 LiDAR→shared kernel→명령/다음 physics와 원격 프로세스 종료·응답 유실 회수를 인수했다. 과거0.6 dense gap20m cruise는 passed였으며 gap8m 정지 선행차는 두 profile 모두 failed였다. 최신0.8의 고정 센서·스택 campaign에서는 cruise와 가까운 정지 선행차 모두 감속·jerk 기준을 실패했다. Seed configuration 누락은 source/바이너리로 조사했고 수정 엔진 인수는 남았다. 가까운 표면의 관측 구간·정지 응답, 물리 불안정 startup timeout·host/storage 장애, 추가 센서/학습 제어와 실측 상관성도 후속이다.

- 첫 고객 workflow와 주사용자: scenario engineer, sensor engineer, validation engineer 중 누가 어떤 결정을 내리는지.
- ODD와 비교 대상: 속도·도로 형태·날씨·시간대·객체·실측/참조 baseline.
- 출력 경계: 객체, RGB, Bayer RAW, point cloud, radar detection/ADC 중 필요한 수준.
- 스택 경계와 실행 환경: API/ROS 2/ECU, 로컬/서버/GPU, 허용 latency.
- 데이터·asset·model의 출처, 재사용 범위와 품질 근거.

설계의 상세 경계는 [architecture.md](architecture.md), 재사용 후보의 원문은 [open-source-register.md](open-source-register.md)를 따른다.
